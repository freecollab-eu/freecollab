#!/usr/bin/env python3
"""Actual private API + frozen application + Chromium launch-guide proof.

No real users, provider credentials, remote writes, email or deployment. All
fixture accounts, keys, SQLite data, files and child services are temporary.
"""
from ui_navigation import close_channels, open_channels, open_chat_tool
import base64
import importlib.util
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
REPORT = ROOT.parent / '.freecollab/proof-results/workspace-launch-browser.json'


def main():
    processes, streams, results, stage = [], [], {}, 'private fixture startup'
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
                try:
                    stage = 'native administrator opens the real Chat launch entry'
                    p.login(page, app_base, owner, workspace)
                    open_channels(page)
                    expect(page.locator('.fc-chat-launch-card')).to_be_visible()
                    open_chat_tool(page, 'Get started')
                    guide = page.get_by_role('dialog', name='Workspace launch guide', exact=True)
                    expect(guide.get_by_role('heading', name='Give your workspace an identity', exact=True)).to_be_visible()
                    assert not p.api(api_base, 'GET', '/channels?workspace_id=' + workspace['id'], owner)
                    assert not p.api(api_base, 'GET', '/workspaces/' + workspace['id'] + '/invites', owner)
                    results['actual_chat_entry_no_automatic_seed_or_invite'] = True

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
                    guide.get_by_label('First channel name', exact=True).fill('Our first conversation')
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
                    guide.get_by_role('button', name='Copy invitation', exact=True).click()
                    expect(guide.get_by_role('button', name='Copy again', exact=True)).to_be_visible()
                    assert page.evaluate('() => navigator.clipboard.readText()') == secret
                    assert page.evaluate('(secret) => [...Object.values(localStorage), ...Object.values(sessionStorage)].every(value => !value.includes(secret))', secret)
                    guide.get_by_role('button', name='I have saved this invitation privately', exact=True).click()
                    expect(link).to_have_count(0)
                    invite_token = parse_qs(urlsplit(secret).fragment)['workspace-invite'][0]
                    accepted = p.api(api_base, 'POST', '/workspaces/invites/accept', teammate, {'token': invite_token})
                    assert accepted['id'] == workspace['id']
                    p.rejects(api_base, 'POST', '/workspaces/invites/accept', teammate, {'token': invite_token}, statuses=(400, 404, 409))
                    guide.get_by_role('button', name='Refresh workspace launch state', exact=True).click()
                    expect(guide.get_by_text('2 people', exact=True)).to_be_visible()
                    assert all('token' not in item or item['token'] is None for item in p.api(api_base, 'GET', '/workspaces/' + workspace['id'] + '/invites', owner))
                    guide.get_by_role('button', name='Create single-use invitation', exact=True).click()
                    expect(guide.get_by_label('Single-use invitation link', exact=True)).to_be_visible()
                    guide.get_by_role('button', name='Revoke this invitation', exact=True).click()
                    expect(guide.get_by_label('Single-use invitation link', exact=True)).to_have_count(0)
                    results['actual_single_use_invite_copy_ack_revoke_and_no_storage'] = True
                    print('PASS native identity/logo, guarded channel draft and real single-use invitation lifecycle', flush=True)

                    stage = 'existing personal appearance and both customer-owned connection guides'
                    guide.get_by_role('button', name='Language & appearance', exact=True).click()
                    appearance = page.get_by_role('dialog', name='Make space your own.', exact=True)
                    expect(appearance).to_be_visible()
                    appearance.locator('input[name="appearance-mode"][value="dark"]').check()
                    appearance.get_by_role('button', name='Save changes', exact=True).click()
                    expect(appearance).to_have_count(0)
                    guide.get_by_role('button', name='Refresh workspace launch state', exact=True).click()
                    expect(guide.get_by_text(re.compile('Dark appearance'))).to_be_visible()
                    guide.get_by_role('button', name='Open connection guides', exact=True).click()
                    integrations = page.get_by_role('dialog', name='Integrations', exact=True)
                    expect(integrations).to_be_visible()
                    expect(integrations.locator('[data-connection-recipe="files.google"]')).to_be_visible()
                    expect(integrations.locator('[data-connection-recipe="files.microsoft"]')).to_be_visible()
                    integrations.get_by_role('button', name='Close integrations', exact=True).click()
                    expect(integrations).to_have_count(0); expect(guide).to_be_visible()
                    results['personal_appearance_and_google_m365_guides_preserve_context'] = True

                    stage = 'Swedish actual language preference and 320px launch guide'
                    guide.get_by_label('Language', exact=True).select_option('sv')
                    guide = page.get_by_role('dialog', name='Startguide för arbetsytan', exact=True)
                    expect(guide.get_by_role('heading', name='En genomtänkt start.', exact=True)).to_be_visible()
                    assert p.api(api_base, 'GET', '/preferences/language', owner)['locale'] == 'sv'
                    page.set_viewport_size({'width': 320, 'height': 900})
                    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth + 1')
                    REPORT.parent.mkdir(parents=True, exist_ok=True)
                    guide.locator('.fc-account-body').evaluate('node => node.scrollTo(0, 0)')
                    page.screenshot(path=str(REPORT.with_name('workspace-launch-mobile.png')), full_page=True)
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

                    stage = 'ordinary teammate has no admin launch entry'
                    other_context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
                    try:
                        other = other_context.new_page(); other.set_default_timeout(30000)
                        p.login(other, app_base, teammate, accepted)
                        other.get_by_role('button', name='More channel tools', exact=True).click()
                        expect(other.get_by_role('button', name='Get started', exact=True)).to_have_count(0)
                        other.keyboard.press('Escape')
                        open_channels(other)
                        expect(other.locator('.fc-chat-launch-card')).to_have_count(0)
                        close_channels(other)
                    finally:
                        other_context.close()
                    assert not page_errors
                    results['member_cannot_enter_admin_guide_and_zero_page_errors'] = True
                finally:
                    context.close(); browser.close()
            REPORT.write_text(json.dumps({'scope': 'Actual private API, frozen full application and Chromium; no providers, email or real users', 'results': results,
                'cleanup': 'Own API/Vite services, SQLite data, files, keys, frozen frontend and browser contexts removed in finally'}, indent=2) + '\n')
            print('PASS workspace launch guide: ' + str(len(results)) + ' actual browser checks', flush=True)
        except Exception as failure:
            print('FAIL workspace launch proof stage: ' + stage + ' (' + type(failure).__name__ + ')', file=sys.stderr)
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
