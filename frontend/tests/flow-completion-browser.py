#!/usr/bin/env python3
"""Full production App: real native-source routing and private-calendar authoring.

Disposable verified users, actual HTTP handlers, SQLite, real Yjs and uploads.
No API response is fulfilled by Playwright. HeldRead only delays an unchanged GET.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import wave
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from playwright.sync_api import expect, sync_playwright
from ui_navigation import activate_chat_feature, open_chat_tool, capture_work_card, close_work_card, choose_chat_channel

ROOT=Path(__file__).resolve().parents[2]
def module(name,file):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file));value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
p=module('flow_private_helpers','customer-sso-browser.py')
held=module('flow_held_reads','my-day-browser.py')
PYTHON=os.getenv('BACKEND_PYTHON') or str(ROOT.parent/'core/backend/.venv/bin/python')
REPORT=Path(os.getenv('FLOW_COMPLETION_REPORT',str(ROOT.parent/'.freecollab/proof-results/gap-completion-20261025/flow-completion/browser.json')))
SOURCES=['frontend/src/'+name for name in ['App.tsx','components/Chat.tsx','components/NativeLinkController.tsx','lib/native-links.ts','components/WorkHub.tsx','components/MyDay.tsx','components/PersonalCalendar.tsx','components/ActivityFeedPanel.tsx','components/WorkflowsPanel.tsx','components/MeetingsPanel.tsx','components/SharedDocumentOrganization.tsx','lib/useResourceTargetFocus.ts','lib/personal-calendar-api.ts']]
SOURCES+=['frontend/tests/flow-completion-browser.py','backend/app/api/content_completion.py','backend/app/services/content_completion.py']

def upload(base,owner,channel,name,mime,body):
    boundary='private-flow-'+secrets.token_hex(12)
    data=('--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="'+name+'"\r\nContent-Type: '+mime+'\r\n\r\n').encode()+body+('\r\n--'+boundary+'--\r\n').encode()
    req=Request(base+'/files/channels/'+channel,data=data,method='POST',headers={'Authorization':'Bearer '+owner['access_token'],'Content-Type':'multipart/form-data; boundary='+boundary})
    with urlopen(req,timeout=30) as response:return json.load(response)

def private_context_checks(page,base,owner,alternate,workspace,home,channel,fields,checks):
    ws=workspace['id'];event_id='private-context-target';api_path=f'/workspaces/{ws}/personal-calendar'
    p.api(base,'POST',api_path,owner,{'id':event_id,'context_channel_id':home['id'],'fields':{**fields,'title':'Private context appointment'}})
    p.api(base,'POST',f'/channels/{home["id"]}/projects',owner,{'id':'home-project','fields':{'title':'Home context project','description':'Native context target','color':'sage'}})
    choose_chat_channel(page,channel['name'])
    def calendar_editor(title):
        open_chat_tool(page,'My day');day=page.get_by_role('dialog',name='My day',exact=True)
        calendar=day.get_by_role('region',name='Private calendar',exact=True)
        if not calendar.is_visible():day.get_by_role('button',name='Your private calendar',exact=True).click()
        with held.HeldRead(page,'/channels/'+home['id']) as metadata:
            calendar.get_by_role('button',name='Edit private event Private context appointment',exact=True).click()
            form=calendar.get_by_role('form',name='Private event',exact=True);form.get_by_label('Event title',exact=True).fill(title);metadata.wait();metadata.release()
        checks['private_context_metadata_load_survives_immediate_new_editor_typing']=True
        button=form.get_by_role('button',name='Continue in '+home['name'],exact=True);expect(button).to_be_visible()
        return day,calendar,form,button
    def cancel_private(day,form):
        page.once('dialog',lambda prompt:prompt.accept());form.get_by_role('button',name='Cancel',exact=True).click();expect(form).to_have_count(0);day.get_by_role('button',name='Close my day',exact=True).click()
    def unchanged():
        row=next(row for row in p.api(base,'GET',api_path,owner) if row['id']==event_id)
        assert row['revision']==1 and row['fields']['title']=='Private context appointment'
        assert row['context_channel_id']==home['id'] and row['fields']['field_references']['description'][0]['id']==owner['user_id']
    # A separate unsaved B card is still a real scope guard; only MyDay itself
    # is exempt from its own intentional context handoff.
    activate_chat_feature(page,'task');card=capture_work_card(page);card.get_by_role('button',name='New task',exact=True).click();task=card.get_by_role('form',name='New task',exact=True);task.get_by_label('Task title',exact=True).fill('Keep the separate B task draft.');task_node=task.element_handle()
    day,calendar,form,button=calendar_editor('Keep the private A draft too.');private_node=form.element_handle();declines=[]
    def decline(prompt):declines.append(prompt.message);prompt.dismiss()
    page.on('dialog',decline)
    try:
        button.click();deadline=__import__('time').monotonic()+8
        while not declines and __import__('time').monotonic()<deadline:page.wait_for_timeout(25)
        assert len(declines)==1,'A different dirty conversation card must be reviewed before switching'
        expect(page.locator('.fc-chat-topbar-channel')).to_contain_text(channel['name']);expect(form.get_by_label('Event title',exact=True)).to_have_value('Keep the private A draft too.')
        assert form.evaluate('(node,previous)=>node===previous&&previous.isConnected',private_node)
        assert task_node.evaluate('(node)=>node.isConnected&&node.querySelector("input").value==="Keep the separate B task draft."')
        unchanged()
    finally:page.remove_listener('dialog',decline)
    cancel_private(day,form);task.get_by_label('Task title',exact=True).fill('');close_work_card(page,card)
    checks['private_context_declined_other_dirty_card_preserves_both_exact_doms_and_no_write']=True
    # A changed source cancels the original continuation while its actual GET
    # is still in flight. The unchanged authenticated handler is then released.
    day,calendar,form,button=calendar_editor('Current private context draft.');private_node=form.element_handle()
    with held.HeldRead(page,'/channels/'+home['id']) as gate:
        button.click();gate.wait();form.get_by_label('Event title',exact=True).fill('A newer private context thought.');gate.release()
        expect(page.locator('.fc-chat-topbar-channel')).to_contain_text(channel['name']);expect(form.get_by_label('Event title',exact=True)).to_have_value('A newer private context thought.');assert form.evaluate('(node,previous)=>node===previous&&previous.isConnected',private_node);unchanged()
    checks['private_context_delayed_lookup_rejects_newer_private_input']=True
    with held.HeldRead(page,'/channels/'+home['id']) as gate:
        button.click();gate.wait();form.get_by_label('Description',exact=True).dispatch_event('compositionstart');gate.release();expect(page.locator('.fc-chat-topbar-channel')).to_contain_text(channel['name']);unchanged()
    form.get_by_label('Description',exact=True).dispatch_event('compositionend',{'data':''})
    checks['private_context_ime_start_retires_old_channel_lookup']=True

    # The deliberate A handoff keeps this exact editor and private references.
    description_node=form.get_by_label('Description',exact=True).element_handle();title_node=form.get_by_label('Event title',exact=True).element_handle();original_description=form.get_by_label('Description',exact=True).input_value();button.click();expect(page.locator('.fc-chat-topbar-channel')).to_contain_text(home['name']);expect(form.get_by_label('Event title',exact=True)).to_have_value('A newer private context thought.');expect(form.get_by_label('Description',exact=True)).to_have_value(original_description);assert form.get_by_label('Description',exact=True).evaluate('(node,previous)=>node===previous&&previous.isConnected',description_node);assert form.get_by_label('Event title',exact=True).evaluate('(node,previous)=>node===previous&&previous.isConnected',title_node);assert form.evaluate('(node,previous)=>node===previous&&previous.isConnected',private_node);unchanged()
    description=form.get_by_label('Description',exact=True);description.fill('@Flow');option=page.get_by_role('option').filter(has_text='Flow Owner');expect(option).to_be_visible();option.click();description.press('End');description.press('Enter');description.type('/task Private context task');description.press('Enter')
    opened=capture_work_card(page);expect(opened.get_by_role('form',name='New task',exact=True).get_by_label('Task title',exact=True)).to_have_value('Private context task');assert private_node.evaluate('(node)=>node.isConnected');unchanged();page.once('dialog',lambda prompt:prompt.accept());close_work_card(page,opened);expect(form).to_be_visible();assert form.evaluate('(node,previous)=>node===previous&&previous.isConnected',private_node);expect(description).to_have_value('@Flow Owner \n');unchanged();assert not p.api(base,'GET',f'/channels/{home["id"]}/projects/home-project/tasks',owner)['items']
    checks['private_context_handoff_keeps_editor_references_and_enables_new_actual_a_slash_mentions_without_save']=True
    cancel_private(day,form);choose_chat_channel(page,channel['name'])
    # Real fixture sessions A→B→A in the same turn cannot revive the old source
    # callback merely because actor/token compare equal again afterward.
    day,calendar,form,button=calendar_editor('Private draft before account boundary.');private_node=form.element_handle();session={**owner,'access_token':page.evaluate('localStorage.getItem("freecollab_token")')}
    with held.HeldRead(page,'/channels/'+home['id']) as gate:
        button.click();gate.wait();page.evaluate('''async sessions=>{const{api}=await import('/src/lib/api');api.applySession(sessions[0]);api.applySession(sessions[1]);}''',[alternate,session]);expect(day).to_have_count(0);gate.release();assert not private_node.evaluate('(node)=>node.isConnected');unchanged()
    checks['private_context_auth_aba_cannot_restore_retired_editor_or_continue_old_lookup']=True
    expect(page.locator('.fc-chat')).to_be_visible();choose_chat_channel(page,channel['name'])


def main():
    stage='startup';checks={};errors=[];reads=[];processes=[];streams=[];browser=None
    hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='freecollab-flow-completion-') as directory:
        private=Path(directory)
        try:
            api_port,app_port=p.port(),p.port();base,app=f'http://127.0.0.1:{api_port}',f'http://127.0.0.1:{app_port}';database=private/'proof.db'
            environment={**os.environ,'ENVIRONMENT':'test','DATABASE_URL':'sqlite:///'+str(database),'STORAGE_PATH':str(private/'files'),'STORAGE_BACKEND':'local','REDIS_URL':'','JWT_SECRET':secrets.token_urlsafe(48),'ACCOUNT_SECURITY_KEY':base64.b64encode(secrets.token_bytes(32)).decode(),'ACCOUNT_SECURITY_KEY_FILE':str(private/'account.key'),'MESSAGE_ENCRYPTION_KEYS':json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),'MESSAGE_ENCRYPTION_ACTIVE_KEY_ID':'proof','MESSAGE_ENCRYPTION_KEY_FILE':str(private/'message.keys'),'PUBLIC_APP_URL':app,'CORS_ORIGINS':json.dumps([app]),'MATRIX_ENABLED':'false','MAIL_DELIVERY_MODE':'disabled','OIDC_ENABLED':'false','WEBHOOKS_ENABLED':'false','SYNC_ENABLED':'false','OTRUST_ENABLED':'false','COPILOT_ENABLED':'false','CLOUD_FILES_ENABLED':'false','EXTERNAL_MEETINGS_ENABLED':'false','WORKFLOWS_ENABLED':'true','PYTHONPATH':str(ROOT/'backend'),'VITE_API_URL':'/api'}
            launcher=private/'api.py';launcher.write_text('from app.main import socket_app\nimport uvicorn\nuvicorn.run(socket_app,host="127.0.0.1",port='+str(api_port)+',access_log=False,proxy_headers=False)\n')
            vite=p.create_vite(private,base,app_port);vite.write_text(vite.read_text().replace('server:{host:','server:{fs:{allow:'+json.dumps([str(private/'frontend'),str(ROOT/'frontend')])+'},host:'))
            for command,name,cwd in [([PYTHON,str(launcher)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',ROOT/'frontend')]:
                stream=(private/name).open('w');streams.append(stream);processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            p.ready(base+'/ready',processes);p.ready(app+'/',processes)
            owner=p.native_user(base,database,'Flow Owner');other=p.native_user(base,database,'Another Account')
            other_workspace=p.api(base,'POST','/workspaces',other,{'name':'Another private studio'});workspace=p.api(base,'POST','/workspaces',owner,{'name':'Flow completion studio'});home=p.api(base,'POST','/channels',owner,{'name':'Home room','workspace_id':workspace['id']});channel=p.api(base,'POST','/channels',owner,{'name':'Exact source room','workspace_id':workspace['id']});ch=channel['id'];ws=workspace['id']
            source=p.api(base,'POST','/messages/channels/'+home['id'],owner,{'content':'A retained source communication.'})
            records={}
            for key,title in [('first','First unrelated project'),('target','Exact project target')]:records['project-'+key]=p.api(base,'POST',f'/channels/{ch}/projects',owner,{'id':'project-'+key,'fields':{'title':title,'description':'Current canonical project','color':'sage'}})
            task=p.api(base,'POST',f'/channels/{ch}/projects/project-target/tasks',owner,{'id':'task-target','fields':{'title':'Exact task target','description':'Current task body','status':'todo','priority':'normal','assignee_user_id':owner['user_id'],'due_date':None,'checklist':[],'file_ids':[],'decision_ids':[]}})
            for key,title in [('first','First unrelated document'),('target','Exact document target')]:
                encoded=subprocess.check_output(['node','--input-type=module','-e','import*as Y from"yjs";const d=new Y.Doc();d.getText("title").insert(0,'+json.dumps(title)+');d.getText("body").insert(0,'+json.dumps('Canonical '+key+' body for the actual Yjs reader.')+');process.stdout.write(Buffer.from(Y.encodeStateAsUpdate(d)).toString("base64"))'],cwd=ROOT/'frontend',text=True)
                records['document-'+key]=p.api(base,'POST',f'/channels/{ch}/shared-documents',owner,{'id':'document-'+key,'update_id':'initial-'+key,'title':title,'update_base64':encoded})
            stack=p.api(base,'POST',f'/channels/{ch}/shared-document-organization/stacks',owner,{'id':'stack-target','title':'Exact stack target'})
            for key,title in [('first','First unrelated update'),('target','Exact activity target')]:records['feed-'+key]=p.api(base,'POST',f'/channels/{ch}/activity-feed',owner,{'id':'feed-'+key,'fields':{'title':title,'body':'Actual current '+key+' activity body.'}})
            decision=p.api(base,'POST',f'/channels/{ch}/decisions',owner,{'title':'Exact decision target','content':'Actual current decision content.','status':'accepted'})
            start=datetime.now(timezone.utc)+timedelta(days=1);event={'title':'Exact shared calendar target','description':'Shared event source','location':'Studio','starts_at':start.isoformat(),'ends_at':(start+timedelta(hours=1)).isoformat(),'timezone':'UTC','all_day':False,'reminder_minutes':None,'external_uid':None}
            p.api(base,'POST',f'/channels/{ch}/integrations/calendar',owner,{'id':'calendar-target','fields':event})
            p.api(base,'POST',f'/channels/{ch}/integrations/documents',owner,{'id':'link-target','fields':{'title':'Exact linked document target','provider':'google_drive','url':'https://docs.google.com/document/d/flow-proof-target/edit','notes':'Visible canonical link notes'}})
            file=upload(base,owner,ch,'exact-source.txt','text/plain',b'Exact verified file contents for native navigation.')
            sound=io.BytesIO()
            with wave.open(sound,'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(8000);wav.writeframes(b'\0\0'*800)
            audio=upload(base,owner,ch,'recording.wav','audio/wav',sound.getvalue())
            clip=p.api(base,'POST',f'/channels/{ch}/clips',owner,{'id':'clip-target','file_id':audio['id'],'title':'Exact recording target','duration':0.1,'transcript':'Actual current recording transcript.'})
            meeting=p.api(base,'POST',f'/channels/{ch}/meetings',owner,{})['meeting']
            workflow=p.api(base,'POST',f'/workspaces/{ws}/channels/{ch}/workflows',owner,{'password':p.PASSWORD,'fields':{'name':'Exact workflow target','trigger':'manual','conditions':{'actor':'any','status':None},'steps':[{'kind':'notify','recipients':[owner['user_id']]}]}})
            workflow_run=p.api(base,'POST',f'/workspaces/{ws}/channels/{ch}/workflows/{workflow["id"]}/run',owner)
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_PATH') or '/usr/bin/chromium',args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce');page.set_default_timeout(30000)
                page.on('pageerror',lambda failure:errors.append(str(failure)));page.on('request',lambda request:reads.append({'method':request.method,'path':urlsplit(request.url).path}) if '/api/' in request.url else None)
                p.login(page,app,owner,workspace);choose_chat_channel(page,home['name']);composer=page.locator('.fc-chat-composer textarea');expect(composer).to_be_visible()
                stage='actual typed slash opens private calendar without consuming surrounding communication'
                composer.fill('/calendar');popup=page.locator('.fc-slash-commands');expect(popup).to_be_visible();popup.get_by_role('option').filter(has_text='Your private calendar').click();day=page.get_by_role('dialog',name='My day',exact=True);calendar=day.get_by_role('region',name='Private calendar',exact=True);expect(calendar).to_be_visible();expect(composer).to_have_value('');day.get_by_role('button',name='Close my day',exact=True).click();draft='Unsent source remains.\n/calendar';composer.fill(draft);expect(popup).to_have_count(0);activate_chat_feature(page,'personal-calendar');expect(calendar).to_be_visible();expect(composer).to_have_value(draft)
                calendar.get_by_role('button',name='New private event',exact=True).click();editor=calendar.get_by_role('form',name='Private event',exact=True);title=editor.get_by_label(re.compile('^Event title'));description=editor.get_by_label(re.compile('^Description'));title.fill('My private appointment');description.fill('@Flow');mention=page.get_by_role('option').filter(has_text='Flow Owner');expect(mention).to_be_visible();mention.click();expect(description).to_have_value('@Flow Owner ')
                description.press('End');description.press('Enter');description.type('/task Keep my private notes');expect(page.locator('.fc-collaboration-popup .fc-slash-command').first).to_be_visible();description.press('Escape');expect(page.locator('.fc-collaboration-popup')).to_have_count(0);expect(day).to_be_visible();expect(description).to_have_value('@Flow Owner \n/task Keep my private notes');editor.get_by_role('button',name='Save private event',exact=True).click();expect(calendar.get_by_text('My private appointment',exact=True)).to_be_visible();private_events=p.api(base,'GET',f'/workspaces/{ws}/personal-calendar',owner);assert len(private_events)==1;private_event=private_events[0];assert private_event['fields']['field_references']['description'][0]['id']==owner['user_id'];assert p.api(base,'GET',f'/channels/{home["id"]}/integrations/calendar',owner)==[];checks['typed_calendar_and_browse_preserved_literal_draft_with_persistent_private_mentions']=True
                calendar.get_by_role('button',name='Edit private event My private appointment',exact=True).click();editor=calendar.get_by_role('form',name='Private event',exact=True);editor.get_by_label(re.compile('^Event title')).fill('Revised private appointment');editor.get_by_role('button',name='Save private event',exact=True).click();expect(calendar.get_by_text('Revised private appointment',exact=True)).to_be_visible();assert p.api(base,'GET',f'/workspaces/{ws}/personal-calendar',owner)[0]['revision']==2
                with page.expect_download() as download:calendar.get_by_role('button',name='Export calendar',exact=True).click()
                assert 'Revised private appointment' in Path(download.value.path()).read_text();page.once('dialog',lambda prompt:prompt.accept());calendar.get_by_role('button',name='Delete private event Revised private appointment',exact=True).click();expect(calendar.get_by_text('Your private calendar is empty.',exact=True)).to_be_visible();assert not p.api(base,'GET',f'/workspaces/{ws}/personal-calendar',owner);calendar.get_by_role('button',name='New private event',exact=True).click();unsaved=calendar.get_by_role('form',name='Private event',exact=True);unsaved.get_by_label(re.compile('^Event title')).fill('Unsaved private draft');page.once('dialog',lambda prompt:prompt.dismiss());unsaved.get_by_label(re.compile('^Event title')).press('Escape');expect(day).to_be_visible();expect(unsaved.get_by_label(re.compile('^Event title'))).to_have_value('Unsaved private draft');page.once('dialog',lambda prompt:prompt.accept());unsaved.get_by_role('button',name='Cancel',exact=True).click();expect(unsaved).to_have_count(0);day.press('Escape');expect(day).to_have_count(0);expect(composer).to_have_value(draft);checks['private_calendar_real_update_export_delete_and_source_draft_retention']=True;checks['child_palette_escape_and_private_draft_discard_guard_precede_modal_close']=True
                stage='My day opens the same private calendar component'
                open_chat_tool(page,'My day');
                if not day.get_by_role('region',name='Private calendar',exact=True).is_visible(): day.get_by_role('button',name='Your private calendar',exact=True).click()
                expect(day.get_by_role('region',name='Private calendar',exact=True)).to_be_visible();day.get_by_role('button',name='Close my day',exact=True).click();composer.fill('')
                checks['my_day_and_slash_use_same_real_private_calendar']=True
                stage='private calendar original-context continuation and source leases'
                private_context_checks(page,base,owner,other,workspace,home,channel,private_event['fields'],checks)
                def source_link(kind,item,project=None):
                    data={'workspace_id':ws,'channel_id':ch,'kind':kind,'item_id':item}
                    if project:data['project_id']=project
                    return app+'/#open='+base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip('=')
                def open_source(kind,item,selector,project=None):
                    page.goto(source_link(kind,item,project),wait_until='domcontentloaded');target=page.locator(selector).first;expect(target).to_be_visible(timeout=45000)
                    return target
                def close_source():
                    integrations=page.get_by_role('dialog',name='Integrations',exact=True)
                    preview=page.get_by_role('dialog',name='Preview exact-source.txt',exact=True)
                    if integrations.is_visible():integrations.get_by_role('button',name='Close integrations',exact=True).click()
                    elif preview.is_visible():preview.get_by_role('button',name='Close file preview',exact=True).click()
                    else:
                        cards=page.get_by_role('region',name='Work in this conversation',exact=True)
                        for index in range(cards.count()-1,-1,-1):
                            card=cards.nth(index)
                            if card.is_visible():close_work_card(page,capture_work_card(page,card));break
                cases=[('document','document-target','.fc-shared-reading-page:visible .fc-shared-reading-title','Exact document target',None),('project','project-target','.fc-project-flow-actions','Exact project target',None),('task','task-target','[data-task-id="task-target"]','Exact task target','project-target'),('feed','feed-target','[data-resource-kind="feed"][data-resource-id="feed-target"]','Exact activity target',None),('decision',decision['id'],'.fc-decision-card','Exact decision target',None),('calendar','calendar-target','[data-resource-kind="calendar"][data-resource-id="calendar-target"]',event['title'],None),('linked-document','link-target','[data-resource-kind="linked-document"][data-resource-id="link-target"]','Exact linked document target',None),('file',file['id'],'.fc-file-preview-content','Exact verified file contents',None),('meeting',meeting['id'],'[data-media-kind="meeting"][data-media-id="'+meeting['id']+'"]','Channel meeting',None),('clip','clip-target','[data-media-kind="clip"][data-media-id="clip-target"]','Exact recording target',None),('stack','stack-target','[data-document-stack="stack-target"]','Exact stack target',None),('workflow',workflow['id'],'[data-resource-kind="workflow"][data-resource-id="'+workflow['id']+'"]','Exact workflow target',None),('workflow-run',workflow_run['id'],'[data-resource-kind="workflow-run"][data-resource-id="'+workflow_run['id']+'"]','Exact workflow target',None)]
                for kind,item,selector,label,project in cases:
                    stage='native exact '+kind+' source';target=open_source(kind,item,selector,project);expect(target).to_contain_text(label);assert '#open=' not in page.url;checks['actual_native_exact_'+kind+'_source']=True;print('PASS '+stage,flush=True);close_source()
                stage='held native metadata lookup cannot override newer app-link intent'
                choose_chat_channel(page,home['name'])
                with held.HeldRead(page,'/channels/'+ch) as gate:
                    page.goto(source_link('document','document-first'),wait_until='domcontentloaded');gate.wait();page.goto(source_link('feed','feed-target'),wait_until='domcontentloaded');gate.release();expect(page.locator('[data-resource-kind="feed"][data-resource-id="feed-target"]')).to_be_visible(timeout=45000);expect(page.locator('.fc-shared-reading-page:visible')).to_have_count(0)
                checks['delayed_native_source_read_cannot_override_newer_native_intent']=True;close_source()
                stage='held native lookup cannot cross a real sign-out'
                choose_chat_channel(page,home['name'])
                with held.HeldRead(page,'/channels/'+ch) as gate:
                    page.goto(source_link('task','task-target','project-target'),wait_until='domcontentloaded');gate.wait();open_chat_tool(page,'Sign out');expect(page.locator('.fc-chat')).to_have_count(0);route,gate.route=gate.route,None;route.continue_();expect(page.locator('[data-task-id="task-target"]')).to_have_count(0)
                checks['native_authority_lease_rejects_delivery_after_real_sign_out']=True
                p.login(page,app,other,other_workspace);expect(page.locator('.fc-workspace-switch-trigger')).to_contain_text('Another private studio');expect(page.locator('[data-task-id="task-target"]')).to_have_count(0);checks['old_native_hint_never_opens_a_prior_accounts_resource_after_new_sign_in']=True
                assert not errors,errors;checks['no_browser_runtime_errors']=True
            after={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES};REPORT.write_text(json.dumps({'status':'passed','checks':checks,'source_hashes':hashes,'sources_unchanged':hashes==after,'actual_api_reads':reads,'scope':'Full production App/API; no response mocks. Native fragment path, exact canonical readers, private calendar CRUD/export, shared writing and real session fences.'},indent=2)+'\n');print(json.dumps({'status':'passed','checks':checks,'report':str(REPORT)}),flush=True)
        except Exception as failure:
            logs={name:(private/name).read_text(errors='replace')[-5000:] for name in ['api.log','vite.log'] if (private/name).exists()};REPORT.write_text(json.dumps({'status':'failed','stage':stage,'checks':checks,'error':str(failure).replace(p.PASSWORD,'[redacted]'),'browser_errors':errors,'logs':logs,'source_hashes':hashes},indent=2)+'\n');raise
        finally:
            p.stop(processes)
            for stream in streams:stream.close()

if __name__=='__main__':main()
