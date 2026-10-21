"""Real account/workspace UI flows against disposable local services.

CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/account-browser.py
Set CHAT_BASE_URL and ACCOUNT_MAIL_PATH for an isolated development server.
Never target a customer database. No passwords, tokens or recovery codes are printed.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import struct
import time
import uuid
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright


BASE = os.getenv('CHAT_BASE_URL', 'http://127.0.0.1:3000').rstrip('/')
MAIL = Path(os.getenv('ACCOUNT_MAIL_PATH', '/workspace/.freecollab/product-mail'))
PASSWORD = 'Account-browser-test!2026'
RUN = uuid.uuid4().hex[:12]


def api(method, path, user=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if user:
        headers['Authorization'] = 'Bearer ' + user['access_token']
    request = Request(BASE + '/api' + path, method=method, headers=headers,
                      data=json.dumps(data).encode() if data is not None else None)
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def register(name):
    email = f'{name.lower()}-{RUN}@example.com'
    result = api('POST', '/auth/register', data={'name': name, 'email': email, 'password': PASSWORD})
    return {**result, 'email': email}


def login(page, user, password=PASSWORD):
    page.goto(BASE, wait_until='domcontentloaded', timeout=90000)
    page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
    page.get_by_label('Email', exact=True).fill(user['email'])
    page.get_by_label('Password', exact=True).fill(password)
    page.get_by_role('button', name='Sign In', exact=True).click()


def open_sidebar(page):
    toggle = page.get_by_role('button', name='Toggle channels', exact=True)
    expect(toggle).to_be_visible()
    if toggle.get_attribute('aria-expanded') == 'false':
        toggle.click()


def fit(page, dialog):
    expect(dialog).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Viewport overflow'
    assert dialog.evaluate('element => element.scrollWidth <= element.clientWidth + 1'), 'Dialog overflow'
    assert dialog.evaluate('element => element.contains(document.activeElement)'), 'Focus outside dialog'
    for key in ['Shift+Tab', 'Tab', 'Tab', 'Tab']:
        page.keyboard.press(key)
        assert dialog.evaluate('element => element.contains(document.activeElement)'), 'Focus escaped dialog'


def workspace_menu(page):
    page.get_by_role('button', name=re.compile('^Switch workspace')).click()
    return page.get_by_role('menu', name='Your workspaces', exact=True)


def otp(secret):
    key = base64.b32decode(secret + '=' * ((8 - len(secret) % 8) % 8))
    digest = hmac.new(key, struct.pack('>Q', int(time.time()) // 30), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return f'{(struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000:06d}'


def mail_link(email, kind, exclude=None):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        for path in sorted(MAIL.glob('*.eml'), key=lambda item: item.stat().st_mtime, reverse=True):
            message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
            if email not in str(message['To']):
                continue
            body = message.get_body(preferencelist=('plain',)).get_content()
            match = re.search(r'[^\s]+#' + kind + r'=([^\s]+)', body)
            if match:
                candidate = BASE + '#' + kind + '=' + match[1]
                if candidate != exclude:
                    return candidate
        time.sleep(.2)
    raise AssertionError('Expected private development mail was not delivered')


alice, bob = register('AccountAlice'), register('AccountBob')
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or None,
                                headless=True, args=['--no-sandbox'])
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
    page = context.new_page()
    page.set_default_timeout(30000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        login(page, alice)
        expect(page.get_by_role('button', name='Account settings', exact=True)).to_be_visible()
        page.get_by_role('button', name='Account settings', exact=True).click()
        account = page.get_by_role('dialog', name='Account settings', exact=True)
        fit(page, account)
        expect(account.get_by_text(alice['email'], exact=True)).to_be_visible()
        # Signup already queues a link. A resend invalidates it immediately,
        # while its mail can still arrive before the replacement is delivered.
        initial_verification = mail_link(alice['email'], 'verify-email')
        account.get_by_role('button', name='Send verification email', exact=True).click()
        expect(account.get_by_role('status')).to_contain_text('queued')
        verification = mail_link(alice['email'], 'verify-email', exclude=initial_verification)
        account.get_by_role('button', name='Close account settings', exact=True).click()
        page.goto(verification, wait_until='domcontentloaded')
        page.get_by_role('button', name='Verify email', exact=True).click()
        expect(page.get_by_role('heading', name='Email verified.', exact=True)).to_be_visible()
        page.get_by_role('button', name='Continue', exact=True).click()

        workspace_menu(page).get_by_role('menuitem', name='New workspace', exact=True).click()
        settings = page.get_by_role('dialog', name='Workspace settings', exact=True)
        settings.get_by_label('Workspace name', exact=True).fill('Studio North ' + RUN)
        settings.get_by_role('button', name='Create workspace', exact=True).click()
        expect(settings.get_by_role('heading', name='Workspace settings.', exact=True)).to_be_visible()
        expect(settings.get_by_role('textbox', name='Workspace name', exact=True)).to_have_value('Studio North ' + RUN)
        settings.get_by_role('button', name='Invitations', exact=True).click()
        settings.get_by_label('Workspace role', exact=True).select_option('member')
        settings.get_by_role('button', name='Create workspace invitation', exact=True).click()
        invitation = settings.get_by_label('Workspace invitation link', exact=True).input_value()
        assert '#workspace-invite=' in invitation
        settings.get_by_role('button', name='Close workspace settings', exact=True).click()

        guest_context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
        guest = guest_context.new_page()
        guest.set_default_timeout(30000)
        login(guest, bob)
        open_sidebar(guest)
        expect(guest.get_by_role('button', name='Account settings', exact=True)).to_be_visible()
        guest.goto(invitation, wait_until='domcontentloaded')
        guest.get_by_role('button', name='Join workspace', exact=True).click()
        open_sidebar(guest)
        expect(guest.get_by_role('button', name=re.compile('^Switch workspace, Studio North'))).to_be_visible()
        assert guest.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Invitation mobile overflow'
        guest_context.close()

        page.get_by_role('button', name='Account settings', exact=True).click()
        account = page.get_by_role('dialog', name='Account settings', exact=True)
        account.get_by_role('button', name='Sessions', exact=True).click()
        expect(account.get_by_text('Current', exact=True)).to_be_visible()
        account.get_by_role('button', name='Security', exact=True).click()
        account.get_by_label('Current password', exact=True).first.fill(PASSWORD)
        account.get_by_role('button', name='Set up two-factor authentication', exact=True).click()
        expect(account.get_by_label('Authenticator code', exact=True)).to_be_visible()
        secret = account.locator('.fc-account-secret code').inner_text()
        account.get_by_label('Authenticator code', exact=True).fill(otp(secret))
        account.get_by_role('button', name='Enable two-factor authentication', exact=True).click()
        expect(account.get_by_role('heading', name='Save your sign-in recovery codes', exact=True)).to_be_visible()
        recovery_codes = account.locator('.fc-account-recovery-codes code').all_text_contents()
        assert len(recovery_codes) >= 8
        account.get_by_role('button', name='Close account settings', exact=True).click()
        expect(account).to_be_visible()
        account.get_by_label('I have stored these codes somewhere safe.', exact=True).check()
        account.get_by_role('button', name='Done', exact=True).first.click()
        account.get_by_role('button', name='Close account settings', exact=True).click()

        # New browser must complete a real second-factor challenge; no token is seeded.
        mfa_context = browser.new_context(viewport={'width': 320, 'height': 844}, reduced_motion='reduce')
        mfa = mfa_context.new_page()
        mfa.set_default_timeout(30000)
        login(mfa, alice)
        mfa.get_by_label('Authentication or recovery code', exact=True).fill(recovery_codes[0])
        mfa.get_by_role('button', name='Verify and sign in', exact=True).click()
        open_sidebar(mfa)
        expect(mfa.get_by_role('button', name='Account settings', exact=True)).to_be_visible()
        mfa.get_by_role('button', name='Account settings', exact=True).click()
        mobile_account = mfa.get_by_role('dialog', name='Account settings', exact=True)
        fit(mfa, mobile_account)
        mobile_account.get_by_role('button', name='Security', exact=True).click()
        expect(mobile_account.get_by_text('Enabled', exact=True)).to_be_visible()
        assert mfa.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile security overflow'
        mfa.screenshot(path='/workspace/.freecollab/product-account-mobile.png')
        mobile_account.get_by_role('button', name='Close account settings', exact=True).click()
        mfa_context.close()

        # A public reset link uses a separate key from history recovery and revokes MFA sessions.
        reset_context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
        reset_page = reset_context.new_page()
        reset_page.set_default_timeout(30000)
        reset_page.goto(BASE)
        reset_page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
        reset_page.get_by_role('button', name='Forgot password?', exact=True).click()
        reset_page.get_by_label('Email', exact=True).fill(alice['email'])
        reset_page.get_by_role('button', name='Send reset link', exact=True).click()
        expect(reset_page.get_by_role('status')).to_be_visible()
        reset_page.goto(mail_link(alice['email'], 'reset-password'), wait_until='domcontentloaded')
        reset_page.get_by_label('New password', exact=True).fill(PASSWORD + '-new')
        reset_page.get_by_label('Confirm new password', exact=True).fill(PASSWORD + '-new')
        reset_page.get_by_role('button', name='Reset password', exact=True).click()
        expect(reset_page.get_by_role('heading', name='A fresh start.', exact=True)).to_be_visible()
        expect(reset_page.get_by_text('Encrypted history still requires a trusted device or its separate recovery key.', exact=True)).to_be_visible()
        reset_context.close()
        assert not errors, errors
        print('Account browser flows passed: verification, workspace create/join, sessions, MFA enrollment/challenge, password recovery, keyboard focus and 320px layout.')
    finally:
        context.close()
        browser.close()
