"""Run against Vite/backend: python frontend/tests/chat-browser.py.

Requires Python Playwright and system Chromium; CHAT_BASE_URL and CHROMIUM_PATH
can override local defaults. Creates unique local accounts/channels, never uses
production credentials. Verifies the actual React UI in two browser contexts.
"""
from ui_navigation import close_channels, open_channels
import json
import os
import re
import uuid
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright

BASE = os.getenv('CHAT_BASE_URL', 'http://127.0.0.1:3000').rstrip('/')
PASSWORD = 'Local-browser-test-password!'
RUN = uuid.uuid4().hex[:12]


def api(method, path, token=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = Request(BASE + '/api' + path, headers=headers, method=method,
                      data=json.dumps(data).encode() if data is not None else None)
    with urlopen(request, timeout=45) as response:
        return json.load(response)


def register(name):
    email = f'{name.lower()}-{RUN}@example.com'
    result = api('POST', '/auth/register', data={'name': name, 'email': email, 'password': PASSWORD})
    return {**result, 'email': email}


def send(channel, user, text):
    return api('POST', '/messages/channels/' + channel['id'], user['access_token'], {'content': text})


def login(page, user):
    page.goto(BASE, wait_until='domcontentloaded', timeout=90000)
    page.get_by_role('button', name=re.compile(r'Open App|Launch Demo', re.I)).first.click()
    page.locator('input[type=email]').fill(user['email'])
    page.locator('input[type=password]').fill(PASSWORD)
    page.get_by_role('button', name='Sign In', exact=True).click()
    expect(page.get_by_role('textbox', name='Message #' + shared['name'], exact=True)).to_be_visible(timeout=90000)
    expect(page.get_by_text('Shared history ' + RUN, exact=True)).to_be_visible(timeout=90000)


def write(page, channel, text):
    composer = page.get_by_role('textbox', name='Message #' + channel['name'], exact=True)
    composer.fill(text)
    composer.press('Enter')
    expect(composer).to_have_value('')


def choose_channel(page, channel):
    page.get_by_role('button', name='Toggle channels', exact=True).click()
    page.get_by_role('navigation', name='Channels', exact=True).get_by_role('button').filter(has=page.get_by_text(channel['name'], exact=True)).click()


alice, bob = register('BrowserAlice'), register('BrowserBob')
shared = api('POST', '/channels', alice['access_token'], {
    'name': 'Shared-' + RUN, 'type': 'group', 'member_ids': [bob['user_id']],
})
private = api('POST', '/channels', alice['access_token'], {
    'name': 'Private-' + RUN, 'type': 'group', 'member_ids': [],
})
send(shared, alice, 'Shared history ' + RUN)
send(private, alice, 'Private history ' + RUN)

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or None,
                                headless=True, args=['--no-sandbox'], timeout=90000)
    alice_context, bob_context = browser.new_context(), browser.new_context()
    alice_page, bob_page = alice_context.new_page(), bob_context.new_page()
    errors = []
    for page in (alice_page, bob_page):
        page.set_default_timeout(30000)
        page.on('pageerror', lambda error: errors.append(str(error)))
    login(alice_page, alice)
    login(bob_page, bob)
    print('PASS: two independent UI logins and shared history', flush=True)

    alice_text, bob_text = 'Alice realtime ' + RUN, 'Bob realtime ' + RUN
    write(alice_page, shared, alice_text)
    expect(bob_page.get_by_text(alice_text, exact=True)).to_be_visible()
    expect(alice_page.get_by_text(alice_text, exact=True)).to_have_count(1)
    write(bob_page, shared, bob_text)
    expect(alice_page.get_by_text(bob_text, exact=True)).to_be_visible()
    expect(bob_page.get_by_text(bob_text, exact=True)).to_have_count(1)
    print('PASS: bidirectional realtime receive and send/event deduplication', flush=True)

    bob_page.reload(wait_until='domcontentloaded')
    expect(bob_page.get_by_text(alice_text, exact=True)).to_be_visible()
    expect(bob_page.get_by_text(bob_text, exact=True)).to_have_count(1)
    print('PASS: persisted history after reload', flush=True)

    choose_channel(alice_page, private)
    expect(alice_page.get_by_text('Private history ' + RUN, exact=True)).to_be_visible()
    expect(alice_page.get_by_text(alice_text, exact=True)).to_have_count(0)
    bob_channels = open_channels(bob_page)
    expect(bob_channels.get_by_role('button').filter(has=bob_page.get_by_text(private['name'], exact=True))).to_have_count(0)
    close_channels(bob_page)
    private_text = 'Alice private message ' + RUN
    write(alice_page, private, private_text)
    expect(alice_page.get_by_text(private_text, exact=True)).to_have_count(1)
    expect(bob_page.get_by_text(private_text, exact=True)).to_have_count(0)
    while_away = 'Shared while Alice away ' + RUN
    write(bob_page, shared, while_away)
    expect(bob_page.get_by_text(while_away, exact=True)).to_be_visible()
    expect(alice_page.get_by_text(while_away, exact=True)).to_have_count(0)
    choose_channel(alice_page, shared)
    expect(alice_page.get_by_text(while_away, exact=True)).to_be_visible()
    expect(alice_page.get_by_text(private_text, exact=True)).to_have_count(0)
    final_text = 'Realtime after channel switch ' + RUN
    write(bob_page, shared, final_text)
    expect(alice_page.get_by_text(final_text, exact=True)).to_be_visible()
    assert not errors, 'Unexpected browser errors: ' + '; '.join(errors)
    print('PASS: private-channel isolation, channel switching and resubscription', flush=True)
    browser.close()
