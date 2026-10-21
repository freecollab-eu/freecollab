#!/usr/bin/env python3
"""Native Chromium service worker, offline vault and reconnect proof.

Builds the complete production app into an owned temporary directory and runs
only private API/static services with independent fixture accounts and keys.
No existing service, browser profile or live push provider is used.
"""
import base64
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(os.getenv('MOBILE_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/offline-library-browser.json')))


def port():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0)); return server.getsockname()[1]


def ready(url, process):
    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        assert process.poll() is None, 'Private API exited before readiness'
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200: return
        except Exception: pass
        time.sleep(.2)
    raise AssertionError('Private API did not become ready')


VAULT = '''async()=>{const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('freecollab-mobile-v1');r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)});try{return await new Promise((resolve,reject)=>{const tx=db.transaction(['keys','vault']);const keys=tx.objectStore('keys').getAll(),rows=tx.objectStore('vault').getAll();tx.oncomplete=()=>resolve({keys:keys.result.map(k=>({type:k.type,extractable:k.extractable,algorithm:k.algorithm.name})),rows:rows.result.map(r=>({id:r.id,owner:r.owner,kind:r.kind,ciphertext:[...new Uint8Array(r.ciphertext)],fields:Object.keys(r)}))});tx.onerror=()=>reject(tx.error)})}finally{db.close()}}'''


def main():
    results, processes, streams, stage = {}, [], [], 'private services'
    with tempfile.TemporaryDirectory(prefix='freecollab-mobile-') as directory:
        private = Path(directory); api_port, app_port = port(), port()
        api_base, app = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
        environment = os.environ.copy(); environment.update(ENVIRONMENT='test',DATABASE_URL='sqlite:///'+str(private/'proof.db'),STORAGE_PATH=str(private/'files'),JWT_SECRET=secrets.token_urlsafe(48),ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',MATRIX_ENABLED='false',MAIL_DELIVERY_MODE='disabled',OIDC_ENABLED='false',WEBHOOKS_ENABLED='false',OTRUST_ENABLED='false',SYNC_ENABLED='false',PUSH_ENABLED='false',WORKFLOWS_ENABLED='false',DATA_CONTROLS_ENABLED='false',PUBLIC_APP_URL=app,CORS_ORIGINS=json.dumps([app]),PYTHONPATH=str(ROOT/'backend'),PROOF_CREDENTIAL_FILE=str(private/'access.json'))
        environment['NO_PROXY'] = ','.join(filter(None,[environment.get('NO_PROXY',''),'localhost','127.0.0.1','::1']))
        python = os.getenv('BACKEND_PYTHON') or str(ROOT/'backend/.venv/bin/python')
        launcher=private/'api.py'; launcher.write_text('''import json,os
from app.main import socket_app
from app.database import engine,init_db
from app.models.user import User
from app.models.channel import Channel,ChannelMember
from app.models.workspace import Workspace,WorkspaceMember,WorkspaceChannel
from app.services.auth import create_token
from sqlmodel import Session
import uvicorn
init_db()
with Session(engine) as session:
    session.add_all([User(id='alice',name='Alice North',email='private-alice@example.com',password_hash='unused'),User(id='bob',name='Bob North',email='private-bob@example.com',password_hash='unused')])
    session.add(Workspace(id='team',name='Private studio'));session.add(Channel(id='shared',name='Studio'));session.commit()
    session.add_all([WorkspaceMember(workspace_id='team',user_id='alice',role='owner'),WorkspaceMember(workspace_id='team',user_id='bob'),WorkspaceChannel(channel_id='shared',workspace_id='team'),ChannelMember(channel_id='shared',user_id='alice',role='admin'),ChannelMember(channel_id='shared',user_id='bob')]);session.commit()
    tokens={name:create_token(name,session) for name in ['alice','bob']}
    fd=os.open(os.environ['PROOF_CREDENTIAL_FILE'],os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(tokens,stream)
uvicorn.run(socket_app,host='127.0.0.1',port='''+str(api_port)+''',proxy_headers=False,access_log=False)
''')
        server = None
        try:
            build_log=(private/'build.log').open('w'); streams.append(build_log)
            build = subprocess.run(['node',str(ROOT/'frontend/node_modules/vite/bin/vite.js'),'build','--outDir',str(private/'web'),'--emptyOutDir'],cwd=ROOT/'frontend',env=environment,stdout=build_log,stderr=build_log)
            assert build.returncode == 0, 'Private production build failed; inspect current frontend TypeScript/module readiness'
            stream=(private/'api.log').open('w');streams.append(stream)
            process=subprocess.Popen([python,str(launcher)],cwd=ROOT/'backend',env=environment,stdout=stream,stderr=stream,start_new_session=True);processes.append(process)
            ready(api_base+'/ready',process)
            tokens=json.loads((private/'access.json').read_text())
            class Server(SimpleHTTPRequestHandler):
                def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(private/'web'),**kwargs)
                def log_message(self,*_): pass
                def proxy(self):
                    path=self.path[4:] if self.path.startswith('/api/') else self.path
                    body=self.rfile.read(int(self.headers.get('Content-Length',0))) if self.command not in ['GET','HEAD'] else None
                    request=Request(api_base+path,method=self.command,data=body,headers={key:value for key,value in self.headers.items() if key.lower() not in ['host','connection','content-length','accept-encoding']})
                    try: response=urlopen(request,timeout=30)
                    except HTTPError as error: response=error
                    with response:
                        value=response.read();self.send_response(response.status)
                        for key,value_header in response.headers.items():
                            if key.lower() not in ['content-length','connection','transfer-encoding']:self.send_header(key,value_header)
                        self.send_header('Content-Length',str(len(value)));self.end_headers()
                        if self.command!='HEAD':
                            try:self.wfile.write(value)
                            except (BrokenPipeError,ConnectionResetError):pass  # Intentional browser offline/abort.
                def do_GET(self):
                    if self.path.startswith(('/api/','/socket.io')):self.proxy()
                    else:super().do_GET()
                do_POST=do_PUT=do_PATCH=do_DELETE=do_OPTIONS=proxy
            server=ThreadingHTTPServer(('127.0.0.1',app_port),Server);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def count():
                with sqlite3.connect(private/'proof.db') as db:return db.execute('SELECT COUNT(*) FROM offlinemessagereceipt').fetchone()[0]
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
                context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce');errors=[]
                context.add_init_script('if(location.origin==='+json.dumps(app)+'&&!sessionStorage.getItem("proof-initialized")){localStorage.setItem("freecollab_token",'+json.dumps(tokens['alice'])+');localStorage.setItem("freecollab_user_id","alice");sessionStorage.setItem("proof-initialized","1")}')
                context.on('page',lambda opened:opened.on('pageerror',lambda error:errors.append(str(error)+'\n'+(error.stack or ''))))
                page=context.new_page();page.set_default_timeout(25000)
                try:
                    stage='explicit opt-in online history and file snapshot'
                    page.goto(app,wait_until='networkidle');page.wait_for_function('navigator.serviceWorker.controller!==null',timeout=30000)
                    page.get_by_role('navigation',name='Channels',exact=True).get_by_role('button',name='Studio').click()
                    composer=page.get_by_role('textbox',name='Message #Studio',exact=True)
                    composer.fill('Offline history secret A');page.get_by_role('button',name='Send message',exact=True).click()
                    expect(page.get_by_text('Offline history secret A',exact=True)).to_be_visible()
                    boundary='freecollab-offline-proof'
                    content=b'Offline attachment confidential bytes A'
                    multipart=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="private-reference.txt"\r\nContent-Type: text/plain\r\n\r\n'.encode()+content+f'\r\n--{boundary}--\r\n'.encode())
                    with urlopen(Request(api_base+'/files/channels/shared',data=multipart,headers={'Authorization':'Bearer '+tokens['alice'],'Content-Type':'multipart/form-data; boundary='+boundary}),timeout=20) as response:reference=json.load(response)
                    page.get_by_role('button',name='Mobile and offline settings',exact=True).click()
                    dialog=page.get_by_role('dialog',name='Mobile and offline',exact=True)
                    expect(dialog.get_by_text('Nothing is saved automatically',exact=False)).to_have_count(1)
                    dialog.get_by_role('button',name='Save this channel offline',exact=True).click()
                    expect(dialog.get_by_role('button',name='Refresh saved history',exact=True)).to_be_visible()
                    expect(dialog.get_by_role('region',name='Saved history for Studio',exact=True).get_by_text('Offline history secret A',exact=True)).to_be_visible()
                    dialog.get_by_role('button',name='Save offline private-reference.txt',exact=True).click()
                    expect(dialog.get_by_role('button',name='Open offline private-reference.txt',exact=True)).to_be_visible()
                    results['explicit_channel_enrollment_and_authorized_history_and_file_snapshot']=True
                    vault=page.evaluate(VAULT)
                    assert {'offline-channel','offline-file'}.issubset({row['kind'] for row in vault['rows']})
                    assert all(set(row['fields'])=={'id','owner','kind','iv','ciphertext'} for row in vault['rows'])
                    assert all(not key['extractable'] and key['algorithm']=='AES-GCM' for key in vault['keys'])
                    raw=json.dumps(vault)
                    for secret in ['Offline history secret A','private-reference.txt',content.decode(),tokens['alice']]:assert secret not in raw
                    results['history_file_metadata_and_bytes_encrypted_under_nonextractable_origin_account_key']=True
                    stage='real service worker offline reload and binary attachment read'
                    context.set_offline(True);page.reload(wait_until='domcontentloaded')
                    page.get_by_role('button',name='Mobile and offline settings',exact=True).click();dialog=page.get_by_role('dialog',name='Mobile and offline',exact=True)
                    dialog.get_by_role('button',name='Studio',exact=True).click()
                    expect(dialog.get_by_role('region',name='Saved history for Studio',exact=True).get_by_text('Offline history secret A',exact=True)).to_be_visible()
                    expect(dialog.get_by_text('Saved copy · access has not been checked while offline.',exact=True)).to_be_visible()
                    with page.expect_download() as pending:dialog.get_by_role('button',name='Open offline private-reference.txt',exact=True).click()
                    download=pending.value
                    assert Path(download.path()).read_bytes()==content
                    results['actual_offline_reload_read_history_and_download_verified_local_bytes']=True
                    stage='durable encrypted offline attachment queue'
                    dialog.locator('#offline-attachment-input').set_input_files({'name':'queued-private.txt','mimeType':'text/plain','buffer':b'Queued binary confidential A'})
                    expect(dialog.get_by_role('list',name='Queued attachments',exact=True).get_by_text('queued-private.txt',exact=True)).to_be_visible()
                    vault=page.evaluate(VAULT);assert 'attachment-outbox' in {row['kind'] for row in vault['rows']}
                    raw=json.dumps(vault);assert 'Queued binary confidential A' not in raw and 'queued-private.txt' not in raw
                    page.reload(wait_until='domcontentloaded');page.get_by_role('button',name='Mobile and offline settings',exact=True).click();dialog=page.get_by_role('dialog',name='Mobile and offline',exact=True)
                    expect(dialog.get_by_role('list',name='Queued attachments',exact=True).get_by_text('queued-private.txt',exact=True)).to_be_visible()
                    results['offline_binary_outbox_survives_actual_reload_without_plaintext_metadata_or_bytes']=True
                    page.set_viewport_size({'width':320,'height':900});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');assert dialog.evaluate('e=>e.scrollWidth<=e.clientWidth+1')
                    REPORT.parent.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(REPORT.with_name('offline-library-320.png')),full_page=True)
                    results['offline_library_and_queue_fit_320px']=True;page.set_viewport_size({'width':1440,'height':1000})
                    stage='lost upload acknowledgement retries stable receipt once'
                    def file_count():
                        with sqlite3.connect(private/'proof.db') as db:return db.execute('SELECT COUNT(*) FROM file').fetchone()[0]
                    lost={'once':False}
                    def lose_ack(route):
                        if not lost['once']:
                            lost['once']=True;route.fetch();route.abort('failed')
                        else:route.continue_()
                    context.route('**/api/files/channels/shared',lose_ack)
                    context.set_offline(False)
                    deadline=time.monotonic()+30
                    while file_count()!=2 and time.monotonic()<deadline:page.wait_for_timeout(100)
                    assert file_count()==2 and lost['once']
                    context.unroute('**/api/files/channels/shared',lose_ack)
                    dialog.get_by_role('button',name='Send eligible attachments',exact=True).click()
                    expect(dialog.get_by_role('list',name='Queued attachments',exact=True)).to_have_count(0)
                    assert file_count()==2
                    results['actual_lost_ack_attachment_replay_uses_same_atomic_receipt_without_duplicate']=True
                    stage='removed membership erases known revoked cache and blocks attachment replay'
                    context.set_offline(True)
                    dialog.get_by_role('button',name='Studio',exact=True).click()
                    dialog.locator('#offline-attachment-input').set_input_files({'name':'revoked-private.txt','mimeType':'text/plain','buffer':b'Must never leave revoked device'})
                    expect(dialog.get_by_role('list',name='Queued attachments',exact=True).get_by_text('revoked-private.txt',exact=True)).to_be_visible()
                    with sqlite3.connect(private/'proof.db') as db:db.execute("DELETE FROM channelmember WHERE channel_id='shared' AND user_id='alice'");db.commit()
                    context.set_offline(False)
                    expect(dialog.get_by_role('list',name='Saved offline channels',exact=True).get_by_role('button',name='Studio',exact=True)).to_have_count(0)
                    expect(dialog.get_by_role('list',name='Queued attachments',exact=True).get_by_text('Needs review',exact=False)).to_be_visible()
                    assert file_count()==2
                    vault=page.evaluate(VAULT);assert not any(row['kind'] in ['offline-channel','offline-file'] for row in vault['rows'])
                    results['online_revocation_purges_retained_history_and_files_and_blocks_queued_attachment']=True
                    stage='service worker public-only cache and logout erasure'
                    cache_urls=page.evaluate("""async()=>{const urls=[];for(const key of await caches.keys())for(const request of await(await caches.open(key)).keys())urls.push(request.url);return urls}""")
                    assert cache_urls and all('/api' not in url and '/socket.io' not in url and 'token' not in url for url in cache_urls)
                    results['private_library_uses_indexeddb_never_authenticated_response_cache']=True
                    page.get_by_role('button',name='Close mobile settings',exact=True).click()
                    if page.get_by_role('button',name='Sign out',exact=True).is_hidden():page.get_by_role('button',name='Toggle channels',exact=True).click()
                    page.get_by_role('button',name='Sign out',exact=True).click();page.wait_for_function('localStorage.getItem("freecollab_token")===null')
                    deadline=time.monotonic()+10
                    while time.monotonic()<deadline:
                        vault=page.evaluate(VAULT)
                        if not vault['keys'] and not vault['rows']:break
                        page.wait_for_timeout(100)
                    assert not vault['keys'] and not vault['rows'];results['logout_erases_history_files_attachment_outbox_and_nonextractable_keys']=True
                    assert not errors, 'Unexpected browser page error';results['zero_uncaught_browser_errors']=True
                finally:
                    if sys.exc_info()[0]:
                        try:
                            visible=page.locator('body').inner_text()
                            for secret in tokens.values():visible=visible.replace(secret,'[fixture credential]')
                            print('Fixture UI: '+visible[:5000],file=sys.stderr)
                        except Exception:pass
                    context.close();browser.close()
        except Exception:
            print('FAILED offline library proof stage: '+stage,file=sys.stderr)
            # Error messages omit fixture credentials, private drafts and request logs.
            raise
        finally:
            if server:server.shutdown();server.server_close()
            for process in reversed(processes):
                if process.poll() is None:process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            for stream in streams:stream.close()
    REPORT.parent.mkdir(parents=True,exist_ok=True);REPORT.write_text(json.dumps({'scope':'Complete production app, native Chromium SW/IndexedDB/CacheStorage, private API/SQLite and fixture accounts; no external or live push provider','results':results,'cleanup':'Owned temporary production assets, API/database/keys, static server and browser profiles removed'},indent=2)+'\n')
    print('Offline library browser proof passed: '+str(len(results))+' checks; native service worker and encrypted storage.')


if __name__=='__main__':main()
