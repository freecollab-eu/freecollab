"""Exercise saved items, decisions, watched threads and settings on disposable local services."""
from ui_navigation import open_chat_tool, reveal_message_actions
import json
import os
import re
import uuid
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright

BASE = os.getenv('CHAT_BASE_URL', 'http://127.0.0.1:3000').rstrip('/')
RUN = uuid.uuid4().hex[:12]
PASSWORD = 'Collaboration-browser!2026'


def api(method, path, user=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if user:
        headers['Authorization'] = 'Bearer ' + user['access_token']
    request = Request(BASE + '/api' + path, method=method, headers=headers,
                      data=json.dumps(data).encode() if data is not None else None)
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def register(name):
    email = name.lower() + '-' + RUN + '@example.com'
    return {**api('POST', '/auth/register', data={'name': name, 'email': email, 'password': PASSWORD}), 'email': email}


def select_notebook_section(panel, name):
    options = panel.locator('details.fc-collaboration-flow-options')
    if options.get_attribute('open') is None:
        options.locator('summary').click()
    section = options.get_by_role('button', name=name, exact=True)
    section.click()
    expect(section).to_have_attribute('aria-current', 'page')


owner, member = register('NotebookOwner'), register('NotebookMember')
channel = api('POST', '/channels', owner, {'name': 'Notebook-' + RUN, 'member_ids': [member['user_id']]})
message = api('POST', '/messages/channels/' + channel['id'], owner, {'content': 'A useful reference ' + RUN})

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or None, headless=True, args=['--no-sandbox'])
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
    page = context.new_page()
    page.set_default_timeout(30000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(BASE, wait_until='domcontentloaded', timeout=90000)
        page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
        page.get_by_label('Email', exact=True).fill(owner['email'])
        page.get_by_label('Password', exact=True).fill(PASSWORD)
        page.get_by_role('button', name='Sign In', exact=True).click()
        row = page.locator('#message-' + message['id'])
        expect(row).to_be_visible(timeout=90000)
        reveal_message_actions(row)
        row.get_by_role('button', name='Save message', exact=True).click()
        expect(row.get_by_role('button', name='Remove saved message', exact=True)).to_have_attribute('aria-pressed', 'true')
        row.get_by_role('button', name='Pin message', exact=True).click()
        expect(row.get_by_role('button', name='Unpin message', exact=True)).to_have_attribute('aria-pressed', 'true')
        page.reload(wait_until='domcontentloaded')
        expect(row).to_be_visible()
        reveal_message_actions(row)
        expect(row.get_by_role('button', name='Remove saved message', exact=True)).to_have_attribute('aria-pressed', 'true')
        expect(row.get_by_role('button', name='Unpin message', exact=True)).to_have_attribute('aria-pressed', 'true')
        open_chat_tool(page, 'Saved items and decisions')
        panel = page.get_by_role('region', name=re.compile(r'^(Channel notebook|Decisions in this conversation)$'))
        expect(panel).to_have_attribute('data-presentation', 'flow')
        expect(panel.get_by_text(message['content'], exact=True)).to_be_visible()
        select_notebook_section(panel, 'Saved')
        expect(panel.get_by_text(message['content'], exact=True)).to_be_visible()
        select_notebook_section(panel, 'Decisions')
        panel.get_by_role('button', name='Record a decision', exact=True).click()
        panel.get_by_label('Title', exact=True).fill('Launch agreement ' + RUN)
        panel.get_by_label('Decision and context', exact=True).fill('We will launch with the small, useful workflow first.')
        panel.get_by_label('Status', exact=True).select_option('accepted')
        panel.get_by_label('Responsible person', exact=True).select_option(member['user_id'])
        panel.get_by_label('Decision date', exact=True).fill('2026-10-20')
        panel.get_by_role('button', name='Record decision', exact=True).click()
        expect(panel.get_by_role('heading', name='Launch agreement ' + RUN, exact=True)).to_be_visible()
        panel.get_by_role('button', name='Revise', exact=True).click()
        panel.get_by_label('Decision and context', exact=True).fill('Revised launch agreement with a named owner.')
        panel.get_by_role('button', name='Save revision', exact=True).click()
        expect(panel.get_by_text('Revised launch agreement with a named owner.', exact=True)).to_be_visible()
        panel.get_by_role('button', name='History', exact=True).click()
        expect(panel.get_by_role('list', name='Decision revision history')).to_contain_text('We will launch with the small, useful workflow first.')
        select_notebook_section(panel, 'Notifications')
        panel.get_by_label('Every message').check()
        panel.get_by_label('Email summaries', exact=True).select_option('weekly')
        panel.get_by_role('button', name='Save preferences', exact=True).click()
        expect(panel.get_by_role('status')).to_contain_text('have been saved')
        assert api('GET', '/notifications/channels/' + channel['id'] + '/settings', owner) == {'level': 'all', 'email_digest': 'weekly'}
        page.set_viewport_size({'width': 320, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert panel.evaluate('element => element.scrollWidth <= element.clientWidth + 1')
        page.keyboard.press('Shift+Tab')
        assert panel.evaluate('element => element.contains(document.activeElement)')
        page.screenshot(path='/tmp/freecollab-collaboration-mobile.png', full_page=True)
        composer = page.locator('.fc-chat-composer textarea')
        composer.click()
        expect(composer).to_be_focused()
        panel.locator('details.fc-collaboration-flow-options > summary').click()
        page.keyboard.press('Escape')
        expect(panel).to_have_count(0)
        page.set_viewport_size({'width': 1440, 'height': 1000})
        reveal_message_actions(row)
        row.get_by_role('button', name='Reply in thread', exact=True).click()
        thread = page.get_by_role('dialog', name='Thread', exact=True)
        expect(thread.get_by_role('button', name='Watching thread', exact=True)).to_have_attribute('aria-pressed', 'true')
        thread.get_by_role('button', name='Watching thread', exact=True).click()
        expect(thread.get_by_role('button', name='Watch thread', exact=True)).to_have_attribute('aria-pressed', 'false')
        page.keyboard.press('Escape')
        followup = api('POST', '/messages/channels/' + channel['id'], member, {
            'content': 'A mentioned thread follow-up ' + RUN, 'parent_id': message['id'], 'mentions': [owner['user_id']],
        })
        expect(page.locator('.fc-notification-count')).to_contain_text('1')
        page.get_by_role('button', name='Notifications', exact=True).click()
        notifications = page.get_by_role('dialog', name='Notifications', exact=True)
        notifications.get_by_role('button', name='You were mentioned, unread. Mark as read', exact=True).click()
        expect(page.get_by_role('dialog', name='Thread', exact=True)).to_contain_text(followup['content'])
        assert api('GET', '/notifications/unread-count', owner)['unread_count'] == 0
        page.keyboard.press('Escape')
        assert len(api('GET', '/bookmarks', owner)) == 1
        assert api('GET', '/bookmarks', member) == []
        assert len(api('GET', '/channels/' + channel['id'] + '/pins', member)) == 1
        decisions = api('GET', '/channels/' + channel['id'] + '/decisions', member)
        assert decisions[0]['revision'] == 2 and decisions[0]['responsible_user_id'] == member['user_id']
        assert not errors, 'Unexpected browser errors: ' + '; '.join(errors)
        print('Collaboration browser passed: private bookmarks, shared pins, immutable decision revisions, channel notification/digest settings, watched threads, mention notification navigation, 320px layout and keyboard focus.')
    finally:
        context.close()
        browser.close()
