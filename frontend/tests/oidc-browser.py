#!/usr/bin/env python3
"""Real browser OIDC proof with private services and a synthetic HTTPS IdP.

Run: CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/oidc-browser.py
Starts only ephemeral loopback API/Vite/IdP services and a private test database.
No real provider credentials, messages or external emails are sent or logged.
"""
import base64
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from tests.oidc_mock import LocalProvider, tls_fixture
from app.services.account_security import totp_at

PASSWORD = 'Isolated-OIDC-browser-password!2026'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def api(base, method, path, data=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    with urlopen(Request(base + path, method=method, headers=headers,
                         data=json.dumps(data).encode() if data is not None else None), timeout=45) as response:
        return json.load(response)


def denied(base, token):
    try:
        api(base, 'GET', '/auth/me', token=token)
    except HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError('A revoked native session remained active')


def ready(url):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(.2)
    raise AssertionError('An isolated proof service failed to become ready')


def token_from(page):
    return page.evaluate("()=>localStorage.getItem('freecollab_token')")


def authenticated(page):
    page.wait_for_selector('[aria-label="Account settings"]', state='attached', timeout=60000)
    if not page.get_by_role('button', name='Account settings', exact=True).is_visible():
        page.get_by_role('button', name='Toggle channels', exact=True).click()
    expect(page.get_by_role('button', name='Account settings', exact=True)).to_be_visible(timeout=60000)
    assert '#sso' not in page.url and 'access_token=' not in page.url
    assert token_from(page)


def native_login(page, email, codes):
    page.get_by_label('Email', exact=True).fill(email)
    page.get_by_label('Password', exact=True).fill(PASSWORD)
    page.get_by_role('button', name='Sign In', exact=True).click()
    page.get_by_label('Authentication or recovery code', exact=True).fill(codes.pop(0))
    page.get_by_role('button', name='Verify and sign in', exact=True).click()
    authenticated(page)


def sso_login(page, codes=None):
    page.get_by_role('button', name='Continue with Organization SSO', exact=True).click()
    if codes is not None:
        expect(page.get_by_label('Authentication or recovery code', exact=True)).to_be_visible(timeout=60000)
        page.get_by_label('Authentication or recovery code', exact=True).fill(codes.pop(0))
        page.get_by_role('button', name='Verify and sign in', exact=True).click()
    authenticated(page)


def account(page):
    authenticated(page)
    if page.viewport_size['width'] < 768:
        toggle = page.get_by_role('button', name='Toggle channels', exact=True)
        if toggle.get_attribute('aria-expanded') == 'false':
            toggle.click()
    page.get_by_role('button', name='Account settings', exact=True).click()
    panel = page.get_by_role('dialog', name='Account settings', exact=True)
    expect(panel.get_by_role('heading', name='Single sign-on', exact=True)).to_be_visible(timeout=30000)
    return panel


def sign_out_to_auth(page):
    page.get_by_role('button', name='Sign out', exact=True).click()
    page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()


with tempfile.TemporaryDirectory(prefix='freecollab-oidc-browser-') as directory:
    private = Path(directory)
    cert, key = tls_fixture(private / 'tls')
    provider = LocalProvider(cert_path=cert, key_path=key)
    processes, streams = [], []
    stage = 'isolated service startup'
    try:
        api_port, web_port = free_port(), free_port()
        api_base, app_base = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{web_port}'
        db = private / 'proof.db'
        # Add the isolated CA to inherited/system trust rather than disabling TLS.
        trust = private / 'ca-bundle.pem'
        inherited = os.getenv('SSL_CERT_FILE') or ssl.get_default_verify_paths().cafile
        trust.write_bytes((Path(inherited).read_bytes() if inherited else b'') + b'\n' + Path(cert).read_bytes())
        environment = os.environ.copy()
        environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(db), STORAGE_PATH=str(private / 'files'),
                           JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
                           MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'message-keys.json'), MATRIX_ENABLED='false',
                           MAIL_DELIVERY_MODE='disabled', PUBLIC_APP_URL=app_base, CORS_ORIGINS=json.dumps([app_base]),
                           OIDC_ENABLED='true', OIDC_PROVIDER_NAME='Organization SSO', OIDC_ISSUER_URL=provider.issuer,
                           OIDC_CLIENT_ID='freecollab-test', OIDC_CLIENT_SECRET='client-secret',
                           OIDC_REDIRECT_URI=app_base + '/api/auth/oidc/callback', OIDC_TRUST_EMAIL_CLAIM='false',
                           SSL_CERT_FILE=str(trust))
        stream = (private / 'api.log').open('w'); streams.append(stream)
        local_python = ROOT / 'backend/.venv/bin/python'
        backend_python = os.getenv('BACKEND_PYTHON') or (str(local_python) if local_python.exists() else sys.executable)
        processes.append(subprocess.Popen([backend_python, '-m', 'uvicorn', 'app.main:socket_app',
                                           '--host', '127.0.0.1', '--port', str(api_port), '--no-proxy-headers'],
                                          cwd=ROOT / 'backend', env=environment, stdout=stream, stderr=stream))
        vite = private / 'vite.mjs'
        vite.write_text("import {createServer} from " + json.dumps(str(ROOT / 'frontend/node_modules/vite/dist/node/index.js')) + ";\n"
                        "import react from " + json.dumps(str(ROOT / 'frontend/node_modules/@vitejs/plugin-react/dist/index.js')) + ";\n"
                        "const server=await createServer({configFile:false,root:" + json.dumps(str(ROOT / 'frontend')) + ",plugins:[react()],cacheDir:" + json.dumps(str(private / 'vite-cache')) + ",optimizeDeps:{exclude:['@matrix-org/matrix-sdk-crypto-wasm']},server:{host:'127.0.0.1',port:" + str(web_port) + ",strictPort:true,proxy:{'/api':{target:" + json.dumps(api_base) + ",changeOrigin:true,rewrite:p=>p.replace(/^\\/api/,'')},'/socket.io':{target:" + json.dumps(api_base) + ",ws:true}}}});await server.listen();\n")
        stream = (private / 'vite.log').open('w'); streams.append(stream)
        processes.append(subprocess.Popen(['node', str(vite)], cwd=ROOT / 'frontend', env=os.environ.copy(), stdout=stream, stderr=stream))
        ready(api_base + '/ready'); ready(app_base + '/')

        native = api(api_base, 'POST', '/auth/register', {'name': 'Native SSO Fixture', 'email': 'native-sso-fixture@example.com', 'password': PASSWORD})
        # Verification is fixture setup; the independent account/mail proof tests
        # real email links. No production verification bypass is added.
        with sqlite3.connect(db) as connection:
            connection.execute('UPDATE accountsecurity SET email_verified_at=? WHERE user_id=?', (datetime.utcnow().isoformat(' '), native['user_id']))
        setup = api(api_base, 'POST', '/account/mfa/setup', {'password': PASSWORD}, native['access_token'])
        enrollment = api(api_base, 'POST', '/account/mfa/confirm', {'code': totp_at(setup['secret'], int(time.time() // 30))}, native['access_token'])
        codes = enrollment['recovery_codes']

        with sync_playwright() as playwright:
            launch = {'headless': True, 'args': ['--no-sandbox']}
            executable = os.getenv('CHROMIUM_PATH') or shutil.which('chromium')
            if executable:
                launch['executable_path'] = executable
            browser = playwright.chromium.launch(**launch)
            try:
                context = browser.new_context(ignore_https_errors=True, viewport={'width': 1440, 'height': 1000})
                page = context.new_page(); page.set_default_timeout(30000)
                errors, request_urls, exchanges = [], [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: request_urls.append(request.url))
                page.on('request', lambda request: exchanges.append(request.url) if request.url.endswith('/auth/oidc/exchange') else None)
                page.goto(app_base, wait_until='domcontentloaded')
                page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
                stage = 'new account SSO callback and handoff'
                sso_login(page)
                first_token = token_from(page)
                first_user = api(api_base, 'GET', '/auth/me', token=first_token)['id']
                assert len(exchanges) == 1, 'StrictMode duplicated the one-time handoff exchange'
                assert not any('access_token=' in url or re.search(r'eyJ[A-Za-z0-9_-]+\.', url) for url in request_urls)
                assert '#sso' not in page.evaluate('()=>location.href')
                print('PASS actual HTTPS IdP redirect, PKCE/JWKS signup, one-time handoff and token-free callback URL', flush=True)

                stage = 'returning SSO login and existing email collision'
                sign_out_to_auth(page)
                sso_login(page)
                assert api(api_base, 'GET', '/auth/me', token=token_from(page))['id'] == first_user
                sign_out_to_auth(page)
                provider.subject, provider.email, provider.name = 'native-fixture-subject', 'native-sso-fixture@example.com', 'Native SSO Fixture'
                page.get_by_role('button', name='Continue with Organization SSO', exact=True).click()
                expect(page.get_by_text('An account already uses this email.', exact=False)).to_be_visible(timeout=60000)
                stage = 'native MFA and explicit account link'
                native_login(page, provider.email, codes)
                native_token = token_from(page)
                panel = account(page)
                panel.get_by_role('button', name='Link organization sign-in', exact=True).click()
                form = panel.get_by_role('form', name='Link organization sign-in', exact=True)
                form.get_by_label('Confirm with your FreeCollab password', exact=True).fill('wrong-password')
                form.get_by_label('Authentication or recovery code', exact=True).fill(codes[0])
                form.get_by_role('button', name='Continue to organization', exact=True).click()
                expect(panel.get_by_role('alert')).to_contain_text('Current password is incorrect')
                assert api(api_base, 'GET', '/auth/me', token=native_token)['id'] == native['user_id']
                form.get_by_label('Confirm with your FreeCollab password', exact=True).fill(PASSWORD)
                form.get_by_label('Authentication or recovery code', exact=True).fill(codes.pop(0))
                stage = 'acknowledged explicit account link'
                with page.expect_response(lambda response: response.url.endswith('/auth/oidc/exchange') and response.request.method == 'POST', timeout=60000) as linked_response:
                    form.get_by_role('button', name='Continue to organization', exact=True).click()
                assert linked_response.value.status == 200 and linked_response.value.json().get('linked') is True, 'Link callback was not acknowledged'
                authenticated(page)
                assert token_from(page) == native_token, 'Linking unexpectedly replaced the current session'
                assert len(api(api_base, 'GET', '/auth/oidc/identities', token=native_token)) == 1, 'Explicit link did not persist its identity'
                print('PASS returning identity, email-collision guidance, native MFA/password reauthentication and explicit account link', flush=True)

                stage = 'local MFA through SSO and explicit unlink'
                sign_out_to_auth(page)
                stage = 'SSO login with local MFA'
                sso_login(page, codes)
                linked_token = token_from(page)
                challenge = api(api_base, 'POST', '/auth/login', {'email': provider.email, 'password': PASSWORD})
                second = api(api_base, 'POST', '/auth/mfa/verify', {'challenge_token': challenge['challenge_token'], 'code': codes.pop(0)})
                stage = 'open mobile account settings'
                page.set_viewport_size({'width': 320, 'height': 760})
                # Wait for the responsive sidebar's asynchronous close before opening it.
                expect(page.get_by_role('button', name='Toggle channels', exact=True)).to_have_attribute('aria-expanded', 'false')
                panel = account(page)
                stage = 'open SSO removal form'
                panel.get_by_role('button', name='Remove Organization SSO sign-in connection', exact=True).click()
                form = panel.get_by_role('form', name='Remove organization sign-in', exact=True)
                form.get_by_label('Confirm with your FreeCollab password', exact=True).fill('wrong-password')
                form.get_by_label('Authentication or recovery code', exact=True).fill(codes[0])
                stage = 'reject wrong-password SSO removal'
                form.get_by_role('button', name='Remove connection and sign out', exact=True).click()
                expect(panel.get_by_role('alert')).to_contain_text('Current password is incorrect')
                assert api(api_base, 'GET', '/auth/me', token=linked_token)['id'] == native['user_id']
                form.get_by_label('Confirm with your FreeCollab password', exact=True).fill(PASSWORD)
                form.get_by_label('Authentication or recovery code', exact=True).fill(codes.pop(0))
                stage = 'confirmed SSO removal and all-session signout'
                form.get_by_role('button', name='Remove connection and sign out', exact=True).click()
                expect(page.get_by_text('Organization sign-in removed. All sessions signed out.', exact=False)).to_be_visible(timeout=30000)
                denied(api_base, linked_token); denied(api_base, second['access_token'])
                stage = 'native password and MFA fallback after SSO removal'
                native_login(page, provider.email, codes)
                panel = account(page)
                expect(panel.get_by_role('button', name='Link organization sign-in', exact=True)).to_be_visible()
                assert page.evaluate('()=>document.documentElement.scrollWidth <= innerWidth + 1')
                assert not errors, 'Unexpected browser page exception'
                print('PASS SSO retains local MFA; failed unlink retains session; successful unlink revokes all sessions and native sign-in works at320px', flush=True)
                if os.getenv('OIDC_PROOF_REPORT'):
                    Path(os.environ['OIDC_PROOF_REPORT']).write_text(json.dumps({
                        'scope': 'Local synthetic HTTPS OIDC provider and isolated real API/Vite/Chromium; no external tenant credentials',
                        'results': {'https_provider_redirect_and_pkce_jwks': True, 'new_and_returning_account': True,
                                    'one_exchange_per_handoff': True, 'no_bearer_in_callback_urls': True,
                                    'existing_email_requires_explicit_link': True, 'password_and_mfa_link': True,
                                    'local_mfa_preserved_for_sso': True, 'failed_unlink_retains_session': True,
                                    'unlink_revokes_all_native_sessions': True, 'native_password_fallback': True,
                                    'mobile_320px': True, 'zero_page_errors': True},
                        'cleanup': 'Temporary services, database, fixture keys and CA bundle removed by finally block',
                    }, indent=2) + '\n')
            finally:
                browser.close()
    except Exception as failure:
        # Print only bounded operational diagnostics, never provider tokens or
        # fixture keys. Detailed assertion identifies the failed user action.
        for process in processes:
            if process.poll() is not None:
                print('Isolated proof service exited with code', process.returncode, file=sys.stderr)
        # Only the first summary line; Playwright's full call log can contain
        # ephemeral callback codes and filled test recovery codes.
        summary = str(failure).splitlines()[0] if str(failure) else 'assertion failed'
        print('FAIL isolated OIDC proof stage:', stage, '(' + type(failure).__name__ + ')', summary[:180], file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        for process in reversed(processes):
            process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        for stream in streams:
            stream.close()
        provider.close()
