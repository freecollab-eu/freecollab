#!/usr/bin/env python3
"""Exercise actual Compose/Railway Nginx native-link routes on loopback.

Uses an installed Nginx or caller-provided binary/loader/libraries. No container
or package installation occurs in this test. Native association validation and
the open-resource redirect come from the actual product FastAPI router.
"""
import argparse
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
HOST = 'native-ingress.freecollab.example'
FINGERPRINT = ':'.join(['AB'] * 32)
SOURCES = ['frontend/nginx.conf', 'frontend/railway/nginx.conf.template',
           'backend/app/api/native_associations.py', 'backend/app/services/auth.py',
           'frontend/tests/nginx-native-links.py']


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def port():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0)); return server.getsockname()[1]


def request(number, path, *, host=HOST, headers=None):
    client = http.client.HTTPConnection('127.0.0.1', number, timeout=8)
    try:
        client.request('GET', path, headers={'Host': host, **(headers or {})})
        response = client.getresponse(); body = response.read(131072)
        return {'status': response.status, 'headers': {key.lower(): value for key,value in response.getheaders()}, 'body': body}
    finally: client.close()


def ready(number, process):
    deadline=time.monotonic()+30
    while True:
        assert process.poll() is None, 'Owned listener exited before readiness'
        try:
            with socket.create_connection(('127.0.0.1', number), timeout=.3): return
        except OSError: pass
        assert time.monotonic()<deadline, 'Owned listener did not start'; time.sleep(.05)


def stop(process):
    if process.poll() is None: os.killpg(process.pid, signal.SIGTERM)
    try: process.wait(timeout=10)
    except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)


def render(source, name, web, listener, upstream):
    changes={'root': 'fixture static directory', 'listeners': 'loopback-only assigned port', 'backend': 'owned loopback API assigned port'}
    value=source.replace('/usr/share/nginx/html',str(web))
    if name=='compose':
        assert value.count('listen 80;')==1
        value=value.replace('listen 80;',f'listen 127.0.0.1:{listener};')
        value=value.replace('http://backend:8000',f'http://127.0.0.1:{upstream}')
    else:
        allowed={'PORT':str(listener),'FREECOLLAB_PUBLIC_HOST':HOST,'FREECOLLAB_BACKEND_HOST':'127.0.0.1','FREECOLLAB_TRUSTED_EDGE_CIDR':'192.0.2.0/24'}
        found=set(re.findall(r'\$\{([A-Z_]+)\}',value)); assert found==set(allowed),found
        for key,entry in allowed.items(): value=value.replace('${'+key+'}',entry)
        assert value.count('server 127.0.0.1:8000;')==1
        value=value.replace('server 127.0.0.1:8000;',f'server 127.0.0.1:{upstream};')
        value=value.replace(f'listen {listener}',f'listen 127.0.0.1:{listener}').replace(f'listen [::]:{listener}',f'listen [::1]:{listener}')
        changes['template_variables']=allowed
    assert '${' not in value
    return value,changes


def probe(command,mime,source,name,private,upstream,*,control=False,upstream_down=False):
    private.mkdir();web=private/'html';web.mkdir()
    html='<!doctype html><html><title>Owned native ingress SPA</title><main>Owned app fallback</main></html>'
    (web/'index.html').write_text(html);(web/'probe.css').write_text('body { color: rgb(1, 2, 3); }\n');(web/'worker.mjs').write_text('export const ownIngress = true;\n')
    listener=port();fragment,changes=render(source,name,web,listener,upstream)
    if control:
        fragment,count=re.subn(r'(?m)^\s*access_log off;\n','',fragment);assert count>0
        fragment,count=re.subn(r'(?m)^\s*error_log /dev/null emerg;\n','',fragment);assert count>0
    (private/'server.conf').write_text(fragment)
    access,error=private/'access.log',private/'error.log'
    main=private/'nginx.conf'
    main.write_text(f'daemon off; master_process off; pid {private}/nginx.pid;\nerror_log {error} info;\nevents{{worker_connections 64;}}\nhttp{{\n'
        +f'include {mime}; default_type application/octet-stream; access_log {access} combined;\n'
        +''.join(f'{kind}_temp_path {private}/{kind};\n' for kind in ('client_body','proxy','fastcgi','uwsgi','scgi'))
        +f'include {private}/server.conf;\n}}\n')
    checked=subprocess.run([*command,'-p',str(private)+'/', '-c',str(main),'-t'],capture_output=True,text=True)
    assert checked.returncode==0,checked.stderr
    stream=(private/'process.log').open('w');process=subprocess.Popen([*command,'-p',str(private)+'/', '-c',str(main)],stdout=stream,stderr=stream,start_new_session=True)
    checks={};marker='own-native-'+secrets.token_hex(12);normal={'workspace_id':'owned-workspace','channel_id':'owned-channel','kind':'document','item_id':marker}
    try:
        ready(listener,process)
        opened=request(listener,'/open?'+urlencode(normal),headers={'X-Forwarded-For':'198.51.100.88','X-Forwarded-Proto':'https'})
        if upstream_down:
            assert opened['status']==502,opened['status']; checks['upstream_failure_is_real_502']=True
        else:
            assert opened['status']==307,opened
            location=opened['headers']['location'];assert location.startswith('/#open=')
            encoded=location.split('=',1)[1];decoded=json.loads(base64.urlsafe_b64decode(encoded+'='*((-len(encoded))%4)))
            assert decoded==normal and 'set-cookie' not in opened['headers']
            assert opened['headers']['cache-control']=='no-store' and opened['headers']['referrer-policy']=='no-referrer'
            checks['actual_validated_307_carries_only_exact_resource_hint_without_session_or_cookie']=True
            assert 'nosniff' in opened['headers'].get('x-content-type-options','')
            assert opened['headers'].get('x-owned-forwarded-for')=='127.0.0.1' and opened['headers'].get('x-owned-forwarded-proto')=='http'
            checks['native_redirect_nosniff_and_no_referrer_headers_survive_proxy']=True
            apple=request(listener,'/.well-known/apple-app-site-association');android=request(listener,'/.well-known/assetlinks.json')
            assert apple['status']==android['status']==200
            assert apple['headers']['content-type'].startswith('application/json') and android['headers']['content-type'].startswith('application/json')
            a=json.loads(apple['body']);b=json.loads(android['body'])
            assert a['applinks']['details'][0]['appIDs']==['ABCDE12345.eu.freecollab.fixture']
            assert a['applinks']['details'][0]['components']==[{'/':'/open'}]
            assert b[0]['target']['package_name']=='eu.freecollab.fixture' and b[0]['target']['sha256_cert_fingerprints']==[FINGERPRINT]
            assert 'nosniff' in apple['headers'].get('x-content-type-options','') and 'nosniff' in android['headers'].get('x-content-type-options','')
            checks['both_associations_are_real_operator_bound_json_not_spa_html']=True
            invalid=[urlencode(normal)+'&item_id=other',urlencode({**normal,'kind':'unknown'}),urlencode({**normal,'next':'https://outside.example/'}),urlencode({**normal,'kind':'task'})]
            for query in invalid:
                rejected=request(listener,'/open?'+query);assert rejected['status']==422,rejected['status']
                assert 'location' not in rejected['headers'] and 'set-cookie' not in rejected['headers']
            checks['repeated_unknown_kind_unknown_field_and_task_without_project_rejected_by_actual_router']=True
            protected=request(listener,'/api/private-native-probe')
            assert protected['status'] in {401,403} and 'set-cookie' not in protected['headers']
            checks['public_open_hint_does_not_authorize_actual_protected_dependency']=True
            echo=request(listener,'/api/echo-ingress',headers={'X-Forwarded-For':'198.51.100.88','X-Forwarded-Proto':'https'})
            assert echo['status']==200,echo
            actual=json.loads(echo['body'])
            assert actual=={'host':HOST,'forwarded_for':'127.0.0.1','forwarded_proto':'http','authorization_seen':False},actual
            checks['untrusted_forwarding_headers_are_overwritten_on_native_backend_path']=True
            deep=request(listener,'/a/deep/product/path?hint='+marker)
            assert deep['status']==200 and deep['body'].decode()==html
            assert deep['headers'].get('x-content-type-options')=='nosniff' and deep['headers'].get('referrer-policy')=='strict-origin-when-cross-origin'
            css=request(listener,'/probe.css');module=request(listener,'/worker.mjs')
            assert css['body'].startswith(b'body {') and css['headers']['content-type'].startswith('text/css')
            assert module['body'].startswith(b'export const') and module['headers']['content-type'].startswith('application/javascript')
            assert 'nosniff' in module['headers'].get('x-content-type-options','')
            checks['ordinary_spa_static_mime_and_existing_security_headers_preserved']=True
            if name=='compose': assert 'public' in css['headers'].get('cache-control','')
            else: assert 'cache-control' not in css['headers']
            checks['configured_static_cache_behavior_preserved']=True
            if name=='railway':
                assert request(listener,'/.well-known/assetlinks.json',host='untrusted.invalid')['status']==421
                assert request(listener,'/api/ready',host='untrusted.invalid')['status']==200
                checks['railway_default_host_serves_only_readiness']=True
        # Raw invalid escape is rejected by Nginx itself, before route matching.
        assert request(listener,'/open%GG?item_id='+marker)['status']==400
        checks['early_nginx_rejection_is_exercised']=True
    finally:
        stop(process);stream.close()
    logs='\n'.join(path.read_text() for path in (access,error,private/'process.log') if path.exists())
    logged=marker in logs
    assert logged==control, 'Actual URL logging control failed'
    checks['raw_native_url_logging_matches_expected_boundary']=logged==control
    return {'checks':checks,'rendered_config_sha256':digest(private/'server.conf'),'fixture_transformations':changes,
            'config_test_passed':True,'raw_url_marker_logged':logged,'nginx_config_test':checked.stderr.strip(),
            'content_security_policy_configured': 'Content-Security-Policy' in source}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nginx',default=shutil.which('nginx'));parser.add_argument('--loader',type=Path)
    parser.add_argument('--library-path');parser.add_argument('--mime-types',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args();assert args.nginx,'Pass an installed or caller-provided Nginx binary'
    command=[str(args.nginx)]
    if args.loader:
        assert args.library_path;command=[str(args.loader),'--library-path',args.library_path,*command]
    hashes=lambda:{name:digest(ROOT/name) for name in SOURCES}
    before=hashes();result={'status':'running','before':before,'binary_sha256':digest(args.nginx),
        'version':subprocess.run([*command,'-v'],check=True,capture_output=True,text=True).stderr.strip(),'configurations':{}}
    processes=[];streams=[];args.report.parent.mkdir(parents=True,exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='freecollab-nginx-native-') as directory:
            private=Path(directory);upstream=port();launcher=private/'api.py'
            launcher.write_text('import sys\nsys.path.insert(0,'+repr(str(ROOT/'backend'))+')\nfrom fastapi import FastAPI,Depends,Request\nfrom app.api.native_associations import router\nfrom app.services.auth import get_current_user\nimport uvicorn\napp=FastAPI();app.include_router(router)\n@app.middleware("http")\nasync def inspect(request:Request,call_next):\n response=await call_next(request);response.headers["X-Owned-Forwarded-For"]=request.headers.get("x-forwarded-for","");response.headers["X-Owned-Forwarded-Proto"]=request.headers.get("x-forwarded-proto","");return response\n@app.get("/ready")\ndef ready():return {"status":"ready"}\n@app.get("/private-native-probe")\ndef protected(user=Depends(get_current_user)):return {"authorized":True}\n@app.get("/echo-ingress")\ndef echo(request:Request):return {"host":request.headers.get("host"),"forwarded_for":request.headers.get("x-forwarded-for"),"forwarded_proto":request.headers.get("x-forwarded-proto"),"authorization_seen":"authorization" in request.headers}\nuvicorn.run(app,host="127.0.0.1",port='+str(upstream)+',access_log=False,proxy_headers=False)\n')
            env=os.environ.copy();env.update(ENVIRONMENT='test',DATABASE_URL='sqlite:///'+str(private/'owned.db'),STORAGE_PATH=str(private/'files'),JWT_SECRET=secrets.token_urlsafe(48),NATIVE_APPLICATION_ID='eu.freecollab.fixture',NATIVE_APPLE_TEAM_ID='ABCDE12345',NATIVE_ANDROID_CERT_SHA256=FINGERPRINT)
            log=(private/'api.log').open('w');streams.append(log)
            api=subprocess.Popen([os.getenv('BACKEND_PYTHON') or '/workspace/core/backend/.venv/bin/python',str(launcher)],cwd=private,env=env,stdout=log,stderr=log,start_new_session=True);processes.append(api);ready(upstream,api)
            sources={'compose':(ROOT/'frontend/nginx.conf').read_text(),'railway':(ROOT/'frontend/railway/nginx.conf.template').read_text()}
            for name,source in sources.items():
                result['configurations'][name]={'protected':probe(command,args.mime_types,source,name,private/(name+'-protected'),upstream),
                    'logging_positive_control':probe(command,args.mime_types,source,name,private/(name+'-control'),upstream,control=True)}
            stop(api)
            for name,source in sources.items():
                result['configurations'][name]['upstream_failure']=probe(command,args.mime_types,source,name,private/(name+'-failure'),upstream,upstream_down=True)
            result['api_log_tail']=(private/'api.log').read_text()[-1000:]
        result['after']=hashes();result['sources_unchanged']=before==result['after'];assert result['sources_unchanged']
        result['status']='passed'
    finally:
        for process in reversed(processes):stop(process)
        for stream in streams:stream.close()
        result['cleanup']='All owned Nginx/API listeners, processes and temporary files removed. No Docker runtime or existing service used.'
        args.report.write_text(json.dumps(result,indent=2)+'\n')
    print('Native Nginx proof passed for both Compose and Railway configurations.')


if __name__=='__main__':main()
