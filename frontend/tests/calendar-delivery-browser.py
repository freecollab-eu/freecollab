#!/usr/bin/env python3
"""Actual calendar review/queue/worker/ICS UI against owned Google/Graph TLS.

No customer credentials or live provider claims. The disposable launchers change
only public DNS resolution and CONNECT proxy selection for the exact two vendor
hosts; production TLS, Host/SNI, destination validation and request code run.
"""
import base64
from copy import deepcopy
from datetime import datetime, timedelta
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
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from tests.provider_calendar_fixture_013 import calendar_https, CONFIG, CREDS, FIELDS
from app.services import remote_transport
spec = importlib.util.spec_from_file_location('calendar_helpers', Path(__file__).with_name('customer-sso-browser.py'))
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
REPORT = Path(os.getenv('CALENDAR_DELIVERY_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/calendar-delivery-013-browser.json')))
PYTHON = str(ROOT / 'backend/.venv/bin/python')


def main():
    stage, checks, processes, streams = 'startup', {}, [], []
    owned = ['frontend/src/components/CalendarDeliveryPanel.tsx', 'frontend/src/lib/calendar-delivery-api.ts',
        'frontend/src/styles/calendar-delivery.css', 'frontend/src/i18n/calendar-delivery.sv.ts',
        'backend/app/api/calendar_delivery.py', 'backend/app/services/calendar_delivery.py',
        'backend/app/services/calendar_subscriptions.py', 'backend/app/models/calendar_delivery.py',
        'backend/app/services/provider_calendar.py', 'backend/app/services/remote_providers.py',
        'backend/app/services/remote_sync.py', 'backend/tests/provider_calendar_fixture_013.py',
        'frontend/tests/calendar-delivery-browser.py']
    hashes = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in owned}
    with tempfile.TemporaryDirectory(prefix='freecollab-calendar-delivery-') as directory:
        private = Path(directory)
        patch = pytest.MonkeyPatch(); fixture = calendar_https.__wrapped__(private, patch); state = next(fixture)
        try:
            proxy = remote_transport.pinned.getproxies()['https']
            api_port, app_port = p.port(), p.port(); base, app = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
            database = private/'proof.db'
            environment = os.environ.copy()
            environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///'+str(database), STORAGE_PATH=str(private/'files'), STORAGE_BACKEND='local', REDIS_URL='',
                JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), ACCOUNT_SECURITY_KEY_FILE=str(private/'account.key'),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof', MESSAGE_ENCRYPTION_KEY_FILE=str(private/'keys.json'),
                PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]), MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled', OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', SYNC_ENABLED='true',
                OTRUST_ENABLED='false', COPILOT_ENABLED='false', CLOUD_FILES_ENABLED='false', EXTERNAL_MEETINGS_ENABLED='false', PYTHONPATH=str(ROOT/'backend'))
            transport = ('from app.services import remote_transport\n'
                'remote_transport.pinned.getproxies=lambda:'+repr({'https':proxy})+'\n'
                'def addresses(host,timeout=5):\n assert host in {"www.googleapis.com","graph.microsoft.com"}\n return ["8.8.8.8"]\n'
                'remote_transport.pinned.public_addresses=addresses\n')
            launcher = private/'api.py'
            # Let the delivery panel load before the actual native event read.
            # This preserves the real response and reproduces a slower catalog.
            events_requested, events_release = private/'calendar-events-requested', private/'calendar-events-release'
            launcher.write_text(transport+'from app.main import app,socket_app\nimport asyncio\nfrom pathlib import Path\n'
                '@app.middleware("http")\nasync def delayed_native_events(request,call_next):\n'
                ' if request.method=="GET" and request.url.path.startswith("/channels/") and request.url.path.endswith("/integrations/calendar"):\n'
                '  Path('+repr(str(events_requested))+').touch()\n'
                '  while not Path('+repr(str(events_release))+').exists(): await asyncio.sleep(.01)\n'
                ' return await call_next(request)\n'
                'import uvicorn\nuvicorn.run(socket_app,host="127.0.0.1",port='+str(api_port)+',access_log=False,proxy_headers=False)\n')
            worker = private/'worker.py'
            worker.write_text(transport+'from app import main\nfrom app.database import engine\nfrom app.services.integration_worker import run_once\nprint(run_once(engine,sync_batch=lambda session:0),flush=True)\n')
            vite = p.create_vite(private,base,app_port)
            vite.write_text(vite.read_text().replace('strictPort:true,proxy:','strictPort:true,fs:{allow:'+json.dumps([str(private/'frontend'),str(ROOT/'frontend')])+'},proxy:'))
            for command,name,cwd in [([PYTHON,str(launcher)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',ROOT/'frontend')]:
                stream=(private/name).open('w');streams.append(stream);processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            p.ready(base+'/ready',processes);p.ready(app+'/',processes)
            owner=p.native_user(base,database,'Calendar Delivery Owner',mfa=True);peer=p.native_user(base,database,'Calendar Delivery Peer')
            workspace=p.api(base,'POST','/workspaces',owner,{'name':'Calendar delivery studio'})
            invite=p.api(base,'POST','/workspaces/'+workspace['id']+'/invites',owner,{'role':'member'})
            p.api(base,'POST','/workspaces/invites/accept',peer,{'token':invite['token']})
            channel=p.api(base,'POST','/channels',owner,{'name':'Reviewed schedule','workspace_id':workspace['id'],'member_ids':[peer['user_id']]})
            day=(datetime.utcnow()+timedelta(days=2)).strftime('%Y-%m-%d')
            fields={**FIELDS,'title':'Deliberate weekly planning','description':'Private provider discussion','location':'Private studio','starts_at':day+'T09:00:00Z','ends_at':day+'T09:30:00Z'}
            event=p.api(base,'POST',f"/channels/{channel['id']}/integrations/calendar",owner,{'id':secrets.token_hex(16),'fields':fields})
            seed=private/'seed.json';seed.write_text(json.dumps({'workspace':workspace['id'],'channel':channel['id'],'user':owner['user_id']}))
            seeder=private/'seed.py';seeder.write_text('from app import main\nfrom app.database import engine\nfrom sqlmodel import Session\nfrom app.models.remote_sync import RemoteSyncConnection\nfrom app.services.remote_sync import save_credentials\nimport json\nv=json.load(open('+repr(str(seed))+'))\n'+
                'with Session(engine) as s:\n'+''.join(' row=RemoteSyncConnection(id='+repr(provider)+',workspace_id=v["workspace"],channel_id=v["channel"],created_by=v["user"],grant_user_id=v["user"],name='+repr('Customer '+provider+' calendar')+',provider='+repr(provider)+',kind="calendar",config_json='+repr(json.dumps(CONFIG[provider]))+',enabled=True,tested_revision=1,status="ready")\n save_credentials(row,'+repr({**CREDS,'scope':'https://www.googleapis.com/auth/calendar.events' if provider=='google' else 'Calendars.ReadWrite'})+')\n s.add(row)\n' for provider in ['google','microsoft'])+' s.commit()\n')
            subprocess.run([PYTHON,str(seeder)],cwd=ROOT/'backend',env=environment,check=True,capture_output=True,timeout=45)
            def tick():
                result=subprocess.run([PYTHON,str(worker)],cwd=ROOT/'backend',env=environment,check=True,capture_output=True,text=True,timeout=90)
                if "'calendar-delivery': 1" not in result.stdout:
                    print('JOB DIAGNOSTIC '+json.dumps(delivery_state()['jobs']),flush=True)
                    raise AssertionError(result.stdout+result.stderr)
            def delivery_state():return p.api(base,'GET',f"/channels/{channel['id']}/calendar/provider-delivery",owner)
            assert len(delivery_state()['connections'])==2
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or '/usr/bin/chromium',headless=True,args=['--no-sandbox'])
                context=browser.new_context(viewport={'width':1440,'height':1080},reduced_motion='reduce');page=context.new_page();page.set_default_timeout(40000)
                errors=[]
                def runtime_error(error):
                    errors.append(str(error));print('BROWSER ERROR '+str(error),flush=True)
                page.on('pageerror',runtime_error)
                try:p.login(page,app,owner,workspace)
                except Exception:
                    print('STARTUP BODY '+page.locator('body').inner_text()[:2000],flush=True)
                    raise
                page.get_by_role('button',name='Integrations',exact=True).click();panel=page.get_by_role('dialog',name='Integrations',exact=True)
                panel.get_by_role('navigation',name='Integration sections').get_by_role('button',name='Calendar',exact=True).click();panel.get_by_label('Channel',exact=True).select_option(channel['id'])
                panel.get_by_role('button',name='External calendars & subscription links',exact=True).click();delivery=panel.get_by_role('region',name='External calendar delivery and subscriptions',exact=True)
                try:expect(delivery.get_by_label('Provider calendar connection',exact=True)).to_be_visible(timeout=5000)
                except Exception:
                    print('DELIVERY MARKUP '+panel.locator('.fc-calendar-delivery').inner_html(),flush=True)
                    REPORT.parent.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(REPORT.with_suffix('.failure.png')),full_page=True)
                    raise
                stage='late native event requires a visible explicit keyboard selection'
                deadline=time.monotonic()+5
                while not events_requested.exists() and time.monotonic()<deadline:page.wait_for_timeout(50)
                assert events_requested.exists(),'The actual initial event read did not reach the delay gate'
                chooser=delivery.get_by_label('Event to review',exact=True)
                review_button=delivery.get_by_role('button',name='Review calendar action',exact=True)
                expect(chooser).to_have_value('');expect(chooser.locator('option')).to_have_count(1)
                expect(chooser.locator('option:checked')).to_have_text('Choose an event');expect(review_button).to_be_disabled()
                assert all(connection['ready'] for connection in delivery_state()['connections'])
                events_release.touch()
                expect(chooser.locator('option[value="'+event['id']+'"]')).to_have_text(fields['title'])
                expect(chooser).to_have_value('');expect(chooser.locator('option:checked')).to_have_text('Choose an event')
                expect(review_button).to_be_disabled()
                chooser.focus();chooser.press('ArrowDown')
                expect(chooser).to_have_value(event['id']);expect(review_button).to_be_enabled()
                checks['late_real_native_event_has_truthful_placeholder_and_actual_keyboard_review_selection']=True
                stage='reviewed Google and Graph invitations, actual queue/worker/TLS'
                for provider in ['google','microsoft']:
                    delivery.get_by_label('Provider calendar connection',exact=True).select_option(provider)
                    delivery.get_by_label('External attendee emails, one per line',exact=True).fill('invited@example.net')
                    delivery.get_by_role('button',name='Review calendar action',exact=True).click()
                    review=delivery.get_by_role('region',name='Reviewed calendar provider action',exact=True)
                    expect(review.get_by_text('invited@example.net',exact=True)).to_be_visible()
                    expect(review.get_by_role('button',name='Approve and queue provider action',exact=True)).to_be_disabled()
                    assert not state.events[provider]
                    review.get_by_role('checkbox').check();review.get_by_role('button',name='Approve and queue provider action',exact=True).click()
                    expect(delivery.get_by_role('status')).to_contain_text('Calendar action queued')
                    tick();delivery.get_by_role('button',name='Refresh calendar delivery status',exact=True).click()
                    expect(delivery.get_by_role('list',name='Calendar delivery receipts',exact=True).locator('li').first).to_contain_text('succeeded')
                    assert len(state.events[provider])==1
                    remote=next(iter(state.events[provider].values()));assert remote.get('recurrence') and len(state.instances[provider][remote['id']])==4
                    assert 'invited@example.net' in json.dumps(remote['attendees'])
                    checks[provider+'_actual_react_review_consent_queue_worker_trusted_https_recurring_invitations']=True
                assert state.connects and all(row==b'CONNECT 8.8.8.8:443 HTTP/1.1' for row in state.connects)
                assert {'www.googleapis.com','graph.microsoft.com'}<=set(state.sni)
                stage='provider status and own delegated RSVP'
                # This owned scenario represents a whole-series personal RSVP,
                # not an instance-specific attendee override. Real per-instance
                # overrides have their separate fail-closed protocol tests.
                original_versioned=state.versioned
                def series_response(provider,value):
                    result=original_versioned(provider,value)
                    if value.get('id') in state.events[provider]:
                        for instance in state.instances[provider][value['id']].values():
                            instance['attendees']=deepcopy(value.get('attendees',[]))
                    return result
                state.versioned=series_response
                for provider in ['google','microsoft']:
                    remote=next(iter(state.events[provider].values()))
                    if provider=='google':
                        remote['organizer']={'email':'organizer@example.net','self':False};remote['attendees'][0].update(self=True,responseStatus='needsAction')
                    else:remote['isOrganizer']=False
                    state.versioned(provider,remote)
                    delivery.get_by_label('Provider calendar connection',exact=True).select_option(provider)
                    delivery.get_by_label('Provider action',exact=True).select_option('refresh');delivery.get_by_role('button',name='Review calendar action',exact=True).click();delivery.get_by_role('button',name='Queue provider read',exact=True).click()
                    tick();delivery.get_by_role('button',name='Refresh calendar delivery status',exact=True).click()
                    delivery.get_by_label('Provider action',exact=True).select_option('respond');delivery.get_by_label('My provider response',exact=True).select_option('accepted');delivery.get_by_role('button',name='Review calendar action',exact=True).click()
                    review=delivery.get_by_role('region',name='Reviewed calendar provider action',exact=True);review.get_by_role('checkbox').check();review.get_by_role('button',name='Approve and queue provider action',exact=True).click();tick()
                    assert remote['attendees'][0]['responseStatus']=='accepted' if provider=='google' else remote['responseStatus']['response']=='accepted'
                    delivery.get_by_role('button',name='Refresh calendar delivery status',exact=True).click()
                    checks[provider+'_explicit_delegated_self_rsvp_after_fresh_provider_read']=True
                stage='explicit organizer cancellation and provider notification'
                for provider in ['google','microsoft']:
                    remote=next(iter(state.events[provider].values()))
                    if provider=='google':remote['organizer']['self']=True
                    else:remote['isOrganizer']=True
                    state.versioned(provider,remote)
                    delivery.get_by_label('Provider calendar connection',exact=True).select_option(provider)
                    delivery.get_by_label('Provider action',exact=True).select_option('refresh');delivery.get_by_role('button',name='Review calendar action',exact=True).click();delivery.get_by_role('button',name='Queue provider read',exact=True).click();tick()
                    delivery.get_by_role('button',name='Refresh calendar delivery status',exact=True).click()
                    delivery.get_by_label('Provider action',exact=True).select_option('cancel');delivery.get_by_role('button',name='Review calendar action',exact=True).click()
                    review=delivery.get_by_role('region',name='Reviewed calendar provider action',exact=True);expect(review.get_by_role('button',name='Approve and queue provider action',exact=True)).to_be_disabled()
                    review.get_by_role('checkbox').check();review.get_by_role('button',name='Approve and queue provider action',exact=True).click();tick()
                    assert not state.events[provider]
                    delivery.get_by_role('button',name='Refresh calendar delivery status',exact=True).click()
                    checks[provider+'_explicit_organizer_cancellation_notification_and_receipt']=True
                stage='public Busy-only link, title opt-in and immediate revocation'
                feed=delivery.locator('form.fc-calendar-feed-form');feed.get_by_label('Confirm password',exact=True).fill(p.PASSWORD)
                feed.get_by_label('Authentication or recovery code (if enabled)',exact=True).fill(owner['codes'].pop(0))
                expect(feed.get_by_role('button',name='Create subscription link',exact=True)).to_be_disabled()
                feed.get_by_role('checkbox',name='I explicitly authorize public access to this selected channel schedule for the displayed lifetime.',exact=True).check();feed.get_by_role('button',name='Create subscription link',exact=True).click()
                url=delivery.get_by_label('New calendar subscription URL',exact=True);expect(url).to_have_value(__import__('re').compile(r'.*\.ics$'));link=url.input_value()
                with urlopen(link,timeout=15) as response:
                    ics=response.read().decode();assert response.headers['Referrer-Policy']=='no-referrer' and 'no-store' in response.headers['Cache-Control']
                    assert 'SUMMARY:Busy' in ics and 'Deliberate weekly planning' not in ics and 'Private studio' not in ics and 'Private provider discussion' not in ics and 'invited@example.net' not in ics
                feed.get_by_label('Confirm password',exact=True).fill(p.PASSWORD);feed.get_by_label('Authentication or recovery code (if enabled)',exact=True).fill(owner['codes'].pop(0));delivery.get_by_role('button',name='Revoke subscription',exact=True).click();expect(url).to_have_count(0)
                try:urlopen(link,timeout=15);raise AssertionError('Revoked link remained readable')
                except HTTPError as error:assert error.code==404
                feed.get_by_label('Confirm password',exact=True).fill(p.PASSWORD);feed.get_by_label('Authentication or recovery code (if enabled)',exact=True).fill(owner['codes'].pop(0));feed.get_by_role('checkbox',name='Include event titles in the public subscription',exact=True).check();feed.get_by_role('checkbox',name='I explicitly authorize public access to this selected channel schedule for the displayed lifetime.',exact=True).check();feed.get_by_role('button',name='Create subscription link',exact=True).click()
                expect(url).to_have_count(1);expect(url).to_have_value(__import__('re').compile(r'.*\.ics$'));title_link=url.input_value()
                with urlopen(title_link,timeout=15) as response:
                    titled=response.read().decode();assert 'SUMMARY:Deliberate weekly planning' in titled and 'invited@example.net' not in titled and 'Private studio' not in titled
                checks['real_ui_explicit_busy_only_and_title_optin_public_ics_with_reauth_and_revocation']=True
                stage='small screen and local secret storage boundaries'
                page.set_viewport_size({'width':320,'height':850});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                with sqlite3.connect(database) as connection:
                    rows=connection.execute('SELECT encrypted_result FROM calendarproviderpublication').fetchall();assert rows and all('invited@example.net' not in row[0] for row in rows)
                    jobs=connection.execute('SELECT status,encrypted_payload,claims_json FROM calendarproviderjob').fetchall();assert all(status=='succeeded' and payload is None and claims=='{}' for status,payload,claims in jobs)
                assert not errors,errors
                REPORT.parent.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(REPORT.with_suffix('.png')),full_page=True,mask=[url])
                checks['320px_theme_aware_no_overflow_runtime_errors_and_scrubbed_encrypted_receipts']=True
                stage='Swedish calendar controls and localized receipts'
                p.api(base,'PUT','/preferences/language',owner,{'locale':'sv'})
                page.evaluate('key=>{localStorage.setItem(key,"sv");window.dispatchEvent(new StorageEvent("storage",{key}));}', 'freecollab.language:'+owner['user_id'])
                expect(page.locator('html')).to_have_attribute('lang','sv')
                swedish=page.locator('.fc-calendar-delivery');expect(swedish.get_by_role('button',name='Externa kalendrar och prenumerationslänkar',exact=True)).to_be_visible()
                expect(swedish.get_by_label('Händelse att granska',exact=True).locator('option[value=""]')).to_have_text('Välj en händelse')
                expect(swedish.get_by_role('list',name='Kvitton för kalenderåtgärder',exact=True)).to_contain_text('Slutförd')
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.screenshot(path=str(REPORT.with_suffix('.sv.png')),full_page=True,mask=[swedish.locator('.fc-calendar-new-link input')])
                checks['saved_swedish_preference_updates_real_calendar_controls_and_receipts_at_320px']=True
                browser.close()
        except Exception:
            print('Calendar delivery proof failed during '+stage,flush=True)
            for name in ['api.log','vite.log']:
                path=private/name
                if path.exists():print(path.read_text()[-4000:],flush=True)
            raise
        finally:
            (private/'calendar-events-release').touch()
            p.stop(processes)
            for stream in streams:stream.close()
            fixture.close();patch.undo()
            REPORT.parent.mkdir(parents=True,exist_ok=True);REPORT.write_text(json.dumps({'stage':stage,'checks':checks,'synthetic_owned_providers_only':True,'source_hashes_at_start':hashes,'source_hashes_at_end':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in owned}},indent=2))
    print(json.dumps(checks,indent=2),flush=True)


if __name__=='__main__':main()
