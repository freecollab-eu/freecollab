#!/usr/bin/env python3
"""Real full-app personal home, source navigation and source-authorized actions."""
from ui_navigation import open_chat_tool
import base64
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('my_day_helpers', Path(__file__).with_name('customer-sso-browser.py'))
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
PYTHON = os.getenv('BACKEND_PYTHON') or str(ROOT / 'backend/.venv/bin/python')
REPORT = Path(os.getenv('MY_DAY_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/my-day-browser.json')))


def main():
    checks, processes, streams, stage = {}, [], [], 'private startup'
    with tempfile.TemporaryDirectory(prefix='freecollab-my-day-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = p.port(), p.port()
            base, app = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
            database = private / 'proof.db'; environment = os.environ.copy()
            environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(database), STORAGE_PATH=str(private/'files'),
                STORAGE_BACKEND='local', REDIS_URL='', JWT_SECRET=secrets.token_urlsafe(48),
                ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), ACCOUNT_SECURITY_KEY_FILE=str(private/'account.key'),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',
                MESSAGE_ENCRYPTION_KEY_FILE=str(private/'message.keys'), PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]), MATRIX_ENABLED='false',
                MAIL_DELIVERY_MODE='disabled', OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', SYNC_ENABLED='false', OTRUST_ENABLED='false',
                COPILOT_ENABLED='false', CLOUD_FILES_ENABLED='false', EXTERNAL_MEETINGS_ENABLED='false', PYTHONPATH=str(ROOT/'backend'))
            launcher = private/'api.py'
            launcher.write_text('from app.main import socket_app\nimport uvicorn\nuvicorn.run(socket_app,host="127.0.0.1",port='+str(api_port)+',access_log=False,proxy_headers=False)\n')
            vite = p.create_vite(private, base, app_port)
            vite.write_text(vite.read_text().replace('server:{host:', 'server:{fs:{allow:' + json.dumps([str(private/'frontend'),str(ROOT/'frontend')]) + '},host:'))
            for command,name,cwd in [([PYTHON,str(launcher)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',ROOT/'frontend')]:
                stream=(private/name).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            p.ready(base+'/ready',processes); p.ready(app+'/',processes)
            owner=p.native_user(base,database,'My Day Owner')
            first=p.api(base,'POST','/workspaces',owner,{'name':'Studio North'})
            second=p.api(base,'POST','/workspaces',owner,{'name':'Studio South'})
            home=p.api(base,'POST','/channels',owner,{'name':'A calm start','workspace_id':first['id']})
            channel=p.api(base,'POST','/channels',owner,{'name':'The next chapter','workspace_id':second['id']})
            p.api(base,'POST',f"/channels/{channel['id']}/projects",owner,{'id':'first-project','fields':{'title':'First project','description':'','color':'sage'}})
            p.api(base,'POST',f"/channels/{channel['id']}/projects",owner,{'id':'target-project','fields':{'title':'Second project','description':'','color':'blue'}})
            fields={'title':'Prepare the thoughtful launch','description':'A current assigned task','status':'todo','priority':'high','assignee_user_id':owner['user_id'],
                'due_date':datetime.now(timezone.utc).date().isoformat(),'checklist':[],'file_ids':[],'decision_ids':[]}
            p.api(base,'POST',f"/channels/{channel['id']}/projects/target-project/tasks",owner,{'id':'personal-task','fields':fields})
            start=datetime.now(timezone.utc)+timedelta(days=1)
            event={'title':'A quiet planning hour','description':'','location':'North room','starts_at':start.isoformat(),'ends_at':(start+timedelta(hours=1)).isoformat(),
                'all_day':False,'timezone':'UTC','reminder_minutes':None,'external_uid':None}
            p.api(base,'POST',f"/channels/{channel['id']}/integrations/calendar",owner,{'id':'personal-event','fields':event})
            message=p.api(base,'POST',f"/messages/channels/{home['id']}",owner,{'content':'A useful follow-up'})
            p.api(base,'POST','/bookmarks',owner,{'channel_id':home['id'],'message_id':message['id']})
            reminder=p.api(base,'POST',f"/channels/{home['id']}/reminders",owner,{'message_id':message['id'],'due_at':(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()})
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or '/usr/bin/chromium',headless=True,args=['--no-sandbox'])
                context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                page=context.new_page(); page.set_default_timeout(40000); errors=[]
                page.on('pageerror',lambda failure:errors.append(str(failure)))
                p.login(page,app,owner,first)
                stage='real personal home cross-workspace snapshot'
                open_chat_tool(page, 'My day')
                day=page.get_by_role('dialog',name='My day',exact=True)
                expect(day.get_by_role('button',name=fields['title'],exact=True)).to_be_visible()
                expect(day.get_by_role('button',name=event['title'],exact=True)).to_be_visible()
                expect(day.get_by_text('Studio South · The next chapter',exact=True).first).to_be_visible()
                checks['real_home_aggregates_current_assigned_task_and_calendar_across_workspaces']=True
                REPORT.parent.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(REPORT.with_name(REPORT.stem+'-desktop.png')))
                stage='exact project navigation switches workspace'
                day.get_by_role('button',name=fields['title'],exact=True).click()
                work=page.get_by_role('region',name='Work in this conversation',exact=True)
                expect(work).to_be_visible(); expect(work).to_have_attribute('data-work-kind','tasks'); expect(work.locator('.fc-project-card')).to_have_attribute('data-task-id','personal-task')
                expect(work.get_by_role('heading',name=fields['title'],exact=True)).to_be_visible()
                work.get_by_role('button',name='Close this work',exact=True).click()
                checks['task_link_reauthorizes_switches_workspace_and_opens_exact_project']=True
                stage='source-authorized completion and follow-up'
                open_chat_tool(page, 'My day')
                day.get_by_role('button',name='Complete task '+fields['title'],exact=True).click()
                expect(day.get_by_text('No open tasks assigned to you.',exact=True)).to_be_visible()
                tasks=p.api(base,'GET',f"/channels/{channel['id']}/projects/target-project/tasks",owner)
                assert tasks['items'][0]['fields']['status']=='done' and tasks['items'][0]['revision']==2
                day.get_by_role('button',name='In one hour',exact=True).click()
                expect(day.get_by_role('button',name='Complete reminder',exact=True)).to_be_enabled()
                changed=p.api(base,'GET',f"/channels/{home['id']}/reminders",owner)[0]
                assert changed['id']==reminder['id'] and datetime.fromisoformat(changed['due_at'].replace('Z','+00:00'))>datetime.now(timezone.utc)+timedelta(minutes=50)
                day.get_by_role('button',name='Complete reminder',exact=True).click()
                expect(day.get_by_role('button',name='Complete reminder',exact=True)).to_have_count(0)
                day.get_by_role('button',name='Complete saved follow-up',exact=True).click()
                expect(day.get_by_text('Nothing waiting for your attention.',exact=True)).to_be_visible()
                assert p.api(base,'GET','/bookmarks',owner)[0]['completed_at']
                checks['real_task_revision_reminder_snooze_completion_and_saved_completion_persist']=True
                stage='calendar opens through authorized source'
                day.get_by_role('button',name=event['title'],exact=True).click()
                integrations=page.get_by_role('dialog',name='Integrations',exact=True)
                expect(integrations).to_be_visible(); expect(integrations.get_by_text(event['title'],exact=True)).to_be_visible()
                integrations.get_by_role('button',name='Close integrations',exact=True).click()
                checks['calendar_link_opens_current_channel_calendar']=True
                stage='Swedish and narrow layout with appearance tokens'
                page.evaluate('async()=>{(await import("/src/i18n/core.ts")).applyLocale("sv")}')
                open_chat_tool(page, 'Min arbetsdag')
                day=page.get_by_role('dialog',name='Min arbetsdag',exact=True)
                expect(day.get_by_role('heading',name='Min arbetsdag',exact=True)).to_be_visible()
                page.set_viewport_size({'width':320,'height':800})
                expect(day.get_by_role('button',name=event['title'],exact=True)).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert day.evaluate('(node)=>node.scrollWidth<=node.clientWidth'), day.evaluate('(node)=>({width:node.clientWidth,scroll:node.scrollWidth,children:[...node.querySelectorAll("*")].filter(e=>e.getBoundingClientRect().right>node.getBoundingClientRect().right).map(e=>({tag:e.tagName,cls:e.className,width:e.getBoundingClientRect().width}))})')
                REPORT.parent.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(REPORT.with_suffix('.png')))
                checks['swedish_320px_theme_aware_personal_home_without_horizontal_overflow']=True
                stage='deleted source vanishes on refresh'
                # Fixture-owned API archives the calendar through its real current
                # author endpoint; the personal view must drop the old revision.
                p.api(base,'POST',f"/channels/{channel['id']}/integrations/calendar",owner,{'id':'personal-event','expected_revision':1,'deleted':True})
                day.get_by_role('button',name='Uppdatera min arbetsdag',exact=True).click()
                expect(day.get_by_role('button',name=event['title'],exact=True)).to_have_count(0)
                expect(day.get_by_text('Lite utrymme i kalendern.',exact=True)).to_be_visible()
                checks['refresh_discards_deleted_source_without_retained_title_copy']=True
                assert not errors, 'Runtime errors: '+','.join(errors)
                checks['no_browser_runtime_errors']=True
                browser.close()
        except Exception:
            print('My Day proof failed during '+stage,flush=True)
            for name in ['api.log','vite.log']:
                path=private/name
                if path.exists():print(path.read_text()[-3000:],flush=True)
            raise
        finally:
            p.stop(processes)
            for stream in streams:stream.close()
            REPORT.parent.mkdir(parents=True,exist_ok=True); REPORT.write_text(json.dumps({'stage':stage,'checks':checks},indent=2))
    print(json.dumps(checks,indent=2),flush=True)


if __name__=='__main__':main()
