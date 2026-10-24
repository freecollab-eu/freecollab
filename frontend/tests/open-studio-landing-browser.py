#!/usr/bin/env python3
"""Exercise only the built public landing's real, memory-only studio preview.

Use an owned static website origin. This script starts no server or account.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--report', type=Path, default=ROOT/'.freecollab/proof-results/open-studio-landing.json')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    origin = urlsplit(base)
    if origin.scheme != 'http' or origin.hostname not in {'127.0.0.1', 'localhost', '::1'} or origin.path or origin.query or origin.fragment or origin.username or origin.password:
        parser.error('Use only the exact origin of an owned loopback static website fixture.')
    checks, errors, prohibited, sockets, screenshots = {}, [], [], [], []
    seed = {'freecollab_token': 'open-studio-account-fixture', 'freecollab_user_id': 'open-studio-user-fixture',
            'freecollab_public_key': 'open-studio-public-fixture', 'freecollab_encrypted_private_key': 'open-studio-opaque-fixture'}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),
                                             headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = browser.new_context(viewport={'width': 1440, 'height': 1050}, locale='en-GB', reduced_motion='reduce')
        context.add_init_script('''
          if (!sessionStorage.getItem('open-studio-landing-seeded')) {
            for (const [key,value] of Object.entries('''+json.dumps(seed)+''')) localStorage.setItem(key,value);
            sessionStorage.setItem('open-studio-landing-seeded','yes');
          }
        ''')
        def route(request):
            url = urlsplit(request.request.url)
            if url.scheme in {'blob', 'data'}:
                request.continue_(); return
            if (url.scheme, url.netloc) != (origin.scheme, origin.netloc) or re.match(r'^/(api|socket\.io|embed|native)(/|$)', url.path):
                prohibited.append(url.path); request.abort(); return
            request.continue_()
        context.route('**/*', route)
        page = context.new_page(); page.set_default_timeout(15000)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('websocket', lambda socket: sockets.append(urlsplit(socket.url).path))
        try:
            page.goto(base, wait_until='networkidle')
            expect(page.get_by_role('heading', level=1)).to_have_text('Good work.Your terms.')
            expect(page.get_by_text('Shall we open up towards the courtyard?', exact=True)).to_be_visible()
            expect(page.locator('.fc-preview-sidebar')).to_have_count(0)
            assert page.locator('.fc-preview-material > img').evaluate('(image)=>image.complete && image.naturalWidth>0')
            before = page.evaluate('Object.fromEntries(Object.entries(localStorage))')
            root_style = page.locator('html').get_attribute('style')
            initial_accent = page.locator('.fc-preview').evaluate('(element)=>getComputedStyle(element).getPropertyValue("--fc-accent")')
            checks['real_studio_conversation_local_material_and_no_dashboard_sidebar'] = True

            reaction = page.get_by_role('button', name="Like Maja's sample message", exact=True)
            reaction.click(); expect(reaction).to_have_attribute('aria-pressed', 'true')
            expect(reaction).to_have_text('2')
            page.get_by_role('button', name='Read sample project notes', exact=True).click()
            expect(page.get_by_text('For the courtyard review', exact=True)).to_be_visible()
            message = 'A local thought <img src="https://invalid.example/tracker">'
            page.get_by_role('textbox', name='Write a sample message', exact=True).fill(message)
            page.get_by_role('button', name='Send sample message', exact=True).click()
            expect(page.locator('.fc-preview-message.is-own p')).to_have_text(message)
            expect(page.locator('.fc-preview-message.is-own img')).to_have_count(0)
            checks['actual_plaintext_send_reaction_and_sample_file_interactions'] = True

            palettes = page.get_by_role('group', name='Sample color palette', exact=True).get_by_role('button')
            palettes.nth(2).click()
            expect(palettes.nth(2)).to_have_attribute('aria-pressed', 'true')
            assert page.locator('.fc-preview').evaluate('(element)=>getComputedStyle(element).getPropertyValue("--fc-accent")') != initial_accent
            page.get_by_label('Sample typeface', exact=True).select_option('manrope')
            expect(page.locator('.fc-preview')).to_have_css('font-family', re.compile('Manrope'))
            assert page.locator('html').get_attribute('style') == root_style
            assert page.evaluate('Object.fromEntries(Object.entries(localStorage))') == before, 'The sample wrote persistent storage'
            checks['palette_and_real_typeface_are_sample_only_with_zero_storage_writes'] = True

            channel = page.get_by_label('Sample channels', exact=True)
            channel.select_option('notes')
            expect(page.get_by_text('No sample notes yet.', exact=True)).to_be_visible()
            page.get_by_role('button', name='Write the first note', exact=True).click()
            expect(page.get_by_role('textbox', name='Write a sample message')).to_have_value('An idea for our next review: ')
            page.get_by_role('textbox', name='Write a sample message').fill('One small next step')
            page.get_by_role('button', name='Send sample message', exact=True).click()
            channel.select_option('general')
            expect(page.get_by_text('One small next step', exact=True)).to_have_count(0)
            channel.select_option('notes')
            expect(page.get_by_text('One small next step', exact=True)).to_be_visible()
            page.get_by_role('button', name='Reset sample conversation', exact=True).click()
            expect(page.locator('.fc-preview-message.is-own')).to_have_count(0)
            expect(channel).to_have_value('general')
            expect(page.get_by_label('Sample typeface', exact=True)).to_have_value('')
            checks['channel_local_notes_and_reset_preserve_existing_controls'] = True

            def capture(name):
                page.evaluate('window.scrollTo(0,0)')
                page.evaluate('document.fonts.ready')
                page.wait_for_function('document.documentElement.scrollWidth<=innerWidth', timeout=5000)
                path = args.report.with_name(name)
                page.screenshot(path=str(path), full_page=True, animations='disabled')
                screenshots.append(str(path))
            capture('open-studio-landing-en-desktop.png')
            page.get_by_label('Language', exact=True).select_option('sv')
            expect(page.locator('html')).to_have_attribute('lang', 'sv')
            expect(page.get_by_text('Ska vi öppna upp mot gården?', exact=True)).to_be_visible()
            expect(page.get_by_text('Gården, version 04', exact=True)).to_be_visible()
            for width in (1440, 390, 320):
                page.set_viewport_size({'width': width, 'height': 1000})
                capture(f'open-studio-landing-sv-{width}.png')
            checks['english_swedish_and_actual_1440_390_320px_layout'] = True

            page.get_by_role('button', name='Dokumentation', exact=True).first.click()
            expect(page.locator('.fc-docs')).to_be_visible()
            page.goto(base, wait_until='networkidle')
            page.locator('.fc-public-nav-cta').click()
            expect(page.locator('.fc-showroom')).to_be_visible()
            checks['documentation_and_existing_website_demo_callbacks'] = True
            actual = page.evaluate('(keys)=>Object.fromEntries(keys.map(key=>[key,localStorage.getItem(key)]))', list(seed))
            assert actual == seed, 'Existing account keys changed'
            assert page.evaluate('navigator.serviceWorker.getRegistrations().then(rows=>rows.length)') == 0
            assert not prohibited and not sockets and not errors, {'prohibited': prohibited, 'websockets': sockets, 'errors': errors}
            checks['no_account_provider_native_or_worker_activity_and_existing_keys_unchanged'] = True
        finally:
            context.close(); browser.close()
    names = ['frontend/src/components/Landing.tsx', 'frontend/src/styles/public.css',
             'frontend/src/i18n/open-studio-public.sv.ts', 'frontend/tests/open-studio-landing-browser.py']
    args.report.write_text(json.dumps({'status': 'PASS', 'groups': len(checks), 'checks': checks,
        'source_sha256': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names},
        'screenshots': screenshots, 'scope': 'Actual built static website and local sample only. No hosted deployment or live collaboration/provider behavior is claimed.'}, indent=2)+'\n')
    print(f'PASS: {len(checks)} open-studio landing browser groups')


if __name__ == '__main__':
    main()
