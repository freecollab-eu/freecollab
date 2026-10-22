#!/usr/bin/env python3
"""Two owned Synapse servers, isolated APIs/DBs and actual Rust browser crypto.

Only this disposable launcher maps exact synthetic HTTPS peer names to its own
local TLS receivers, and only its homeservers permit private fixture federation
addresses. Production transport TLS verification and Ed25519 signatures remain.
"""
import base64
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from nacl.signing import SigningKey
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / 'backend/.venv/bin/python'
REPORT = ROOT.parent / '.freecollab/proof-results/connect-federation-browser-013.json'
IMAGE = 'ghcr.io/element-hq/synapse@sha256:97665cd4539dcf107c69fb6afdb720e1d1c9c09fb2b5701fb13d6c33b22cd8f0'


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); return sock.getsockname()[1]


def docker(arguments):
    env = dict(os.environ)
    for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'): env.pop(key, None)
    return subprocess.run(['docker', '--host=unix:///var/run/docker.sock', *arguments], env=env,
        check=True, text=True, capture_output=True)


def request(base, path, body=None, token=None, method=None):
    headers = {'Content-Type': 'application/json'}
    if token: headers['Authorization'] = 'Bearer ' + token
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urlopen(Request(base + path, data=data, headers=headers, method=method), timeout=100) as response:
            value = response.read(); return response.status, json.loads(value) if value else None
    except HTTPError as error:
        value = error.read(); return error.code, json.loads(value) if value else None


def succeeds(base, path, body=None, token=None, method=None):
    status, value = request(base, path, body, token, method)
    assert status in {200, 201}, f'Owned API failed: {path} ({status}) {value}'
    return value


def ready(base, processes, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process in processes): raise AssertionError('Owned fixture process exited')
        try:
            if request(base, '/health')[0] == 200: return
        except Exception: pass
        time.sleep(.2)
    raise AssertionError('Owned fixture readiness timeout')


def launcher(private, side, api_port, web_origin, peer_ports, matrix_port, seed, peerkey):
    path = private / ('api-' + side + '.py')
    path.write_text('''import os,json,base64
from app.services import oidc_transport
original_addresses=oidc_transport.public_addresses
original_connect=oidc_transport._PinnedBackend.connect_tcp
ports=''' + repr(peer_ports) + '''
def addresses(host,timeout=5):
    return ['127.0.0.1'] if host in ports else original_addresses(host,timeout=timeout)
def connect(self,host,port,timeout=None,local_address=None,socket_options=None):
    if self.hostname in ports and host==self.ip=='127.0.0.1' and port==443:port=ports[self.hostname]
    return original_connect(self,host,port,timeout=timeout,local_address=local_address,socket_options=socket_options)
oidc_transport.public_addresses=addresses
oidc_transport._PinnedBackend.connect_tcp=connect
# Exact local synthetic peers use their fixture TLS receivers; no public traffic
# is sent to a different transport and certificate verification remains enabled.
original_proxies=oidc_transport.getproxies
def proxies():
    values=original_proxies();values['no']='connect-a.example.com,connect-b.example.com';return values
oidc_transport.getproxies=proxies
from app.services import remote_transport
original_request=remote_transport.request
def diagnostic_request(*args,**kwargs):
    try:
        response=original_request(*args,**kwargs)
        if response.status>=400:print('Owned peer response status:',response.status,flush=True)
        return response
    except remote_transport.ProviderError as error:
        print('Owned peer transport diagnostic:',error.code,error.status,flush=True);raise
remote_transport.request=diagnostic_request
import uvicorn
from sqlmodel import Session
from app.main import socket_app
from app.database import init_db,engine
from app.models.user import User
from app.models.workspace import Workspace,WorkspaceMember
from app.models.connect_federation import ConnectFederationIdentity
from app.services.crypto import encrypt_message
from app.services.auth import create_token,hash_password
init_db()
with Session(engine) as session:
    session.add(Workspace(id=''' + repr(side) + ''',name='Company ''' + side + ''''))
    values={}
    for name,role in [('admin','owner'),('member','member'),('outsider','member')]:
        identifier=name+'-''' + side + ''''
        session.add(User(id=identifier,name=''' + repr(side.upper() + ' ') + '''+name,email=identifier+'@fixture.example',password_hash=hash_password('Owned-Private-Fixture!2026')))
        session.flush();session.add(WorkspaceMember(workspace_id=''' + repr(side) + ''',user_id=identifier,role=role))
    session.add(ConnectFederationIdentity(encrypted_seed=encrypt_message(''' + repr(seed) + ''','connect-federation-identity')))
    session.commit()
    for name in ['admin','member','outsider']:values[name]={'user_id':name+'-''' + side + '''','token':create_token(name+'-''' + side + '''',session)}
    session.commit()
    with os.fdopen(os.open(os.environ['FIXTURE_TOKENS'],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w')as file:json.dump(values,file)
uvicorn.run(socket_app,host='127.0.0.1',port=''' + str(api_port) + ''',proxy_headers=False,access_log=False)
''')
    other = 'b' if side == 'a' else 'a'
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'backend'), 'ENVIRONMENT': 'test',
        'DATABASE_URL': 'sqlite:///' + str(private / ('api-' + side + '.db')),
        'JWT_SECRET': secrets.token_urlsafe(48), 'STORAGE_PATH': str(private / ('files-' + side)),
        'MESSAGE_ENCRYPTION_KEY_FILE': str(private / ('messages-' + side + '.json')),
        'ACCOUNT_SECURITY_KEY_FILE': str(private / ('accounts-' + side + '.key')),
        'MATRIX_ENABLED': 'true', 'MATRIX_SERVER_NAME': 'matrix-' + side + '.example.com',
        'MATRIX_INTERNAL_URL': 'http://127.0.0.1:' + str(matrix_port),
        'MATRIX_REGISTRATION_SHARED_SECRET': seed, 'CORS_ORIGINS': json.dumps([web_origin]),
        'CONNECT_FEDERATION_ENABLED': 'true', 'CONNECT_FEDERATION_PUBLIC_URL': 'https://connect-' + side + '.example.com',
        'CONNECT_FEDERATION_PEERS': json.dumps({'https://connect-' + other + '.example.com': {
            'public_key': peerkey, 'matrix_server_name': 'matrix-' + other + '.example.com'}}),
        'FIXTURE_TOKENS': str(private / ('tokens-' + side + '.json')),
        'SSL_CERT_FILE': str(private / 'combined-ca.pem'), 'PUBLIC_APP_URL': web_origin,
        'MAIL_DELIVERY_MODE': 'disabled'}
    return path, env


def main():
    processes, streams, servers, contexts, containers = [], [], [], [], []
    checks, stage = {}, 'owned fixture creation'
    peer_payloads = []
    network = 'fc-federation-proof-' + secrets.token_hex(5)
    browser = None
    fixture_entry = None
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='freecollab-federation-proof-') as directory:
        private = Path(directory); private.chmod(0o700)
        # Excluded Rust/WASM imports resolve through this fixture-owned symlink
        # when the isolated prebundle cache lives outside the source tree.
        (private/'node_modules').symlink_to(ROOT/'frontend/node_modules',target_is_directory=True)
        try:
            docker(['info', '--format', '{{.ServerVersion}}'])
            docker(['image', 'inspect', IMAGE, '--format', '{{.Id}}'])
            docker(['network', 'create', '--label', 'freecollab.proof=connect-federation', network])
            cert, key = private / 'cert.pem', private / 'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=connect-a.example.com','-addext','subjectAltName=DNS:connect-a.example.com,DNS:connect-b.example.com,DNS:matrix-a.example.com,DNS:matrix-b.example.com'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            key.chmod(0o600)
            default_ca = ssl.get_default_verify_paths().cafile
            (private / 'combined-ca.pem').write_bytes((Path(default_ca).read_bytes() if default_ca else b'') + b'\n' + cert.read_bytes())
            matrix_ports, seeds = {}, {side: base64.b64encode(bytes(SigningKey.generate())).decode() for side in 'ab'}
            signer_keys = {side: SigningKey(base64.b64decode(seeds[side])) for side in 'ab'}
            matrix_config = private / 'configure-matrix.py'
            matrix_config.write_text('''import os,runpy,yaml
from pathlib import Path
runpy.run_path('/run/secure-config.py',run_name='__main__')
p=Path('/data/homeserver.yaml');v=yaml.safe_load(p.read_text())
v['listeners'].append({'port':8448,'tls':True,'type':'http','x_forwarded':False,'resources':[{'names':['federation'],'compress':False}]})
v['tls_certificate_path']='/run/fixture-cert.pem';v['tls_private_key_path']='/run/fixture-key.pem'
v['federation_custom_ca_list']=['/run/fixture-cert.pem'];v['federation_verify_certificates']=True
# Private addresses are enabled exclusively in this generated fixture, never in
# the operator configuration script or product runtime.
v['federation_ip_range_blacklist']=[]
v['no_proxy_hosts']=['matrix-a.example.com','matrix-b.example.com']
v['rc_federation']={'window_size':1000,'sleep_limit':1000,'sleep_delay':10,'reject_limit':2000,'concurrent':100}
p.write_text(yaml.safe_dump(v));p.chmod(0o600)
''')
            for side in 'ab':
                stage = 'own Synapse configuration: ' + side
                print('Federation proof stage: '+stage,flush=True)
                data = private / ('matrix-' + side); data.mkdir(mode=0o700)
                envfile = private / ('matrix-' + side + '.env')
                other = 'b' if side == 'a' else 'a'
                envfile.write_text('MATRIX_SERVER_NAME=matrix-' + side + '.example.com\nSYNAPSE_SERVER_NAME=matrix-' + side + '.example.com\nSYNAPSE_REPORT_STATS=no\nMATRIX_REGISTRATION_SHARED_SECRET=' + seeds[side] + '\nMATRIX_FEDERATION_DOMAINS=["matrix-' + other + '.example.com"]\nNO_PROXY=' + os.environ.get('NO_PROXY','') + ',matrix-a.example.com,matrix-b.example.com\nno_proxy=' + os.environ.get('no_proxy',os.environ.get('NO_PROXY','')) + ',matrix-a.example.com,matrix-b.example.com\nSYNAPSE_CONFIG_PATH=/data/homeserver.yaml\n'); envfile.chmod(0o600)
                mounts = ['--user', str(os.getuid()) + ':' + str(os.getgid()), '--env-file', str(envfile),
                    '--mount', f'type=bind,src={data},dst=/data',
                    '--mount', f'type=bind,src={ROOT / "scripts/secure-matrix-config.py"},dst=/run/secure-config.py,readonly',
                    '--mount', f'type=bind,src={matrix_config},dst=/run/configure.py,readonly',
                    '--mount', f'type=bind,src={cert},dst=/run/fixture-cert.pem,readonly',
                    '--mount', f'type=bind,src={key},dst=/run/fixture-key.pem,readonly',
                    '--mount', 'type=bind,src=/etc/ssl/certs/ca-certificates.crt,dst=/run/session-ca.pem,readonly', '-e', 'SSL_CERT_FILE=/run/session-ca.pem']
                docker(['run','--rm',*mounts,'--entrypoint','python',IMAGE,'/run/configure.py'])
                selected = port(); matrix_ports[side] = selected
                name = network + '-' + side; containers.append(name)
                docker(['run','-d','--name',name,'--label','freecollab.proof=connect-federation','--network',network,'--network-alias','matrix-' + side + '.example.com',*mounts,'-p',f'127.0.0.1:{selected}:8008',IMAGE])
            for side in 'ab':
                deadline=time.monotonic()+120
                while time.monotonic()<deadline:
                    try:
                        with urlopen('http://127.0.0.1:'+str(matrix_ports[side])+'/_matrix/client/versions',timeout=2)as response:
                            if response.status==200:break
                    except Exception:time.sleep(.3)
                else:raise AssertionError('Owned Synapse readiness timeout: '+side)
            checks['two_independent_tls_federating_synapse_homeservers']=True
            critical_paths = [ROOT/'backend/app/api/connect_federation.py', ROOT/'backend/app/services/connect_federation.py', ROOT/'backend/app/models/connect_federation.py', ROOT/'backend/app/api/secure_messaging.py', ROOT/'backend/app/api/channels.py', ROOT/'backend/app/services/matrix_bridge.py', ROOT/'backend/app/services/workspaces.py', ROOT/'backend/app/services/connect.py', ROOT/'frontend/src/lib/secure/index.ts', ROOT/'frontend/src/lib/secure/files.ts', ROOT/'frontend/src/lib/secure/transaction-retry.ts', ROOT/'frontend/src/lib/calendar.ts']
            critical_paths += [ROOT / path for path in ['frontend/src/components/WorkHub.tsx', 'backend/app/models/federated_work.py', 'backend/app/services/federated_work.py', 'frontend/src/lib/federated-work-api.ts', 'frontend/src/lib/federated-work-model.ts', 'frontend/src/lib/federated-work-document.ts', 'frontend/src/lib/federated-work-document.worker.ts', 'frontend/src/lib/federated-work-pointers.ts', 'frontend/src/lib/federated-work-trust.ts', 'frontend/src/components/FederatedWorkPanel.tsx']]
            runtime_sources = {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in critical_paths}
            api_ports = {side:port() for side in 'ab'}; web_port=port(); web_origin='http://127.0.0.1:'+str(web_port)
            api_bases={side:'http://127.0.0.1:'+str(api_ports[side]) for side in 'ab'}
            peer_ports={}
            for side in 'ab':
                base=api_bases[side]
                def receiver(target):
                    class Receiver(BaseHTTPRequestHandler):
                        def log_message(self,*_):pass
                        def do_POST(self):
                            if not self.path.startswith('/connect/federation/wire/'):return self.send_error(404)
                            length=int(self.headers.get('Content-Length','0'))
                            if not 0<length<131072:return self.send_error(413)
                            payload=self.rfile.read(length)
                            peer_payloads.append(payload.decode())
                            try:
                                with urlopen(Request(target+self.path,data=payload,headers={'Content-Type':'application/json'}),timeout=90)as result:status,data=result.status,result.read()
                            except HTTPError as result:status,data=result.code,result.read()
                            if status>=400:
                                with (private/'wire-failure.log').open('ab')as log:log.write((str(status)+' '+self.path+' ').encode()+data+b'\\n')
                            self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                    return Receiver
                server=ThreadingHTTPServer(('127.0.0.1',0),receiver(base));context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key);server.socket=context.wrap_socket(server.socket,server_side=True)
                threading.Thread(target=server.serve_forever,daemon=True).start();servers.append(server);peer_ports['connect-'+side+'.example.com']=server.server_port
            for side in 'ab':
                other='b' if side=='a' else 'a'
                path,env=launcher(private,side,api_ports[side],web_origin,peer_ports,matrix_ports[side],seeds[side],base64.b64encode(bytes(signer_keys[other].verify_key)).decode())
                stream=(private/('api-'+side+'.log')).open('wb');streams.append(stream)
                processes.append(subprocess.Popen([str(PYTHON),str(path)],cwd=ROOT/'backend',env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True))
            for base in api_bases.values():ready(base,processes)
            identities={side:json.loads((private/('tokens-'+side+'.json')).read_text()) for side in 'ab'}
            stage='real signed bilateral HTTPS acceptance'
            print('Federation proof stage: '+stage,flush=True)
            host=succeeds(api_bases['a'],'/connect/federation/workspaces/a/connections',{'name':'Independent encrypted channel','peer_origin':'https://connect-b.example.com','peer_workspace_id':'b'},identities['a']['admin']['token'])
            guest=succeeds(api_bases['b'],'/connect/federation/workspaces/b/accept',{'peer_origin':'https://connect-a.example.com','invitation_token':host['invitation_token']},identities['b']['admin']['token'])
            connections={'a':host,'b':guest}
            checks['real_signed_https_acceptance_binds_distinct_local_channels_to_one_federated_room']=host['channel_id']!=guest['channel_id']
            for side in 'ab':
                succeeds(api_bases[side],f'/connect/federation/workspaces/{side}/connections/'+host['id']+'/members',{'user_id':identities[side]['member']['user_id']},identities[side]['admin']['token'])
            for side in 'ab':succeeds(api_bases[side],f'/connect/federation/workspaces/{side}/connections/'+host['id']+'/sync',{},identities[side]['admin']['token'])
            fixture_entry=ROOT/'frontend'/('.'+network+'.html')
            fixture_entry.write_text('''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React from 'react';import {createRoot} from 'react-dom/client';
import {api} from '/src/lib/api.ts';import {getSecureMessaging} from '/src/lib/secure-client.ts';
import {FederatedWorkClient} from '/src/lib/federated-work-api.ts';
import {FederatedWorkPanel} from '/src/components/FederatedWorkPanel.tsx';import {WorkHub} from '/src/components/WorkHub.tsx';
window.fixtureModules={React,createRoot,api,getSecureMessaging,FederatedWorkClient,FederatedWorkPanel,WorkHub};
</script></body></html>''')
            vite_config=private/'vite.config.mjs'
            vite_config.write_text('import {mergeConfig} from '+json.dumps(str(ROOT/'frontend/node_modules/vite/dist/node/index.js'))+';import product from '+json.dumps(str(ROOT/'frontend/vite.config.ts'))+';export default mergeConfig(product,'+json.dumps({'cacheDir':str(private/'vite-cache'),'optimizeDeps':{'entries':[str(fixture_entry)]},'server':{'hmr':False,'fs':{'allow':[str(ROOT/'frontend'),str(private/'vite-cache')]}}})+');\n')
            stream=(private/'vite.log').open('wb');streams.append(stream)
            processes.append(subprocess.Popen(['node',str(ROOT/'frontend/node_modules/vite/bin/vite.js'),'--config',str(vite_config),'--host','127.0.0.1','--port',str(web_port),'--strictPort'],cwd=ROOT/'frontend',stdout=stream,stderr=subprocess.STDOUT,start_new_session=True))
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                try:
                    with urlopen(web_origin,timeout=2)as result:
                        if result.status==200:break
                except Exception:time.sleep(.2)
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
                pages=[];traffic=[]
                for side in 'ab':
                    stage='real Rust browser initialization: '+side
                    print('Federation proof stage: '+stage,flush=True)
                    context=browser.new_context();contexts.append(context);page=context.new_page();pages.append(page)
                    records=[];traffic.append(records)
                    page.on('request',lambda req,rows=records:rows.append((req.url,req.method,req.post_data or '')) if '/_matrix/' in req.url or any(req.url.startswith(base) for base in api_bases.values()) else None)
                    page.goto(web_origin+'/'+fixture_entry.name,wait_until='networkidle')
                    page.wait_for_function('Boolean(window.fixtureModules)',timeout=90000)
                    page.evaluate('''async({base,token,user,channel})=>{const {api}=await import('/src/lib/api.ts');api.getBaseUrl=()=>base;api.getAccessToken=()=>token;api.getCurrentUserId=()=>user;api.request=async(path,options={})=>{const r=await fetch(base+path,{...options,headers:{'Content-Type':'application/json',Authorization:'Bearer '+token,...options.headers}});const body=await r.json();if(!r.ok){const e=new Error(typeof body.detail==='string'?body.detail:'Owned fixture API denied');e.status=r.status;throw e;}return body};window.cryptoProof=await(await import('/src/lib/secure-client.ts')).getSecureMessaging();await window.cryptoProof.ensureSession();await window.cryptoProof.roomFor(channel);window.proofChannel=channel;const {FederatedWorkClient}=await import('/src/lib/federated-work-api.ts');window.workProof=new FederatedWorkClient(channel)}''',{'base':api_bases[side],'token':identities[side]['member']['token'],'user':identities[side]['member']['user_id'],'channel':connections[side]['channel_id']})
                stage='cross-homeserver device query and fingerprint verification'
                print('Federation proof stage: '+stage,flush=True)
                own=[]
                for index,side in enumerate('ab'):
                    devices=pages[index].evaluate('async()=>await window.cryptoProof.getDevices(window.proofChannel)')
                    own.append(next(device for device in devices if device['user_id']==identities[side]['member']['user_id']))
                for index,side in enumerate('ab'):
                    peer=own[1-index];deadline=time.monotonic()+90
                    while time.monotonic()<deadline:
                        devices=pages[index].evaluate('async()=>await window.cryptoProof.getDevices(window.proofChannel)')
                        remote=next((device for device in devices if device['matrix_user_id']==peer['matrix_user_id'] and device['device_id']==peer['device_id']),None)
                        if remote:break
                        pages[index].wait_for_timeout(300)
                    assert remote,'Federated peer device was not discovered'
                    assert remote['user_id']==remote['matrix_user_id'],'Remote identity lost its homeserver namespace'
                    pages[index].evaluate('async({device,fingerprint})=>await window.cryptoProof.verifyDevice(device,fingerprint)',{'device':remote,'fingerprint':peer['fingerprint']})
                checks['actual_rust_device_keys_cross_homeservers_and_verified_fingerprints']=True
                messages=['Private encrypted from installation A','Private encrypted reply from independent B']
                for index,message in enumerate(messages):
                    stage='real federated encrypted send and peer decrypt: '+str(index+1)
                    print('Federation proof stage: '+stage,flush=True)
                    pages[index].evaluate('async text=>await window.cryptoProof.sendMessage(window.proofChannel,text)',message)
                    for page in pages:
                        deadline=time.monotonic()+100
                        while time.monotonic()<deadline:
                            rows=page.evaluate('async()=>await window.cryptoProof.getMessages(window.proofChannel)')
                            if any(row['content']==message for row in rows):break
                            page.wait_for_timeout(300)
                        else:raise AssertionError('Actual federated peer did not decrypt message')
                checks['bidirectional_real_megolm_rust_send_decrypt_between_distinct_homeservers']=True
                records=[record for rows in traffic for record in rows]
                encrypted=[json.loads(body)for url,method,body in records if '/send/m.room.encrypted/'in url and method in {'PUT','POST'}]
                assert len(encrypted)>=2 and all(value.get('algorithm')=='m.megolm.v1.aes-sha2' and value.get('ciphertext')for value in encrypted)
                assert all(message not in body for message in messages for _,_,body in records)
                checks['matrix_wire_contains_ciphertext_and_no_message_plaintext']=True
                stage='actual encrypted attachment across independent homeservers'
                print('Federation proof stage: '+stage,flush=True)
                pages[0].evaluate('''async()=>{window.fileProof=(await import('/src/lib/secure/files.ts')).secureFileProvider;window.fileRecord=await window.fileProof.upload(window.proofChannel,new File(['Private federated attachment bytes'],'private-federated.txt',{type:'text/plain'}),{requestId:crypto.randomUUID()})}''')
                pages[1].evaluate('''async()=>{window.fileProof=(await import('/src/lib/secure/files.ts')).secureFileProvider}''')
                deadline=time.monotonic()+100
                while time.monotonic()<deadline:
                    files=pages[1].evaluate('async()=>await window.fileProof.list(window.proofChannel)')
                    if any(row['filename']=='private-federated.txt' for row in files):break
                    pages[1].wait_for_timeout(300)
                else:raise AssertionError('Peer did not list actual encrypted attachment')
                content=pages[1].evaluate('''async()=>{const row=(await window.fileProof.list(window.proofChannel)).find(row=>row.filename==='private-federated.txt');if(!row.document_owner_id.endsWith(':matrix-a.example.com'))throw Error('Remote owner namespace missing');return await(await window.fileProof.download(row)).text()}''')
                assert content=='Private federated attachment bytes'
                checks['actual_client_encrypted_attachment_upload_remote_metadata_and_authenticated_federated_download']=True
                stage='shared work current participant context and no implicit foreign enrollment'
                print('Federation proof stage: '+stage,flush=True)
                for side in 'ab':
                    work_path='/connect/federation/channels/'+connections[side]['channel_id']
                    assert request(api_bases[side],work_path+'/work-context',token=identities[side]['outsider']['token'])[0]==403
                    assert request(api_bases[side],work_path+'/work-index',token=identities[side]['admin']['token'])[0]==403
                    other='b' if side=='a' else 'a'
                    assert request(api_bases[side],work_path+'/work-index',token=identities[other]['member']['token'])[0]==401
                checks['shared_work_current_grants_and_independent_native_jwt_boundaries']=True
                sensitive=['Private cross-installation task A','Private cross-installation task B','Private cross-installation event A','Private cross-installation event B','Organization A private document text. ','Organization B private document text. ']
                stage='real task creation and editing from both isolated installation browsers'
                print('Federation proof stage: '+stage,flush=True)
                for index in [0,1]:
                    created=pages[index].evaluate('''async({title,assignee})=>{const record=await window.workProof.save('task',{title,description:'Encrypted bilateral task details',status:'todo',priority:'high',assignee_matrix_id:assignee,due_date:'2026-10-23',checklist:[{id:'review',title:'Review together',done:false}]});return record.id}''',{'title':sensitive[index],'assignee':own[1-index]['matrix_user_id']})
                    pages[1-index].evaluate('''async id=>{const view=await window.workProof.load();const row=view.records.find(r=>r.id===id);if(!row)throw Error('Peer task pointer missing');await window.workProof.save('task',{...row.selected.fields,status:'doing',checklist:[{id:'review',title:'Review together',done:true}]},row)}''',created)
                    result=pages[index].evaluate('''async id=>{const row=(await window.workProof.load()).records.find(r=>r.id===id);return{status:row.selected.fields.status,done:row.selected.fields.checklist[0].done,history:row.history.length}}''',created)
                    assert result=={'status':'doing','done':True,'history':2},result
                checks['actual_both_side_encrypted_task_creation_global_assignment_peer_edit_and_convergence']=True
                stage='real finite recurring calendar series and peer exception edit on both installations'
                print('Federation proof stage: '+stage,flush=True)
                for index in [0,1]:
                    created=pages[index].evaluate('''async title=>(await window.workProof.save('calendar',{title,description:'Encrypted series details',location:'Shared room',starts_at:'2026-10-23T08:00:00Z',ends_at:'2026-10-23T09:00:00Z',all_day:false,timezone:'Europe/Stockholm',reminder_minutes:null,external_uid:null,recurrence:{frequency:'weekly',interval:1,count:3},exceptions:[]})).id''',sensitive[index+2])
                    pages[1-index].evaluate('''async id=>{const row=(await window.workProof.load()).records.find(r=>r.id===id);await window.workProof.save('calendar',{...row.selected.fields,location:'Peer agreed room',exceptions:[{original_start:'2026-10-30T09:00:00.000Z',cancelled:true}]},row)}''',created)
                    result=pages[index].evaluate('''async id=>{const row=(await window.workProof.load()).records.find(r=>r.id===id),m=await import('/src/lib/calendar.ts');return{location:row.selected.fields.location,dates:m.expandCalendarOccurrences(row.selected.fields,'2026-10-01','2026-12-01').map(r=>r.starts_at)}}''',created)
                    assert result=={'location':'Peer agreed room','dates':['2026-10-23T08:00:00.000Z','2026-11-06T09:00:00.000Z']},result
                checks['actual_both_side_encrypted_calendar_recurrence_dst_and_peer_occurrence_exception']=True
                stage='real Yjs document creation on both installations and concurrent independent browser edits'
                print('Federation proof stage: '+stage,flush=True)
                document_ids=[]
                for index in [0,1]:
                    created=pages[index].evaluate('async title=>(await window.workProof.createDocument(title)).id','Shared document created on '+str(index))
                    document_ids.append(created)
                    pages[1-index].evaluate('''async id=>{const row=(await window.workProof.load()).records.find(r=>r.id===id),state=await window.workProof.document(row);await window.workProof.saveDocument(row,state,'Peer updated title','Peer organization contribution. ')}''',created)
                    assert pages[index].evaluate('''async id=>{const row=(await window.workProof.load()).records.find(r=>r.id===id);return(await window.workProof.document(row)).body}''',created)=='Peer organization contribution. '
                for page in pages:page.evaluate('''async id=>{window.concurrentRow=(await window.workProof.load()).records.find(r=>r.id===id);window.concurrentBase=await window.workProof.document(window.concurrentRow)}''',document_ids[0])
                pages[0].evaluate('''text=>{window.concurrentSave=window.workProof.saveDocument(window.concurrentRow,window.concurrentBase,'Concurrent shared title',window.concurrentBase.body+text).then(()=>({ok:true}),error=>({error:error.message}))}''',sensitive[4])
                pages[1].evaluate('''text=>{window.concurrentSave=window.workProof.saveDocument(window.concurrentRow,window.concurrentBase,'Peer updated title',text+window.concurrentBase.body).then(()=>({ok:true}),error=>({error:error.message}))}''',sensitive[5])
                for page in pages:assert page.evaluate('async()=>await window.concurrentSave')=={'ok':True}
                document_states=[]
                for page in pages:document_states.append(page.evaluate('''async id=>{const row=(await window.workProof.load()).records.find(r=>r.id===id),state=await window.workProof.document(row);return{title:state.title,body:state.body}}''',document_ids[0]))
                assert document_states[0]==document_states[1],document_states
                assert all(value in document_states[0]['body'] for value in sensitive[4:])
                checks['actual_two_browser_yjs_document_creation_peer_edits_concurrent_merge_and_title_convergence']=True
                stage='actual encrypted Yjs fragment commitment across homeservers'
                print('Federation proof stage: '+stage,flush=True)
                marker='Private fragmented shared document text. '
                pages[0].evaluate('''async({id,marker})=>{const row=(await window.workProof.load()).records.find(r=>r.id===id),state=await window.workProof.document(row);await window.workProof.saveDocument(row,state,state.title,state.body+marker+'x'.repeat(20000))}''',{'id':document_ids[1],'marker':marker})
                deadline=time.monotonic()+90
                while time.monotonic()<deadline:
                    large=pages[1].evaluate('''async id=>{const row=(await window.workProof.load()).records.find(r=>r.id===id);try{return(await window.workProof.document(row)).body}catch{return ''}}''',document_ids[1])
                    if marker in large:break
                    pages[1].wait_for_timeout(300)
                assert marker in large and len(large)>20000
                sensitive.append(marker)
                checks['actual_large_yjs_change_encrypted_fragment_commitment_and_peer_reconstruction']=True
                stage='actual concurrent task branches and explicit reviewed merge across installations'
                print('Federation proof stage: '+stage,flush=True)
                task_id=pages[0].evaluate('async author=>(await window.workProof.load()).records.find(r=>r.kind==="task"&&r.created_by===author).id',own[0]['matrix_user_id'])
                for page in pages:page.evaluate('''async id=>{window.taskBase=(await window.workProof.load()).records.find(r=>r.id===id)}''',task_id)
                for index,page in enumerate(pages):
                    page.evaluate('''index=>{window.taskBranch=(async()=>{const {api}=window.fixtureModules,m=await import('/src/lib/federated-work-model.ts'),v=await window.workProof.load(),row=window.taskBase;const result=await window.cryptoProof.sendEvent(window.proofChannel,m.WORK_REVISION,{protocol:m.WORK_PROTOCOL,connection_id:v.context.connection_id,id:row.id,kind:'task',parents:row.heads.map(head=>head.event_id),archived:false,fields:{...row.selected.fields,...(index?{status:'done'}:{priority:'low'})}});await api.request('/connect/federation/channels/'+window.proofChannel+'/work-index',{method:'POST',body:JSON.stringify({matrix_event_id:result.event_id})});return true})()}''',index)
                for page in pages:assert page.evaluate('async()=>await window.taskBranch')
                for page in pages:
                    result=page.evaluate('''async id=>{const row=(await window.workProof.load()).records.find(r=>r.id===id);let blocked=false;try{await window.workProof.save('task',row.selected.fields,row)}catch{blocked=true}return{heads:row.heads.length,conflicts:row.conflicts,blocked}}''',task_id)
                    assert result=={'heads':2,'conflicts':True,'blocked':True},result
                pages[0].evaluate('''async id=>{const row=(await window.workProof.load()).records.find(r=>r.id===id);await window.workProof.save('task',{...row.selected.fields,priority:'low',status:'done'},row,row.heads.map(head=>head.event_id))}''',task_id)
                for page in pages:
                    result=page.evaluate('''async id=>{const row=(await window.workProof.load()).records.find(r=>r.id===id);return{conflicts:row.conflicts,parents:row.selected.parents.length,priority:row.selected.fields.priority,status:row.selected.fields.status}}''',task_id)
                    assert result=={'conflicts':False,'parents':2,'priority':'low','status':'done'},result
                checks['actual_concurrent_task_heads_block_blind_save_and_explicit_merge_converges']=True
                stage='actual malicious pointer isolation and authenticated incomplete ancestry'
                print('Federation proof stage: '+stage,flush=True)
                target=pages[0].evaluate('async author=>(await window.workProof.load()).records.find(r=>r.kind==="task"&&r.created_by===author).id',own[0]['matrix_user_id'])
                pages[1].evaluate('''async id=>{const {api}=await import('/src/lib/api.ts'),m=await import('/src/lib/federated-work-model.ts'),v=await window.workProof.load();const result=await window.cryptoProof.sendEvent(window.proofChannel,m.WORK_REVISION,{protocol:m.WORK_PROTOCOL,connection_id:v.context.connection_id,id,kind:'task',parents:[],archived:false,fields:{title:'Forged foreign root',description:'',status:'todo',priority:'normal',assignee_matrix_id:null,due_date:null,checklist:[]}});await api.request('/connect/federation/channels/'+window.proofChannel+'/work-index',{method:'POST',body:JSON.stringify({matrix_event_id:result.event_id})})}''',target)
                isolated=pages[0].evaluate('''async id=>{const view=await window.workProof.load(),row=view.records.find(r=>r.id===id);return{invalid:row.invalid.length,quarantined:view.quarantined_event_ids.length,usable:window.workProof.hasCurrentScope()}}''',target)
                assert isolated['invalid']==0 and isolated['quarantined']>=1 and isolated['usable'],isolated
                broken=pages[1].evaluate('''async target=>{const {api}=await import('/src/lib/api.ts'),m=await import('/src/lib/federated-work-model.ts'),v=await window.workProof.load(),orphan=v.context.connection_id+':'+await m.authorNamespace(v.context.matrix_user_id)+':'+crypto.randomUUID();for(const id of [target,orphan]){const result=await window.cryptoProof.sendEvent(window.proofChannel,m.WORK_REVISION,{protocol:m.WORK_PROTOCOL,connection_id:v.context.connection_id,id,kind:'task',parents:['$unknown-concurrent-ancestor'],archived:false,fields:{title:'Incomplete verified revision',description:'',status:'todo',priority:'normal',assignee_matrix_id:null,due_date:null,checklist:[]}});await api.request('/connect/federation/channels/'+window.proofChannel+'/work-index',{method:'POST',body:JSON.stringify({matrix_event_id:result.event_id})})}return orphan}''',target)
                for page in pages:
                    state=page.evaluate('''async({target,orphan})=>{const view=await window.workProof.load(),known=view.records.find(r=>r.id===target),unknown=view.records.find(r=>r.id===orphan);let blocked=false;try{await window.workProof.save('task',known.selected.fields,known)}catch{blocked=true}const healthy=view.records.find(r=>r.kind==='task'&&r.id!==target&&r.id!==orphan&&!r.invalid.length);if(!healthy)throw Error('Healthy resource poisoned');await window.workProof.save('task',{...healthy.selected.fields,priority:'low'},healthy);return{knownInvalid:known.invalid.length,rootlessInvalid:unknown.invalid.length,rootlessRoot:unknown.root_event_id,blocked,usable:window.workProof.hasCurrentScope()}}''',{'target':target,'orphan':broken})
                    assert state['knownInvalid'] and state['rootlessInvalid'] and not state['rootlessRoot'] and state['blocked'] and state['usable'],state
                checks['actual_malicious_indexed_candidate_quarantine_rootless_visibility_and_resource_scoped_incomplete_history']=True
                stage='actual accessible shared work UI edits on isolated installations'
                print('Federation proof stage: '+stage,flush=True)
                for index,page in enumerate(pages):
                    page.evaluate('''async()=>{const {React,createRoot,FederatedWorkPanel}=window.fixtureModules;document.getElementById('root').style.display='none';const node=document.createElement('div');node.id='work-proof';node.style.padding='24px';document.body.append(node);window.workUI=createRoot(node);window.workUI.render(React.createElement(FederatedWorkPanel,{channelId:window.proofChannel}))}''')
                    page.get_by_role('button',name='New shared task',exact=True).wait_for(state='visible',timeout=90000)
                    page.get_by_role('button',name='New shared task',exact=True).click()
                    page.get_by_label('Title',exact=True).fill('UI shared task from '+str(index))
                    page.get_by_role('button',name='Save encrypted changes',exact=True).click()
                    page.get_by_role('status').filter(has_text='Encrypted shared changes saved.').wait_for(timeout=90000)
                    page.get_by_label('Title',exact=True).fill('UI edited shared task from '+str(index))
                    page.get_by_role('button',name='Save encrypted changes',exact=True).click()
                    page.get_by_role('status').filter(has_text='Encrypted shared changes saved.').wait_for(timeout=90000)
                    page.evaluate('()=>window.workUI.unmount()')
                    page.evaluate('''async side=>{const {React,createRoot,WorkHub}=window.fixtureModules;window.workHubUI=createRoot(document.getElementById('work-proof'));window.workHubUI.render(React.createElement(WorkHub,{workspaceId:side,channel:{id:window.proofChannel,name:'Independent shared work',type:'group',secure:true,encrypted_channel_key:null,role:'member'},initialTab:'shared-work',onClose:()=>{}}))}''','ab'[index])
                    page.get_by_role('tab',name='Shared work',exact=True).wait_for(state='visible',timeout=90000)
                    assert page.get_by_role('tab',name='Shared work',exact=True).get_attribute('aria-selected')=='true'
                    page.get_by_role('tab',name='Mobile & offline',exact=True).click()
                    page.get_by_role('tab',name='Shared work',exact=True).click()
                    page.get_by_role('button',name='New shared task',exact=True).wait_for(state='visible',timeout=90000)
                    page.evaluate('()=>window.workHubUI.unmount()')
                for index,page in enumerate(pages):
                    titles=page.evaluate('async()=>(await window.workProof.load()).records.filter(r=>r.kind==="task").map(r=>r.selected.fields.title)')
                    assert all('UI edited shared task from '+str(i) in titles for i in [0,1])
                checks['actual_shared_work_ui_creation_editing_on_both_independent_installations']=True
                sensitive += ['UI shared task from '+str(i) for i in [0,1]] + ['UI edited shared task from '+str(i) for i in [0,1]]
                stage='shared work wire and local database plaintext absence'
                print('Federation proof stage: '+stage,flush=True)
                records=[record for rows in traffic for record in rows]
                assert all(value not in body for value in sensitive for _,_,body in records)
                assert all(value not in body for value in sensitive for body in peer_payloads)
                import sqlite3
                for side in 'ab':
                    with sqlite3.connect(private/('api-'+side+'.db'))as database:
                        tables={row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                        assert {'taskcard','shareddocument','integrationcontent'}.issubset(tables)
                        for table in ['taskcard','shareddocument']:
                            assert database.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
                        assert database.execute("SELECT count(*) FROM integrationcontent WHERE kind='calendar'").fetchone()[0]==0
                        columns={row[1] for row in database.execute('PRAGMA table_info(federatedworkpointer)')}
                        assert columns=={'sequence','connection_id','matrix_event_id','matrix_user_id','published_by','created_at'}
                        assert database.execute('SELECT count(*) FROM federatedworkpointer').fetchone()[0]>0
                checks['native_peer_and_matrix_wire_opaque_and_only_event_pointers_in_independent_sql_indexes']=True
                stage='current remote participant revocation and peer fail-closed send'
                print('Federation proof stage: '+stage,flush=True)
                succeeds(api_bases['b'],'/connect/federation/workspaces/b/connections/'+host['id']+'/members/'+identities['b']['member']['user_id'],token=identities['b']['admin']['token'],method='DELETE')
                assert request(api_bases['b'],'/channels/'+guest['channel_id'],token=identities['b']['member']['token'])[0]==403
                succeeds(api_bases['a'],'/connect/federation/workspaces/a/connections/'+host['id']+'/sync',{},identities['a']['admin']['token'])
                checks['remote_removal_denies_own_product_access_and_updates_other_homeserver_membership']=True
                denied=pages[1].evaluate('''async()=>{try{await window.workProof.load();return false}catch{return true}}''');assert denied
                assert request(api_bases['b'],'/connect/federation/channels/'+guest['channel_id']+'/work-index',{'matrix_event_id':'$unauthorized'},identities['b']['member']['token'])[0]==403
                checks['revoked_participant_existing_browser_cannot_refresh_or_publish_shared_work']=True
                stage='bilateral durable connection revocation'
                print('Federation proof stage: '+stage,flush=True)
                succeeds(api_bases['b'],'/connect/federation/workspaces/b/connections/'+host['id'],token=identities['b']['admin']['token'],method='DELETE')
                for side in 'ab':assert request(api_bases[side],'/channels/'+connections[side]['channel_id'],token=identities[side]['member']['token'])[0]==403
                for page in pages:assert page.evaluate('''async()=>{try{await window.workProof.load();return false}catch{return true}}''')
                checks['either_owner_revokes_both_installations_without_becoming_content_participant']=True
                browser.close();browser=None
            assert runtime_sources == {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in critical_paths},'Critical federation runtime sources changed during the proof'
            REPORT.write_text(json.dumps({'runtime_source_sha256':runtime_sources,'runtime_source_unchanged':True,'checks':checks,'scope':'Two independent owned Synapse homeservers with verified synthetic TLS federation, two isolated real API/databases, actual source Rust browser crypto; fixture-issued native sessions, not customer IdP login or public deployment.','transport':'Real signed HTTPS peer control plane; exact fixture DNS/socket mapping only in owned launchers. Production public-address validation remains unchanged.','source_sha256':{str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for base in [ROOT/'backend/app',ROOT/'frontend/src'] for path in sorted(base.rglob('*'))if path.is_file()and path.suffix in {'.py','.ts','.tsx','.css'}}},indent=2)+'\n');REPORT.chmod(0o600)
            print('Federation browser proof passed: '+str(len(checks))+' checks, two actual Synapse homeservers and Rust E2EE.')
        except Exception:
            # Keep only bounded owned-service diagnostics, never tokens/config/env.
            for side in 'ab':
                log=private/('api-'+side+'.log')
                if log.exists():
                    diagnostic=REPORT.with_name('connect-federation-'+side+'-failure.log');diagnostic.write_bytes(log.read_bytes()[-12000:]);diagnostic.chmod(0o600)
            if (private/'vite.log').exists():
                diagnostic=REPORT.with_name('connect-federation-vite-failure.log');diagnostic.write_bytes((private/'vite.log').read_bytes()[-12000:]);diagnostic.chmod(0o600)
            for container in containers:
                with suppress(Exception):
                    result=docker(['logs','--tail','100',container])
                    diagnostic=REPORT.with_name(container+'-failure.log');diagnostic.write_text(result.stdout+result.stderr);diagnostic.chmod(0o600)
            if (private/'wire-failure.log').exists():
                diagnostic=REPORT.with_name('connect-federation-wire-failure.log');diagnostic.write_bytes((private/'wire-failure.log').read_bytes());diagnostic.chmod(0o600)
            print('Federation proof failed at: '+stage)
            raise
        finally:
            if browser:
                with suppress(Exception):browser.close()
            for server in servers:
                with suppress(Exception):server.shutdown();server.server_close()
            for process in reversed(processes):
                if process.poll() is None:
                    with suppress(Exception):os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=12)
                    if process.poll()is None:
                        with suppress(Exception):os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
            for stream in streams:stream.close()
            if fixture_entry:
                with suppress(Exception):fixture_entry.unlink()
            for container in containers:
                with suppress(Exception):docker(['rm','-f',container])
            with suppress(Exception):docker(['network','rm',network])


if __name__=='__main__':main()
