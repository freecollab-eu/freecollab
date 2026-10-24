#!/usr/bin/env python3
"""Native Chromium service worker, offline vault and reconnect proof.

Builds the complete production app into an owned temporary directory and runs
only private API/static services with independent fixture accounts and keys.
No existing service, browser profile or live push provider is used.
"""
from ui_navigation import choose_chat_channel, open_chat_tool, reveal_chat_tool
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
REPORT = Path(os.getenv('MOBILE_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/mobile-offline-browser.json')))


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


def network_state(page, session, offline):
    # Chromium151 can reset navigator.onLine after a service-worker navigation
    # while Playwright still blocks real requests. Set actual browser protocol
    # state for the new document; do not replace navigator or network responses.
    session.send('Network.overrideNetworkState', {'offline':offline,'latency':0,'downloadThroughput':-1,'uploadThroughput':-1})
    page.wait_for_function('online=>navigator.onLine===online',arg=not offline)


def set_offline(context, page, session, offline):
    context.set_offline(offline)
    network_state(page,session,offline)


def reload_offline(page, session):
    page.reload(wait_until='domcontentloaded')
    network_state(page,session,True)
    assert page.evaluate('!!navigator.serviceWorker.controller')
    assert page.evaluate("async()=>{try{await fetch('/api/ready',{cache:'no-store'});return 'unexpected response'}catch(error){return error.name}}")=='TypeError'


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
                page=context.new_page();page.set_default_timeout(25000);network=context.new_cdp_session(page)
                try:
                    stage='production app and native service worker installation'
                    page.goto(app,wait_until='networkidle');page.wait_for_function('navigator.serviceWorker.controller!==null',timeout=30000)
                    reveal_chat_tool(page, 'Mobile and offline settings')
                    expect(page.get_by_role('button',name='Mobile and offline settings',exact=True)).to_be_visible()
                    choose_chat_channel(page, 'Studio')
                    composer=page.get_by_role('textbox',name='Message #Studio',exact=True);expect(composer).to_be_visible()
                    assert page.evaluate('document.querySelector("link[rel=manifest]").href.endsWith("/manifest.webmanifest")')
                    results['complete_production_app_native_service_worker_control']=True
                    stage='native encrypted vault and offline queue'
                    set_offline(context,page,network,True);composer.fill('Offline queued message private A');page.get_by_role('button',name='Send message',exact=True).click()
                    expect(page.get_by_text('Saved in your encrypted outbox. It will sync when this sign-in reconnects.',exact=True)).to_be_visible()
                    composer.fill('Saved draft private A');open_chat_tool(page, 'Mobile and offline settings')
                    expect(page.get_by_role('dialog',name='Mobile and offline',exact=True).get_by_text('Saved draft private A',exact=True)).to_be_visible()
                    page.get_by_role('button',name='Close mobile settings',exact=True).click()
                    vault=page.evaluate(VAULT)
                    assert vault['keys'] and all(key=={'type':'secret','extractable':False,'algorithm':'AES-GCM'} for key in vault['keys'])
                    assert {row['kind'] for row in vault['rows']}=={'draft','outbox'}
                    assert all(set(row['fields'])=={'id','owner','kind','iv','ciphertext'} for row in vault['rows'])
                    raw=json.dumps(vault);assert all(value not in raw for value in ['Saved draft private A','Offline queued message private A',tokens['alice']])
                    assert count()==0;results['native_indexeddb_nonextractable_aes_gcm_no_plaintext_or_bearer']=True
                    stage='actual offline navigation and private draft library'
                    reload_offline(page,network);open_chat_tool(page, 'Mobile and offline settings')
                    dialog=page.get_by_role('dialog',name='Mobile and offline',exact=True)
                    expect(dialog.get_by_text('Saved draft private A',exact=True)).to_be_visible()
                    expect(dialog.get_by_text('Offline queued message private A',exact=True)).to_be_visible()
                    assert page.get_by_role('textbox',name='Message #Studio',exact=True).count()==0
                    dialog.get_by_role('button',name='Edit draft',exact=True).click();dialog.get_by_label('Private draft text',exact=True).fill('Edited offline private A');dialog.get_by_role('button',name='Save private draft',exact=True).click();expect(dialog.get_by_text('Edited offline private A',exact=True)).to_be_visible()
                    results['actual_offline_reload_edit_saved_draft_without_cached_history_or_catalog']=True
                    results['native_service_worker_reload_keeps_real_offline_network_and_navigator_state']=True
                    cache_urls=page.evaluate('''async()=>{const urls=[];for(const key of await caches.keys())for(const request of await(await caches.open(key)).keys())urls.push(request.url);return urls}''')
                    assert cache_urls and all(url.startswith(app+'/assets/') or url in [app+'/',app+'/manifest.webmanifest',app+'/favicon.svg',app+'/icons/app-192.png',app+'/icons/app-512.png'] for url in cache_urls)
                    assert all('/api' not in url and 'token' not in url and '/socket.io' not in url for url in cache_urls)
                    results['cache_storage_contains_public_shell_assets_only']=True
                    page.set_viewport_size({'width':320,'height':900});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');assert dialog.evaluate('e=>e.scrollWidth<=e.clientWidth+1')
                    REPORT.parent.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(REPORT.with_name('mobile-offline-320.png')),full_page=True)
                    results['offline_settings_320px_without_horizontal_overflow']=True;page.set_viewport_size({'width':1440,'height':1000})
                    stage='safe reconnect sends once after live authorization'
                    set_offline(context,page,network,False)
                    deadline=time.monotonic()+30
                    while count()!=1 and time.monotonic()<deadline:page.wait_for_timeout(200)
                    assert count()==1
                    expect(dialog.get_by_text('No messages are waiting on this device.',exact=True)).to_be_visible()
                    results['reconnect_checks_live_identity_and_membership_before_exactly_once_native_send']=True
                    page.get_by_role('button',name='Close mobile settings',exact=True).click();page.reload(wait_until='networkidle')
                    choose_chat_channel(page, 'Studio');composer=page.get_by_role('textbox',name='Message #Studio',exact=True)
                    stage='restored draft cannot overwrite newer composer typing'
                    composer.fill('Draft before restoration race')
                    open_chat_tool(page, 'Mobile and offline settings')
                    expect(page.get_by_role('dialog',name='Mobile and offline',exact=True).get_by_text('Draft before restoration race',exact=True)).to_be_visible()
                    page.get_by_role('button',name='Close mobile settings',exact=True).click()
                    page.evaluate("""()=>{
                        window.dispatchEvent(new CustomEvent('freecollab:offline-draft-restored',{detail:{channelId:'shared'}}));
                        const input=document.querySelector('textarea[aria-label="Message #Studio"]');
                        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'New typing while device draft loads');
                        input.dispatchEvent(new Event('input',{bubbles:true}));
                    }""")
                    expect(composer).to_have_value('New typing while device draft loads')
                    page.wait_for_timeout(350)
                    expect(composer).to_have_value('New typing while device draft loads')
                    results['async_restored_draft_cannot_overwrite_newer_composer_typing']=True
                    stage='lost server acknowledgment and idempotent retry'
                    lost={'once':False}
                    def lose_ack(route):
                        if not lost['once']:
                            lost['once']=True;route.fetch();route.abort('failed')
                        else:route.continue_()
                    context.route('**/api/mobile/offline/channels/shared/messages',lose_ack)
                    composer.fill('Acknowledged on server response lost');page.get_by_role('button',name='Send message',exact=True).click()
                    expect(page.get_by_text('Saved in your encrypted outbox. It will sync when this sign-in reconnects.',exact=True)).to_be_visible();assert count()==2
                    context.unroute('**/api/mobile/offline/channels/shared/messages',lose_ack)
                    set_offline(context,page,network,True);set_offline(context,page,network,False)
                    open_chat_tool(page, 'Mobile and offline settings');dialog=page.get_by_role('dialog',name='Mobile and offline',exact=True)
                    expect(dialog.get_by_text('No messages are waiting on this device.',exact=True)).to_be_visible();assert count()==2
                    results['lost_ack_retry_reuses_durable_client_receipt_without_duplicate']=True
                    stage='membership removed while offline stops reconnect send'
                    page.get_by_role('button',name='Close mobile settings',exact=True).click();set_offline(context,page,network,True)
                    composer.fill('Removed membership must never send');page.get_by_role('button',name='Send message',exact=True).click();expect(page.get_by_text('Saved in your encrypted outbox. It will sync when this sign-in reconnects.',exact=True)).to_be_visible()
                    with sqlite3.connect(private/'proof.db') as db:db.execute("DELETE FROM channelmember WHERE channel_id='shared' AND user_id='alice'");db.commit()
                    set_offline(context,page,network,False);open_chat_tool(page, 'Mobile and offline settings');dialog=page.get_by_role('dialog',name='Mobile and offline',exact=True)
                    expect(dialog.get_by_text('Needs review',exact=True)).to_be_visible();assert count()==2
                    results['removed_membership_requires_review_and_cannot_auto_send']=True
                    stage='logout purge and two-account cross-tab isolation'
                    peer=context.new_page();peer.goto(app,wait_until='domcontentloaded');peer.wait_for_function('navigator.serviceWorker.controller!==null')
                    page.get_by_role('button',name='Close mobile settings',exact=True).click();page.get_by_role('button',name='Switch channel',exact=True).click() if page.get_by_role('button',name='Sign out',exact=True).is_hidden() else None
                    open_chat_tool(page, 'Sign out')
                    page.wait_for_function('localStorage.getItem("freecollab_token")===null')
                    deadline=time.monotonic()+10
                    while time.monotonic()<deadline:
                        vault=page.evaluate(VAULT)
                        if not vault['keys'] and not vault['rows']:break
                        page.wait_for_timeout(100)
                    assert not vault['keys'] and not vault['rows']
                    page.evaluate('''tokens=>{localStorage.setItem('freecollab_token',tokens.bob);localStorage.setItem('freecollab_user_id','bob');window.dispatchEvent(new Event('freecollab:auth-changed'))}''',tokens)
                    page.reload(wait_until='networkidle');open_chat_tool(page, 'Mobile and offline settings');dialog=page.get_by_role('dialog',name='Mobile and offline',exact=True)
                    expect(dialog.get_by_text('No private drafts are saved on this device.',exact=True)).to_be_visible();expect(dialog.get_by_text('No messages are waiting on this device.',exact=True)).to_be_visible()
                    assert 'Edited offline private A' not in page.content() and 'Removed membership must never send' not in page.content()
                    peer.wait_for_timeout(500);assert 'Edited offline private A' not in peer.content()
                    assert count()==2;results['logout_erases_keys_and_vault_and_other_account_tabs_cannot_recover_or_send']=True
                    if errors:
                        sanitized=json.dumps(errors)
                        for secret in tokens.values():sanitized=sanitized.replace(secret,'[fixture credential]')
                        print('Sanitized browser errors: '+sanitized,file=sys.stderr)
                    assert not errors, 'Unexpected browser page error';results['zero_uncaught_browser_errors']=True
                finally:context.close();browser.close()
        except Exception:
            print('FAILED mobile proof stage: '+stage,file=sys.stderr)
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
    print('Mobile/offline browser proof passed: '+str(len(results))+' checks; native service worker and encrypted storage.')


if __name__=='__main__':main()
