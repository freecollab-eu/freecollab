#!/usr/bin/env python3
"""Narrow actual-App operator/health repair proof with private real API only."""
import base64
import importlib.util
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
from urllib.parse import urlsplit
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('connection_setup_fixture', ROOT / 'frontend/tests/connection-setup-browser.py')
f = importlib.util.module_from_spec(spec); spec.loader.exec_module(f)
REPORT = Path(os.getenv('CONNECTION_HEALTH_REPORT', str(ROOT.parent / '.freecollab/proof-results/open-release-health-browser.json')))


def no_overflow(page, panel):
    assert panel.evaluate('el=>el.scrollWidth<=el.clientWidth+1'), 'Panel overflows internally'
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'Page overflows horizontally'


def main():
    processes, streams, checks = [], [], f.ProofChecks()
    with tempfile.TemporaryDirectory(prefix='freecollab-health-browser-') as directory:
        private = Path(directory)
        try:
            api_port, web_port = f.port(), f.port(); base = f'http://127.0.0.1:{api_port}'; origin = f'http://127.0.0.1:{web_port}'
            environment = os.environ.copy(); environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(private/'proof.db'), STORAGE_PATH=str(private/'files'), JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof', ACCOUNT_SECURITY_KEY_FILE=str(private/'account.key'), MESSAGE_ENCRYPTION_KEY_FILE=str(private/'message.keys'), MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled', PUBLIC_APP_URL=origin, CORS_ORIGINS=json.dumps([origin]), OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', OTRUST_ENABLED='false', SYNC_ENABLED='true', CLOUD_FILES_ENABLED='false', EXTERNAL_MEETINGS_ENABLED='false', COPILOT_ENABLED='false', PUSH_ENABLED='false', PYTHONPATH=str(ROOT/'backend'), CONNECTION_PROOF_PRIVATE=str(private))
            environment['NO_PROXY'] = ','.join(filter(None,[environment.get('NO_PROXY',''),'127.0.0.1','localhost','::1']))
            f.SENSITIVE.extend([environment[key] for key in ('JWT_SECRET','ACCOUNT_SECURITY_KEY','MESSAGE_ENCRYPTION_KEYS')])
            f.controls(private)
            launcher = f.private_api_launcher(private, api_port)
            web, vite = f.frontend_snapshot(private, base, web_port)
            frozen = {'frontend_src': f.source_hash(web/'src'), 'backend_app': f.source_hash(ROOT/'backend/app','*.py')}
            for command, name, cwd in [([str(ROOT/'backend/.venv/bin/python'),str(launcher)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',web)]:
                stream = (private/name).open('w'); streams.append(stream); processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            f.ready(base+'/ready',processes); f.ready(origin,processes)
            email = 'health-owner-'+secrets.token_hex(6)+'@example.com'
            owner = f.api(base,'POST','/auth/register',data={'name':'Linnea Berg','email':email,'password':f.PASSWORD}); owner['email'] = email; f.SENSITIVE.append(owner['access_token'])
            workspace = f.api(base,'POST','/workspaces',owner,{'name':'Studio Nord'})
            channels = [f.api(base,'POST','/channels',owner,{'name':name,'workspace_id':workspace['id']}) for name in ('Design studio','Provider review')]
            ws, first, second = workspace['id'], channels[0]['id'], channels[1]['id']
            # Only disposable fixture identity is promoted, before real UI login.
            with sqlite3.connect(private/'proof.db') as db: db.execute('UPDATE user SET role=? WHERE id=?',('admin',owner['user_id'])); db.commit()
            for channel, kind, name in [(first,'calendar','Studio calendar'),(second,'files','Other channel files')]:
                secret = 'private-customer-app-'+secrets.token_hex(24); f.SENSITIVE.append(secret)
                f.api(base,'POST','/workspaces/'+ws+'/sync',owner,{'name':name,'provider':'google','kind':kind,'channel_id':channel,'config':{'client_id':'customer-fixture-app',**({'calendar_id':'primary'} if kind=='calendar' else {'folder_id':'selected-folder'})},'credentials':{'client_secret':secret},'password':f.PASSWORD,'consent':True})
            REPORT.parent.mkdir(parents=True,exist_ok=True)
            errors, blocked = [], []
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
                context = browser.new_context(viewport={'width':1440,'height':1050},reduced_motion='reduce')
                def route(request):
                    parsed = urlsplit(request.request.url)
                    if parsed.scheme in {'data','blob'} or parsed.hostname in {'127.0.0.1','localhost','::1'}: request.continue_()
                    else: blocked.append(parsed.hostname); request.abort()
                context.route('**/*',route); page = context.new_page(); page.set_default_timeout(30000); page.on('pageerror',lambda error: errors.append(f.redacted(error)))
                dialog, _ = f.login(page,origin,owner,email,ws)
                dialog.get_by_role('button',name='Connection health',exact=True).click()
                health = dialog.locator('.fc-connection-health'); expect(health).to_have_attribute('aria-busy','false')
                health.get_by_role('combobox',name=re.compile('^Connection channel')).select_option(first)
                expect(health).to_have_attribute('aria-busy','false')
                expect(health.get_by_text('Studio calendar',exact=True)).to_be_visible(); expect(health.get_by_text('Other channel files',exact=True)).to_have_count(0)
                expect(health.get_by_text('Account authorization needed',exact=True)).to_be_visible()
                expect(health.get_by_text('Configuration ready',exact=True)).to_have_count(1)  # summary label, no ready recipe badge
                checks['actual_saved_state_scoped_to_selected_channel_and_authorization_required'] = True
                f.controls(private,hold={'method':'GET','path':'/workspaces/'+ws+'/connection-setup'})
                health.get_by_role('combobox',name=re.compile('^Connection channel')).select_option(second); f.wait_file(page,private/'held-response')
                health.get_by_role('combobox',name=re.compile('^Connection channel')).select_option(first)
                (private/'release-response').touch(); f.controls(private)
                expect(health).to_have_attribute('aria-busy','false'); expect(health.get_by_text('Studio calendar',exact=True)).to_be_visible(); expect(health.get_by_text('Other channel files',exact=True)).to_have_count(0)
                checks['real_delayed_response_channel_switch_does_not_restore_stale_other_channel_state'] = True
                health.get_by_role('button',name='Authorize the customer account',exact=True).click()
                center = dialog.locator('.fc-connection-setup'); expect(center).to_have_attribute('aria-busy','false')
                expect(center.get_by_label('Connection recipe',exact=True)).to_have_value('calendar.google'); expect(center.get_by_label('Connection channel',exact=True)).to_have_value(first)
                f.step(center,'configure'); expect(center.get_by_role('heading',name='Studio calendar',exact=True)).to_be_visible()
                center.get_by_role('button',name='Edit connection',exact=True).click(); expect(center.locator('#sync-calendar-id')).to_have_value('primary')
                center.locator('#sync-name').fill('Unsaved edited calendar'); dialog.get_by_role('button',name='Connection health',exact=True).click()
                expect(center.locator('#sync-name')).to_have_value('Unsaved edited calendar'); expect(center).to_be_visible()
                center.get_by_role('button',name='Cancel',exact=True).click(); dialog.get_by_role('button',name='Connection health',exact=True).click(); expect(health).to_be_visible()
                checks['health_repair_opens_exact_recipe_channel_existing_draft_and_preserves_unsaved_edit_guard'] = True
                dialog.get_by_role('button',name='Operations',exact=True).click()
                readiness = dialog.get_by_role('region',name='Deployment readiness',exact=True)
                expect(readiness.get_by_role('heading',name='Your installation, in view.',exact=True)).to_be_visible()
                expect(readiness.locator('.fc-pilot-checks>li')).to_have_count(14)
                expect(readiness.get_by_text('Off-host backup and restore evidence',exact=True)).to_be_visible()
                assert readiness.get_by_text('Needs external verification',exact=True).count() >= 3
                checks['real_native_installation_operator_readiness_14_checks_and_honest_unknown_offhost_proxy_host'] = True
                dialog.get_by_role('button',name='Done',exact=True).click(); page.get_by_role('button',name='Account settings',exact=True).click()
                page.get_by_label('Language',exact=True).select_option('sv'); expect(page.locator('.fc-language-status')).to_have_text('Språket har sparats.'); page.locator('.fc-account-panel .fc-account-header button').click()
                page.get_by_role('button',name='Integrationer',exact=True).click(); dialog = page.get_by_role('dialog',name='Integrationer',exact=True)
                dialog.get_by_role('button',name='Drift',exact=True).click(); readiness = dialog.get_by_role('region',name='Driftkontroll',exact=True)
                expect(readiness.get_by_role('heading',name='Er installation, under uppsikt.',exact=True)).to_be_visible(); expect(readiness.get_by_text('Bevis för extern säkerhetskopia och återställning',exact=True)).to_be_visible()
                page.screenshot(path=str(REPORT.parent/'open-release-readiness-sv-desktop.png'),full_page=True,animations='disabled')
                page.set_viewport_size({'width':320,'height':900}); no_overflow(page,readiness); page.screenshot(path=str(REPORT.parent/'open-release-readiness-sv-320.png'),full_page=True,animations='disabled')
                dialog.get_by_role('button',name='Anslutningsstatus',exact=True).click(); health = dialog.locator('.fc-connection-health'); health.get_by_role('combobox',name=re.compile('^Anslutningens kanal')).select_option(first); expect(health).to_have_attribute('aria-busy','false')
                expect(health.get_by_text('Studio calendar',exact=True)).to_be_visible(); expect(health.get_by_role('button',name='Godkänn åtkomst för ert konto',exact=True)).to_be_visible(); no_overflow(page,health); page.screenshot(path=str(REPORT.parent/'open-release-health-sv-320.png'),full_page=True,animations='disabled')
                checks['persisted_swedish_operator_and_health_render_at_320_without_horizontal_overflow'] = True
                page.evaluate('async()=>{const {api}=await import("/src/lib/api.ts");api.logout()}'); expect(health).to_have_count(0)
                checks['real_auth_change_removes_saved_connection_ui'] = True
                assert not errors, 'JavaScript errors: '+f.redacted(errors); assert not blocked, 'Unexpected external browser requests'
                context.close(); browser.close()
            evidence = f.api(base,'GET','/__connection-proof-evidence'); assert not evidence['provider_attempts']; checks['zero_external_provider_requests_and_zero_javascript_errors'] = True
            REPORT.write_text(json.dumps({'ok':True,'checks':checks,'source_sha256':frozen,'scope':'Frozen actual frontend App source with real private native API/SQLite; no synthetic readiness/catalog responses or provider network.','cleanup':'Temporary API/Vite, SQLite, users, keys and browser context removed. No Docker or existing identities used.','screenshots':[str(REPORT.parent/name) for name in ('open-release-readiness-sv-desktop.png','open-release-readiness-sv-320.png','open-release-health-sv-320.png')]},indent=2)+'\n')
            print(json.dumps({'ok':True,'report':str(REPORT)},indent=2))
        except Exception as failure:
            state = page.evaluate('()=>({dialogs:[...document.querySelectorAll(\"[role=dialog]\")].map(el=>el.getAttribute(\"aria-label\")),health:document.querySelector(\".fc-connection-health\")?.innerText,readiness:document.querySelector(\".fc-pilot-section[aria-label]\")?.innerText,comboboxes:[...document.querySelectorAll(\"select\")].map(el=>({label:el.getAttribute(\"aria-label\"),value:el.value,parent:el.parentElement?.textContent?.slice(0,120)}))})') if 'page' in locals() else {}; error=f.redacted(str(failure)+' Rendered state: '+str(state)); REPORT.parent.mkdir(parents=True,exist_ok=True); REPORT.write_text(json.dumps({'ok':False,'checks':checks,'error':error},indent=2)+'\n'); raise AssertionError(error) from None
        finally:
            f.controls(private); f.stop(processes)
            for stream in streams: stream.close()


if __name__ == '__main__': main()
