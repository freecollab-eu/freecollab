#!/usr/bin/env python3
"""Manual DOCX upload/version/download/passive-preview proof using private services.

CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/office-files-browser.py
No Matrix, provider credentials, external services or existing databases are used.
"""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit
import zipfile

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('private_sync_proof_helpers', Path(__file__).with_name('remote-sync-browser.py'))
helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(helpers)
PASSWORD = 'Private-office-files-proof!2026'
MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
REPORT = Path(os.getenv('OFFICE_FILES_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/office-files-browser.json')))


def docx_bytes(text):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr('word/document.xml', '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>' + text + '</w:t></w:r></w:p></w:body></w:document>')
    return output.getvalue()


def downloaded(page, button, expected, record, target):
    with page.expect_response(lambda response: urlsplit(response.url).path == '/api/files/' + record['id'] and response.request.method == 'GET') as fetched:
        with page.expect_download() as saved:
            page.get_by_role('button', name=button, exact=True).click()
    response = fetched.value
    assert response.status == 200
    assert response.headers['content-type'].split(';')[0] == MIME
    assert response.headers.get('content-disposition', '').startswith('attachment;')
    assert record['filename'] in unquote(response.headers['content-disposition'])
    assert response.headers.get('x-content-type-options') == 'nosniff'
    saved.value.save_as(str(target)); assert target.read_bytes() == expected
    assert saved.value.suggested_filename == record['filename']


def main():
    stage, results, processes, streams = 'private services startup', {}, [], []
    with tempfile.TemporaryDirectory(prefix='freecollab-office-files-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = helpers.port(), helpers.port()
            base, app = 'http://127.0.0.1:' + str(api_port), 'http://127.0.0.1:' + str(app_port)
            environment = os.environ.copy()
            environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(private / 'proof.db'), STORAGE_PATH=str(private / 'files'),
                JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof': base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',
                MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'message-keys.json'), ACCOUNT_SECURITY_KEY_FILE=str(private / 'account.key'),
                MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled', PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]),
                OIDC_ENABLED='false', OTRUST_ENABLED='false', SYNC_ENABLED='false', PYTHONPATH=str(ROOT / 'backend'))
            environment['NO_PROXY'] = ','.join(filter(None, [environment.get('NO_PROXY', ''), 'localhost', '127.0.0.1', '::1']))
            python = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)
            server = private / 'api.py'
            server.write_text('import uvicorn\nfrom app.main import socket_app\nuvicorn.run(socket_app,host="127.0.0.1",port=' + str(api_port) + ',proxy_headers=False,access_log=False)\n')
            for command, filename, cwd in [([python, str(server)], 'api.log', ROOT / 'backend'), (['node', str(helpers.vite_launcher(private, base, app_port))], 'vite.log', ROOT / 'frontend')]:
                stream = (private / filename).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, start_new_session=True))
            helpers.ready(base + '/ready', processes); helpers.ready(app + '/', processes)
            stage = 'new private native account and channel'
            email = 'office-proof-' + secrets.token_hex(6) + '@example.com'
            user = helpers.api(base, 'POST', '/auth/register', data={'name': 'Office files proof', 'email': email, 'password': PASSWORD})
            workspace = helpers.api(base, 'POST', '/workspaces', user, {'name': 'Studio documents'})
            channel = helpers.api(base, 'POST', '/channels', user, {'name': 'Project documents', 'workspace_id': workspace['id']})
            assert not channel.get('secure')
            first_name, second_name = 'Project brief.docx', 'Revised project brief.docx'
            first_bytes, second_bytes = docx_bytes('Original private Office document'), docx_bytes('Revised private Office document')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), args=['--no-sandbox'])
                context = browser.new_context(accept_downloads=True, viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
                page = context.new_page(); page.set_default_timeout(30000); page_errors = []
                page.on('pageerror', lambda error: page_errors.append(type(error).__name__))
                try:
                    stage = 'native password sign-in and actual file picker'
                    page.goto(app, wait_until='domcontentloaded')
                    page.evaluate('([key,value]) => localStorage.setItem(key,value)', ['freecollab.workspace:' + user['user_id'], workspace['id']])
                    page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
                    page.get_by_label('Email', exact=True).fill(email); page.get_by_label('Password', exact=True).fill(PASSWORD)
                    page.get_by_role('button', name='Sign In', exact=True).click()
                    page.get_by_role('button', name='Channel files', exact=True).click()
                    panel = page.get_by_role('dialog', name='Channel files', exact=True)
                    chooser = page.get_by_label('Choose a file', exact=True)
                    assert MIME in chooser.get_attribute('accept').split(',')
                    stage = 'manual DOCX upload acknowledges real stored metadata'
                    with page.expect_response(lambda response: urlsplit(response.url).path == '/api/files/channels/' + channel['id'] and response.request.method == 'POST') as uploaded:
                        chooser.set_input_files({'name': first_name, 'mimeType': MIME, 'buffer': first_bytes})
                    assert uploaded.value.status in (200, 201)
                    first = uploaded.value.json(); assert first['content_type'] == MIME and first['version_number'] == 1
                    expect(panel.get_by_role('button', name='Download ' + first_name, exact=True)).to_be_visible()
                    first_row = panel.locator('.fc-file-row').filter(has=page.get_by_text(first_name, exact=True))
                    expect(first_row.locator('.fc-file-copy > span')).to_contain_text('Word document')
                    first_row.get_by_role('button', name='Preview ' + first_name, exact=True).click()
                    preview = page.get_by_role('dialog', name='Preview ' + first_name, exact=True)
                    expect(preview.get_by_text('Original private Office document', exact=True)).to_be_visible()
                    expect(preview.get_by_text('Passive text preview. Layout, embedded content and formulas are not executed.', exact=True)).to_be_visible()
                    assert preview.locator('iframe,embed,object,script').count() == 0
                    preview.get_by_role('button', name='Close file preview', exact=True).click()
                    downloaded(page, 'Download ' + first_name, first_bytes, first, private / 'first.docx')
                    results['docx_manual_file_picker_upload_and_metadata'] = True
                    results['docx_download_exact_bytes_mime_attachment_nosniff'] = True
                    stage = 'manual DOCX new version preserves document identity'
                    first_row.get_by_role('button', name='New version', exact=True).click()
                    version_chooser = page.get_by_label('Choose a new version', exact=True)
                    assert MIME in version_chooser.get_attribute('accept').split(',')
                    with page.expect_response(lambda response: urlsplit(response.url).path == '/api/files/' + first['document_id'] + '/versions' and response.request.method == 'POST') as revised:
                        version_chooser.set_input_files({'name': second_name, 'mimeType': MIME, 'buffer': second_bytes})
                    assert revised.value.status in (200, 201)
                    second = revised.value.json()
                    assert second['content_type'] == MIME and second['version_number'] == 2 and second['version_count'] == 2
                    assert second['document_id'] == first['document_id'] and second['id'] != first['id']
                    expect(panel.get_by_role('button', name='Download ' + second_name, exact=True)).to_be_visible()
                    assert panel.get_by_role('button', name='Download ' + first_name, exact=True).count() == 0
                    downloaded(page, 'Download ' + second_name, second_bytes, second, private / 'second.docx')
                    results['docx_new_version_upload_same_document_correct_metadata'] = True
                    stage = 'both immutable Office revisions download and passively preview exact historical text'
                    second_row = panel.locator('.fc-file-row').filter(has=page.get_by_text(second_name, exact=True))
                    expect(second_row.locator('.fc-file-copy > span')).to_contain_text('Word document')
                    second_row.get_by_role('button', name='2 versions', exact=True).click()
                    history = panel.get_by_role('region', name='Version history for ' + second_name, exact=True)
                    expect(history.get_by_text('Version 1', exact=True)).to_be_visible()
                    assert history.get_by_role('button', name=re.compile('Preview')).count() == 2
                    assert second_row.get_by_role('button', name='Preview ' + second_name, exact=True).count() == 1
                    for number, filename, expected in [(1, first_name, 'Original private Office document'), (2, second_name, 'Revised private Office document')]:
                        history.get_by_role('button', name='Preview version ' + str(number) + ' of ' + filename, exact=True).click()
                        preview = page.get_by_role('dialog', name='Preview ' + filename, exact=True)
                        expect(preview.get_by_text(expected, exact=True)).to_be_visible()
                        assert preview.locator('iframe,embed,object,script').count() == 0
                        preview.get_by_role('button', name='Close file preview', exact=True).click()
                    assert panel.locator('iframe,embed,object').count() == 0
                    downloaded(page, 'Download version 1 of ' + first_name, first_bytes, first, private / 'history-first.docx')
                    downloaded(page, 'Download version 2 of ' + second_name, second_bytes, second, private / 'history-second.docx')
                    stored = helpers.api(base, 'GET', '/files/channels/' + channel['id'], user)
                    assert len(stored) == 1 and stored[0]['id'] == second['id']
                    results['immutable_office_history_retains_both_exact_versions'] = True
                    results['office_passive_current_and_historical_text_without_active_inline_documents'] = True
                    results['office_file_type_label_not_plain_text'] = True
                    stage = 'mobile native Office files layout'
                    page.set_viewport_size({'width': 320, 'height': 900})
                    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth + 1')
                    assert panel.evaluate('element => element.scrollWidth <= element.clientWidth + 1')
                    REPORT.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(REPORT.with_name('office-files-mobile.png')))
                    assert not page_errors, 'Browser page exception'
                    results['native_office_mobile_320px_zero_browser_errors'] = True
                    print('PASS native DOCX manual upload/new version, exact response+downloads, attachment MIME, immutable history and passive current/historical Office text previews', flush=True)
                finally: context.close(); browser.close()
        except Exception as failure:
            detail = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+', '[redacted]', str(failure).replace(PASSWORD, '[redacted]'))
            print('FAIL office-files browser stage: ' + stage + '; exception=' + type(failure).__name__ + '\n' + detail[:2500], file=sys.stderr, flush=True)
            raise SystemExit(1) from None
        finally:
            helpers.stop(processes)
            for stream in streams: stream.close()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({'scope': 'Actual private API/Vite/Chromium, native account and channel, real DOCX zip bytes; no Matrix or external provider', 'results': results,
        'cleanup': 'Own API/Vite processes, SQLite database, files, keys, downloads and browser contexts removed in finally'}, indent=2) + '\n')


if __name__ == '__main__': main()
