#!/usr/bin/env python3
"""Actual product/API native opt-in and call handoff, with synthetic OS/provider.

No live Firebase/Apple credential, signed device or background delivery is claimed.
"""
from ui_navigation import open_chat_tool
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('native_push_browser_fixture',Path(__file__).with_name('customer-sso-browser.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
PYTHON=os.getenv('BACKEND_PYTHON') or (str(ROOT/'backend/.venv/bin/python') if (ROOT/'backend/.venv/bin/python').is_file() else sys.executable)
REPORT=ROOT.parent/'.freecollab/proof-results/native-push-browser.json'
TOKEN='synthetic_android_registration_'+'x'*80


def main():
    processes,streams,checks=[],[],[]
    stage='startup'
    source_paths=['frontend/src/components/NativePushSettings.tsx','frontend/src/lib/native-push.ts','backend/app/api/native_push.py','backend/app/services/native_push.py']
    source_hashes={path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in source_paths}
    with tempfile.TemporaryDirectory(prefix='freecollab-native-push-ui-') as directory:
        private=Path(directory);deliveries=private/'vendor-deliveries.jsonl'
        try:
            api_port,app_port=p.port(),p.port();base,app=f'http://127.0.0.1:{api_port}',f'http://127.0.0.1:{app_port}'
            database=private/'proof.db'
            key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            account={'type':'service_account','project_id':'customer-project','client_email':'sender@customer-project.iam.gserviceaccount.com',
                     'private_key':key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()}
            environment=dict(os.environ,ENVIRONMENT='test',DATABASE_URL='sqlite:///'+str(database),STORAGE_PATH=str(private/'files'),
                JWT_SECRET=secrets.token_urlsafe(48),ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),ACCOUNT_SECURITY_KEY_FILE=str(private/'account.key'),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'push-proof':base64.b64encode(secrets.token_bytes(32)).decode()}),MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='push-proof',MESSAGE_ENCRYPTION_KEY_FILE=str(private/'message-keys.json'),
                PUBLIC_APP_URL=app,CORS_ORIGINS=json.dumps([app]),MATRIX_ENABLED='false',OIDC_ENABLED='false',MAIL_DELIVERY_MODE='disabled',OTRUST_ENABLED='false',WEBHOOKS_ENABLED='false',SYNC_ENABLED='false',
                CLOUD_FILES_ENABLED='false',EXTERNAL_MEETINGS_ENABLED='false',COPILOT_ENABLED='false',PUSH_ENABLED='false',NATIVE_PUSH_ENABLED='true',NATIVE_PUSH_FCM_PROJECT_ID='customer-project',NATIVE_PUSH_FCM_SERVICE_ACCOUNT=json.dumps(account),PYTHONPATH=str(ROOT/'backend'))
            launcher=private/'api.py'
            launcher.write_text('''from pathlib import Path
import json
from sqlmodel import Session
from app.main import app, socket_app
from app.database import engine
from app.services import native_push
import uvicorn
def delivered(platform, token, payload, **options):
    with Path('''+repr(str(deliveries))+''').open('a') as stream: stream.write(json.dumps(payload)+'\\n')
native_push.dispatch=delivered
@app.post('/__proof/native-worker')
def worker():
    with Session(engine) as session: return {'delivered':native_push.run_pending(session)}
uvicorn.run(socket_app,host='127.0.0.1',port='''+str(api_port)+''',proxy_headers=False,access_log=False)
''')
            vite=p.create_vite(private,base,app_port)
            for command,filename,cwd in [([PYTHON,str(launcher)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',ROOT/'frontend')]:
                stream=(private/filename).open('w');streams.append(stream)
                processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            p.ready(base+'/ready',processes);p.ready(app+'/',processes)
            owner=p.native_user(base,database,'Native Push Owner');host=p.native_user(base,database,'Native Call Host')
            workspace=p.api(base,'POST','/workspaces',owner,{'name':'Call studio'})
            invitation=p.api(base,'POST','/workspaces/'+workspace['id']+'/invites',owner,{'role':'member'})
            p.api(base,'POST','/workspaces/invites/accept',host,{'token':invitation['token']})
            channel=p.api(base,'POST','/channels',owner,{'name':'Quiet calls','member_ids':[host['user_id']],'workspace_id':workspace['id']})
            verifier=secrets.token_urlsafe(32)
            challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            grant=p.api(base,'POST','/native-clients/connect',owner,{'code_challenge':challenge,'redirect_uri':'freecollab://native/complete','client_kind':'mobile'})
            child=p.api(base,'POST','/native-clients/exchange',data={'code':grant['code'],'code_verifier':verifier,'redirect_uri':'freecollab://native/complete'})
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or '/usr/bin/chromium',headless=True,args=['--no-sandbox'])
                context=browser.new_context(viewport={'width':1280,'height':900},reduced_motion='reduce')
                context.add_init_script('''window.__nativePending=[];window.__nativeRegistrations=0;window.__nativeMedia=0;
const push={registerPush:async()=>{window.__nativeRegistrations++;return {platform:'android',token:'''+json.dumps(TOKEN)+'''}},refreshPush:async()=>({platform:'android',token:'''+json.dumps(TOKEN)+'''}),unregisterPush:async()=>{window.__nativePending=[]},getPendingPush:async()=>({items:window.__nativePending}),clearPush:async({key})=>{window.__nativePending=window.__nativePending.filter(item=>item.key!==key)}};
window.Capacitor={getPlatform:()=> 'android',isNativePlatform:()=>true,registerPlugin:name=>name==='FreeCollabPush'?push:{signIn:async()=>{throw new Error('Not used')},cancelSignIn:async()=>{}}};
navigator.mediaDevices.getUserMedia=async()=>{window.__nativeMedia++;throw new Error('No automatic media allowed')};
''')
                context.add_init_script('if(location.origin==='+json.dumps(app)+'){localStorage.setItem("freecollab_token",'+json.dumps(child['access_token'])+');localStorage.setItem("freecollab_user_id",'+json.dumps(child['user_id'])+');localStorage.setItem('+json.dumps('freecollab.workspace:'+child['user_id'])+','+json.dumps(workspace['id'])+');}')
                page=context.new_page();page.set_default_timeout(30000);errors=[];urls=[]
                page.on('pageerror',lambda error:errors.append(str(error)));page.on('request',lambda request:urls.append(request.url))
                page.goto(app,wait_until='domcontentloaded')
                expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
                assert page.evaluate('window.__nativeRegistrations')==0
                checks.append('actual native UI makes no permission/registration request before explicit opt-in')
                stage='device opt-in'
                open_chat_tool(page, 'Mobile and offline settings')
                dialog=page.get_by_role('dialog',name='Mobile and offline',exact=True)
                expect(dialog.get_by_role('button',name='Enable on this mobile device',exact=True)).to_be_enabled()
                dialog.get_by_role('checkbox',name='Also alert me when a channel call starts',exact=True).check()
                dialog.get_by_role('button',name='Enable on this mobile device',exact=True).click()
                expect(dialog.get_by_text('Confirmation pending',exact=False)).to_be_visible()
                assert page.evaluate('window.__nativeRegistrations')==1
                rows=p.api(base,'GET','/mobile/native/devices',child)['items'];assert len(rows)==1 and not rows[0]['active']
                delivery=json.loads(deliveries.read_text().splitlines()[-1]);delivery['key']=delivery['device_id']
                page.evaluate('(item)=>window.__nativePending.push(item)',delivery)
                expect(dialog.get_by_text('Enabled',exact=False)).to_be_visible(timeout=30000)
                rows=p.api(base,'GET','/mobile/native/devices',child)['items'];assert rows[0]['active'] and rows[0]['confirmed'] and rows[0]['calls_enabled']
                checks.append('real native opt-in waits for delivered one-use device proof, then enables current mobile sign-in')
                page.set_viewport_size({'width':320,'height':900})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert dialog.evaluate('(node)=>node.scrollWidth<=node.clientWidth')
                checks.append('native notification settings fit 320px without horizontal overflow')
                dialog.get_by_role('button',name='Close mobile settings',exact=True).click()
                # Dismiss only the confirmation toast before the distinct call alert.
                for button in page.locator('.fc-native-signin').get_by_role('button',name='Close',exact=True).all():button.click()
                stage='actual incoming call'
                meeting=p.api(base,'POST','/channels/'+channel['id']+'/meetings',host,{})['meeting']
                result=p.api(base,'POST','/__proof/native-worker',data={});assert result['delivered']==1
                incoming=json.loads(deliveries.read_text().splitlines()[-1])
                assert incoming['type']=='incoming_call' and set(incoming)=={'type','id'}
                incoming['key']=incoming['id'];page.evaluate('(item)=>window.__nativePending.push(item)',incoming)
                expect(page.get_by_role('button',name='Open call',exact=True)).to_be_visible(timeout=30000)
                assert page.evaluate('window.__nativeMedia')==0
                page.get_by_role('button',name='Open call',exact=True).click()
                work=page.get_by_role('region',name='Work in this conversation',exact=True)
                expect(work).to_be_visible(timeout=30000)
                expect(work).to_have_attribute('data-work-kind','meetings'); expect(work.get_by_role('tablist')).to_have_count(0)
                assert page.evaluate('window.__nativeMedia')==0
                checks.append('generic incoming-call reference resolves current authority and opens real meeting controls without automatic media/admission')
                work.get_by_role('button',name='Close this work',exact=True).click()
                stage='expired action'
                p.api(base,'POST','/meetings/'+meeting['id']+'/end',host,{})
                page.evaluate('(item)=>window.__nativePending.push(item)',incoming)
                page.wait_for_function('window.__nativePending.length===0',timeout=30000)
                expect(page.get_by_role('button',name='Open call',exact=True)).to_have_count(0)
                checks.append('ended call reference is discarded after fresh API resolution and exposes no call UI')
                stage='Swedish settings'
                p.api(base,'PUT','/preferences/language',child,{'locale':'sv'})
                page.reload(wait_until='domcontentloaded')
                page.get_by_role('button',name='Fler kanalverktyg',exact=True).click()
                open_chat_tool(page, 'Inställningar för mobil och offline')
                expect(page.get_by_role('heading',name='Mobilnotiser i appen',exact=True)).to_be_visible()
                checks.append('actual mobile notification UI respects Swedish account language')
                assert not errors,'Unexpected page errors'
                assert all(child['access_token'] not in url and TOKEN not in url for url in urls)
                checks.append('no page errors or native device capability/bearer in request URLs')
                context.close();browser.close()
        except Exception:
            print('Native push browser stage failed: '+stage,file=sys.stderr)
            for stream in streams:stream.flush()
            raise
        finally:
            p.stop(processes)
            for stream in streams:stream.close()
    assert source_hashes=={path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in source_paths}, 'Native push source changed during proof'
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps({'passed':len(checks),'checks':checks,'source_sha256':source_hashes,
        'scope':'Actual copied React product/API with synthetic native bridge and vendor delivery. No real OS/provider background delivery claim.'},indent=2)+'\n')
    print('Native push browser proof passed: '+str(len(checks))+' checks.')

if __name__=='__main__':main()
