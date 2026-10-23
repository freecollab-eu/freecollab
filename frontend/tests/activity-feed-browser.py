#!/usr/bin/env python3
"""Actual native UI and Matrix feed posts/comments/polls/reactions proof."""
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import traceback

from ui_navigation import open_conversation_work

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('feed_document_proof_helpers', Path(__file__).with_name('shared-documents-browser.py'))
documents = importlib.util.module_from_spec(spec); spec.loader.exec_module(documents)
helpers = documents.helpers
PASSWORD = 'Private-activity-feed!2026'
REPORT = Path(os.getenv('ACTIVITY_FEED_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/activity-feed-browser.json')))


def enter(page, app, email, workspace, identity):
    page.goto(app, wait_until='domcontentloaded')
    page.evaluate('([key,value])=>localStorage.setItem(key,value)', ['freecollab.workspace:' + identity, workspace])
    page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
    page.get_by_label('Email', exact=True).fill(email); page.get_by_label('Password', exact=True).fill(PASSWORD)
    page.get_by_role('button', name='Sign In', exact=True).click()
    panel = open_conversation_work(page, 'feed')
    expect(panel).to_be_visible()
    expect(panel).to_have_attribute('data-work-kind', 'feed')
    expect(panel.get_by_role('tablist')).to_have_count(0)
    assert not panel.evaluate('element=>!!element.closest("[inert]")||element.getAttribute("aria-modal")==="true"')
    expect(page.locator('.fc-chat-composer textarea')).to_be_enabled()
    expect(panel.get_by_role('heading', level=3, name='Activity feed', exact=True)).to_be_visible()
    return panel


def secure_proof(base, private, workspace, users, pages, results):
    alice, bob = users; left, right = pages
    for page in pages:
        page.set_default_timeout(90000)
        page.evaluate('''async()=>{window.feedApi=(await import('/src/lib/activity-feed-api.ts')).activityFeedApi;
            window.feedSecure=await(await import('/src/lib/secure-client.ts')).getSecureMessaging();}''')
    channel = helpers.api(base, 'POST', '/channels', alice, {'name': 'Encrypted activity proof', 'workspace_id': workspace['id'], 'member_ids': [bob['user_id']]})['id']
    left.evaluate('channel=>feedSecure.enableChannel(channel,"Private activity")', channel)
    right.evaluate('channel=>feedSecure.getMessages(channel)', channel)
    for page, other in [(left, bob), (right, alice)]:
        devices = documents.poll(lambda: page.evaluate('channel=>feedSecure.getDevices(channel)', channel),
            lambda values: any(value['user_id'] == other['user_id'] for value in values), 'Real recipient cryptographic device unavailable')
        for device in devices:
            if device['user_id'] == other['user_id']: page.evaluate('device=>feedSecure.verifyDevice(device,device.fingerprint)', device)
    requests = []
    for page in pages:
        page.on('request', lambda request: requests.append(request.post_data or '') if '/api/channels/' + channel + '/activity-feed' in request.url else None)
    fields = {'title': 'Private encrypted announcement', 'body': 'Private encrypted feed body', 'poll': {
        'id': secrets.token_hex(16), 'nonce': secrets.token_hex(16), 'question': 'Private encrypted question',
        'options': [{'id': secrets.token_hex(16), 'label': 'Private first option'}, {'id': secrets.token_hex(16), 'label': 'Private second option'}],
        'multiple': False, 'closes_at': None}}
    post = left.evaluate('args=>feedApi.savePost(...args)', [channel, fields])
    assert post.get('fields') == fields and not post.get('resolution_error')
    verified = right.evaluate('args=>feedApi.detail(...args)', [channel, post['id']])
    assert verified['post']['fields'] == fields
    comment = right.evaluate('args=>feedApi.saveComment(...args)', [channel, post['id'], {'body': 'Private authenticated comment'}])
    vote = right.evaluate('args=>feedApi.vote(...args)', [channel, post, [fields['poll']['options'][0]['id']]])
    reaction = right.evaluate('args=>feedApi.react(...args)', [channel, post['id'], 'celebrate'])
    verified = left.evaluate('args=>feedApi.detail(...args)', [channel, post['id']])
    assert verified['comments'][0]['fields']['body'] == 'Private authenticated comment'
    assert verified['votes'][0]['created_by'] == bob['user_id'] and verified['votes'][0]['fields']['option_ids'] == [fields['poll']['options'][0]['id']]
    assert verified['reactions'][0]['fields']['emoji'] == 'celebrate'
    native = helpers.api(base, 'GET', '/channels/' + channel + '/activity-feed/' + post['id'], alice)
    for row in [native['post'], *native['comments'], *native['votes'], *native['reactions']]:
        assert row['content_mode'] == 'matrix' and 'fields' not in row and row['matrix_event_id']
        wire = left.evaluate('async([channel,eventId])=>{const event=await feedSecure.getDecryptedEvent(channel,eventId);return{type:event.getWireType(),content:event.getWireContent()}}', [channel, row['matrix_event_id']])
        assert wire['type'] == 'm.room.encrypted'
        assert fields['title'] not in json.dumps(wire) and fields['poll']['question'] not in json.dumps(wire)
    assert all(secret not in '\n'.join(requests) for secret in [fields['title'], fields['body'], fields['poll']['question'], 'Private authenticated comment', fields['poll']['nonce']])
    with sqlite3.connect(private / 'proof.db') as database:
        for table in ['activityfeedrevision', 'activityfeedcommentrevision', 'activityfeedchoicerevision']:
            rows = database.execute('SELECT content_mode,encrypted_payload,matrix_event_id FROM ' + table + ' WHERE content_mode=?', ('matrix',)).fetchall()
            assert rows and all(mode == 'matrix' and payload is None and pointer for mode, payload, pointer in rows)
    # A ciphertext copied from another post is not displayed as this post.
    copied = helpers.api(base, 'POST', '/channels/' + channel + '/activity-feed', alice,
        {'id': 'misbound-private-post', 'matrix_event_id': post['matrix_event_id'], 'poll': post['poll']})
    rejected = right.evaluate('args=>feedApi.detail(...args)', [channel, copied['id']])
    assert rejected['post'].get('resolution_error') and rejected['post'].get('fields') is None
    # Correct author alone cannot make an invalid option count as a vote.
    invalid = right.evaluate('args=>feedSecure.sendEvent(...args)', [channel, 'eu.freecollab.activity.vote', {
        'id': vote['id'], 'kind': 'vote', 'post_id': post['id'], 'revision': 2, 'archived': False,
        'poll_id': post['poll']['id'], 'fields': {'option_ids': ['f' * 32]}}])
    helpers.api(base, 'POST', '/channels/' + channel + '/activity-feed/' + post['id'] + '/vote', bob,
        {'expected_revision': 1, 'poll_id': post['poll']['id'], 'matrix_event_id': invalid['event_id']})
    rejected = left.evaluate('args=>feedApi.detail(...args)', [channel, post['id']])
    assert rejected['votes'][0].get('resolution_error') and rejected['votes'][0].get('fields') is None
    results['actual_matrix_two_trusted_devices_posts_comments_polls_reactions_opaque_api_db_wire'] = True
    results['actual_matrix_wrong_context_and_invalid_authenticated_poll_response_excluded'] = True
    print('PASS real Matrix encrypted feed, authenticated comments/reactions/votes, opaque DB/API/wire and adversarial context/choice rejection', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--secure', action='store_true'); options = parser.parse_args()
    stage, results, processes, streams = 'private services startup', {}, [], []
    with tempfile.TemporaryDirectory(prefix='freecollab-activity-feed-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = helpers.port(), helpers.port(); base, app = 'http://127.0.0.1:' + str(api_port), 'http://127.0.0.1:' + str(app_port)
            environment = os.environ.copy(); environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(private / 'proof.db'), STORAGE_PATH=str(private / 'files'),
                JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof': base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',
                MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'message-keys.json'), ACCOUNT_SECURITY_KEY_FILE=str(private / 'account.key'),
                MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled', PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]), OIDC_ENABLED='false',
                WEBHOOKS_ENABLED='false', OTRUST_ENABLED='false', SYNC_ENABLED='false', PUSH_ENABLED='false', ACTIVITY_FEEDS_ENABLED='true', PYTHONPATH=str(ROOT / 'backend'))
            environment['NO_PROXY'] = ','.join(filter(None, [environment.get('NO_PROXY', ''), 'localhost', '127.0.0.1', '::1']))
            if options.secure: environment.update(documents.fixture_matrix_environment())
            python = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)
            server = private / 'api.py'; server.write_text('import uvicorn\nfrom app.main import socket_app\nuvicorn.run(socket_app,host="127.0.0.1",port=' + str(api_port) + ',proxy_headers=False,access_log=False)\n')
            frontend = private / 'frontend'; shutil.copytree(ROOT / 'frontend', frontend, ignore=shutil.ignore_patterns('node_modules', 'dist', 'dist-widget', '__pycache__', '.git'))
            (frontend / 'node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
            vite = helpers.vite_launcher(private, base, app_port); source = vite.read_text().replace('root:' + json.dumps(str(ROOT / 'frontend')), 'root:' + json.dumps(str(frontend)))
            source = source.replace(json.dumps(str(private / 'vite-cache')), json.dumps(str(frontend / '.vite-cache'))).replace('server:{host:', 'server:{fs:{allow:' + json.dumps([str(private), str(ROOT / 'frontend')]) + '},host:'); vite.write_text(source)
            for command, filename, cwd in [([python, str(server)], 'api.log', ROOT / 'backend'), (['node', str(vite)], 'vite.log', frontend)]:
                stream = (private / filename).open('w'); streams.append(stream); processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, start_new_session=True))
            helpers.ready(base + '/ready', processes); helpers.ready(app + '/', processes)
            users, emails = [], []
            for name in ['Alice activity proof', 'Bob activity proof']:
                email = 'feed-' + secrets.token_hex(6) + '@example.com'; emails.append(email)
                users.append(helpers.api(base, 'POST', '/auth/register', data={'name': name, 'email': email, 'password': PASSWORD}))
            alice, bob = users; workspace = helpers.api(base, 'POST', '/workspaces', alice, {'name': 'Activity studio'})
            channel = helpers.api(base, 'POST', '/channels', alice, {'name': 'Updates', 'workspace_id': workspace['id']})
            invitation = helpers.api(base, 'POST', '/channels/' + channel['id'] + '/invites', alice, {})
            helpers.api(base, 'POST', '/channels/invites/accept', bob, {'token': invitation['token']})
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), args=['--no-sandbox'])
                contexts = [browser.new_context(viewport={'width': 1440, 'height': 1100}, locale='en-US', reduced_motion='reduce') for _ in users]
                pages = [context.new_page() for context in contexts]; errors = []
                for page in pages: page.set_default_timeout(30000); page.on('pageerror', lambda error: errors.append(str(error)[:300]))
                try:
                    stage = 'native activity composer with safe rich text and poll'
                    panels = [enter(page, app, email, workspace['id'], user['user_id']) for page, email, user in zip(pages, emails, users)]
                    left, right = panels
                    left.get_by_role('button', name='Write an update', exact=True).click(); editor = left.get_by_role('form', name='Activity editor', exact=True)
                    editor.get_by_label('Update title', exact=True).fill('A considered team update')
                    editor.get_by_label('Update text', exact=True).fill('**A clear direction**\n- First step\n<script>window.feedUnsafe=true</script>')
                    editor.get_by_label('Include a poll', exact=True).check(); editor.get_by_label('Poll question', exact=True).fill('Which day works?')
                    editor.get_by_label('Option 1', exact=True).fill('Monday'); editor.get_by_label('Option 2', exact=True).fill('Tuesday')
                    editor.get_by_role('button', name='Publish update', exact=True).click()
                    expect(left.get_by_role('heading', name='A considered team update', exact=True)).to_be_visible()
                    assert pages[0].evaluate('()=>window.feedUnsafe!==true')
                    expect(left.locator('.fc-feed-post strong').filter(has_text='A clear direction')).to_be_visible()
                    right.get_by_role('button', name='Refresh activity feed', exact=True).click()
                    expect(right.get_by_role('heading', name='A considered team update', exact=True)).to_be_visible()
                    stage = 'peer follow, authenticated comment, vote, reaction and private read state'
                    right.get_by_role('button', name='Follow author', exact=True).click()
                    right.get_by_label('Show', exact=True).select_option('following')
                    right.get_by_role('button', name='Open discussion', exact=True).click()
                    discussion = right.get_by_role('region', name='Activity discussion', exact=True)
                    discussion.get_by_label('Monday', exact=False).check()
                    discussion.get_by_role('button', name='Submit vote', exact=True).click()
                    expect(discussion.get_by_text('1 voters', exact=True)).to_be_visible()
                    discussion.get_by_label('Add a comment', exact=True).fill('An authenticated peer comment')
                    discussion.get_by_role('button', name='Save comment', exact=True).click()
                    expect(discussion.get_by_text('An authenticated peer comment', exact=True)).to_be_visible()
                    discussion.get_by_role('button', name='Like update', exact=True).click()
                    expect(discussion.get_by_role('button', name='Like update', exact=True)).to_have_attribute('aria-pressed', 'true')
                    discussion.get_by_role('button', name='Mark as read', exact=True).click()
                    expect(right.get_by_text('Read', exact=True)).to_be_visible()
                    results['native_actual_composer_safe_formatting_poll_authenticated_comment_reaction_follow_read'] = True
                    stage = 'author edit, immutable revision history, archive and restore'
                    left.get_by_role('button', name='Edit', exact=True).click(); editor.get_by_label('Update text', exact=True).fill('A revised team update')
                    editor.get_by_role('button', name='Publish update', exact=True).click()
                    left.get_by_role('button', name='Versions', exact=True).click(); history = left.get_by_role('region', name='Activity version history', exact=True)
                    expect(history.get_by_text('Revision 2 · Alice activity proof', exact=True)).to_be_visible()
                    expect(history.get_by_text('<script>window.feedUnsafe=true</script>', exact=True)).to_be_visible()
                    left.get_by_role('button', name='Archive update', exact=True).click(); left.get_by_label('Show', exact=True).select_option('archived')
                    left.get_by_role('button', name='Restore update', exact=True).click(); left.get_by_label('Show', exact=True).select_option('all')
                    expect(left.get_by_role('heading', name='A considered team update', exact=True)).to_be_visible()
                    with sqlite3.connect(private / 'proof.db') as database:
                        payloads = database.execute('SELECT encrypted_payload FROM activityfeedrevision').fetchall()
                        assert payloads and all('considered' not in payload and 'Which day' not in payload for (payload,) in payloads)
                    results['native_encrypted_at_rest_immutable_history_archive_restore'] = True
                    stage = 'Swedish localized 320px activity composer'
                    pages[0].evaluate('async()=>{(await import("/src/i18n/core.ts")).applyLocale("sv")}')
                    localized = pages[0].locator('.fc-conversation-work[data-work-kind="feed"]')
                    expect(localized.get_by_role('heading', level=3, name='Aktivitetsflöde', exact=True)).to_be_visible()
                    pages[0].set_viewport_size({'width': 320, 'height': 900}); localized.get_by_role('button', name='Skriv en uppdatering', exact=True).click()
                    expect(localized.get_by_label('Uppdateringens titel', exact=True)).to_be_visible()
                    assert pages[0].evaluate('()=>document.documentElement.scrollWidth<=innerWidth+1')
                    assert localized.evaluate('element=>element.scrollWidth<=element.clientWidth+1')
                    REPORT.parent.mkdir(parents=True, exist_ok=True); pages[0].screenshot(path=str(REPORT.with_name('activity-feed-mobile.png')))
                    results['native_swedish_activity_ui_mobile_320px'] = True
                    if options.secure:
                        stage = 'actual Matrix feed cryptography and hostile ciphertext contexts'; secure_proof(base, private, workspace, users, pages, results)
                    assert not errors, 'Browser page exception'
                    print('PASS native activity UI, rich text, peer interaction, poll, follow/read, revisions/archive/restore and Swedish320px', flush=True)
                finally:
                    for context in contexts: context.close()
                    browser.close()
        except Exception as failure:
            detail = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+', '[redacted]', str(failure).replace(PASSWORD, '[redacted]'))
            print('FAIL activity-feed stage: ' + stage + '; exception=' + type(failure).__name__ + '\n' + detail[:2500], file=sys.stderr, flush=True)
            if 'errors' in locals(): print('Browser exceptions: ' + json.dumps(errors[:3]), file=sys.stderr, flush=True)
            if failure.__traceback__:
                last = traceback.extract_tb(failure.__traceback__)[-1]
                print('Failure location: ' + Path(last.filename).name + ':' + str(last.lineno), file=sys.stderr, flush=True)
            raise SystemExit(1) from None
        finally:
            helpers.stop(processes)
            for stream in streams: stream.close()
    REPORT.parent.mkdir(parents=True, exist_ok=True); REPORT.write_text(json.dumps({'scope': 'Disposable API/Vite/Chromium two-user native UI' + (' and actual retained Matrix with new isolated identities' if options.secure else ''),
        'results': results, 'cleanup': 'Own API/Vite processes, SQLite/files/keys, immutable frontend copy and browser contexts removed. Retained Matrix is preserved.'}, indent=2) + '\n')


if __name__ == '__main__': main()
