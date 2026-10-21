"""Exercise keyboard, composition and reading flows against disposable services.

From the repository root:
CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/elite-browser.py
CHAT_BASE_URL defaults to http://127.0.0.1:3000. Uses unique fixture accounts,
channels and messages; never target a customer database.
"""
import json
import os
import re
import uuid
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright


BASE = os.getenv('CHAT_BASE_URL', 'http://127.0.0.1:3000').rstrip('/')
PASSWORD = 'Elite-interaction-browser-test!'
RUN = uuid.uuid4().hex[:12]


def api(method, path, user=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if user:
        headers['Authorization'] = 'Bearer ' + user['access_token']
    request = Request(BASE + '/api' + path, method=method, headers=headers,
                      data=json.dumps(data).encode() if data is not None else None)
    with urlopen(request, timeout=45) as response:
        return json.load(response)


def register(name):
    email = f'{name.lower()}-{RUN}@example.com'
    user = api('POST', '/auth/register', data={'name': name, 'email': email, 'password': PASSWORD})
    return {**user, 'email': email}


def login(page, user, channel):
    page.goto(BASE, wait_until='domcontentloaded', timeout=90000)
    page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
    page.get_by_label('Email', exact=True).fill(user['email'])
    page.get_by_label('Password', exact=True).fill(PASSWORD)
    page.get_by_role('button', name='Sign In', exact=True).click()
    expect(page.get_by_placeholder('Message #' + channel['name'])).to_be_visible(timeout=90000)
    page.evaluate('() => document.fonts.ready')


def composer(page, channel):
    return page.get_by_placeholder('Message #' + channel['name'])


def send(channel, user, content):
    return api('POST', '/messages/channels/' + channel['id'], user, {'content': content})


def choose_channel(page, channel):
    nav = page.get_by_role('navigation', name='Channels', exact=True)
    if not nav.is_visible():
        page.get_by_role('button', name='Toggle channels', exact=True).click()
    nav.get_by_role('button').filter(has=page.get_by_text(channel['name'], exact=True)).click()
    expect(composer(page, channel)).to_be_visible()


def assert_fit(page, description):
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), description


def next_paint(page):
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')


def assert_modal(page, dialog, background):
    expect(dialog).to_be_visible()
    assert dialog.evaluate('element => element.contains(document.activeElement)'), 'Initial focus is outside the modal'
    assert background.evaluate('element => !!element.closest("[inert]")'), 'Modal background remains interactive'
    for key in ['Shift+Tab', *(['Tab'] * 12)]:
        page.keyboard.press(key)
        assert dialog.evaluate('element => element.contains(document.activeElement)'), 'Keyboard focus escaped the modal'


def open_palette(page, shortcut='Control+k'):
    page.keyboard.press(shortcut)
    dialog = page.get_by_role('dialog', name='Quick switcher', exact=True)
    expect(dialog).to_be_visible()
    search = dialog.get_by_role('combobox', name='Search channels, messages or actions', exact=True)
    expect(search).to_be_focused()
    return dialog, search


def wait_bottom(page):
    page.wait_for_function("""() => {
      const element = document.querySelector('.fc-chat-timeline');
      return element && element.scrollHeight - element.scrollTop - element.clientHeight < 3;
    }""")


def wait_read(page, channel, user, message):
    page.wait_for_function("""async expected => {
      const response = await fetch('/api/read-receipts/channels/' + expected.channel, {
        headers: {Authorization: 'Bearer ' + localStorage.getItem('freecollab_token')},
      });
      return (await response.json()).some(receipt => receipt.user_id === expected.user && receipt.last_read_message_id === expected.message);
    }""", arg={'channel': channel['id'], 'user': user['user_id'], 'message': message['id']}, polling=100)


def last_read(channel, user):
    return next(record['last_read_message_id']
                for record in api('GET', '/read-receipts/channels/' + channel['id'], user)
                if record['user_id'] == user['user_id'])


alice, bob = register('EliteAlice'), register('EliteBob')
studio = api('POST', '/channels', alice, {
    'name': 'Studio-' + RUN, 'type': 'group', 'member_ids': [bob['user_id']],
})
notes = api('POST', '/channels', alice, {
    'name': 'Notes-' + RUN, 'type': 'group', 'member_ids': [],
})
old_needle = 'archived-needle-' + RUN
loaded_needle = 'diagram-needle-' + RUN
private_needle = 'notes-only-' + RUN
for index in range(60):
    content = old_needle if index == 0 else loaded_needle if index == 48 else (
        f'Conversation {index:02d} · {RUN}\n'
        'A clear thought with enough room to read.\n'
        'A second detail worth returning to.'
    )
    send(studio, alice if index % 2 else bob, content)
send(notes, alice, private_needle)


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or None,
                                headless=True, args=['--no-sandbox'], timeout=90000)
    try:
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        page.set_default_timeout(30000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        login(page, alice, studio)
        expect(page.get_by_text(loaded_needle, exact=True)).to_have_count(1)
        expect(page.get_by_text(old_needle, exact=True)).to_have_count(0)
        entry = composer(page, studio)
        expect(entry).to_have_js_property('tagName', 'TEXTAREA')
        first_line, second_line = 'A considered first line ' + RUN, 'And a second line ' + RUN
        entry.fill(first_line)
        entry.press('Shift+Enter')
        entry.press_sequentially(second_line)
        multiline = first_line + '\n' + second_line
        expect(entry).to_have_value(multiline)
        submitted = []
        page.on('request', lambda request: submitted.append(request)
                if request.method == 'POST' and request.url.endswith('/messages/channels/' + studio['id']) else None)
        entry.dispatch_event('keydown', {'key': 'Enter', 'code': 'Enter', 'isComposing': True, 'bubbles': True, 'cancelable': True})
        next_paint(page)
        expect(entry).to_have_value(multiline)
        assert not submitted, 'An IME composition Enter submitted the message'
        entry.press('Enter')
        expect(entry).to_have_value('')
        assert any(record['content'] == multiline for record in api('GET', '/messages/channels/' + studio['id'], alice))
        assert len(submitted) == 1, 'Enter sent a multiline message more than once'
        article = page.locator('article').filter(has=page.get_by_text(multiline, exact=True))
        article.hover()
        article.get_by_role('button', name='Edit', exact=True).click()
        edit = page.get_by_label('Edit message', exact=True)
        expect(edit).to_have_value(multiline)
        revised = multiline + '\nOne final detail.'
        edit.fill(revised)
        page.get_by_role('button', name='Save edit', exact=True).click()
        expect(edit).to_have_count(0)
        assert any(record['content'] == revised for record in api('GET', '/messages/channels/' + studio['id'], alice))
        print('PASS: Shift+Enter, IME-safe Enter, exact multiline persistence and edit round-trip', flush=True)

        failed = 'A draft that stays here ' + RUN + '\nIncluding its second line.'
        def fail_send(route):
            route.abort('failed') if route.request.method == 'POST' else route.continue_()
        route_pattern = '**/api/messages/channels/' + studio['id']
        page.route(route_pattern, fail_send)
        entry.fill(failed)
        page.get_by_role('button', name='Send message', exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('Message was not sent')
        expect(entry).to_have_value(failed)
        choose_channel(page, notes)
        expect(composer(page, notes)).to_have_value('')
        choose_channel(page, studio)
        expect(composer(page, studio)).to_have_value(failed)
        page.unroute(route_pattern, fail_send)
        page.get_by_role('button', name='Send message', exact=True).click()
        expect(composer(page, studio)).to_have_value('')
        assert sum(record['content'] == failed for record in api('GET', '/messages/channels/' + studio['id'], alice)) == 1
        print('PASS: failed multiline draft survives channel changes and retries exactly once', flush=True)

        timeline = page.get_by_label('Channel messages', exact=True)
        wait_bottom(page)
        latest_before = api('GET', '/messages/channels/' + studio['id'], alice)[0]
        page.wait_for_function("""async expected => {
          const response = await fetch('/api/read-receipts/channels/' + expected.channel, {
            headers: {Authorization: 'Bearer ' + localStorage.getItem('freecollab_token')},
          });
          const receipts = await response.json();
          return receipts.some(receipt => receipt.user_id === expected.user && receipt.last_read_message_id === expected.message);
        }""", arg={'channel': studio['id'], 'user': alice['user_id'], 'message': latest_before['id']}, polling=100)
        timeline.evaluate('element => { element.scrollTop = 450; }')
        next_paint(page)
        before_top = timeline.evaluate('element => element.scrollTop')
        assert before_top > 0
        incoming_text = 'A new thought while you are reading ' + RUN
        incoming = send(studio, bob, incoming_text)
        expect(page.get_by_text(incoming_text, exact=True)).to_have_count(1)
        jump = page.get_by_role('button', name='Jump to latest messages', exact=True)
        expect(jump).to_be_visible()
        assert abs(timeline.evaluate('element => element.scrollTop') - before_top) < 3, 'Incoming message moved the reading position'
        receipt = next(record for record in api('GET', '/read-receipts/channels/' + studio['id'], alice) if record['user_id'] == alice['user_id'])
        assert receipt['last_read_message_id'] == latest_before['id'], 'An off-screen incoming message was marked read'
        jump.click()
        wait_bottom(page)
        expect(jump).to_have_count(0)
        page.wait_for_function("""async expected => {
          const response = await fetch('/api/read-receipts/channels/' + expected.channel, {
            headers: {Authorization: 'Bearer ' + localStorage.getItem('freecollab_token')},
          });
          return (await response.json()).some(receipt => receipt.user_id === expected.user && receipt.last_read_message_id === expected.message);
        }""", arg={'channel': studio['id'], 'user': alice['user_id'], 'message': incoming['id']}, polling=100)
        timeline.evaluate('element => { element.scrollTop = 450; }')
        next_paint(page)
        own_text = 'An intentional reply takes me to the latest ' + RUN
        composer(page, studio).fill(own_text)
        composer(page, studio).press('Enter')
        expect(composer(page, studio)).to_have_value('')
        wait_bottom(page)
        own_message = api('GET', '/messages/channels/' + studio['id'], alice)[0]
        wait_read(page, studio, alice, own_message)
        page.get_by_role('button', name='Channel files', exact=True).click()
        files = page.get_by_role('dialog', name='Channel files', exact=True)
        expect(files).to_be_visible()
        obscured_text = 'This stays unread behind the Files panel ' + RUN
        obscured = send(studio, bob, obscured_text)
        expect(page.get_by_text(obscured_text, exact=True)).to_have_count(1)
        wait_bottom(page)
        assert last_read(studio, alice) == own_message['id'], 'A message obscured by a modal was marked read'
        page.keyboard.press('Escape')
        expect(files).to_have_count(0)
        wait_read(page, studio, alice, obscured)

        # Headless Chromium keeps its tabs visible. Emulate the document's
        # visibility state and dispatch visibilitychange while real Socket.IO
        # delivery, DOM updates and authenticated API requests continue. This
        # exercises app reconciliation, not an OS background/freeze lifecycle.
        page.evaluate("""() => {
          Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
          Object.defineProperty(document, 'hidden', {configurable: true, value: true});
          document.dispatchEvent(new Event('visibilitychange'));
        }""")
        try:
            assert page.evaluate('document.visibilityState') == 'hidden'
            hidden_text = 'This stays unread while the document is hidden ' + RUN
            hidden = send(studio, bob, hidden_text)
            expect(page.get_by_text(hidden_text, exact=True)).to_have_count(1)
            wait_bottom(page)
            assert last_read(studio, alice) == obscured['id'], 'A hidden document marked an incoming message read'
        finally:
            page.evaluate("""() => {
              delete document.visibilityState;
              delete document.hidden;
              document.dispatchEvent(new Event('visibilitychange'));
            }""")
        assert page.evaluate('document.visibilityState') == 'visible'
        wait_read(page, studio, alice, hidden)
        print('PASS: reading position stays still; off-screen, modal-obscured and hidden-document messages remain unread until seen', flush=True)

        entry = composer(page, studio)
        entry.fill('A draft stays put while I navigate ' + RUN)
        entry.focus()
        entry.dispatch_event('keydown', {'key': 'k', 'code': 'KeyK', 'ctrlKey': True, 'isComposing': True, 'bubbles': True, 'cancelable': True})
        next_paint(page)
        expect(page.get_by_role('dialog', name='Quick switcher', exact=True)).to_have_count(0)
        palette, search = open_palette(page)
        assert_modal(page, palette, entry)
        expect(search).to_have_attribute('aria-expanded', 'true')
        search.focus()
        search.fill(notes['name'])
        expect(palette.get_by_role('option')).to_have_count(1)
        search.press('ArrowDown')
        selected_id = search.get_attribute('aria-activedescendant')
        assert selected_id and page.locator('[id="' + selected_id + '"]').get_attribute('aria-selected') == 'true'
        search.press('Enter')
        expect(palette).to_have_count(0)
        expect(composer(page, notes)).to_be_focused()
        palette, search = open_palette(page, 'Meta+k')
        search.fill(private_needle)
        expect(palette.get_by_role('group', name='In this channel · loaded messages', exact=True).get_by_role('option')).to_have_count(1)
        search.press('Enter')
        private_article = page.locator('article').filter(has=page.get_by_text(private_needle, exact=True))
        expect(private_article).to_be_focused()
        palette, search = open_palette(page)
        search.fill(studio['name'])
        search.press('Enter')
        expect(composer(page, studio)).to_be_focused()
        expect(composer(page, studio)).to_have_value('A draft stays put while I navigate ' + RUN)
        palette, search = open_palette(page)
        expect(palette).to_contain_text('messages loaded in this channel')
        for unavailable in (old_needle, private_needle):
            search.fill(unavailable)
            expect(palette.get_by_role('option')).to_have_count(0)
            expect(palette.get_by_text('No matches found.', exact=True)).to_be_visible()
        search.fill(loaded_needle)
        expect(palette.get_by_role('group', name='In this channel · loaded messages', exact=True).get_by_role('option')).to_have_count(1)
        search.press('Enter')
        loaded_article = page.locator('article').filter(has=page.get_by_text(loaded_needle, exact=True))
        expect(loaded_article).to_be_focused()
        expect(loaded_article).to_have_class(re.compile('is-highlighted'))
        palette, search = open_palette(page)
        page.keyboard.press('Escape')
        expect(palette).to_have_count(0)
        expect(loaded_article).to_be_focused()
        print('PASS: Ctrl/Cmd+K, keyboard channel selection, retained drafts and honest loaded-current-channel message search', flush=True)

        entry = composer(page, studio)
        entry.focus()
        palette, search = open_palette(page)
        search.fill('Open files')
        search.press('Enter')
        files = page.get_by_role('dialog', name='Channel files', exact=True)
        assert_modal(page, files, entry)
        page.keyboard.press('Control+k')
        expect(files).to_be_visible()
        expect(page.get_by_role('dialog')).to_have_count(1)
        page.keyboard.press('Escape')
        expect(files).to_have_count(0)
        expect(entry).to_be_focused()
        palette, search = open_palette(page)
        search.fill('Create a channel')
        search.press('Enter')
        create = page.get_by_role('dialog', name='Create channel', exact=True)
        expect(create.get_by_label('Channel name', exact=True)).to_be_focused()
        assert_modal(page, create, entry)
        page.keyboard.press('Escape')
        expect(create).to_have_count(0)
        expect(entry).to_be_focused()
        palette, search = open_palette(page)
        search.fill('Appearance settings')
        search.press('Enter')
        appearance = page.get_by_role('dialog', name='Make space your own.', exact=True)
        assert_modal(page, appearance, entry)
        page.keyboard.press('Control+k')
        expect(page.get_by_role('dialog')).to_have_count(1)
        expect(appearance).to_be_visible()
        page.keyboard.press('Escape')
        expect(appearance).to_have_count(0)
        expect(entry).to_be_focused()
        for trigger_name, dialog_name, close_name in (
            ('Channel members', 'Channel members', 'Close channel panel'),
            ('Channel files', 'Channel files', 'Close channel panel'),
            ('Notifications', 'Notifications', 'Close notifications'),
        ):
            trigger = page.get_by_role('button', name=trigger_name, exact=True)
            trigger.click()
            panel = page.get_by_role('dialog', name=dialog_name, exact=True)
            expect(panel.get_by_role('button', name=close_name, exact=True)).to_be_focused()
            assert_modal(page, panel, entry)
            page.keyboard.press('Escape')
            expect(panel).to_have_count(0)
            expect(trigger).to_be_focused()
        loaded_article.hover()
        reply_trigger = loaded_article.get_by_role('button', name='Reply in thread', exact=True)
        reply_trigger.click()
        thread = page.get_by_role('dialog', name='Thread', exact=True)
        assert_modal(page, thread, entry)
        page.keyboard.press('Escape')
        expect(thread).to_have_count(0)
        expect(reply_trigger).to_be_focused()
        assert not entry.evaluate('element => !!element.closest("[inert]")'), 'Closing dialogs left the workspace inert'
        print('PASS: palette actions, modal isolation, trapped focus, Escape and original-trigger restoration', flush=True)

        preferences = api('GET', '/preferences/appearance', alice)
        api('PUT', '/preferences/appearance', alice, {
            **preferences, 'mode': 'dark', 'accent_color': '#eddccf',
            'dark_background_color': '#121a19', 'font_family': 'manrope',
            'font_size': 'large', 'density': 'compact', 'corner_style': 'rounded',
        })
        page.reload(wait_until='domcontentloaded')
        expect(page.locator('html')).to_have_attribute('data-mode', 'dark')
        expect(page.locator('html')).to_have_attribute('data-font', 'manrope')
        expect(composer(page, studio)).to_be_visible()
        for width, height in ((390, 844), (320, 568)):
            page.set_viewport_size({'width': width, 'height': height})
            expect(page.get_by_role('button', name='Toggle channels', exact=True)).to_have_attribute('aria-expanded', 'false')
            assert_fit(page, f'Chat overflowed at {width}px')
            page.get_by_role('button', name='Toggle channels', exact=True).click()
            page.get_by_role('button', name='Quick switcher', exact=True).click()
            palette = page.get_by_role('dialog', name='Quick switcher', exact=True)
            search = palette.get_by_role('combobox', name='Search channels, messages or actions', exact=True)
            assert_modal(page, palette, composer(page, studio))
            assert_fit(page, f'Quick switcher overflowed at {width}px')
            search.fill('Appearance settings')
            search.press('Enter')
            appearance = page.get_by_role('dialog', name='Make space your own.', exact=True)
            assert_modal(page, appearance, composer(page, studio))
            assert_fit(page, f'Appearance overflowed at {width}px')
            type_navigation = appearance.get_by_role('button', name='Typography', exact=True)
            type_navigation.click()
            expect(page.locator('[id="' + type_navigation.get_attribute('aria-controls') + '"]')).to_be_focused()
            font = appearance.get_by_role('radio', name=re.compile('Manrope'))
            expect(font).to_be_checked()
            font.locator('..').click()
            assert_fit(page, f'Typography controls overflowed at {width}px')
            page.keyboard.press('Escape')
            for trigger_name, dialog_name in (('Channel files', 'Channel files'), ('Channel members', 'Channel members'), ('Notifications', 'Notifications')):
                page.get_by_role('button', name=trigger_name, exact=True).click()
                panel = page.get_by_role('dialog', name=dialog_name, exact=True)
                assert_modal(page, panel, composer(page, studio))
                assert_fit(page, f'{dialog_name} overflowed at {width}px')
                page.keyboard.press('Escape')
        assert not errors, 'Unexpected browser exceptions: ' + '; '.join(errors)
        print('PASS:320/390px quick switcher, appearance sections and dialogs fit a custom dark large-type theme', flush=True)
    finally:
        browser.close()
