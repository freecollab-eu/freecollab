#!/usr/bin/env python3
"""Actual product sample shell → native meeting → automatic quota-bounded capture.

Own temporary copies, API/DB and browser devices only. No product/API mocks,
provider accounts, retained installation, or physical media acceptance claims.
"""
import base64,hashlib,importlib.util,json,os,secrets,shutil,signal,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright,expect
from ui_navigation import activate_chat_feature,close_work_card
ROOT=Path(__file__).resolve().parents[2]
REPORT=Path(os.getenv('MEETING_CAPTURE_PROOF_REPORT',str(ROOT.parent/'.freecollab/proof-results/gap-completion-20261025/media-native/sample-browser.json')))
spec=importlib.util.spec_from_file_location('sample_proof_helpers',Path(__file__).with_name('product-demo-browser.py'))
helpers=importlib.util.module_from_spec(spec);spec.loader.exec_module(helpers)
helpers.SOURCES=[*helpers.SOURCES,'frontend/tests/meeting-capture-browser.py']

def main():
    processes=[];streams=[];stage='isolated actual product sample';checks={};requests=[];errors=[];clip_lists=[]
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='freecollab-capture-sample-') as directory:
        private=Path(directory)
        try:
            api_port,web_port=helpers.port(),helpers.port();base=f'http://127.0.0.1:{api_port}';origin=f'http://127.0.0.1:{web_port}'
            frontend,backend,data=private/'frontend',private/'backend',private/'public-demo';backend.mkdir();data.mkdir()
            shutil.copytree(ROOT/'backend/app',backend/'app',ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(ROOT/'frontend',frontend,ignore=shutil.ignore_patterns('node_modules','dist*','.vite*','.git','__pycache__'))
            (frontend/'node_modules').symlink_to(ROOT/'frontend/node_modules',target_is_directory=True)
            hashes=helpers.runtime_hashes(private)
            env={**os.environ,'ENVIRONMENT':'test','DEMO_INSTANCE':'true','DEMO_DATA_PATH':str(data),'DATABASE_URL':'sqlite:///'+str(data/'public-demo.sqlite'),
              'STORAGE_PATH':str(data/'files'),'JWT_SECRET':secrets.token_urlsafe(48),'ACCOUNT_SECURITY_KEY':base64.b64encode(secrets.token_bytes(32)).decode(),
              'MESSAGE_ENCRYPTION_KEYS':json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),'MESSAGE_ENCRYPTION_ACTIVE_KEY_ID':'proof',
              'MESSAGE_ENCRYPTION_KEY_FILE':str(data/'message-keys.json'),'ACCOUNT_SECURITY_KEY_FILE':str(data/'account-security.key'),'STORAGE_BACKEND':'local',
              'MAIL_DELIVERY_MODE':'disabled','DATA_CONTROLS_ENABLED':'false','PUBLIC_APP_URL':origin,'CORS_ORIGINS':json.dumps([origin]),'REDIS_URL':'',
              'MEETINGS_SFU_PROVIDER':'disabled','MEETINGS_ICE_SERVERS':'[]','MEETINGS_TURN_URLS':'[]','MEETINGS_TURN_SECRET':'',
              'VITE_API_URL':'/api','PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(backend)}
            for flag in ['MATRIX_ENABLED','CONNECT_FEDERATION_ENABLED','OIDC_ENABLED','WEBHOOKS_ENABLED','SYNC_ENABLED','OTRUST_ENABLED','CLOUD_FILES_ENABLED','EXTERNAL_MEETINGS_ENABLED','COPILOT_ENABLED','PUSH_ENABLED','NATIVE_PUSH_ENABLED','NATIVE_CALLS_ENABLED','ENABLE_EXPERIMENTAL_FEATURES','ENTERPRISE_SEARCH_ENABLED']:
                env[flag]='false'
            env['NO_PROXY']=','.join(filter(None,[env.get('NO_PROXY',''),'127.0.0.1','localhost','::1']))
            python=os.getenv('BACKEND_PYTHON')or(str(ROOT/'backend/.venv/bin/python')if(ROOT/'backend/.venv/bin/python').exists()else sys.executable)
            if os.sep in python:python=os.path.abspath(python)
            api=private/'api.py';api.write_text(f'import uvicorn\nfrom app.main import socket_app\nuvicorn.run(socket_app,host="127.0.0.1",port={api_port},proxy_headers=False,access_log=False)\n')
            vite=private/'vite.mjs';vite.write_text('import {createServer} from '+json.dumps(str(ROOT/'frontend/node_modules/vite/dist/node/index.js'))+';const s=await createServer({configFile:'+json.dumps(str(frontend/'vite.demo.config.ts'))+',cacheDir:'+json.dumps(str(private/'vite-cache'))+',server:{host:"127.0.0.1",port:'+str(web_port)+',strictPort:true,hmr:false,watch:{ignored:["**"]},fs:{allow:'+json.dumps([str(private),str(ROOT/'frontend')])+'},proxy:{"/api":{target:'+json.dumps(base)+',changeOrigin:true,rewrite:p=>p.replace(/^\\/api/,"")},"/socket.io":{target:'+json.dumps(base)+',ws:true}}}});await s.listen();')
            for command,name,cwd in [([python,str(api)],'api.log',backend),(['node',str(vite)],'vite.log',frontend)]:
                stream=(private/name).open('w');streams.append(stream);processes.append(subprocess.Popen(command,cwd=cwd,env=env,stdout=stream,stderr=stream,start_new_session=True))
            helpers.ready(base+'/ready',processes);helpers.ready(origin+'/product-demo/',processes)
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH')or shutil.which('chromium'),headless=True,args=['--no-sandbox','--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream','--autoplay-policy=no-user-gesture-required'])
                context=browser.new_context(viewport={'width':1440,'height':1100},permissions=['microphone','camera']);page=context.new_page();page.set_default_timeout(45000)
                context.add_init_script(helpers.INSTRUMENTATION)
                page.on('pageerror',lambda error:errors.append(str(error)))
                def observed(response):
                    path=urlsplit(response.url).path
                    if path.startswith('/api/'):
                        if response.request.method=='GET' and path.endswith('/clips'):clip_lists.append(response)
                        headers=response.request.headers;requests.append({'path':path,'method':response.request.method,'status':response.status,'member_authorization':bool(headers.get('authorization')),'meeting_capability':bool(headers.get('x-meeting-token'))})
                page.on('response',observed)
                context.route('**/*',lambda route:route.continue_() if urlsplit(route.request.url).scheme in {'blob','data'} or urlsplit(route.request.url).hostname=='127.0.0.1' and urlsplit(route.request.url).port==web_port else route.abort())
                stage='real sample visit and product shell'
                with page.expect_response(lambda r:r.request.method=='POST' and urlsplit(r.url).path=='/api/demo/session') as bootstrap:page.goto(origin+'/product-demo/?locale=en',wait_until='domcontentloaded')
                assert bootstrap.value.ok;visit=bootstrap.value.json();expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
                checks['actual_public_sample_session_and_real_product_Chat']=True
                stage='actual feature launcher and member native meeting'
                activate_chat_feature(page,'meetings');panel=page.get_by_role('region',name='Channel meetings',exact=True);expect(panel).to_be_visible()
                panel.get_by_label('Start with camera',exact=True).uncheck()
                panel.get_by_label('Ask participants to record automatically after consent',exact=True).check()
                panel.get_by_role('button',name='Start channel meeting',exact=True).click()
                dock=page.get_by_role('complementary',name='Active channel meeting',exact=True);expect(dock).to_be_visible()
                dock.locator('.fc-recording-dot').wait_for(timeout=60000)
                expect(dock.get_by_text('Sample recording parts are at most 15 seconds with a 1.5 MiB target. The sample allows 2 MiB per upload and 8 MiB for the session; over-limit parts stay private for download or discard.',exact=True)).to_be_visible()
                polls=[row for row in requests if '/meeting-access/'in row['path'] and row['path'].endswith('/poll')]
                assert polls and all(row['status']==200 and row['member_authorization'] and row['meeting_capability'] for row in polls),polls
                checks['actual_member_capability_poll_uses_original_sample_Bearer_and_explicit_recording_opt_in']=True
                stage='real automatic 15 second part and durable sample file/clip'
                deadline=time.monotonic()+75
                while time.monotonic()<deadline:
                    saved=[row for row in requests if row['method']=='POST' and row['path'].endswith('/clips') and row['status']==201]
                    if saved:break
                    page.wait_for_timeout(300)
                assert saved,'The automatic short recording part did not publish through the real sample API'
                cards=panel.locator('.fc-clip-card').filter(has_text='Meeting recording');cards.first.wait_for(timeout=60000)
                dock.get_by_role('button',name='Minimize meeting',exact=True).click()
                cards.first.get_by_role('button',name='Play recording',exact=True).click()
                audio=cards.first.locator('audio');expect(audio).to_be_visible();audio.evaluate('node=>node.play()');page.wait_for_function('()=>document.querySelector(".fc-clip-card audio")?.currentTime>0.1')
                assert clip_lists,'The real clip collection was never reloaded';clips=clip_lists[-1].json()
                recorded=[row for row in clips if row.get('fields',{}).get('title','').startswith('Meeting recording')]
                assert recorded and recorded[0]['file']['size']<2*1024*1024
                checks['actual_MediaRecorder_automatic_quota_bounded_part_native_file_commit_clip_and_audio_decode']=True
                stage='persistent call while tools close and real leave capability'
                published_before_leave=len([row for row in requests if row['method']=='POST'and row['path'].endswith('/clips')and row['status']==201])
                close_work_card(page);expect(dock).to_be_visible();dock.get_by_role('button',name='End meeting for everyone',exact=True).click();expect(dock).to_have_count(0)
                page.wait_for_timeout(500)
                assert any(row['path'].endswith('/leave') and row['status']==200 and row['member_authorization'] for row in requests)
                published_after_leave=[index for index,row in enumerate(requests)if row['method']=='POST'and row['path'].endswith('/clips')and row['status']==201]
                leave_index=next(index for index,row in enumerate(requests)if row['path'].endswith('/leave')and row['status']==200)
                assert len(published_after_leave)>published_before_leave and published_after_leave[-1]<leave_index,'Immediate leave did not wait for the actual final recording publication'
                checks['actual_immediate_leave_waits_for_final_part_file_and_clip_before_ending_native_call']=True
                assert not any(row['status']in {401,429,500}for row in requests),[row for row in requests if row['status']in{401,429,500}]
                stage='actual failed retention preserves available bytes and prevents a false successful leave'
                activate_chat_feature(page,'meetings');panel=page.get_by_role('region',name='Channel meetings',exact=True)
                panel.get_by_label('Start with camera',exact=True).uncheck();panel.get_by_label('Ask participants to record automatically after consent',exact=True).check()
                panel.get_by_role('button',name='Start channel meeting',exact=True).click();dock=page.get_by_role('complementary',name='Active channel meeting',exact=True)
                expect(dock).to_be_visible()
                if dock.get_by_role('button',name='Expand meeting',exact=True).count():dock.get_by_role('button',name='Expand meeting',exact=True).click()
                dock.locator('.fc-recording-dot').wait_for(timeout=60000)
                page.wait_for_timeout(1500)
                # Fault only the first actual audio Blob retention read. Media,
                # REST, encoding and subsequent download remain genuine.
                page.evaluate("""()=>{const original=FileReader.prototype.readAsDataURL;FileReader.prototype.readAsDataURL=function(blob){if(blob.type.startsWith('audio/')){FileReader.prototype.readAsDataURL=original;throw new DOMException('Owned retention failure fixture','NotReadableError')}return original.call(this,blob)}}""")
                dock.get_by_role('button',name='End meeting for everyone',exact=True).click()
                download_button=dock.get_by_role('button',name='Download unsaved recording',exact=True);expect(download_button).to_be_visible(timeout=60000);expect(dock).to_be_visible()
                expect(dock.get_by_text('Recording storage failed. Download or discard the unsaved recording before leaving.',exact=True)).to_be_visible()
                assert page.get_by_role('complementary',name='Recording publication pending',exact=True).count()==0,'Failed retention invented a stored private draft'
                with page.expect_download() as download:download_button.click()
                available=private/'available-unsaved.webm';download.value.save_as(available);assert available.stat().st_size>100
                dock.get_by_role('button',name='Discard unsaved recording',exact=True).click();dock.get_by_role('button',name='End meeting for everyone',exact=True).click();expect(dock).to_have_count(0)
                checks['actual_retention_fault_blocks_false_leave_preserves_available_WebM_download_and_explicit_discard']=True
                stage='actual held upload leaves an isolated private draft notice that clears after the last discard'
                panel.get_by_role('button',name='Start channel meeting',exact=True).click();dock=page.get_by_role('complementary',name='Active channel meeting',exact=True);expect(dock).to_be_visible()
                if dock.get_by_role('button',name='Expand meeting',exact=True).count():dock.get_by_role('button',name='Expand meeting',exact=True).click()
                dock.locator('.fc-recording-dot').wait_for(timeout=60000);page.wait_for_timeout(1200)
                held=[]
                def hold_file(route):held.append(route)
                pattern='**/api/files/channels/*';page.route(pattern,hold_file)
                dock.get_by_role('button',name='End meeting for everyone',exact=True).click();expect(dock).to_have_count(0,timeout=45000)
                notice=page.get_by_role('complementary',name='Recording publication pending',exact=True);expect(notice).to_be_visible();expect(notice.get_by_role('region',name='Private recording drafts',exact=True)).to_be_visible()
                assert held,'The actual final upload was not held at the intended network boundary'
                for route in held:
                    try:route.abort()
                    except Exception:pass
                page.unroute(pattern,hold_file)
                notice.get_by_role('button',name='Discard private draft',exact=True).click();expect(notice).to_have_count(0)
                checks['actual_bounded_pending_leave_keeps_memory_isolated_recovery_and_last_discard_clears_notice']=True
                helpers.storage_untouched(page)
                assert not errors,errors
                checks['actual_persistent_meeting_dock_and_authenticated_leave_without_retained_account_storage']=True
                page.screenshot(path=str(REPORT.with_name('sample-after-capture.png')),full_page=True)
                browser.close()
            assert hashes==helpers.runtime_hashes(private),'Owned source snapshot changed during sample capture'
            REPORT.write_text(json.dumps({'checks':checks,'requests':requests,'runtime_source_sha256':hashes,'runtime_source_unchanged':True,'scope':'Actual full public sample product Chat, real API/database/native mesh capability, explicit host opt-in, actual browser MediaRecorder automatic short part, file/clip and audio playback. One admitted synthetic device; cross-installation two-device WebRTC/capture is a separate proof. No physical microphone/OS signing or real ASR in this run.'},indent=2)+'\n')
        except Exception:
            print('FAILED sample capture stage: '+stage,file=sys.stderr)
            for name in ['api.log','vite.log']:
                path=private/name
                if path.exists():print(name+'\n'+path.read_text()[-3500:],file=sys.stderr)
            raise
        finally:
            helpers.stop(processes)
            for stream in streams:stream.close()

if __name__=='__main__':main()
