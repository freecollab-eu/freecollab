#!/usr/bin/env python3
"""Built public App/landing and real-product demo handoff; no provider calls.

The expected iframe navigation is inspected and stopped before a sample session
can be created. Actual sample-workspace operations are separately verified by
product-demo-browser.py against the real application and isolated backend.
"""
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(os.getenv('PUBLIC_LAUNCH_REPORT', str(ROOT.parent / '.freecollab/proof-results/open-release-public-browser.json')))


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
    checks, errors, unwanted, screenshots, demo_navigations = {}, [], [], [], []
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
            context = browser.new_context(viewport={'width':1440, 'height':1000}, locale='en-GB', reduced_motion='reduce')
            page = context.new_page(); page.set_default_timeout(15000)
            page.on('pageerror', lambda error: errors.append(str(error)))
            def route(request):
                parsed = urlsplit(request.request.url)
                if request.request.is_navigation_request() and request.request.frame != page.main_frame:
                    # Inspect only the actual product iframe handoff. Never call
                    # the public demo backend from a landing/build acceptance.
                    assert parsed.path.rstrip('/').endswith('/product-demo'), request.request.url
                    assert parsed.scheme == 'https' or parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'}
                    assert not parsed.username and not parsed.password
                    demo_navigations.append(request.request.url)
                    request.abort()
                    return
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
            preview = page.locator('.fc-public-product-preview')
            expect(preview).to_contain_text('Samma produkt, med exempeldata.')
            product_image = preview.locator('img.fc-public-product-image')
            expect(product_image).to_be_visible()
            expect(product_image).to_have_js_property('complete', True)
            assert product_image.evaluate('(image)=>image.naturalWidth>0&&image.naturalHeight>0'), 'The actual product screenshot did not load'
            assert product_image.evaluate('(image)=>image.naturalWidth===Number(image.getAttribute("width"))&&image.naturalHeight===Number(image.getAttribute("height"))'), 'Product screenshot dimensions do not match its native capture'
            screenshot_response = context.request.get(product_image.evaluate('(image)=>image.src'))
            assert screenshot_response.ok
            screenshot_sha256 = hashlib.sha256(screenshot_response.body()).hexdigest()
            assert screenshot_sha256 == hashlib.sha256((ROOT / 'frontend/src/assets/product/courtyard-product.png').read_bytes()).hexdigest(), 'Built landing uses a stale product screenshot'
            expect(preview.get_by_role('button', name='Utforska demon', exact=True)).to_be_visible()
            expect(page.locator('.fc-preview, .fc-preview-composer, .fc-preview-message')).to_have_count(0)
            capture('open-release-website-sv-desktop.png')
            capture('open-release-website-sv-full.png', True)
            for width in [390, 320]:
                page.set_viewport_size({'width':width, 'height':900})
                capture(f'open-release-website-sv-{width}.png')
                page.get_by_text('Är FreeCollab gratis?', exact=True).click()
                expect(page.get_by_text('Källkoden finns under MIT-licens', exact=False)).to_be_visible()
                page.get_by_text('Är FreeCollab gratis?', exact=True).click()
            checks['saved_guest_swedish_language_faq_and_desktop_390_320_without_horizontal_overflow'] = True
            preview.get_by_role('button', name='Utforska demon', exact=True).click()
            expect(page.locator('[data-product-demo-frame]')).to_be_visible()
            frame = page.get_by_title('FreeCollab produktdemo', exact=True)
            expect(frame).to_be_visible()
            assert page.evaluate('location.hash') == '#demo'
            source = frame.get_attribute('src')
            expected_demo = os.getenv('PUBLIC_LAUNCH_DEMO_URL') or os.getenv('VITE_PUBLIC_DEMO_URL')
            if expected_demo:
                actual, expected = urlsplit(source), urlsplit(expected_demo)
                assert (actual.scheme, actual.netloc, actual.path) == (expected.scheme, expected.netloc, expected.path)
            assert parse_qs(urlsplit(source).query) == {'section': ['chat'], 'locale': ['sv']}
            expect(page.locator('.fc-showroom, .fc-demo-desk, [data-demo-assist]')).to_have_count(0)
            launch = page.get_by_role('link', name='Öppna demon i en ny flik', exact=True)
            expect(launch).to_have_attribute('href', source)
            expect(launch).to_have_attribute('target', '_blank')
            assert 'noopener' in launch.get_attribute('rel')
            capture('open-release-demo-sv-320.png')
            page.reload(wait_until='networkidle')
            expect(page.locator('[data-product-demo-frame]')).to_be_visible()
            expect(page.get_by_title('FreeCollab produktdemo', exact=True)).to_have_attribute('src', source)
            for section, provider in [('documents', None), ('projects', None), ('connections', 'microsoft'), ('connections', 'google')]:
                fragment = '#demo=' + section + ('&provider=' + provider if provider else '')
                page.goto(origin + '/' + fragment, wait_until='networkidle')
                frame = page.get_by_title('FreeCollab produktdemo', exact=True)
                expect(frame).to_be_visible()
                query = parse_qs(urlsplit(frame.get_attribute('src')).query)
                assert query == {'section': [section], 'locale': ['sv'], **({'provider': [provider]} if provider else {})}
            page.set_viewport_size({'width':1440, 'height':1050})
            capture('open-release-demo-sv-desktop.png')
            assert demo_navigations, 'The built app never attempted its real product iframe handoff'
            checks['real_app_demo_handoff_reload_language_and_document_project_provider_destinations'] = True
            page.get_by_role('button', name='Till webbplatsen', exact=True).click()
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
        REPORT.write_text(json.dumps({'ok':True, 'checks':checks, 'production_dist_sha256':hashes, 'native_product_screenshot_sha256':screenshot_sha256, 'screenshots':screenshots, 'external_requests':0, 'account_api_calls':0, 'inspected_demo_iframe_navigations':len(demo_navigations), 'cleanup':'Private static HTTP server and browser context closed; expected demo iframe navigation was aborted before any sample account/session request.', 'scope':'Actual built App entry, guest landing with exact current native screenshot bytes and real-product iframe handoff. Inner sample-workspace behavior is verified separately by product-demo-browser.py; no provider or Railway deployment claim.'}, indent=2)+'\n')
        REPORT.chmod(0o600)
        print(f'Public built-app proof: {len(checks)}/{len(checks)} passed')
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


if __name__ == '__main__': main()
