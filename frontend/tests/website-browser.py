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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--report', type=Path, default=ROOT.parent/'.freecollab/proof-results/website-browser.json')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        parser.error('Use an exact owned website origin.')
    if parsed.scheme == 'http' and parsed.hostname not in {'localhost', '127.0.0.1', '::1'}:
        parser.error('HTTP acceptance is limited to an owned loopback fixture.')
    with urlopen(base+'/healthz', timeout=10) as response:
        assert response.status == 200 and response.read() == b'ready\n'
    with urlopen(base+'/', timeout=10) as response:
        assert "connect-src 'none'" in response.headers.get('Content-Security-Policy', '')
        assert "worker-src 'none'" in response.headers.get('Content-Security-Policy', '')
        assert response.headers.get('Referrer-Policy') == 'no-referrer'
        assert response.headers.get('X-Content-Type-Options') == 'nosniff'
    checks = {'actual_static_server_headers_and_readiness': True}
    for path in ('/api/ready', '/socket.io/?EIO=4&transport=polling', '/embed', '/native/authorize', '/service-worker.js'):
        try:
            urlopen(base+path, timeout=10)
        except HTTPError as failure:
            assert failure.code == 404, (path, failure.code)
        else:
            raise AssertionError('Static website exposed an account/worker route: '+path)
    checks['static_origin_refuses_account_routes_and_worker'] = True
    errors, prohibited, calls = [], [], []
    seed = {'freecollab_token': 'website-existing-token-fixture', 'freecollab_user_id': 'website-existing-account-fixture',
            'freecollab_public_key': 'website-existing-public-key-fixture', 'freecollab_encrypted_private_key': 'website-existing-opaque-key-fixture'}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),
                                             headless=True, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce', locale='en-US')
        context.add_init_script(r'''
          if (!sessionStorage.getItem('website-proof-seeded')) {
            for (const [key,value] of Object.entries('''+json.dumps(seed)+r''')) localStorage.setItem(key,value);
            sessionStorage.setItem('website-proof-seeded','yes');
          }
          window.websiteProofCalls=[];
          const originalFetch=window.fetch;
          window.fetch=function(input,...rest) {
            const url=new URL(typeof input==='string'?input:input.url,location.href);
            window.websiteProofCalls.push(url.pathname);
            if (/^\/(api|socket\.io|embed|native)(\/|$)/.test(url.pathname)) throw new Error('Website attempted an account/provider request');
            return originalFetch.call(this,input,...rest);
          };
        ''')
        def route(request):
            url = urlsplit(request.request.url)
            if url.scheme in {'data', 'blob'}:
                request.continue_(); return
            if (url.scheme, url.netloc) != (parsed.scheme, parsed.netloc) or url.path.startswith(('/api', '/socket.io', '/embed', '/native')):
                prohibited.append(url.path); request.abort(); return
            request.continue_()
        context.route('**/*', route)
        page = context.new_page(); page.set_default_timeout(20000)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base, wait_until='networkidle')
        expect(page.locator('.fc-public')).to_be_visible()
        expect(page.locator('.fc-public-nav-cta')).to_have_text('Explore demo')
        page.locator('.fc-public-nav-cta').click()
        expect(page.get_by_role('heading', name='Shall we open up to the courtyard?', exact=True)).to_be_visible()
        expect(page.get_by_text('Sample workspace · fictional people · changes stay in this tab', exact=True)).to_be_visible()
        message = 'A local website proof <img src="https://invalid.example/tracker">'
        page.get_by_label('Your demo message', exact=True).fill(message)
        page.get_by_role('button', name='Send demo message', exact=True).click()
        expect(page.get_by_role('log').get_by_text(message, exact=True)).to_be_visible()
        assert page.get_by_role('log').locator('img').count() == 0
        page.get_by_role('button', name='Tools', exact=True).click()
        page.get_by_role('tab', name='Projects', exact=True).click()
        page.get_by_label('New demo task', exact=True).fill('Website-only task')
        page.get_by_role('button', name='Add task', exact=True).click()
        expect(page.get_by_role('checkbox', name='Website-only task', exact=True)).to_be_visible()
        page.get_by_role('button', name='Tools', exact=True).click()
        page.get_by_role('tab', name='Documents', exact=True).click()
        page.get_by_label('Edit demo document', exact=True).fill('Website-only document')
        channel_trigger=page.get_by_role('button',name='Choose channel',exact=True)
        channel_trigger.click()
        picker=page.get_by_role('dialog',name='Channels',exact=True)
        picker.get_by_role('button',name=re.compile(r'^Manage channel')).click()
        manage=page.get_by_role('dialog',name='Manage channel',exact=True)
        manage.get_by_label('Channel name',exact=True).fill('Website-only courtyard')
        manage.get_by_role('button',name='Save channel',exact=True).click()
        expect(channel_trigger).to_be_focused();expect(channel_trigger).to_contain_text('Website-only courtyard')
        desk=page.locator('.fc-demo-desk:visible')
        desk.get_by_role('button',name='New',exact=True).click()
        desk.get_by_role('button',name='Stack',exact=True).click()
        desk.get_by_label('Stack name',exact=True).fill('Website-only stack')
        desk.get_by_role('button',name='Create stack',exact=True).click()
        stack=desk.get_by_role('region',name='Website-only stack',exact=True)
        stack.get_by_role('button',name='Add document',exact=True).click()
        desk.get_by_label('Document title',exact=True).fill('Website-only document title')
        desk.get_by_role('button',name='Create document',exact=True).click()
        page.get_by_label('Edit demo document',exact=True).fill('Website-only created document')
        expect(page.get_by_role('region',name='Document editor',exact=True)).to_contain_text('Website-only document title')
        checks['built_demo_channel_management_and_document_stack_creation_are_local'] = True
        page.get_by_role('button', name='Tools', exact=True).click()
        page.get_by_role('tab', name='Appearance', exact=True).click()
        page.get_by_label('Demo typeface', exact=True).select_option('manrope')
        page.evaluate('document.fonts.ready')
        assert 'Manrope' in page.locator('.fc-showroom').evaluate('(e)=>getComputedStyle(e).fontFamily')
        assert page.evaluate('document.fonts.check(\'14px "Manrope Variable"\')')
        checks['actual_local_demo_messages_tasks_documents_and_customer_typeface'] = True
        page.reload(wait_until='networkidle')
        expect(page.get_by_role('log').get_by_text(message, exact=True)).to_have_count(0)
        page.get_by_role('button', name='Tools', exact=True).click()
        page.get_by_role('tab', name='Projects', exact=True).click()
        expect(page.get_by_role('checkbox', name='Website-only task', exact=True)).to_have_count(0)
        page.get_by_role('button', name='Tools', exact=True).click()
        page.get_by_role('tab', name='Documents', exact=True).click()
        assert page.get_by_label('Edit demo document').input_value() != 'Website-only document'
        expect(page.get_by_role('button',name='Choose channel',exact=True)).to_contain_text('courtyard')
        expect(page.locator('.fc-demo-desk:visible').get_by_text('Website-only stack',exact=True)).to_have_count(0)
        expect(page.locator('.fc-demo-desk:visible').get_by_text('Website-only document title',exact=True)).to_have_count(0)
        checks['demo_mutations_disappear_on_reload'] = True
        page.get_by_role('button', name='Back to FreeCollab', exact=True).click()
        page.get_by_role('button', name='Documentation', exact=True).first.click()
        expect(page.locator('.fc-docs')).to_be_visible()
        page.goto(base, wait_until='networkidle')
        page.locator('.fc-language select').select_option('sv')
        assert page.evaluate('document.documentElement.lang') == 'sv'
        for width in (390, 320):
            page.set_viewport_size({'width': width, 'height': 950})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), f'Landing overflow at{width}'
            page.locator('.fc-public-nav-cta').click()
            expect(page.locator('.fc-showroom')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), f'Demo overflow at{width}'
            trigger=page.get_by_role('button',name='Välj kanal',exact=True)
            expect(trigger).to_contain_text('Kanaler')
            trigger.click();picker=page.get_by_role('dialog',name='Kanaler',exact=True)
            expect(picker).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), f'Channel picker overflow at{width}'
            page.keyboard.press('Escape');expect(trigger).to_be_focused()
            page.get_by_role('button', name='Tillbaka till FreeCollab', exact=True).click()
        checks['documentation_swedish_and_390_320px_layout'] = True
        actual = page.evaluate('(keys)=>Object.fromEntries(keys.map(key=>[key,localStorage.getItem(key)]))', list(seed))
        assert actual == seed, 'Static website changed existing account material'
        assert page.evaluate('navigator.serviceWorker.getRegistrations().then(r=>r.length)') == 0
        calls.extend(page.evaluate('window.websiteProofCalls'))
        assert not any(path.startswith(('/api', '/socket.io', '/embed', '/native')) for path in calls)
        assert not prohibited and not errors, {'prohibited': prohibited, 'errors': errors}
        checks['existing_credentials_unchanged_no_account_provider_requests_or_service_worker'] = True
        args.report.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(args.report.with_suffix('.png')), full_page=True)
        context.close(); browser.close()
    source = ['frontend/src/website-entry.tsx', 'frontend/vite.website.config.ts', 'frontend/website/index.html',
              'frontend/website/Dockerfile', 'frontend/website/nginx.conf.template', 'frontend/website/19-validate-port.sh',
              'frontend/src/components/Landing.tsx', 'frontend/src/components/Demo.tsx',
              'frontend/src/components/DemoChannelSwitcher.tsx', 'frontend/src/components/DemoDocumentDesk.tsx',
              'frontend/src/lib/demo-showroom.ts', 'frontend/src/lib/useModalFocus.ts',
              'frontend/src/styles/demo-showroom.css', 'frontend/src/styles/demo-channel-switcher.css',
              'frontend/src/styles/demo-document-desk.css', 'frontend/tests/website-browser.py']
    args.report.write_text(json.dumps({'checks': checks, 'groups': len(checks), 'source_sha256': {
        name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in source},
        'limits': ['Read-only acceptance against an owned static server; no Railway, DNS or public TLS deployment is claimed.',
                   'Demo interactions are local sample data; no real Microsoft/Google connection or collaboration API was exercised.']}, indent=2)+'\n')
    print(f'PASS: {len(checks)} actual static website/browser groups; no account/provider calls')


if __name__ == '__main__':
    main()
