#!/usr/bin/env python3
"""Real sample-session return summary in the shared App, with retained-storage guards."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from ui_navigation import activate_chat_feature, choose_chat_channel, close_channels, show_channel_introduction

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('return_demo_helpers', Path(__file__).with_name('product-demo-browser.py'))
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
REPORT = Path(os.getenv('CHANNEL_RETURN_DEMO_REPORT', str(ROOT.parent/'.freecollab/proof-results/channel-return-demo-browser.json')))


class HeldConversationWork:
    """Deliver one genuine authorized response after the reading gesture."""
    def __init__(self, page, app):
        self.page, self.pending, self.captured = page, None, False
        page.route(app+'/api/channels/*/conversation-work*', self.hold)

    def hold(self, route):
        if self.captured or route.request.method!='GET' or not urlsplit(route.request.url).path.endswith('/conversation-work'):
            route.continue_(); return
        self.captured = True
        assert 'retained-real-' not in route.request.headers.get('authorization','')
        response = route.fetch()
        assert response.ok
        self.pending = (route, response)

    def release(self, channel, token):
        deadline = time.monotonic()+15
        while self.pending is None:
            assert time.monotonic()<deadline,'The actual conversation-work read was not captured'
            self.page.wait_for_timeout(25)
        route, response = self.pending
        assert urlsplit(route.request.url).path=='/api/channels/'+channel+'/conversation-work'
        assert route.request.headers.get('authorization')=='Bearer '+token
        route.fulfill(response=response)
        self.pending = None
        self.page.wait_for_function("document.querySelectorAll('.fc-discussion-work').length>0",polling=100)


def reading_geometry(page, message_id):
    return page.evaluate('''id=>{
      const timeline=document.querySelector('.fc-chat-timeline'),root=document.getElementById('message-'+id);
      const t=timeline.getBoundingClientRect(),r=root.getBoundingClientRect();
      return {top:timeline.scrollTop,height:timeline.scrollHeight,viewport:timeline.clientHeight,gap:timeline.scrollHeight-timeline.scrollTop-timeline.clientHeight,
        rootTop:r.top,rootBottom:r.bottom,timelineTop:t.top,timelineBottom:t.bottom,
        summaries:document.querySelectorAll('.fc-discussion-work').length,roomDisplay:getComputedStyle(document.querySelector('.fc-chat-room-content')).display,
        workColumnHidden:document.querySelector('.fc-chat-room-work-column').hidden};
    }''',message_id)


def main():
    checks, processes, streams, stage, source_hashes, evidence = {}, [], [], 'private startup', {}, []
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='freecollab-return-demo-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = p.port(), p.port()
            base, app = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
            frontend, backend, data = private/'frontend', private/'backend', private/'public-demo'
            backend.mkdir(); data.mkdir()
            shutil.copytree(ROOT/'backend/app', backend/'app', ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(ROOT/'frontend', frontend, ignore=shutil.ignore_patterns('node_modules','dist*','.vite*','.git','__pycache__'))
            (frontend/'node_modules').symlink_to(ROOT/'frontend/node_modules', target_is_directory=True)
            source_hashes = p.runtime_hashes(private)
            for name in ('channel-return-demo-browser.py','ui_navigation.py'):
                source_hashes['frontend/tests/'+name] = hashlib.sha256((frontend/'tests'/name).read_bytes()).hexdigest()
            env = os.environ.copy()
            env.update(ENVIRONMENT='test', DEMO_INSTANCE='true', DEMO_DATA_PATH=str(data), DATABASE_URL='sqlite:///'+str(data/'public-demo.sqlite'),
                STORAGE_PATH=str(data/'files'), STORAGE_BACKEND='local', JWT_SECRET=secrets.token_urlsafe(48),
                ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), ACCOUNT_SECURITY_KEY_FILE=str(data/'account-security.key'),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',
                MESSAGE_ENCRYPTION_KEY_FILE=str(data/'message-keys.json'), PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]),
                REDIS_URL='', MAIL_DELIVERY_MODE='disabled', MEETINGS_SFU_PROVIDER='disabled', MEETINGS_ICE_SERVERS='[]', MEETINGS_TURN_URLS='[]',
                VITE_API_URL='/api', PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(backend))
            for flag in ['MATRIX_ENABLED','CONNECT_FEDERATION_ENABLED','OIDC_ENABLED','WEBHOOKS_ENABLED','SYNC_ENABLED','OTRUST_ENABLED',
                         'CLOUD_FILES_ENABLED','EXTERNAL_MEETINGS_ENABLED','COPILOT_ENABLED','PUSH_ENABLED','NATIVE_PUSH_ENABLED','NATIVE_CALLS_ENABLED',
                         'ENABLE_EXPERIMENTAL_FEATURES','ENTERPRISE_SEARCH_ENABLED','DATA_CONTROLS_ENABLED']:
                env[flag] = 'false'
            python = os.getenv('BACKEND_PYTHON') or str(ROOT/'backend/.venv/bin/python')
            server = private/'api.py'; server.write_text('import uvicorn\nfrom app.main import socket_app\nuvicorn.run(socket_app,host="127.0.0.1",port='+str(api_port)+',proxy_headers=False,access_log=False)\n')
            vite = private/'vite.mjs'
            vite.write_text('import {createServer} from '+json.dumps(str(ROOT/'frontend/node_modules/vite/dist/node/index.js'))+';\n'
                'const server=await createServer({configFile:'+json.dumps(str(frontend/'vite.demo.config.ts'))+',cacheDir:'+json.dumps(str(private/'vite-cache'))+
                ',server:{host:"127.0.0.1",port:'+str(app_port)+',strictPort:true,fs:{allow:'+json.dumps([str(private),str(ROOT/'frontend')])+'},proxy:{"/api":{target:'+json.dumps(base)+
                ',changeOrigin:true,rewrite:p=>p.replace(/^\\/api/,"")},"/socket.io":{target:'+json.dumps(base)+',ws:true}}}});await server.listen();\n')
            for command, name, cwd in [([python,str(server)],'api.log',backend),(['node',str(vite)],'vite.log',frontend)]:
                stream = (private/name).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command,cwd=cwd,env=env,stdout=stream,stderr=stream,start_new_session=True))
            p.ready(base+'/ready',processes); p.ready(app+'/product-demo/',processes)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or '/usr/bin/chromium',headless=True,args=['--no-sandbox'])
                # Keep the genuine first unread history scrollable, so the
                # reading choice is explicit rather than an already-visible end.
                context = browser.new_context(viewport={'width':1440,'height':700},locale='en-US',reduced_motion='reduce')
                context.add_init_script(p.INSTRUMENTATION)
                unexpected, errors, summary_reads = [], [], []
                def network(route):
                    url = urlsplit(route.request.url)
                    if url.scheme not in ('blob','data') and (url.hostname != '127.0.0.1' or url.port != app_port):
                        unexpected.append(url.scheme+'://'+url.netloc+url.path); route.abort(); return
                    assert 'retained-real-' not in route.request.headers.get('authorization','')
                    route.continue_()
                context.route('**/*',network)
                page = context.new_page(); page.set_default_timeout(30000)
                held_context = HeldConversationWork(page,app)
                receipt_posts = []
                page.on('request',lambda request:receipt_posts.append(request) if request.method=='POST' and '/api/read-receipts/channels/' in urlsplit(request.url).path else None)
                page.on('pageerror',lambda error:errors.append(type(error).__name__))
                page.on('response',lambda response:summary_reads.append(response) if urlsplit(response.url).path.endswith('/return-summary') else None)
                stage = 'actual disposable demo entry and allowlisted summary'
                with page.expect_response(lambda response: response.request.method=='POST' and urlsplit(response.url).path=='/api/demo/session') as started, page.expect_response(lambda response: response.request.method=='GET' and re.fullmatch(r'/api/messages/channels/[^/]+',urlsplit(response.url).path) is not None) as initial_history:
                    page.goto(app+'/product-demo/?locale=en',wait_until='domcontentloaded')
                assert started.value.ok
                visit = started.value.json(); token = visit['session']['access_token']; channel = visit['channel_id']
                expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
                stage = 'actual sample history and explicit latest reading'
                history = initial_history.value
                assert history.ok and urlsplit(history.url).path=='/api/messages/channels/'+channel
                assert history.request.headers.get('authorization')=='Bearer '+token
                roots = [row for row in history.json() if not row.get('parent_id') and not row.get('deleted')]
                assert len(roots)>=3
                oldest, newest = min(roots,key=lambda row:row['created_at']), max(roots,key=lambda row:row['created_at'])
                latest = page.locator('#message-'+newest['id'])
                expect(latest).to_be_attached()
                expect(page.locator('.fc-chat-history-loading')).to_have_count(0)
                timeline = page.locator('.fc-chat-timeline')
                page.wait_for_function("document.querySelector('.fc-chat-timeline').scrollHeight>document.querySelector('.fc-chat-timeline').clientHeight")
                first_action = page.locator('#message-'+oldest['id']).get_by_role('button',name='More message actions',exact=True)
                first_action.focus(); first_action.press('Control+Home')
                page.wait_for_function("document.querySelector('.fc-chat-timeline').scrollTop===0")
                evidence.append({'sampleHistory':{'status':history.status,'rootCount':len(roots),'newestRootId':newest['id'],
                    'timelineBeforeJump':timeline.evaluate('(node)=>({top:node.scrollTop,height:node.scrollHeight,viewport:node.clientHeight})')}})
                # Opening the App and scrolling an empty/loading timeline do
                # not choose to follow latest. Use the actual reading control.
                with page.expect_response(lambda response: response.request.method=='POST' and urlsplit(response.url).path=='/api/read-receipts/channels/'+channel and response.request.post_data_json=={'message_id':newest['id']} and response.request.headers.get('authorization')=='Bearer '+token) as explicit_receipt:
                    page.get_by_role('button',name='Jump to latest messages',exact=True).click()
                assert explicit_receipt.value.ok
                before_context = reading_geometry(page,newest['id'])
                receipt_count = len(receipt_posts)
                held_context.release(channel,token)
                expect(latest).to_be_in_viewport(ratio=.7)
                page.wait_for_function('''id=>{const n=document.querySelector('.fc-chat-timeline'),r=document.getElementById('message-'+id).getBoundingClientRect(),t=n.getBoundingClientRect();return Math.abs(n.scrollHeight-n.scrollTop-n.clientHeight)<=1&&r.bottom<=t.bottom+1&&r.bottom>t.top}''',arg=newest['id'],timeout=5000)
                after_context = reading_geometry(page,newest['id'])
                assert before_context['summaries']==0 and after_context['summaries']>0
                assert after_context['workColumnHidden'] and after_context['roomDisplay']=='block'
                assert len(receipt_posts)==receipt_count
                evidence.append({'lateAuthorizedContext':{'before':before_context,'after':after_context,'extraReceiptPosts':len(receipt_posts)-receipt_count}})
                checks['late_authorized_work_keeps_explicit_latest_visible_without_an_extra_receipt'] = True
                deadline = time.monotonic()+15
                while True:
                    receipts = p.request(base,'GET','/read-receipts/channels/'+channel,token)
                    if any(row['user_id']==visit['session']['user_id'] and row['last_read_message_id']==newest['id'] for row in receipts): break
                    assert time.monotonic()<deadline,'Actual sample reading did not establish its receipt'
                    page.wait_for_timeout(200)
                evidence.append({'explicitLatestReading':{'receiptStatus':explicit_receipt.value.status,'messageId':newest['id'],
                    'timelineAfterJump':timeline.evaluate('(node)=>({top:node.scrollTop,height:node.scrollHeight,viewport:node.clientHeight})')}})
                checks['actual_loaded_sample_history_and_explicit_latest_choice_establish_exact_server_receipt'] = True
                channels = p.request(base,'GET','/channels?workspace_id='+visit['workspace_id'],token)
                target = next(row for row in channels if row['id']==channel)
                another = next(row for row in channels if row['id']!=channel)
                choose_chat_channel(page,another['name']); close_channels(page)
                p.request(base,'POST','/channels/'+channel+'/decisions',token,{'title':'A sample return decision','content':'Only this disposable visit.','status':'accepted'})
                choose_chat_channel(page,target['name']); close_channels(page)
                summary = page.get_by_role('region',name='Since your last read',exact=True)
                expect(summary).to_be_visible(timeout=30000)
                deadline = time.monotonic()+10
                while not summary_reads:
                    assert time.monotonic()<deadline,'Actual shared App did not inspect its return summary'; page.wait_for_timeout(25)
                read = summary_reads[0]
                assert read.ok and urlsplit(read.url).path=='/api/read-receipts/channels/'+channel+'/return-summary'
                assert read.request.headers.get('authorization')=='Bearer '+token
                record = read.json(); assert record['channel_id']==channel
                checks['shared_app_requests_real_allowlisted_summary_with_only_its_sample_visit_token'] = True
                before = p.request(base,'GET','/read-receipts/channels/'+channel,token)
                summary.locator('summary').click()
                for key, label in [('decisions','Decisions'),('documents','Updated documents'),('attention','For your attention')]:
                    expect(summary.locator('summary')).to_contain_text(label+': '+str(record['groups'][key]['count']))
                summary.locator('summary').click()
                after = p.request(base,'GET','/read-receipts/channels/'+channel,token)
                assert after==before
                checks['sample_summary_matches_real_records_and_preview_does_not_mark_read'] = True
                status = page.evaluate('''async({channel,token})=>{const r=await fetch('/api/read-receipts/channels/'+channel+'/return-summary/encrypted-decisions?since=2026-01-01T00%3A00%3A00',{headers:{Authorization:'Bearer '+token}});return r.status}''',{'channel':channel,'token':token})
                assert status==403
                checks['sample_policy_still_denies_secure_only_metadata_subroute'] = True
                p.storage_untouched(page)
                assert not errors and not unexpected, {'errors':errors,'unexpected':unexpected}
                checks['summary_keeps_retained_account_storage_vaults_workers_and_external_services_untouched'] = True
                page.screenshot(path=str(REPORT.with_suffix('.png')))
                context.close()
                for mode in ('older','modal'):
                    stage = 'late actual context preserves '+mode+' reading'
                    guarded_context = browser.new_context(viewport={'width':1440,'height':700},locale='en-US',reduced_motion='reduce')
                    guarded_context.add_init_script(p.INSTRUMENTATION)
                    guarded_context.route('**/*',network)
                    guarded_page = guarded_context.new_page(); guarded_page.set_default_timeout(30000)
                    guarded_page.on('pageerror',lambda error:errors.append(type(error).__name__))
                    guarded_hold = HeldConversationWork(guarded_page,app)
                    guarded_posts = []
                    guarded_page.on('request',lambda request:guarded_posts.append(request) if request.method=='POST' and '/api/read-receipts/channels/' in urlsplit(request.url).path else None)
                    try:
                        with guarded_page.expect_response(lambda response:response.request.method=='POST' and urlsplit(response.url).path=='/api/demo/session') as guarded_start, guarded_page.expect_response(lambda response:response.request.method=='GET' and re.fullmatch(r'/api/messages/channels/[^/]+',urlsplit(response.url).path) is not None) as guarded_history:
                            guarded_page.goto(app+'/product-demo/?locale=en',wait_until='domcontentloaded')
                        assert guarded_start.value.ok and guarded_history.value.ok
                        guarded_visit = guarded_start.value.json(); guarded_token = guarded_visit['session']['access_token']; guarded_channel = guarded_visit['channel_id']
                        assert guarded_history.value.request.headers.get('authorization')=='Bearer '+guarded_token
                        assert urlsplit(guarded_history.value.url).path=='/api/messages/channels/'+guarded_channel
                        guarded_roots = [row for row in guarded_history.value.json() if not row.get('parent_id') and not row.get('deleted')]
                        anchor = min(guarded_roots,key=lambda row:row['created_at'])
                        anchor_action = guarded_page.locator('#message-'+anchor['id']).get_by_role('button',name='More message actions',exact=True)
                        expect(anchor_action).to_be_attached(); expect(guarded_page.locator('.fc-chat-history-loading')).to_have_count(0)
                        anchor_action.focus(); anchor_action.press('Control+Home')
                        guarded_page.wait_for_function("document.querySelector('.fc-chat-timeline').scrollTop===0")
                        if mode=='modal':
                            activate_chat_feature(guarded_page,'appearance')
                            expect(guarded_page.locator('.appearance-panel[aria-modal=true]')).to_be_visible()
                        before = reading_geometry(guarded_page,anchor['id'])
                        before_receipts = p.request(base,'GET','/read-receipts/channels/'+guarded_channel,guarded_token)
                        assert not guarded_posts
                        guarded_hold.release(guarded_channel,guarded_token)
                        guarded_page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                        after = reading_geometry(guarded_page,anchor['id'])
                        assert before['top']==after['top']==0 and abs(before['rootTop']-after['rootTop'])<=1
                        assert after['workColumnHidden'] and after['roomDisplay']=='block'
                        assert not guarded_posts
                        assert p.request(base,'GET','/read-receipts/channels/'+guarded_channel,guarded_token)==before_receipts
                        if mode=='modal':
                            expect(guarded_page.locator('.appearance-panel[aria-modal=true]')).to_be_visible()
                            guarded_page.get_by_role('button',name='Close appearance settings',exact=True).click()
                        else:
                            show_channel_introduction(guarded_page)
                            expect(guarded_page.locator('.fc-chat-shared-material')).to_be_visible()
                            expect(guarded_page.locator('.fc-chat-room-content')).to_have_css('display','grid')
                            assert not guarded_page.locator('.fc-chat-room-work-column').evaluate('node=>node.hidden')
                        guarded_page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                        assert not guarded_posts
                        assert p.request(base,'GET','/read-receipts/channels/'+guarded_channel,guarded_token)==before_receipts
                        p.storage_untouched(guarded_page)
                        evidence.append({'lateContextReadingGuard':mode,'before':before,'after':after,'receiptPosts':len(guarded_posts),'serverReceiptsUnchanged':True})
                        checks['late_authorized_work_preserves_'+mode+'_reading_without_a_receipt_write'] = True
                        guarded_page.screenshot(path=str(REPORT.with_name(REPORT.stem+'-'+mode+'.png')))
                    finally:
                        guarded_context.close()
                assert not errors and not unexpected, {'errors':errors,'unexpected':unexpected}
                current_hashes = p.runtime_hashes(ROOT)
                for name in ('channel-return-demo-browser.py','ui_navigation.py'):
                    current_hashes['frontend/tests/'+name] = hashlib.sha256((ROOT/'frontend/tests'/name).read_bytes()).hexdigest()
                assert source_hashes==current_hashes,'The copied actual demo source changed during the proof'
                browser.close(); stage = 'passed'
        except Exception:
            for name in ['api.log','vite.log']:
                if (private/name).is_file(): shutil.copyfile(private/name,REPORT.with_name(REPORT.stem+'.'+name))
            raise
        finally:
            p.stop(processes)
            for stream in streams: stream.close()
            REPORT.write_text(json.dumps({'stage':stage,'checks':checks,'evidence':evidence,'source_sha256':source_hashes,
                'limits':['Actual public-demo native App/API with temporary sample credentials and owned loopback services only.',
                          'No retained real account, external provider, public deployment or encrypted-room acceptance is claimed.'],
                'cleanup':'Private API/Vite processes, SQLite/keys/source snapshots and browser context removed.'},indent=2)+'\n')
    print('PASS: '+str(len(checks))+' actual sample-session return-summary groups',flush=True)


if __name__=='__main__': main()
