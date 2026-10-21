#!/usr/bin/env python3
"""Customer-owned SSO proof using Chromium and two synthetic HTTPS IdPs.

CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/customer-sso-browser.py

Runs only private loopback services, temporary SQLite data and ephemeral keys.
The production public-HTTPS transport and URL validation are used, including
pinned sockets, certificate validation, response limits and redirect rejection.
Two public-shaped HTTPS/443 names map to private TLS sockets only in this
disposable API launcher and Chromium. No production validator is replaced.
No real organization credentials, external provider calls or email are used.
"""
import base64
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from tests.oidc_mock import LocalProvider, tls_fixture
from app.services.account_security import totp_at

PASSWORD = 'Isolated-customer-SSO-browser!2026'
RUN = secrets.token_hex(6)
REPORT = Path(os.getenv('CUSTOMER_SSO_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/customer-sso-browser.json')))
FIXTURE_HOSTS = ('idp-a.customer-sso.example.org', 'idp-b.customer-sso.example.org')


def public_tls_fixture(directory):
    """Own ephemeral certificate, valid for both exact synthetic DNS names."""
    cert, key = tls_fixture(directory)
    certificate = x509.load_pem_x509_certificate(Path(cert).read_bytes())
    private_key = serialization.load_pem_private_key(Path(key).read_bytes(), password=None)
    builder = (x509.CertificateBuilder().subject_name(certificate.subject)
               .issuer_name(certificate.issuer).public_key(certificate.public_key())
               .serial_number(certificate.serial_number)
               .not_valid_before(certificate.not_valid_before_utc)
               .not_valid_after(certificate.not_valid_after_utc))
    for extension in certificate.extensions:
        value = extension.value
        if isinstance(value, x509.SubjectAlternativeName):
            value = x509.SubjectAlternativeName([*value, *(x509.DNSName(host) for host in FIXTURE_HOSTS)])
        builder = builder.add_extension(value, critical=extension.critical)
    Path(cert).write_bytes(builder.sign(private_key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))
    return cert, key


def port():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        return server.getsockname()[1]


def api(base, method, path, user=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if user:
        headers['Authorization'] = 'Bearer ' + user['access_token']
    request = Request(base + path, method=method, headers=headers,
                      data=json.dumps(data).encode() if data is not None else None)
    with urlopen(request, timeout=45) as response:
        return json.load(response)


def rejects(base, method, path, user=None, data=None, statuses=(400, 401, 403, 404, 409)):
    try:
        api(base, method, path, user, data)
    except HTTPError as failure:
        assert failure.code in statuses, 'Unexpected rejection status'
        return failure.code
    raise AssertionError('A forbidden fixture operation succeeded')


def ready(url, processes):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        assert all(process.poll() is None for process in processes), 'An isolated proof service exited before readiness'
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (HTTPError, URLError, TimeoutError):
            pass
        time.sleep(.2)
    raise AssertionError('An isolated proof service did not become ready')


def stop(processes):
    for process in reversed(processes):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


def token(page):
    return page.evaluate("() => localStorage.getItem('freecollab_token')")


def native_user(base, database, name, mfa=False):
    email = name.lower().replace(' ', '-') + '-' + RUN + '@example.com'
    user = api(base, 'POST', '/auth/register', data={'name': name, 'email': email, 'password': PASSWORD})
    user['email'], user['name'] = email, name
    api(base, 'GET', '/account/security', user)
    # This isolated fixture prepares verified members; no verification bypass is
    # introduced into product code. Account/mail browser proofs cover email links.
    with sqlite3.connect(database) as connection:
        connection.execute('UPDATE accountsecurity SET email_verified_at=? WHERE user_id=?',
                           (datetime.utcnow().isoformat(' '), user['user_id']))
    if mfa:
        setup = api(base, 'POST', '/account/mfa/setup', user, {'password': PASSWORD})
        response = api(base, 'POST', '/account/mfa/confirm', user,
                       {'code': totp_at(setup['secret'], int(time.time() // 30))})
        user.update(response['session'])
        user['codes'] = response['recovery_codes']
    return user


def reauth(user, correct=True):
    result = {'password': PASSWORD if correct else 'Incorrect-fixture-password!'}
    if user.get('codes'):
        result['code'] = user['codes'].pop(0) if correct else user['codes'][0]
    return result


def authenticated(page):
    expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
    page.wait_for_selector('[aria-label="Account settings"]', state='attached', timeout=30000)
    if not page.get_by_role('button', name='Account settings', exact=True).is_visible():
        page.get_by_role('button', name='Toggle channels', exact=True).click()
    assert token(page), 'The browser has no native session after authentication'
    assert '#sso=' not in page.url and 'access_token=' not in page.url, 'A callback credential remained in the URL'


def login(page, base, user, workspace):
    page.goto(base, wait_until='domcontentloaded')
    page.evaluate('([key, value]) => localStorage.setItem(key, value)', ['freecollab.workspace:' + user['user_id'], workspace['id']])
    page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
    page.get_by_label('Email', exact=True).fill(user['email'])
    page.get_by_label('Password', exact=True).fill(PASSWORD)
    page.get_by_role('button', name='Sign In', exact=True).click()
    if user.get('codes'):
        page.get_by_label('Authentication or recovery code', exact=True).fill(user['codes'].pop(0))
        page.get_by_role('button', name='Verify and sign in', exact=True).click()
    authenticated(page)


def integrations(page):
    authenticated(page)
    page.get_by_role('button', name='Integrations', exact=True).click()
    panel = page.get_by_role('dialog', name='Integrations', exact=True)
    panel.get_by_role('button', name='Sign-in', exact=True).click()
    return panel


def fill_reauth(scope, user, correct=True):
    credentials = reauth(user, correct)
    scope.get_by_label('Confirm with your FreeCollab password', exact=True).fill(credentials['password'])
    if 'code' in credentials:
        scope.get_by_label('Authentication or recovery code', exact=True).fill(credentials['code'])


def admin_action(panel, user, action, correct=True):
    panel.get_by_role('region', name='Workspace single sign-on', exact=True).get_by_role('button', name=action, exact=True).click()
    confirmation = panel.get_by_role('form', name='Confirm workspace SSO change', exact=True)
    fill_reauth(confirmation, user, correct)
    submit = {'Save SSO configuration': 'Confirm configuration', 'Test connection': 'Confirm connection test',
              'Enable SSO': 'Confirm enable SSO', 'Disable SSO': 'Confirm disable SSO'}[action]
    confirmation.get_by_role('button', name=submit, exact=True).click()
    if correct:
        try:
            expect(confirmation).to_have_count(0)
        except AssertionError:
            # Classify known generic failures; never print form values or raw
            # provider error bodies, authorization URLs or recovery codes.
            text = ' '.join(panel.get_by_role('alert').all_text_contents()).lower()
            category = next((word for word in ['discovery', 'public https', 'password', 'code', 'changed'] if word in text), 'confirmation pending')
            raise AssertionError(action + ': ' + category) from None
    else:
        expect(panel.get_by_role('alert')).to_contain_text(re.compile('password', re.I))
        expect(confirmation.get_by_label('Confirm with your FreeCollab password', exact=True)).to_have_value('')


def no_saved_secrets(page, values):
    assert page.evaluate('(values) => values.every(secret => !Object.values(localStorage).some(value => value.includes(secret)) && !Object.values(sessionStorage).some(value => value.includes(secret)))', values), 'A customer client secret entered browser storage'
    assert all(value not in page.url for value in values), 'A customer client secret entered the URL'


def empty_secret(form):
    field = form.get_by_label('Client secret', exact=True)
    if field.count():
        expect(field).to_have_value('')


def safe_provider(base, workspace, user, secrets_to_exclude):
    config = api(base, 'GET', '/workspaces/' + workspace['id'] + '/sso', user)
    serialized = json.dumps(config)
    assert all(secret not in serialized for secret in secrets_to_exclude), 'A customer client secret was returned by the configuration API'
    assert 'encrypted_client_secret' not in serialized and 'client_secret' not in config.get('provider', {}), 'The SSO API exposed a secret field'
    return config


def sso(page, login_url, provider_name, user=None):
    page.goto(login_url, wait_until='domcontentloaded')
    page.get_by_role('button', name='Continue with ' + provider_name, exact=True).click()
    if user and user.get('codes'):
        expect(page.get_by_label('Authentication or recovery code', exact=True)).to_be_visible(timeout=60000)
        page.get_by_label('Authentication or recovery code', exact=True).fill(user['codes'].pop(0))
        page.get_by_role('button', name='Verify and sign in', exact=True).click()
    authenticated(page)


def rejected_sso(page, provider_name, guidance):
    # SSO starts with fetch, so click's navigation auto-wait cannot observe the
    # later redirect. Wait for the actual provider callback before its guidance.
    # A generic /sign.in/ status match also matches the native submit spinner.
    with page.expect_response(lambda response: urlsplit(response.url).path == '/api/auth/oidc/callback', timeout=60000) as callback:
        page.get_by_role('button', name='Continue with ' + provider_name, exact=True).click()
    assert callback.value.status in (302, 303)
    expect(page.get_by_role('status').filter(has_text=guidance)).to_be_visible(timeout=60000)


def create_launcher(private, providers, api_port):
    """Route exact synthetic hosts to private sockets; retain URL/TLS validation."""
    launcher = private / 'api.py'
    routes = {urlsplit(provider.issuer).hostname: provider.server.server_port for provider in providers}
    # Other existing proofs import this helper with their original loopback
    # providers. Preserve that fixture contract without using it in this proof.
    legacy_origins = {provider.issuer for provider in providers if urlsplit(provider.issuer).hostname == '127.0.0.1'}
    launcher.write_text('''from urllib.parse import urlsplit
import uvicorn
from app.services import oidc_transport
original_addresses = oidc_transport.public_addresses
original_connect = oidc_transport._PinnedBackend.connect_tcp
routes = ''' + repr(routes) + '''
legacy_origins = ''' + repr(legacy_origins) + '''
if legacy_origins:
    original_normalize = oidc_transport.normalize_url
    def legacy_fixture_normalize(value, *, issuer=False):
        if isinstance(value, str):
            parts = urlsplit(value)
            if parts.scheme + '://' + parts.netloc in legacy_origins and parts.scheme == 'https' and not parts.username and not parts.password and not parts.fragment:
                return parts, '127.0.0.1'
        return original_normalize(value, issuer=issuer)
    oidc_transport.normalize_url = legacy_fixture_normalize
def fixture_addresses(host, timeout=5):
    if host in routes:
        return ['127.0.0.1']
    return original_addresses(host, timeout=timeout)
def fixture_connect(self, host, port, timeout=None, local_address=None, socket_options=None):
    if self.hostname in routes and self.ip == host == '127.0.0.1' and port == 443:
        port = routes[self.hostname]
    return original_connect(self, host, port, timeout=timeout, local_address=local_address, socket_options=socket_options)
oidc_transport.public_addresses = fixture_addresses
oidc_transport._PinnedBackend.connect_tcp = fixture_connect
from app.main import socket_app
uvicorn.run(socket_app, host='127.0.0.1', port=''' + str(api_port) + ''', proxy_headers=False, access_log=False)
''')
    return launcher


def create_vite(private, api_base, app_port):
    frontend = private / 'frontend'
    shutil.copytree(ROOT / 'frontend', frontend, ignore=shutil.ignore_patterns('node_modules', 'dist', 'tests', '.vite'))
    (frontend / 'node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
    vite = private / 'vite.mjs'
    vite.write_text('import {createServer} from ' + json.dumps(str(ROOT / 'frontend/node_modules/vite/dist/node/index.js')) + ';\n'
                    'import react from ' + json.dumps(str(ROOT / 'frontend/node_modules/@vitejs/plugin-react/dist/index.js')) + ';\n'
                    'const server=await createServer({configFile:false,root:' + json.dumps(str(frontend)) + ',plugins:[react()],cacheDir:' + json.dumps(str(private / 'vite-cache')) + ",optimizeDeps:{exclude:['@matrix-org/matrix-sdk-crypto-wasm']},server:{host:'127.0.0.1',port:" + str(app_port) + ",strictPort:true,proxy:{'/api':{target:" + json.dumps(api_base) + ",changeOrigin:true,rewrite:p=>p.replace(/^\\/api/,'')},'/socket.io':{target:" + json.dumps(api_base) + ',ws:true}}}});await server.listen();\n')
    return vite


def main():
    stage, results, processes, streams, providers = 'fixture startup', {}, [], [], []
    with tempfile.TemporaryDirectory(prefix='freecollab-customer-sso-') as directory:
        private = Path(directory)
        try:
            cert, key = public_tls_fixture(private / 'tls')
            providers = [LocalProvider(cert_path=cert, key_path=key, client_id='studio-a-' + RUN, client_secret=secrets.token_urlsafe(32)),
                         LocalProvider(cert_path=cert, key_path=key, client_id='studio-b-' + RUN, client_secret=secrets.token_urlsafe(32))]
            for provider, host in zip(providers, FIXTURE_HOSTS):
                provider.issuer = 'https://' + host
            api_port, app_port = port(), port()
            api_base, app_base = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
            database = private / 'proof.db'
            trust = private / 'ca-bundle.pem'
            inherited = os.getenv('SSL_CERT_FILE') or ssl.get_default_verify_paths().cafile
            trust.write_bytes((Path(inherited).read_bytes() if inherited else b'') + b'\n' + Path(cert).read_bytes())
            environment = os.environ.copy()
            environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(database), STORAGE_PATH=str(private / 'files'),
                               JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
                               MESSAGE_ENCRYPTION_KEYS=json.dumps({'customer-proof': base64.b64encode(secrets.token_bytes(32)).decode()}),
                               MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='customer-proof', MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'message-keys.json'),
                               ACCOUNT_SECURITY_KEY_FILE=str(private / 'account.key'), MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled',
                               PUBLIC_APP_URL=app_base, CORS_ORIGINS=json.dumps([app_base]), OIDC_ENABLED='false', OTRUST_ENABLED='false',
                               SSL_CERT_FILE=str(trust), PYTHONPATH=str(ROOT / 'backend'))
            environment['NO_PROXY'] = ','.join(filter(None, [environment.get('NO_PROXY', ''), 'localhost', '127.0.0.1', '::1', *FIXTURE_HOSTS]))
            # urllib honors lowercase no_proxy before NO_PROXY. Both lists keep
            # inherited entries and add only these fixed private fixture routes.
            environment['no_proxy'] = ','.join(filter(None, [environment.get('no_proxy', ''), environment['NO_PROXY']]))
            local_python = ROOT / 'backend/.venv/bin/python'
            python = os.getenv('BACKEND_PYTHON') or (str(local_python) if local_python.exists() else sys.executable)
            for command, filename, cwd in [([python, str(create_launcher(private, providers, api_port))], 'api.log', ROOT / 'backend'),
                                           (['node', str(create_vite(private, api_base, app_port))], 'vite.log', ROOT / 'frontend')]:
                stream = (private / filename).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, start_new_session=True))
            ready(api_base + '/ready', processes); ready(app_base + '/', processes)

            stage = 'independent native workspaces and invited member fixtures'
            owner_a = native_user(api_base, database, 'Studio A Owner', mfa=True)
            owner_b = native_user(api_base, database, 'Studio B Owner')
            member = native_user(api_base, database, 'Shared Native Member', mfa=True)
            outsider = native_user(api_base, database, 'Uninvited Native Member')
            workspace_a = api(api_base, 'POST', '/workspaces', owner_a, {'name': 'Studio A'})
            workspace_b = api(api_base, 'POST', '/workspaces', owner_b, {'name': 'Studio B'})
            for workspace, owner in [(workspace_a, owner_a), (workspace_b, owner_b)]:
                invite = api(api_base, 'POST', '/workspaces/' + workspace['id'] + '/invites', owner, {'role': 'member'})
                api(api_base, 'POST', '/workspaces/invites/accept', member, {'token': invite['token']})
            channel_a = api(api_base, 'POST', '/channels', owner_a, {'name': 'Studio A Room', 'type': 'group',
                            'workspace_id': workspace_a['id'], 'member_ids': [member['user_id']]})
            providers[0].subject, providers[0].email, providers[0].name = 'studio-a-native-member', member['email'], member['name']
            providers[1].subject, providers[1].email, providers[1].name = 'studio-b-native-member', member['email'], member['name']

            with sync_playwright() as playwright:
                resolver_rules = ', '.join('MAP ' + urlsplit(provider.issuer).hostname + ':443 127.0.0.1:' + str(provider.server.server_port) for provider in providers)
                launch = {'headless': True, 'args': ['--no-sandbox', '--host-resolver-rules=' + resolver_rules],
                          'env': {**os.environ, 'NO_PROXY': environment['NO_PROXY'], 'no_proxy': environment['no_proxy']}}
                executable = os.getenv('CHROMIUM_PATH') or shutil.which('chromium')
                if executable:
                    launch['executable_path'] = executable
                browser = playwright.chromium.launch(**launch)
                contexts, page_errors, requested_urls, handoffs = [], [], [], []
                all_secrets = [provider.client_secret for provider in providers]
                try:
                    def new_page(width=1440):
                        context = browser.new_context(ignore_https_errors=True, viewport={'width': width, 'height': 1000}, reduced_motion='reduce', permissions=['clipboard-read', 'clipboard-write'])
                        contexts.append(context)
                        page = context.new_page(); page.set_default_timeout(30000)
                        page.on('pageerror', lambda error: page_errors.append(type(error).__name__))
                        page.on('request', lambda request: requested_urls.append(request.url))
                        page.on('request', lambda request: handoffs.append(request.url) if request.url.endswith('/auth/oidc/exchange') else None)
                        return page

                    pages, configs = [], []
                    for index, (owner, workspace, provider) in enumerate([(owner_a, workspace_a, providers[0]), (owner_b, workspace_b, providers[1])]):
                        stage = 'customer ' + str(index + 1) + ' configures its own SSO in the browser'
                        page = new_page(); pages.append(page)
                        login(page, app_base, owner, workspace)
                        panel = integrations(page)
                        # Product selectors are deliberately accessible labels.
                        form = panel.get_by_role('region', name='Workspace single sign-on', exact=True)
                        expect(form).to_be_visible()
                        callback = form.get_by_label('Callback URI', exact=True)
                        expect(callback).to_have_value(app_base + '/api/auth/oidc/callback')
                        expect(callback).to_have_attribute('readonly', '')
                        form.get_by_role('button', name='Copy callback URI', exact=True).click()
                        expect(panel.get_by_role('status').filter(has_text='Callback URI copied')).to_be_visible()
                        assert page.evaluate('() => navigator.clipboard.readText()') == app_base + '/api/auth/oidc/callback'
                        form.get_by_label('Provider name', exact=True).fill('Studio ' + ('A' if index == 0 else 'B') + ' SSO')
                        form.get_by_label('Issuer URL', exact=True).fill(provider.issuer)
                        form.get_by_label('Client ID', exact=True).fill(provider.client_id)
                        expect(form.get_by_label('Client secret', exact=True)).to_have_attribute('type', 'password')
                        form.get_by_label('Client secret', exact=True).fill(provider.client_secret)
                        form.get_by_label('Scopes', exact=True).fill('openid profile email')
                        if index == 1:
                            native_token = token(page)
                            with page.expect_response(lambda response: response.url.endswith('/workspaces/' + workspace['id'] + '/sso') and response.request.method == 'PUT') as rejected:
                                admin_action(panel, owner, 'Save SSO configuration', correct=False)
                            assert rejected.value.status == 403
                            assert token(page) == native_token
                            assert api(api_base, 'GET', '/auth/me', {'access_token': native_token})['id'] == owner['user_id']
                            assert not api(api_base, 'GET', '/workspaces/' + workspace['id'] + '/sso', owner)['configured']
                            results['wrong_password_config_change_retains_session_and_draft'] = True
                        admin_action(panel, owner, 'Save SSO configuration')
                        empty_secret(form)
                        config = safe_provider(api_base, workspace, owner, all_secrets)
                        assert not config['provider']['enabled'], 'Saving enabled an untested customer IdP'
                        expect(form.get_by_role('button', name='Enable SSO', exact=True)).to_be_disabled()
                        if index == 1:
                            rejects(api_base, 'POST', '/workspaces/' + workspace['id'] + '/sso/enable', owner,
                                    {'password': PASSWORD}, statuses=(400, 409))
                        admin_action(panel, owner, 'Test connection')
                        expect(panel.get_by_role('status').filter(has_text=re.compile('success|validated|passed|verified', re.I))).to_be_visible()
                        admin_action(panel, owner, 'Enable SSO')
                        expect(panel.get_by_role('status').filter(has_text=re.compile('enabled', re.I))).to_be_visible()
                        config = safe_provider(api_base, workspace, owner, all_secrets)
                        assert config['provider']['enabled'] and config['provider']['client_id'] == provider.client_id
                        assert config['provider']['issuer_url'] == provider.issuer
                        assert (config.get('callback_url') or config['provider'].get('callback_url')) == app_base + '/api/auth/oidc/callback'
                        link_field = form.get_by_label('Workspace sign-in link', exact=True)
                        expect(link_field).to_have_attribute('readonly', '')
                        form.get_by_role('button', name='Copy workspace sign-in link', exact=True).click()
                        expect(panel.get_by_role('status').filter(has_text='Workspace sign-in link copied')).to_be_visible()
                        assert page.evaluate('() => navigator.clipboard.readText()') == config['login_url']
                        configs.append(config)
                        no_saved_secrets(page, all_secrets)
                    results['two_customer_owned_idps_configured_in_ui'] = True
                    results['test_required_before_enable'] = True
                    results['server_derived_readonly_callback_and_copyable_customer_link'] = True
                    results['password_and_mfa_admin_reauthentication'] = True
                    print('PASS two independent customers configure, validate and enable their own HTTPS identity providers', flush=True)

                    # The remainder is kept in the same actual services/browser
                    # process so changes and sign-in are tested against live state.
                    stage = 'secret retention after reload and ordinary member isolation'
                    for page, owner, workspace in [(pages[0], owner_a, workspace_a), (pages[1], owner_b, workspace_b)]:
                        page.reload(wait_until='domcontentloaded')
                        panel = integrations(page)
                        form = panel.get_by_role('region', name='Workspace single sign-on', exact=True)
                        empty_secret(form)
                        assert safe_provider(api_base, workspace, owner, all_secrets)['provider']['has_secret']
                        no_saved_secrets(page, all_secrets)
                    # The second owner deliberately saves an ordinary edit using
                    # the explicit retain option. A working later validation
                    # proves the encrypted secret survived an omitted field.
                    retain = pages[1].get_by_role('region', name='Workspace single sign-on', exact=True)
                    retain.get_by_label('Client secret handling', exact=True).select_option(label='Keep saved secret')
                    retain.get_by_label('Scopes', exact=True).fill('openid profile email groups')
                    panel_b = pages[1].get_by_role('dialog', name='Integrations', exact=True)
                    admin_action(panel_b, owner_b, 'Save SSO configuration')
                    empty_secret(retain)
                    retained = safe_provider(api_base, workspace_b, owner_b, all_secrets)
                    assert retained['provider']['has_secret']
                    assert retained['provider']['scopes'] == 'openid profile email groups'
                    admin_action(panel_b, owner_b, 'Test connection')
                    expect(pages[1].get_by_role('status').filter(has_text=re.compile('success|validated|passed|verified', re.I))).to_be_visible()
                    admin_action(panel_b, owner_b, 'Enable SSO')
                    expect(pages[1].get_by_role('status').filter(has_text=re.compile('enabled', re.I))).to_be_visible()
                    results['explicit_keep_secret_on_configuration_save'] = True
                    config_a = safe_provider(api_base, workspace_a, owner_a, all_secrets)
                    config_b = safe_provider(api_base, workspace_b, owner_b, all_secrets)
                    for owner, other in [(owner_a, workspace_b), (owner_b, workspace_a), (member, workspace_a)]:
                        rejects(api_base, 'GET', '/workspaces/' + other['id'] + '/sso', owner, statuses=(403, 404))
                    rejects(api_base, 'POST', '/workspaces/' + workspace_a['id'] + '/sso/disable', member,
                            {'password': PASSWORD}, statuses=(403, 404))
                    member_page = new_page()
                    login(member_page, app_base, member, workspace_a)
                    panel = integrations(member_page)
                    expect(panel.get_by_label('Client ID', exact=True)).to_have_count(0)
                    expect(panel.get_by_label('Client secret', exact=True)).to_have_count(0)
                    assert providers[0].client_id not in panel.inner_text(), 'Ordinary member saw customer provider client settings'
                    results['secret_masked_not_returned_or_stored'] = True
                    results['ordinary_member_and_other_owner_cannot_read_config'] = True

                    stage = 'safe desktop and mobile customer sign-in screenshots'
                    evidence = REPORT.parent
                    evidence.mkdir(parents=True, exist_ok=True)
                    preview = pages[1]
                    preview_panel = preview.get_by_role('dialog', name='Integrations', exact=True)
                    assert not preview_panel.locator('input[type="password"]').count()
                    no_saved_secrets(preview, all_secrets)
                    preview_panel.locator('.fc-account-body').evaluate('node => node.scrollTo(0, 0)')
                    preview.screenshot(path=str(evidence / 'customer-sso-desktop.png'), full_page=True)
                    preview.set_viewport_size({'width': 320, 'height': 900})
                    preview_panel.get_by_role('button', name='Sign-in', exact=True).scroll_into_view_if_needed()
                    assert preview.evaluate('() => document.documentElement.scrollWidth <= innerWidth + 1')
                    preview.screenshot(path=str(evidence / 'customer-sso-mobile.png'), full_page=True)
                    preview.set_viewport_size({'width': 1440, 'height': 1000})
                    results['customer_sign_in_admin_ui_mobile_320px'] = True

                    stage = 'explicit native member identity link requires password and MFA'
                    panel.get_by_role('button', name='Link organization sign-in', exact=True).click()
                    form = panel.get_by_role('form', name='Link organization sign-in', exact=True)
                    before = token(member_page)
                    fill_reauth(form, member, correct=False)
                    form.get_by_role('button', name='Continue to organization', exact=True).click()
                    expect(panel.get_by_role('alert')).to_contain_text(re.compile('password', re.I))
                    assert token(member_page) == before
                    fill_reauth(form, member)
                    before_handoffs = len(handoffs)
                    with member_page.expect_response(lambda response: response.url.endswith('/auth/oidc/exchange') and response.request.method == 'POST', timeout=60000) as linked:
                        form.get_by_role('button', name='Continue to organization', exact=True).click()
                    assert linked.value.status == 200 and linked.value.json().get('linked') is True
                    authenticated(member_page)
                    assert token(member_page) == before, 'Explicit link replaced the existing native session'
                    assert len(handoffs) == before_handoffs + 1, 'StrictMode exchanged a one-time handoff more than once'
                    identities = api(api_base, 'GET', '/auth/oidc/identities', {'access_token': before})
                    assert len(identities) == 1
                    results['explicit_link_password_mfa_and_failed_reauth_retains_session'] = True

                    stage = 'opaque customer login links and no public tenant directory'
                    login_a = config_a.get('login_url') or config_a['provider']['login_url']
                    login_b = config_b.get('login_url') or config_b['provider']['login_url']
                    login_id_a = parse_qs(urlsplit(login_a).fragment)['workspace-sso'][0]
                    login_id_b = parse_qs(urlsplit(login_b).fragment)['workspace-sso'][0]
                    assert login_id_a != workspace_a['id'] and login_id_b != workspace_b['id'] and login_id_a != login_id_b
                    public = api(api_base, 'GET', '/auth/oidc/providers')
                    assert not public['providers'], 'Public provider discovery listed customer workspaces'
                    for login_id, name, other in [(login_id_a, 'Studio A SSO', 'Studio B SSO'), (login_id_b, 'Studio B SSO', 'Studio A SSO')]:
                        targeted = api(api_base, 'GET', '/auth/oidc/providers?login_id=' + login_id)
                        assert len(targeted['providers']) == 1 and targeted['providers'][0]['name'] == name
                        assert other not in json.dumps(targeted)
                    results['opaque_customer_login_links_no_public_directory'] = True

                    stage = 'real customer OIDC sign-in retains local MFA and workspace scope'
                    sso_page = new_page()
                    before_handoffs = len(handoffs)
                    sso(sso_page, login_a, 'Studio A SSO', member)
                    assert len(handoffs) == before_handoffs + 1
                    scoped = {'access_token': token(sso_page)}
                    scoped_me = api(api_base, 'GET', '/auth/me', scoped)
                    assert scoped_me['id'] == member['user_id'] and scoped_me['session_scope'] == 'workspace' and scoped_me['workspace_id'] == workspace_a['id']
                    allowed = api(api_base, 'GET', '/workspaces', scoped)
                    assert [value['id'] for value in allowed] == [workspace_a['id']], 'Customer SSO session escaped into another workspace'
                    rejects(api_base, 'GET', '/auth/oidc/providers?workspace_id=' + workspace_b['id'], scoped, statuses=(403, 404))
                    rejects(api_base, 'GET', '/account/security', scoped, statuses=(403,))
                    scoped_panel = integrations(sso_page)
                    expect(scoped_panel.get_by_role('region', name='Workspace sign-in session', exact=True)).to_be_visible()
                    expect(scoped_panel.get_by_role('button', name='Continue with FreeCollab password', exact=True)).to_be_visible()
                    expect(scoped_panel.get_by_label('Client secret', exact=True)).to_have_count(0)
                    scoped_panel.get_by_role('button', name='Close integrations', exact=True).click()
                    assert providers[0].exchange_count >= 2, 'The browser did not perform actual PKCE token exchanges'
                    results['actual_https_authorization_code_pkce_jwks'] = True
                    results['native_mfa_retained_for_customer_sso'] = True
                    results['customer_session_only_accesses_its_workspace'] = True

                    stage = 'non-Matrix scoped encryption unlock UI with real password and MFA grant'
                    initial_secure = api(api_base, 'GET', '/secure/status', scoped)
                    assert initial_secure['requires_encryption_unlock'] and initial_secure['mfa_required'] and not initial_secure['enabled']
                    # Only the availability presentation is replaced: all token,
                    # password/MFA, expiry and tenant checks use the real API.
                    # After unlock actual enabled=false avoids Matrix traffic.
                    def show_unlock_form(route):
                        response = route.fetch()
                        payload = response.json()
                        if payload['requires_encryption_unlock']:
                            payload['enabled'] = True
                        route.fulfill(response=response, json=payload)
                    sso_page.route('**/api/secure/status', show_unlock_form)
                    sso_page.get_by_role('button', name='Server-encrypted messages', exact=True).click()
                    encryption = sso_page.get_by_role('dialog', name='Encryption devices and recovery', exact=True)
                    unlock = encryption.get_by_role('form', name='Unlock encrypted messaging', exact=True)
                    expect(unlock).to_be_visible()
                    unlock.get_by_label('FreeCollab password', exact=True).fill('Incorrect-fixture-password!')
                    unlock.get_by_label('Authentication or recovery code', exact=True).fill(member['codes'][0])
                    unlock.get_by_role('button', name='Unlock encrypted messaging', exact=True).click()
                    expect(encryption.get_by_role('alert')).to_contain_text(re.compile('password', re.I))
                    assert token(sso_page) == scoped['access_token']
                    assert api(api_base, 'GET', '/secure/status', scoped)['requires_encryption_unlock']
                    unlock.get_by_label('FreeCollab password', exact=True).fill(PASSWORD)
                    unlock.get_by_label('Authentication or recovery code', exact=True).fill(member['codes'].pop(0))
                    unlock.get_by_role('button', name='Unlock encrypted messaging', exact=True).click()
                    expect(encryption.get_by_role('status').filter(has_text='Encryption access unlocked')).to_be_visible()
                    assert not api(api_base, 'GET', '/secure/status', scoped)['requires_encryption_unlock']
                    assert [value['id'] for value in api(api_base, 'GET', '/workspaces', scoped)] == [workspace_a['id']]
                    rejects(api_base, 'GET', '/account/security', scoped, statuses=(403,))
                    with sqlite3.connect(database) as connection:
                        assert connection.execute('SELECT COUNT(*) FROM matrixsession').fetchone()[0] == 0
                    encryption.get_by_role('button', name='Close encryption settings', exact=True).click()
                    sso_page.unroute('**/api/secure/status', show_unlock_form)
                    results['non_matrix_scoped_encryption_unlock_ui_fixture'] = True
                    results['actual_password_mfa_encryption_grant_preserves_tenant_boundary'] = True

                    stage = 'unlinked tenant and uninvited identity cannot create or move accounts'
                    wrong_page = new_page()
                    wrong_page.goto(login_b, wait_until='domcontentloaded')
                    stage = 'start sign-in at unlinked second customer provider'
                    rejected_sso(wrong_page, 'Studio B SSO', 'accept your workspace invitation')
                    assert not token(wrong_page)
                    assert len(api(api_base, 'GET', '/auth/oidc/identities', {'access_token': before})) == 1
                    providers[0].subject, providers[0].email = 'uninvited-identity', outsider['email']
                    stage = 'open first customer link after rejected second customer callback'
                    wrong_page.goto(login_a, wait_until='domcontentloaded')
                    stage = 'start first customer sign-in for uninvited identity'
                    rejected_sso(wrong_page, 'Studio A SSO', 'accept your workspace invitation')
                    assert not token(wrong_page)
                    stage = 'unknown external identity cannot provision a native account'
                    with sqlite3.connect(database) as connection:
                        users_before = connection.execute('SELECT COUNT(*) FROM user').fetchone()[0]
                    providers[0].subject, providers[0].email = 'unknown-external-identity', 'never-created-' + RUN + '@example.com'
                    wrong_page.goto(login_a, wait_until='domcontentloaded')
                    rejected_sso(wrong_page, 'Studio A SSO', 'accept your workspace invitation')
                    assert not token(wrong_page)
                    with sqlite3.connect(database) as connection:
                        assert connection.execute('SELECT COUNT(*) FROM user').fetchone()[0] == users_before
                    results['unknown_external_identity_cannot_provision_native_account'] = True
                    providers[0].subject, providers[0].email = 'studio-a-native-member', member['email']
                    providers[0].claim_overrides = {'iss': providers[1].issuer}
                    stage = 'open customer link for wrong-issuer identity rejection'
                    wrong_page.goto(login_a, wait_until='domcontentloaded')
                    stage = 'start first customer sign-in with forged issuer claim'
                    rejected_sso(wrong_page, 'Studio A SSO', 'Single sign-on could not be completed. Please start again.')
                    assert not token(wrong_page)
                    providers[0].claim_overrides = {}
                    assert len(api(api_base, 'GET', '/auth/oidc/identities', {'access_token': before})) == 1
                    results['unlinked_wrong_tenant_and_uninvited_identity_rejected'] = True
                    results['wrong_provider_issuer_cannot_move_identity'] = True

                    stage = 'customer client secret rotation invalidates only its configuration'
                    panel = pages[0].get_by_role('dialog', name='Integrations', exact=True)
                    form = panel.get_by_role('region', name='Workspace single sign-on', exact=True)
                    form.get_by_label('Client secret handling', exact=True).select_option(label='Replace secret')
                    rotated = secrets.token_urlsafe(32); all_secrets.append(rotated)
                    form.get_by_label('Client secret', exact=True).fill(rotated)
                    admin_action(panel, owner_a, 'Save SSO configuration')
                    providers[0].client_secret = rotated
                    empty_secret(form)
                    changed = safe_provider(api_base, workspace_a, owner_a, all_secrets)
                    assert changed['provider']['revision'] > config_a['provider']['revision'] and not changed['provider']['enabled']
                    rejects(api_base, 'GET', '/auth/me', scoped, statuses=(401,))
                    unchanged = safe_provider(api_base, workspace_b, owner_b, all_secrets)
                    assert unchanged['provider']['revision'] == config_b['provider']['revision'] and unchanged['provider']['enabled']
                    admin_action(panel, owner_a, 'Test connection')
                    expect(panel.get_by_role('status').filter(has_text=re.compile('success|validated|passed|verified', re.I))).to_be_visible()
                    admin_action(panel, owner_a, 'Enable SSO')
                    expect(panel.get_by_role('status').filter(has_text=re.compile('enabled', re.I))).to_be_visible()
                    rotated_page = new_page(width=320)
                    sso(rotated_page, login_a, 'Studio A SSO', member)
                    assert api(api_base, 'GET', '/auth/me', {'access_token': token(rotated_page)})['id'] == member['user_id']
                    no_saved_secrets(pages[0], all_secrets)
                    results['rotation_requires_retest_and_other_customer_unchanged'] = True
                    results['real_login_after_secret_rotation'] = True

                    stage = 'disable one customer and preserve mobile native fallback'
                    admin_action(panel, owner_a, 'Disable SSO')
                    expect(panel.get_by_role('status').filter(has_text=re.compile('disabled', re.I))).to_be_visible()
                    assert not safe_provider(api_base, workspace_a, owner_a, all_secrets)['provider']['enabled']
                    assert safe_provider(api_base, workspace_b, owner_b, all_secrets)['provider']['enabled']
                    rejects(api_base, 'GET', '/auth/oidc/providers?login_id=' + login_id_a, statuses=(404,))
                    assert len(api(api_base, 'GET', '/auth/oidc/providers?login_id=' + login_id_b)['providers']) == 1
                    stage = 'native password and MFA fallback after disabling one customer'
                    fallback = new_page(width=320)
                    login(fallback, app_base, member, workspace_a)
                    assert fallback.evaluate('() => document.documentElement.scrollWidth <= innerWidth + 1')
                    assert not page_errors, 'Unexpected browser page exception'
                    assert not any('access_token=' in url or re.search(r'eyJ[A-Za-z0-9_-]+\.', url) for url in requested_urls), 'A bearer token entered a browser request URL'
                    assert not any(secret in url for secret in all_secrets for url in requested_urls), 'A customer secret entered a browser request URL'
                    for stream in streams:
                        stream.flush()
                    assert all(secret not in path.read_text(errors='replace') for secret in all_secrets for path in [private / 'api.log', private / 'vite.log']), 'A customer secret entered a fixture service log'
                    with sqlite3.connect(database) as connection:
                        stored = [row[0] for row in connection.execute('SELECT encrypted_client_secret FROM workspaceoidcprovider')]
                    assert stored and all(not any(secret in (value or '') for secret in all_secrets) for value in stored), 'A customer secret was stored in plaintext'
                    results['disable_scoped_to_one_customer'] = True
                    results['native_password_mfa_fallback_mobile_320px'] = True
                    results['secrets_encrypted_at_rest'] = True
                    results['no_bearer_or_secret_in_urls'] = True
                    results['zero_browser_page_errors'] = True
                    print('PASS native member linking, actual customer OIDC/MFA, tenant isolation, secret rotation, scoped disable and320px native fallback', flush=True)
                finally:
                    for context in contexts:
                        context.close()
                    browser.close()
            REPORT.parent.mkdir(parents=True, exist_ok=True)
            REPORT.write_text(json.dumps({'scope': 'Isolated real API/Vite/Chromium and two synthetic HTTPS customer IdPs; no operator IdP or external credentials',
                                          'results': results,
                                          'cleanup': 'Own ephemeral services, database, private keys, CA bundle and browser contexts removed in finally'}, indent=2) + '\n')
        except Exception as failure:
            # Never print Playwright call logs: they may include filled passwords,
            # recovery codes or ephemeral provider authorization codes.
            summary = str(failure).splitlines()[0] if str(failure) else 'assertion failed'
            print('FAIL isolated customer SSO proof stage:', stage, '(' + type(failure).__name__ + ')', summary[:180], file=sys.stderr)
            for line in str(failure).splitlines():
                if 'waiting for get_by_role(' in line:
                    print('Safe locator diagnostic:', line.strip()[:180], file=sys.stderr)
                    break
            raise SystemExit(1) from None
        finally:
            stop(processes)
            for stream in streams:
                stream.close()
            for provider in providers:
                provider.close()


if __name__ == '__main__':
    main()
