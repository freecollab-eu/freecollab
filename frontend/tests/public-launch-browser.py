#!/usr/bin/env python3
"""Guest landing/#demo proof against the actual built frontend; no provider calls."""
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
from threading import Thread
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT.parent / '.freecollab/proof-results/open-release-public-browser.json'


def main():
    dist = ROOT / 'frontend/dist'
    if not (dist / 'index.html').is_file():
        raise RuntimeError('Build the frontend with npm run build before this proof')
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(dist), **kwargs)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    checks, errors, unwanted, screenshots = {}, [], [], []
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
            context = browser.new_context(viewport={'width':1440, 'height':1000}, locale='en-GB', reduced_motion='reduce')
            page = context.new_page(); page.set_default_timeout(15000)
            page.on('pageerror', lambda error: errors.append(str(error)))
            def route(request):
                parsed = urlsplit(request.request.url)
                if parsed.hostname != '127.0.0.1' or parsed.path.startswith(('/api/', '/socket.io')):
                    unwanted.append(parsed.scheme + '://' + (parsed.hostname or '') + parsed.path)
                    request.abort()
                else: request.continue_()
            page.route('**/*', route)
            page.goto(origin, wait_until='networkidle')
            expect(page.get_by_role('heading', level=1)).to_have_text('Good work.Your terms.')
            expect(page.get_by_role('navigation', name='Main navigation').get_by_role('button', name='Open App', exact=True)).to_be_visible()
            expect(page.get_by_role('heading', name='Microsoft 365', exact=True)).to_have_count(1)
            expect(page.get_by_role('heading', name='Google Workspace', exact=True)).to_have_count(1)
            assert page.locator('meta[property="og:image"]').get_attribute('content') == 'https://freecollab.eu/social-card.png'
            assert page.locator('a[href$="/LICENSE"]').count() == 1
            checks['production_landing_distinct_demo_app_source_and_both_customer_provider_paths'] = True
            def capture(name, full=False):
                page.evaluate('window.scrollTo(0, 0)')
                page.evaluate('document.fonts.ready')
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), name
                path = REPORT.with_name(name); page.screenshot(path=str(path), full_page=full, animations='disabled'); screenshots.append(str(path))
            capture('open-release-website-en-desktop.png')
            page.get_by_label('Language', exact=True).select_option('sv')
            expect(page.locator('html')).to_have_attribute('lang', 'sv')
            expect(page.get_by_role('heading', level=1)).to_have_text('Bra arbete.På era villkor.')
            page.locator('.fc-preview-composer input').fill('Good work.')
            page.locator('.fc-preview-composer button').click()
            expect(page.locator('.fc-preview-message.is-own p')).to_have_text('Good work.')
            page.locator('.fc-preview-titlebar button').click()
            capture('open-release-website-sv-desktop.png')
            capture('open-release-website-sv-full.png', True)
            for width in [390, 320]:
                page.set_viewport_size({'width':width, 'height':900})
                capture(f'open-release-website-sv-{width}.png')
                page.get_by_text('Är FreeCollab gratis?', exact=True).click()
                expect(page.get_by_text('Källkoden finns under MIT-licens', exact=False)).to_be_visible()
                page.get_by_text('Är FreeCollab gratis?', exact=True).click()
            checks['saved_guest_swedish_language_faq_and_desktop_390_320_without_horizontal_overflow'] = True
            page.get_by_role('button', name='Utforska demon', exact=True).first.click()
            expect(page.locator('.fc-showroom')).to_be_visible()
            assert page.evaluate('location.hash') == '#demo'
            capture('open-release-demo-sv-320.png')
            page.get_by_role('textbox', name='Ditt demomeddelande', exact=True).fill('Only this guest browser tab')
            page.get_by_role('button', name='Skicka demomeddelande', exact=True).click()
            expect(page.get_by_role('log').get_by_text('Only this guest browser tab', exact=True)).to_be_visible()
            page.reload(wait_until='networkidle')
            expect(page.locator('.fc-showroom')).to_be_visible()
            expect(page.get_by_role('log').get_by_text('Only this guest browser tab', exact=True)).to_have_count(0)
            page.set_viewport_size({'width':1440, 'height':1050})
            capture('open-release-demo-sv-desktop.png')
            checks['real_app_hash_demo_guest_entry_reload_clears_content_and_swedish_persists'] = True
            page.get_by_role('button', name='Tillbaka till FreeCollab', exact=True).click()
            expect(page.get_by_role('heading', level=1)).to_have_text('Bra arbete.På era villkor.')
            assert page.evaluate('location.hash') == ''
            page.get_by_role('button', name='Dokumentation', exact=True).first.click()
            expect(page.get_by_role('heading', level=1)).to_have_text('Introduktion')
            expect(page.get_by_text('FreeCollab är en arbetsyta med MIT-licens', exact=False)).to_be_visible()
            checks['back_route_and_documentation_translate_at_render_time'] = True
            assert not errors, errors
            assert not unwanted, unwanted
            checks['zero_javascript_errors_account_api_calls_and_external_provider_requests'] = True
            context.close(); browser.close()
        hashes = {str(p.relative_to(dist)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dist.rglob('*')) if p.is_file()}
        REPORT.write_text(json.dumps({'ok':True, 'checks':checks, 'production_dist_sha256':hashes, 'screenshots':screenshots, 'external_requests':0, 'account_api_calls':0, 'cleanup':'Private static HTTP server and browser context closed; no database/accounts/provider configuration created.', 'scope':'Actual built App entry, guest landing and local showroom; no live customer/provider or Railway deployment claim.'}, indent=2)+'\n')
        REPORT.chmod(0o600)
        print(f'Public built-app proof: {len(checks)}/{len(checks)} passed')
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


if __name__ == '__main__': main()
