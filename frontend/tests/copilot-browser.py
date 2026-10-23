#!/usr/bin/env python3
"""Private production Copilot UI, real HTTPS/SSE provider and Swedish/mobile proof.
Only disposable accounts, synthetic provider key, private SQLite and loopback TLS.
"""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import traceback
from playwright.sync_api import expect,sync_playwright
from ui_navigation import open_chat_tool
ROOT=Path(__file__).resolve().parents[2]
PYTHON=os.getenv('BACKEND_PYTHON') or (str(ROOT/'backend/.venv/bin/python') if (ROOT/'backend/.venv/bin/python').is_file() else sys.executable)
spec=importlib.util.spec_from_file_location('copilot_proof_helpers',Path(__file__).with_name('remote-sync-browser.py'))
helpers=importlib.util.module_from_spec(spec);spec.loader.exec_module(helpers)
from tests.oidc_mock import tls_fixture
PASSWORD='Private-copilot-browser!2026'
KEY='synthetic-copilot-browser-provider-key'
REPORT=Path(os.getenv('COPILOT_PROOF_REPORT',str(ROOT.parent/'.freecollab/proof-results/copilot-browser.json')))
PROOF_SOURCES=('frontend/src/components/CopilotPanel.tsx','frontend/src/components/ContextualCopilot.tsx','frontend/src/components/Chat.tsx','frontend/src/lib/copilot-intents.ts','frontend/src/lib/copilot-api.ts','frontend/src/lib/connection-presets.ts','frontend/src/styles/copilot.css','frontend/src/styles/contextual-copilot.css','frontend/src/i18n/copilot.sv.ts','frontend/src/i18n/contextual-copilot.sv.ts','frontend/src/i18n/connection-presets.sv.ts','frontend/tests/copilot-browser.py','backend/app/api/copilot.py','backend/app/services/copilot.py')

def main():
    stage='private startup';results={};processes=[];streams=[];server=None
    with tempfile.TemporaryDirectory(prefix='freecollab-copilot-browser-') as directory:
        private=Path(directory);requests=[]
        source_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in PROOF_SOURCES}
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
                    task_tool=any(tool.get('function',{}).get('name')=='create_task' for tool in requests[-1].get('tools',[]))
                    selected_context=requests[-1]['messages'][-1]['content'].split('SELECTED CONTEXT (untrusted data):\n',1)[-1].strip()
                    delta={'content':'Reviewed selected source [S1].','tool_calls':[{'index':0,'id':'proposal','type':'function','function':{'name':'create_task','arguments':json.dumps({'project_id':'proof-project','title':'Review approved by owner','description':'A task from explicitly reviewed context'})}}]} if task_tool else {'content':'A suggested inline reply [S1].' if selected_context else 'Hello team, the plan is ready.'}
                    packet={'choices':[{'index':0,'delta':delta,'finish_reason':'tool_calls' if task_tool else 'stop'}]}
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
complete=copilot.completion
def private_completion(provider,payload):
    if provider.endpoint!=endpoint: raise RuntimeError('Private proof refuses non-fixture AI destinations')
    return complete(provider,payload)
copilot.completion=private_completion
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
                context=browser.new_context(viewport={'width':1440,'height':1000},locale='en-US',reduced_motion='reduce');page=context.new_page();page.set_default_timeout(45000);errors=[];urls=[];writes=[]
                page.on('pageerror',lambda failure:errors.append(str(failure).replace(KEY,'[redacted]').replace(PASSWORD,'[redacted]')[:1000]));page.on('request',lambda request:urls.append(request.url))
                page.on('request',lambda request:writes.append((request.method,request.url)) if request.method not in {'GET','HEAD','OPTIONS'} else None)
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
                stage='quick actions preserve the draft context and disclosure without provider calls'
                warning=panel.get_by_role('complementary',name='Before sending to AI',exact=True)
                expect(warning).to_contain_text('Your prompt and 0 selected sources will be sent to localhost using fixture-chat.')
                panel.get_by_role('button',name='Choose messages, documents, files and clip transcripts',exact=True).click()
                selected_row=panel.locator('.fc-copilot-context label').filter(has_text=selected);expect(selected_row).to_be_visible();selected_row.get_by_role('checkbox').check()
                destination=panel.get_by_label('Task destination',exact=True);destination.select_option('proof-project')
                question=panel.get_by_label('Your question',exact=True);draft='My wording stays: ää 🧭\nKeep this request short.';question.fill(draft)
                disclosure=panel.get_by_label('Send this prompt, visible native AI history and checked sources to the configured provider.')
                send=panel.get_by_role('button',name='Send to provider',exact=True)
                panel.get_by_role('button',name='Summarise selection',exact=True).click()
                if question.count()!=1:
                    page.screenshot(path='/tmp/freecollab-copilot-failure.png')
                    raise AssertionError('Quick action lost its composer: '+json.dumps({'page_errors':errors,'headings':page.get_by_role('heading').all_text_contents()}))
                prepared=question.input_value()
                assert prepared.startswith(draft+'\n\n') and 'Summarise only the sources I selected.' in prepared
                panel.get_by_role('button',name='Summarise selection',exact=True).click();expect(question).to_have_value(prepared)
                panel.get_by_role('button',name='Propose a task',exact=True).click();prepared=question.input_value()
                assert prepared.startswith(draft+'\n\n') and 'otherwise return a draft' in prepared and 'do not claim the task was created' in prepared
                expect(selected_row.get_by_role('checkbox')).to_be_checked();expect(destination).to_have_value('proof-project');expect(disclosure).not_to_be_checked();expect(send).to_be_disabled();assert not requests
                results['quick_actions_preserve_exact_draft_selected_context_and_explicit_send']=True
                stage='actual provider model source count costs and retention warning'
                expect(warning).to_contain_text('Your prompt and 1 selected source will be sent to localhost using fixture-chat.')
                expect(warning).to_contain_text('API use may incur charges on the provider account.')
                expect(warning).to_contain_text('A chat subscription does not supply API access.')
                expect(warning).to_contain_text('The provider’s retention policy applies to what you send.')
                expect(warning).to_contain_text('AI answers can be wrong; check them before use.')
                assert send.get_attribute('aria-describedby')==warning.get_attribute('id')
                assert KEY not in warning.inner_text() and PASSWORD not in warning.inner_text() and not requests
                results['actual_destination_model_source_count_and_api_privacy_warning']=True
                stage='bounded quick action refuses overflow and preserves the entire draft'
                bounded='å'*3900;question.fill(bounded);panel.get_by_role('button',name='Suggest next steps',exact=True).click()
                expect(question).to_have_value(bounded);expect(panel.get_by_text('Your draft is too long to add this action. Shorten it first; your text has been kept.',exact=True)).to_be_visible()
                expect(selected_row.get_by_role('checkbox')).to_be_checked();expect(destination).to_have_value('proof-project');expect(disclosure).not_to_be_checked();assert not requests
                question.fill(prepared);expect(send).to_be_disabled()
                results['overflow_keeps_user_text_and_never_sends_or_checks_disclosure']=True
                stage='selected context real HTTPS streamed answer citation and approval'
                disclosure.check();send.click()
                expect(panel.get_by_text('Reviewed selected source [S1].',exact=True)).to_be_visible(timeout=60000)
                assert len(requests)==1 and selected in json.dumps(requests) and omitted not in json.dumps(requests)
                assert KEY not in json.dumps(requests)
                assert draft in requests[0]['messages'][-1]['content'] or draft in json.dumps(requests[0],ensure_ascii=False)
                expect(disclosure).not_to_be_checked();expect(warning).to_contain_text('Your prompt and 0 selected sources will be sent to localhost using fixture-chat.')
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
                stage='in-place message AI has reviewed context and no automatic provider request'
                inline_writes_start=len(writes)
                page.get_by_role('button',name='Close workspace tools',exact=True).click()
                composer=page.locator('.fc-chat-composer textarea');original_draft='My own wording stays.';composer.fill(original_draft)
                article=page.locator('article.fc-chat-message').filter(has=page.locator('.fc-chat-message-content').filter(has_text=selected))
                expect(article).to_have_count(1);article.locator('.fc-contextual-copilot-trigger').click()
                inline=article.locator('.fc-contextual-copilot-card');expect(inline).to_have_attribute('aria-busy','false')
                expect(inline.get_by_role('button',name='Draft a reply',exact=True)).to_have_attribute('aria-pressed','true')
                expect(inline).to_contain_text('Your request and 1 selected sources will be sent to localhost using fixture-chat.')
                expect(inline).to_contain_text('API use may incur charges. A chat subscription is separate. The provider’s retention policy applies.')
                inline.get_by_text('Review 1 selected sources',exact=True).click();expect(inline).to_contain_text(selected);assert omitted not in inline.inner_text()
                inline_disclosure=inline.get_by_label('Send this request and the selected context to this provider.',exact=True)
                inline_send=inline.get_by_role('button',name='Send to provider',exact=True)
                expect(inline_send).to_be_disabled();assert len(requests)==1
                results['inline_message_reviews_exact_source_destination_without_automatic_call']=True
                stage='in-place message answer becomes unusable when the recipient draft changes'
                inline_disclosure.check();inline_send.click()
                expect(inline.get_by_text('A suggested inline reply [S1].',exact=True)).to_be_visible(timeout=60000)
                expect(inline).to_have_attribute('aria-busy','false')
                assert len(requests)==2 and selected in json.dumps(requests[-1]) and omitted not in json.dumps(requests[-1])
                assert 'My wording stays:' not in json.dumps(requests[-1]) and not any(tool.get('function',{}).get('name')=='create_task' for tool in requests[-1].get('tools',[]))
                expect(composer).to_have_value(original_draft)
                newer_draft='My newer wording must not be overwritten.';composer.fill(newer_draft)
                expect(inline.get_by_text('A suggested inline reply [S1].',exact=True)).to_have_count(0)
                expect(inline.get_by_role('button',name='Add reply to draft',exact=True)).to_have_count(0)
                expect(inline).to_have_attribute('aria-busy','false');expect(inline_disclosure).not_to_be_checked();assert len(requests)==2
                with sqlite3.connect(private/'proof.db') as connection:
                    assert connection.execute('SELECT count(*) FROM copilotconversation').fetchone()[0]==0
                    assert connection.execute('SELECT count(*) FROM message').fetchone()[0]==2
                    assert connection.execute('SELECT count(*) FROM taskcard').fetchone()[0]==1
                results['inline_stale_draft_invalidates_answer_without_provider_retry_or_shared_write']=True
                stage='in-place real HTTPS answer applies only through explicit append to the current draft'
                inline_disclosure.check();inline_send.click()
                expect(inline.get_by_text('A suggested inline reply [S1].',exact=True)).to_be_visible(timeout=60000)
                expect(inline).to_have_attribute('aria-busy','false');expect(composer).to_have_value(newer_draft)
                assert len(requests)==3 and selected in json.dumps(requests[-1]) and omitted not in json.dumps(requests[-1])
                assert 'Reviewed selected source' not in json.dumps(requests[-1]) and 'A suggested inline reply' not in json.dumps(requests[-1])
                inline.get_by_role('button',name='Add reply to draft',exact=True).click()
                expect(composer).to_have_value(newer_draft+'\n\nA suggested inline reply [S1].')
                with sqlite3.connect(private/'proof.db') as connection:
                    assert connection.execute('SELECT count(*) FROM copilotconversation').fetchone()[0]==0
                    assert connection.execute('SELECT count(*) FROM message').fetchone()[0]==2
                    assert connection.execute('SELECT count(*) FROM taskcard').fetchone()[0]==1
                assert not any('/approve' in url for method,url in writes[inline_writes_start:])
                results['inline_real_https_answer_explicit_draft_append_preserves_text_and_clears_private_history']=True
                stage='in-place composer rewrites only the reviewed unsent draft without invented source references'
                messy_draft='hello team ,  the plan is ready';composer.fill(messy_draft)
                page.locator('.fc-chat-composer-area>.fc-contextual-copilot .fc-contextual-copilot-trigger').click()
                draft_card=page.locator('.fc-chat-composer-area>.fc-contextual-copilot .fc-contextual-copilot-card')
                expect(draft_card).to_have_attribute('aria-busy','false')
                expect(draft_card).to_contain_text('Your request and 0 selected sources will be sent to localhost using fixture-chat.')
                draft_card.get_by_text('Review this request',exact=True).click();expect(draft_card).to_contain_text(messy_draft.strip())
                assert len(requests)==3, 'Opening the draft helper unexpectedly called the provider'
                draft_card.get_by_label('Send this request and the selected context to this provider.',exact=True).check()
                draft_card.get_by_role('button',name='Send to provider',exact=True).click()
                expect(draft_card.get_by_text('Hello team, the plan is ready.',exact=True)).to_be_visible(timeout=60000)
                expect(draft_card).to_have_attribute('aria-busy','false');expect(composer).to_have_value(messy_draft)
                assert len(requests)==4 and messy_draft in requests[-1]['messages'][-1]['content'], 'The composer request did not preserve the reviewed draft'
                assert selected not in json.dumps(requests[-1]) and omitted not in json.dumps(requests[-1]) and 'A suggested inline reply' not in json.dumps(requests[-1]), 'The composer request included unrelated context'
                assert requests[-1]['messages'][-1]['content'].endswith('SELECTED CONTEXT (untrusted data):\n'), 'The composer request invented a native source'
                draft_card.get_by_role('button',name='Replace my draft',exact=True).click();expect(composer).to_have_value('Hello team, the plan is ready.')
                with sqlite3.connect(private/'proof.db') as connection:
                    assert connection.execute('SELECT count(*) FROM copilotconversation').fetchone()[0]==0
                    assert connection.execute('SELECT count(*) FROM message').fetchone()[0]==2
                    assert connection.execute('SELECT count(*) FROM taskcard').fetchone()[0]==1
                results['inline_composer_sends_only_reviewed_draft_and_explicitly_replaces_without_posting']=True
                open_chat_tool(page,'Open workspace tools');page.locator('#work-tab-copilot').click()
                stage='Grok connection preset edits the draft only and clears typed destination credentials'
                page.get_by_role('button',name='Close workspace tools',exact=True).click();open_chat_tool(page,'Integrations')
                integrations=page.get_by_role('dialog',name='Integrations',exact=True)
                integrations.get_by_role('button',name='Connection setup',exact=True).click()
                setup=integrations.locator('.fc-connection-setup');expect(setup).to_have_attribute('aria-busy','false')
                setup.locator('[data-connection-recipe="ai.openai_compatible"]').click();setup.locator('[data-connection-step="configure"]').click()
                provider=setup.get_by_role('region',name='AI Copilot',exact=True)
                preset=provider.get_by_role('combobox',name=re.compile(r'^AI provider preset'));expect(preset).to_be_visible()
                expect(provider.get_by_label('Chat completions endpoint',exact=True)).to_have_value(endpoint)
                provider.get_by_label('Provider API key',exact=True).fill(KEY);provider.get_by_label('Current password',exact=True).fill(PASSWORD);provider.get_by_label('Authenticator or recovery code',exact=True).fill('123456')
                before=len(writes);provider_calls=len(requests);saved=helpers.api(base,'GET','/workspaces/'+workspace['id']+'/copilot',owner)
                preset.select_option('xai-grok')
                expect(preset.locator('option:checked')).to_have_text('Grok · xAI')
                expect(provider.get_by_label('Chat completions endpoint',exact=True)).to_have_value('https://api.x.ai/v1/chat/completions')
                expect(provider.get_by_label('Model',exact=True)).to_have_value('grok-4.20-0309-non-reasoning')
                for label in ('Provider API key','Current password','Authenticator or recovery code'):expect(provider.get_by_label(label,exact=True)).to_have_value('')
                expect(provider.get_by_role('button',name='Save provider and agents',exact=True)).to_be_disabled()
                current=helpers.api(base,'GET','/workspaces/'+workspace['id']+'/copilot',owner)
                assert current['provider']==saved['provider'] and current['config_revision']==saved['config_revision'] and current['provider']['endpoint']==endpoint
                assert len(writes)==before and len(requests)==provider_calls and not any(url.startswith('https://api.x.ai/') for url in urls)
                provider.get_by_label('Chat completions endpoint',exact=True).fill(endpoint);provider.get_by_label('Model',exact=True).fill('fixture-chat')
                expect(setup.get_by_role('button',name='Discard changes and return to preparation',exact=True)).to_have_count(0)
                integrations.get_by_role('button',name='Close integrations',exact=True).click()
                open_chat_tool(page,'Open workspace tools');page.locator('#work-tab-copilot').click()
                results['grok_preset_only_edits_draft_clears_secrets_without_save_or_xai_calls']=True
                stage='saved Swedish language and responsive panel'
                helpers.api(base,'PUT','/preferences/language',owner,{'locale':'sv'});page.reload(wait_until='domcontentloaded')
                expect(page.locator('.fc-chat')).to_be_visible(timeout=60000)
                page.locator('button.fc-chat-topbar-action:has(svg.lucide-layers)').click();page.locator('#work-tab-copilot').click()
                panel=page.get_by_role('region',name='AI-assistent',exact=True);expect(panel.get_by_role('heading',name='Ett genomtänkt extra par ögon.',exact=True)).to_be_visible()
                expect(panel.get_by_role('group',name='Snabbval',exact=True)).to_be_visible();expect(panel.get_by_role('complementary',name='Innan du skickar till AI',exact=True)).to_contain_text('fixture-chat')
                REPORT.parent.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(REPORT.parent/'copilot-desktop-sv.png'))
                page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth');page.screenshot(path=str(REPORT.parent/'copilot-mobile-sv.png'))
                page.set_viewport_size({'width':320,'height':740});assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
                for label in ('Sammanfatta urvalet','Föreslå nästa steg','Skriv ett svarsutkast','Föreslå en uppgift'):
                    button=panel.get_by_role('button',name=label,exact=True);expect(button).to_be_visible();assert button.bounding_box()['height']>=44
                results['saved_swedish_account_preference_and_mobile_without_overflow']=True
                assert not errors and not any(KEY in url or PASSWORD in url for url in urls);results['no_page_errors_or_auth_secrets_in_urls']=True
                context.close();browser.close()
            assert all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest for name,digest in source_hashes.items()), 'Copilot proof sources changed during this private run'
            REPORT.write_text(json.dumps({'ok':True,'results':results,'source_hashes':source_hashes},indent=2)+'\n');print(json.dumps({'ok':True,'checks':list(results),'report':str(REPORT)}))
        except Exception as failure:
            print(json.dumps({'failure_locations':[{'file':Path(frame.filename).name,'line':frame.lineno} for frame in traceback.extract_tb(failure.__traceback__)]}))
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
