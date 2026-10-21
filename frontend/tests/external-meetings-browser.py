#!/usr/bin/env python3
"""Actual provider panel and OAuth UI using private hostname-verified HTTPS.
Native sessions and provider consent are explicit local fixtures, never live
provider accounts. Production adapters use real TLS and the pinned CONNECT path.
"""
import base64
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('private_meeting_harness', Path(__file__).with_name('meetings-browser.py'))
harness = importlib.util.module_from_spec(spec); spec.loader.exec_module(harness)
helpers = harness.helpers
REPORT = Path(os.getenv('EXTERNAL_MEETINGS_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/external-meetings-browser.json')))
SOURCE_FILES = ['backend/app/models/external_meetings.py','backend/app/services/external_meetings.py','backend/app/services/external_meeting_providers.py','backend/app/api/external_meetings.py','backend/tests/test_external_meetings.py','backend/tests/test_external_meeting_protocols.py','backend/tests/external_meeting_fixture.py','backend/tests/external_meeting_browser_api.py','frontend/src/lib/external-meetings-api.ts','frontend/src/components/ExternalMeetingsPanel.tsx','frontend/src/styles/external-meetings.css','frontend/src/i18n/external-meetings.sv.ts','frontend/src/i18n/legacy-4.sv.ts','frontend/tests/external-meetings-browser.py','docs/EXTERNAL_MEETINGS.md']

def frontend(private, api_base, port):
    launcher = harness.create_frontend(private, api_base, port)
    source = """import React from 'react';import{createRoot}from'react-dom/client';
import{ExternalMeetingsPanel}from PANEL;import{LocaleProvider,LanguagePicker}from I18N;
import './base.css';import ACCOUNT;import CONTENT;
createRoot(document.getElementById('root')).render(<React.StrictMode><LocaleProvider><div className="proof-meeting"><LanguagePicker compact/><ExternalMeetingsPanel workspaceId="team" channelId="room" isAdmin/></div></LocaleProvider></React.StrictMode>);
"""
    for key, value in {'PANEL':'frontend/src/components/ExternalMeetingsPanel.tsx','I18N':'frontend/src/i18n/index.tsx','ACCOUNT':'frontend/src/styles/account.css','CONTENT':'frontend/src/styles/integration-content.css'}.items():
        source = source.replace(key, json.dumps(str(ROOT / value)))
    (private / 'web/entry.tsx').write_text(source)
    launcher.write_text(launcher.read_text().replace('strictPort:true,fs:', 'strictPort:true,hmr:false,watch:{ignored:["**"]},fs:'))
    return launcher

def get(url):
    with urlopen(url, timeout=10) as response: return json.load(response)

def main():
    results, processes, streams, stage = {}, [], [], 'private services'
    with tempfile.TemporaryDirectory(prefix='freecollab-external-meetings-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = helpers.port(), helpers.port()
            api_base, app = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
            env = os.environ.copy()
            env.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(private/'proof.db'), STORAGE_PATH=str(private/'files'), JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof', EXTERNAL_MEETINGS_ENABLED='true', MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled', OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', OTRUST_ENABLED='false', SYNC_ENABLED='false', PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]), PYTHONPATH=str(ROOT/'backend'), PROOF_DIRECTORY=str(private), PROOF_API_PORT=str(api_port))
            env['NO_PROXY'] = ','.join(filter(None, [env.get('NO_PROXY',''), 'localhost', '127.0.0.1', '::1']))
            python = os.getenv('BACKEND_PYTHON') or str(ROOT/'backend/.venv/bin/python')
            commands = [([python, str(ROOT/'backend/tests/external_meeting_browser_api.py')], 'api.log', ROOT/'backend'), (['node', str(frontend(private, api_base, app_port))], 'vite.log', ROOT/'frontend')]
            for command, name, cwd in commands:
                stream = (private/name).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=env, stdout=stream, stderr=stream, start_new_session=True))
            helpers.ready(api_base+'/ready', processes); helpers.ready(app+'/', processes)
            token = json.loads((private/'access.json').read_text())['token']
            with sync_playwright() as playwright:
                launch = {'headless':True, 'args':['--no-sandbox']}
                executable = os.getenv('CHROMIUM_PATH') or shutil.which('chromium')
                if executable: launch['executable_path'] = executable
                browser = playwright.chromium.launch(**launch)
                context = browser.new_context(viewport={'width':1400,'height':1000}, locale='en-US')
                context.add_init_script('localStorage.setItem("freecollab_token",'+json.dumps(token)+');localStorage.setItem("freecollab_user_id","owner");')
                errors, unexpected = [], []
                def authorize(route):
                    url = urlsplit(route.request.url); query = parse_qs(url.query)
                    provider = 'google_meet' if url.hostname=='accounts.google.com' else 'microsoft_teams' if url.hostname=='login.microsoftonline.com' else 'zoom'
                    payload = json.dumps({'provider':provider,'challenge':query['code_challenge'][0],'client_id':query['client_id'][0]}).encode()
                    with urlopen(Request(api_base+'/fixture/issue-code', data=payload, headers={'Content-Type':'application/json'}), timeout=10) as response:
                        code = json.load(response)['code']
                    callback = query['redirect_uri'][0]+'?'+urlencode({'code':code,'state':query['state'][0]})
                    route.fulfill(status=200, content_type='text/html', body='<h1>Private customer OAuth protocol fixture</h1><p>No real provider account is used.</p><a href="'+callback.replace('&','&amp;')+'">Authorize private fixture account</a>')
                for host in ['accounts.google.com','login.microsoftonline.com','zoom.us']:
                    context.route('https://'+host+'/**', authorize)
                page = context.new_page(); page.set_default_timeout(30000)
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: unexpected.append(request.url) if urlsplit(request.url).hostname not in ['127.0.0.1','localhost','accounts.google.com','login.microsoftonline.com','zoom.us'] else None)
                try:
                    stage = 'customer configuration, OAuth consent, test and create for three providers'
                    for provider,label in [('google_meet','Google Meet'),('microsoft_teams','Microsoft Teams'),('zoom','Zoom')]:
                        page.goto(app, wait_until='networkidle')
                        page.get_by_role('button',name='Add meeting provider',exact=True).click()
                        form = page.get_by_role('form',name='Configure customer meeting provider')
                        # Changing customer applications clears every native/provider credential.
                        form.get_by_label('Provider',exact=True).select_option(provider)
                        form.get_by_label('Connection name',exact=True).fill('Private '+label)
                        page.get_by_label('Your current FreeCollab password',exact=True).fill('private-ui-password')
                        form.get_by_label('Customer OAuth client ID',exact=True).fill('private-fixture-'+provider)
                        form.get_by_label('Customer OAuth client secret',exact=True).fill('private-protocol-fixture-client-secret')
                        if provider!='zoom': form.get_by_label('Selected calendar ID',exact=True).fill('selected-calendar')
                        if provider=='microsoft_teams': form.get_by_label('Microsoft tenant ID',exact=True).fill('11111111-2222-3333-4444-555555555555')
                        form.get_by_label('I authorize explicit meeting creation for this channel with this customer app. No background access to channel content is granted.',exact=True).check()
                        form.get_by_role('button',name='Save customer provider configuration',exact=True).click()
                        connection = page.locator('.fc-external-meeting-connections>li').filter(has=page.get_by_text('Private '+label,exact=True))
                        expect(connection).to_be_visible()
                        page.get_by_label('Your current FreeCollab password',exact=True).fill('private-ui-password')
                        connection.get_by_role('button',name='Authorize customer account',exact=True).click()
                        page.get_by_role('link',name='Authorize private fixture account',exact=True).click()
                        expect(page.get_by_role('status').filter(has_text='Provider authorization saved.')).to_be_visible()
                        page.get_by_label('Your current FreeCollab password',exact=True).fill('private-ui-password')
                        connection = page.locator('.fc-external-meeting-connections>li').filter(has=page.get_by_text('Private '+label,exact=True))
                        connection.get_by_role('button',name='Test provider connection',exact=True).click()
                        expect(connection.get_by_text(label+' · Ready',exact=True)).to_be_visible()
                        form = page.get_by_role('form',name='Schedule external meeting')
                        option = form.get_by_label('Meeting provider',exact=True).locator('option').filter(has_text='Private '+label).get_attribute('value')
                        form.get_by_label('Meeting provider',exact=True).select_option(option)
                        form.get_by_label('Meeting title',exact=True).fill('Private '+label+' planning')
                        form.get_by_label('Starts',exact=True).fill('2026-12-01T10:00')
                        form.get_by_label('Ends',exact=True).fill('2026-12-01T11:00')
                        form.get_by_label('Time zone',exact=True).fill('UTC')
                        button = form.get_by_role('button',name='Create provider meeting and calendar entry',exact=True)
                        expect(button).to_be_disabled()
                        form.get_by_label('I choose to disclose this meeting title, time and connected account to the selected provider. No participant invitations will be sent.',exact=True).check()
                        button.click()
                        row = page.locator('.fc-external-meeting-list>li').filter(has=page.get_by_text('Private '+label+' planning',exact=True))
                        expect(row.get_by_role('link',name='Open provider meeting',exact=True)).to_be_visible()
                        results[provider+'_config_oauth_test_explicit_create_calendar_link_ui'] = True
                    stage = 'actual HTTPS uncertain create and read-only recovery'
                    with urlopen(Request(api_base+'/fixture/uncertain-zoom',data=b'{}',headers={'Content-Type':'application/json'}),timeout=10): pass
                    form = page.get_by_role('form',name='Schedule external meeting')
                    form.get_by_label('Meeting title',exact=True).fill('Recover uncertain Zoom')
                    form.get_by_label('I choose to disclose this meeting title, time and connected account to the selected provider. No participant invitations will be sent.',exact=True).check()
                    form.get_by_role('button',name='Create provider meeting and calendar entry',exact=True).click()
                    expect(page.get_by_role('alert')).to_be_visible()
                    row = page.locator('.fc-external-meeting-list>li').filter(has_text='Provider result uncertain')
                    expect(row).to_be_visible()
                    page.get_by_label('I allow the lookup or cancellation I choose below to contact that meeting provider.',exact=True).check()
                    row.get_by_role('button',name='Retrieve provider meeting',exact=True).click()
                    row = page.locator('.fc-external-meeting-list>li').filter(has=page.get_by_text('Recover uncertain Zoom',exact=True))
                    expect(row.get_by_role('link',name='Open provider meeting',exact=True)).to_be_visible()
                    assert get(api_base+'/fixture/report')['posts']=={'google_meet':1,'microsoft_teams':1,'zoom':2}
                    results['actual_https_uncertainty_recovery_no_duplicate_create'] = True
                    stage = 'actual conditional provider cancellation'
                    row.get_by_role('button',name='Cancel provider meeting',exact=True).click()
                    row.get_by_role('button',name='Confirm cancellation',exact=True).click()
                    expect(row.get_by_text('Zoom · Cancelled',exact=True)).to_be_visible()
                    assert get(api_base+'/fixture/report')['deletes']=={'zoom':1}
                    results['actual_https_cancel_and_native_calendar_tombstone_ui'] = True
                    with urlopen(Request(api_base+'/channels/room/integrations/calendar',headers={'Authorization':'Bearer '+token}),timeout=10) as response: calendars=json.load(response)
                    assert len(calendars)==3 and all(record['fields']['title']!='Recover uncertain Zoom' for record in calendars)
                    assert page.evaluate('Object.keys(localStorage).every(k=>!localStorage.getItem(k).includes("private-protocol-fixture-client-secret"))')
                    assert not unexpected
                    results['real_native_calendar_api_no_client_secret_storage_or_live_requests'] = True
                    page.get_by_role('combobox',name='Language',exact=True).select_option('sv')
                    expect(page.get_by_role('heading',name='Mötas på era villkor.',exact=True)).to_be_visible()
                    page.reload(wait_until='networkidle')
                    expect(page.get_by_role('heading',name='Mötas på era villkor.',exact=True)).to_be_visible()
                    results['actual_swedish_ui_language_persists_after_reload_user_titles_unchanged'] = True
                    REPORT.parent.mkdir(parents=True,exist_ok=True)
                    page.screenshot(path=str(REPORT.with_name('external-meetings-desktop.png')),full_page=True)
                    page.set_viewport_size({'width':320,'height':1000})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.screenshot(path=str(REPORT.with_name('external-meetings-mobile.png')),full_page=True)
                    results['responsive_320px_no_horizontal_overflow'] = True
                    assert not errors, 'Browser page errors'
                    results['zero_browser_page_errors'] = True
                    protocol_counts = get(api_base+'/fixture/report')
                finally:
                    context.close(); browser.close()
        except Exception:
            print('FAILED external meeting proof stage: '+stage,file=sys.stderr)
            if 'errors' in locals(): print('Page errors: '+json.dumps(errors),file=sys.stderr)
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None: process.terminate()
            for process in reversed(processes):
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            for stream in streams: stream.close()
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps({'scope':'Production external meeting panel/adapters; private native session and OAuth consent fixtures; actual verified HTTPS/pinned CONNECT; no live providers/accounts or mocked provider API','results':results,'private_protocol_counts':protocol_counts,'source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE_FILES},'cleanup':'Own services/proxy/HTTPS/browser/database/keys removed'},indent=2)+'\n')
    print('External meetings browser proof passed: '+str(len(results))+' checks.')

if __name__=='__main__': main()
