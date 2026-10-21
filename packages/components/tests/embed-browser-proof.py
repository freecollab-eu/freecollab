#!/usr/bin/env python3
"""Actual packed SDK, separate host/trusted origins and private production APIs.

This harness owns every account, key, database and process it creates. Optional
--matrix starts a separate pinned private Synapse fixture; it never reads an
existing homeserver's credentials. A synthetic HTTPS customer IdP exercises the
normal OIDC transport, callback, one-use handoff and managed-account policy.
Public provider, npm publication and external deployment acceptance are separate.
"""
import argparse
import base64
from datetime import datetime
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit, urlencode
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright
from cryptography import x509
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / 'packages/components'
REPORT = Path(os.getenv('EMBED_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/embed-browser.json')))
MATRIX_IMAGE = 'ghcr.io/element-hq/synapse@sha256:97665cd4539dcf107c69fb6afdb720e1d1c9c09fb2b5701fb13d6c33b22cd8f0'
PYTHON = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').exists() else sys.executable)
sys.path.insert(0, str(ROOT / 'backend'))
from tests.oidc_mock import LocalProvider


def load_sso_helpers():
    spec = importlib.util.spec_from_file_location('embed_sso_fixture', ROOT / 'frontend/tests/customer-sso-browser.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def port():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        return server.getsockname()[1]


def http(base, path, body=None, token=None, origin=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if origin:
        headers['Origin'] = origin
    with urlopen(Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers), timeout=90) as response:
        return json.load(response)


def ready(url, processes, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert all(process.poll() is None for process in processes), 'An owned proof service exited before readiness'
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (HTTPError, URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(.15)
    raise AssertionError('An owned proof service did not become ready')


def docker(arguments, **kwargs):
    environment = dict(os.environ)
    for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        environment.pop(key, None)
    return subprocess.run(['docker', '--host=unix:///var/run/docker.sock', *arguments], env=environment, check=True, capture_output=True, text=True, **kwargs)


def start_matrix(private, name):
    """Own generated fixture configuration, with the inherited proxy and CA."""
    docker(['info', '--format', '{{.ServerVersion}}'])
    docker(['image', 'inspect', MATRIX_IMAGE, '--format', '{{.Id}}'])
    matrix = private / 'matrix'
    matrix.mkdir(mode=0o700)
    secret = secrets.token_urlsafe(48)
    envfile = private / 'matrix.env'
    envfile.write_text('MATRIX_SERVER_NAME=embed-proof.example\nSYNAPSE_SERVER_NAME=embed-proof.example\nSYNAPSE_REPORT_STATS=no\nMATRIX_REGISTRATION_SHARED_SECRET=' + secret + '\nSYNAPSE_CONFIG_PATH=/data/homeserver.yaml\n')
    envfile.chmod(0o600)
    mount = ['--mount', f'type=bind,src={matrix},dst=/data', '--mount', f'type=bind,src={ROOT / "scripts/secure-matrix-config.py"},dst=/run/secure-config.py,readonly', '--mount', 'type=bind,src=/etc/ssl/certs/ca-certificates.crt,dst=/run/session-ca.pem,readonly', '-e', 'SSL_CERT_FILE=/run/session-ca.pem', '--env-file', str(envfile)]
    # Running as the task UID makes the disposable fixture removable by this
    # same user, including when a later browser assertion fails.
    user = ['--user', str(os.getuid()) + ':' + str(os.getgid())]
    docker(['run', '--rm', *user, *mount, '--entrypoint', 'python', MATRIX_IMAGE, '/run/secure-config.py'])
    selected = port()
    docker(['run', '-d', '--name', name, *user, *mount, '-p', f'127.0.0.1:{selected}:8008', MATRIX_IMAGE])
    ready(f'http://127.0.0.1:{selected}/_matrix/client/versions', [], timeout=90)
    return {'MATRIX_ENABLED': 'true', 'MATRIX_SERVER_NAME': 'embed-proof.example', 'MATRIX_INTERNAL_URL': f'http://127.0.0.1:{selected}', 'MATRIX_REGISTRATION_SHARED_SECRET': secret}


def write_launcher(private, provider, api_port, host_origin):
    """Exact fixture DNS mapping; production TLS and URL validation remain."""
    launcher = private / 'api.py'
    launcher.write_text('''import os,json
from urllib.parse import urlsplit
import uvicorn
from sqlmodel import Session
from app.services import oidc_transport
original_addresses=oidc_transport.public_addresses
original_connect=oidc_transport._PinnedBackend.connect_tcp
fixture_host=''' + repr(urlsplit(provider.issuer).hostname) + '''
fixture_port=''' + str(provider.server.server_port) + '''
def addresses(host,timeout=5):
    return ['127.0.0.1'] if host==fixture_host else original_addresses(host,timeout=timeout)
def connect(self,host,port,timeout=None,local_address=None,socket_options=None):
    if self.hostname==fixture_host and host==self.ip=='127.0.0.1' and port==443:port=fixture_port
    return original_connect(self,host,port,timeout=timeout,local_address=local_address,socket_options=socket_options)
oidc_transport.public_addresses=addresses
oidc_transport._PinnedBackend.connect_tcp=connect
from app.main import socket_app
from app.services import embed as embed_service
# A short lifetime belongs only to this owned test launcher. The production
# default remains ten minutes; normal UI must renew without losing device trust.
if os.environ.get('EMBED_FIXTURE_RENEWAL')=='true':embed_service.CHILD_SESSION_SECONDS=20
from app.database import init_db,engine
from app.models.user import User
from app.models.channel import Channel,ChannelMember
from app.models.workspace import Workspace,WorkspaceMember,WorkspaceChannel
from app.models.workspace_oidc import WorkspaceOIDCProvider,WorkspaceOIDCSession
from app.models.workforce import WorkspaceIdentityPolicy
from app.models.embed import EmbedApplication
from app.services.auth import create_token,token_digest,hash_password
from app.services.workspace_oidc import encrypted_secret
from app.services.workforce import ensure_principal
init_db()
with Session(engine) as session:
    session.add(User(id='owner',name='Private fixture owner',email='owner@embed-proof.example',password_hash=hash_password('Owned-fixture-password!2026')))
    session.add(Workspace(id='team',name='Embedded customer workspace'))
    session.add_all([Channel(id='shared',name='Embedded native conversation'),Channel(id='secure',name='Encrypted fixture'),Channel(id='separate',name='Unshared channel')]);session.commit()
    session.add(WorkspaceMember(workspace_id='team',user_id='owner',role='owner'))
    session.add_all([WorkspaceChannel(workspace_id='team',channel_id=name) for name in ['shared','secure','separate']])
    session.add_all([ChannelMember(channel_id=name,user_id='owner',role='admin') for name in ['shared','separate']])
    session.add(WorkspaceOIDCProvider(workspace_id='team',name='Customer-owned embedded SSO',issuer_url=os.environ['FIXTURE_ISSUER'],client_id=os.environ['FIXTURE_CLIENT_ID'],encrypted_client_secret=encrypted_secret(os.environ['FIXTURE_CLIENT_SECRET'],'team'),created_by='owner',enabled=True,revision=1,tested_revision=1))
    session.add(WorkspaceIdentityPolicy(workspace_id='team',mode='provisioned',identity_claim='sub'))
    identities={}
    for key in ['alpha','beta','outsider']:
        principal=ensure_principal(session,'team','employee-'+key,'Embedded '+key.title(),key+'@embed-proof.example');identities[key]=principal.user_id
        if key!='outsider':session.add_all([ChannelMember(channel_id=name,user_id=principal.user_id,role='admin' if key=='alpha' and name=='secure' else 'member') for name in ['shared','secure']])
    session.add(EmbedApplication(id='app',workspace_id='team',name='Customer product',parent_origin=''' + repr(host_origin) + ''',created_by='owner',enabled=True,revision=1));session.commit()
    tokens={}
    for key,user in identities.items():
        value=create_token(user,session,auth_methods=['sso'],workspace_scope={'workspace_id':'team','provider_revision':1});session.add(WorkspaceOIDCSession(token_hash=token_digest(value),workspace_id='team',provider_revision=1));tokens[key]={'user_id':user,'access_token':value}
    tokens['owner']={'user_id':'owner','access_token':create_token('owner',session)};session.commit()
    with os.fdopen(os.open(os.environ['EMBED_FIXTURE_TOKENS'],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:json.dump(tokens,stream)
uvicorn.run(socket_app,host='127.0.0.1',port=''' + str(api_port) + ''',proxy_headers=False,access_log=False)
''')
    return launcher


def write_preview(private, api_base, app_port):
    script = private / 'preview.mjs'
    script.write_text('import{preview}from' + json.dumps(str(ROOT / 'frontend/node_modules/vite/dist/node/index.js')) + ';await preview({configFile:false,root:' + json.dumps(str(ROOT / 'frontend')) + ',build:{outDir:' + json.dumps(str(ROOT / 'frontend/dist')) + '},preview:{host:"127.0.0.1",port:' + str(app_port) + ',strictPort:true,proxy:{"/api":{target:' + json.dumps(api_base) + ',changeOrigin:true,rewrite:p=>p.replace(/^\\/api/,"")},"/socket.io":{target:' + json.dumps(api_base) + ',ws:true}}}});')
    return script


def make_host(web, api_base, app_base, host_origin, tokens, counters):
    """The customer's server owns scoped login glue; tokens never reach a URL."""
    class Host(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(web), **kwargs)

        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != '/grant':
                return self.send_error(404)
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length < 4096:
                return self.send_error(400)
            data = json.loads(self.rfile.read(length))
            try:
                selected = tokens[data.pop('identity', 'alpha')]
                result = http(api_base, '/workspaces/team/embed/launch', {'application_id': 'app', 'channel_id': data['channelId'], 'parent_origin': host_origin, 'code_challenge': data['codeChallenge']}, selected['access_token'], origin=host_origin)
                counters['grants'] += 1
                payload = json.dumps(result).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'private, no-store')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (KeyError, HTTPError):
                self.send_error(403, 'The fixture user has no channel access')

    (web / 'index.html').write_text('''<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Customer product</title><style>body{margin:0;font:14px system-ui}header{padding:12px}#collaboration{height:820px}iframe{max-width:100%;border:0}</style></head><body><header><h1>Customer product</h1><button id="mount">Open collaboration</button><button id="switch">Switch user</button><button id="disconnect">Disconnect</button><button id="popup">Sign in with customer SSO</button></header><main id="collaboration"></main><script type="module" src="/host.js"></script></body></html>''')
    (web / 'host.js').write_text('''import{mountWorkspace,mountSecureChat}from'/sdk/embed.js';import{createEmbedSignIn}from'/sdk/auth.js';
window.identity='alpha';window.events=[];window.bridgeMessages=[];window.fixtureApp=APP;
window.addEventListener('message',e=>{if(e.origin===APP)window.bridgeMessages.push(e.data)});
const config={appUrl:APP,applicationId:'app',workspaceId:'team',channelId:'shared',locale:'en',theme:{accent:'#884455',font:'Georgia, serif'},onState:s=>window.events.push(s),onError:e=>window.events.push({error:e.code}),grantFactory:async context=>{window.lastChallenge=context.codeChallenge;const response=await fetch('/grant',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...context,signal:undefined,identity:window.identity}),signal:context.signal});if(!response.ok)throw Error('Host fixture denies this user');return response.json()}};
window.mount=async(options={})=>{window.view?.destroy();window.view=(options.secure?mountSecureChat:mountWorkspace)(document.querySelector('#collaboration'),{...config,...options});await window.view.ready};
document.querySelector('#mount').onclick=()=>void window.mount();document.querySelector('#switch').onclick=()=>{window.identity='beta';void window.view.reauthenticate()};document.querySelector('#disconnect').onclick=()=>window.view?.destroy();
window.mountPopup=async()=>{window.signIn=createEmbedSignIn({appUrl:APP,applicationId:'app'});window.signIn.open();window.view?.destroy();window.view=mountWorkspace(document.querySelector('#collaboration'),{...config,grantFactory:window.signIn.grantFactory});await window.view.ready};
document.querySelector('#popup').onclick=()=>void window.mountPopup();
'''.replace('APP', json.dumps(app_base)))
    return Host


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-build', action='store_true', help='Use current completed frontend and SDK builds')
    parser.add_argument('--matrix', action='store_true', help='Prove actual Rust crypto using a new owned private Synapse fixture')
    args = parser.parse_args()
    stage, checks, processes, streams, servers, provider = 'build and pack', {}, [], [], [], None
    revoked_stale_devices = 0
    container = 'freecollab-embed-proof-' + secrets.token_hex(6)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='freecollab-embed-proof-') as temporary:
        private = Path(temporary)
        try:
            if not args.skip_build:
                subprocess.run(['node', str(PACKAGE / 'scripts/build.mjs')], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
                subprocess.run(['npm', 'run', 'build'], cwd=ROOT / 'frontend', check=True, stdout=subprocess.DEVNULL)
            assert (ROOT / 'frontend/dist/embed-entry.js').is_file(), 'Build the actual trusted embed entry before this proof'
            packed = subprocess.run(['npm', '--cache=/tmp/freecollab-sdk-npm-cache', 'pack', '--ignore-scripts', '--json', '--pack-destination', str(private)], cwd=PACKAGE, check=True, capture_output=True, text=True)
            archive = private / json.loads(packed.stdout)[0]['filename']
            with tarfile.open(archive) as tar:
                assert all(member.name.startswith('package/') and '..' not in Path(member.name).parts for member in tar.getmembers())
                tar.extractall(private / 'packed', filter='data')
            package_release = json.loads((private / 'packed/package/dist/release.json').read_text())
            assert package_release['apiLine'] == '0.10' and './embed' in package_release['entries'] and './auth' in package_release['entries']
            web = private / 'web'
            web.mkdir()
            shutil.copytree(private / 'packed/package/dist', web / 'sdk')
            sdk_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
            frontend_dist = ROOT / 'frontend/dist'
            frontend_manifest = {str(path.relative_to(frontend_dist)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(frontend_dist.rglob('*')) if path.is_file()}
            sources = [PACKAGE / 'src/embed.ts', PACKAGE / 'src/auth.ts', ROOT / 'frontend/src/components/EmbedSignIn.tsx', ROOT / 'frontend/src/components/EmbedApp.tsx', ROOT / 'backend/app/api/embed.py', ROOT / 'backend/app/services/embed.py']
            source_snapshot = {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
            checks['actual_packed_esm_embed_and_auth_modules'] = True
            api_port, app_port, host_port, attacker_port = port(), port(), port(), port()
            api_base, app_base, host_origin, attacker_origin = (f'http://127.0.0.1:{selected}' for selected in (api_port, app_port, host_port, attacker_port))
            helpers = load_sso_helpers()
            cert, key = helpers.public_tls_fixture(private / 'tls')
            provider = LocalProvider(cert_path=cert, key_path=key, client_id='embed-customer-owned', client_secret=secrets.token_urlsafe(40))
            provider.issuer = 'https://' + helpers.FIXTURE_HOSTS[0]
            provider.subject, provider.name, provider.email = 'employee-alpha', 'Embedded Alpha', 'alpha@embed-proof.example'
            trust = private / 'ca-bundle.pem'
            inherited = os.getenv('SSL_CERT_FILE') or ssl.get_default_verify_paths().cafile
            trust.write_bytes((Path(inherited).read_bytes() if inherited else b'') + b'\n' + Path(cert).read_bytes())
            environment = dict(os.environ)
            environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(private / 'proof.db'), STORAGE_PATH=str(private / 'files'), JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), MESSAGE_ENCRYPTION_KEYS=json.dumps({'embed-proof': base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='embed-proof', MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'message-keys.json'), MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled', OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', OTRUST_ENABLED='false', SYNC_ENABLED='false', CLOUD_FILES_ENABLED='false', EXTERNAL_MEETINGS_ENABLED='false', COPILOT_ENABLED='false', PUBLIC_APP_URL=app_base, CORS_ORIGINS=json.dumps([app_base, host_origin]), PYTHONPATH=str(ROOT / 'backend'), EMBED_FIXTURE_TOKENS=str(private / 'tokens.json'), FIXTURE_ISSUER=provider.issuer, FIXTURE_CLIENT_ID=provider.client_id, FIXTURE_CLIENT_SECRET=provider.client_secret, SSL_CERT_FILE=str(trust))
            environment['EMBED_FIXTURE_RENEWAL'] = 'true'
            environment['NO_PROXY'] = ','.join(filter(None, [environment.get('NO_PROXY', ''), 'localhost', '127.0.0.1', '::1', urlsplit(provider.issuer).hostname]))
            environment['no_proxy'] = ','.join(filter(None, [environment.get('no_proxy', ''), environment['NO_PROXY']]))
            if args.matrix:
                stage = 'new isolated private Matrix fixture'
                environment.update(start_matrix(private, container))
            stage = 'private API and actual built frontend'
            for command, filename, cwd in [([PYTHON, str(write_launcher(private, provider, api_port, host_origin))], 'api.log', ROOT / 'backend'), (['node', str(write_preview(private, api_base, app_port))], 'frontend.log', ROOT / 'frontend')]:
                stream = (private / filename).open('w')
                streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, start_new_session=True))
            ready(api_base + '/ready', processes)
            ready(app_base + '/', processes)
            tokens = json.loads((private / 'tokens.json').read_text())
            counters = {'grants': 0}
            host = ThreadingHTTPServer(('127.0.0.1', host_port), make_host(web, api_base, app_base, host_origin, tokens, counters))
            evil_web = private / 'attacker'
            evil_web.mkdir()
            (evil_web / 'index.html').write_text('<!doctype html><html><title>Unregistered host</title><body><h1>Unregistered host</h1></body></html>')
            class Attacker(SimpleHTTPRequestHandler):
                def __init__(self, *a, **k): super().__init__(*a, directory=str(evil_web), **k)
                def log_message(self, *a): pass
            attacker = ThreadingHTTPServer(('127.0.0.1', attacker_port), Attacker)
            for server in (host, attacker):
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                servers.append((server, thread))
            with sync_playwright() as playwright:
                resolver = 'MAP ' + urlsplit(provider.issuer).hostname + ':443 127.0.0.1:' + str(provider.server.server_port)
                # Trust only this generated fixture certificate's exact public
                # key in Chromium. There is no global TLS-verification bypass.
                # The production API verifies its hostname and generated CA.
                certificate = x509.load_pem_x509_certificate(Path(cert).read_bytes())
                spki = certificate.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
                pin = base64.b64encode(hashlib.sha256(spki).digest()).decode()
                browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), headless=True, args=['--no-sandbox', '--host-resolver-rules=' + resolver, '--ignore-certificate-errors-spki-list=' + pin], env={**os.environ, 'NO_PROXY': environment['NO_PROXY'], 'no_proxy': environment['no_proxy']})
                context = browser.new_context(viewport={'width':1440, 'height':1000}, reduced_motion='reduce')
                # A pre-existing standalone-client session must survive an
                # embedded connection and a popup login on this same trusted
                # origin. These fixed sentinels are deliberately not real JWTs.
                sentinels = {'freecollab_token':'unrelated-standalone-session-sentinel','freecollab_user_id':'unrelated-standalone-user','freecollab_public_key':'unrelated-public-key','freecollab_encrypted_private_key':'unrelated-encrypted-key'}
                context.add_init_script('if(location.origin===' + json.dumps(app_base) + '){for(const[key,value]of Object.entries(' + json.dumps(sentinels) + ')){if(!localStorage.getItem(key))localStorage.setItem(key,value)}}')
                page = context.new_page()
                page.set_default_timeout(45000)
                urls, errors, bodies, children = [], [], [], []
                context.on('request', lambda request: (urls.append(request.url), bodies.append((request.url, request.post_data or '')) if request.method in ('POST', 'PUT') else None))
                def child_session(response):
                    if response.status == 200 and urlsplit(response.url).path in ('/api/embed/app/exchange', '/api/embed/app/renew'):
                        value = response.json()
                        children.append({'kind':urlsplit(response.url).path.rsplit('/',1)[1], 'token':value['access_token'], 'time':time.monotonic(), 'user':value['user_id'], 'expires_at':value['expires_at']})
                context.on('response', child_session)
                context.on('page', lambda opened: opened.on('pageerror', lambda error: errors.append(type(error).__name__)))
                page.on('pageerror', lambda error: errors.append(type(error).__name__))
                try:
                    stage = 'cross-origin actual trusted-client bootstrap and scoped grant exchange'
                    page.goto(host_origin, wait_until='networkidle')
                    page.get_by_role('button', name='Open collaboration', exact=True).click()
                    frame = page.frame_locator('#collaboration iframe:visible')
                    expect(frame.locator('[data-embed-state="ready"]')).to_be_visible(timeout=60000)
                    expect(frame.locator('.fc-chat')).to_be_visible()
                    assert page.evaluate('new URL(document.querySelector("#collaboration iframe").src).origin!==location.origin')
                    assert counters['grants'] == 1
                    assert page.evaluate('localStorage.length===0&&sessionStorage.length===0')
                    checks['cross_origin_built_client_uses_real_single_use_scoped_exchange'] = True
                    assert frame.locator('#root').evaluate('(el,expected)=>Object.entries(expected).every(([key,value])=>localStorage.getItem(key)===value)', sentinels)
                    checks['embedding_never_adopts_or_overwrites_standalone_origin_session'] = True
                    # This additional real grant is deliberately never mounted
                    # and never renewed. Its signed JWT outlives its short test
                    # lease, so later rejection demonstrates the DB lease fence.
                    unmounted_verifier = secrets.token_urlsafe(32)
                    unmounted_challenge = base64.urlsafe_b64encode(hashlib.sha256(unmounted_verifier.encode()).digest()).rstrip(b'=').decode()
                    unmounted_grant = http(api_base, '/workspaces/team/embed/launch', {'application_id':'app','channel_id':'shared','parent_origin':host_origin,'code_challenge':unmounted_challenge}, tokens['alpha']['access_token'], origin=host_origin)
                    unmounted = http(api_base, '/embed/app/exchange', {'code':unmounted_grant['code'],'code_verifier':unmounted_verifier,'parent_origin':host_origin,'channel_id':'shared'}, origin=app_base)

                    stage = 'embedded actual native conversation write'
                    composer = frame.get_by_role('textbox', name='Message', exact=True)
                    if composer.count() == 0:
                        composer = frame.locator('textarea').first
                    composer.fill('Embedded customer product native message')
                    frame.get_by_role('button', name='Send message', exact=True).click()
                    expect(frame.locator('.fc-chat-message').filter(has_text='Embedded customer product native message')).to_be_visible()
                    native_rows = http(api_base, '/messages/channels/shared', token=tokens['alpha']['access_token'])
                    assert any(row['content'] == 'Embedded customer product native message' for row in native_rows)
                    checks['embedded_native_message_actual_production_api_write'] = True
                    assert frame.locator('#root').evaluate('()=>getComputedStyle(document.body).getPropertyValue("--fc-accent").trim()==="#884455"&&getComputedStyle(document.querySelector(".fc-chat")).fontFamily.includes("Georgia")')
                    checks['host_theme_and_font_survive_account_preference_loading'] = True

                    stage = 'source and nonce spoof does not authorize or reconnect'
                    before = counters['grants']
                    page.evaluate('''()=>{const frame=document.querySelector('#collaboration iframe');const nonce=new URL(frame.src).hash.slice(1).replace(/^nonce=/,'');for(const message of [{type:'ready',nonce},{type:'state',state:'signed-out',nonce},{type:'ready',nonce:'wrong-nonce'}])window.dispatchEvent(new MessageEvent('message',{origin:window.fixtureApp,source:window,data:{protocol:'freecollab-embed-v1',...message}}))}''')
                    expect(frame.locator('.fc-chat-message').filter(has_text='Embedded customer product native message')).to_be_visible()
                    assert counters['grants'] == before
                    checks['spurious_messages_with_wrong_source_and_nonce_cannot_reconnect'] = True

                    stage = 'host DOM cannot read trusted iframe and bridge carries no bearer/content'
                    blocked = page.evaluate('''()=>{try{return document.querySelector('#collaboration iframe').contentWindow.document.body.innerText}catch(e){return e.name}}''')
                    assert blocked == 'SecurityError'
                    bridge = page.evaluate('JSON.stringify(window.bridgeMessages)')
                    assert 'Embedded customer product native message' not in bridge
                    assert all(record['access_token'] not in bridge for record in tokens.values())
                    checks['trusted_frame_separates_dom_and_no_plaintext_content_or_jwt_bridge'] = True

                    stage = 'account switching remounts isolated session and attribution'
                    page.get_by_role('button', name='Switch user', exact=True).click()
                    expect(frame.locator('[data-embed-state="ready"]')).to_be_visible(timeout=60000)
                    composer = frame.locator('textarea').first
                    composer.fill('Embedded Beta wrote after account change')
                    frame.get_by_role('button', name='Send message', exact=True).click()
                    expect(frame.locator('.fc-chat-message').filter(has_text='Embedded Beta wrote after account change')).to_be_visible()
                    native_rows = http(api_base, '/messages/channels/shared', token=tokens['alpha']['access_token'])
                    assert any(row['content'] == 'Embedded Beta wrote after account change' and row['author_id'] == tokens['beta']['user_id'] for row in native_rows)
                    checks['account_switch_new_session_attribution'] = True

                    stage = 'mobile actual built iframe and teardown'
                    page.set_viewport_size({'width':320, 'height':1000})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.screenshot(path=str(REPORT.with_name('embed-product-mobile.png')), full_page=True)
                    previous_child = children[-1]['token']
                    page.get_by_role('button', name='Disconnect', exact=True).click()
                    expect(page.locator('#collaboration iframe')).to_have_count(0)
                    try:
                        http(api_base, '/auth/me', token=previous_child)
                    except HTTPError as failure:
                        assert failure.code == 401
                    else:
                        raise AssertionError('Acknowledged frame teardown left the old child session active')
                    assert http(api_base, '/auth/me', token=tokens['beta']['access_token'])['id'] == tokens['beta']['user_id']
                    checks['320px_host_and_destroy_removes_private_frame'] = True
                    checks['acknowledged_teardown_revokes_only_child_not_customer_source_session'] = True
                    page.set_viewport_size({'width':1440, 'height':1000})

                    stage = 'unregistered parent document refusal and actual CSP ancestor denial'
                    denied = context.request.get(app_base + '/api/embed/app/document?' + urlencode({'parent_origin':attacker_origin}))
                    assert denied.status in (403, 404)
                    legit_url = app_base + '/api/embed/app/document?' + urlencode({'parent_origin':host_origin})
                    policy = context.request.get(legit_url)
                    assert host_origin in policy.headers.get('content-security-policy', '') and attacker_origin not in policy.headers.get('content-security-policy', '')
                    evil = context.new_page()
                    evil.goto(attacker_origin)
                    evil.evaluate('url=>{const frame=document.createElement("iframe");frame.src=url;document.body.append(frame)}', legit_url)
                    expect(evil.frame_locator('iframe').locator('#root')).to_have_count(0)
                    evil.close()
                    checks['exact_registered_origin_and_browser_csp_ancestor_fence'] = True

                    stage = 'real customer OIDC popup to PKCE frame session'
                    # The click imports the packed auth helper and its popup.
                    # Start a popup synchronously inside the host user gesture.
                    with page.expect_popup(timeout=45000) as popup_event:
                        page.get_by_role('button', name='Sign in with customer SSO', exact=True).click()
                    popup = popup_event.value
                    expect(popup.get_by_role('button', name='Continue to organization', exact=True)).to_be_visible(timeout=60000)
                    popup.get_by_role('button', name='Continue to organization', exact=True).click()
                    expect(frame.locator('[data-embed-state="ready"]')).to_be_visible(timeout=90000)
                    assert provider.exchange_count >= 1
                    assert page.evaluate('localStorage.length===0&&sessionStorage.length===0')
                    assert frame.locator('#root').evaluate('(el,expected)=>Object.entries(expected).every(([key,value])=>localStorage.getItem(key)===value)', sentinels)
                    checks['actual_customer_oidc_popup_callback_and_pkce_launch'] = True

                    if args.matrix:
                        stage = 'actual new private Matrix encrypted iframe proof'
                        # The managed identity has workspace-scoped crypto and
                        # needs neither a personal password nor foreign keys.
                        http(api_base, '/secure/channels/secure/enable', {}, tokens['alpha']['access_token'])
                        page.evaluate('window.identity="alpha";window.mount({channelId:"secure",secure:true})')
                        expect(frame.locator('[data-embed-state="ready"]')).to_be_visible(timeout=90000)
                        # A peer test context uses the same built trusted frame;
                        # direct access is through this harness's trusted origin
                        # only. No parent integration API exposes decrypted data.
                        peer_context = browser.new_context(viewport={'width':1440,'height':1000})
                        peer = peer_context.new_page()
                        peer.goto(host_origin, wait_until='networkidle')
                        peer.evaluate('window.identity="beta";window.mount({channelId:"secure",secure:true})')
                        peer_frame = peer.frame_locator('#collaboration iframe:visible')
                        expect(peer_frame.locator('[data-embed-state="ready"]')).to_be_visible(timeout=90000)
                        # Use only the actual shipped device UI, including its
                        # explicit fingerprint comparison. No test crypto API or
                        # second implementation is injected into the product.
                        dialogs = []
                        for target in (frame, peer_frame):
                            target.get_by_role('button', name='End-to-end encrypted', exact=True).click()
                            dialog = target.get_by_role('dialog', name='Encryption devices and recovery', exact=True)
                            expect(dialog.get_by_role('button', name='Channel devices', exact=True)).to_be_visible(timeout=90000)
                            dialog.get_by_role('button', name='Channel devices', exact=True).click()
                            expect(dialog.locator('.fc-secure-fingerprint code')).to_be_visible(timeout=90000)
                            assert dialog.evaluate('el=>getComputedStyle(el).getPropertyValue("--fc-accent").trim()==="#884455"')
                            dialogs.append(dialog)
                        fingerprints = [dialog.locator('.fc-secure-fingerprint code').inner_text().replace(' ', '') for dialog in dialogs]
                        device_ids = [dialog.locator('.fc-secure-device-id').first.inner_text() for dialog in dialogs]
                        # Previous mounts may have left a disconnected device.
                        # A current managed SSO user explicitly revokes their own
                        # stale devices through the shipped UI. Never verify an
                        # unavailable device using another device's fingerprint.
                        for dialog in dialogs:
                            current_device = dialog.locator('.fc-secure-device-list li').filter(has_text='This browser')
                            deadline = time.monotonic() + 45
                            while current_device.count() != 1 and time.monotonic() < deadline:
                                dialog.get_by_role('button', name='Refresh encryption devices', exact=True).click()
                                expect(dialog.locator('.fc-secure-fingerprint code')).to_be_visible(timeout=90000)
                                page.wait_for_timeout(500)
                            expect(current_device).to_have_count(1)
                            dialog.get_by_role('button', name='Refresh encryption devices', exact=True).click()
                            expect(dialog.locator('.fc-secure-fingerprint code')).to_be_visible(timeout=90000)
                            # The visible label is "Revoke lost device", but
                            # aria-label includes the exact public device ID.
                            stale = dialog.locator('.fc-secure-device-list li').filter(has_text='Your account').get_by_role('button', name=re.compile(r'^Revoke encryption device '))
                            while stale.count():
                                stale.first.click()
                                revoke = dialog.get_by_role('form', name='Revoke encryption device', exact=True)
                                revoke.get_by_label('I understand this device will lose access to new encrypted messages.', exact=True).check()
                                revoke.get_by_role('button', name='Revoke device', exact=True).click()
                                expect(revoke).to_have_count(0, timeout=90000)
                                revoked_stale_devices += 1
                        checks['current_browser_device_published_before_peer_fingerprint_verification'] = True
                        for index, (dialog, other) in enumerate(zip(dialogs, ('beta', 'alpha'))):
                            dialog.get_by_role('button', name='Refresh encryption devices', exact=True).click()
                            expect(dialog.locator('.fc-secure-fingerprint code')).to_be_visible(timeout=90000)
                            peer_row = dialog.locator('.fc-secure-device-list li').filter(has_text='Account ' + tokens[other]['user_id'])
                            deadline = time.monotonic() + 45
                            while peer_row.count() != 1 and time.monotonic() < deadline:
                                dialog.get_by_role('button', name='Refresh encryption devices', exact=True).click()
                                expect(dialog.locator('.fc-secure-fingerprint code')).to_be_visible(timeout=90000)
                                page.wait_for_timeout(250)
                            if peer_row.count() != 1:
                                # Public device metadata from synthetic accounts
                                # only; never bearer tokens or recovery material.
                                (REPORT.parent / 'embed-device-diagnostics.json').write_text(json.dumps({'dialog_index':index,'fingerprints':fingerprints,'device_ids':device_ids,'device_lists':[value.locator('.fc-secure-device-list li').all_text_contents() for value in dialogs]},indent=2))
                            expect(peer_row).to_have_count(1)
                            peer_row.get_by_role('button', name='Compare and verify device', exact=True).click()
                            dialog.get_by_label('Fingerprint from the other device', exact=True).fill(fingerprints[1-index])
                            dialog.get_by_label('I checked this fingerprint through a separate trusted channel.', exact=True).check()
                            dialog.get_by_role('button', name='Verify device', exact=True).click()
                            expect(peer_row.get_by_text('Verified', exact=True)).to_be_visible(timeout=90000)
                            dialog.get_by_role('button', name='Done', exact=True).click()
                        secret_text = 'Actual encrypted message from the embedded customer product'
                        frame.locator('textarea').first.fill(secret_text)
                        frame.get_by_role('button', name='Send message', exact=True).click()
                        expect(frame.locator('.fc-chat-message').filter(has_text=secret_text)).to_be_visible(timeout=90000)
                        expect(peer_frame.locator('.fc-chat-message').filter(has_text=secret_text)).to_be_visible(timeout=90000)
                        wire = [json.loads(body) for url, body in bodies if '/send/m.room.encrypted/' in url]
                        assert wire and any(record.get('algorithm') == 'm.megolm.v1.aes-sha2' and record.get('ciphertext') for record in wire)
                        assert all(secret_text not in json.dumps(record) for record in wire)
                        assert not any(secret_text in body for url, body in bodies if '/_matrix/' in url)
                        assert not any('/messages/channels/secure' in url for url in urls)
                        assert secret_text not in page.evaluate('JSON.stringify(window.bridgeMessages)')
                        assert page.evaluate('localStorage.length===0&&sessionStorage.length===0')
                        checks['actual_trusted_frame_rust_crypto_verified_peer_decrypts_ui_send'] = True
                        checks['matrix_wire_encrypted_no_plaintext_native_fallback_or_parent_bridge'] = True
                        # A separate recovery key, rather than the SSO session,
                        # is still required to recover encrypted history.
                        frame.get_by_role('button', name='End-to-end encrypted', exact=True).click()
                        dialog = frame.get_by_role('dialog', name='Encryption devices and recovery', exact=True)
                        dialog.get_by_role('button', name='Recovery', exact=True).click()
                        dialog.get_by_role('button', name='Set up encrypted history recovery', exact=True).click()
                        expect(dialog.locator('.fc-secure-generated-key code')).to_be_visible(timeout=90000)
                        recovery = dialog.locator('.fc-secure-generated-key code').inner_text()
                        assert isinstance(recovery, str) and len(recovery) > 32
                        assert recovery not in page.evaluate('JSON.stringify(window.bridgeMessages)')
                        assert all(recovery not in url for url in urls)
                        checks['encrypted_recovery_key_stays_inside_trusted_origin'] = True
                        dialog.get_by_label('I have stored this recovery key somewhere safe.', exact=True).check()
                        dialog.get_by_role('button', name='I have saved my key', exact=True).click()
                        dialog.get_by_role('button', name='Done', exact=True).click()
                        stage = 'automatic real child renewal preserves the trusted Matrix device'
                        # Capture one currently usable child, then observe actual
                        # scheduled renewal rather than calling a test-only API.
                        before = children[-1]['token']
                        previous_expiry = children[-1]['expires_at']
                        initial_rotations = sum(item['kind']=='renew' for item in children)
                        deadline = time.monotonic() + 35
                        while sum(item['kind']=='renew' for item in children) <= initial_rotations and time.monotonic() < deadline:
                            page.wait_for_timeout(200)
                        assert sum(item['kind']=='renew' for item in children) > initial_rotations
                        assert children[-1]['token'] == before
                        assert children[-1]['expires_at'] > previous_expiry
                        assert http(api_base, '/auth/me', token=before)['id'] == tokens['alpha']['user_id']
                        frame.get_by_role('button', name='End-to-end encrypted', exact=True).click()
                        dialog = frame.get_by_role('dialog', name='Encryption devices and recovery', exact=True)
                        dialog.get_by_role('button', name='Channel devices', exact=True).click()
                        expect(dialog.locator('.fc-secure-fingerprint code')).to_be_visible(timeout=90000)
                        assert dialog.locator('.fc-secure-fingerprint code').inner_text().replace(' ', '') == fingerprints[0]
                        assert dialog.locator('.fc-secure-device-id').first.inner_text() == device_ids[0]
                        peer_row = dialog.locator('.fc-secure-device-list li').filter(has_text='Account ' + tokens['beta']['user_id'])
                        expect(peer_row.get_by_text('Verified', exact=True)).to_be_visible()
                        dialog.get_by_role('button', name='Done', exact=True).click()
                        refreshed_text = 'Encrypted conversation continues after automatic session renewal'
                        frame.locator('textarea').first.fill(refreshed_text)
                        frame.get_by_role('button', name='Send message', exact=True).click()
                        expect(peer_frame.locator('.fc-chat-message').filter(has_text=refreshed_text)).to_be_visible(timeout=90000)
                        assert any('/send/m.room.encrypted/' in url and refreshed_text not in body for url, body in bodies)
                        checks['automatic_authoritative_lease_renewal_preserves_credential_device_and_verified_crypto'] = True
                        peer_context.close()

                    stage = 'unmounted child expires while a live frame renews its authoritative lease'
                    deadline = unmounted['expires_at'] + 1
                    while time.time() < deadline:
                        page.wait_for_timeout(200)
                    try:
                        http(api_base, '/auth/me', token=unmounted['access_token'])
                    except HTTPError as failure:
                        assert failure.code == 401
                    else:
                        raise AssertionError('An unmounted embedding bearer bypassed its expired authoritative lease')
                    assert http(api_base, '/auth/me', token=children[-1]['token'])['id'] == tokens['alpha']['user_id']
                    checks['unmounted_expired_lease_denied_while_live_embed_renews'] = True

                    stage = 'native administrator registers and copies a real integration in the product UI'
                    admin_context = browser.new_context(viewport={'width':1440,'height':1000}, permissions=['clipboard-read','clipboard-write'])
                    admin = admin_context.new_page()
                    admin.set_default_timeout(45000)
                    admin.goto(app_base, wait_until='networkidle')
                    admin.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
                    admin.get_by_label('Email', exact=True).fill('owner@embed-proof.example')
                    admin.get_by_label('Password', exact=True).fill('Owned-fixture-password!2026')
                    admin.get_by_role('button', name='Sign In', exact=True).click()
                    expect(admin.locator('.fc-chat')).to_be_visible(timeout=90000)
                    admin.get_by_role('button', name='Integrations', exact=True).click()
                    settings = admin.get_by_role('dialog', name='Integrations', exact=True)
                    settings.get_by_role('button', name='Embedding', exact=True).click()
                    application_settings = settings.get_by_role('region', name='Embedded applications', exact=True)
                    application_settings.get_by_role('button', name='New application', exact=True).click()
                    application_settings.get_by_label('Application name', exact=True).fill('Browser registered customer product')
                    application_settings.get_by_label('Application origin', exact=True).fill(attacker_origin)
                    application_settings.get_by_label('Confirm with your FreeCollab password', exact=True).fill('Owned-fixture-password!2026')
                    application_settings.get_by_role('button', name='Register application', exact=True).click()
                    expect(application_settings.get_by_text('Application saved. Copy the integration example for your product.', exact=True)).to_be_visible()
                    expect(application_settings.get_by_label('Confirm with your FreeCollab password', exact=True)).to_have_value('')
                    application_settings.get_by_role('button', name='Copy integration example', exact=True).click()
                    copied = admin.evaluate('navigator.clipboard.readText()')
                    assert 'createEmbedSignIn' in copied and 'mountWorkspace' in copied and app_base in copied
                    assert all(record['access_token'] not in copied for record in tokens.values())
                    checks['native_admin_ui_registration_reauthentication_and_credential_free_example'] = True

                    stage = 'registration disable clears the actual embedded client on current authorization'
                    application_settings.get_by_role('button', name='Customer product · Enabled', exact=True).click()
                    application_settings.get_by_label('Allow embedding from this origin', exact=True).uncheck()
                    application_settings.get_by_label('Confirm with your FreeCollab password', exact=True).fill('Owned-fixture-password!2026')
                    application_settings.get_by_role('button', name='Save application', exact=True).click()
                    expect(application_settings.get_by_text('Application disabled. Its embedding grants and sessions are no longer valid.', exact=True)).to_be_visible()
                    if frame.locator('[data-embed-state="ready"]').count():
                        frame.get_by_role('button', name='Refresh messages', exact=True).click()
                    expect(frame.locator('[data-embed-state="signed-out"]')).to_be_visible(timeout=45000)
                    expect(frame.locator('.fc-chat')).to_have_count(0)
                    assert http(api_base, '/auth/me', token=tokens['alpha']['access_token'])['id'] == tokens['alpha']['user_id']
                    checks['admin_ui_disable_revokes_live_embedding_and_clears_private_content'] = True
                    admin_context.close()

                    stage = 'no bearer request URLs and browser errors'
                    assert all(record['access_token'] not in url for record in tokens.values() for url in urls)
                    assert all(record['token'] not in url for record in children for url in urls)
                    assert not errors, 'Unexpected page error in actual embedded product'
                    checks['no_bearer_in_urls_and_zero_page_errors'] = True
                finally:
                    context.close()
                    browser.close()
        except Exception:
            print('FAILED embedded browser proof stage: ' + stage, file=sys.stderr)
            # A bounded category avoids logging one-use codes, tokens or secret
            # fixture provider data on an assertion failure.
            for name in ('api.log', 'frontend.log'):
                filename = private / name
                if filename.exists():
                    text = filename.read_text()
                    if 'Traceback' in text or 'Error' in text:
                        print(name + ': owned service recorded a startup/runtime error (private log retained only for this run)', file=sys.stderr)
            raise
        finally:
            for server, thread in reversed(servers):
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
            for process in reversed(processes):
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
            for process in reversed(processes):
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            for stream in streams:
                stream.close()
            if provider:
                provider.close()
            if args.matrix:
                try:
                    docker(['rm', '-f', container])
                except subprocess.CalledProcessError:
                    pass
        assert source_snapshot == {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}, 'Sensitive source changed while the proof was running; rebuild and rerun against the final artifact'
        assert frontend_manifest == {str(path.relative_to(frontend_dist)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(frontend_dist.rglob('*')) if path.is_file()}, 'Frontend artifacts changed during the proof'
        REPORT.write_text(json.dumps({'scope':'Actual packed SDK across independent host/trusted deployment origins, private production API and scoped managed accounts, synthetic HTTPS customer-owned OIDC provider; no live customer provider, registry publication or deployment', 'matrix_tested':args.matrix, 'revoked_stale_devices':revoked_stale_devices, 'packed_sha256':sdk_hash, 'package_release':package_release, 'frontend_dist_sha256':hashlib.sha256(json.dumps(frontend_manifest,sort_keys=True).encode()).hexdigest(), 'tls_fixture_trust':'Production transport validates fixture hostname and generated CA; Chromium trusts only the ephemeral leaf SPKI; no global HTTPS verification bypass', 'checks':checks, 'source_sha256':source_snapshot, 'cleanup':'Owned API, preview, host, attacker, IdP, browser contexts and temporary data removed; original services and data retained'}, indent=2) + '\n')
    print('Embedded product browser proof passed: ' + str(len(checks)) + ' checks.')


if __name__ == '__main__':
    main()
