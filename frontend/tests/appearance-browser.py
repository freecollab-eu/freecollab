"""Verify personal appearance through the real client and authenticated API.

Run against disposable API/Vite services from the repository root:
CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/appearance-browser.py
CHAT_BASE_URL defaults to http://127.0.0.1:3000. Creates two isolated accounts;
never run this script against a customer database.
"""
import json
import os
import re
import uuid
from urllib.request import Request, urlopen

from playwright.sync_api import Error as PlaywrightError, expect, sync_playwright


BASE = os.getenv('CHAT_BASE_URL', 'http://127.0.0.1:3000').rstrip('/')
PASSWORD = 'Personal-appearance-browser-test!'
RUN = uuid.uuid4().hex[:12]
DEFAULT = {
    'version': 1,
    'mode': 'light',
    'accent_color': '#36594c',
    'background_color': '#f6f5f1',
    'dark_background_color': '#151b18',
    'font_family': 'inter',
    'font_size': 'medium',
    'density': 'comfortable',
    'corner_style': 'soft',
}


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
    user = api('POST', '/auth/register', data={
        'name': name, 'email': email, 'password': PASSWORD,
    })
    return {**user, 'email': email}


def login(page, user):
    page.goto(BASE, wait_until='domcontentloaded', timeout=90000)
    page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
    page.get_by_label('Email', exact=True).fill(user['email'])
    page.get_by_label('Password', exact=True).fill(PASSWORD)
    with page.expect_response(lambda response:
                              response.url.endswith('/preferences/appearance')
                              and response.request.method == 'GET'):
        page.get_by_role('button', name='Sign In', exact=True).click()
    expect(page.get_by_role('button', name='Appearance settings', exact=True).first).to_be_visible()


def css(page, token):
    return page.evaluate('token => getComputedStyle(document.documentElement).getPropertyValue(token).trim()', token)


def appearance_snapshot(page):
    return page.evaluate("""() => {
      const style = getComputedStyle(document.documentElement);
      return Object.fromEntries(['--fc-bg', '--fc-text', '--fc-muted', '--fc-accent',
        '--fc-on-accent', '--fc-font', '--fc-message-size', '--fc-radius', '--fc-space']
        .map(key => [key, style.getPropertyValue(key).trim()]));
    }""")


def assert_mobile_fit(page, description):
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), description


def open_appearance(page):
    button = page.get_by_role('button', name='Appearance settings', exact=True).first
    if not button.is_visible():
        page.get_by_role('button', name='Toggle channels', exact=True).click()
    button.click()
    dialog = page.get_by_role('dialog')
    expect(dialog).to_be_visible()
    return dialog


def choose(dialog, name, exact=True):
    radio = dialog.get_by_role('radio', name=name, exact=exact)
    radio.locator('..').click()
    expect(radio).to_be_checked()


def save(page, dialog):
    with page.expect_response(lambda response:
                              response.url.endswith('/preferences/appearance')
                              and response.request.method == 'PUT') as saved:
        dialog.get_by_role('button', name='Save changes', exact=True).click()
    assert saved.value.ok, saved.value.status
    expect(dialog).not_to_be_visible()


def contrast(foreground, background):
    def luminance(color):
        values = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in values]
        return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    first, second = sorted((luminance(foreground), luminance(background)))
    return (second + 0.05) / (first + 0.05)


def assert_theme_contrast(page):
    for foreground, background in (
        ('--fc-text', '--fc-bg'), ('--fc-muted', '--fc-bg'),
        ('--fc-text', '--fc-surface'), ('--fc-muted', '--fc-surface'),
        ('--fc-on-accent', '--fc-accent'),
    ):
        first, second = css(page, foreground), css(page, background)
        assert re.fullmatch(r'#[0-9a-fA-F]{6}', first), (foreground, first)
        assert re.fullmatch(r'#[0-9a-fA-F]{6}', second), (background, second)
        assert contrast(first, second) >= 4.5, f'{foreground} {first} lacks contrast on {background} {second}'


# The interaction scenario below intentionally uses the public labels of the
# settings panel. It exercises the same controls available to keyboard users.
alice, bob = register('AppearanceAlice'), register('AppearanceBob')
assert api('GET', '/preferences/appearance', alice) == DEFAULT
assert api('GET', '/preferences/appearance', bob) == DEFAULT

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or None,
                                headless=True, args=['--no-sandbox'], timeout=90000)
    try:
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        page.set_default_timeout(30000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        login(page, alice)
        baseline = appearance_snapshot(page)
        assert_theme_contrast(page)
        dialog = open_appearance(page)
        expect(dialog).to_have_accessible_name('Make space your own.')
        expect(dialog.get_by_role('button', name='Close appearance settings', exact=True)).to_be_focused()
        for key in ['Shift+Tab', *(['Tab'] * 35)]:
            page.keyboard.press(key)
            assert dialog.evaluate('element => element.contains(document.activeElement)'), 'Focus escaped the settings dialog'
        dialog.get_by_role('button', name='Fjord', exact=True).click()
        expect(dialog.get_by_role('button', name='Fjord', exact=True)).to_have_attribute('aria-pressed', 'true')
        assert appearance_snapshot(page) != baseline
        page.keyboard.press('Escape')
        expect(dialog).not_to_be_visible()
        expect(page.get_by_role('button', name='Appearance settings', exact=True).first).to_be_focused()
        assert appearance_snapshot(page) == baseline
        assert api('GET', '/preferences/appearance', alice) == DEFAULT
        print('PASS: palette preview, focus trap, Escape rollback and restored focus', flush=True)

        dialog = open_appearance(page)
        dialog.get_by_role('button', name='Clay', exact=True).click()
        dialog.get_by_label('Accent, hexadecimal color', exact=True).fill('#754b68')
        dialog.get_by_label('Canvas · light, hexadecimal color', exact=True).fill('#f5f1f4')
        choose(dialog, re.compile('Manrope'), exact=False)
        choose(dialog, 'Large')
        choose(dialog, 'Compact')
        choose(dialog, 'Square')
        expected = {
            **DEFAULT, 'accent_color': '#754b68', 'background_color': '#f5f1f4',
            'dark_background_color': '#231b17', 'font_family': 'manrope',
            'font_size': 'large', 'density': 'compact', 'corner_style': 'square',
        }
        save(page, dialog)
        assert api('GET', '/preferences/appearance', alice) == expected
        personal = appearance_snapshot(page)
        assert 'Manrope' in personal['--fc-font']
        assert personal['--fc-message-size'] != baseline['--fc-message-size']
        assert personal['--fc-radius'] != baseline['--fc-radius']
        assert personal['--fc-space'] != baseline['--fc-space']
        page.reload(wait_until='domcontentloaded')
        expect(page.get_by_role('button', name='Appearance settings', exact=True)).to_be_visible()
        assert appearance_snapshot(page) == personal
        assert_theme_contrast(page)
        fresh_context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        fresh = fresh_context.new_page()
        login(fresh, alice)
        assert appearance_snapshot(fresh) == personal, 'Saved preferences did not follow the account into a fresh browser'
        fresh_context.close()
        print('PASS: custom colors, bundled font, size, density and corners persist on server and fresh-browser login', flush=True)

        dialog = open_appearance(page)
        dialog.get_by_role('button', name='Reset', exact=True).click()
        assert appearance_snapshot(page) == baseline
        assert api('GET', '/preferences/appearance', alice) == expected, 'Reset persisted without Save'
        dialog.get_by_role('button', name='Cancel', exact=True).click()
        assert appearance_snapshot(page) == personal
        dialog = open_appearance(page)
        choose(dialog, 'Dark')
        assert page.locator('html').get_attribute('data-mode') == 'dark'
        assert_theme_contrast(page)
        dialog.get_by_label('Canvas · dark, hexadecimal color', exact=True).fill('#000000')
        dialog.get_by_label('Accent, hexadecimal color', exact=True).fill('#ffffff')
        assert_theme_contrast(page)
        choose(dialog, 'System')
        page.emulate_media(color_scheme='light')
        expect(page.locator('html')).to_have_attribute('data-mode', 'light')
        page.emulate_media(color_scheme='dark')
        expect(page.locator('html')).to_have_attribute('data-mode', 'dark')
        page.keyboard.press('Escape')
        assert appearance_snapshot(page) == personal
        print('PASS: reset remains a preview; light, dark, device changes and extreme colors remain readable', flush=True)

        dialog = open_appearance(page)
        choose(dialog, 'Rounded')
        def fail_save(route):
            if route.request.method == 'PUT':
                route.fulfill(status=503, content_type='application/json', body=json.dumps({'detail': 'Temporary test failure'}))
            else:
                route.continue_()
        page.route('**/api/preferences/appearance', fail_save)
        dialog.get_by_role('button', name='Save changes', exact=True).click()
        expect(dialog.get_by_role('alert')).to_contain_text('couldn’t be saved')
        expect(dialog).to_be_visible()
        assert api('GET', '/preferences/appearance', alice) == expected
        page.unroute('**/api/preferences/appearance', fail_save)
        dialog.get_by_role('button', name='Cancel', exact=True).click()
        assert appearance_snapshot(page) == personal
        print('PASS: failed save reports failure, preserves preview and leaves persisted settings intact', flush=True)

        held_put = []
        def delay_save(route):
            if route.request.method == 'PUT':
                held_put.append(route)
            else:
                route.continue_()
        page.route('**/api/preferences/appearance', delay_save)
        dialog = open_appearance(page)
        choose(dialog, re.compile('Source Serif'), exact=False)
        with page.expect_request(lambda request:
                                 request.url.endswith('/preferences/appearance') and request.method == 'PUT'):
            dialog.get_by_role('button', name='Save changes', exact=True).click()
        expect(dialog.get_by_role('radio', name='Compact', exact=True)).to_be_disabled()
        page.keyboard.press('Escape')
        dialog = open_appearance(page)
        assert held_put, 'PUT was not delayed'
        response = held_put[0].fetch()
        held_put[0].fulfill(response=response)
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role('button', name='Save changes', exact=True)).to_be_disabled()
        expect(dialog.get_by_role('radio', name=re.compile('Source Serif'))).to_be_checked()
        dialog.get_by_role('button', name='Cancel', exact=True).click()
        expected['font_family'] = 'serif'
        assert api('GET', '/preferences/appearance', alice) == expected
        page.unroute('**/api/preferences/appearance', delay_save)
        personal = appearance_snapshot(page)
        print('PASS: delayed save cannot close a reopened dialog or create a false unsaved state', flush=True)

        page.set_viewport_size({'width': 390, 'height': 844})
        expect(page.get_by_role('button', name='Toggle channels', exact=True)).to_have_attribute('aria-expanded', 'false')
        dialog = open_appearance(page)
        assert_mobile_fit(page, 'Appearance panel overflowed mobile viewport')
        choose(dialog, 'Large')
        choose(dialog, 'Rounded')
        assert_mobile_fit(page, 'Personalized appearance panel overflowed mobile viewport')
        dialog.get_by_role('button', name='Cancel', exact=True).click()
        assert appearance_snapshot(page) == personal
        assert_mobile_fit(page, 'Chat overflowed mobile viewport')
        print('PASS: mobile entry, full settings controls and custom large-font layout fit390px', flush=True)

        page.set_viewport_size({'width': 1440, 'height': 1000})
        expect(page.locator('.fc-chat-sidebar')).to_have_attribute('aria-hidden', 'false')
        sibling = context.new_page()
        sibling.goto(BASE, wait_until='domcontentloaded')
        expect(sibling.get_by_role('button', name='Appearance settings', exact=True)).to_be_visible()
        assert appearance_snapshot(sibling) == personal
        held_get = []
        def delay_old_get(route):
            if route.request.method == 'GET' and not held_get:
                held_get.append(route)
            else:
                route.continue_()
        page.route('**/api/preferences/appearance', delay_old_get)
        with page.expect_request(lambda request:
                                 request.url.endswith('/preferences/appearance') and request.method == 'GET'):
            page.reload(wait_until='domcontentloaded')
        page.get_by_role('button', name='Sign out', exact=True).click()
        expect(page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True)).to_be_visible()
        expect(sibling.get_by_role('navigation').get_by_role('button', name='Open App', exact=True)).to_be_visible()
        assert appearance_snapshot(page) == baseline
        assert appearance_snapshot(sibling) == baseline
        login(page, bob)
        expect(sibling.get_by_role('button', name='Appearance settings', exact=True)).to_be_visible()
        assert appearance_snapshot(page) == baseline
        assert appearance_snapshot(sibling) == baseline
        try:
            held_get[0].fulfill(status=200, content_type='application/json', body=json.dumps(expected))
        except PlaywrightError:
            # AbortController may already have cancelled this obsolete request.
            assert held_get[0].request.failure, 'Unexpected failure fulfilling the obsolete appearance request'
        page.unroute('**/api/preferences/appearance', delay_old_get)
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        assert appearance_snapshot(page) == baseline, 'Old account response replaced the new account appearance'
        dialog = open_appearance(page)
        expect(dialog.get_by_role('radio', name='Light', exact=True)).to_be_checked()
        expect(dialog.get_by_role('radio', name=re.compile('Inter'))).to_be_checked()
        dialog.get_by_role('button', name='Ink', exact=True).click()
        save(page, dialog)
        bob_settings = api('GET', '/preferences/appearance', bob)
        assert bob_settings['accent_color'] == '#454951'
        assert api('GET', '/preferences/appearance', alice) == expected
        expect(sibling.locator('html')).to_have_attribute('data-font', 'inter')
        sibling.wait_for_function("getComputedStyle(document.documentElement).getPropertyValue('--fc-accent-original').trim() === '#454951'")
        dialog = open_appearance(page)
        dialog.get_by_role('button', name='Reset', exact=True).click()
        save(page, dialog)
        assert api('GET', '/preferences/appearance', bob) == DEFAULT
        held_channels = []
        def delay_old_channels(route):
            if route.request.method == 'GET' and not held_channels:
                held_channels.append(route)
            else:
                route.continue_()
        page.route('**/api/channels*', delay_old_channels)
        with page.expect_request(lambda request:
                                 request.url.split('?')[0].endswith('/api/channels') and request.method == 'GET'):
            page.reload(wait_until='domcontentloaded')
        sibling.get_by_role('button', name='Sign out', exact=True).click()
        expect(sibling.get_by_role('navigation').get_by_role('button', name='Open App', exact=True)).to_be_visible()
        login(sibling, alice)
        expect(page.get_by_role('button', name='Appearance settings', exact=True)).to_be_visible()
        assert appearance_snapshot(page) == personal
        try:
            held_channels[0].fulfill(status=401, content_type='application/json',
                                    body=json.dumps({'detail': 'Old account session expired'}))
        except PlaywrightError:
            # It is equally valid to cancel the old component's request.
            assert held_channels[0].request.failure, 'Unexpected failure fulfilling the obsolete channels request'
        expired_response = held_channels[0].request.response()
        if expired_response:
            assert expired_response.status == 401
            assert expired_response.json()['detail'] == 'Old account session expired'
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        expect(page.get_by_role('button', name='Appearance settings', exact=True)).to_be_visible()
        assert page.evaluate("localStorage.getItem('freecollab_user_id')") == alice['user_id']
        assert appearance_snapshot(page) == personal, 'Old401 expired the current account session'
        page.unroute('**/api/channels*', delay_old_channels)
        assert not errors, 'Browser exceptions: ' + '; '.join(errors)
        print('PASS: account isolation, late response/401 guards, cross-tab login/logout and preference synchronization', flush=True)
    finally:
        browser.close()
