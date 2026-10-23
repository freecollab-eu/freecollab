#!/usr/bin/env python3
"""Read-only Chromium acceptance of the separately built static website/demo.

Run against an owned website container, not the customer application image.
Every demo mutation remains inside the new browser context.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit
from urllib.request import urlopen
from urllib.error import HTTPError

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]


def product_documentation(page, base, report):
    """Inspect rendered current-product help; never follow its external links."""
    source = 'https://github.com/freecollab-eu/freecollab/blob/design/open-studio/docs/'
    topics = [
        ('Conversations and channels', 'Samtal och kanaler', 'CHANNELS.md'),
        ('Documents and stacks', 'Dokument och högar', 'SHARED_DOCUMENTS.md'),
        ('Projects, tasks and decisions', 'Projekt, uppgifter och beslut', 'PROJECT_TASKS.md'),
        ('Files and Office', 'Filer och Office', 'FILE_EXPERIENCE.md'),
        ('Calendar and meetings', 'Kalender och möten', 'CALENDAR_PLANNING.md'),
        ('Personal appearance and language', 'Personligt utseende och språk', 'APPEARANCE.md'),
        ('AI where you work', 'AI där du arbetar', 'COPILOT.md'),
        ('Your organization’s services', 'Organisationens tjänster', 'CONNECTION_SETUP.md'),
        ('Embedded collaboration', 'Inbäddat samarbete', 'COMPONENT_SDK.md'),
        ('Mobile, offline and calling', 'Mobil, offline och samtal', 'NATIVE_CLIENTS.md'),
        ('Exports, governance and operation', 'Export, styrning och drift', 'DATA_CONTROLS.md'),
    ]
    labels = {
        'en': ('Documentation', 'Product guide', 'Feature status, code and verification', 'API Reference',
               'React Integration', 'React integration', 'Flows & Comments', 'Flows Overview', 'Creating Posts', 'Comments System'),
        'sv': ('Dokumentation', 'Produktguide', 'Funktionsstatus, kod och verifiering', 'API-referens',
               'React-integrering', 'React-integration', 'Flöden och kommentarer', 'Flödesöversikt', 'Skapa inlägg', 'Kommentarer'),
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    for locale in ('en', 'sv'):
        docs_label, guide_label, status_label, api_label, react_label, react_heading, flow_section, flow_label, post_label, comment_label = labels[locale]
        for width in (1440, 390, 320):
            page.set_viewport_size({'width': width, 'height': 950})
            page.goto(base, wait_until='networkidle')
            page.locator('.fc-language select').select_option(locale)
            assert page.evaluate('document.documentElement.lang') == locale
            page.get_by_role('button', name=docs_label, exact=True).first.click()
            nav, main = page.locator('.fc-docs-navigation'), page.locator('.fc-docs-content')
            nav.get_by_role('button', name=guide_label, exact=True).click()
            expect(main.get_by_role('heading', name=guide_label, exact=True)).to_be_visible()
            expect(nav.get_by_role('button', name=guide_label, exact=True)).to_have_attribute('aria-current', 'page')
            guide = main.locator('[data-product-guide]')
            expect(guide.locator('dl > div')).to_have_count(11)
            expect(guide.get_by_role('link')).to_have_count(14)
            for english, swedish, document in topics:
                link = guide.get_by_role('link', name=swedish if locale == 'sv' else english, exact=True)
                expect(link).to_have_attribute('href', source+document)
                assert (ROOT/'docs'/document).is_file(), 'Missing linked product contract: '+document
            expect(guide.get_by_role('link', name=status_label, exact=True)).to_have_attribute('href', source+'FEATURE_AUDIT.md')
            assert (ROOT/'docs/FEATURE_AUDIT.md').is_file(), 'Missing linked feature status document'
            installation_label = 'Local and network installation' if locale == 'en' else 'Lokal installation och nätverk'
            expect(guide.get_by_role('link', name=installation_label, exact=True)).to_have_attribute('href', source+'SELF_HOSTING.md')
            assert (ROOT/'docs/SELF_HOSTING.md').is_file(), 'Missing linked self-hosting document'
            expect(guide.get_by_role('link', name='The same product, with example data' if locale == 'en' else 'Samma produkt, med exempeldata', exact=True)).to_have_attribute('href', source+'PRODUCT_DEMO.md')
            for link in guide.get_by_role('link').all():
                expect(link).to_have_attribute('target', '_blank')
                assert {'noopener', 'noreferrer'} <= set((link.get_attribute('rel') or '').split())
            if locale == 'en':
                expect(guide).to_contain_text('Run FreeCollab on your own computer or a server in your private network.')
                expect(guide).to_contain_text('Accounts belong to that installation; no central FreeCollab cloud account is required.')
                expect(guide).to_contain_text('The demo runs the same app and API with temporary example accounts.')
                expect(guide).to_contain_text('Its latest changes are not yet a published release.')
                expect(guide).to_contain_text('Customer SSO and content access have separate configuration and consent.')
                expect(guide).to_contain_text('npm publication is a separate release step.')
                expect(guide).to_contain_text('Protocol tests do not establish live customer-provider acceptance')
            else:
                expect(guide).to_contain_text('Kör FreeCollab på en egen dator eller server i ert privata nätverk.')
                expect(guide).to_contain_text('Konton hör till den installationen; inget centralt FreeCollab-molnkonto krävs.')
                expect(guide).to_contain_text('Demon kör samma app och API med tillfälliga exempelkonton.')
                expect(guide).to_contain_text('Dess senaste ändringar ingår ännu inte i en publicerad version.')
                expect(guide).to_contain_text('Kundens SSO och innehållsåtkomst har separata inställningar och samtycken.')
                expect(guide).to_contain_text('publicering på npm är ett separat versionssteg.')
                expect(guide).to_contain_text('Protokolltester bekräftar inte fungerande kundanslutningar')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), f'{locale} product guide overflow at {width}'
            assert guide.get_by_role('link').evaluate_all('(links)=>links.every(e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1})'), f'{locale} product guide link overflow at {width}'
            page.evaluate('document.fonts.ready')
            main.get_by_role('heading', name=guide_label, exact=True).scroll_into_view_if_needed()
            page.screenshot(path=str(report.with_name(report.stem+f'.product-guide-{locale}-{width}.png')), full_page=True)

            nav.get_by_role('button', name=api_label, exact=True).click()
            nav.get_by_role('button', name=react_label, exact=True).click()
            expect(main.get_by_role('heading', name=react_heading, exact=True)).to_be_visible()
            code = main.locator('pre code')
            expect(code).to_contain_text("import { createClient } from '@freecollab/components'")
            expect(code).to_contain_text("import { Chat } from '@freecollab/components/react'")
            expect(code).to_contain_text('yourBackend.currentCollaborationSession({ scope, reason, signal })')
            expect(code).to_contain_text('client.destroy()')
            expect(main.get_by_role('link').first).to_have_attribute('href', source+'COMPONENT_SDK.md')
            expect(main).to_contain_text('registry publication is a separate authenticated release step.' if locale == 'en'
                else 'publicering i paketregistret är ett separat autentiserat versionssteg.')
            expect(main).to_contain_text('trusted-origin embedded workspace' if locale == 'en' else 'inbäddade arbetsytan från ett betrott ursprung')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), f'{locale} React documentation overflow at {width}'

            nav.get_by_role('button', name='Core Features' if locale == 'en' else 'Grundfunktioner', exact=True).click()
            thread_label = 'Threads & Replies' if locale == 'en' else 'Trådar och svar'
            nav.get_by_role('button', name=thread_label, exact=True).click()
            expect(main.get_by_role('heading', name=thread_label, exact=True)).to_be_visible()
            expect(main.locator('pre code')).to_contain_text('Read the latest page; older replies have an explicit cursor.')
            expect(main.locator('pre code')).to_contain_text('thread.has_more && thread.replies.length')
            expect(main.locator('pre code')).to_contain_text('before: thread.replies[0].id')
            expect(main.locator('pre code')).not_to_contain_text('Get all replies')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), f'{locale} paged thread documentation overflow at {width}'

            nav.get_by_role('button', name=flow_section, exact=True).click()
            nav.get_by_role('button', name=flow_label, exact=True).click()
            expect(main.get_by_role('heading', name=flow_label, exact=True)).to_be_visible()
            expect(main.locator('pre code')).to_contain_text("import { mountFeed } from '@freecollab/components/feed'")
            expect(main.locator('pre code')).to_contain_text('feed.destroy()')
            expect(main.locator('pre code')).to_contain_text('client.destroy()')
            expect(main).to_contain_text('older experimental posts routes are separate and disabled by default.' if locale == 'en'
                else 'äldre experimentella inläggsrutter är separata och avstängda som standard.')
            nav.get_by_role('button', name=post_label, exact=True).click()
            expect(main.get_by_role('heading', name=post_label, exact=True)).to_be_visible()
            expect(main.locator('pre code')).to_contain_text('POST /channels/{channel_id}/activity-feed')
            expect(main.locator('pre code')).to_contain_text('"expected_revision": 0')
            expect(main.locator('pre code')).not_to_contain_text('POST /posts')
            nav.get_by_role('button', name=comment_label, exact=True).click()
            expect(main.get_by_role('heading', name=comment_label, exact=True)).to_be_visible()
            expect(main.locator('pre code')).to_contain_text('POST /channels/{channel_id}/activity-feed/{post_id}/comments')
            expect(main.locator('pre code')).to_contain_text('/channels/{channel_id}/shared-documents/{document_id}/comments')
            expect(main).to_contain_text('there is no unrestricted comment endpoint for arbitrary objects.' if locale == 'en'
                else 'det finns ingen fri kommentarsrutt för godtyckliga objekt.')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), f'{locale} feed/comment documentation overflow at {width}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--report', type=Path, default=ROOT.parent/'.freecollab/proof-results/website-browser.json')
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    base = args.base_url.rstrip('/')
    checks = {}
    with urlopen(base+'/healthz', timeout=10) as response:
        assert response.status == 200
        policy = response.headers.get('Content-Security-Policy', '')
        assert "connect-src 'none'" in policy and "worker-src 'none'" in policy
        assert 'frame-src ' in policy
        assert response.headers.get('Referrer-Policy') == 'no-referrer'
        assert response.headers.get('X-Content-Type-Options') == 'nosniff'
    checks['website_security_headers_and_controlled_demo_frame_policy'] = True
    for path in ('/api/ready', '/socket.io/?EIO=4&transport=polling', '/embed', '/native/authorize', '/service-worker.js'):
        try:
            urlopen(base+path, timeout=10)
        except HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError('Static website exposed a private API/worker route: '+path)
    checks['website_stays_separate_from_product_api_and_workers'] = True
    seed = {'freecollab_token':'website-existing-token-fixture','freecollab_user_id':'website-existing-user-fixture',
            'freecollab_public_key':'website-existing-public-key-fixture','freecollab_encrypted_private_key':'website-existing-key-fixture'}
    errors, prohibited, frames = [], [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), headless=True, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width':1440,'height':1000}, reduced_motion='reduce', locale='en-US')
        context.add_init_script("""if(window===window.top&&!sessionStorage.getItem('website-proof-seeded')){for(const[k,v]of Object.entries("""+json.dumps(seed)+"""))localStorage.setItem(k,v);sessionStorage.setItem('website-proof-seeded','yes')}""")
        def route_handler(route):
            request = route.request
            if request.is_navigation_request() and request.frame.parent_frame is not None:
                frames.append(request.url)
                route.abort()
                return
            uri = urlsplit(request.url)
            if uri.netloc != urlsplit(base).netloc or re.match(r'^/(api|socket\.io|embed|native)(/|$)',uri.path):
                prohibited.append(request.url);route.abort();return
            route.continue_()
        context.route('**/*', route_handler)
        page = context.new_page()
        page.on('pageerror', lambda error:errors.append(str(error)))
        page.goto(base, wait_until='networkidle')
        page.locator('.fc-language select').select_option('en')
        expect(page.get_by_role('heading', name='Good work. Your terms.')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        checks['actual_landing_and_personal_language'] = True
        for width in (1440,390,320):
            page.set_viewport_size({'width':width,'height':950})
            page.goto(base+'/#demo', wait_until='networkidle')
            frame = page.locator('iframe[title="FreeCollab product demo"]')
            expect(frame).to_be_visible()
            target = urlsplit(frame.get_attribute('src'))
            assert target.scheme in {'http','https'} and target.path == '/product-demo/'
            assert 'section=chat' in target.query and 'locale=en' in target.query
            assert page.locator('.fc-demo-desk,.fc-demo-showroom,[data-demo-assist]').count() == 0
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            expect(page.get_by_role('link',name='Open demo in a new tab',exact=True)).to_have_attribute('href',frame.get_attribute('src'))
            page.get_by_role('button',name='Back to website',exact=True).click()
            expect(page.locator('.fc-public')).to_be_visible()
        checks['demo_is_real_product_frame_handoff_not_an_alternate_feature_ui'] = True
        page.goto(base+'/#demo=documents',wait_until='networkidle')
        assert 'section=documents' in page.locator('iframe').get_attribute('src')
        page.goto(base+'/#demo=connections&provider=microsoft',wait_until='networkidle')
        assert 'provider=microsoft' in page.locator('iframe').get_attribute('src')
        page.goto(base+'/#demo=connections&provider=google',wait_until='networkidle')
        assert 'provider=google' in page.locator('iframe').get_attribute('src')
        checks['product_document_and_both_customer_connection_destinations'] = True
        product_documentation(page,base,args.report)
        checks['current_product_guides_en_sv_14_links_sdk_feed_threads_and_mobile_layout'] = True
        assert page.evaluate('(seed)=>Object.entries(seed).every(([k,v])=>localStorage.getItem(k)===v)', seed)
        assert page.evaluate('navigator.serviceWorker.getRegistrations().then(r=>r.length)') == 0
        assert not errors and not prohibited, {'errors':errors,'prohibited':prohibited}
        assert frames, 'The configured demo origin was not permitted by the website frame policy'
        checks['existing_credentials_preserved_no_website_api_or_provider_requests'] = True
        browser.close()
    source = ['frontend/src/website-entry.tsx','frontend/src/components/Demo.tsx','frontend/src/lib/product-demo-url.ts',
              'frontend/src/lib/demo-showroom.ts','frontend/src/components/Landing.tsx','frontend/src/components/Docs.tsx',
              'frontend/src/components/ProductGuide.tsx','frontend/src/styles/product-demo-frame.css','frontend/src/styles/public.css',
              'frontend/src/i18n/index.tsx','frontend/src/i18n/core.ts','frontend/src/lib/api.ts','frontend/vite.website.config.ts',
              'frontend/website/nginx.conf.template','frontend/website/prepare-demo-frame.mjs','frontend/tests/website-browser.py']
    source = sorted(set(source+[str(path.relative_to(ROOT)) for path in (ROOT/'frontend/src/i18n').glob('*.sv.ts')]+['frontend/src/i18n/sv.ts']))
    args.report.write_text(json.dumps({'checks':checks,'groups':len(checks),'source_sha256':{
        name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in source},'limits':[
        'Website handoff to the real product iframe is inspected and stopped before network; actual app/API flows have the separate product-demo-browser proof.',
        'No public-origin browser or live external-provider acceptance is claimed by this owned static-server run.',
        'The old separate browser-only feature simulation and its dead-code tests were replaced, not counted as current product coverage.']},indent=2)+'\n')
    print(f'PASS: {len(checks)} actual website/handoff/documentation groups')

if __name__ == '__main__':
    main()
