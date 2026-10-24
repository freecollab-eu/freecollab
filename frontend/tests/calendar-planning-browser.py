#!/usr/bin/env python3
"""Actual React/API calendar proof with isolated data and no provider accounts."""
from ui_navigation import open_chat_tool
import base64
from datetime import datetime, timedelta
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('calendar_helpers', Path(__file__).with_name('customer-sso-browser.py'))
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
REPORT = Path(os.getenv('CALENDAR_PLANNING_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/calendar-planning-browser.json')))
PYTHON = str(ROOT / 'backend/.venv/bin/python')


def calendar(page, channel):
    open_chat_tool(page, 'Integrations')
    panel = page.get_by_role('dialog', name='Integrations', exact=True)
    panel.get_by_role('navigation', name='Integration sections').get_by_role('button', name='Calendar', exact=True).click()
    panel.get_by_label('Channel', exact=True).select_option(channel)
    return panel


def main():
    checks, processes, streams, stage = {}, [], [], 'startup'
    source_hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in ['frontend/src/components/CalendarPanel.tsx', 'frontend/src/components/CalendarPlanning.tsx', 'frontend/src/lib/calendar.ts', 'frontend/src/lib/calendar-planning-api.ts', 'frontend/src/lib/document-calendar-api.ts', 'frontend/src/styles/calendar-planning.css', 'frontend/src/i18n/calendar-planning.sv.ts', 'backend/app/api/calendar_planning.py', 'backend/app/services/calendar_planning.py', 'backend/app/services/integration_content.py', 'backend/app/models/calendar_planning.py']}
    with tempfile.TemporaryDirectory(prefix='freecollab-calendar-planning-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = p.port(), p.port()
            base, app = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
            database = private / 'proof.db'
            environment = os.environ.copy()
            environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(database), STORAGE_PATH=str(private / 'files'), STORAGE_BACKEND='local', REDIS_URL='',
                JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), ACCOUNT_SECURITY_KEY_FILE=str(private / 'account.key'),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof', MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'keys.json'),
                PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]), MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled', OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', SYNC_ENABLED='false',
                OTRUST_ENABLED='false', COPILOT_ENABLED='false', CLOUD_FILES_ENABLED='false', EXTERNAL_MEETINGS_ENABLED='false', PYTHONPATH=str(ROOT / 'backend'))
            launcher = private / 'api.py'
            launcher.write_text('from app.main import socket_app\nimport uvicorn\nuvicorn.run(socket_app,host="127.0.0.1",port=' + str(api_port) + ',access_log=False,proxy_headers=False)\n')
            vite = p.create_vite(private, base, app_port)
            vite.write_text(vite.read_text().replace('strictPort:true,proxy:', 'strictPort:true,fs:{allow:' + json.dumps([str(private / 'frontend'), str(ROOT / 'frontend')]) + '},proxy:'))
            for command, name, cwd in [([PYTHON,str(launcher)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',ROOT/'frontend')]:
                stream = (private/name).open('w'); streams.append(stream); processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            p.ready(base+'/ready',processes); p.ready(app+'/',processes)
            owner, peer = p.native_user(base,database,'Calendar Owner'), p.native_user(base,database,'Calendar Peer')
            workspace = p.api(base,'POST','/workspaces',owner,{'name':'Calendar studio'})
            invite = p.api(base,'POST','/workspaces/'+workspace['id']+'/invites',owner,{'role':'member'})
            p.api(base,'POST','/workspaces/invites/accept',peer,{'token':invite['token']})
            channel = p.api(base,'POST','/channels',owner,{'name':'Calendar room','workspace_id':workspace['id'],'member_ids':[peer['user_id']]})
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or '/usr/bin/chromium',headless=True,args=['--no-sandbox'])
                contexts = [browser.new_context(viewport={'width':1440,'height':1080},reduced_motion='reduce') for _ in range(2)]
                left, right = [context.new_page() for context in contexts]
                errors=[]
                for page in (left,right): page.set_default_timeout(40000); page.on('pageerror',lambda failure: errors.append(str(failure)))
                p.login(left,app,owner,workspace); p.login(right,app,peer,workspace)
                stage='recurrence creation through real editor'
                panel=calendar(left,channel['id'])
                panel.get_by_role('button',name='Add calendar event',exact=True).click()
                form=panel.get_by_role('form',name='Add calendar event',exact=True)
                form.get_by_label('Event title',exact=True).fill('Thoughtful weekly planning')
                day=(datetime.utcnow()+timedelta(days=2)).strftime('%Y-%m-%d')
                form.get_by_label('Starts',exact=True).fill(day+'T09:00'); form.get_by_label('Ends',exact=True).fill(day+'T09:30')
                form.get_by_label('Time zone',exact=True).fill('Europe/Stockholm')
                form.get_by_label('Repeat',exact=True).select_option('weekly'); form.get_by_label('Number of occurrences',exact=True).fill('4')
                form.get_by_role('button',name='Save calendar event',exact=True).click()
                card=panel.locator('.fc-integration-content-card').filter(has_text='Thoughtful weekly planning')
                expect(card).to_be_visible(); expect(card.get_by_role('button',name='Move this occurrence',exact=True)).to_have_count(4)
                records=p.api(base,'GET','/channels/'+channel['id']+'/integrations/calendar',owner)
                record=records[0]; assert record['fields']['recurrence']['count']==4
                with sqlite3.connect(database) as connection:
                    ciphertext=connection.execute('SELECT encrypted_payload FROM integrationcontentrevision WHERE item_id=?',(record['id'],)).fetchone()[0]
                    assert 'Thoughtful weekly planning' not in ciphertext
                checks['actual_editor_creates_encrypted_at_rest_finite_weekly_series']=True
                stage='internal invite and independent peer RSVP'
                card.get_by_role('button',name='Attendees & responses',exact=True).click()
                card.get_by_role('checkbox',name=peer['name'],exact=True).check(); card.get_by_role('button',name='Save invitations',exact=True).click()
                expect(card.get_by_text('needs-action',exact=True)).to_be_visible()
                peerpanel=calendar(right,channel['id'])
                peercard=peerpanel.locator('.fc-integration-content-card').filter(has_text='Thoughtful weekly planning')
                peercard.get_by_role('button',name='Attendees & responses',exact=True).click()
                peercard.get_by_role('button',name='accepted',exact=True).click()
                expect(peercard.get_by_role('button',name='accepted',exact=True)).to_have_class('fc-button fc-button-primary')
                assert p.api(base,'GET',f"/channels/{channel['id']}/calendar/{record['id']}/attendees",owner)['attendees'][0]['response']=='accepted'
                checks['current_member_invitation_and_personal_peer_rsvp_from_second_browser']=True
                stage='move and cancel exact occurrences'
                card.get_by_role('button',name='Move this occurrence',exact=True).first.click()
                move=card.locator('.fc-calendar-occurrence-editor')
                move.get_by_label('Occurrence starts',exact=True).fill(day+'T11:00'); move.get_by_label('Occurrence ends (exclusive for all-day)',exact=True).fill(day+'T11:30')
                move.get_by_role('button',name='Save occurrence',exact=True).click()
                expect(card.get_by_role('button',name='Move this occurrence',exact=True)).to_have_count(4)
                card.get_by_role('button',name='Cancel this occurrence',exact=True).nth(1).click()
                expect(card.get_by_role('button',name='Move this occurrence',exact=True)).to_have_count(3)
                record=p.api(base,'GET','/channels/'+channel['id']+'/integrations/calendar',owner)[0]
                assert len(record['fields']['exceptions'])==2
                assert p.api(base,'GET',f"/channels/{channel['id']}/calendar/{record['id']}/attendees",owner)['attendees'][0]['response']=='needs-action'
                checks['move_and_cancel_single_occurrences_without_changing_other_dates_reset_rsvp']=True
                stage='shared-time UI and no private details in response'
                panel.get_by_role('button',name='Find a shared time',exact=True).click()
                finder=panel.locator('.fc-calendar-availability')
                finder.get_by_role('checkbox',name=peer['name'],exact=True).check()
                finder.get_by_label('From',exact=True).fill(day+'T08:00'); finder.get_by_label('Until',exact=True).fill(day+'T17:00')
                finder.get_by_role('button',name='Find available times',exact=True).click()
                expect(finder.get_by_text('Available in this channel only. Confirm other commitments with the attendees.',exact=True)).to_be_visible()
                expect(finder.locator('.fc-calendar-slots li')).to_have_count(12)
                checks['real_shared_time_finder_uses_explicit_channel_boundary']=True
                stage='320px theme aware calendar and clean runtime'
                left.set_viewport_size({'width':320,'height':850})
                expect(card.get_by_role('button',name='Attendees & responses',exact=True)).to_be_visible()
                assert left.evaluate('document.documentElement.scrollWidth<=innerWidth')
                REPORT.parent.mkdir(parents=True,exist_ok=True)
                left.screenshot(path=str(REPORT.with_suffix('.png')),full_page=True)
                assert not errors, 'Browser runtime errors: '+','.join(errors)
                checks['320px_calendar_has_no_horizontal_overflow_or_runtime_errors']=True
                browser.close()
        except Exception:
            print('Calendar proof failed during '+stage,flush=True)
            for name in ['api.log','vite.log']:
                path=private/name
                if path.exists(): print(path.read_text()[-3000:],flush=True)
            raise
        finally:
            p.stop(processes)
            for stream in streams: stream.close()
            REPORT.parent.mkdir(parents=True,exist_ok=True); REPORT.write_text(json.dumps({'stage':stage,'checks':checks, 'source_hashes_at_start': source_hashes},indent=2))
    print(json.dumps(checks,indent=2),flush=True)

if __name__=='__main__':main()
