#!/usr/bin/env python3
"""Actual AI product components, disposable API/worker and owned HTTPS recipients.

The host supplies a real private session and collaboration scope. No UI API
responses, provider adapters, jobs, search, collections or ledger writes are mocked.
Only endpoint policy for the exact owned TLS fixture is substituted in the launcher.
"""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from tests.oidc_mock import tls_fixture
spec = importlib.util.spec_from_file_location('private_helpers', Path(__file__).with_name('remote-sync-browser.py'))
helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(helpers)
PYTHON = os.getenv('BACKEND_PYTHON') or str(ROOT.parent / 'core/backend/.venv/bin/python')
REPORT = Path(os.getenv('AI_SURFACE_REPORT', str(ROOT.parent / '.freecollab/proof-results/gap-completion-20261025/ai-surface/browser.json')))
PASSWORD = 'Private-ai-surface!2026'
KEY = 'synthetic-owned-ai-surface-key'
SOURCES = ['frontend/src/components/' + name + '.tsx' for name in ['AIRuntimePanel', 'AIReadTools', 'WritingCopilot', 'ReviewedResourceCopilot', 'ContextualCopilot', 'CollaborationInput', 'ScopedCollaborationInput']]
SOURCES += ['frontend/src/lib/ai-runtime-api.ts', 'frontend/src/lib/copilot-api.ts', 'frontend/src/lib/content-collections-api.ts', 'frontend/src/lib/semantic-search.ts', 'frontend/tests/ai-surface-browser.py', 'backend/app/services/ai_runtime.py', 'backend/app/api/ai_runtime.py', 'backend/app/services/ai_providers.py', 'backend/app/services/semantic_index.py']
STYLE = ':root{--fc-bg:#fff;--fc-surface:#faf9f6;--fc-text:#252722;--fc-muted:#686c64;--fc-border:#dddcd7;--fc-accent:#8e5144;--fc-accent-soft:#f3eee7;--fc-danger:#a12e25;--fc-font:sans-serif;--fc-heading-font:sans-serif;--fc-radius:10px;--fc-body-size:16px;--fc-caption-size:12px;--fc-detail-size:14px}*{box-sizing:border-box}body{font-family:sans-serif;margin:0;padding:16px;background:var(--fc-bg);color:var(--fc-text)}main{max-width:780px;margin:auto}button{min-height:44px;max-width:100%;cursor:pointer}textarea{max-width:100%}.fc-button{padding:8px 12px;border:1px solid var(--fc-border);background:var(--fc-surface);color:inherit;border-radius:7px}#writer{width:100%;min-height:100px;font:inherit}section{min-width:0}'
ENTRY = '''import React,{useState}from'react';import{createRoot}from'react-dom/client';import{api}from'./src/lib/api';import{AIRuntimePanel}from'./src/components/AIRuntimePanel';import{CollaborationInput}from'./src/components/CollaborationInput';import{ReviewedResourceCopilot}from'./src/components/ReviewedResourceCopilot';import{CollaborationProvider}from'./src/lib/collaboration-context';
const fixture=__FIXTURE__;api.applySession(fixture.owner);
const commands={hasChannel:true,hasWorkspace:true,secure:false,embedded:false,nativeSession:true,workspaceRole:'owner',hasAdmin:true};
function App(){const[text,setText]=useState('Keep the courtyard welcoming.'),[long,setLong]=useState(false),[channel,setChannel]=useState(fixture.channel);return <main><h1>Private AI collaboration proof</h1><button onClick={()=>setChannel(fixture.other)}>Switch conversation</button><CollaborationProvider value={{surfaceId:'proof',workspaceId:fixture.workspace,channelId:channel,commandContext:commands,onCommand:()=>false}}><section id="writer-host"><h2>Current draft</h2><CollaborationInput id="writer" aria-label="Current draft" contextId="proof:actual-writer" as="textarea" value={text} onValueChange={setText} maxLength={50000} toolbar/><button onClick={()=>setText('A newer draft owns this field.')}>Change source draft</button></section><section id="read-host"><h2>Current source</h2><button onClick={()=>setLong(value=>!value)}>Toggle large source</button><ReviewedResourceCopilot workspaceId={fixture.workspace} channelId={channel} label="Reviewed courtyard message" snapshot={long?'long':'short'} text={long?'Visible current source '.repeat(200):fixture.message.content} sourceRefs={[{kind:'message',id:fixture.message.id}]}/></section><AIRuntimePanel workspaceId={fixture.workspace} channelId={channel} isAdmin/></CollaborationProvider></main>}
createRoot(document.getElementById('root')).render(<App/>);'''


def main():
    checks, errors, requests, reads, processes, streams = {}, [], [], [], [], []
    stage = 'startup'; server = browser = None
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='freecollab-ai-surface-') as directory:
        private = Path(directory)
        try:
            certificate, certificate_key = tls_fixture(private / 'tls')
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_): pass
                def do_GET(self):
                    reads.append(parse_qs(urlsplit(self.path).query)); data = json.dumps({'results':[{'title':'Owned search result','url':'https://example.com/review','content':'Actual bounded external result for the courtyard.'}]}).encode()
                    self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
                def do_POST(self):
                    assert self.headers.get('Authorization') == 'Bearer ' + KEY
                    payload = json.loads(self.rfile.read(int(self.headers['Content-Length']))); requests.append(payload)
                    text = '\n'.join(item['content'] for item in payload['messages'])
                    if 'Propose a bounded plan, but do not execute it.' in text:
                        catalog = json.JSONDecoder().raw_decode(text.split('this reviewed catalog: ',1)[1])[0]
                        result = json.dumps({'plan':[{'operation':'summarize','profile_id':catalog[0]['profile_id'],'instructions':'Summarize selected sources.'},{'operation':'draft','profile_id':catalog[-1]['profile_id'],'instructions':'Review the prior summary.'}]})
                    elif 'Return ONLY a JSON object {"groups"' in text:
                        ids = re.findall(r'\[S\d+\] (?:message|resource|shared_document|file|clip) ([^\n]+)\n', text)
                        assert len(ids) >= 2, 'Grouping provider did not receive two actual IDs'
                        result = json.dumps({'groups':[{'title':'Courtyard decisions','source_ids':ids[:2],'reason':'Both reviewed sources concern the same space.'}]})
                    elif 'Improve the clarity and grammar of this text.' in text:
                        result = 'Keep the courtyard warm and welcoming.'
                    else: result = 'Actual selected-source summary from the owned HTTPS model.'
                    data = json.dumps({'choices':[{'finish_reason':'stop','message':{'content':result}}], 'usage':{'prompt_tokens':37,'completion_tokens':19}}).encode()
                    self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data))); self.end_headers()
                    for index in range(0,len(data),17): self.wfile.write(data[index:index+17]); self.wfile.flush()
            server = ThreadingHTTPServer(('127.0.0.1',0), Handler); tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); tls.load_cert_chain(certificate,certificate_key); server.socket = tls.wrap_socket(server.socket,server_side=True); threading.Thread(target=server.serve_forever,daemon=True).start()
            origin = 'https://localhost:' + str(server.server_port); endpoint = origin + '/native'; read_endpoint = origin + '/read'
            api_port, web_port = helpers.port(), helpers.port(); base, app = 'http://127.0.0.1:' + str(api_port), 'http://127.0.0.1:' + str(web_port)
            environment = {**os.environ, 'ENVIRONMENT':'test','DATABASE_URL':'sqlite:///' + str(private/'proof.db'),'STORAGE_PATH':str(private/'files'),'JWT_SECRET':secrets.token_urlsafe(48),'ACCOUNT_SECURITY_KEY':base64.b64encode(secrets.token_bytes(32)).decode(),'MESSAGE_ENCRYPTION_KEYS':json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),'MESSAGE_ENCRYPTION_ACTIVE_KEY_ID':'proof','MESSAGE_ENCRYPTION_KEY_FILE':str(private/'keys.json'),'ACCOUNT_SECURITY_KEY_FILE':str(private/'account.key'),'MATRIX_ENABLED':'false','MAIL_DELIVERY_MODE':'disabled','PUBLIC_APP_URL':app,'CORS_ORIGINS':json.dumps([app]),'OIDC_ENABLED':'false','OTRUST_ENABLED':'false','WEBHOOKS_ENABLED':'false','SYNC_ENABLED':'false','COPILOT_ENABLED':'true','VITE_API_URL':'/api','PYTHONPATH':str(ROOT/'backend'),'SSL_CERT_FILE':certificate,'PROOF_AI_ENDPOINT':endpoint,'FREECOLLAB_AI_TOOL_PRIVATE_ORIGINS':json.dumps([origin])}
            environment['NO_PROXY'] = ','.join(filter(None,[environment.get('NO_PROXY',''),'localhost','127.0.0.1','::1']))
            (private/'files').mkdir(mode=0o700)
            launcher = private/'api.py'; launcher.write_text('''import os,sys,uvicorn
from urllib.parse import urlsplit
from app.services import ai_providers
endpoint=os.environ['PROOF_AI_ENDPOINT']; normal=ai_providers.endpoint; proxy=ai_providers.transport._proxy
ai_providers.endpoint=lambda value,kind:(urlsplit(value),'localhost',True) if value==endpoint else normal(value,kind)
ai_providers.transport._proxy=lambda host,port:None if host=='localhost' else proxy(host,port)
if '--worker' in sys.argv:
    sys.argv=['ai-runtime-worker','--interval','1']; from app.services.ai_runtime_worker import main; main()
else:
    from app.main import socket_app
    uvicorn.run(socket_app,host='127.0.0.1',port=''' + str(api_port) + ''',proxy_headers=False,access_log=False)
''')
            def launch(command,name,cwd):
                stream=(private/name).open('w'); streams.append(stream); processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            launch([PYTHON,str(launcher)],'api.log',ROOT/'backend'); helpers.ready(base+'/ready',processes)
            owner=helpers.api(base,'POST','/auth/register',data={'name':'Private AI reviewer','email':'ai-'+secrets.token_hex(5)+'@example.com','password':PASSWORD})
            workspace=helpers.api(base,'POST','/workspaces',owner,{'name':'Actual private AI workspace'})
            channel=helpers.api(base,'POST','/channels',owner,{'name':'Courtyard','workspace_id':workspace['id']})
            other=helpers.api(base,'POST','/channels',owner,{'name':'Acoustics','workspace_id':workspace['id']})
            first=helpers.api(base,'POST','/messages/channels/'+channel['id'],owner,{'content':'Courtyard timber table with warm oak samples.'})
            second=helpers.api(base,'POST','/messages/channels/'+channel['id'],owner,{'content':'Courtyard seating should leave the timber doors open.'})
            other_message=helpers.api(base,'POST','/messages/channels/'+other['id'],owner,{'content':'Courtyard acoustic timber screens should soften the sound.'})
            prefix='/workspaces/'+workspace['id']+'/copilot/runtime'
            web=private/'web';web.mkdir();shutil.copytree(ROOT/'frontend/src',web/'src');(web/'node_modules').symlink_to(ROOT/'frontend/node_modules',target_is_directory=True)
            (web/'index.html').write_text('<!doctype html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+STYLE+'</style></head><body><div id="root"></div><script type="module" src="/entry.tsx"></script></body></html>')
            (web/'entry.tsx').write_text(ENTRY.replace('__FIXTURE__',json.dumps({'owner':owner,'workspace':workspace['id'],'channel':channel['id'],'other':other['id'],'message':first})))
            vite=private/'vite.mjs';vite.write_text('import{createServer}from '+json.dumps(str(ROOT/'frontend/node_modules/vite/dist/node/index.js'))+';import react from '+json.dumps(str(ROOT/'frontend/node_modules/@vitejs/plugin-react/dist/index.js'))+';const server=await createServer({configFile:false,root:'+json.dumps(str(web))+',plugins:[react()],cacheDir:'+json.dumps(str(private/'vite-cache'))+',resolve:{alias:{react:'+json.dumps(str(ROOT/'frontend/node_modules/react'))+',"react-dom":'+json.dumps(str(ROOT/'frontend/node_modules/react-dom'))+'}},server:{host:"127.0.0.1",port:'+str(web_port)+',strictPort:true,hmr:false,watch:{ignored:["**"]},proxy:{"/api":{target:'+json.dumps(base)+',changeOrigin:true,rewrite:path=>path.replace(/^\\/api/,"")}},fs:{allow:['+json.dumps(str(ROOT/'frontend'))+','+json.dumps(str(web))+']}}});await server.listen();')
            launch(['node',str(vite)],'vite.log',ROOT/'frontend');helpers.ready(app,processes)
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1000,'height':1000},locale='en-US',reduced_motion='reduce');page.set_default_timeout(30000);page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(app,wait_until='domcontentloaded');panel=page.get_by_role('region',name='Models and AI work',exact=True);expect(panel.get_by_label('AI budget')).to_be_visible(timeout=90000)
                def start_job():
                    with page.expect_response(lambda response: response.request.method=='POST' and response.url.endswith('/runtime/runs')) as queued:
                        panel.get_by_role('button',name='Start AI work',exact=True).click()
                    assert queued.value.status==201, queued.value.text()
                    run=queued.value.json();expect(panel.get_by_role('article',name='Current AI work',exact=True)).to_have_attribute('data-ai-run-id',run['id']);return run
                assert not requests and not reads;checks['opening_real_components_never_calls_external_recipients']=True
                stage='owner configures two real profiles and exact user consent'
                settings=panel.locator('details').filter(has=panel.locator('summary').filter(has_text=re.compile('^Model profiles and monthly limits$'))).last
                panel.get_by_text('Model profiles and monthly limits',exact=True).click()
                form=panel.get_by_role('group',name='Model profile settings',exact=True)
                def create_profile(name,model):
                    form.get_by_label('Profile name',exact=True).fill(name);form.get_by_label('Provider endpoint',exact=True).fill(endpoint);form.get_by_label('Model',exact=True).fill(model);form.get_by_label('Provider API key',exact=True).fill(KEY);form.get_by_label('Current password',exact=True).fill(PASSWORD)
                    form.get_by_role('button',name='Save model profile',exact=True).click();expect(form.get_by_label('Provider API key',exact=True)).to_have_value('');expect(form.get_by_label('Current password',exact=True)).to_have_value('')
                    consent=panel.get_by_label('I opt in to this exact model profile and recipient.',exact=True);expect(consent).to_be_enabled();consent.click();expect(consent).to_be_checked();expect(panel.get_by_role('button',name='Refresh',exact=True)).to_be_enabled()
                create_profile('Fast reviewer','fixture-fast');panel.get_by_role('button',name='New model profile',exact=True).click();create_profile('Careful reviewer','fixture-careful')
                profiles=helpers.api(base,'GET',prefix,owner)['profiles'];fast=next(profile for profile in profiles if profile['model']=='fixture-fast');careful=next(profile for profile in profiles if profile['model']=='fixture-careful')
                panel.get_by_label('Model profile',exact=True).select_option(fast['id']);assert not requests;checks['actual_profile_save_reauth_secret_clear_and_per_recipient_consent']=True
                stage='writer selection receives real provider output and explicitly applies only to current draft'
                writer=page.locator('#writer');writer.fill('Keep the courtyard welcoming.');page.locator('#writer-host').get_by_role('button',name='AI help for this text',exact=True).click()
                card=page.locator('#writer-host .fc-contextual-copilot-card');card.get_by_label('Model profile',exact=True).select_option(fast['id']);expect(card.get_by_role('button',name='Send to provider',exact=True)).to_be_disabled();assert not requests
                card.get_by_label('Send this request and the selected context to this provider.',exact=True).check();card.get_by_role('button',name='Send to provider',exact=True).click();expect(card.get_by_text('Keep the courtyard warm and welcoming.',exact=True)).to_be_visible();expect(writer).to_have_value('Keep the courtyard welcoming.')
                card.get_by_role('button',name='Use in this field',exact=True).click();expect(writer).to_have_value('Keep the courtyard warm and welcoming.');checks['real_writer_response_requires_explicit_apply']=True
                page.locator('#writer-host').get_by_role('button',name='AI help for this text',exact=True).click();card=page.locator('#writer-host .fc-contextual-copilot-card');card.get_by_label('Model profile',exact=True).select_option(fast['id']);card.get_by_label('Send this request and the selected context to this provider.',exact=True).check();card.get_by_role('button',name='Send to provider',exact=True).click();expect(card.get_by_role('button',name='Use in this field',exact=True)).to_be_visible();page.get_by_role('button',name='Change source draft',exact=True).click();expect(card).to_have_count(0);expect(writer).to_have_value('A newer draft owns this field.');checks['editing_source_invalidates_old_apply_lease']=True
                stage='read context large passage selection does not include the hidden rest of the source'
                page.get_by_role('button',name='Toggle large source',exact=True).click();read_host=page.locator('#read-host');read_host.get_by_text('Choose a passage for AI',exact=True).click();read_host.get_by_text('Choose passage by position',exact=True).click();read_host.get_by_label('Passage end (character)',exact=True).fill('43');read_host.get_by_role('button',name='Review this passage with AI',exact=True).click();read_host.get_by_role('button',name='Summarize item for Reviewed courtyard message',exact=True).click();read_card=read_host.locator('.fc-contextual-copilot-card');read_card.get_by_label('Model profile',exact=True).select_option(fast['id']);expect(read_card.get_by_text(re.compile('Your request and 0 selected sources'))).to_be_visible();checks['large_read_source_requires_exact_passage_and_omits_full_source_ref']=True
                read_card.get_by_role('button',name='Close AI help',exact=True).click()
                stage='semantic retrieval selects current accessible sources and reviewed collection applies real IDs'
                panel.get_by_label('Action',exact=True).select_option('group');panel.get_by_label('AI work request',exact=True).fill('Group the reviewed courtyard ideas without changing any source.');panel.get_by_text('Retrieve context by meaning',exact=True).click();panel.get_by_label('Find relevant context',exact=True).fill('courtyard timber');panel.get_by_role('button',name='Find context',exact=True).click()
                first_label=panel.locator('label').filter(has_text=first['content']).last;second_label=panel.locator('label').filter(has_text=second['content']).last
                expect(first_label).to_be_visible();first_label.get_by_role('checkbox').check();second_label.get_by_role('checkbox').check();before=len(requests);expect(panel.get_by_role('button',name='Start AI work',exact=True)).to_be_disabled();assert len(requests)==before
                panel.get_by_label('Send this reviewed request to the selected model.',exact=True).check();start_job();expect(panel.get_by_role('article',name='Current AI work',exact=True)).to_contain_text('queued');assert len(requests)==before
                launch([PYTHON,str(launcher),'--worker'],'worker.log',ROOT/'backend');expect(panel.get_by_role('button',name='Review as a collection',exact=True)).to_be_visible(timeout=45000);assert not helpers.api(base,'GET','/channels/'+channel['id']+'/content-collections',owner)
                panel.get_by_role('button',name='Review as a collection',exact=True).click();review=panel.get_by_role('region',name='Review collection',exact=True);expect(review).to_contain_text(first['content']);review.get_by_role('button',name='Create this collection',exact=True).click();expect(panel.get_by_text('The reviewed collection was created.',exact=True)).to_be_visible();collections=helpers.api(base,'GET','/channels/'+channel['id']+'/content-collections',owner);assert len(collections)==1 and {item['id'] for item in collections[0]['items']}=={first['id'],second['id']};checks['semantic_rag_real_worker_grouping_review_and_canonical_collection_write']=True
                stage='cross-channel retrieval is local until every source and extra disclosure is reviewed'
                panel.get_by_label('Action',exact=True).select_option('answer');panel.get_by_label('AI work request',exact=True).fill('Summarize the separately reviewed acoustic idea.');panel.get_by_label('Search permitted sources across this workspace',exact=True).check();panel.get_by_role('button',name='Find context',exact=True).click();other_row=panel.locator('label').filter(has_text=other_message['content']).last;expect(other_row).to_be_visible();other_row.get_by_role('checkbox').check();panel.get_by_label('Send this reviewed request to the selected model.',exact=True).check();expect(panel.get_by_role('button',name='Start AI work',exact=True)).to_be_disabled();before=len(requests);panel.get_by_label('I disclose these reviewed sources from other channels to the selected models. The result stays private until I apply it.',exact=True).check();expect(panel.get_by_label('Send this reviewed request to the selected model.',exact=True)).not_to_be_checked();panel.get_by_label('Send this reviewed request to the selected model.',exact=True).check();start_job();expect(panel.get_by_role('article',name='Current AI work',exact=True)).to_contain_text('awaiting_review',timeout=45000);assert len(requests)==before+1 and other_message['content'] in json.dumps(requests[-1]);assert first['content'] not in json.dumps(requests[-1]);checks['workspace_rag_requires_separate_cross_channel_disclosure_and_only_selected_text']=True
                stage='real model planner waits for review then two models consume actual prior output'
                panel.get_by_label('Action',exact=True).select_option('summarize');panel.get_by_label('AI work request',exact=True).fill('Summarize the selected courtyard ideas then prepare a draft.');panel.get_by_label('Maximum steps',exact=True).fill('2');panel.get_by_text('Review or suggest a model plan',exact=True).click();panel.locator('label').filter(has_text='Careful reviewer · fixture-careful').get_by_role('checkbox').check();panel.get_by_label('Send this reviewed request to the selected model.',exact=True).check();before=len(requests);panel.get_by_role('button',name='Suggest an AI plan',exact=True).click();expect(panel.get_by_text('Review every step and recipient before starting this plan. Nothing has executed.',exact=True)).to_be_visible();assert len(requests)==before+1;expect(panel.get_by_role('button',name='Start AI work',exact=True)).to_be_disabled();panel.get_by_label('Send this reviewed request to the selected model.',exact=True).check();start_job();expect(panel.get_by_role('article',name='Current AI work',exact=True)).to_contain_text('awaiting_review',timeout=45000);assert requests[-2]['model']=='fixture-fast' and requests[-1]['model']=='fixture-careful';assert 'Previous result:' in json.dumps(requests[-1]);checks['provider_generated_plan_exact_two_recipient_handoff_after_new_review']=True
                stage='review receipt reloads the run and removing history retains charged usage'
                panel.get_by_role('button',name='Mark reviewed',exact=True).click();expect(panel.get_by_role('article',name='Current AI work',exact=True)).to_contain_text('completed');used=helpers.api(base,'GET',prefix,owner)['budget']['used_tokens'];panel.get_by_role('button',name='Remove this private AI history',exact=True).click();expect(panel.get_by_role('article',name='Current AI work',exact=True)).to_have_count(0);assert helpers.api(base,'GET',prefix,owner)['budget']['used_tokens']==used;checks['review_receipt_canonical_reload_and_history_removal_preserves_usage']=True
                stage='registered read has separate recipient consent then model context selection'
                tools=panel.locator('.fc-ai-read-tools');tools.get_by_text('Web search and connected reads',exact=True).click();tools.get_by_text('Configure approved read tools',exact=True).click();tools.get_by_label('Tool name',exact=True).fill('Owned search');tools.get_by_label('Recipient endpoint',exact=True).fill(read_endpoint);tools.get_by_label('Current password',exact=True).fill(PASSWORD);tools.get_by_role('button',name='Save read tool',exact=True).click();expect(tools.get_by_text('Read tool saved. Each request requires its own recipient review.',exact=True)).to_be_visible();tools.get_by_label('Query for this recipient',exact=True).fill('courtyard seating');expect(tools.get_by_role('button',name='Run reviewed read',exact=True)).to_be_disabled();assert not reads;before=len(requests);tools.get_by_label('Send this query to this separate tool recipient.',exact=True).check();tools.get_by_role('button',name='Run reviewed read',exact=True).click();expect(tools.get_by_role('region',name='Review search results',exact=True)).to_contain_text('Actual bounded external result');assert len(reads)==1 and len(requests)==before;tools.get_by_role('button',name='Select this text for the AI request',exact=True).click();expect(panel.get_by_text(re.compile('characters of reviewed tool results are selected'))).to_be_visible();assert len(requests)==before;checks['real_https_tool_read_and_separate_model_disclosure']=True
                stage='real shared execution projection and budget ledger'
                panel.get_by_text('All AI and automation executions',exact=True).click();expect(panel.locator('.fc-ai-execution').first).to_be_visible();budget=helpers.api(base,'GET',prefix,owner)['budget'];assert budget['used_tokens']>0 and budget['reserved_tokens']==0;checks['actual_execution_projection_and_settled_budget_ledger']=True
                stage='canonical profile refresh retires old disclosure and a scope change clears typed secrets'
                panel.get_by_label('Send this reviewed request to the selected model.',exact=True).check();before=len(requests);helpers.api(base,'PUT',prefix+'/profiles/'+fast['id'],owner,{'password':PASSWORD,'expected_revision':fast['revision'],'name':'Fast reviewer','protocol':'openai','endpoint':endpoint,'model':'fixture-fast-v2','key_source':'workspace','enabled':True,'max_output_tokens':2048,'input_cost_per_million_micros':0,'output_cost_per_million_micros':0});panel.get_by_role('button',name='Refresh',exact=True).click();expect(panel.get_by_label('I opt in to this exact model profile and recipient.',exact=True)).not_to_be_checked();expect(panel.get_by_label('Send this reviewed request to the selected model.',exact=True)).not_to_be_checked();assert len(requests)==before;form.get_by_label('Provider API key',exact=True).fill('synthetic-unsaved-scoped-key');form.get_by_label('Current password',exact=True).fill(PASSWORD);page.get_by_role('button',name='Switch conversation',exact=True).click();expect(panel.get_by_label('AI budget')).to_be_visible();summary=panel.get_by_text('Model profiles and monthly limits',exact=True);
                if not summary.evaluate('(node)=>node.parentElement.open'): summary.click()
                expect(form.get_by_label('Provider API key',exact=True)).to_have_value('');expect(form.get_by_label('Current password',exact=True)).to_have_value('');checks['recipient_revision_refresh_and_scope_change_retire_disclosure_and_clear_secrets']=True
                stage='narrow keyboard and touch layout'
                page.set_viewport_size({'width':320,'height':900});page.get_by_role('heading',name='Private AI collaboration proof',exact=True).scroll_into_view_if_needed();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1');page.screenshot(path=str(REPORT.with_suffix('.png')),full_page=True);checks['320px_no_document_overflow']=True
                assert not errors, errors
            after={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}
            checks['current_owned_frontend_sources']=all(after[name]==hashes[name] for name in SOURCES if name.startswith('frontend/src/components/') or name in {'frontend/src/lib/ai-runtime-api.ts','frontend/src/lib/copilot-api.ts'})
            REPORT.write_text(json.dumps({'status':'passed','checks':checks,'provider_requests':len(requests),'tool_reads':len(reads),'browser_errors':errors,'source_hashes':hashes,'sources_unchanged':after==hashes,'scope':'Actual product components in explicit private host; real API, SQLite worker, semantic index, collections and synthetic TLS providers. No external customer provider, Matrix server or full Chat navigation in this proof.'},indent=2)+'\n')
            print(json.dumps({'status':'passed','checks':checks,'report':str(REPORT)}))
        except Exception as failure:
            if browser:
                try: page.screenshot(path=str(REPORT.with_name('failure.png')),full_page=True)
                except Exception: pass
            logs={path.name:path.read_text(errors='replace')[-6000:].replace(KEY,'[redacted]').replace(PASSWORD,'[redacted]') for path in private.glob('*.log')}
            REPORT.write_text(json.dumps({'status':'failed','stage':stage,'checks':checks,'error':str(failure).replace(KEY,'[redacted]').replace(PASSWORD,'[redacted]'),'browser_errors':errors,'logs':logs,'source_hashes':hashes},indent=2)+'\n');raise
        finally:
            if browser:
                try: browser.close()
                except Exception: pass
            for process in processes:
                if process.poll() is None: os.killpg(process.pid,signal.SIGTERM)
            for process in processes:
                try: process.wait(timeout=8)
                except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
            for stream in streams: stream.close()
            if server: server.shutdown();server.server_close()


if __name__=='__main__':main()
