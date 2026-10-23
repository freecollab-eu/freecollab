#!/usr/bin/env python3
"""Actual Chat threads/presence proof in disposable loopback services only."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from ui_navigation import open_chat_tool, reveal_message_actions

ROOT = Path(__file__).resolve().parents[2]
PYTHON = os.getenv('BACKEND_PYTHON') or sys.executable
REPORT = Path(os.getenv('COMMUNICATION_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/feature-audit-20261023/communication-browser.json')))
PASSWORD = 'Private-communication-proof!2026'
SOURCES = ('frontend/src/components/Chat.tsx', 'frontend/src/components/PresenceIndicator.tsx', 'frontend/src/lib/api.ts', 'frontend/src/lib/socket.ts', 'frontend/src/lib/thread-history.ts', 'frontend/src/lib/secure/index.ts', 'frontend/src/styles/thread-history.css', 'frontend/src/i18n/presence.sv.ts', 'frontend/src/i18n/thread-history.sv.ts', 'backend/app/api/messages.py', 'backend/app/api/threads.py', 'backend/app/api/reactions.py', 'backend/app/api/presence.py', 'backend/app/realtime/socket.py', 'backend/app/services/shared_runtime.py', 'frontend/src/components/WorkHub.tsx', 'frontend/src/components/SharedDocumentsPanel.tsx', 'frontend/src/components/SharedDocumentOrganization.tsx', 'frontend/src/lib/document-organization-api.ts', 'frontend/src/lib/channel-metadata.ts', 'frontend/tests/communication-browser.py')
spec = importlib.util.spec_from_file_location('communication_proof_helpers', Path(__file__).with_name('remote-sync-browser.py'))
helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(helpers)


def main():
    results, processes, streams = {}, [], []
    stage = 'private startup'
    with tempfile.TemporaryDirectory(prefix='freecollab-communication-browser-') as directory:
        private = Path(directory)
        hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
        snapshot = private / 'source'; snapshot.mkdir()
        shutil.copytree(ROOT / 'frontend', snapshot / 'frontend', ignore=shutil.ignore_patterns('node_modules', 'dist', '.vite', '.env*'))
        (snapshot / 'frontend/node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
        helpers.ROOT = snapshot
        api_port, app_port = helpers.port(), helpers.port()
        base, app = 'http://127.0.0.1:' + str(api_port), 'http://127.0.0.1:' + str(app_port)
        environment = os.environ.copy()
        environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(private / 'proof.db'), STORAGE_PATH=str(private / 'files'), JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), ACCOUNT_SECURITY_KEY_FILE=str(private / 'account.key'), MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof': base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof', MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'message-keys.json'), MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled', PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]), OIDC_ENABLED='false', OTRUST_ENABLED='false', WEBHOOKS_ENABLED='false', SYNC_ENABLED='false', COPILOT_ENABLED='false', PUSH_ENABLED='false', NATIVE_PUSH_ENABLED='false', NATIVE_CALLS_ENABLED='false', VITE_API_URL='/api', PYTHONPATH=str(ROOT / 'backend'))
        launcher = private / 'api.py'
        launcher.write_text('import uvicorn\nfrom app.main import socket_app\nuvicorn.run(socket_app,host="127.0.0.1",port=' + str(api_port) + ',proxy_headers=False,access_log=False)\n')
        try:
            for command, filename, cwd in [([PYTHON, str(launcher)], 'api.log', ROOT / 'backend'), (['node', str(helpers.vite_launcher(private, base, app_port))], 'vite.log', ROOT / 'frontend')]:
                stream = (private / filename).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, start_new_session=True))
            helpers.ready(base + '/ready', processes); helpers.ready(app + '/', processes)
            email = 'communication-' + secrets.token_hex(5) + '@example.com'
            owner = helpers.api(base, 'POST', '/auth/register', data={'name': 'Private communication owner', 'email': email, 'password': PASSWORD})
            workspace = helpers.api(base, 'GET', '/workspaces', owner)[0]
            channel = helpers.api(base, 'POST', '/channels', owner, {'name': 'Thread studio', 'workspace_id': workspace['id']})
            root = helpers.api(base, 'POST', '/messages/channels/' + channel['id'], owner, {'content': 'Visible original thread root'})
            replies = [helpers.api(base, 'POST', '/threads/messages/' + root['id'] + '/reply', owner, {'content': 'Historical reply ' + str(n).zfill(3) + ' · 🧭'}) for n in range(105)]
            other = helpers.api(base, 'POST', '/messages/channels/' + channel['id'], owner, {'content': 'A separate thread root'})
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), args=['--no-sandbox'])
                context = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='en-US', reduced_motion='reduce')
                page = context.new_page(); page.set_default_timeout(45000); errors, urls, requests = [], [], []
                page.on('pageerror', lambda failure: errors.append(str(failure)[:1000]))
                page.on('request', lambda request: urls.append(request.url))
                page.on('request', lambda request: requests.append((request.method, request.url)))
                page.goto(app, wait_until='domcontentloaded', timeout=90000)
                page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
                page.get_by_label('Email', exact=True).fill(email); page.get_by_label('Password', exact=True).fill(PASSWORD)
                page.get_by_role('button', name='Sign In', exact=True).click()
                stage = 'main timeline retains roots despite105 newer thread replies'
                expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
                expect(page.locator('.fc-chat-message-content').filter(has_text=root['content'])).to_be_visible()
                expect(page.locator('article.fc-chat-message')).to_have_count(2)
                assert any('roots_only=true' in url for url in urls)
                results['main_timeline_retains_both_roots_despite105_newer_replies'] = True

                def open_thread(message):
                    article = page.locator('article.fc-chat-message').filter(has=page.locator('.fc-chat-message-content').filter(has_text=message['content']))
                    reveal_message_actions(article); article.get_by_role('button', name='Reply in thread', exact=True).click()
                    dialog = page.get_by_role('dialog', name='Thread', exact=True)
                    expect(dialog.locator('.fc-chat-thread-replies')).to_have_attribute('aria-busy', 'false')
                    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                    return dialog

                stage = 'actual earlier reply pages preserve typed draft and reading anchor'
                dialog = open_thread(root)
                expect(dialog.locator('[data-thread-reply]')).to_have_count(50)
                draft = 'Keep my unsent thread reply 🧭'; dialog.get_by_role('textbox', name='Reply in thread', exact=True).fill(draft)
                scroller = dialog.locator('.fc-chat-thread-replies'); scroller.evaluate('element => element.scrollTop = 0')
                anchor = dialog.locator('[data-thread-reply]').first
                before = anchor.evaluate('element => element.getBoundingClientRect().top')
                dialog.get_by_role('button', name='Load earlier replies', exact=True).click()
                expect(dialog.locator('[data-thread-reply]')).to_have_count(100); expect(scroller).to_have_attribute('aria-busy', 'false')
                page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                expect(dialog.get_by_role('textbox', name='Reply in thread', exact=True)).to_have_value(draft)
                old_anchor = dialog.locator('[data-thread-reply="' + replies[55]['id'] + '"]')
                after = old_anchor.evaluate('element => element.getBoundingClientRect().top')
                assert abs(after - before) < 4, 'Older-page reading anchor moved: ' + json.dumps({'before': before, 'after': after, 'scrollTop': scroller.evaluate('element => element.scrollTop')})
                scroller.evaluate('element => element.scrollTop = 0'); dialog.get_by_role('button', name='Load earlier replies', exact=True).click()
                expect(dialog.locator('[data-thread-reply]')).to_have_count(105)
                expect(dialog.get_by_role('button', name='Load earlier replies', exact=True)).to_have_count(0)
                expect(dialog.get_by_role('textbox', name='Reply in thread', exact=True)).to_have_value(draft)
                results['actual105_reply_pages_keep_exact_draft_and_scroll_anchor'] = True
                dialog.get_by_role('button', name='Close thread', exact=True).click()

                stage = 'different thread drafts remain isolated and return intact'
                dialog = open_thread(other); expect(dialog.get_by_role('textbox', name='Reply in thread', exact=True)).to_have_value('')
                dialog.get_by_role('textbox', name='Reply in thread', exact=True).fill('Other unsent draft')
                dialog.get_by_role('button', name='Close thread', exact=True).click()
                dialog = open_thread(root); expect(dialog.get_by_role('textbox', name='Reply in thread', exact=True)).to_have_value(draft)
                for source, action in [(root, dialog.locator('.fc-chat-thread-parent').get_by_role('button', name='Save in document stack', exact=True)), (replies[-1], dialog.locator('[data-thread-reply="' + replies[-1]['id'] + '"]').get_by_role('button', name='Save in document stack', exact=True))]:
                    action.click(); expect(page.get_by_role('dialog', name='Thread', exact=True)).to_have_count(0)
                    work = page.get_by_role('dialog', name='Workspace tools', exact=True)
                    expect(work.get_by_role('tab', name='Documents', exact=True)).to_have_attribute('aria-selected', 'true')
                    review = work.get_by_role('region', name='Save message in a document stack', exact=True)
                    expect(review.locator('blockquote')).to_have_text(source['content'])
                    expect(review.get_by_role('button', name='Save message reference', exact=True)).to_be_disabled()
                    work.get_by_role('button', name='Close workspace tools', exact=True).click()
                    dialog = open_thread(root); expect(dialog.get_by_role('textbox', name='Reply in thread', exact=True)).to_have_value(draft)
                dialog.get_by_role('button', name='Close thread', exact=True).click()
                results['thread_switch_and_exact_parent_reply_stack_review_preserve_unsent_drafts'] = True

                stage = 'late older thread response cannot populate the newly opened thread'
                held = []
                page.route('**/api/threads/messages/' + root['id'] + '?*', lambda route: held.append(route), times=1)
                article = page.locator('article.fc-chat-message').filter(has=page.locator('.fc-chat-message-content').filter(has_text=root['content']))
                reveal_message_actions(article); article.get_by_role('button', name='Reply in thread', exact=True).click()
                dialog = page.get_by_role('dialog', name='Thread', exact=True); expect(dialog.locator('.fc-chat-thread-replies')).to_have_attribute('aria-busy', 'true')
                dialog.get_by_role('button', name='Close thread', exact=True).click(); dialog = open_thread(other)
                assert len(held) == 1
                with page.expect_response(lambda response: '/threads/messages/' + root['id'] + '?' in response.url): held[0].continue_()
                page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                expect(dialog.locator('[data-thread-reply]')).to_have_count(0)
                expect(dialog.get_by_role('textbox', name='Reply in thread', exact=True)).to_have_value('Other unsent draft')
                dialog.get_by_role('button', name='Close thread', exact=True).click()
                results['late_previous_thread_response_is_discarded'] = True

                stage = 'full history still finds an old reply and opens its exact focused source'
                open_chat_tool(page, 'Search all history')
                search = page.get_by_role('dialog', name='Search all history', exact=True)
                search.get_by_role('textbox', name='Search messages', exact=True).fill('Historical reply 000')
                search.locator('button.fc-history-result').filter(has_text=replies[0]['content']).click()
                dialog = page.get_by_role('dialog', name='Thread', exact=True)
                target = dialog.locator('[data-thread-reply="' + replies[0]['id'] + '"]')
                expect(target).to_be_visible(); expect(target).to_be_focused(); expect(dialog.locator('[data-thread-reply]')).to_have_count(105)
                REPORT.parent.mkdir(parents=True, exist_ok=True); page.screenshot(path=str(REPORT.parent / 'communication-thread-desktop.png'))
                page.set_viewport_size({'width': 320, 'height': 740}); assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                expect(dialog.get_by_role('textbox', name='Reply in thread', exact=True)).to_be_visible()
                page.screenshot(path=str(REPORT.parent / 'communication-thread-mobile.png'))
                dialog.get_by_role('button', name='Close thread', exact=True).click(); page.set_viewport_size({'width': 1440, 'height': 1000})
                results['full_history_old_reply_opens_actual_focused_source_and320px_thread'] = True

                stage = 'reachable custom status saves via real SocketIO and reloads from authorized REST'
                open_chat_tool(page, 'Set your status')
                status = page.get_by_role('dialog', name='Set your status', exact=True)
                text = 'Reviewing this thread 🧭'; status.get_by_label('Set a custom status...', exact=True).fill(text)
                status.get_by_role('button', name='Do not disturb', exact=False).click()
                expect(status).to_have_count(0)
                saved = helpers.api(base, 'GET', '/presence/users/' + owner['user_id'], owner)
                assert saved['status'] == 'busy' and saved['custom_status'] == text
                open_chat_tool(page, 'Set your status'); status = page.get_by_role('dialog', name='Set your status', exact=True)
                expect(status.get_by_label('Set a custom status...', exact=True)).to_have_value(text)
                status.get_by_label('Set a custom status...', exact=True).fill(''); status.get_by_role('button', name='Save Status', exact=True).click()
                expect(status).to_have_count(0)
                assert 'custom_status' not in helpers.api(base, 'GET', '/presence/users/' + owner['user_id'], owner)
                results['reachable_status_real_socket_ack_rest_roundtrip_and_explicit_clear'] = True
                with sqlite3.connect(private / 'proof.db') as database:
                    assert database.execute('SELECT count(*) FROM message WHERE channel_id=?', (channel['id'],)).fetchone()[0] == 107
                    assert database.execute('SELECT count(*) FROM shareddocumentstackreference').fetchone()[0] == 0
                assert not any(method != 'GET' and '/shared-document-organization' in url for method, url in requests), 'Opening a stack review must not save references'
                assert not errors and all(urlsplit(url).hostname in {'127.0.0.1', 'localhost'} for url in urls if urlsplit(url).scheme in {'http', 'https', 'ws', 'wss'})
                results['zero_unexpected_posts_external_requests_or_browser_errors'] = True
                context.close(); browser.close()
            assert hashes == {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}, 'Communication sources changed during proof'
            REPORT.parent.mkdir(parents=True, exist_ok=True)
            REPORT.write_text(json.dumps({'ok': True, 'results': results, 'source_hashes': hashes}, indent=2) + '\n')
            print(json.dumps({'ok': True, 'checks': list(results), 'report': str(REPORT)}))
        except Exception as failure:
            print(json.dumps({'ok': False, 'stage': stage, 'error_type': type(failure).__name__, 'error': str(failure).replace(PASSWORD, '[redacted]')[:4000]}))
            raise
        finally:
            helpers.stop(processes)
            for stream in streams: stream.close()


if __name__ == '__main__': main()
