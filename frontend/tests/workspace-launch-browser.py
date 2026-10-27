#!/usr/bin/env python3
"""Actual private API + frozen application + Chromium launch-guide proof.

No real users, provider credentials, remote writes, email or deployment. All
fixture accounts, keys, SQLite data, files and child services are temporary.
"""
from ui_navigation import close_channels, open_channels, open_chat_tool
import base64
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import zlib
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PYTHON = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)
spec = importlib.util.spec_from_file_location('customer_sso_fixture', Path(__file__).with_name('customer-sso-browser.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
REPORT = Path(os.getenv('WORKSPACE_LAUNCH_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/workspace-launch-browser.json')))


def mobile_layout(guide):
    return guide.evaluate('''root=>({viewport:innerWidth,document:document.documentElement.scrollWidth,
      guide:{client:root.clientWidth,scroll:root.scrollWidth,font:getComputedStyle(root).font},fonts:document.fonts.status,
      overflow:[...document.body.querySelectorAll('*')].filter(node=>node.getClientRects().length).map(node=>{
        const box=node.getBoundingClientRect();return {tag:node.tagName,classes:typeof node.className==='string'?node.className:'',
          left:box.left,right:box.right,client:node.clientWidth,scroll:node.scrollWidth};
      }).filter(row=>row.left< -1||row.right>innerWidth+1).slice(0,24)})''')


def wait_mobile_layout(page, guide, evidence, name, check_guide=False):
    entry = {'name': name, 'initial': mobile_layout(guide)}
    evidence.append(entry)
    try:
        # Resize and font loading are asynchronous; persistent overflow still fails
        # within three seconds, using the original exact one-pixel limits.
        page.wait_for_function('''async ({root,checkGuide})=>{await document.fonts.ready;
          return document.documentElement.scrollWidth<=innerWidth+1&&(!checkGuide||root.scrollWidth<=root.clientWidth+1);
        }''', arg={'root': guide.element_handle(), 'checkGuide': check_guide}, timeout=3000)
    finally:
        entry['settled'] = mobile_layout(guide)


def main():
    processes, streams, results, stage = [], [], {}, 'private fixture startup'
    layout_evidence = []
    with tempfile.TemporaryDirectory(prefix='freecollab-workspace-launch-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = p.port(), p.port()
            api_base, app_base = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
            database = private / 'proof.db'
            environment = os.environ.copy()
            environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(database), STORAGE_PATH=str(private / 'files'),
                JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
                ACCOUNT_SECURITY_KEY_FILE=str(private / 'account.key'), MESSAGE_ENCRYPTION_KEYS=json.dumps({'launch-proof': base64.b64encode(secrets.token_bytes(32)).decode()}),
                MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='launch-proof', MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'message-keys.json'),
                PUBLIC_APP_URL=app_base, CORS_ORIGINS=json.dumps([app_base]), MATRIX_ENABLED='false', OIDC_ENABLED='false',
                OTRUST_ENABLED='false', MAIL_DELIVERY_MODE='disabled', PYTHONPATH=str(ROOT / 'backend'))
            for command, filename, cwd in [([PYTHON, str(p.create_launcher(private, [], api_port))], 'api.log', ROOT / 'backend'),
                (['node', str(p.create_vite(private, api_base, app_port))], 'vite.log', ROOT / 'frontend')]:
                stream = (private / filename).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, start_new_session=True))
            p.ready(api_base + '/ready', processes); p.ready(app_base + '/', processes)
            owner = p.native_user(api_base, database, 'Launch Owner')
            teammate = p.native_user(api_base, database, 'Launch Teammate')
            outsider = p.native_user(api_base, database, 'Launch Outsider')
            workspace = p.api(api_base, 'POST', '/workspaces', owner, {'name': 'Northern Studio'})
            logo = private / 'studio.png'
            def png_chunk(kind, content):
                return struct.pack('!I', len(content)) + kind + content + struct.pack('!I', zlib.crc32(kind + content) & 0xffffffff)
            logo.write_bytes(b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', struct.pack('!2I5B', 100, 100, 8, 2, 0, 0, 0))
                             + png_chunk(b'IDAT', zlib.compress((b'\x00' + bytes.fromhex('36594c') * 100) * 100)) + png_chunk(b'IEND', b''))
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), headless=True, args=['--no-sandbox'])
                context = browser.new_context(viewport={'width': 1440, 'height': 1050}, reduced_motion='reduce', permissions=['clipboard-read', 'clipboard-write'])
                page = context.new_page(); page.set_default_timeout(30000)
                page_errors = []; page.on('pageerror', lambda error: page_errors.append(type(error).__name__))
                other_context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
                other = other_context.new_page(); other.set_default_timeout(30000)
                other.on('pageerror', lambda error: page_errors.append(type(error).__name__))
                try:
                    stage = 'native administrator opens the real Chat launch entry'
                    p.login(page, app_base, owner, workspace)
                    expect(page.get_by_role('dialog', name='Workspace launch guide', exact=True)).to_have_count(0)
                    prompt = page.locator('.fc-chat-launch-prompt')
                    expect(prompt.get_by_role('button', name='Set up this workspace', exact=True)).to_be_visible()
                    expect(page.get_by_role('navigation', name='Channels', exact=True)).to_be_hidden()
                    page.set_viewport_size({'width': 320, 'height': 900})
                    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth + 1')
                    REPORT.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(REPORT.with_name('workspace-empty-mobile.png')), full_page=True)
                    entry = prompt.get_by_role('button', name='Set up this workspace', exact=True)
                    entry.focus(); page.keyboard.press('Enter')
                    guide = page.get_by_role('dialog', name='Workspace launch guide', exact=True)
                    expect(guide).to_be_visible(); page.keyboard.press('Escape')
                    expect(guide).to_have_count(0); expect(entry).to_be_focused()
                    page.set_viewport_size({'width': 1440, 'height': 1050})
                    open_channels(page)
                    expect(page.locator('.fc-chat-launch-card')).to_be_visible()
                    close_channels(page)
                    entry.click()
                    guide = page.get_by_role('dialog', name='Workspace launch guide', exact=True)
                    expect(guide.get_by_role('heading', name='Give your workspace an identity', exact=True)).to_be_visible()
                    assert not p.api(api_base, 'GET', '/channels?workspace_id=' + workspace['id'], owner)
                    assert not p.api(api_base, 'GET', '/workspaces/' + workspace['id'] + '/invites', owner)
                    results['actual_chat_entry_no_automatic_seed_or_invite'] = True
                    results['visible_empty_workspace_setup_keyboard_focus_and_320px'] = True

                    stage = 'real workspace identity name and logo saves'
                    guide.get_by_label('Workspace name', exact=True).fill('Northern Team Studio')
                    guide.get_by_role('button', name='Save name', exact=True).click()
                    expect(guide.get_by_role('status').filter(has_text='Workspace name saved.')).to_be_visible()
                    guide.get_by_label('Upload workspace logo', exact=True).set_input_files(logo)
                    expect(guide.get_by_role('status').filter(has_text='Workspace logo saved.')).to_be_visible()
                    saved = next(item for item in p.api(api_base, 'GET', '/workspaces', owner) if item['id'] == workspace['id'])
                    assert saved['name'] == 'Northern Team Studio' and saved['logo_url']
                    results['actual_workspace_name_and_validated_logo'] = True

                    stage = 'dirty guard and actual first native channel'
                    guide.get_by_label('First channel name', exact=True).fill('An unfinished channel')
                    page.once('dialog', lambda dialog: dialog.dismiss())
                    guide.get_by_role('button', name='Close workspace launch guide', exact=True).click()
                    expect(guide.get_by_label('First channel name', exact=True)).to_have_value('An unfinished channel')
                    guide.get_by_label('First channel name', exact=True).fill('Our first conversation — design, delivery and decisions across Northern Studio')
                    guide.get_by_role('button', name='Create channel', exact=True).click()
                    expect(guide.get_by_role('status').filter(has_text='Channel created.')).to_be_visible()
                    channels = p.api(api_base, 'GET', '/channels?workspace_id=' + workspace['id'], owner)
                    assert len(channels) == 1 and not channels[0]['secure'] and channels[0]['workspace_id'] == workspace['id']
                    channel = channels[0]
                    with sqlite3.connect(database) as connection:
                        assert connection.execute('SELECT COUNT(*) FROM message WHERE channel_id=?', (channel['id'],)).fetchone()[0] == 0
                    results['dirty_close_guard_and_real_channel_without_automatic_message'] = True

                    stage = 'single-use invitation copy acknowledgement navigation guard and acceptance'
                    guide.get_by_role('button', name='Create single-use invitation', exact=True).click()
                    link = guide.get_by_label('Single-use invitation link', exact=True)
                    expect(link).to_be_visible(); secret = link.input_value()
                    guide.get_by_role('button', name='Close workspace launch guide', exact=True).click()
                    expect(guide).to_be_visible()
                    guide.get_by_role('button', name='Open connection guides', exact=True).click()
                    expect(page.get_by_role('dialog', name='Integrations', exact=True)).to_have_count(0)
                    guide.get_by_role('button', name='Open channel invitations', exact=True).click()
                    expect(page.get_by_role('dialog', name='Channel members', exact=True)).to_have_count(0)
                    guide.get_by_role('button', name='Write the first message', exact=True).click()
                    expect(guide).to_be_visible()
                    guide.get_by_role('button', name='Copy invitation', exact=True).click()
                    expect(guide.get_by_role('button', name='Copy again', exact=True)).to_be_visible()
                    assert page.evaluate('() => navigator.clipboard.readText()') == secret
                    assert page.evaluate('(secret) => [...Object.values(localStorage), ...Object.values(sessionStorage)].every(value => !value.includes(secret))', secret)
                    guide.get_by_role('button', name='I have saved this invitation privately', exact=True).click()
                    expect(link).to_have_count(0)
                    invite_token = parse_qs(urlsplit(secret).fragment)['workspace-invite'][0]
                    other.goto(secret, wait_until='domcontentloaded')
                    other.get_by_label('Email', exact=True).fill(teammate['email'])
                    other.get_by_label('Password', exact=True).fill(p.PASSWORD)
                    other.get_by_role('button', name='Sign In', exact=True).click()
                    expect(other.get_by_role('heading', name='You’re invited.', exact=True)).to_be_visible()
                    assert len(p.api(api_base, 'GET', '/workspaces/' + workspace['id'] + '/members', owner)) == 1
                    with other.expect_response(lambda response: response.url.endswith('/workspaces/invites/accept') and response.request.method == 'POST') as joined_workspace:
                        other.get_by_role('button', name='Join workspace', exact=True).click()
                    assert joined_workspace.value.ok
                    accepted = joined_workspace.value.json()
                    assert accepted['id'] == workspace['id']
                    p.authenticated(other)
                    assert not p.api(api_base, 'GET', '/channels?workspace_id=' + workspace['id'], teammate)
                    expect(other.locator('.fc-chat-launch-prompt')).to_have_count(0)
                    expect(other.locator('.fc-chat-composer textarea')).to_have_count(0)
                    for person in (teammate, outsider):
                        p.rejects(api_base, 'GET', '/messages/channels/' + channel['id'], person, statuses=(403, 404))
                        p.rejects(api_base, 'POST', '/messages/channels/' + channel['id'], person, {'content': 'Access must be explicit.'}, statuses=(403, 404))
                    p.rejects(api_base, 'POST', '/workspaces/invites/accept', teammate, {'token': invite_token}, statuses=(400, 404, 409))
                    guide.get_by_role('button', name='Refresh workspace launch state', exact=True).click()
                    expect(guide.get_by_text('2 people', exact=True)).to_be_visible()
                    assert all('token' not in item or item['token'] is None for item in p.api(api_base, 'GET', '/workspaces/' + workspace['id'] + '/invites', owner))
                    guide.get_by_role('button', name='Create single-use invitation', exact=True).click()
                    expect(guide.get_by_label('Single-use invitation link', exact=True)).to_be_visible()
                    revoked_workspace_token = parse_qs(urlsplit(guide.get_by_label('Single-use invitation link', exact=True).input_value()).fragment)['workspace-invite'][0]
                    guide.get_by_role('button', name='Revoke this invitation', exact=True).click()
                    expect(guide.get_by_label('Single-use invitation link', exact=True)).to_have_count(0)
                    p.rejects(api_base, 'POST', '/workspaces/invites/accept', outsider, {'token': revoked_workspace_token}, statuses=(400, 404, 409))
                    results['actual_single_use_invite_copy_ack_revoke_and_no_storage'] = True
                    results['actual_invitee_signin_and_workspace_acceptance_without_channel_access'] = True
                    print('PASS native identity/logo, guarded channel draft and real single-use invitation lifecycle', flush=True)

                    stage = 'separate explicit channel invitation and actual teammate acceptance'
                    assert not p.api(api_base, 'GET', '/channels/' + channel['id'] + '/invites', owner)
                    assert len(p.api(api_base, 'GET', '/channels/' + channel['id'] + '/members', owner)) == 1
                    guide.get_by_role('button', name='Open channel invitations', exact=True).click()
                    expect(guide).to_have_count(0)
                    members = page.get_by_role('dialog', name='Channel members', exact=True)
                    expect(members.get_by_text(channel['name'], exact=True)).to_be_visible()
                    with page.expect_response(lambda response: response.url.endswith('/channels/' + channel['id'] + '/invites') and response.request.method == 'POST') as channel_invitation:
                        members.get_by_role('button', name='Create invitation link', exact=True).click()
                    assert channel_invitation.value.ok
                    channel_link = members.get_by_label('Invitation link', exact=True)
                    expect(channel_link).to_be_visible(); channel_secret = channel_link.input_value()
                    channel_token = parse_qs(urlsplit(channel_secret).fragment)['invite'][0]
                    assert not p.api(api_base, 'GET', '/channels?workspace_id=' + workspace['id'], teammate)
                    other.goto(channel_secret, wait_until='domcontentloaded')
                    expect(other.get_by_role('button', name='Join channel', exact=True)).to_be_visible()
                    assert not p.api(api_base, 'GET', '/channels?workspace_id=' + workspace['id'], teammate)
                    with other.expect_response(lambda response: response.url.endswith('/channels/invites/accept') and response.request.method == 'POST') as joined_channel:
                        other.get_by_role('button', name='Join channel', exact=True).click()
                    assert joined_channel.value.ok and joined_channel.value.json()['id'] == channel['id']
                    p.authenticated(other)
                    teammate_channels = p.api(api_base, 'GET', '/channels?workspace_id=' + workspace['id'], teammate)
                    assert [item['id'] for item in teammate_channels] == [channel['id']]
                    assert len(p.api(api_base, 'GET', '/channels/' + channel['id'] + '/members', owner)) == 2
                    p.rejects(api_base, 'POST', '/channels/invites/accept', outsider, {'token': channel_token}, statuses=(400, 404, 409))
                    with sqlite3.connect(database) as connection:
                        assert connection.execute('SELECT COUNT(*) FROM message WHERE channel_id=?', (channel['id'],)).fetchone()[0] == 0
                    members.get_by_role('button', name='Close channel panel', exact=True).click()
                    open_chat_tool(page, 'Get started')
                    guide = page.get_by_role('dialog', name='Workspace launch guide', exact=True)
                    expect(guide).to_be_visible()
                    results['actual_separate_channel_invitation_explicit_acceptance_and_no_automatic_message'] = True

                    stage = 'existing personal appearance and both customer-owned connection guides'
                    guide.get_by_role('button', name='Language & appearance', exact=True).click()
                    appearance = page.get_by_role('dialog', name='Make space your own.', exact=True)
                    expect(appearance).to_be_visible()
                    appearance.locator('input[name="appearance-mode"][value="dark"]').check()
                    appearance.locator('input[name="appearance-font"][value="serif"]').check()
                    appearance.get_by_role('radio', name='Large', exact=True).check()
                    appearance.get_by_role('button', name='Save changes', exact=True).click()
                    expect(appearance).to_have_count(0)
                    guide.get_by_role('button', name='Refresh workspace launch state', exact=True).click()
                    expect(guide.get_by_text(re.compile('Dark appearance'))).to_be_visible()
                    personal = p.api(api_base, 'GET', '/preferences/appearance', owner)
                    assert personal['mode'] == 'dark' and personal['font_family'] == 'serif' and personal['font_size'] == 'large'
                    guide.get_by_role('button', name='Open connection guides', exact=True).click()
                    integrations = page.get_by_role('dialog', name='Integrations', exact=True)
                    expect(integrations).to_be_visible()
                    expect(integrations.locator('[data-connection-recipe="files.google"]')).to_be_visible()
                    expect(integrations.locator('[data-connection-recipe="files.microsoft"]')).to_be_visible()
                    integrations.get_by_role('button', name='Close integrations', exact=True).click()
                    expect(integrations).to_have_count(0); expect(guide).to_be_visible()
                    results['personal_appearance_and_google_m365_guides_preserve_context'] = True

                    stage = 'Swedish actual language preference and 320px launch guide'
                    with page.expect_response(lambda response: urlsplit(response.url).path == '/api/preferences/language'
                                              and response.request.method == 'PUT') as language_saved:
                        guide.get_by_label('Language', exact=True).select_option('sv')
                    assert language_saved.value.ok
                    guide = page.get_by_role('dialog', name='Startguide för arbetsytan', exact=True)
                    expect(guide.get_by_role('heading', name='En genomtänkt start.', exact=True)).to_be_visible()
                    assert p.api(api_base, 'GET', '/preferences/language', owner)['locale'] == 'sv'
                    page.set_viewport_size({'width': 320, 'height': 900})
                    wait_mobile_layout(page, guide, layout_evidence, 'Swedish320 document')
                    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth + 1')
                    REPORT.parent.mkdir(parents=True, exist_ok=True)
                    guide.locator('.fc-account-body').evaluate('node => node.scrollTo(0, 0)')
                    page.screenshot(path=str(REPORT.with_name('workspace-launch-mobile.png')), full_page=True)
                    guide.get_by_role('heading', name='Sedan, gå med i samtalet', exact=True).scroll_into_view_if_needed()
                    wait_mobile_layout(page, guide, layout_evidence, 'Swedish320 channel access', check_guide=True)
                    assert guide.evaluate('node => node.scrollWidth <= node.clientWidth + 1')
                    page.screenshot(path=str(REPORT.with_name('workspace-channel-access-mobile.png')), full_page=True)
                    results['saved_swedish_language_and_320px_layout'] = True

                    stage = 'explicit first message through the actual focused composer'
                    guide.get_by_role('button', name='Skriv det första meddelandet', exact=True).click()
                    expect(guide).to_have_count(0)
                    composer = page.locator('.fc-chat-composer textarea')
                    stage = 'first-message composer focus after closing the launch guide'
                    expect(composer).to_be_focused()
                    stage = 'first-message native send through the composer'
                    composer.fill('Vårt första riktiga samtal börjar här.')
                    with page.expect_response(lambda response: response.url.endswith('/mobile/offline/channels/' + channel['id'] + '/messages') and response.request.method == 'POST') as sent:
                        page.get_by_role('button', name='Skicka meddelande', exact=True).click()
                    print('Native first-message HTTP status: ' + str(sent.value.status), flush=True)
                    assert sent.value.ok, 'Native message POST was not successful'
                    delivered = sent.value.json()
                    assert delivered['content'] == 'Vårt första riktiga samtal börjar här.' and delivered['channel_id'] == channel['id']
                    expect(page.locator('#message-' + delivered['id'] + ' .fc-chat-message-content')).to_have_text('Vårt första riktiga samtal börjar här.')
                    with sqlite3.connect(database) as connection:
                        rows = connection.execute('SELECT content FROM message WHERE channel_id=?', (channel['id'],)).fetchall()
                    stage = 'first-message encrypted-at-rest persisted row'
                    assert len(rows) == 1 and 'Vårt första riktiga samtal börjar här.' not in rows[0][0]
                    stage = 'established workspace no longer receives the first-start suggestion'
                    open_channels(page)
                    expect(page.locator('.fc-chat-launch-card')).to_have_count(0)
                    close_channels(page)
                    results['actual_first_message_encrypted_at_rest_and_established_workspace_not_nagged'] = True

                    stage = 'two real participants share the first conversation while an outsider remains excluded'
                    expect(other.locator('#message-' + delivered['id'] + ' .fc-chat-message-content')).to_have_text('Vårt första riktiga samtal börjar här.')
                    other.locator('.fc-chat-composer textarea').fill('Jag är med. Nu kan vi prata i vår gemensamma kanal.')
                    with other.expect_response(lambda response: response.url.endswith('/mobile/offline/channels/' + channel['id'] + '/messages') and response.request.method == 'POST') as replied:
                        other.get_by_role('button', name='Send message', exact=True).click()
                    assert replied.value.ok
                    reply = replied.value.json()
                    assert reply['channel_id'] == channel['id'] and reply['author_id'] == teammate['user_id']
                    expect(page.locator('#message-' + reply['id'] + ' .fc-chat-message-content')).to_have_text('Jag är med. Nu kan vi prata i vår gemensamma kanal.')
                    p.rejects(api_base, 'GET', '/messages/channels/' + channel['id'], outsider, statuses=(403, 404))
                    p.rejects(api_base, 'POST', '/messages/channels/' + channel['id'], outsider, {'content': 'Must remain outside.'}, statuses=(403, 404))
                    with sqlite3.connect(database) as connection:
                        rows = connection.execute('SELECT content FROM message WHERE channel_id=?', (channel['id'],)).fetchall()
                    assert len(rows) == 2 and all('Vårt första riktiga samtal' not in row[0] and 'Jag är med.' not in row[0] for row in rows)
                    page.screenshot(path=str(REPORT.with_name('workspace-first-conversation-mobile.png')), full_page=True)
                    results['two_actual_users_exchange_messages_after_explicit_access_and_outsider_denied'] = True

                    stage = 'ordinary teammate has no admin launch entry'
                    other.get_by_role('button', name='More channel tools', exact=True).click()
                    expect(other.get_by_role('button', name='Get started', exact=True)).to_have_count(0)
                    other.keyboard.press('Escape')
                    open_channels(other)
                    expect(other.locator('.fc-chat-launch-card')).to_have_count(0)
                    close_channels(other)
                    assert not page_errors
                    results['member_cannot_enter_admin_guide_and_zero_page_errors'] = True
                finally:
                    other_context.close(); context.close(); browser.close()
            proof_files = ['components/Chat.tsx', 'components/WorkspaceLaunchPanel.tsx', 'components/ChannelTools.tsx',
                'components/WorkspaceInvitation.tsx', 'components/AcceptInvite.tsx', 'App.tsx', 'lib/workspace-launch.ts',
                'styles/workspace-launch.css', 'i18n/workspace-first-conversation.sv.ts']
            frozen_sources = {'frontend/src/' + name: hashlib.sha256((private / 'frontend/src' / name).read_bytes()).hexdigest() for name in proof_files}
            REPORT.write_text(json.dumps({'scope': 'Actual private API, frozen full application and Chromium; no providers, email or real users', 'results': results,
                'frozen_frontend_sources': frozen_sources,
                'mobile_layout_evidence': layout_evidence,
                'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'cleanup': 'Own API/Vite services, SQLite data, files, keys, frozen frontend and browser contexts removed in finally'}, indent=2) + '\n')
            print('PASS workspace launch guide: ' + str(len(results)) + ' actual browser checks', flush=True)
        except Exception as failure:
            print('FAIL workspace launch proof stage: ' + stage + ' (' + type(failure).__name__ + ')', file=sys.stderr)
            if layout_evidence: print('Mobile layout evidence: ' + json.dumps(layout_evidence), file=sys.stderr)
            if 'first-message' in stage:
                for line in (private / 'api.log').read_text(errors='replace').splitlines():
                    if re.match(r'^(?:NameError|TypeError|AttributeError|RuntimeError|ValueError):', line):
                        print('Safe server exception class: ' + line.split(':')[0], file=sys.stderr)
            # Locator diagnostics contain product labels, never filled values.
            for line in str(failure).splitlines():
                if 'waiting for get_by_role(' in line:
                    print('Safe locator diagnostic: ' + line.strip()[:200], file=sys.stderr); break
            raise SystemExit(1) from None
        finally:
            p.stop(processes)
            for stream in streams: stream.close()


if __name__ == '__main__':
    main()
