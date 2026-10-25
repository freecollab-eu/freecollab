#!/usr/bin/env python3
"""Actual AppCommandReview React UI with private native API/outbox, no provider calls.

This is a component-host proof. Chat slash/source retention is tested separately
by the product harness; no mocked catalogue, successful queue or app response.
"""
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
from urllib.parse import urlsplit
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('app_review_helpers', Path(__file__).with_name('customer-sso-browser.py'))
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
PYTHON = os.getenv('BACKEND_PYTHON') or sys.executable
REPORT = Path(os.getenv('INSTALLED_APP_REVIEW_REPORT', str(ROOT.parent / '.freecollab/proof-results/gap-completion-20261025/slack-apps/component-browser/report.json')))
SOURCES = ['frontend/src/components/AppCommandReview.tsx', 'frontend/src/lib/installed-app-commands.ts', 'backend/app/api/app_platform.py', 'backend/app/services/app_platform.py', 'backend/app/services/app_foreground.py', 'backend/app/models/app_platform.py', 'frontend/tests/installed-app-command-browser.py']
ENTRY = r'''import React,{useState}from'react';import{createRoot}from'react-dom/client';import{api}from'./src/lib/api';import{AppCommandReview}from'./src/components/AppCommandReview';import{LocaleProvider,applyLocale}from'./src/i18n';import './src/index.css';
api.useMemorySession();applyLocale('en');window.fixture={pending:[],executed:[]};function Host(){const[entry,setEntry]=useState(null);window.fixture.open=(session,command,args,workspace,channel)=>{api.applySession(session);setEntry({session,command,args,workspace,channel,key:crypto.randomUUID()})};window.fixture.rotate=(session,original)=>{api.applySession(session);api.applySession(original)};return <LocaleProvider><main>{entry?<AppCommandReview key={entry.key} workspaceId={entry.workspace} channelId={entry.channel} command={entry.command} arguments={entry.args} onPendingChange={pending=>window.fixture.pending.push(pending)} onExecuted={job=>window.fixture.executed.push(job.id)}/>:<p>Ready for a private account</p>}</main></LocaleProvider>}createRoot(document.getElementById('root')).render(<Host/>);'''


def main():
    hashes = lambda: {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}
    before, checks, processes, streams, errors, writes, external = hashes(), {}, [], [], [], [], []
    stage = 'startup'; REPORT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='freecollab-app-command-review-') as directory:
        private=Path(directory)
        try:
            api_port, app_port=p.port(),p.port();base=f'http://127.0.0.1:{api_port}';app=f'http://127.0.0.1:{app_port}';database=private/'proof.db'
            environment=os.environ.copy();environment.update(ENVIRONMENT='test',DATABASE_URL='sqlite:///'+str(database),STORAGE_PATH=str(private/'files'),STORAGE_BACKEND='local',REDIS_URL='',JWT_SECRET=secrets.token_urlsafe(48),ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),MESSAGE_ENCRYPTION_KEYS=json.dumps({'fixture':base64.b64encode(secrets.token_bytes(32)).decode()}),MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='fixture',PUBLIC_APP_URL=app,CORS_ORIGINS=json.dumps([app]),MATRIX_ENABLED='false',MAIL_DELIVERY_MODE='disabled',COPILOT_ENABLED='false',SYNC_ENABLED='false',PYTHONPATH=str(ROOT/'backend'))
            launcher=private/'api.py';launcher.write_text('from app.main import socket_app\nimport uvicorn\nuvicorn.run(socket_app,host="127.0.0.1",port='+str(api_port)+',access_log=False,proxy_headers=False)\n')
            vite=p.create_vite(private,base,app_port)
            (private/'frontend/index.html').write_text('<!doctype html><html lang="en"><head><meta charset="utf-8"></head><body><div id="root"></div><script type="module" src="/entry.tsx"></script></body></html>')
            (private/'frontend/entry.tsx').write_text(ENTRY)
            for command,name,cwd in [([PYTHON,str(launcher)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',ROOT/'frontend')]:
                stream=(private/name).open('w');streams.append(stream);processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            p.ready(base+'/ready',processes);p.ready(app+'/',processes)
            owner=p.native_user(base,database,'App command owner');other=p.native_user(base,database,'App command other actor')
            workspace=p.api(base,'POST','/workspaces',owner,{'name':'Private command workspace'})
            channel=p.api(base,'POST','/channels',owner,{'name':'Reviewed commands','workspace_id':workspace['id']})
            # A literal public destination exercises real destination admission
            # without depending on external DNS. No worker/provider call runs.
            install={'manifest':{'app_id':'fixture.receiver','name':'Private receiver','version':'1.0.0','description':'A genuine installed command', 'scopes':['commands:invoke'],'commands':[{'name':'review','description':'Send only approved text','arguments':[{'name':'text','label':'Reviewed text','required':True,'max_length':2000}]}]},'channel_ids':[channel['id']],'endpoint':'https://8.8.8.8/freecollab-fixture','authorization':''}
            made=p.api(base,'POST',f"/workspaces/{workspace['id']}/apps",owner,install)['installation']
            catalogue_path=f"/workspaces/{workspace['id']}/channels/{channel['id']}/apps/commands"
            catalogue=p.api(base,'GET',catalogue_path,owner)['commands'][0]
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or '/usr/bin/chromium',headless=True,args=['--no-sandbox'])
                context=browser.new_context(viewport={'width':1000,'height':850},reduced_motion='reduce');page=context.new_page();page.set_default_timeout(30000)
                page.on('pageerror',lambda failure:errors.append(str(failure)))
                def track(request):
                    parts=urlsplit(request.url)
                    if parts.hostname not in ('127.0.0.1','localhost'):external.append(parts.hostname)
                    if request.method=='POST' and '/apps/commands/' in parts.path:writes.append(parts.path)
                page.on('request',track);page.goto(app,wait_until='networkidle');page.wait_for_function('()=>!!window.fixture?.open')
                def open_review(command,text):
                    page.evaluate('(values)=>window.fixture.open(...values)',[owner,command,{'text':text},workspace['id'],channel['id']])
                    expect(page.get_by_role('heading',name='Private receiver · /review',exact=True)).to_be_visible()
                    expect(page.get_by_role('textbox',name='Reviewed text',exact=True)).to_have_value(text)
                    expect(page.get_by_role('textbox',name='Reviewed text',exact=True)).to_be_enabled()
                approve=page.get_by_role('button',name='Approve and run command',exact=True)
                consent=page.get_by_role('checkbox',name='I reviewed these exact arguments and approve sharing them with this installed app.',exact=True)
                stage='real catalogue without automatic delivery';open_review(catalogue,'Initial explicit arguments')
                expect(approve).to_be_disabled();assert not writes
                page.wait_for_function('()=>fixture.pending.at(-1)===true')
                checks['opening_real_review_keeps_initial_argument_draft_pending_and_queues_nothing']=True
                stage='edited arguments require fresh disclosure';consent.check();expect(approve).to_be_enabled()
                page.get_by_role('textbox',name='Reviewed text',exact=True).fill('Only the edited reviewed arguments')
                expect(consent).not_to_be_checked();expect(approve).to_be_disabled();assert not writes
                checks['editing_arguments_revokes_the_old_confirmation_without_external_calls']=True
                consent.check();approve.click();expect(page.get_by_role('status')).to_contain_text('Command queued')
                page.wait_for_function('()=>fixture.pending.at(-1)===false')
                assert len(writes)==1
                jobs=lambda:p.api(base,'GET',f"/workspaces/{workspace['id']}/apps/{made['id']}/jobs",owner)['jobs']
                assert len(jobs())==1
                checks['explicit_approval_queues_one_actual_encrypted_outbox_job_and_clears_pending_on_ack']=True
                stage='canonical recipient changed after review';open_review(catalogue,'Stale recipient must not receive this')
                changed=p.api(base,'PUT',f"/workspaces/{workspace['id']}/apps/{made['id']}",owner,{**install,'expected_generation':made['generation'],'endpoint':'https://8.8.8.8/changed-fixture'})['installation']
                consent.check();approve.click();expect(page.get_by_role('alert')).to_contain_text('installed app changed')
                assert len(writes)==1 and len(jobs())==1
                checks['changed_installation_generation_is_rejected_before_queueing_the_stale_review']=True
                catalogue=p.api(base,'GET',catalogue_path,owner)['commands'][0]
                stage='unknown response preserves idempotency';open_review(catalogue,'Exactly one request despite lost response')
                pattern='**/api'+catalogue_path+'/'+made['id']
                def lose_response(route):
                    response=route.fetch();assert response.status==202;route.abort()
                page.route(pattern,lose_response);consent.check();approve.click();expect(page.get_by_role('alert')).to_be_visible()
                assert len(jobs())==2 and page.evaluate('()=>fixture.pending.at(-1)') is True
                page.unroute(pattern,lose_response);approve.click();expect(page.get_by_role('status')).to_contain_text('Command queued')
                assert len(jobs())==2 and len(writes)==3
                checks['lost_native_queue_response_retries_the_same_request_without_duplicate_job']=True
                stage='account ABA invalidates exact review';open_review(catalogue,'Do not leak after account transition')
                page.evaluate('(sessions)=>window.fixture.rotate(...sessions)',[other,owner])
                expect(page.get_by_role('textbox',name='Reviewed text',exact=True)).to_have_value('');expect(approve).to_be_disabled()
                assert len(jobs())==2 and len(writes)==3
                checks['real_account_ABA_scrubs_arguments_and_never_revives_the_old_approval']=True
                page.set_viewport_size({'width':320,'height':800});expect(page.get_by_role('heading',name='Private receiver · /review',exact=True)).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                checks['review_remains_usable_at_320px_without_horizontal_overflow']=True
                page.screenshot(path=str(REPORT.with_suffix('.png')))
                assert not errors and not external,(errors,external)
                context.close();browser.close()
            assert hashes()==before,'Reviewed component/backend inputs changed during proof'
            REPORT.write_text(json.dumps({'ok':True,'checks':checks,'source_sha256':before,'page_errors':errors,'external_destinations':external,'queue_requests':len(writes),'scope':__doc__},indent=2)+'\n')
            print(json.dumps({'ok':True,'checks':len(checks),'report':str(REPORT)}))
        except Exception as failure:
            REPORT.write_text(json.dumps({'ok':False,'stage':stage,'error':str(failure)[:2000],'checks':checks,'source_sha256':before,'page_errors':errors,'external_destinations':external},indent=2)+'\n')
            for name in ('api.log','vite.log'):
                if (private/name).exists():(REPORT.parent/name).write_bytes((private/name).read_bytes())
            raise
        finally:
            p.stop(processes)
            for stream in streams:stream.close()


if __name__=='__main__':main()
