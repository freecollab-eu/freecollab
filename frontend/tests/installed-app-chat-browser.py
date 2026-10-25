#!/usr/bin/env python3
"""Actual full native Chat/thread/document /app review and real outbox proof.

No mocked catalogue, command dispatcher, UI action or successful queue response.
The customer-owned public HTTPS destination is configured but no worker/provider
transport is run. Encrypted foreground transport has separate device/API proofs.
"""
from ui_navigation import activate_chat_feature, capture_work_card, close_work_card, open_conversation_work, reveal_message_actions
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from playwright.sync_api import expect, sync_playwright

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('installed_chat_helpers',Path(__file__).with_name('customer-sso-browser.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
PYTHON=os.getenv('BACKEND_PYTHON') or sys.executable
REPORT=Path(os.getenv('INSTALLED_APP_CHAT_REPORT',str(ROOT.parent/'.freecollab/proof-results/gap-completion-20261025/slack-apps/full-chat-browser/report.json')))
SOURCES=['frontend/src/components/Chat.tsx','frontend/src/components/AppCommandWork.tsx','frontend/src/components/AppCommandReview.tsx',
    'frontend/src/components/CollaborationInput.tsx','frontend/src/components/SharedDocumentsPanel.tsx','frontend/src/lib/composer-commands.ts',
    'frontend/src/lib/slash-commands.ts','frontend/src/lib/installed-app-commands.ts','backend/app/api/app_platform.py',
    'backend/app/services/app_platform.py','frontend/tests/installed-app-chat-browser.py']


def main():
    checks,processes,streams,errors,writes,external={},[],[],[],[],[]
    hashes=lambda:{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}
    before=hashes();stage='private startup';REPORT.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='freecollab-installed-app-chat-') as directory:
        private=Path(directory)
        try:
            api_port,app_port=p.port(),p.port();base,app=f'http://127.0.0.1:{api_port}',f'http://127.0.0.1:{app_port}';database=private/'proof.db'
            environment=os.environ.copy();environment.update(ENVIRONMENT='test',DATABASE_URL='sqlite:///'+str(database),STORAGE_PATH=str(private/'files'),STORAGE_BACKEND='local',REDIS_URL='',JWT_SECRET=secrets.token_urlsafe(48),
                ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),ACCOUNT_SECURITY_KEY_FILE=str(private/'account.key'),MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),
                MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',MESSAGE_ENCRYPTION_KEY_FILE=str(private/'message.keys'),PUBLIC_APP_URL=app,CORS_ORIGINS=json.dumps([app]),MATRIX_ENABLED='false',MAIL_DELIVERY_MODE='disabled',
                OIDC_ENABLED='false',SYNC_ENABLED='false',OTRUST_ENABLED='false',COPILOT_ENABLED='false',CLOUD_FILES_ENABLED='false',EXTERNAL_MEETINGS_ENABLED='false',SEMANTIC_OPENSEARCH_URL='',PYTHONPATH=str(ROOT/'backend'))
            launcher=private/'api.py';launcher.write_text('from app.main import socket_app\nimport uvicorn\nuvicorn.run(socket_app,host="127.0.0.1",port='+str(api_port)+',access_log=False,proxy_headers=False)\n')
            vite=p.create_vite(private,base,app_port)
            vite.write_text(vite.read_text().replace('server:{host:','server:{fs:{allow:'+json.dumps([str(private/'frontend'),str(ROOT/'frontend')])+'},host:'))
            for command,name,cwd in [([PYTHON,str(launcher)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',ROOT/'frontend')]:
                stream=(private/name).open('w');streams.append(stream);processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            p.ready(base+'/ready',processes);p.ready(app+'/',processes)
            owner=p.native_user(base,database,'Actual installed command owner');workspace=p.api(base,'POST','/workspaces',owner,{'name':'Actual command workspace'})
            channel=p.api(base,'POST','/channels',owner,{'name':'Actual conversations','workspace_id':workspace['id']})
            parent=p.api(base,'POST',f"/messages/channels/{channel['id']}",owner,{'content':'A real thread source'})
            manifest={'app_id':'fixture.receiver','name':'Private receiver','version':'1.0.0','description':'Reviewed customer app','scopes':['commands:invoke'],'commands':[{'name':'ship','description':'Run a typed reviewed action','arguments':[
                {'name':'title','label':'Reviewed title','required':True,'max_length':400},{'name':'copies','label':'Copies','required':True,'max_length':8,'type':'number'}]}]}
            made=p.api(base,'POST',f"/workspaces/{workspace['id']}/apps",owner,{'manifest':manifest,'channel_ids':[channel['id']],'endpoint':'https://8.8.8.8/reviewed-fixture','authorization':''})['installation']
            catalogue=p.api(base,'GET',f"/workspaces/{workspace['id']}/channels/{channel['id']}/apps/commands",owner)['commands'][0]
            jobs=lambda:p.api(base,'GET',f"/workspaces/{workspace['id']}/apps/{made['id']}/jobs",owner)['jobs']
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or '/usr/bin/chromium',headless=True,args=['--no-sandbox'])
                context=browser.new_context(viewport={'width':1500,'height':1100},reduced_motion='reduce');page=context.new_page();page.set_default_timeout(35000)
                page.on('pageerror',lambda error:errors.append(str(error)))
                def track(request):
                    parts=urlsplit(request.url)
                    if parts.hostname not in ('127.0.0.1','localhost'):external.append(parts.hostname)
                    if request.method=='POST' and '/apps/commands/' in parts.path:writes.append(request.post_data_json)
                page.on('request',track);p.login(page,app,owner,workspace)
                main_input=page.locator('.fc-chat-composer textarea')
                expect(main_input).to_be_visible();expect(page.locator('#message-'+parent['id'])).to_be_visible()
                def review(title,copies):
                    region=page.get_by_role('region',name='Review app command',exact=True);expect(region).to_be_visible()
                    expect(region.get_by_role('textbox',name='Reviewed title',exact=True)).to_have_value(title)
                    expect(region.get_by_role('textbox',name='Copies',exact=True)).to_have_value(copies)
                    expect(region.get_by_text('https://8.8.8.8',exact=True)).to_be_visible()
                    expect(region.get_by_role('button',name='Approve and run command',exact=True)).to_be_disabled()
                    return region,capture_work_card(page,region)
                def typed(input_,title,copies='2'):
                    input_.fill('/fixture.receiver:ship title='+json.dumps(title,ensure_ascii=False)+' copies='+copies)
                    panel=page.locator('.fc-slash-commands');expect(panel).to_be_visible()
                    panel.get_by_role('option').filter(has_text='Private receiver: ship').click()
                    return review(title,copies)
                def approve(region,title,copies):
                    count=len(jobs());assert len(writes)==count
                    region.get_by_role('checkbox',name='I reviewed these exact arguments and approve sharing them with this installed app.',exact=True).check()
                    region.get_by_role('button',name='Approve and run command',exact=True).click()
                    expect(region.get_by_role('status')).to_contain_text('Command queued for delivery.')
                    assert len(jobs())==count+1 and len(writes)==count+1
                    assert writes[-1]['expected_generation']==catalogue['generation'] and writes[-1]['arguments']=={'title':title,'copies':copies}
                    assert writes[-1]['command']=='ship' and writes[-1]['disclose_to_app'] is True
                stage='dynamic feature launcher retains unrelated source draft'
                main_input.fill('UNSENT MAIN DRAFT MUST STAY')
                activate_chat_feature(page,'app:'+catalogue['key'])
                region,card=review('','');assert not jobs() and not writes
                close_work_card(page,card);expect(main_input).to_have_value('UNSENT MAIN DRAFT MUST STAY')
                checks['real_dynamic_feature_launcher_opens_blank_review_and_preserves_unsent_main_draft']=True
                stage='main typed installed namespace review'
                region,card=typed(main_input,'  MAIN EXACT Å 🧭  ','3');assert not jobs() and not writes
                expect(main_input).to_have_value('')
                approve(region,'  MAIN EXACT Å 🧭  ','3');close_work_card(page,card)
                checks['main_typed_command_uses_current_schema_generation_and_queues_only_after_explicit_approval']=True
                stage='thread typed installed namespace review'
                main_input.fill('MAIN DRAFT WHILE THREAD WORK CONTINUES')
                message=page.locator('#message-'+parent['id']);reveal_message_actions(message);message.get_by_role('button',name='Reply in thread',exact=True).click()
                thread=page.get_by_role('dialog',name='Thread',exact=True);expect(thread).to_be_visible()
                region,card=typed(thread.get_by_role('textbox',name='Reply in thread',exact=True),'THREAD EXACT ARGUMENT','4')
                assert len(jobs())==1 and len(writes)==1
                approve(region,'THREAD EXACT ARGUMENT','4');close_work_card(page,card)
                if thread.is_visible():thread.get_by_role('button',name='Close thread',exact=True).click()
                expect(main_input).to_have_value('MAIN DRAFT WHILE THREAD WORK CONTINUES')
                checks['thread_typed_command_reaches_same_retained_review_and_preserves_main_conversation_draft']=True
                stage='document embedded command preserves surrounding unsaved writing'
                panel=open_conversation_work(page,'documents');source_card=capture_work_card(page,panel)
                panel.get_by_role('button',name='New shared document',exact=True).click()
                title_input=panel.get_by_label('Document title',exact=True);body=panel.get_by_label('Document text',exact=True)
                title_input.fill('UNSAVED DOCUMENT TITLE')
                body.fill('UNSAVED PREFIX\n/fixture.receiver:ship title="DOCUMENT EXACT ARGUMENT" copies=5\nUNSAVED SUFFIX')
                original=body.element_handle();body.press('Control+Home');body.press('ArrowDown');body.press('End')
                palette=page.locator('.fc-slash-commands');expect(palette).to_be_visible();palette.get_by_role('option').filter(has_text='Private receiver: ship').click()
                region,card=review('DOCUMENT EXACT ARGUMENT','5');assert len(jobs())==2 and len(writes)==2
                assert body.evaluate('(node,original)=>node===original&&original.isConnected',original)
                assert 'UNSAVED PREFIX' in body.input_value() and 'UNSAVED SUFFIX' in body.input_value() and '/fixture.receiver:' not in body.input_value()
                expect(title_input).to_have_value('UNSAVED DOCUMENT TITLE')
                assert p.api(base,'GET',f"/channels/{channel['id']}/shared-documents",owner)==[]
                approve(region,'DOCUMENT EXACT ARGUMENT','5');close_work_card(page,card)
                expect(body).to_be_visible();assert body.evaluate('(node,original)=>node===original&&original.isConnected',original)
                assert 'UNSAVED PREFIX' in body.input_value() and 'UNSAVED SUFFIX' in body.input_value()
                checks['document_slash_uses_same_real_review_consumes_only_command_line_and_retains_original_unsaved_form_DOM']=True
                assert p.api(base,'GET',f"/channels/{channel['id']}/shared-documents",owner)==[]
                assert len(p.api(base,'GET',f"/messages/channels/{channel['id']}",owner))==1
                with sqlite3.connect(database) as connection:
                    rows=connection.execute('select status,encrypted_payload from appjob').fetchall()
                    assert len(rows)==3 and all(status=='pending' and 'EXACT' not in payload for status,payload in rows)
                checks['three_real_encrypted_outbox_jobs_no_literal_commands_published_and_no_automatic_document_save']=True
                page.screenshot(path=str(REPORT.with_suffix('.png')))
                assert not errors and not external,(errors,external)
                checks['no_browser_runtime_errors_or_external_network_destinations']=True
                context.close();browser.close()
            after=hashes();drift=[name for name in SOURCES if before[name]!=after[name]]
            REPORT.write_text(json.dumps({'ok':True,'checks':checks,'source_sha256':before,'source_drift':drift,'scope':__doc__,'page_errors':errors,'external_destinations':external,'queued_commands':len(writes)},indent=2)+'\n')
            print(json.dumps({'ok':True,'checks':len(checks),'source_drift':drift,'report':str(REPORT)}))
        except Exception as failure:
            REPORT.write_text(json.dumps({'ok':False,'stage':stage,'error':str(failure)[:3000],'checks':checks,'source_sha256':before,'page_errors':errors,'external_destinations':external},indent=2)+'\n')
            for name in ('api.log','vite.log'):
                if (private/name).exists():(REPORT.parent/name).write_bytes((private/name).read_bytes())
            raise
        finally:
            p.stop(processes)
            for stream in streams:stream.close()


if __name__=='__main__':main()
