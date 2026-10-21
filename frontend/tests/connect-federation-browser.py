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
REPORT = ROOT.parent / '.freecollab/proof-results/connect-federation-browser.json'
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
    network = 'fc-federation-proof-' + secrets.token_hex(5)
    browser = None
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='freecollab-federation-proof-') as directory:
        private = Path(directory); private.chmod(0o700)
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
            critical_paths = [ROOT/'backend/app/api/connect_federation.py', ROOT/'backend/app/services/connect_federation.py', ROOT/'backend/app/models/connect_federation.py', ROOT/'backend/app/api/secure_messaging.py', ROOT/'backend/app/api/channels.py', ROOT/'backend/app/services/matrix_bridge.py', ROOT/'backend/app/services/workspaces.py', ROOT/'backend/app/services/connect.py', ROOT/'frontend/src/lib/secure/index.ts', ROOT/'frontend/src/lib/secure/files.ts']
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
            host=succeeds(api_bases['a'],'/connect/federation/workspaces/a/connections',{'name':'Independent encrypted channel','peer_origin':'https://connect-b.example.com','peer_workspace_id':'b'},identities['a']['admin']['token'])
            guest=succeeds(api_bases['b'],'/connect/federation/workspaces/b/accept',{'peer_origin':'https://connect-a.example.com','invitation_token':host['invitation_token']},identities['b']['admin']['token'])
            connections={'a':host,'b':guest}
            checks['real_signed_https_acceptance_binds_distinct_local_channels_to_one_federated_room']=host['channel_id']!=guest['channel_id']
            for side in 'ab':
                succeeds(api_bases[side],f'/connect/federation/workspaces/{side}/connections/'+host['id']+'/members',{'user_id':identities[side]['member']['user_id']},identities[side]['admin']['token'])
            for side in 'ab':succeeds(api_bases[side],f'/connect/federation/workspaces/{side}/connections/'+host['id']+'/sync',{},identities[side]['admin']['token'])
            stream=(private/'vite.log').open('wb');streams.append(stream)
            processes.append(subprocess.Popen(['node',str(ROOT/'frontend/node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port',str(web_port),'--strictPort'],cwd=ROOT/'frontend',stdout=stream,stderr=subprocess.STDOUT,start_new_session=True))
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
                    context=browser.new_context();contexts.append(context);page=context.new_page();pages.append(page)
                    records=[];traffic.append(records)
                    page.on('request',lambda req,rows=records:rows.append((req.url,req.method,req.post_data or '')) if '/_matrix/' in req.url else None)
                    page.goto(web_origin,wait_until='domcontentloaded')
                    page.evaluate('''async({base,token,user,channel})=>{const m=await import('/src/lib/secure/index.ts');m.configureSecureMessaging({baseUrl:base,getAccessToken:()=>token,getUserId:()=>user});window.cryptoProof=m.secureMessaging;await window.cryptoProof.ensureSession();await window.cryptoProof.roomFor(channel);window.proofChannel=channel}''',{'base':api_bases[side],'token':identities[side]['member']['token'],'user':identities[side]['member']['user_id'],'channel':connections[side]['channel_id']})
                stage='cross-homeserver device query and fingerprint verification'
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
                stage='current remote participant revocation and peer fail-closed send'
                succeeds(api_bases['b'],'/connect/federation/workspaces/b/connections/'+host['id']+'/members/'+identities['b']['member']['user_id'],token=identities['b']['admin']['token'],method='DELETE')
                assert request(api_bases['b'],'/channels/'+guest['channel_id'],token=identities['b']['member']['token'])[0]==403
                succeeds(api_bases['a'],'/connect/federation/workspaces/a/connections/'+host['id']+'/sync',{},identities['a']['admin']['token'])
                checks['remote_removal_denies_own_product_access_and_updates_other_homeserver_membership']=True
                stage='bilateral durable connection revocation'
                succeeds(api_bases['b'],'/connect/federation/workspaces/b/connections/'+host['id'],token=identities['b']['admin']['token'],method='DELETE')
                for side in 'ab':assert request(api_bases[side],'/channels/'+connections[side]['channel_id'],token=identities[side]['member']['token'])[0]==403
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
            for container in containers:
                with suppress(Exception):docker(['rm','-f',container])
            with suppress(Exception):docker(['network','rm',network])


if __name__=='__main__':main()
