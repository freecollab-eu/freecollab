#!/usr/bin/env python3
"""Real integration dashboard proof on an isolated API and Vite proxy.

Starts disposable local services with a private SQLite DB/key/mail directory.
Creates public-HTTPS webhook configuration and queues test records, but NEVER
runs a webhook delivery worker or contacts a webhook destination. SMTP is not
used: the email worker delivers only into the private local fixture directory.

CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/integrations-browser.py
"""
from ui_navigation import open_chat_tool, reveal_chat_tool
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(os.getenv('BACKEND_PYTHON', sys.executable))
PASSWORD = 'Isolated-integrations-browser!2026'
RUN = secrets.token_hex(6)


def port():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        return server.getsockname()[1]


def wait_ready(url, processes):
    for _ in range(450):
        if any(process.poll() is not None for process in processes):
            raise AssertionError('An isolated fixture service exited before readiness')
        try:
            with urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except (HTTPError, URLError, TimeoutError):
            pass
        time.sleep(.2)
    raise AssertionError('Isolated fixture services did not become ready')


def stop(processes):
    for process in reversed(processes):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)


def api(base, method, path, user=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if user:
        headers['Authorization'] = 'Bearer ' + user['access_token']
    request = Request(base + path, method=method, headers=headers,
                      data=json.dumps(data).encode() if data is not None else None)
    with urlopen(request, timeout=45) as response:
        return json.load(response)


def mfa(base, user):
    setup = api(base, 'POST', '/account/mfa/setup', user, {'password': PASSWORD})
    raw = setup['secret']
    key = base64.b32decode(raw + '=' * ((8 - len(raw) % 8) % 8))
    digest = hmac.new(key, struct.pack('>Q', int(time.time()) // 30), hashlib.sha1).digest()
    offset = digest[-1] & 15
    code = f'{(struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000:06d}'
    response = api(base, 'POST', '/account/mfa/confirm', user, {'code': code})
    user.update(response['session'])
    user['codes'] = response['recovery_codes']


def login(page, base, user):
    page.goto(base, wait_until='domcontentloaded', timeout=90000)
    page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
    page.get_by_label('Email', exact=True).fill(user['email'])
    page.get_by_label('Password', exact=True).fill(PASSWORD)
    page.get_by_role('button', name='Sign In', exact=True).click()
    if user.get('codes'):
        page.get_by_label('Authentication or recovery code', exact=True).fill(user['codes'].pop(0))
        page.get_by_role('button', name='Verify and sign in', exact=True).click()
    expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
    reveal_chat_tool(page, 'Account settings')
    expect(page.get_by_role('button', name='Account settings', exact=True)).to_be_visible(timeout=90000)


with tempfile.TemporaryDirectory(prefix='freecollab-integrations-ui-') as directory:
    temporary = Path(directory)
    api_port, app_port = port(), port()
    api_base, app_base = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
    database = temporary / 'product.db'
    env = os.environ.copy()
    env.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(database),
               STORAGE_PATH=str(temporary / 'files'), JWT_SECRET=secrets.token_urlsafe(48),
               MESSAGE_ENCRYPTION_KEYS=json.dumps({'integration-test': base64.b64encode(secrets.token_bytes(32)).decode()}),
               MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='integration-test',
               ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
               MESSAGE_ENCRYPTION_KEY_FILE=str(temporary / 'message-keys.json'),
               ACCOUNT_SECURITY_KEY_FILE=str(temporary / 'account.key'),
               MAIL_DELIVERY_MODE='file', MAIL_FILE_PATH=str(temporary / 'mail'),
               SMTP_FROM='FreeCollab <no-reply@example.com>', PUBLIC_APP_URL=app_base,
               CORS_ORIGINS=json.dumps([app_base]), MATRIX_ENABLED='false', OIDC_ENABLED='false',
               OTRUST_ENABLED='false', WEBHOOKS_ENABLED='true', PROOF_API_PORT=str(api_port), PROOF_APP_PORT=str(app_port))
    env['NO_PROXY'] = ','.join(filter(None, [env.get('NO_PROXY', ''), '127.0.0.1', 'localhost', '::1']))
    processes = []
    try:
        with open(temporary / 'api.log', 'w') as api_log, open(temporary / 'web.log', 'w') as web_log:
            processes.append(subprocess.Popen([str(PYTHON), '-m', 'uvicorn', 'app.main:socket_app', '--host', '127.0.0.1', '--port', str(api_port), '--no-proxy-headers'],
                cwd=ROOT / 'backend', env=env, stdout=api_log, stderr=api_log, start_new_session=True))
            vite = '''import {createServer} from 'vite';import react from '@vitejs/plugin-react';
const server=await createServer({configFile:false,root:process.cwd(),plugins:[react()],optimizeDeps:{exclude:['@matrix-org/matrix-sdk-crypto-wasm']},server:{host:'127.0.0.1',port:Number(process.env.PROOF_APP_PORT),strictPort:true,proxy:{'/api':{target:'http://127.0.0.1:'+process.env.PROOF_API_PORT,changeOrigin:true,rewrite:path=>path.replace(/^\\/api/,'')},'/socket.io':{target:'http://127.0.0.1:'+process.env.PROOF_API_PORT,ws:true}}}});await server.listen();'''
            processes.append(subprocess.Popen(['node', '--input-type=module', '-e', vite], cwd=ROOT / 'frontend', env=env, stdout=web_log, stderr=web_log, start_new_session=True))
            wait_ready(api_base + '/ready', processes)
            wait_ready(app_base, processes)

            def register(name):
                email = name.lower() + '-' + RUN + '@example.com'
                return {**api(api_base, 'POST', '/auth/register', data={'name': name, 'email': email, 'password': PASSWORD}), 'email': email}

            owner, member = register('IntegrationOwner'), register('IntegrationMember')
            api(api_base, 'GET', '/account/security', owner)
            with sqlite3.connect(database) as connection:
                connection.execute('UPDATE user SET role=? WHERE id=?', ('admin', owner['user_id']))
                connection.execute('UPDATE accountsecurity SET email_verified_at=? WHERE user_id=?', (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), owner['user_id']))
            workspace = api(api_base, 'POST', '/workspaces', owner, {'name': 'Integration Studio ' + RUN})
            invitation = api(api_base, 'POST', '/workspaces/' + workspace['id'] + '/invites', owner, {'role': 'member'})
            api(api_base, 'POST', '/workspaces/invites/accept', member, {'token': invitation['token']})
            channel = api(api_base, 'POST', '/channels', owner, {'name': 'Workflow Studio', 'type': 'group', 'workspace_id': workspace['id'], 'member_ids': [member['user_id']]})
            mfa(api_base, owner)

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or None, headless=True, args=['--no-sandbox'])
                context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
                context.add_init_script('localStorage.setItem(' + json.dumps('freecollab.workspace:' + owner['user_id']) + ',' + json.dumps(workspace['id']) + ');')
                page = context.new_page()
                page.set_default_timeout(30000)
                errors = []
                page.on('pageerror', lambda failure: errors.append(str(failure)))
                try:
                    login(page, app_base, owner)
                    open_chat_tool(page, 'Integrations')
                    panel = page.get_by_role('dialog', name='Integrations', exact=True)
                    expect(panel.get_by_role('heading', name='A few useful connections.', exact=True)).to_be_visible()
                    panel.get_by_role('button', name='Webhooks', exact=True).click()
                    panel.get_by_role('button', name='Create webhook', exact=True).click()
                    form = panel.get_by_role('form', name='Create webhook', exact=True)
                    form.get_by_label('Connection name', exact=True).fill('Studio n8n')
                    form.get_by_label('HTTPS receiver URL', exact=True).fill('https://127.0.0.1/forbidden')
                    form.get_by_label('Workflow Studio', exact=True).check()
                    form.get_by_label('message.created', exact=True).check()
                    form.get_by_role('button', name='Create connection', exact=True).click()
                    expect(panel.get_by_role('alert')).to_contain_text('public')
                    form.get_by_label('HTTPS receiver URL', exact=True).fill('https://93.184.216.34/freecollab-fixture-never-dispatched')
                    form.get_by_role('button', name='Create connection', exact=True).click()
                    secret = panel.get_by_role('region', name='Webhook signing secret', exact=True)
                    expect(secret).to_be_visible(timeout=30000)
                    signing_secret = secret.get_by_label('Signing secret', exact=True).input_value()
                    assert len(signing_secret) >= 32
                    panel.get_by_role('button', name='Close integrations', exact=True).click()
                    expect(panel).to_be_visible()
                    assert not page.evaluate('secret => Object.values(localStorage).some(value => value.includes(secret))', signing_secret)
                    secret.get_by_role('checkbox').check()
                    secret.get_by_role('button', name='Hide saved secret', exact=True).click()
                    expect(secret).to_have_count(0)
                    panel.get_by_role('button', name='Send test event', exact=True).click()
                    expect(panel.get_by_role('status').filter(has_text='Test queued')).to_be_visible()
                    expect(panel.get_by_text('webhook.test', exact=True)).to_be_visible()
                    assert panel.get_by_text('delivered', exact=True).count() == 0
                    hook = api(api_base, 'GET', '/workspaces/' + workspace['id'] + '/webhooks', owner)['webhooks'][0]
                    delivery = api(api_base, 'GET', '/workspaces/' + workspace['id'] + '/webhooks/' + hook['id'] + '/deliveries', owner)['deliveries'][0]
                    assert delivery['status'] == 'pending'
                    with sqlite3.connect(database) as connection:
                        connection.execute('UPDATE webhookdelivery SET status=?,failure=? WHERE id=?', ('failed', 'Fixture receiver unavailable', delivery['id']))
                    panel.get_by_role('button', name='Refresh webhook delivery history', exact=True).click()
                    panel.get_by_role('button', name='Retry', exact=True).click()
                    expect(panel.get_by_role('status').filter(has_text='queued for another attempt')).to_be_visible()
                    panel.get_by_role('button', name='Pause', exact=True).click()
                    expect(panel.get_by_role('button', name='Resume', exact=True)).to_be_visible()
                    panel.get_by_role('button', name='Resume', exact=True).click()
                    panel.get_by_role('button', name='Edit', exact=True).click()
                    edit_form = panel.get_by_role('form', name='Edit webhook', exact=True)
                    edit_form.get_by_label('Connection name', exact=True).fill('Studio workflows')
                    edit_form.get_by_role('button', name='Save connection', exact=True).click()
                    expect(panel.get_by_role('heading', name='Studio workflows', exact=True)).to_be_visible()
                    panel.get_by_role('button', name='Rotate secret', exact=True).click()
                    panel.get_by_role('button', name='Replace secret', exact=True).click()
                    rotated = panel.get_by_label('Signing secret', exact=True).input_value()
                    assert rotated != signing_secret
                    panel.get_by_role('checkbox', name='I saved this secret in my receiver’s secure settings.', exact=True).check()
                    panel.get_by_role('button', name='Hide saved secret', exact=True).click()
                    print('PASS real webhook UI: public-address validation, explicit grants, one-time secret, durable queued test, retry, pause/resume, edit and rotation', flush=True)

                    panel.get_by_role('button', name='Operations', exact=True).click()
                    expect(panel.get_by_role('heading', name='Email & operations', exact=True)).to_be_visible()
                    panel.get_by_role('button', name='Send test email', exact=True).click()
                    email_form = panel.get_by_role('form', name='Send test email', exact=True)
                    email_form.get_by_label('Confirm with your password', exact=True).fill('Incorrect-password!')
                    email_form.get_by_label('Authentication or recovery code', exact=True).fill(owner['codes'][0])
                    email_form.get_by_role('button', name='Queue test email', exact=True).click()
                    expect(panel.get_by_role('alert')).to_contain_text('password')
                    email_form.get_by_label('Confirm with your password', exact=True).fill(PASSWORD)
                    email_form.get_by_label('Authentication or recovery code', exact=True).fill(owner['codes'].pop(0))
                    email_form.get_by_role('button', name='Queue test email', exact=True).click()
                    expect(panel.get_by_role('status').filter(has_text='test email is queued')).to_be_visible()
                    worker = subprocess.run([str(PYTHON), '-m', 'app.services.mail', '--once'], cwd=ROOT / 'backend', env=env, capture_output=True, timeout=60)
                    assert worker.returncode == 0, 'The isolated local-mail worker failed'
                    assert list((temporary / 'mail').glob('*.eml')), 'No local fixture email was delivered'
                    panel.get_by_role('button', name='Refresh operations status', exact=True).click()
                    expect(panel.get_by_text('healthy', exact=True)).to_be_visible()
                    assert api(api_base, 'GET', '/operations/status', owner)['queue']['delivered'] >= 1
                    with page.expect_download() as download:
                        panel.get_by_role('button', name='Download metrics', exact=True).click()
                    assert download.value.suggested_filename == 'freecollab-metrics.txt'
                    print('PASS real operations UI: wrong-password retention, MFA-authorized queued test, private file-worker delivery, worker heartbeat and authenticated metrics download', flush=True)

                    panel.get_by_role('button', name='Overview', exact=True).click()
                    evidence = ROOT.parent / '.freecollab'
                    evidence.mkdir(exist_ok=True)
                    panel.locator('.fc-account-body').evaluate('node => node.scrollTo(0, 0)')
                    page.screenshot(path=str(evidence / 'integrations-desktop.png'), full_page=True)
                    page.set_viewport_size({'width': 320, 'height': 844})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert panel.evaluate('node => node.scrollWidth <= node.clientWidth + 1')
                    page.keyboard.press('Shift+Tab')
                    assert panel.evaluate('node => node.contains(document.activeElement)')
                    page.screenshot(path=str(evidence / 'integrations-mobile.png'), full_page=True)
                    page.keyboard.press('Escape')
                    expect(panel).to_have_count(0)
                    assert not errors, 'Unexpected browser page error'

                    member_context = browser.new_context(viewport={'width': 390, 'height': 844})
                    member_context.add_init_script('localStorage.setItem(' + json.dumps('freecollab.workspace:' + member['user_id']) + ',' + json.dumps(workspace['id']) + ');')
                    member_page = member_context.new_page()
                    login(member_page, app_base, member)
                    open_chat_tool(member_page, 'Integrations')
                    member_panel = member_page.get_by_role('dialog', name='Integrations', exact=True)
                    expect(member_panel.get_by_role('heading', name='A few useful connections.', exact=True)).to_be_visible()
                    assert member_panel.get_by_role('button', name='Webhooks', exact=True).count() == 0
                    assert member_panel.get_by_role('button', name='Operations', exact=True).count() == 0
                    member_context.close()
                    print('PASS integration dashboard: account/role isolation, preserved theme, 320px layout and modal keyboard focus; no external webhook dispatch or SMTP', flush=True)
                finally:
                    context.close()
                    browser.close()
    finally:
        stop(processes)
