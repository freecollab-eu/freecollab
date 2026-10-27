#!/usr/bin/env python3
"""Real sample-session return summary in the shared App, with retained-storage guards."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from ui_navigation import choose_chat_channel, close_channels

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('return_demo_helpers', Path(__file__).with_name('product-demo-browser.py'))
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
REPORT = Path(os.getenv('CHANNEL_RETURN_DEMO_REPORT', str(ROOT.parent/'.freecollab/proof-results/channel-return-demo-browser.json')))


def main():
    checks, processes, streams, stage, source_hashes = {}, [], [], 'private startup', {}
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
            source_hashes['frontend/tests/channel-return-demo-browser.py'] = hashlib.sha256((frontend/'tests/channel-return-demo-browser.py').read_bytes()).hexdigest()
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
                context = browser.new_context(viewport={'width':1440,'height':1000},locale='en-US',reduced_motion='reduce')
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
                page.on('pageerror',lambda error:errors.append(type(error).__name__))
                page.on('response',lambda response:summary_reads.append(response) if urlsplit(response.url).path.endswith('/return-summary') else None)
                stage = 'actual disposable demo entry and allowlisted summary'
                with page.expect_response(lambda response: response.request.method=='POST' and urlsplit(response.url).path=='/api/demo/session') as started:
                    page.goto(app+'/product-demo/?locale=en',wait_until='domcontentloaded')
                assert started.value.ok
                visit = started.value.json(); token = visit['session']['access_token']; channel = visit['channel_id']
                expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
                # A first sample visit has no personal receipt. Read through
                # the real timeline, then return through actual channel controls.
                page.locator('.fc-chat-timeline').hover(); page.mouse.wheel(0,4000)
                deadline = time.monotonic()+15
                while True:
                    receipts = p.request(base,'GET','/read-receipts/channels/'+channel,token)
                    if any(row['user_id']==visit['session']['user_id'] for row in receipts): break
                    assert time.monotonic()<deadline,'Actual sample reading did not establish its receipt'
                    page.wait_for_timeout(200)
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
                browser.close(); stage = 'passed'
        except Exception:
            for name in ['api.log','vite.log']:
                if (private/name).is_file(): shutil.copyfile(private/name,REPORT.with_name(REPORT.stem+'.'+name))
            raise
        finally:
            p.stop(processes)
            for stream in streams: stream.close()
            REPORT.write_text(json.dumps({'stage':stage,'checks':checks,'source_sha256':source_hashes,
                'limits':['Actual public-demo native App/API with temporary sample credentials and owned loopback services only.',
                          'No retained real account, external provider, public deployment or encrypted-room acceptance is claimed.'],
                'cleanup':'Private API/Vite processes, SQLite/keys/source snapshots and browser context removed.'},indent=2)+'\n')
    print('PASS: '+str(len(checks))+' actual sample-session return-summary groups',flush=True)


if __name__=='__main__': main()
