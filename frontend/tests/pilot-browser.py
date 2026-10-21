"""Exercise the pilot entirely through its UI against disposable API/Vite services.

From repo root: CHROMIUM_PATH=/usr/bin/chromium python frontend/tests/pilot-browser.py
CHAT_BASE_URL defaults to http://127.0.0.1:3000. With no CHROMIUM_PATH, use the
browser installed by `playwright install chromium`. Creates unique accounts and
channels; never target a customer database. Requires Python Playwright.
"""
import json
import os
import re
from pathlib import Path
import tempfile
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright

BASE = os.getenv('CHAT_BASE_URL', 'http://127.0.0.1:3000').rstrip('/')
RUN = uuid.uuid4().hex[:12]
PASSWORD = 'Pilot-browser-password!'


def api(method, path, token=None, data=None, expected=200):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = Request(BASE + '/api' + path, headers=headers, method=method,
                      data=json.dumps(data).encode() if data is not None else None)
    try:
        with urlopen(request, timeout=45) as response:
            assert response.status == expected
            return json.load(response)
    except HTTPError as error:
        assert error.code == expected, f'{method} {path}: expected {expected}, got {error.code}'
        return json.load(error)


def register_ui(page, name, email, invite=None):
    page.goto(invite or BASE, wait_until='domcontentloaded', timeout=90000)
    if not invite:
        page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
    page.get_by_role('button', name="Don't have an account?").click()
    page.get_by_label('Name', exact=True).fill(name)
    page.get_by_label('Email', exact=True).fill(email)
    page.get_by_label('Password', exact=True).fill(PASSWORD)
    page.get_by_role('button', name='Create Account', exact=True).click()


def credentials(page):
    return page.evaluate("({token:localStorage.getItem('freecollab_token'),id:localStorage.getItem('freecollab_user_id')})")


def create_channel(page, name):
    page.get_by_role('button', name='Create channel', exact=True).click()
    page.get_by_label('Channel name', exact=True).fill(name)
    encrypted = page.get_by_role('checkbox', name=re.compile('^End-to-end encrypted'))
    if encrypted.count():
        encrypted.uncheck()
    page.get_by_role('button', name='Create', exact=True).click()
    expect(page.get_by_placeholder('Message #' + name)).to_be_visible()


def write(page, channel, content):
    composer = page.get_by_placeholder('Message #' + channel)
    composer.fill(content)
    composer.press('Enter')
    expect(composer).to_have_value('')


def choose_channel(page, name):
    page.get_by_role('button').filter(has=page.get_by_text(name, exact=True)).click()
    expect(page.get_by_placeholder('Message #' + name)).to_be_visible()


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or None,
                                headless=True, args=['--no-sandbox'], timeout=90000)
    try:
        alice_context, bob_context = browser.new_context(), browser.new_context()
        alice, bob = alice_context.new_page(), bob_context.new_page()
        errors = []
        for page in (alice, bob):
            page.set_default_timeout(45000)
            page.on('pageerror', lambda error: errors.append(str(error)))
        register_ui(alice, 'Pilot Alice', f'alice-{RUN}@example.com')
        expect(alice.get_by_text('No channels yet', exact=True)).to_be_visible(timeout=90000)
        shared = 'Pilot-shared-' + RUN
        create_channel(alice, shared)
        alice.get_by_role('button', name='Channel members', exact=True).click()
        alice.get_by_role('button', name='Create invitation link', exact=True).click()
        link = alice.get_by_label('Invitation link', exact=True)
        expect(link).to_have_value(re.compile(r'.*#invite=.+'))
        invitation = link.input_value()
        expect(alice.get_by_role('button', name='Make member', exact=True)).to_be_disabled()
        alice.get_by_role('button', name='Close channel panel', exact=True).click()

        register_ui(bob, 'Pilot Bob', f'bob-{RUN}@example.com', invitation)
        expect(bob.get_by_role('button', name='Join channel', exact=True)).to_be_visible(timeout=90000)
        bob.reload(wait_until='domcontentloaded')
        bob.get_by_role('button', name='Join channel', exact=True).click()
        expect(bob.get_by_placeholder('Message #' + shared)).to_be_visible(timeout=90000)
        assert '#invite=' not in bob.url
        print('PASS: UI registration, channel creation, invitation across signup/reload, explicit join and last-admin guard', flush=True)

        alice_text, bob_text = 'Alice message ' + RUN, 'Bob message ' + RUN
        write(alice, shared, alice_text)
        expect(bob.get_by_text(alice_text, exact=True)).to_be_visible()
        write(bob, shared, bob_text)
        expect(alice.get_by_text(bob_text, exact=True)).to_be_visible()
        expect(bob.get_by_text(bob_text, exact=True)).to_have_count(1)
        assert alice.get_by_label('Channel messages', exact=True).evaluate("""element => [...element.querySelectorAll('*')].flatMap(node => [...node.childNodes]).every(node => node.nodeType !== Node.TEXT_NODE || node.textContent.trim() !== '0')"""), 'A zero-count thread rendered a stray zero'
        sidebar = alice.locator('.fc-chat-sidebar')
        assert sidebar.evaluate("element => getComputedStyle(element).borderRadius") == '0px', 'Sidebar inherited the loading-spinner circle'
        bob.reload(wait_until='domcontentloaded')
        expect(bob.get_by_text(alice_text, exact=True)).to_be_visible()
        print('PASS: two-user realtime, deduplication and persisted history', flush=True)

        auth = credentials(alice)
        channels = api('GET', '/channels', auth['token'])
        channel_id = next(channel['id'] for channel in channels if channel['name'] == shared)
        pattern = '**/api/mobile/offline/channels/' + channel_id + '/messages'
        def fail_send(route):
            # Network failures deliberately enter the encrypted outbox. An
            # explicit server rejection must instead preserve the composer.
            if route.request.method == 'POST':
                route.fulfill(status=503, content_type='application/json',
                              body=json.dumps({'detail': 'Temporary send rejection'}))
            else:
                route.continue_()
        alice.route(pattern, fail_send)
        draft = 'Retained failed draft ' + RUN
        alice.get_by_placeholder('Message #' + shared).fill(draft)
        alice.get_by_role('button', name='Send message', exact=True).click()
        expect(alice.get_by_role('alert')).to_contain_text('Message was not sent')
        expect(alice.get_by_placeholder('Message #' + shared)).to_have_value(draft)
        alice.unroute(pattern, fail_send)
        alice.get_by_role('button', name='Send message', exact=True).click()
        expect(bob.get_by_text(draft, exact=True)).to_be_visible()
        expect(bob.get_by_text(draft, exact=True)).to_have_count(1)
        print('PASS: failed send retains draft and retry delivers once', flush=True)

        filename = 'pilot-' + RUN + '.txt'
        content = b'Private pilot document bytes\n'
        file_pattern = '**/api/files/channels/' + channel_id
        held_file_lists = []

        def hold_file_list(route):
            if route.request.method != 'GET':
                route.continue_()
                return
            slot = len(held_file_lists)
            held_file_lists.append(None)
            response = route.fetch()
            held_file_lists[slot] = (route, response)

        def wait_for_held_file_lists():
            deadline = time.monotonic() + 15
            while not held_file_lists or any(record is None for record in held_file_lists):
                assert time.monotonic() < deadline, 'The actual file listing did not complete'
                alice.wait_for_timeout(20)

        def release_file_lists():
            for route, response in held_file_lists:
                route.fulfill(response=response)
            alice.unroute(file_pattern, hold_file_list)
            expect(alice.locator('.fc-channel-panel')).to_have_attribute('aria-busy', 'false')

        alice.route(file_pattern, hold_file_list)
        alice.get_by_role('button', name='Channel files', exact=True).click()
        wait_for_held_file_lists()
        assert all(response.json() == [] for _, response in held_file_lists)
        alice.get_by_label('Choose a file', exact=True).set_input_files({'name': filename, 'mimeType': 'text/plain', 'buffer': content})
        expect(alice.get_by_text(filename, exact=True)).to_be_visible()
        release_file_lists()
        expect(alice.get_by_text(filename, exact=True)).to_be_visible()
        bob.get_by_role('button', name='Channel files', exact=True).click()
        expect(bob.get_by_text(filename, exact=True)).to_be_visible()
        expect(bob.get_by_role('button', name='Delete ' + filename, exact=True)).to_have_count(0)
        with bob.expect_download() as download_info:
            bob.get_by_role('button', name='Download ' + filename, exact=True).click()
        download = download_info.value
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / filename
            download.save_as(str(target))
            assert target.read_bytes() == content
        uploaded = api('GET', '/files/channels/' + channel_id, auth['token'])
        file_id = next(file['id'] for file in uploaded if file['filename'] == filename)
        outsider = api('POST', '/auth/register', data={'name': 'Pilot outsider', 'email': f'outsider-{RUN}@example.com', 'password': PASSWORD})
        api('GET', '/files/' + file_id, outsider['access_token'], expected=403)
        held_file_lists.clear()
        alice.route(file_pattern, hold_file_list)
        alice.get_by_role('button', name='Refresh', exact=True).click()
        wait_for_held_file_lists()
        assert all(any(record['id'] == file_id for record in response.json()) for _, response in held_file_lists)
        alice.once('dialog', lambda dialog: dialog.accept())
        alice.get_by_role('button', name='Delete ' + filename, exact=True).click()
        expect(alice.get_by_text(filename, exact=True)).to_have_count(0)
        release_file_lists()
        expect(alice.get_by_text(filename, exact=True)).to_have_count(0)
        print('PASS: UI upload/download/delete, delayed-list mutation fences and outsider denial', flush=True)
        for page in (alice, bob):
            page.get_by_role('button', name='Close channel panel', exact=True).click()

        private = 'Pilot-private-' + RUN
        create_channel(alice, private)
        private_text = 'Private channel message ' + RUN
        write(alice, private, private_text)
        expect(bob.get_by_text(private_text, exact=True)).to_have_count(0)
        choose_channel(alice, shared)
        alice.get_by_role('button', name='Channel members', exact=True).click()
        bob_member = alice.locator('li').filter(has=alice.get_by_text('Pilot Bob', exact=True))
        alice.once('dialog', lambda dialog: dialog.accept())
        bob_member.get_by_role('button', name='Remove', exact=True).click()
        expect(bob.get_by_placeholder('Message #' + shared)).to_have_count(0)
        expect(bob.get_by_text('Select a channel', exact=True)).to_be_visible()
        expect(bob.get_by_text(alice_text, exact=True)).to_have_count(0)
        print('PASS: private-channel isolation and immediate membership revocation in UI', flush=True)

        alice.get_by_role('button', name='Close channel panel', exact=True).click()
        alice.set_viewport_size({'width': 390, 'height': 844})
        expect(alice.get_by_placeholder('Message #' + shared)).to_be_visible()
        assert alice.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Core chat overflowed a mobile viewport'
        alice.get_by_role('button', name='More channel tools', exact=True).click()
        alice.get_by_role('group', name='More channel tools', exact=True).get_by_role('button', name='Channel files', exact=True).click()
        expect(alice.get_by_label('Choose a file', exact=True)).to_be_visible()
        assert alice.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Files panel overflowed a mobile viewport'
        alice.get_by_role('button', name='Close channel panel', exact=True).click()
        print('PASS: mobile chat and files panel fit the viewport', flush=True)

        bob_auth = credentials(bob)
        bob.get_by_role('button', name='Sign out', exact=True).click()
        expect(bob.get_by_role('navigation').get_by_role('button', name='Open App', exact=True)).to_be_visible()
        assert bob.evaluate("localStorage.getItem('freecollab_token')") is None
        api('GET', '/auth/me', bob_auth['token'], expected=401)
        assert not errors, 'Unexpected browser errors: ' + '; '.join(errors)
        print('PASS: server-revoked logout, cleared local credentials, no browser exceptions', flush=True)
    finally:
        browser.close()
