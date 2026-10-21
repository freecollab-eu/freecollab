#!/usr/bin/env python3
"""Private native API + real Chromium proof of voluntary local feedback.

All users, reports, membership changes and keys are disposable fixtures. No real
community responses, invitations, passive telemetry or provider calls are made.
"""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('feedback_proof_helpers', Path(__file__).with_name('connection-setup-browser.py'))
helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(helpers)
REPORT = ROOT.parent / '.freecollab/proof-results/workspace-feedback-browser.json'
PASSWORD = 'Private-feedback!2026'
SOURCES = ['frontend/src/components/PilotFeedbackPanel.tsx', 'frontend/src/components/WorkspaceFeedbackDialog.tsx', 'frontend/src/lib/pilot-feedback-api.ts', 'frontend/src/styles/pilot-feedback.css', 'frontend/src/i18n/workspace-feedback.sv.ts', 'frontend/tests/pilot-feedback.test.mjs', 'frontend/tests/workspace-feedback-browser.py', 'backend/app/api/pilot_feedback.py', 'backend/app/services/pilot_feedback.py', 'backend/app/models/pilot_feedback.py']


def frontend(private, base, port):
    web = private/'web'; web.mkdir()
    (web/'index.html').write_text('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/entry.tsx"></script></body></html>')
    (web/'base.css').write_text((ROOT/'frontend/src/index.css').read_text().split('\n', 2)[2])
    source = '''import React,{useState}from'react';import{createRoot}from'react-dom/client';
import{WorkspaceFeedbackDialog}from DIALOG;import{LocaleProvider,LanguagePicker}from I18N;import{api}from API;import'./base.css';
window.feedbackApi=api;function Proof(){const[open,setOpen]=useState(false);return <><LanguagePicker/><button id="open-feedback" onClick={()=>setOpen(true)}>Open workspace feedback</button>{open&&<WorkspaceFeedbackDialog workspaceId={window.feedbackWorkspace} onClose={()=>setOpen(false)} onOpenConnections={()=>{window.openedConnections=(window.openedConnections||0)+1}}/>}</>}
createRoot(document.getElementById('root')).render(<React.StrictMode><LocaleProvider><Proof/></LocaleProvider></React.StrictMode>);'''
    for marker, path in {'DIALOG':'frontend/src/components/WorkspaceFeedbackDialog.tsx','I18N':'frontend/src/i18n/index.tsx','API':'frontend/src/lib/api.ts'}.items(): source=source.replace(marker,json.dumps(str(ROOT/path)))
    (web/'entry.tsx').write_text(source)
    launcher=private/'vite.mjs'
    launcher.write_text('import{createServer}from '+json.dumps(str(ROOT/'frontend/node_modules/vite/dist/node/index.js'))+';import react from '+json.dumps(str(ROOT/'frontend/node_modules/@vitejs/plugin-react/dist/index.js'))+';const server=await createServer({configFile:false,root:'+json.dumps(str(web))+',plugins:[react()],cacheDir:'+json.dumps(str(private/'vite-cache'))+',resolve:{alias:{react:'+json.dumps(str(ROOT/'frontend/node_modules/react'))+',"react-dom":'+json.dumps(str(ROOT/'frontend/node_modules/react-dom'))+',"lucide-react":'+json.dumps(str(ROOT/'frontend/node_modules/lucide-react'))+'}},server:{host:"127.0.0.1",port:'+str(port)+',strictPort:true,hmr:false,watch:{ignored:["**"]},fs:{allow:['+json.dumps(str(ROOT/'frontend'))+','+json.dumps(str(web))+']},proxy:{"/api":{target:'+json.dumps(base)+',changeOrigin:true,rewrite:p=>p.replace(/^\\/api/,"")}}}});await server.listen();')
    return launcher


def main():
    checks, processes, streams, stage = helpers.ProofChecks(), [], [], 'private startup'
    with tempfile.TemporaryDirectory(prefix='freecollab-workspace-feedback-') as directory:
        private=Path(directory)
        try:
            api_port, app_port=helpers.port(),helpers.port(); base=f'http://127.0.0.1:{api_port}'; app=f'http://127.0.0.1:{app_port}'
            env={**os.environ,'ENVIRONMENT':'test','DATABASE_URL':'sqlite:///'+str(private/'proof.db'),'STORAGE_PATH':str(private/'files'),'JWT_SECRET':secrets.token_urlsafe(48),'ACCOUNT_SECURITY_KEY':base64.b64encode(secrets.token_bytes(32)).decode(),'MESSAGE_ENCRYPTION_KEYS':json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),'MESSAGE_ENCRYPTION_ACTIVE_KEY_ID':'proof','MATRIX_ENABLED':'false','MAIL_DELIVERY_MODE':'disabled','OIDC_ENABLED':'false','WEBHOOKS_ENABLED':'false','OTRUST_ENABLED':'false','SYNC_ENABLED':'false','COPILOT_ENABLED':'false','EXTERNAL_MEETINGS_ENABLED':'false','PUBLIC_APP_URL':app,'CORS_ORIGINS':json.dumps([app]),'PYTHONPATH':str(ROOT/'backend'),'PROOF_DIRECTORY':str(private)}
            env['NO_PROXY']=','.join(filter(None,[env.get('NO_PROXY',''),'127.0.0.1','localhost','::1']))
            launcher=private/'api.py'; launcher.write_text('''import asyncio,json,os
from pathlib import Path
from fastapi.responses import JSONResponse
from app.main import app,socket_app
from app.api import pilot_feedback
if not any(getattr(route,'path','').endswith('/pilot-feedback')for route in app.routes):app.include_router(pilot_feedback.router)
private=Path(os.environ['PROOF_DIRECTORY'])
@app.middleware('http')
async def proof_controls(request,call_next):
    control=json.loads((private/'controls.json').read_text())if(private/'controls.json').exists()else{}
    if request.url.path.endswith('/pilot-feedback/summary'):
        if control.get('summary_failure'):return JSONResponse({'detail':'Private summary fixture unavailable'},status_code=503)
        if control.get('downgrade'):
            from sqlmodel import Session
            from app.database import engine
            from app.models.workspace import WorkspaceMember
            with Session(engine)as session:
                row=session.get(WorkspaceMember,(control['workspace'],control['owner']));row.role='member';session.add(row);session.commit()
    if '/pilot-feedback'in request.url.path and request.method=='PUT'and control.get('hold'):
        (private/'held').write_text('held')
        for _ in range(1000):
            if(private/'release').exists():break
            await asyncio.sleep(.02)
    return await call_next(request)
import uvicorn
uvicorn.run(socket_app,host='127.0.0.1',port='''+str(api_port)+''',proxy_headers=False,access_log=False)
''')
            python=str(ROOT/'backend/.venv/bin/python')
            for command,name,cwd in [([python,str(launcher)],'api.log',ROOT/'backend'),(['node',str(frontend(private,base,app_port))],'vite.log',ROOT/'frontend')]:
                stream=(private/name).open('w');streams.append(stream);processes.append(subprocess.Popen(command,cwd=cwd,env=env,stdout=stream,stderr=stream,start_new_session=True))
            helpers.ready(base+'/ready',processes);helpers.ready(app,processes)
            owner_email='feedback-owner-'+secrets.token_hex(4)+'@example.com'
            owner=helpers.api(base,'POST','/auth/register',data={'name':'Private owner','email':owner_email,'password':PASSWORD})
            member=helpers.api(base,'POST','/auth/register',data={'name':'Private member','email':'feedback-member-'+secrets.token_hex(4)+'@example.com','password':PASSWORD})
            outsider=helpers.api(base,'POST','/auth/register',data={'name':'Private outsider','email':'feedback-outsider-'+secrets.token_hex(4)+'@example.com','password':PASSWORD})
            workspace=helpers.api(base,'POST','/workspaces',owner,{'name':'Private feedback workspace'});wid=workspace['id']; path='/workspaces/'+wid+'/pilot-feedback'
            seed='''from app.main import app
from sqlmodel import Session
from app.database import engine
from app.models.workspace import WorkspaceMember
with Session(engine)as session:session.add(WorkspaceMember(workspace_id='''+repr(wid)+''',user_id='''+repr(member['user_id'])+''',role='member'));session.commit()
'''
            result=subprocess.run([python,'-c',seed],cwd=ROOT/'backend',env=env,capture_output=True,text=True,timeout=45);assert result.returncode==0,'Private membership fixture failed'
            helpers.reject(base,'GET',path,outsider);helpers.reject(base,'GET',path+'/summary',member)
            helpers.api(base,'PUT',path+'/file_share',member,{'expected_revision':0,'outcome':'blocked','rating':2,'note':'MEMBER-PRIVATE-NOTE','consent':True})
            assert 'MEMBER-PRIVATE-NOTE'not in json.dumps(helpers.api(base,'GET',path,owner))
            assert 'MEMBER-PRIVATE-NOTE'not in json.dumps(helpers.api(base,'GET',path+'/summary',owner))
            checks['native_member_private_notes_outsider_denial_admin_aggregate_only']=True
            with sync_playwright()as playwright:
                browser=playwright.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_PATH')or shutil.which('chromium'),args=['--no-sandbox'])
                context=browser.new_context(viewport={'width':1280,'height':1000},locale='en-US',reduced_motion='reduce')
                context.add_init_script('localStorage.setItem("freecollab_token",'+json.dumps(owner['access_token'])+');localStorage.setItem("freecollab_user_id",'+json.dumps(owner['user_id'])+');window.feedbackWorkspace='+json.dumps(wid)+';')
                unexpected=[]
                def local_only(route):
                    if urlsplit(route.request.url).hostname not in ['127.0.0.1','localhost']:unexpected.append(route.request.url);route.abort()
                    else:route.continue_()
                context.route('**/*',local_only);page=context.new_page();page.set_default_timeout(30000);errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(app,wait_until='networkidle');page.locator('#open-feedback').click();dialog=page.get_by_role('dialog',name='Workspace feedback',exact=True)
                expect(dialog.get_by_text('Not started',exact=True)).to_be_visible();expect(dialog.get_by_text('MEMBER-PRIVATE-NOTE')).to_have_count(0)
                form=dialog.get_by_role('form',name='Feedback for Send a first message',exact=True)
                note=form.get_by_label('Your private note (optional)',exact=False)
                note.fill('LOCAL-UNSAVED-NOTE');expect(form.get_by_role('button',name='Save my task feedback',exact=True)).to_be_disabled()
                assert helpers.api(base,'GET',path,owner)['feedback']==[]
                page.once('dialog',lambda prompt:prompt.dismiss());dialog.get_by_role('button',name='Close workspace feedback',exact=True).click();expect(note).to_have_value('LOCAL-UNSAVED-NOTE')
                page.once('dialog',lambda prompt:prompt.dismiss());page.keyboard.press('Escape');expect(dialog).to_be_visible()
                checks['no_passive_write_and_cancelled_dirty_close_preserves_draft']=True
                form.get_by_role('radio',name='Completed',exact=True).check();form.get_by_label('Ease of this task (optional)',exact=True).select_option('5');form.get_by_role('checkbox').check()
                helpers.controls(private,hold=True);form.get_by_role('button',name='Save my task feedback',exact=True).click();helpers.wait_file(page,private/'held')
                expect(dialog.get_by_role('button',name='Close workspace feedback',exact=True)).to_be_disabled();page.keyboard.press('Escape');page.locator('.fc-account-overlay').dispatch_event('mousedown');expect(dialog).to_be_visible();(private/'release').write_text('release')
                expect(dialog.get_by_text('Your task feedback was saved.',exact=True)).to_be_visible();helpers.controls(private)
                saved=helpers.api(base,'GET',path,owner);assert saved['feedback'][0]['revision']==1 and saved['feedback'][0]['note']=='LOCAL-UNSAVED-NOTE'
                aggregate=helpers.api(base,'GET',path+'/summary',owner);assert aggregate['distinct_participants']==2 and aggregate['feedback_count']==2
                checks['explicit_save_real_native_persistence_and_busy_close_guard']=True
                stage='optimistic conflict and tombstone reuse'
                helpers.api(base,'PUT',path+'/first_message',owner,{'expected_revision':1,'outcome':'blocked','rating':3,'note':'OTHER-WINDOW','consent':True})
                note.fill('LOCAL-CONFLICT-DRAFT');form.get_by_role('checkbox').check();form.get_by_role('button',name='Save my task feedback',exact=True).click()
                expect(dialog.get_by_role('alert')).to_contain_text('changed in another window');expect(note).to_have_value('LOCAL-CONFLICT-DRAFT');expect(form.get_by_role('button',name='Save my task feedback',exact=True)).to_be_disabled()
                page.once('dialog',lambda prompt:prompt.accept());dialog.get_by_role('button',name='Reload workspace feedback',exact=True).click();expect(note).to_have_value('OTHER-WINDOW')
                form.get_by_role('button',name='Delete my task feedback',exact=True).click();form.get_by_role('button',name='Confirm deletion',exact=True).click();expect(dialog.get_by_text('Your feedback for this task was deleted.',exact=True)).to_be_visible()
                deleted=helpers.api(base,'GET',path,owner);assert deleted['feedback']==[]and deleted['revisions']['first_message']==3
                form.get_by_role('radio',name='Completed',exact=True).check();form.get_by_role('checkbox').check();form.get_by_role('button',name='Save my task feedback',exact=True).click();expect(dialog.get_by_text('Your task feedback was saved.',exact=True)).to_be_visible()
                assert helpers.api(base,'GET',path,owner)['revisions']['first_message']==4
                checks['conflict_keeps_draft_requires_reload_and_delete_tombstone_reused']=True
                stage='summary unavailable and permission downgrade'
                helpers.controls(private,summary_failure=True);dialog.get_by_role('button',name='Reload workspace feedback',exact=True).click();expect(dialog.get_by_text('Summary unavailable',exact=True)).to_be_visible();expect(dialog.get_by_text('No reports yet',exact=True)).to_have_count(0);expect(form).to_be_visible()
                checks['failed_summary_is_unknown_never_false_zero']=True
                helpers.controls(private,downgrade=True,workspace=wid,owner=owner['user_id']);dialog.get_by_role('button',name='Reload workspace feedback',exact=True).click();expect(dialog.get_by_role('region',name='Workspace feedback summary',exact=True)).to_have_count(0);expect(form).to_be_visible();helpers.controls(private)
                checks['real_permission_downgrade_clears_aggregate_keeps_own_feedback']=True
                stage='Swedish 320px and session invalidation'
                dialog.get_by_role('button',name='Close workspace feedback',exact=True).click();page.get_by_role('combobox',name='Language',exact=True).select_option('sv');page.locator('#open-feedback').click();dialog=page.get_by_role('dialog',name='Återkoppling i arbetsytan',exact=True)
                expect(dialog.get_by_role('heading',name='Berätta vad som fungerade.',exact=True)).to_be_visible();page.set_viewport_size({'width':320,'height':900})
                assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
                REPORT.parent.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(REPORT.parent/'workspace-feedback-swedish-320.png'),full_page=True)
                checks['swedish_optional_feedback_320px_layout']=True
                note=dialog.get_by_label('Din privata anteckning (valfritt)',exact=False);note.fill('CLEAR-ON-LOGOUT')
                # Hold the real mutation before authority checks, then revoke the real native session.
                (private/'held').unlink(missing_ok=True);(private/'release').unlink(missing_ok=True);helpers.controls(private,hold=True)
                dialog.get_by_role('checkbox').check();dialog.get_by_role('button',name='Spara min återkoppling',exact=True).click();helpers.wait_file(page,private/'held')
                page.evaluate('window.feedbackApi.logoutSession()');(private/'release').write_text('release');helpers.controls(private)
                # Logout intentionally resets the account-owned locale to the browser default.
                dialog=page.get_by_role('dialog',name='Workspace feedback',exact=True)
                expect(dialog.get_by_role('alert')).to_contain_text('Your access changed');expect(dialog.get_by_role('textbox')).to_have_count(0)
                fresh=helpers.api(base,'POST','/auth/login',data={'email':owner_email,'password':PASSWORD})
                assert helpers.api(base,'GET',path,fresh)['revisions']['first_message']==4
                checks['auth_invalidation_removes_private_draft_and_pending_context']=True
                assert not errors,'Browser JavaScript errors';assert not unexpected,'Nonlocal request attempted';checks['no_javascript_errors_or_external_requests']=True
                browser.close()
            REPORT.write_text(json.dumps({'scope':'Disposable private API/browser fixtures only. Optional local feedback; no real community participation or product verdict.','checks':checks,'public_requests':0,'source_sha256':{path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest()for path in SOURCES},'cleanup':'All private accounts, database, keys and processes removed.'},indent=2)+'\n')
            print('Workspace feedback private browser proof passed: '+str(len(checks))+' checks.')
        except Exception:
            print('Failed private feedback stage: '+stage,file=sys.stderr)
            for name in ['api.log','vite.log']:
                path=private/name
                if path.exists():print(path.read_text()[-5000:],file=sys.stderr)
            raise
        finally:
            helpers.stop(processes)
            for stream in streams:stream.close()
if __name__=='__main__':main()
