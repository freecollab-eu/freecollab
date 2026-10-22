#!/usr/bin/env python3
"""Real native-client approval/exchange UI with an isolated product API.

Browser sign-in is prepared with a fixture-issued ordinary session; this test
does not claim a live customer IdP, OS app store, or Matrix encryption proof.
"""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from threading import Event, Thread
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('native_browser_fixture', Path(__file__).with_name('customer-sso-browser.py'))
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
PYTHON = str(ROOT/'backend/.venv/bin/python') if (ROOT/'backend/.venv/bin/python').is_file() else sys.executable
REPORT = ROOT.parent/'.freecollab/proof-results/native-client-browser-011.json'

def main():
    processes, streams, checks, source_hashes = [], [], [], {}
    with tempfile.TemporaryDirectory(prefix='freecollab-native-ui-') as directory:
        private = Path(directory)
        received = []
        callback_received = Event()
        class Callback(BaseHTTPRequestHandler):
            def do_GET(self):
                target = urlsplit(self.path)
                if target.path != '/freecollab-native/callback':
                    self.send_response(404); self.send_header('Cache-Control','no-store'); self.end_headers()
                    return
                received.append(parse_qs(target.query))
                callback_received.set()
                self.send_response(200); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(b'Return to the app.')
            def log_message(self,*_): pass
        callback = ThreadingHTTPServer(('127.0.0.1',0),Callback)
        Thread(target=callback.serve_forever,daemon=True).start()
        redirect = 'http://127.0.0.1:'+str(callback.server_port)+'/freecollab-native/callback'
        try:
            api_port, app_port = p.port(), p.port()
            api_base, app_base = f'http://127.0.0.1:{api_port}',f'http://127.0.0.1:{app_port}'
            database = private/'native.db'
            environment = dict(os.environ,ENVIRONMENT='test',DATABASE_URL='sqlite:///'+str(database),
                STORAGE_PATH=str(private/'files'),JWT_SECRET=secrets.token_urlsafe(48),
                ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),ACCOUNT_SECURITY_KEY_FILE=str(private/'account.key'),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'native-proof':base64.b64encode(secrets.token_bytes(32)).decode()}),
                MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='native-proof',MESSAGE_ENCRYPTION_KEY_FILE=str(private/'message-keys.json'),
                PUBLIC_APP_URL=app_base,CORS_ORIGINS=json.dumps([app_base]),MATRIX_ENABLED='false',OIDC_ENABLED='false',
                MAIL_DELIVERY_MODE='disabled',OTRUST_ENABLED='false',WEBHOOKS_ENABLED='false',SYNC_ENABLED='false',
                CLOUD_FILES_ENABLED='false',EXTERNAL_MEETINGS_ENABLED='false',COPILOT_ENABLED='false',PYTHONPATH=str(ROOT/'backend'))
            launcher=private/'api.py'
            launcher.write_text("from app.main import socket_app\nimport uvicorn\nuvicorn.run(socket_app,host='127.0.0.1',port="+str(api_port)+",proxy_headers=False,access_log=False)\n")
            vite = p.create_vite(private,api_base,app_port)
            for command,filename,cwd in [([PYTHON,str(launcher)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',ROOT/'frontend')]:
                stream=(private/filename).open('w');streams.append(stream)
                processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            p.ready(api_base+'/ready',processes);p.ready(app_base+'/',processes)
            owner=p.native_user(api_base,database,'Native Approval Owner')
            verifier=secrets.token_urlsafe(32);state=secrets.token_urlsafe(32)
            challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            import time
            request={'protocol':'freecollab-native-v1','clientKind':'desktop','redirectUri':redirect,
                'state':state,'codeChallenge':challenge,'expiresAt':int(time.time()*1000)+600000}
            encoded=base64.urlsafe_b64encode(json.dumps(request).encode()).rstrip(b'=').decode()
            for relative in ['frontend/src/components/NativeClientSignIn.tsx','frontend/src/lib/native-client-context.ts','frontend/src/main.tsx',
                             'backend/app/services/native_clients.py','backend/app/services/auth.py','backend/app/services/request_guard.py']:
                source_hashes[relative]=hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or '/usr/bin/chromium',headless=True,args=['--no-sandbox'])
                errors,urls=[],[]
                context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                page=context.new_page();page.set_default_timeout(30000)
                page.on('pageerror',lambda error:errors.append(type(error).__name__))
                page.on('request',lambda record:urls.append(record.url))
                context.add_init_script('if(location.origin==='+json.dumps(app_base)+'){localStorage.setItem("freecollab_token",'+json.dumps(owner['access_token'])+');localStorage.setItem("freecollab_user_id",'+json.dumps(owner['user_id'])+');}')
                page.goto(app_base+'/#native-login='+encoded,wait_until='domcontentloaded')
                dialog=page.get_by_role('dialog',name='Connect your app',exact=True)
                expect(dialog).to_be_visible(timeout=90000)
                assert '#native-login=' not in page.url
                public=page.evaluate('sessionStorage.getItem("freecollab.native-client.context.v1")')
                assert public and all(value not in public for value in [owner['access_token'],verifier])
                checks.append('real source approval UI captures only public PKCE context and strips incoming fragment')
                assert not received
                checks.append('existing browser session never approves a native app automatically')
                page.set_viewport_size({'width':320,'height':900})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert dialog.evaluate('(node)=>node.scrollWidth<=node.clientWidth')
                checks.append('real approval dialog fits 320px')
                dialog.get_by_role('button',name='Connect this app',exact=True).click()
                page.wait_for_url('http://127.0.0.1:'+str(callback.server_port)+'/**')
                assert callback_received.wait(30), 'Timed out waiting for native approval callback'
                assert len(received)==1, 'Expected one native approval callback'
                assert received[0]['state']==[state]
                code=received[0]['code'][0]
                assert len(code)==43 and owner['access_token'] not in page.url and verifier not in page.url
                checks.append('explicit approval delivers only a one-use code and exact state to loopback')

                native_context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                native_page=native_context.new_page();native_page.set_default_timeout(30000)
                native_page.on('pageerror',lambda error:errors.append(type(error).__name__))
                native_page.on('request',lambda record:urls.append(record.url))
                completion={'code':code,'code_verifier':verifier,'redirect_uri':redirect}
                native_context.add_init_script('if(location.origin==='+json.dumps(app_base)+'){window.freecollabNative={kind:"desktop",signIn:async()=>('+json.dumps(completion)+'),cancelSignIn:async()=>{}};}')
                native_page.goto(app_base,wait_until='domcontentloaded')
                native_page.get_by_role('button',name='Sign in with browser',exact=True).click()
                expect(native_page.locator('.fc-chat')).to_be_visible(timeout=90000)
                child=native_page.evaluate('localStorage.getItem("freecollab_token")')
                assert child and child!=owner['access_token']
                assert p.api(api_base,'GET','/auth/me',{'access_token':child})['id']==owner['user_id']
                checks.append('native bridge completion uses actual exchange API and opens product with independent child session')
                replay=browser.new_context();replay_page=replay.new_page()
                replay.add_init_script('if(location.origin==='+json.dumps(app_base)+'){window.freecollabNative={kind:"desktop",signIn:async()=>('+json.dumps(completion)+'),cancelSignIn:async()=>{}};}')
                replay_page.goto(app_base);replay_page.get_by_role('button',name='Sign in with browser',exact=True).click()
                expect(replay_page.get_by_role('alert')).to_contain_text('The connection could not be completed.')
                assert replay_page.evaluate('localStorage.getItem("freecollab_token")') is None
                checks.append('replayed connection code leaves native client signed out')
                replay.close()

                p.api(api_base,'PUT','/preferences/language',owner,{'locale':'sv'})
                swedish=browser.new_context(viewport={'width':320,'height':900},reduced_motion='reduce')
                swedish.add_init_script('if(location.origin==='+json.dumps(app_base)+'){localStorage.setItem("freecollab_token",'+json.dumps(owner['access_token'])+');localStorage.setItem("freecollab_user_id",'+json.dumps(owner['user_id'])+');localStorage.setItem('+json.dumps('freecollab.language:'+owner['user_id'])+',"sv");}')
                sv=swedish.new_page();sv.goto(app_base+'/#native-login='+encoded)
                approval=sv.get_by_role('dialog',name='Anslut din app',exact=True);expect(approval).to_be_visible(timeout=90000)
                expect(approval.get_by_role('button',name='Anslut den här appen',exact=True)).to_be_visible()
                approval.get_by_role('button',name='Avbryt',exact=True).click()
                assert sv.evaluate('sessionStorage.getItem("freecollab.native-client.context.v1")') is None
                checks.append('Swedish approval and cancellation clear public connection context at 320px')
                swedish.close()

                p.api(api_base,'POST','/auth/logout',owner)
                p.rejects(api_base,'GET','/auth/me',{'access_token':child},statuses=(401,))
                checks.append('browser source logout invalidates the actual native UI session')
                assert not errors, 'Unexpected browser errors'
                assert all(owner['access_token'] not in url and child not in url and verifier not in url for url in urls)
                checks.append('zero page errors and no bearer or verifier in HTTP request URLs')
                native_context.close();context.close();browser.close()
        except Exception:
            for stream in streams:stream.flush()
            raise
        finally:
            p.stop(processes)
            for stream in streams:stream.close()
            callback.shutdown();callback.server_close()
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps({'passed':len(checks),'checks':checks,'source_sha256':source_hashes,
        'scope':'Actual copied React product and isolated API; native bridge fixture only, not live OS/IdP/Matrix.'},indent=2)+'\n')
    print('Native client browser proof passed: '+str(len(checks))+' checks.')
if __name__=='__main__':main()

