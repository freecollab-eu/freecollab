"""Verify notification read state and preferences against disposable API services.

Run from the repository root with ENVIRONMENT=test, DATABASE_URL matching the
running API, and optional CHAT_BASE_URL/CHROMIUM_PATH. Requires backend Python
dependencies and Playwright. Creates a unique account and seeds notifications
for that account only. Never point browser tests at a customer installation.
"""
import json
import os
from pathlib import Path
import sys
import uuid
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright

if os.getenv('ENVIRONMENT') != 'test' or not os.getenv('DATABASE_URL'):
    raise RuntimeError('Use ENVIRONMENT=test and an explicit disposable DATABASE_URL matching the API.')

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from sqlmodel import Session  # noqa: E402
from app.database import engine  # noqa: E402
from app.models.notification import Notification  # noqa: E402

BASE = os.getenv('CHAT_BASE_URL', 'http://127.0.0.1:3000').rstrip('/')
RUN = uuid.uuid4().hex[:12]
PASSWORD = 'Notification-browser-test-password!'


def api(method, path, user=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if user:
        headers['Authorization'] = 'Bearer ' + user['access_token']
    request = Request(BASE + '/api' + path, method=method, headers=headers,
                      data=json.dumps(data).encode() if data is not None else None)
    with urlopen(request, timeout=45) as response:
        return json.load(response)


email = f'notification-{RUN}@example.com'
user = api('POST', '/auth/register', data={
    'name': 'Notification Browser', 'email': email, 'password': PASSWORD,
})
channel = api('POST', '/channels', user, {
    'name': 'Notification-' + RUN, 'type': 'group', 'member_ids': [],
})
with Session(engine) as session:
    session.add_all([
        Notification(user_id=user['user_id'], type='system', title='Already read ' + RUN,
                     body='An earlier workspace update.', read=True),
        Notification(user_id=user['user_id'], type='mention', title='A new mention ' + RUN,
                     body='A teammate included you in the conversation.'),
        Notification(user_id=user['user_id'], type='reply', title='A new reply ' + RUN,
                     body='There is another thought in the thread.'),
    ])
    session.commit()

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or None,
                                headless=True, args=['--no-sandbox'], timeout=90000)
    try:
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(BASE, wait_until='domcontentloaded', timeout=90000)
        page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
        page.get_by_label('Email', exact=True).fill(email)
        page.get_by_label('Password', exact=True).fill(PASSWORD)
        page.get_by_role('button', name='Sign In', exact=True).click()
        expect(page.get_by_placeholder('Message #' + channel['name'])).to_be_visible(timeout=90000)
        badge = page.locator('.fc-notification-count')
        expect(badge).to_contain_text('2')
        page.get_by_role('button', name='Notifications', exact=True).click()
        dialog = page.get_by_role('dialog', name='Notifications', exact=True)
        already_read = dialog.get_by_role('button', name='Already read ' + RUN, exact=True)
        expect(already_read).not_to_have_class('is-unread')
        already_read.click()
        assert api('GET', '/notifications/unread-count', user)['unread_count'] == 2
        dialog.get_by_role('button', name='A new mention ' + RUN + ', unread. Mark as read', exact=True).click()
        expect(dialog.get_by_role('button', name='A new mention ' + RUN, exact=True)).to_be_visible()
        expect(dialog.get_by_role('status')).to_contain_text('Marked as read.')
        assert api('GET', '/notifications/unread-count', user)['unread_count'] == 1
        page.keyboard.press('Escape')
        expect(badge).to_contain_text('1')
        print('PASS: API read:true stays read; marking one item updates real unread count and Bell badge', flush=True)

        page.get_by_role('button', name='Notifications', exact=True).click()
        dialog = page.get_by_role('dialog', name='Notifications', exact=True)
        dialog.get_by_role('button', name='Notification settings', exact=True).click()
        mentions = dialog.get_by_label('Mentions', exact=True)
        expect(mentions).to_be_checked()
        mentions.uncheck()
        expect(dialog.get_by_role('status')).to_contain_text('Preferences saved')
        assert api('GET', '/notifications/preferences', user)['mentions'] is False
        expect(dialog.get_by_label('Email Notifications', exact=True)).to_have_count(0)
        expect(dialog.get_by_label('Push Notifications', exact=True)).to_have_count(0)
        page.keyboard.press('Escape')
        page.reload(wait_until='domcontentloaded')
        expect(page.get_by_placeholder('Message #' + channel['name'])).to_be_visible()
        page.get_by_role('button', name='Notifications', exact=True).click()
        dialog = page.get_by_role('dialog', name='Notifications', exact=True)
        dialog.get_by_role('button', name='Notification settings', exact=True).click()
        expect(dialog.get_by_label('Mentions', exact=True)).not_to_be_checked()
        print('PASS: supported category choice persists through actual API GET and full browser reload', flush=True)

        dialog.get_by_role('button', name='Done', exact=True).click()
        dialog.get_by_role('button', name='Mark all read', exact=True).click()
        expect(dialog.get_by_role('status')).to_contain_text('You’re all caught up.')
        assert api('GET', '/notifications/unread-count', user)['unread_count'] == 0
        assert all(notification['read'] for notification in api('GET', '/notifications', user))
        page.keyboard.press('Escape')
        expect(badge).to_have_count(0)
        assert not errors, 'Unexpected browser errors: ' + '; '.join(errors)
        print('PASS: read-all persists for all fixture notifications and clears Bell badge; no browser errors', flush=True)
    finally:
        browser.close()
