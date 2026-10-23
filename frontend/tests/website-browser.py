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
            expect(guide.get_by_role('link')).to_have_count(13)
            for english, swedish, document in topics:
                link = guide.get_by_role('link', name=swedish if locale == 'sv' else english, exact=True)
                expect(link).to_have_attribute('href', source+document)
                assert (ROOT/'docs'/document).is_file(), 'Missing linked product contract: '+document
            expect(guide.get_by_role('link', name=status_label, exact=True)).to_have_attribute('href', source+'FEATURE_AUDIT.md')
            assert (ROOT/'docs/FEATURE_AUDIT.md').is_file(), 'Missing linked feature status document'
            installation_label = 'Local and network installation' if locale == 'en' else 'Lokal installation och nätverk'
            expect(guide.get_by_role('link', name=installation_label, exact=True)).to_have_attribute('href', source+'SELF_HOSTING.md')
            assert (ROOT/'docs/SELF_HOSTING.md').is_file(), 'Missing linked self-hosting document'
            for link in guide.get_by_role('link').all():
                expect(link).to_have_attribute('target', '_blank')
                assert {'noopener', 'noreferrer'} <= set((link.get_attribute('rel') or '').split())
            if locale == 'en':
                expect(guide).to_contain_text('Run FreeCollab on your own computer or a server in your private network.')
                expect(guide).to_contain_text('Accounts belong to that installation; no central FreeCollab cloud account is required.')
                expect(guide).to_contain_text('The public demo uses local examples and makes no account or AI-provider requests.')
                expect(guide).to_contain_text('Its latest changes are not yet a published release.')
                expect(guide).to_contain_text('Customer SSO and content access have separate configuration and consent.')
                expect(guide).to_contain_text('npm publication is a separate release step.')
                expect(guide).to_contain_text('Protocol tests do not establish live customer-provider acceptance')
            else:
                expect(guide).to_contain_text('Kör FreeCollab på en egen dator eller server i ert privata nätverk.')
                expect(guide).to_contain_text('Konton hör till den installationen; inget centralt FreeCollab-molnkonto krävs.')
                expect(guide).to_contain_text('Den publika demon använder lokala exempel och gör inga konto- eller AI-anrop.')
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
        source=page.locator('.fc-showroom-transcript article').filter(has=page.get_by_text(message,exact=True))
        source.get_by_role('button',name='Make a task from this message',exact=True).click()
        review=page.locator('.fc-demo-task-from-message:visible')
        expect(review.get_by_label('Task title',exact=True)).to_have_value(message)
        expect(review.get_by_role('button',name='Unassigned',exact=True)).to_have_attribute('aria-pressed','true')
        review.get_by_label('Task title',exact=True).fill('Website source-linked task')
        review.get_by_role('button',name='Noor',exact=True).click()
        review.get_by_role('button',name='Create task',exact=True).click()
        created=page.get_by_role('checkbox',name='Website source-linked task',exact=True)
        expect(created).to_have_count(1)
        row=page.locator('[data-demo-task-id]').filter(has=created)
        expect(row).to_contain_text('Noor')
        review.get_by_role('button',name='Back to tasks',exact=True).click();expect(created).to_be_focused()
        row.get_by_role('button',name='View source message',exact=True).click();expect(source).to_be_focused()
        expect(source.get_by_role('button',name='View task for this message',exact=True)).to_be_visible()
        checks['built_message_task_reuses_source_optional_owner_and_returns_to_original_without_copying'] = True
        ai_message='Bring the cobalt bridge samples to the walkthrough.'
        page.get_by_label('Your demo message',exact=True).fill(ai_message)
        page.get_by_role('button',name='Send demo message',exact=True).click()
        ai_source=page.locator('.fc-showroom-transcript article').filter(has=page.get_by_text(ai_message,exact=True))
        draft='   Keep   my draft.   Review   the bridge.  '
        composer=page.get_by_label('Your demo message',exact=True);composer.fill(draft)
        composer_help=page.locator('[data-demo-assist][data-assist-scope="composer"]:visible')
        composer_help.locator('[data-assist-trigger]').click()
        card=composer_help.locator('.fc-inline-assist-card')
        expect(card.get_by_text('Uses selected text; no AI request.',exact=True)).to_be_visible()
        card.get_by_role('button',name='Prepare example',exact=True).click()
        expect(card.locator('[data-assist-preview="current"]')).to_be_visible();expect(composer).to_have_value(draft)
        card.get_by_role('button',name=re.compile(r'^Use tidy draft\b')).click()
        expect(composer).to_have_value('Keep my draft. Review the bridge.')
        page.get_by_role('button',name='Undo last assisted change',exact=True).click();expect(composer).to_have_value(draft)
        card.get_by_role('button',name='Cancel',exact=True).click()
        expect(page.get_by_role('log').get_by_text('Keep my draft. Review the bridge.',exact=True)).to_have_count(0)
        message_help=ai_source.locator('[data-demo-assist][data-assist-scope="message"]')
        message_help.locator('[data-assist-trigger]').click();card=message_help.locator('.fc-inline-assist-card')
        card.get_by_text('What is shared?',exact=True).click()
        expect(card.locator('.fc-inline-assist-sharing')).to_contain_text('api.x.ai')
        card.get_by_role('button',name='Prepare a task',exact=True).click()
        card.get_by_role('button',name='Prepare example',exact=True).click()
        output=card.locator('[data-assist-preview="current"]')
        expect(output).to_contain_text(ai_message);expect(output).not_to_contain_text(draft)
        expect(output.locator('.fc-inline-assist-citations button')).to_have_count(1)
        expect(page.get_by_role('checkbox',name=ai_message,exact=True)).to_have_count(0)
        output.get_by_role('button',name=re.compile(r'^Review task from this message\b')).click()
        expect(review.get_by_label('Task title',exact=True)).to_have_value(ai_message)
        expect(review.get_by_role('button',name='Unassigned',exact=True)).to_have_attribute('aria-pressed','true')
        expect(page.get_by_role('checkbox',name=ai_message,exact=True)).to_have_count(0)
        review.get_by_role('button',name='Create task',exact=True).click()
        expect(page.get_by_role('checkbox',name=ai_message,exact=True)).to_have_count(1)
        review.get_by_role('button',name='View original message',exact=True).click();expect(ai_source).to_be_focused()
        if message_help.locator('[data-assist-trigger]').get_attribute('aria-expanded')!='true':message_help.locator('[data-assist-trigger]').click()
        card.get_by_role('button',name='Prepare example',exact=True).click()
        card.get_by_role('button',name=re.compile(r'^Review task from this message\b')).click()
        expect(page.get_by_role('checkbox',name=ai_message,exact=True)).to_have_count(1)
        expect(review.get_by_role('button',name='Create task',exact=True)).to_have_count(0)
        review.get_by_role('button',name='View original message',exact=True).click()
        expect(composer).to_have_value(draft)
        checks['built_contextual_ai_draft_edits_require_approval_and_undo_message_task_requires_review_without_duplicates_or_autopost'] = True
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
        page.get_by_role('button',name='Suggested connections',exact=True).click()
        related=page.locator('.fc-demo-connections:visible')
        reference=related.locator('[data-connection-kind="message_stack"]').filter(has=page.get_by_text('I’ve shared the new sketch here. The long table can sit by the open doors.',exact=True))
        reference.get_by_role('button',name='Save reference in Sketches',exact=True).click()
        expect(reference).to_have_count(0)
        pair=related.locator('[data-connection-kind="document_link"]').first
        expect(pair).to_be_visible();pair.get_by_role('button',name=re.compile(r'^Link ')).click()
        expect(related.locator('.fc-demo-connections-saved')).to_be_visible()
        checks['built_content_suggestions_create_only_explicit_local_references_and_links'] = True
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
        expect(page.get_by_role('checkbox', name='Website source-linked task', exact=True)).to_have_count(0)
        expect(page.get_by_role('checkbox', name=ai_message, exact=True)).to_have_count(0)
        page.get_by_role('button', name='Tools', exact=True).click()
        page.get_by_role('tab', name='Documents', exact=True).click()
        assert page.get_by_label('Edit demo document').input_value() != 'Website-only document'
        expect(page.get_by_role('button',name='Choose channel',exact=True)).to_contain_text('courtyard')
        expect(page.locator('.fc-demo-desk:visible').get_by_text('Website-only stack',exact=True)).to_have_count(0)
        expect(page.locator('.fc-demo-desk:visible').get_by_text('Website-only document title',exact=True)).to_have_count(0)
        page.get_by_role('button',name='Suggested connections',exact=True).click()
        related=page.locator('.fc-demo-connections:visible')
        expect(related.locator('.fc-demo-connections-saved')).to_have_count(0)
        expect(related.get_by_role('button',name='Save reference in Sketches',exact=True)).to_be_visible()
        checks['demo_mutations_disappear_on_reload'] = True
        page.get_by_role('button',name='AI settings',exact=True).click()
        settings=page.locator('[data-demo-ai-settings]')
        expect(settings.get_by_role('combobox',name='AI provider',exact=True)).to_have_value('grok')
        expect(settings.get_by_role('textbox',name='AI model',exact=True)).to_have_value('grok-4.20-0309-non-reasoning')
        expect(page.locator('[data-assist-preview]')).to_have_count(0)
        page.get_by_role('button', name='Back to FreeCollab', exact=True).click()
        page.get_by_role('button', name='Documentation', exact=True).first.click()
        expect(page.locator('.fc-docs')).to_be_visible()
        product_documentation(page, base, args.report)
        checks['current_product_guide_en_sv_review_branch_links_react_feed_contracts_and_1440_390_320px_layout'] = True
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
            page.get_by_role('button',name='Föreslagna samband',exact=True).click()
            related=page.locator('.fc-demo-connections:visible')
            expect(related.get_by_role('switch',name='Föreslå samband',exact=True)).to_have_attribute('aria-checked','true')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), f'Swedish suggestions overflow at{width}'
            page.keyboard.press('Escape')
            helper=page.locator('[data-demo-assist][data-assist-scope="channel"]:visible')
            helper.locator('[data-assist-trigger]').click()
            card=helper.locator('.fc-inline-assist-card')
            expect(card.get_by_text('Använder vald text; inget AI-anrop.',exact=True)).to_be_visible()
            card.get_by_role('button',name='Förbered exempel',exact=True).click()
            expect(card.locator('[data-assist-preview="current"]')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), f'Swedish contextual AI overflow at{width}'
            card.get_by_role('button',name='Avbryt',exact=True).click()
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
              'frontend/src/components/Docs.tsx', 'frontend/src/components/ProductGuide.tsx', 'frontend/src/components/Brand.tsx',
              'frontend/src/styles/panels.css', 'frontend/src/index.css',
              'frontend/src/i18n/index.tsx', 'frontend/src/i18n/core.ts',
              'frontend/src/components/DemoChannelSwitcher.tsx', 'frontend/src/components/DemoDocumentDesk.tsx',
              'frontend/src/components/DemoConnections.tsx', 'frontend/src/lib/demo-connections.ts',
              'frontend/src/components/DemoMessageActions.tsx', 'frontend/src/components/DemoTaskFromMessage.tsx',
              'frontend/src/lib/demo-ai.ts',
              'frontend/src/components/DemoInlineAssist.tsx', 'frontend/src/lib/demo-assist.ts',
              'frontend/src/lib/demo-showroom.ts', 'frontend/src/lib/useModalFocus.ts',
              'frontend/src/styles/demo-showroom.css', 'frontend/src/styles/demo-channel-switcher.css',
              'frontend/src/styles/demo-document-desk.css', 'frontend/src/styles/demo-connections.css',
              'frontend/src/styles/demo-message-actions.css',
              'frontend/src/i18n/demo-ai.sv.ts',
              'frontend/src/styles/demo-inline-assist.css', 'frontend/src/i18n/demo-assist.sv.ts',
              'frontend/src/i18n/demo-showroom.sv.ts', 'frontend/tests/website-browser.py']
    source = sorted(set(source + [str(path.relative_to(ROOT)) for path in (ROOT/'frontend/src/i18n').glob('*.sv.ts')]
                        + ['frontend/src/i18n/sv.ts']))
    args.report.write_text(json.dumps({'checks': checks, 'groups': len(checks), 'source_sha256': {
        name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in source},
        'limits': ['Read-only acceptance against an owned static server; no Railway, DNS or public TLS deployment is claimed.',
                   'Demo interactions are local sample data; no real Microsoft/Google connection or collaboration API was exercised.',
                   'Documentation links and local target existence were inspected; external linked pages were not fetched or authenticated.']}, indent=2)+'\n')
    print(f'PASS: {len(checks)} actual static website/browser groups; no account/provider calls')


if __name__ == '__main__':
    main()
