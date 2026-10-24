"""Actual file UI against disposable services. Never run against customer data.

CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/files-browser.py
CHAT_BASE_URL defaults to http://127.0.0.1:3000; FILE_API_URL defaults to the Vite
/api proxy. To test a separate disposable API, set FILE_API_URL to its origin.
"""
from ui_navigation import open_chat_tool
import base64
import json
import os
from pathlib import Path
import tempfile
import uuid
from urllib.request import Request, urlopen
from playwright.sync_api import expect, sync_playwright

BASE = os.getenv('CHAT_BASE_URL', 'http://127.0.0.1:3000').rstrip('/')
API = os.getenv('FILE_API_URL', BASE + '/api').rstrip('/')
RUN = uuid.uuid4().hex[:12]
PASSWORD = 'Files-browser-password!'


def api(method, path, token=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    with urlopen(Request(API + path, headers=headers, method=method,
                         data=json.dumps(data).encode() if data is not None else None), timeout=45) as response:
        return json.load(response)


def pdf_bytes():
    first = b'0 0 1 rg 20 20 80 80 re f 0 0 0 rg BT /F1 16 Tf 10 150 Td (Local PDF preview) Tj ET'
    second = b'0 1 0 rg 30 30 80 80 re f 0 0 0 rg BT /F1 16 Tf 10 150 Td (Second page) Tj ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 240] /Resources << /Font << /F1 7 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 240] /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>',
               f'<< /Length {len(first)} >>\nstream\n'.encode() + first + b'\nendstream',
               f'<< /Length {len(second)} >>\nstream\n'.encode() + second + b'\nendstream',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>']
    body = b'%PDF-1.4\n'; offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(body)); body += f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(body)
    body += f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode()
    body += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    return body + f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()


def downloaded(page, label, expected):
    with page.expect_download() as pending:
        page.get_by_role('button', name=label, exact=True).click()
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / 'download'
        pending.value.save_as(str(target))
        assert target.read_bytes() == expected


auth = api('POST', '/auth/register', data={'name': 'Files tester', 'email': f'files-{RUN}@example.com', 'password': PASSWORD})
workspaces = api('GET', '/workspaces', auth['access_token'])
workspace = workspaces[0]['id']
channel = api('POST', '/channels', auth['access_token'], {'name': 'Files-' + RUN, 'workspace_id': workspace})
channel_id = channel['id']

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or None, headless=True, args=['--no-sandbox'])
    try:
        context = browser.new_context(accept_downloads=True)
        context.add_init_script("""(() => { window.fileUrls = {created: [], revoked: []}; const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL); URL.createObjectURL = blob => { const url = create(blob); window.fileUrls.created.push(url); return url; }; URL.revokeObjectURL = url => { window.fileUrls.revoked.push(url); revoke(url); }; })()""")
        context.add_init_script(f"localStorage.setItem('freecollab_token', {json.dumps(auth['access_token'])}); localStorage.setItem('freecollab_user_id', {json.dumps(auth['user_id'])});")
        page = context.new_page(); page.set_default_timeout(45000)
        errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
        if API != BASE + '/api':
            page.route('**/api/**', lambda route: route.fulfill(response=route.fetch(url=API + route.request.url.split('/api', 1)[1])))
        page.goto(BASE, wait_until='domcontentloaded', timeout=90000)
        expect(page.get_by_role('textbox', name='Message #' + channel['name'], exact=True)).to_be_visible(timeout=90000)
        open_chat_tool(page, 'Channel files')
        text = f'file-{RUN}.txt'; png = f'image-{RUN}.png'; pdf = f'reference-{RUN}.pdf'
        png_bytes = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a0e8AAAAASUVORK5CYII=')
        page.get_by_label('Choose a file', exact=True).set_input_files([
            {'name': text, 'mimeType': 'text/plain', 'buffer': b'original bytes'},
            {'name': png, 'mimeType': 'image/png', 'buffer': png_bytes},
            {'name': pdf, 'mimeType': 'application/pdf', 'buffer': pdf_bytes()},
        ])
        for filename in (text, png, pdf):
            expect(page.get_by_role('button', name='Download ' + filename, exact=True)).to_be_visible()
        assert len(api('GET', '/files/channels/' + channel_id, auth['access_token'])) == 3
        downloaded(page, 'Download ' + text, b'original bytes')
        print('PASS: multi-file queue acknowledges delivery and downloads exact bytes', flush=True)

        row = page.locator('.fc-file-row').filter(has=page.get_by_text(text, exact=True))
        row.get_by_role('button', name='New version', exact=True).click()
        revised = f'revised-{RUN}.txt'
        page.get_by_label('Choose a new version', exact=True).set_input_files({'name': revised, 'mimeType': 'text/plain', 'buffer': b'revised bytes'})
        expect(page.get_by_role('button', name='Download ' + revised, exact=True)).to_be_visible()
        row = page.locator('.fc-file-row').filter(has=page.get_by_text(revised, exact=True))
        row.get_by_role('button', name='2 versions', exact=True).click()
        downloaded(page, 'Download version 1 of ' + text, b'original bytes')
        downloaded(page, 'Download version 2 of ' + revised, b'revised bytes')
        print('PASS: new version replaces current document and immutable history downloads both exact revisions', flush=True)

        page.get_by_role('button', name='Preview ' + png, exact=True).click()
        preview = page.get_by_role('dialog', name='Preview ' + png, exact=True)
        expect(preview.locator('img')).to_be_visible()
        url = preview.locator('img').get_attribute('src'); assert url.startswith('blob:')
        page.get_by_role('button', name='Close file preview', exact=True).click()
        assert page.evaluate('(url) => window.fileUrls.revoked.includes(url)', url)
        page.get_by_role('button', name='Preview ' + pdf, exact=True).click()
        preview = page.get_by_role('dialog', name='Preview ' + pdf, exact=True)
        expect(preview.get_by_role('img', name='PDF page 1', exact=True)).to_be_visible(timeout=90000)
        expect(preview.get_by_text('Page 1 of 2', exact=True)).to_be_visible()
        preview.get_by_role('button', name='Next PDF page', exact=True).click()
        expect(preview.get_by_text('Page 2 of 2', exact=True)).to_be_visible()
        expect(preview.get_by_role('img', name='PDF page 2', exact=True)).to_be_visible()
        page.keyboard.press('Escape')
        expect(page.get_by_role('dialog', name='Channel files', exact=True)).to_be_visible()
        print('PASS: local image preview revokes blob URLs; PDF canvas renders and paginates with local worker', flush=True)

        held = []; retry_keys = []; upload_path = '**/api/files/channels/' + channel_id
        page.evaluate('window.__filesProofHeldPostCount = 0')
        def hold(route):
            if route.request.method == 'POST':
                retry_keys.append(route.request.headers.get('idempotency-key')); held.append(route)
                page.evaluate('(count) => { window.__filesProofHeldPostCount = count; }', len(held))
            else:
                route.fallback()
        page.route(upload_path, hold)
        cancel_name = f'cancel-{RUN}.txt'
        page.get_by_label('Choose a file', exact=True).set_input_files({'name': cancel_name, 'mimeType': 'text/plain', 'buffer': b'cancel then retry'})
        expect(page.get_by_role('button', name='Cancel upload ' + cancel_name, exact=True)).to_be_visible()
        # Cancel is also valid while queued. This proof requires an in-flight
        # request so its retry compares two actual idempotency headers.
        page.wait_for_function('() => window.__filesProofHeldPostCount === 1', timeout=10000)
        page.get_by_role('button', name='Cancel upload ' + cancel_name, exact=True).click()
        queue_row = page.locator('.fc-upload-item').filter(has_text=cancel_name)
        expect(queue_row).to_contain_text('Cancelled locally')
        expect(page.get_by_role('button', name='Download ' + cancel_name, exact=True)).to_have_count(0)
        assert not any(file['filename'] == cancel_name for file in api('GET', '/files/channels/' + channel_id, auth['access_token']))
        for route in held:
            route.abort('aborted')
        page.unroute(upload_path, hold)
        def retry(route):
            if route.request.method == 'POST':
                retry_keys.append(route.request.headers.get('idempotency-key'))
                route.fulfill(response=route.fetch(url=API + '/files/channels/' + channel_id))
            else:
                route.fallback()
        page.route(upload_path, retry)
        page.get_by_role('button', name='Retry upload ' + cancel_name, exact=True).click()
        expect(page.get_by_role('button', name='Download ' + cancel_name, exact=True)).to_be_visible()
        assert len(retry_keys) == 2
        assert all(retry_keys)
        assert retry_keys[0] == retry_keys[1]
        page.unroute(upload_path, retry)
        print('PASS: cancel does not claim delivery; safe retry uses the same upload identity', flush=True)

        lost_name = f'lost-ack-{RUN}.txt'; lost = {'first': True}; ack_ids = []
        def lose_ack(route):
            if route.request.method != 'POST':
                route.fallback(); return
            response = route.fetch(url=API + '/files/channels/' + channel_id)
            ack_ids.append(response.json()['id'])
            if lost['first']:
                lost['first'] = False; route.abort('failed')
            else:
                route.fulfill(response=response)
        page.route(upload_path, lose_ack)
        page.get_by_label('Choose a file', exact=True).set_input_files({'name': lost_name, 'mimeType': 'text/plain', 'buffer': b'saved before lost acknowledgement'})
        expect(page.get_by_role('button', name='Retry upload ' + lost_name, exact=True)).to_be_visible()
        page.get_by_role('button', name='Retry upload ' + lost_name, exact=True).click()
        expect(page.get_by_role('button', name='Download ' + lost_name, exact=True)).to_be_visible()
        assert ack_ids[0] == ack_ids[1]
        assert len([file for file in api('GET', '/files/channels/' + channel_id, auth['access_token']) if file['filename'] == lost_name]) == 1
        page.unroute(upload_path, lose_ack)
        print('PASS: a server save followed by a lost response retries without duplicating the file', flush=True)

        dropped = f'dropped-{RUN}.txt'
        page.locator('.fc-file-dropzone').evaluate("""(element, name) => { const transfer = new DataTransfer(); transfer.items.add(new File(['dropped bytes'], name, {type:'text/plain'})); for (const type of ['dragenter','dragover','drop']) element.dispatchEvent(new DragEvent(type, {bubbles:true,cancelable:true,dataTransfer:transfer})); }""", dropped)
        expect(page.get_by_role('button', name='Download ' + dropped, exact=True)).to_be_visible()
        page.get_by_role('button', name='Close channel panel', exact=True).click()
        decision_title = 'Searchable decision ' + RUN
        decision = api('POST', '/channels/' + channel_id + '/decisions', auth['access_token'], {'title': decision_title, 'content': 'Decision reasoning ' + RUN, 'status': 'accepted'})
        open_chat_tool(page, 'Search all history')
        search = page.get_by_role('dialog', name='Search all history', exact=True)
        expect(search.get_by_text('Loading full history', exact=False)).to_have_count(0, timeout=90000)
        search.get_by_label('Search content type', exact=True).select_option('files')
        search.get_by_label('Search file type', exact=True).select_option('text/plain')
        search.get_by_label('Search messages', exact=True).fill(revised)
        expect(search.locator('.fc-history-result')).to_have_count(1)
        downloaded(page, 'Download search result ' + revised, b'revised bytes')
        search.get_by_label('Search messages', exact=True).fill(text)
        expect(search.locator('.fc-history-result')).to_have_count(0)
        search.get_by_label('Search messages', exact=True).fill(png)
        expect(search.locator('.fc-history-result')).to_have_count(0)
        search.get_by_label('Search file type', exact=True).select_option('images')
        expect(search.locator('.fc-history-result')).to_have_count(1)
        search.get_by_role('button', name='Preview search result ' + png, exact=True).click()
        expect(page.get_by_role('dialog', name='Preview ' + png, exact=True).locator('img')).to_be_visible()
        page.get_by_role('button', name='Close file preview', exact=True).click()
        search.get_by_label('Search content type', exact=True).select_option('decisions')
        search.get_by_label('Search messages', exact=True).fill('Decision reasoning ' + RUN)
        expect(search.locator('.fc-history-result')).to_have_count(1)
        search.locator('.fc-history-result').click()
        notebook = page.get_by_role('region', name='Decisions in this conversation', exact=True)
        expect(notebook.get_by_text(decision_title, exact=True)).to_be_visible()
        expect(page.locator('#decision-' + decision['id'])).to_be_focused()
        page.get_by_role('button', name='Close this work', exact=True).click()
        print('PASS: latest-filename search, file type filter, result download/preview, decision text search and jump to focused decision', flush=True)
        open_chat_tool(page, 'Channel files')
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.get_by_role('button', name='Preview ' + png, exact=True).click()
        expect(page.get_by_role('dialog', name='Preview ' + png, exact=True).locator('img')).to_be_visible()
        last_url = page.get_by_role('dialog', name='Preview ' + png, exact=True).locator('img').get_attribute('src')
        page.evaluate("localStorage.removeItem('freecollab_token'); localStorage.removeItem('freecollab_user_id'); dispatchEvent(new StorageEvent('storage', {key:'freecollab_token'}))")
        expect(page.get_by_role('dialog', name='Preview ' + png, exact=True)).to_have_count(0)
        assert page.evaluate('(url) => window.fileUrls.revoked.includes(url)', last_url)
        page.unroute_all(behavior='wait')
        assert not errors, errors
        print('PASS: drag/drop, mobile width, logout clears decrypted preview and revokes its URL; no browser exceptions', flush=True)
    finally:
        browser.close()
