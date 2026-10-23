from ui_navigation import open_conversation_work
#!/usr/bin/env python3
"""Disposable API/Vite/Chromium proof: exports, retention and identity erasure.

Run: CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/data-controls-browser.py
Only private loopback processes, synthetic accounts, SQLite and files are used.
"""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('data_proof_helpers', Path(__file__).with_name('remote-sync-browser.py'))
helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(helpers)
PASSWORD = 'Private-data-browser-proof!2026'
CONTENT = 'Private export and retention proof content'
REPORT = Path(os.getenv('DATA_CONTROLS_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/data-controls-browser.json')))


def main():
    stage, results, processes, streams = 'private service startup', {}, [], []
    with tempfile.TemporaryDirectory(prefix='freecollab-data-controls-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = helpers.port(), helpers.port()
            base, app = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
            environment = os.environ.copy()
            environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(private / 'proof.db'), STORAGE_PATH=str(private / 'files'),
                JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',
                MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'message-keys.json'), ACCOUNT_SECURITY_KEY_FILE=str(private / 'account.key'),
                MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled', PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]),
                OIDC_ENABLED='false', OTRUST_ENABLED='false', WEBHOOKS_ENABLED='false', SYNC_ENABLED='false', PYTHONPATH=str(ROOT / 'backend'))
            environment['NO_PROXY'] = ','.join(filter(None, [environment.get('NO_PROXY',''), 'localhost', '127.0.0.1', '::1']))
            (private / 'files').mkdir(mode=0o700)
            python = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)
            launcher = private / 'api.py'
            launcher.write_text('import uvicorn\nfrom app.main import socket_app\nuvicorn.run(socket_app,host="127.0.0.1",port=' + str(api_port) + ',proxy_headers=False,access_log=False)\n')
            for command, filename, cwd in [([python,str(launcher)],'api.log',ROOT/'backend'), (['node',str(helpers.vite_launcher(private,base,app_port))],'vite.log',ROOT/'frontend')]:
                stream = (private / filename).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            helpers.ready(base+'/ready',processes); helpers.ready(app+'/',processes)
            stage = 'synthetic native account and content fixtures'
            users = []
            for name in ['owner','successor']:
                email = 'data-' + name + '-' + secrets.token_hex(5) + '@example.com'
                user = helpers.api(base,'POST','/auth/register',data={'name':'Private '+name,'email':email,'password':PASSWORD}); user['email']=email; users.append(user)
            owner, peer = users
            workspace = helpers.api(base,'POST','/workspaces',owner,{'name':'Studio archive'})
            channel = helpers.api(base,'POST','/channels',owner,{'name':'Archive studio','workspace_id':workspace['id']})
            old_message = helpers.api(base,'POST','/messages/channels/'+channel['id'],owner,{'content':CONTENT})
            fresh = helpers.api(base,'POST','/messages/channels/'+channel['id'],owner,{'content':'Fresh shared work survives'})
            file = helpers.upload(base,owner,channel['id'],'private-export.txt',b'PRIVATE DOWNLOAD BYTES')
            old = (datetime.utcnow()-timedelta(days=30)).isoformat(' ')
            with sqlite3.connect(private/'proof.db') as connection:
                connection.execute('UPDATE message SET created_at=? WHERE id=?',(old,old_message['id']))
                connection.execute('UPDATE file SET created_at=? WHERE id=?',(old,file['id']))
                connection.execute('UPDATE filedocument SET created_at=? WHERE current_file_id=?',(old,file['id']))
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),args=['--no-sandbox'])
                context = browser.new_context(viewport={'width':1440,'height':1050},reduced_motion='reduce',accept_downloads=True)
                page = context.new_page(); page.set_default_timeout(45000); page_errors=[]; request_urls=[]
                page.on('pageerror',lambda failure:page_errors.append(type(failure).__name__))
                page.on('request',lambda request:request_urls.append(request.url))
                stage = 'native sign-in and production WorkHub data panel'
                page.goto(app,wait_until='domcontentloaded',timeout=90000)
                page.evaluate('([k,v])=>localStorage.setItem(k,v)',['freecollab.workspace:'+owner['user_id'],workspace['id']])
                page.get_by_role('navigation').get_by_role('button',name='Open App',exact=True).click()
                page.get_by_label('Email',exact=True).fill(owner['email']); page.get_by_label('Password',exact=True).fill(PASSWORD)
                page.get_by_role('button',name='Sign In',exact=True).click(); expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
                dialog=open_conversation_work(page,'data')
                panel=dialog.get_by_role('region',name='Data controls',exact=True)
                expect(panel.get_by_role('heading',name='Workspace export',exact=True)).to_be_visible()
                expect(panel.get_by_label('Preservation hold — block permanent erasure')).to_be_checked()
                results['native_owner_production_panel_and_disabled_held_default']=True
                stage = 'fresh-auth real archive creation and browser download'
                panel.get_by_label('Password',exact=True).fill(PASSWORD)
                panel.get_by_label('I understand that the downloaded archive contains readable native content and member details.').check()
                panel.get_by_role('button',name='Create private export',exact=True).click()
                expect(panel.get_by_role('button',name='Download ZIP',exact=True)).to_be_visible(timeout=60000)
                expect(panel.get_by_label('Password',exact=True)).to_have_value('')
                panel.get_by_label('Password',exact=True).fill(PASSWORD)
                with page.expect_download(timeout=45000) as event:panel.get_by_role('button',name='Download ZIP',exact=True).click()
                download=event.value
                with zipfile.ZipFile(download.path()) as archive:
                    data=json.loads(archive.read('export.json'))
                    assert any(row['content']==CONTENT for row in data['message'])
                    assert archive.read('files/'+file['id'])==b'PRIVATE DOWNLOAD BYTES'
                    assert 'password_hash' not in json.dumps(data) and PASSWORD not in json.dumps(data)
                artifacts=list((private/'files'/'.data-control').glob('*.fcdata')); assert len(artifacts)==1
                assert CONTENT.encode() not in artifacts[0].read_bytes()
                assert not list((private/'files'/'.data-control').glob('download-*.zip'))
                results['real_download_exact_zip_bytes_encrypted_storage_and_no_secret_fields']=True
                stage = 'retention save preview exact confirmation and physical purge'
                panel.get_by_label('Keep content for at least').fill('7')
                panel.get_by_label('Enable manually confirmed retention jobs').check()
                panel.get_by_label('Preservation hold — block permanent erasure').uncheck()
                panel.get_by_label('Password',exact=True).fill(PASSWORD)
                panel.get_by_role('button',name='Save retention policy',exact=True).click()
                expect(panel.get_by_text('Retention policy saved. Create a fresh preview before erasing content.',exact=True)).to_be_visible()
                panel.get_by_label('Password',exact=True).fill(PASSWORD)
                panel.get_by_role('button',name='Preview eligible content',exact=True).click()
                expect(panel.get_by_role('heading',name='Review permanent erasure',exact=True)).to_be_visible()
                expect(panel.get_by_role('button',name='Permanently erase this batch',exact=True)).to_be_disabled()
                panel.get_by_label('I understand permanent local erasure and the separate retention of backups, devices and provider copies.').check()
                panel.get_by_label('Type PURGE '+workspace['id']+' to confirm').fill('PURGE wrong')
                panel.get_by_label('Password',exact=True).fill(PASSWORD)
                expect(panel.get_by_role('button',name='Permanently erase this batch',exact=True)).to_be_disabled()
                panel.get_by_label('Type PURGE '+workspace['id']+' to confirm').fill('PURGE '+workspace['id'])
                panel.get_by_role('button',name='Permanently erase this batch',exact=True).click()
                expect(panel.locator('.fc-data-jobs li').filter(has_text='Content erasure')).to_contain_text('completed',timeout=60000)
                with sqlite3.connect(private/'proof.db') as connection:
                    assert connection.execute('SELECT count(*) FROM message WHERE id=?',(old_message['id'],)).fetchone()[0]==0
                    assert connection.execute('SELECT count(*) FROM message WHERE id=?',(fresh['id'],)).fetchone()[0]==1
                    assert connection.execute('SELECT count(*) FROM file WHERE id=?',(file['id'],)).fetchone()[0]==0
                assert not (private/'files'/file['id']).exists()
                results['real_preview_typed_confirmation_and_fk_safe_row_and_file_byte_erasure']=True
                stage = 'responsive and keyboard data controls'
                context.set_default_timeout(45000); page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
                evidence=REPORT.parent; evidence.mkdir(parents=True,exist_ok=True)
                panel.get_by_role('heading',name='Retention & permanent erasure',exact=True).scroll_into_view_if_needed()
                page.screenshot(path=str(evidence/'data-controls-mobile.png'))
                page.set_viewport_size({'width':1440,'height':1050}); panel.get_by_role('heading',name='Workspace export',exact=True).scroll_into_view_if_needed()
                page.screenshot(path=str(evidence/'data-controls-desktop.png'))
                results['mobile_layout_without_horizontal_overflow_and_desktop_evidence']=True
                stage = 'account dependency blocker handover and irreversible identity erasure'
                panel.get_by_role('button',name='Review account dependencies',exact=True).click()
                expect(panel.get_by_text('Resolve these dependencies first',exact=True)).to_be_visible()
                expect(panel.get_by_role('button',name='Permanently erase my identity',exact=True)).to_be_disabled()
                with sqlite3.connect(private/'proof.db') as connection:
                    connection.execute('INSERT INTO workspacemember(workspace_id,user_id,role,joined_at) VALUES(?,?,?,?)',(workspace['id'],peer['user_id'],'owner',datetime.utcnow().isoformat(' ')))
                    connection.execute('INSERT INTO channelmember(channel_id,user_id,role,joined_at) VALUES(?,?,?,?)',(channel['id'],peer['user_id'],'admin',datetime.utcnow().isoformat(' ')))
                panel.get_by_role('button',name='Review account dependencies',exact=True).click()
                expect(panel.get_by_text('Resolve these dependencies first',exact=True)).to_have_count(0)
                panel.get_by_label('I understand that shared authorship references, provider copies, devices and backups are retained separately.').check()
                panel.get_by_label('Type ERASE MY ACCOUNT to confirm').fill('ERASE MY ACCOUNT')
                panel.get_by_label('Password',exact=True).fill(PASSWORD)
                panel.get_by_role('button',name='Permanently erase my identity',exact=True).click()
                expect(dialog).not_to_be_visible(timeout=60000)
                with sqlite3.connect(private/'proof.db') as connection:
                    row=connection.execute('SELECT name,email,is_blocked FROM user WHERE id=?',(owner['user_id'],)).fetchone()
                    assert row[0]=='Deleted account' and row[1].endswith('@identity.invalid') and row[2]
                    assert connection.execute('SELECT author_id FROM message WHERE id=?',(fresh['id'],)).fetchone()[0]==owner['user_id']
                results['last_owner_admin_blockers_transfer_identity_erasure_and_shared_authorship_preserved']=True
                assert not page_errors
                assert not any(PASSWORD in url for url in request_urls)
                results['no_page_errors_or_authentication_secrets_in_urls']=True
                context.close(); browser.close()
            REPORT.parent.mkdir(parents=True,exist_ok=True)
            REPORT.write_text(json.dumps({'ok':True,'results':results},indent=2)+'\n')
            print(json.dumps({'ok':True,'checks':list(results),'report':str(REPORT)}))
        except Exception as failure:
            print(json.dumps({'ok':False,'stage':stage,'error_type':type(failure).__name__}),file=sys.stderr)
            # Fixture logs can contain synthetic bodies/tokens: expose no raw log.
            raise AssertionError('Private data-controls proof failed at '+stage) from None
        finally:
            helpers.stop(processes)
            for stream in streams:stream.close()

if __name__=='__main__':main()
