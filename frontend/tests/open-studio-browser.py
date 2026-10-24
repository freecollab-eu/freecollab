"""Real App room, navigation and material checks against disposable local services.

Set CHAT_BASE_URL to a fresh isolated API/Vite fixture. Creates unique accounts,
messages and files; never run against a customer database. SCREENSHOT_DIR is an
optional private output directory. No demo route, mock Chat or authority bypass.
"""
import base64
import json
import os
from pathlib import Path
import uuid
from urllib.request import Request, urlopen
from playwright.sync_api import expect, sync_playwright
from ui_navigation import show_channel_introduction

BASE = os.environ['CHAT_BASE_URL'].rstrip('/')
RUN = uuid.uuid4().hex[:10]
PASSWORD = 'Studio-disposable-browser-proof!'


def api(method, path, user=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if user:
        headers['Authorization'] = 'Bearer ' + user['access_token']
    with urlopen(Request(BASE + '/api' + path, headers=headers, method=method,
                         data=json.dumps(data).encode() if data is not None else None), timeout=45) as response:
        return json.load(response)


user = api('POST', '/auth/register', data={'name': 'Studio room tester', 'email': f'studio-{RUN}@example.com', 'password': PASSWORD})
workspace = api('GET', '/workspaces', user)[0]
room = api('POST', '/channels', user, {'name': 'Room-one-' + RUN, 'workspace_id': workspace['id']})
other = api('POST', '/channels', user, {'name': 'Room-two-' + RUN, 'workspace_id': workspace['id']})
for text in ('Could we open the room to the courtyard?', 'There is room for another perspective.', 'The shared study is here for us to consider.'):
    api('POST', '/messages/channels/' + room['id'], user, {'content': text})
api('POST', '/messages/channels/' + other['id'], user, {'content': 'A separate private conversation.'})

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or None, headless=True, args=['--no-sandbox'])
    try:
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a0e8AAAAASUVORK5CYII=')
        uploaded = context.request.post(BASE + '/api/files/channels/' + room['id'], headers={'Authorization': 'Bearer ' + user['access_token']}, multipart={'file': {'name': 'private-room-study.png', 'mimeType': 'image/png', 'buffer': png}})
        assert uploaded.ok, 'Disposable material upload failed'
        context.add_init_script(f"localStorage.setItem('freecollab_token', {json.dumps(user['access_token'])}); localStorage.setItem('freecollab_user_id', {json.dumps(user['user_id'])});")
        page = context.new_page(); page.set_default_timeout(45000)
        errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(BASE, wait_until='domcontentloaded', timeout=90000)
        expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)

        def choose(channel):
            page.get_by_role('button', name='Switch channel', exact=True).click()
            page.get_by_role('navigation', name='Channels', exact=True).get_by_role('button').filter(has=page.get_by_text(channel['name'], exact=True)).click()
            expect(page.get_by_role('textbox', name='Message #' + channel['name'], exact=True)).to_be_visible()
            if channel['id'] == room['id']:
                show_channel_introduction(page)

        choose(room)
        material = page.get_by_role('region', name='Shared in this channel', exact=True)
        expect(material).to_be_visible()
        expect(material.locator('img')).to_have_attribute('src', __import__('re').compile('^blob:'))
        expect(page.locator('.fc-chat-message')).to_have_count(3)
        expect(page.locator('.fc-chat-message.is-lead')).to_have_count(0)
        scales = page.locator('.fc-chat-message-content').evaluate_all('nodes=>nodes.map(node=>getComputedStyle(node).fontSize)')
        assert len(scales) == 3 and len(set(scales)) == 1, 'Ordinary messages do not share the actual selected reading scale'
        message_ids = page.locator('.fc-chat-message').evaluate_all('nodes => nodes.map(node => node.id)')
        print('PASS: real channel messages share the selected reading scale; authenticated material is available through the optional introduction', flush=True)

        def more():
            page.get_by_role('button', name='More channel tools', exact=True).click()

        def close_more():
            page.get_by_role('button', name='More channel tools', exact=True).focus()
            page.keyboard.press('Escape')

        more()
        page.get_by_role('button', name='Shared material on the left', exact=True).click()
        expect(page.get_by_role('button', name='Shared material on the left', exact=True)).to_have_attribute('aria-pressed', 'true')
        close_more()
        assert material.bounding_box()['x'] < page.locator('.fc-chat-message-list').bounding_box()['x'], 'Material did not move left'
        choose(other); expect(material).to_have_count(0)
        choose(room); expect(material).to_be_visible()
        assert material.bounding_box()['x'] < page.locator('.fc-chat-message-list').bounding_box()['x'], 'Channel arrangement was lost during the session'
        more(); page.get_by_role('button', name='Reset room arrangement', exact=True).click(); close_more()
        assert material.bounding_box()['x'] > page.locator('.fc-chat-message-list').bounding_box()['x'], 'Reset did not restore the room'
        print('PASS: keyboard/touch buttons arrange genuine material per channel, and reset restores it', flush=True)

        for width, height in ((1440, 1000), (390, 844), (320, 568)):
            page.set_viewport_size({'width': width, 'height': height})
            toggle = page.get_by_role('button', name='Switch channel', exact=True)
            toggle.focus(); toggle.press('Enter')
            drawer = page.get_by_role('dialog', name='Workspace channels', exact=True)
            expect(drawer).to_be_visible()
            assert page.locator('.fc-chat-main').evaluate('node => !!node.closest("[inert]")'), 'Drawer background is interactive'
            expect(drawer.get_by_role('button', name='Close channels', exact=True)).to_be_focused()
            for key in ['Shift+Tab', *(['Tab'] * 18)]:
                page.keyboard.press(key)
                assert drawer.evaluate('node => node.contains(document.activeElement)'), 'Focus escaped the channels drawer'
            page.keyboard.press('Escape')
            expect(drawer).to_have_count(0); expect(toggle).to_be_focused()
            expect(toggle).to_have_attribute('aria-expanded', 'false')
            tools = page.get_by_role('button', name='More channel tools', exact=True)
            tools.focus(); tools.press('ArrowDown')
            expect(page.get_by_role('button', name='Mobile and offline settings', exact=True)).to_be_focused()
            expect(page.get_by_role('button', name='Channel files', exact=True)).to_be_visible()
            page.keyboard.press('Escape'); expect(tools).to_be_focused()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Room overflowed at {width}px'
            if width < 768:
                message = page.locator('.fc-chat-message').last
                actions = message.get_by_role('button', name='More message actions', exact=True)
                actions.click()
                expect(message.get_by_role('button', name='Reply in thread', exact=True)).to_be_visible()
                actions.click()
                assert material.bounding_box()['y'] >= page.locator('.fc-chat-message-list').bounding_box()['y'] + page.locator('.fc-chat-message-list').bounding_box()['height'], 'Mobile material escaped the single column'
            folder = os.getenv('SCREENSHOT_DIR')
            if folder:
                Path(folder).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(folder) / f'real-chat-{width}.png'), full_page=True)
        print('PASS:1440/390/320px real drawer isolation, focus trap, Escape restoration, overflow reachability and one-column mobile fit', flush=True)

        page.set_viewport_size({'width': 1440, 'height': 1000})
        def toggle_focus(enabled):
            page.get_by_role('button', name='Message options', exact=True).click()
            option = page.get_by_role('menuitemcheckbox', name='Focus mode', exact=True)
            expect(option).to_have_attribute('aria-checked', 'false' if enabled else 'true')
            option.click()
            page.get_by_role('button', name='Message options', exact=True).click()
            expect(option).to_have_attribute('aria-checked', 'true' if enabled else 'false')
            option.focus(); option.press('Escape')

        toggle_focus(True)
        expect(material).to_have_count(0)
        assert page.locator('.fc-chat-message').evaluate_all('nodes => nodes.map(node => node.id)') == message_ids, 'Focus filtered or reordered messages'
        assert page.locator('.fc-chat-timeline-inner').bounding_box()['width'] < 1000, 'Focus did not change reading width'
        print('PASS: honest Focus changes reading layout and shared material while retaining every message', flush=True)

        held = []
        page.route('**/api/files/channels/' + room['id'], lambda route: held.append(route))
        toggle_focus(False)
        # Inline work keeps the authorized material reader mounted during
        # focus mode. Re-enter its real channel to hold a fresh scoped read.
        choose(other)
        with page.expect_request(lambda request: request.method == 'GET' and request.url.split('?')[0] == BASE + '/api/files/channels/' + room['id']):
            choose(room)
        expect(material).to_have_count(0)
        choose(other)
        assert held, 'Expected delayed shared-material request was not held'
        with page.expect_response(lambda response: '/files/channels/' + room['id'] in response.url):
            for route in held:
                route.continue_()
        held.clear()
        page.unroute('**/api/files/channels/' + room['id'])
        page.wait_for_load_state('networkidle')
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        expect(page.get_by_text('private-room-study.png', exact=True)).to_have_count(0)
        expect(page.get_by_role('textbox', name='Message #' + other['name'], exact=True)).to_be_visible()
        expect(page.get_by_text('Could we open the room to the courtyard?', exact=True)).to_have_count(0)
        print('PASS: a delayed file response after channel change cannot display prior private material', flush=True)
        assert not errors, 'Unexpected browser exceptions: ' + '; '.join(errors)
    finally:
        browser.close()
