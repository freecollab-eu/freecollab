#!/usr/bin/env python3
"""Private production Copilot UI, real HTTPS/SSE provider and Swedish/mobile proof.
Only disposable accounts, synthetic provider key, private SQLite and loopback TLS.
"""
import base64
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
from playwright.sync_api import expect,sync_playwright
ROOT=Path(__file__).resolve().parents[2]
PYTHON=os.getenv('BACKEND_PYTHON') or (str(ROOT/'backend/.venv/bin/python') if (ROOT/'backend/.venv/bin/python').is_file() else sys.executable)
spec=importlib.util.spec_from_file_location('copilot_proof_helpers',Path(__file__).with_name('remote-sync-browser.py'))
helpers=importlib.util.module_from_spec(spec);spec.loader.exec_module(helpers)
from tests.oidc_mock import tls_fixture
PASSWORD='Private-copilot-browser!2026'
KEY='synthetic-copilot-browser-provider-key'
REPORT=Path(os.getenv('COPILOT_PROOF_REPORT',str(ROOT.parent/'.freecollab/proof-results/copilot-browser.json')))

def main():
    stage='private startup';results={};processes=[];streams=[];server=None
    with tempfile.TemporaryDirectory(prefix='freecollab-copilot-browser-') as directory:
        private=Path(directory);requests=[]
        snapshot=private/'source';snapshot.mkdir()
        shutil.copytree(ROOT/'frontend',snapshot/'frontend',ignore=shutil.ignore_patterns('node_modules','dist','.vite','.env*'))
        (snapshot/'frontend/node_modules').symlink_to(ROOT/'frontend/node_modules',target_is_directory=True)
        helpers.ROOT=snapshot
        expect.set_options(timeout=30000)
        try:
            certificate,key=tls_fixture(private/'tls')
            class Handler(BaseHTTPRequestHandler):
                def log_message(self,*_):pass
                def do_POST(self):
                    assert self.headers['Authorization']=='Bearer '+KEY
                    requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                    delta={'content':'Reviewed selected source [S1].','tool_calls':[{'index':0,'id':'proposal','type':'function','function':{'name':'create_task','arguments':json.dumps({'project_id':'proof-project','title':'Review approved by owner','description':'A task from explicitly reviewed context'})}}]}
                    packet={'choices':[{'index':0,'delta':delta,'finish_reason':'tool_calls'}]}
                    body=('data: '+json.dumps(packet)+'\n\ndata: [DONE]\n\n').encode()
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(body)));self.end_headers()
                    for offset in range(0,len(body),11):self.wfile.write(body[offset:offset+11]);self.wfile.flush()
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(certificate,key);server.socket=context.wrap_socket(server.socket,server_side=True)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            endpoint='https://localhost:'+str(server.server_port)+'/v1/chat/completions'
            api_port,app_port=helpers.port(),helpers.port();base,app='http://127.0.0.1:'+str(api_port),'http://127.0.0.1:'+str(app_port)
            environment=os.environ.copy();environment.update(ENVIRONMENT='test',DATABASE_URL='sqlite:///'+str(private/'proof.db'),STORAGE_PATH=str(private/'files'),JWT_SECRET=secrets.token_urlsafe(48),ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',MESSAGE_ENCRYPTION_KEY_FILE=str(private/'keys.json'),ACCOUNT_SECURITY_KEY_FILE=str(private/'account.key'),MATRIX_ENABLED='false',MAIL_DELIVERY_MODE='disabled',PUBLIC_APP_URL=app,CORS_ORIGINS=json.dumps([app]),OIDC_ENABLED='false',OTRUST_ENABLED='false',WEBHOOKS_ENABLED='false',SYNC_ENABLED='false',COPILOT_ENABLED='true',VITE_API_URL='/api',PYTHONPATH=str(ROOT/'backend'),SSL_CERT_FILE=certificate,PROOF_COPILOT_ENDPOINT=endpoint)
            environment['NO_PROXY']=','.join(filter(None,[environment.get('NO_PROXY',''),'localhost','127.0.0.1','::1']))
            (private/'files').mkdir(mode=0o700)
            launcher=private/'api.py';launcher.write_text('''import os,uvicorn
from urllib.parse import urlsplit
from app.main import socket_app
from app.services import copilot
endpoint=os.environ['PROOF_COPILOT_ENDPOINT']
normal=copilot.transport.normalize_url
addresses=copilot.transport.public_addresses
proxy=copilot.transport._proxy
copilot.transport.normalize_url=lambda value,**kw:(urlsplit(value),'localhost') if value==endpoint else normal(value,**kw)
copilot.transport.public_addresses=lambda host,**kw:['127.0.0.1'] if host=='localhost' else addresses(host,**kw)
copilot.transport._proxy=lambda host,port:None if host=='localhost' else proxy(host,port)
uvicorn.run(socket_app,host='127.0.0.1',port='''+str(api_port)+''',proxy_headers=False,access_log=False)
''')
            for command,filename,cwd in [([PYTHON,str(launcher)],'api.log',ROOT/'backend'),(['node',str(helpers.vite_launcher(private,base,app_port))],'vite.log',ROOT/'frontend')]:
                stream=(private/filename).open('w');streams.append(stream);processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            helpers.ready(base+'/ready',processes);helpers.ready(app+'/',processes)
            email='copilot-'+secrets.token_hex(5)+'@example.com'
            owner=helpers.api(base,'POST','/auth/register',data={'name':'Private copilot owner','email':email,'password':PASSWORD})
            workspace=helpers.api(base,'POST','/workspaces',owner,{'name':'Private AI studio'})
            channel=helpers.api(base,'POST','/channels',owner,{'name':'Planning room','workspace_id':workspace['id']})
            selected='EXPLICIT CHOSEN SOURCE FOR COPILOT';omitted='UNSELECTED CONTENT MUST STAY OUTSIDE AI'
            helpers.api(base,'POST','/messages/channels/'+channel['id'],owner,{'content':selected});helpers.api(base,'POST','/messages/channels/'+channel['id'],owner,{'content':omitted})
            helpers.api(base,'POST','/channels/'+channel['id']+'/projects',owner,{'id':'proof-project','fields':{'title':'Review project','description':'Private fixture','color':'sage'}})
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),args=['--no-sandbox'])
                context=browser.new_context(viewport={'width':1440,'height':1000},locale='en-US',reduced_motion='reduce');page=context.new_page();page.set_default_timeout(45000);errors=[];urls=[]
                page.on('pageerror',lambda failure:errors.append(type(failure).__name__));page.on('request',lambda request:urls.append(request.url))
                stage='honest no-key production panel'
                page.goto(app,wait_until='domcontentloaded',timeout=90000);page.evaluate('([k,v])=>localStorage.setItem(k,v)',['freecollab.workspace:'+owner['user_id'],workspace['id']])
                page.get_by_role('navigation').get_by_role('button',name='Open App',exact=True).click();page.get_by_label('Email',exact=True).fill(email);page.get_by_label('Password',exact=True).fill(PASSWORD);page.get_by_role('button',name='Sign In',exact=True).click()
                expect(page.locator('.fc-chat')).to_be_visible(timeout=90000);page.get_by_role('button',name='Open workspace tools',exact=True).click();page.locator('#work-tab-copilot').click()
                panel=page.get_by_role('region',name='AI Copilot',exact=True)
                expect(panel.get_by_text('AI configuration required',exact=True)).to_be_visible();assert not requests
                results['honest_configuration_required_no_provider_calls_without_key']=True
                stage='native owner config encrypted key named agent and explicit opt-in'
                panel.get_by_text('Provider and agent settings',exact=True).click()
                panel.get_by_label('Chat completions endpoint',exact=True).fill(endpoint);panel.get_by_label('Model',exact=True).fill('fixture-chat');panel.get_by_label('Provider API key',exact=True).fill(KEY);panel.get_by_label('Enable this workspace provider').check()
                panel.get_by_label('Agent name',exact=True).fill('planner');panel.get_by_label('Agent instructions',exact=True).fill('Summarize the selected source and propose one task.')
                panel.get_by_label('Allow approved native task creation').check();panel.get_by_label('Current password',exact=True).fill(PASSWORD);panel.get_by_role('button',name='Save provider and agents',exact=True).click()
                expect(panel.get_by_label('I opt in to this provider configuration for my account.')).to_be_visible();expect(panel.get_by_label('Provider API key',exact=True)).to_have_value('')
                panel.get_by_label('I opt in to this provider configuration for my account.').check();expect(panel.get_by_role('heading',name='Private conversation',exact=True)).to_be_visible()
                assert not requests;results['fresh_auth_configured_named_agent_key_cleared_and_per_user_opt_in']=True
                stage='selected context real HTTPS streamed answer citation and approval'
                panel.get_by_role('button',name='Choose messages, documents, files and clip transcripts',exact=True).click()
                selected_row=panel.locator('.fc-copilot-context label').filter(has_text=selected);expect(selected_row).to_be_visible();selected_row.get_by_role('checkbox').check()
                panel.get_by_label('Task destination',exact=True).select_option('proof-project');panel.get_by_label('Your question',exact=True).fill('Summarize the selected source and propose a task.')
                panel.get_by_label('Send this prompt, visible native AI history and checked sources to the configured provider.').check();panel.get_by_role('button',name='Send to provider',exact=True).click()
                expect(panel.get_by_text('Reviewed selected source [S1].',exact=True)).to_be_visible(timeout=60000)
                assert len(requests)==1 and selected in json.dumps(requests) and omitted not in json.dumps(requests)
                assert KEY not in json.dumps(requests)
                with sqlite3.connect(private/'proof.db') as connection:assert connection.execute('SELECT count(*) FROM taskcard').fetchone()[0]==0
                panel.get_by_role('button',name='[S1] Message',exact=True).click();expect(panel.locator('.fc-copilot-source-review')).to_contain_text(selected)
                panel.get_by_role('button',name='Approve and create task',exact=True).click();expect(panel.get_by_text('Approved task created. Repeated approval keeps the same task.',exact=True)).to_be_visible()
                panel.get_by_role('button',name='Approve and create task',exact=True).click()
                with sqlite3.connect(private/'proof.db') as connection:
                    assert connection.execute('SELECT count(*) FROM taskcard').fetchone()[0]==1
                    config=connection.execute('SELECT encrypted_config FROM copilotconfig').fetchone()[0];assert KEY not in config
                results['real_https_sse_selected_only_context_citation_and_approved_idempotent_task']=True
                stage='native private history reset'
                panel.get_by_role('button',name='Reset history',exact=True).click()
                expect(panel.get_by_text('Private AI history reset.',exact=True)).to_be_visible()
                with sqlite3.connect(private/'proof.db') as connection:assert connection.execute('SELECT count(*) FROM copilotconversation').fetchone()[0]==0
                results['private_history_reset_retains_approved_shared_task']=True
                stage='saved Swedish language and responsive panel'
                helpers.api(base,'PUT','/preferences/language',owner,{'locale':'sv'});page.reload(wait_until='domcontentloaded')
                expect(page.locator('.fc-chat')).to_be_visible(timeout=60000)
                page.locator('button.fc-chat-topbar-action:has(svg.lucide-layers)').click();page.locator('#work-tab-copilot').click()
                panel=page.get_by_role('region',name='AI-assistent',exact=True);expect(panel.get_by_role('heading',name='Ett genomtänkt extra par ögon.',exact=True)).to_be_visible()
                REPORT.parent.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(REPORT.parent/'copilot-desktop-sv.png'))
                page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth');page.screenshot(path=str(REPORT.parent/'copilot-mobile-sv.png'))
                results['saved_swedish_account_preference_and_mobile_without_overflow']=True
                assert not errors and not any(KEY in url or PASSWORD in url for url in urls);results['no_page_errors_or_auth_secrets_in_urls']=True
                context.close();browser.close()
            REPORT.write_text(json.dumps({'ok':True,'results':results},indent=2)+'\n');print(json.dumps({'ok':True,'checks':list(results),'report':str(REPORT)}))
        except Exception as failure:
            print(json.dumps({'failure':str(failure).replace(KEY,'[redacted]').replace(PASSWORD,'[redacted]')[:5000]}))
            if 'page' in locals():
                try:
                    page.screenshot(path='/tmp/freecollab-copilot-failure.png')
                    print(json.dumps({'alerts':page.get_by_role('alert').all_text_contents(),'failure':str(failure).replace(KEY,'[redacted]').replace(PASSWORD,'[redacted]')[:5000]}))
                except Exception:pass
            print(json.dumps({'ok':False,'stage':stage,'error_type':type(failure).__name__}));raise AssertionError('Private copilot proof failed at '+stage) from None
        finally:
            helpers.stop(processes)
            for stream in streams:stream.close()
            if server:server.shutdown();server.server_close()
if __name__=='__main__':main()
