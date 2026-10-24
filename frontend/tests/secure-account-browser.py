"""Exercise the real encrypted DM, device verification and recovery UI.

Run against disposable local services with the Matrix service enabled:
CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/secure-account-browser.py
No fixture tokens, private keys or plaintext messages are printed.
"""
from ui_navigation import open_chat_tool, reveal_chat_tool
import json
import os
import re
import uuid
import base64
import hashlib
import hmac
import struct
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from playwright.sync_api import expect, sync_playwright


BASE = os.getenv('CHAT_BASE_URL', 'http://127.0.0.1:3000').rstrip('/')
PROOF_DIR = Path(os.getenv('FREECOLLAB_PROOF_DIR', str(Path(__file__).resolve().parents[2].parent / '.freecollab')))
PASSWORD = 'Secure-account-browser!2026'
RUN = uuid.uuid4().hex[:12]


def api(method, path, user=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if user:
        headers['Authorization'] = 'Bearer ' + user['access_token']
    with urlopen(Request(BASE + '/api' + path, method=method, headers=headers,
                         data=json.dumps(data).encode() if data is not None else None), timeout=60) as response:
        return json.load(response)


def register(name):
    email = f'{name.lower()}-{RUN}@example.com'
    return {**api('POST', '/auth/register', data={'name': name, 'email': email, 'password': PASSWORD}), 'email': email}


def enable_mfa(user):
    setup = api('POST', '/account/mfa/setup', user, {'password': PASSWORD})
    secret = setup['secret']
    key = base64.b32decode(secret + '=' * ((8 - len(secret) % 8) % 8))
    digest = hmac.new(key, struct.pack('>Q', int(time.time()) // 30), hashlib.sha1).digest()
    offset = digest[-1] & 15
    code = f'{(struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000:06d}'
    response = api('POST', '/account/mfa/confirm', user, {'code': code})
    user.update(response['session'])
    user['mfa_codes'] = response['recovery_codes']


def login(page, user):
    page.goto(BASE, wait_until='domcontentloaded', timeout=90000)
    page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
    page.get_by_label('Email', exact=True).fill(user['email'])
    page.get_by_label('Password', exact=True).fill(PASSWORD)
    page.get_by_role('button', name='Sign In', exact=True).click()
    if user.get('mfa_codes'):
        page.get_by_label('Authentication or recovery code', exact=True).fill(user['mfa_codes'].pop(0))
        page.get_by_role('button', name='Verify and sign in', exact=True).click()
    reveal_chat_tool(page, 'Account settings')
    expect(page.get_by_role('button', name='Account settings', exact=True)).to_be_visible(timeout=90000)
    page.get_by_role('button', name=re.compile('^Switch workspace, ')).click()
    page.get_by_role('menuitemradio', name=re.compile('Secure Studio')).click()


def direct_picker(page):
    page.get_by_role('button', name=re.compile('^Switch workspace')).click()
    page.get_by_role('menuitem', name='Direct messages', exact=True).click()
    dialog = page.get_by_role('dialog', name='Direct messages', exact=True)
    expect(dialog).to_be_visible()
    return dialog


def encryption_settings(page):
    open_chat_tool(page, 'End-to-end encrypted')
    dialog = page.get_by_role('dialog', name='Encryption devices and recovery', exact=True)
    expect(dialog.get_by_role('heading', name='Encrypted history recovery', exact=True)).to_be_visible(timeout=90000)
    return dialog


def assert_revoked_session(token):
    try:
        api('GET', '/auth/me', {'access_token': token})
    except HTTPError as error:
        assert error.code == 401, 'Revoked device session returned an unexpected status'
    else:
        raise AssertionError('Revoking an encryption device left its native session active')


alice, bob = register('SecureUIAlice'), register('SecureUIBob')
enable_mfa(alice)
assert api('GET', '/secure/status', alice)['enabled'], 'A disposable Matrix-enabled server is required'
workspace = api('POST', '/workspaces', alice, {'name': 'Secure Studio ' + RUN})
invite = api('POST', '/workspaces/' + workspace['id'] + '/invites', alice, {'role': 'member'})
api('POST', '/workspaces/invites/accept', bob, {'token': invite['token']})

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or None,
                                headless=True, args=['--no-sandbox'])
    contexts = [browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce') for _ in range(2)]
    pages = [context.new_page() for context in contexts]
    errors = []
    for page in pages:
        page.set_default_timeout(60000)
        page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        left, right = pages
        login(left, alice)
        print('Secure UI: sender workspace opened.', flush=True)
        picker = direct_picker(left)
        picker.get_by_role('button', name=re.compile('^SecureUIBob')).click()
        reveal_chat_tool(left, 'End-to-end encrypted')
        expect(left.get_by_role('button', name='End-to-end encrypted', exact=True)).to_be_visible(timeout=90000)
        print('Secure UI: encrypted DM provisioned.', flush=True)
        # The sender opens real recovery settings; setup key is shown once, then only supplied to unlock.
        security = encryption_settings(left)
        security.get_by_role('button', name='Set up encrypted history recovery', exact=True).click()
        expect(security.get_by_role('heading', name='Save your encryption recovery key', exact=True)).to_be_visible(timeout=90000)
        print('Secure UI: recovery key generated.', flush=True)
        recovery_key = security.locator('.fc-secure-generated-key > code').inner_text()
        assert recovery_key and not left.evaluate('(key) => Object.values(localStorage).some(value => value.includes(key))', recovery_key)
        security.get_by_role('button', name='Close encryption settings', exact=True).click()
        expect(security).to_be_visible()
        security.get_by_label('I have stored this recovery key somewhere safe.', exact=True).check()
        security.get_by_role('button', name='I have saved my key', exact=True).click()
        security.get_by_role('button', name='Lock recovery key', exact=True).click()
        security.get_by_label('Encryption recovery key', exact=True).fill(recovery_key)
        security.get_by_role('button', name='Unlock encrypted history', exact=True).click()
        expect(security.get_by_role('status')).to_contain_text('unlocked', timeout=90000)
        print('Secure UI: recovery key locked and unlocked.', flush=True)
        security.get_by_role('button', name='Channel devices', exact=True).click()
        own_fingerprint = security.locator('.fc-secure-fingerprint > code').inner_text()
        assert len(own_fingerprint.replace(' ', '')) > 30
        security.get_by_role('button', name='Close encryption settings', exact=True).click()

        # The recipient opens the same canonical encrypted DM and supplies their public fingerprint.
        login(right, bob)
        picker = direct_picker(right)
        picker.get_by_role('button', name=re.compile('^SecureUIAlice')).click()
        reveal_chat_tool(right, 'End-to-end encrypted')
        expect(right.get_by_role('button', name='End-to-end encrypted', exact=True)).to_be_visible(timeout=90000)
        print('Secure UI: recipient encryption device opened.', flush=True)
        other_security = encryption_settings(right)
        other_security.get_by_role('button', name='Channel devices', exact=True).click()
        other_fingerprint = other_security.locator('.fc-secure-fingerprint > code').inner_text()
        other_security.get_by_role('button', name='Close encryption settings', exact=True).click()

        security = encryption_settings(left)
        security.get_by_role('button', name='Channel devices', exact=True).click()
        verify = security.get_by_role('button', name='Compare and verify device', exact=True)
        expect(verify).to_have_count(1, timeout=90000)
        verify.click()
        security.get_by_label('Fingerprint from the other device', exact=True).fill('incorrect-fingerprint')
        security.get_by_label('I checked this fingerprint through a separate trusted channel.', exact=True).check()
        security.get_by_role('button', name='Verify device', exact=True).click()
        expect(security.get_by_role('alert')).to_contain_text('mismatch')
        security.get_by_label('Fingerprint from the other device', exact=True).fill(other_fingerprint)
        security.get_by_role('button', name='Verify device', exact=True).click()
        expect(security.get_by_role('status')).to_contain_text('Device verified')
        security.get_by_role('button', name='Close encryption settings', exact=True).click()

        # Personal DM lifecycle is real API state, independently visible on reopen.
        picker = direct_picker(left)
        picker.get_by_role('button', name='Archive conversation with SecureUIBob', exact=True).click()
        expect(picker.get_by_role('status')).to_contain_text('archived')
        picker.get_by_role('button', name='Archived', exact=True).click()
        picker.get_by_role('button', name='Restore conversation with SecureUIBob', exact=True).click()
        picker.get_by_role('button', name='Show active', exact=True).click()
        picker.get_by_role('button', name='Block SecureUIBob', exact=True).click()
        picker.get_by_role('button', name='Block person', exact=True).click()
        expect(picker.get_by_role('button', name='Unblock SecureUIBob', exact=True)).to_be_visible()
        picker.get_by_role('button', name='Unblock SecureUIBob', exact=True).click()
        expect(picker.get_by_role('status')).to_contain_text('unblocked')
        picker.get_by_role('button', name='Close direct messages', exact=True).click()

        # Losing another browser revokes only that device and its linked account session.
        lost_context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
        contexts.append(lost_context)
        lost = lost_context.new_page()
        lost.set_default_timeout(60000)
        lost.on('pageerror', lambda error: errors.append(str(error)))
        # Desktop navigation for setup; the revocation confirmation is checked at mobile width below.
        lost.set_viewport_size({'width': 1440, 'height': 1000})
        login(lost, alice)
        direct_picker(lost).get_by_role('button', name=re.compile('^SecureUIBob')).click()
        lost_security = encryption_settings(lost)
        lost_security.get_by_role('button', name='Channel devices', exact=True).click()
        lost_device = lost_security.locator('.fc-secure-device-id').first.inner_text().removeprefix('Device ID: ').strip()
        lost_token = lost.evaluate('localStorage.getItem("freecollab_token")')
        lost_security.get_by_role('button', name='Close encryption settings', exact=True).click()

        security = encryption_settings(left)
        security.get_by_role('button', name='Channel devices', exact=True).click()
        security.get_by_role('button', name='Revoke encryption device ' + lost_device, exact=True).click()
        confirmation = security.get_by_role('form', name='Revoke encryption device', exact=True)
        expect(confirmation.get_by_role('button', name='Revoke device', exact=True)).to_be_disabled()
        confirmation.get_by_label('Confirm with your password', exact=True).fill('incorrect-password')
        confirmation.get_by_label('Authentication or recovery code', exact=True).fill(alice['mfa_codes'].pop(0))
        confirmation.get_by_label('I understand this device will lose access to new encrypted messages.', exact=True).check()
        confirmation.get_by_role('button', name='Revoke device', exact=True).click()
        expect(security.get_by_role('alert')).to_contain_text('incorrect')
        assert left.evaluate('localStorage.getItem("freecollab_token")'), 'Failed reauthentication signed out the current session'
        confirmation.get_by_label('Confirm with your password', exact=True).fill(PASSWORD)
        confirmation.get_by_label('Authentication or recovery code', exact=True).fill(alice['mfa_codes'].pop(0))
        confirmation.get_by_role('button', name='Revoke device', exact=True).click()
        expect(security.get_by_role('status')).to_contain_text('Device revoked', timeout=90000)
        expect(security.get_by_role('button', name='Revoke encryption device ' + lost_device, exact=True)).to_have_count(0)
        assert_revoked_session(lost_token)
        assert left.evaluate('localStorage.getItem("freecollab_token")'), 'Revoking another device signed out this session'
        security.get_by_role('button', name='Refresh encryption devices', exact=True).click()
        expect(security.get_by_role('heading', name='Devices in this channel', exact=True)).to_be_visible()
        expect(security.get_by_role('button', name='Revoke encryption device ' + lost_device, exact=True)).to_have_count(0)

        # Revoking the current device has an explicit sign-out flow and also works at 320px.
        current_token = left.evaluate('localStorage.getItem("freecollab_token")')
        left.set_viewport_size({'width': 320, 'height': 844})
        security.get_by_role('button', name='Revoke this encryption device', exact=True).click()
        confirmation = security.get_by_role('form', name='Revoke encryption device', exact=True)
        expect(confirmation.get_by_text('You are revoking this browser. You will be signed out.', exact=True)).to_be_visible()
        assert left.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Device revocation mobile overflow'
        confirmation.get_by_label('Confirm with your password', exact=True).fill(PASSWORD)
        confirmation.get_by_label('Authentication or recovery code', exact=True).fill(alice['mfa_codes'].pop(0))
        confirmation.get_by_label('I understand this device will lose access to new encrypted messages.', exact=True).check()
        confirmation.get_by_role('button', name='Revoke device and sign out', exact=True).click()
        expect(left.get_by_role('button', name='Sign In', exact=True)).to_be_visible(timeout=90000)
        expect(left.get_by_text('This encryption device and its linked session have been revoked. Sign in again to create a new trusted device.', exact=True)).to_be_visible()
        assert not left.evaluate('localStorage.getItem("freecollab_token")')
        assert_revoked_session(current_token)
        assert not errors, errors
        print('Encrypted account UI passed: canonical encrypted DM, recovery setup/lock/unlock, fingerprint verification, archive/block, lost-device revocation with fresh password and MFA, linked-session revocation and current-device sign-out at 320px.')
    except Exception:
        pages[0].screenshot(path=str(PROOF_DIR / 'secure-account-failure.png'), mask=[pages[0].locator('.fc-secure-generated-key > code, #secure-revoke-code, #auth-code')])
        raise
    finally:
        for context in contexts:
            context.close()
        browser.close()
