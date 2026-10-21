"""Late workspace/account responses must never replace a later signed-in account.

Uses real API fixture sessions and delayed browser responses against disposable services.
CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/account-race-browser.py
"""
import json
import os
import re
import uuid
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright


BASE = os.getenv('CHAT_BASE_URL', 'http://127.0.0.1:3000').rstrip('/')
PASSWORD = 'Account-race-browser!2026'
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


alice, bob = register('RaceAlice'), register('RaceBob')
bob_workspace = api('POST', '/workspaces', bob, {'name': 'Bob private ' + RUN})
alice_security = api('GET', '/account/security', alice)
SWITCH_ACCOUNT = """fixture => {
  const session = fixture.session;
  if (fixture.workspace) localStorage.setItem('freecollab.workspace:' + session.user_id, fixture.workspace);
  localStorage.setItem('freecollab_token', session.access_token);
  localStorage.setItem('freecollab_user_id', session.user_id);
  localStorage.setItem('freecollab_public_key', session.public_key || '');
  if (session.encrypted_private_key) localStorage.setItem('freecollab_encrypted_private_key', session.encrypted_private_key);
  else localStorage.removeItem('freecollab_encrypted_private_key');
  window.dispatchEvent(new StorageEvent('storage', {key:'freecollab_token', newValue:session.access_token}));
}"""
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or None,
                                headless=True, args=['--no-sandbox'])
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        page.goto(BASE, wait_until='domcontentloaded')
        page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
        page.get_by_label('Email', exact=True).fill(alice['email'])
        page.get_by_label('Password', exact=True).fill(PASSWORD)
        page.get_by_role('button', name='Sign In', exact=True).click()
        expect(page.get_by_role('button', name='Account settings', exact=True)).to_be_visible()
        held_security = []

        def delay_security(route):
            if not held_security:
                held_security.append(route)
            else:
                route.continue_()

        page.route('**/api/account/security', delay_security)
        page.get_by_role('button', name='Account settings', exact=True).click()
        expect(page.get_by_text('Loading your account…', exact=True)).to_be_visible()
        assert held_security
        page.evaluate(SWITCH_ACCOUNT, {'session': bob, 'workspace': bob_workspace['id']})
        held_security[0].fulfill(status=200, json=alice_security)
        expect(page.get_by_role('dialog', name='Account settings', exact=True)).to_have_count(0)
        expect(page.get_by_role('button', name=re.compile('^Switch workspace, Bob private'))).to_be_visible()
        page.get_by_role('button', name='Account settings', exact=True).click()
        account = page.get_by_role('dialog', name='Account settings', exact=True)
        expect(account.get_by_text(bob['email'], exact=True)).to_be_visible()
        expect(account.get_by_text(alice['email'], exact=True)).to_have_count(0)
        account.get_by_role('button', name='Close account settings', exact=True).click()
        page.unroute('**/api/account/security')

        # A pending list from one account cannot reselect that workspace in the next account.
        page.evaluate(SWITCH_ACCOUNT, {'session': alice})
        expect(page.get_by_role('button', name='Account settings', exact=True)).to_be_visible()
        held_workspaces = []

        def delay_workspace(route):
            if route.request.method != 'GET':
                route.continue_()
                return
            if not held_workspaces:
                held_workspaces.append(route)
            else:
                route.continue_()

        # Wait for Alice's first load before intercepting the create-triggered refresh.
        expect(page.get_by_role('button', name=re.compile('^Switch workspace, '))).to_be_visible()
        page.route('**/api/workspaces', delay_workspace)
        page.get_by_role('button', name=re.compile('^Switch workspace')).click()
        page.get_by_role('menuitem', name='New workspace', exact=True).click()
        manager = page.get_by_role('dialog', name='Workspace settings', exact=True)
        manager.get_by_label('Workspace name', exact=True).fill('Alice pending ' + RUN)
        manager.get_by_role('button', name='Create workspace', exact=True).click()
        expect(manager.get_by_role('button', name='Create workspace', exact=True)).to_be_disabled()
        page.wait_for_timeout(100)
        assert held_workspaces
        alice_workspaces = api('GET', '/workspaces', alice)
        page.evaluate(SWITCH_ACCOUNT, {'session': bob})
        held_workspaces[0].fulfill(status=200, json=alice_workspaces)
        expect(page.get_by_role('dialog', name='Workspace settings', exact=True)).to_have_count(0)
        expect(page.get_by_role('button', name=re.compile('^Switch workspace, Bob private'))).to_be_visible()
        page.get_by_role('button', name=re.compile('^Switch workspace')).click()
        expect(page.get_by_role('menuitemradio', name=re.compile('Alice pending'))).to_have_count(0)
        assert page.evaluate('localStorage.getItem("freecollab_user_id")') == bob['user_id']

        login_context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
        login_page = login_context.new_page()
        login_page.set_default_timeout(30000)
        try:
            pending_login = []
            login_page.goto(BASE)
            login_page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
            login_page.route('**/api/auth/login', lambda route: pending_login.append(route))
            login_page.get_by_label('Email', exact=True).fill(alice['email'])
            login_page.get_by_label('Password', exact=True).fill(PASSWORD)
            login_page.get_by_role('button', name='Sign In', exact=True).click()
            expect(login_page.get_by_role('button', name='Sign In', exact=True)).to_be_disabled()
            assert pending_login
            login_page.evaluate(SWITCH_ACCOUNT, {'session': bob, 'workspace': bob_workspace['id']})
            pending_login[0].fulfill(status=200, json=alice)
            expect(login_page.get_by_role('button', name=re.compile('^Switch workspace, Bob private'))).to_be_visible()
            assert login_page.evaluate('localStorage.getItem("freecollab_user_id")') == bob['user_id']
        finally:
            login_context.close()
        print('Account race browser passed: late login, account and workspace responses cannot cross account boundaries or reopen old dialogs.')
    finally:
        context.close()
        browser.close()
