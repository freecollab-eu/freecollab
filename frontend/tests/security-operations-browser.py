#!/usr/bin/env python3
"""Private API/Vite/Chromium proof of operator security status and activity.

CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/security-operations-browser.py
Uses temporary native accounts, directory/session metadata and private file mail.
No customer account, existing database, provider, SMTP server or Docker is used.
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('private_ops_proof_helpers', Path(__file__).with_name('remote-sync-browser.py'))
helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(helpers)
PASSWORD = 'Private-security-operations-proof!2026'
WRONG_PASSWORD = 'Wrong-private-operations-password!2026'
PRIVATE_RECIPIENT = 'private-audit-outbox@example.com'
PRIVATE_BODY = 'private-reset-body-and-recovery-data-must-stay-outside-activity'
REPORT = Path(os.getenv('SECURITY_OPERATIONS_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/security-operations-browser.json')))


def login(page, app, user, workspace):
    page.goto(app, wait_until='domcontentloaded', timeout=90000)
    page.evaluate('([key,value]) => localStorage.setItem(key,value)', ['freecollab.workspace:' + user['user_id'], workspace['id']])
    page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
    page.get_by_label('Email', exact=True).fill(user['email'])
    page.get_by_label('Password', exact=True).fill(PASSWORD)
    page.get_by_role('button', name='Sign In', exact=True).click()
    expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
    if not page.get_by_role('button', name='Integrations', exact=True).is_visible():
        page.get_by_role('button', name='Toggle channels', exact=True).click()
    page.get_by_role('button', name='Integrations', exact=True).click()
    panel = page.get_by_role('dialog', name='Integrations', exact=True)
    expect(panel).to_be_visible()
    return panel


def main():
    stage, results, processes, streams = 'private services startup', {}, [], []
    with tempfile.TemporaryDirectory(prefix='freecollab-security-operations-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = helpers.port(), helpers.port()
            base, app = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
            metrics_token = secrets.token_urlsafe(40)
            environment = os.environ.copy()
            environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(private / 'proof.db'), STORAGE_PATH=str(private / 'files'),
                JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof': base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',
                MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'message-keys.json'), ACCOUNT_SECURITY_KEY_FILE=str(private / 'account.key'),
                MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='file', MAIL_FILE_PATH=str(private / 'mail'), PUBLIC_APP_URL=app,
                CORS_ORIGINS=json.dumps([app]), OPERATIONS_METRICS_TOKEN=metrics_token, SECURITY_AUDIT_RETENTION_DAYS='90',
                OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', OTRUST_ENABLED='false', SYNC_ENABLED='false', PYTHONPATH=str(ROOT / 'backend'))
            environment['NO_PROXY'] = ','.join(filter(None, [environment.get('NO_PROXY', ''), 'localhost', '127.0.0.1', '::1']))
            (private / 'files').mkdir(mode=0o700)
            python = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').exists() else sys.executable)
            launcher = private / 'api.py'
            launcher.write_text('import uvicorn\nfrom app.main import socket_app\nuvicorn.run(socket_app,host="127.0.0.1",port=' + str(api_port) + ',proxy_headers=False,access_log=False)\n')
            for command, filename, cwd in [([python, str(launcher)], 'api.log', ROOT / 'backend'), (['node', str(helpers.vite_launcher(private, base, app_port))], 'vite.log', ROOT / 'frontend')]:
                stream = (private / filename).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, start_new_session=True))
            helpers.ready(base + '/ready', processes); helpers.ready(app + '/', processes)
            stage = 'private native users, operator and bounded event fixtures'
            run = secrets.token_hex(6)
            owner_email, member_email = 'ops-owner-' + run + '@example.com', 'ops-member-' + run + '@example.com'
            owner = helpers.api(base, 'POST', '/auth/register', data={'name': 'Private server operator', 'email': owner_email, 'password': PASSWORD}); owner['email'] = owner_email
            member = helpers.api(base, 'POST', '/auth/register', data={'name': 'Private workspace member', 'email': member_email, 'password': PASSWORD}); member['email'] = member_email
            workspace = helpers.api(base, 'POST', '/workspaces', owner, {'name': 'Studio operations'})
            channel = helpers.api(base, 'POST', '/channels', owner, {'name': 'Operations project', 'workspace_id': workspace['id']})
            fixture_env = {**environment, 'PROOF_OWNER_ID': owner['user_id'], 'PROOF_MEMBER_ID': member['user_id'], 'PROOF_WORKSPACE_ID': workspace['id'], 'PROOF_CHANNEL_ID': channel['id']}
            fixture = '''import json,os
from datetime import datetime,timedelta
from uuid import uuid4
from sqlmodel import Session
from app import main
from app.database import engine
from app.models.user import User
from app.models.channel import ChannelMember
from app.models.workspace import WorkspaceMember
from app.models.account_security import AccountSecurity
from app.models.security_event import SecurityEvent
from app.models.workspace_oidc import WorkspaceOIDCProvider,WorkspaceOIDCSession
from app.services.auth import create_token,token_digest
from app.services.mail import enqueue_mail
owner,member,workspace,channel=(os.environ[key] for key in ['PROOF_OWNER_ID','PROOF_MEMBER_ID','PROOF_WORKSPACE_ID','PROOF_CHANNEL_ID'])
with Session(engine) as session:
    user=session.get(User,owner); user.role='admin'; session.add(user)
    state=session.get(AccountSecurity,owner); state.email_verified_at=datetime.utcnow(); session.add(state)
    session.add(WorkspaceMember(workspace_id=workspace,user_id=member,role='member'))
    session.add(ChannelMember(channel_id=channel,user_id=member,role='member'))
    session.commit()
    row=enqueue_mail(session,"''' + PRIVATE_RECIPIENT + '''",'Private reset subject',"''' + PRIVATE_BODY + '''")
    row.status='processing'; row.claimed_at=datetime.utcnow()-timedelta(minutes=11); session.add(row)
    now=datetime.utcnow()-timedelta(minutes=1)
    for index in range(55):
        session.add(SecurityEvent(created_at=now-timedelta(seconds=index),actor_kind='account',actor_id=owner,workspace_id=workspace,target_id=uuid4().hex,action='account.session_revoke',outcome='success',status_code=200))
    session.add(WorkspaceOIDCProvider(workspace_id=workspace,name='Private scope fixture',issuer_url='https://identity.private-proof.example',client_id='scope-fixture',enabled=True,revision=1,created_by=owner))
    session.commit()
    token=create_token(owner,session,auth_methods=['sso'],workspace_scope={'workspace_id':workspace,'provider_revision':1})
    session.add(WorkspaceOIDCSession(token_hash=token_digest(token),workspace_id=workspace,provider_revision=1)); session.commit()
    print(json.dumps({'scoped_token':token}))
'''
            seeded = subprocess.run([python, '-c', fixture], cwd=ROOT / 'backend', env=fixture_env, capture_output=True, text=True, timeout=45)
            assert seeded.returncode == 0, 'Private operator fixture initialization failed'
            scoped = {'access_token': json.loads(seeded.stdout)['scoped_token']}
            stage = 'operator, member, scoped and metrics-only authorization boundaries'
            for path in ['/operations/security', '/operations/security-events']:
                assert helpers.rejects(base, 'GET', path) == 401
                assert helpers.rejects(base, 'GET', path, member) == 403
                assert helpers.rejects(base, 'GET', path, scoped) in (401, 403)
                assert helpers.rejects(base, 'GET', path, {'access_token': metrics_token}) == 401
            metrics = helpers.api(base, 'GET', '/operations/metrics', {'access_token': metrics_token}, raw=True)
            assert b'freecollab_mail' in metrics
            results['native_operator_only_member_scoped_and_metrics_token_excluded'] = True
            snapshot = helpers.api(base, 'GET', '/operations/security', owner)
            checks = {row['id']: row['status'] for row in snapshot['checks']}
            assert snapshot['operators']['active'] == 1 and snapshot['operators']['without_mfa'] == 1 and checks['operator_mfa'] == 'attention'
            assert snapshot['queues']['mail']['expired_leases'] == 1 and checks['mail_leases'] == 'attention'
            assert snapshot['storage']['free_bytes'] > 0 and snapshot['storage']['total_bytes'] > 0
            assert not any(value in json.dumps(snapshot) for value in [str(private), PRIVATE_RECIPIENT, PRIVATE_BODY, PASSWORD, metrics_token])
            results['real_diagnostics_operator_mfa_storage_and_expired_claim_attention'] = True
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), args=['--no-sandbox'])
                context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce', accept_downloads=True)
                page = context.new_page(); page.set_default_timeout(30000); page_errors, request_urls = [], []
                page.on('pageerror', lambda failure: page_errors.append(type(failure).__name__))
                page.on('request', lambda request: request_urls.append(request.url))
                try:
                    stage = 'actual operator UI status and claims'
                    panel = login(page, app, owner, workspace)
                    panel.get_by_role('button', name='Operations', exact=True).click()
                    expect(panel.get_by_role('heading', name='Security & service health', exact=True)).to_be_visible()
                    expect(panel.get_by_role('heading', name='Recent security actions', exact=True)).to_be_visible()
                    configuration = panel.get_by_role('list', name='Security configuration checks', exact=True)
                    expect(configuration.locator('li').filter(has_text='Operator authenticators')).to_contain_text('Needs attention')
                    expect(configuration.locator('li').filter(has_text='Email worker leases')).to_contain_text('Needs attention')
                    expect(panel.get_by_text('0 of 1 enabled', exact=True)).to_be_visible()
                    expect(panel.get_by_text('Free storage', exact=True)).to_be_visible()
                    results['real_operator_overview_ui_with_fixed_checks_and_no_secret_values'] = True
                    stage = 'preserved email actions generate real denial and success events'
                    panel.get_by_role('button', name='Send test email', exact=True).click()
                    form = panel.get_by_role('form', name='Send test email', exact=True)
                    form.get_by_label('Confirm with your password', exact=True).fill(WRONG_PASSWORD)
                    form.get_by_role('button', name='Queue test email', exact=True).click()
                    expect(panel.get_by_role('alert')).to_contain_text('password')
                    assert helpers.api(base, 'GET', '/auth/me', owner)['id'] == owner['user_id']
                    form.get_by_label('Confirm with your password', exact=True).fill(PASSWORD)
                    form.get_by_role('button', name='Queue test email', exact=True).click()
                    expect(panel.get_by_role('status').filter(has_text='test email is queued')).to_be_visible()
                    worker = subprocess.run([python, '-m', 'app.services.mail', '--once'], cwd=ROOT / 'backend', env=environment, capture_output=True, timeout=60)
                    assert worker.returncode == 0 and list((private / 'mail').glob('*.eml')), 'Private file-mail worker failed'
                    panel.get_by_role('button', name='Refresh operations status', exact=True).click()
                    assert helpers.api(base, 'GET', '/operations/status', owner)['queue']['delivered'] >= 1
                    results['existing_mail_wrong_password_retains_session_and_success_delivers_privately'] = True
                    stage = 'real middleware events are sanitized and filterable'
                    data = helpers.api(base, 'GET', '/operations/security-events?limit=100', owner)
                    assert any(row['action'] == 'account.sign_in' and row['outcome'] == 'success' for row in data['items'])
                    assert any(row['action'] == 'operations.mail_test' and row['outcome'] == 'denied' for row in data['items'])
                    assert any(row['action'] == 'operations.mail_test' and row['outcome'] == 'success' for row in data['items'])
                    assert not any(value in json.dumps(data) for value in [PASSWORD, WRONG_PASSWORD, PRIVATE_RECIPIENT, PRIVATE_BODY, metrics_token, owner_email])
                    results['actual_signin_and_privileged_success_denial_events_redact_bodies_and_credentials'] = True
                    outcome = panel.get_by_label('Show results', exact=True)
                    with page.expect_response(lambda response: urlsplit(response.url).path == '/api/operations/security-events' and 'outcome=denied' in response.url) as filtered:
                        outcome.select_option('denied')
                    assert filtered.value.status == 200 and all(row['outcome'] == 'denied' for row in filtered.value.json()['items'])
                    history = panel.get_by_role('list', name='Security action history', exact=True)
                    expect(history.get_by_text('Diagnostic email request', exact=True)).to_be_visible()
                    assert history.get_by_text('Successful', exact=True).count() == 0
                    with page.expect_response(lambda response: urlsplit(response.url).path == '/api/operations/security-events' and 'outcome=' not in response.url):
                        outcome.select_option('')
                    expect(history.locator('li')).to_have_count(50)
                    panel.get_by_role('button', name='Show earlier actions', exact=True).click()
                    expect(history.locator('li')).to_have_count(len(data['items']))
                    assert len(data['items']) > 50
                    expect(panel.get_by_text(re.compile('Up to 90 days, newest'))).to_be_visible()
                    results['audit_outcome_filter_and_stable_cursor_pagination_ui'] = True
                    stage = 'failed new endpoint retains existing mail controls'
                    page.route('**/api/operations/security', lambda route: route.fulfill(status=503, content_type='application/json', body=json.dumps({'detail': 'Temporary security status fixture unavailable'})))
                    panel.get_by_role('button', name='Refresh security status', exact=True).click()
                    expect(panel.get_by_role('alert')).to_contain_text('Temporary security status fixture unavailable')
                    expect(panel.get_by_role('heading', name='Email & operations', exact=True)).to_be_visible()
                    expect(panel.get_by_role('button', name='Send test email', exact=True)).to_be_enabled()
                    page.unroute('**/api/operations/security')
                    panel.get_by_role('button', name='Refresh security status', exact=True).click()
                    expect(panel.get_by_text('Free storage', exact=True)).to_be_visible()
                    results['security_status_failure_does_not_hide_or_disable_existing_mail'] = True
                    stage = 'operator mobile layout focus and safe evidence'
                    REPORT.parent.mkdir(parents=True, exist_ok=True)
                    panel.locator('.fc-account-body').evaluate('node => node.scrollTo(0,0)')
                    page.screenshot(path=str(REPORT.with_name('security-operations-desktop.png')))
                    page.set_viewport_size({'width': 320, 'height': 900})
                    panel.get_by_role('button', name='Operations', exact=True).scroll_into_view_if_needed()
                    panel.locator('.fc-account-body').evaluate('node => node.scrollTo(0,0)')
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    assert panel.evaluate('node => node.scrollWidth <= node.clientWidth + 1')
                    page.keyboard.press('Tab'); page.keyboard.press('Shift+Tab')
                    assert panel.evaluate('node => node.contains(document.activeElement)')
                    page.screenshot(path=str(REPORT.with_name('security-operations-mobile.png')))
                    assert not page_errors, 'Browser page exception'
                    for value in [PASSWORD, WRONG_PASSWORD, PRIVATE_RECIPIENT, PRIVATE_BODY, metrics_token]:
                        assert not any(value in url for url in request_urls)
                        assert value not in page.evaluate('JSON.stringify(localStorage)')
                    results['operator_320px_keyboard_focus_no_credentials_in_urls_or_storage_zero_page_errors'] = True
                    stage = 'ordinary member never sees operator controls'
                    member_context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
                    try:
                        member_page = member_context.new_page(); member_page.set_default_timeout(30000)
                        member_panel = login(member_page, app, member, workspace)
                        assert member_panel.get_by_role('button', name='Operations', exact=True).count() == 0
                        assert member_panel.get_by_role('heading', name='Security & service health', exact=True).count() == 0
                    finally: member_context.close()
                    results['ordinary_member_operator_ui_hidden'] = True
                    print('PASS private actual security status, authenticated activity, denial/success redaction, filters/paging, existing mail and320px', flush=True)
                finally: context.close(); browser.close()
        except Exception as failure:
            detail = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+', '[redacted]', str(failure))
            for secret in [PASSWORD, WRONG_PASSWORD, PRIVATE_RECIPIENT, PRIVATE_BODY]: detail = detail.replace(secret, '[redacted]')
            print('FAIL security-operations browser stage: ' + stage + '; exception=' + type(failure).__name__ + '\n' + detail[:2000], file=sys.stderr, flush=True)
            raise SystemExit(1) from None
        finally:
            helpers.stop(processes)
            for stream in streams: stream.close()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({'scope': 'Actual private API, Vite, native operator/member and scoped fixture, Chromium, durable audit middleware and private file-mail worker; synthetic retained metadata only, no external provider', 'results': results,
        'cleanup': 'Own API/Vite processes, private SQLite database, files, keys, mail, fixture sessions and browser contexts removed in finally'}, indent=2) + '\n')


if __name__ == '__main__': main()
