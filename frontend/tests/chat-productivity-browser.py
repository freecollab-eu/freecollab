#!/usr/bin/env python3
"""Private real-app proof: group DMs, durable sends, reminders and device queues."""
from ui_navigation import choose_chat_channel, open_chat_tool, reveal_message_actions
import base64
from datetime import datetime, timedelta
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import shutil
import tempfile

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('productivity_helpers', Path(__file__).with_name('customer-sso-browser.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
REPORT = Path(os.getenv('CHAT_PRODUCTIVITY_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/chat-productivity-browser.json')))
PYTHON = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)


def main():
    checks, processes, streams, stage = {}, [], [], 'private startup'
    with tempfile.TemporaryDirectory(prefix='freecollab-chat-productivity-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = p.port(), p.port()
            base, app = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
            database = private / 'proof.db'
            environment = os.environ.copy()
            environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(database), STORAGE_PATH=str(private / 'files'),
                STORAGE_BACKEND='local', REDIS_URL='', JWT_SECRET=secrets.token_urlsafe(48),
                ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), ACCOUNT_SECURITY_KEY_FILE=str(private / 'account.key'),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',
                MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'keys.json'), PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]), MATRIX_ENABLED='false',
                MAIL_DELIVERY_MODE='disabled', OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', SYNC_ENABLED='false', OTRUST_ENABLED='false',
                COPILOT_ENABLED='false', CLOUD_FILES_ENABLED='false', EXTERNAL_MEETINGS_ENABLED='false', PYTHONPATH=str(ROOT / 'backend'))
            launcher = private / 'api.py'
            launcher.write_text('from app.main import socket_app\nimport uvicorn\nuvicorn.run(socket_app,host="127.0.0.1",port=' + str(api_port) + ',access_log=False,proxy_headers=False)\n')
            vite = p.create_vite(private, base, app_port)
            for command, name, cwd in [([PYTHON,str(launcher)],'api.log',ROOT / 'backend'),(['node',str(vite)],'vite.log',ROOT / 'frontend')]:
                stream = (private / name).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            p.ready(base + '/ready', processes); p.ready(app + '/', processes)
            owner = p.native_user(base,database,'Productivity Owner')
            peers = [p.native_user(base,database,'Productivity Peer '+str(index)) for index in range(2)]
            workspace = p.api(base,'POST','/workspaces',owner,{'name':'Productivity studio'})
            for peer in peers:
                invite = p.api(base,'POST','/workspaces/'+workspace['id']+'/invites',owner,{'role':'member'})
                p.api(base,'POST','/workspaces/invites/accept',peer,{'token':invite['token']})
            channel = p.api(base,'POST','/channels',owner,{'name':'Thoughtful work','workspace_id':workspace['id'],'member_ids':[peer['user_id'] for peer in peers]})
            text = 'A real scheduled message, delivered after its browser closed the editor.'
            def worker():
                subprocess.run([PYTHON,'-m','app.services.integration_worker','--once'],cwd=ROOT/'backend',env=environment,
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=45)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
                context = browser.new_context(viewport={'width':1440,'height':1080},reduced_motion='reduce')
                page = context.new_page(); page.set_default_timeout(40000)
                errors, requests = [], []
                page.on('pageerror',lambda failure:errors.append(str(failure)))
                page.on('request',lambda request:requests.append(request.url))
                p.login(page,app,owner,workspace)
                stage = 'actual group conversation creation'
                open_chat_tool(page, 'Direct messages')
                direct = page.get_by_role('dialog',name='Direct messages',exact=True)
                direct.get_by_role('button',name='New group conversation',exact=True).click()
                for peer in peers:
                    direct.get_by_role('checkbox',name=peer['name'],exact=True).check()
                direct.get_by_role('button',name='Open group conversation',exact=True).click()
                expect(direct).to_have_count(0)
                group = p.api(base,'GET','/group-conversations?workspace_id='+workspace['id'],owner)[0]
                assert len(group['participants']) == 3
                checks['actual_group_picker_creates_private_three_person_conversation'] = True
                choose_chat_channel(page, 'Thoughtful work')
                composer = page.get_by_role('textbox',name='Message #Thoughtful work',exact=True)
                stage = 'actual scheduled send and encrypted database record'
                composer.fill(text)
                page.get_by_role('button',name='Schedule & reminders',exact=True).click()
                panel = page.get_by_role('dialog',name='Scheduled messages and reminders',exact=True)
                panel.get_by_role('button',name='Schedule message',exact=True).click()
                expect(panel).to_have_count(0); expect(composer).to_have_value('')
                with sqlite3.connect(database) as connection:
                    rows = connection.execute('SELECT id,encrypted_content,claims_json FROM scheduledmessage').fetchall()
                    assert len(rows) == 1 and text not in rows[0][1]
                    assert 'access_token' not in rows[0][2] and 'Bearer' not in rows[0][2]
                    scheduled_id = rows[0][0]
                    connection.execute('UPDATE scheduledmessage SET due_at=? WHERE id=?',((datetime.utcnow()-timedelta(seconds=2)).isoformat(' '),scheduled_id))
                worker()
                message = page.locator('#message-'+scheduled_id)
                expect(message).to_contain_text(text,timeout=20000)
                checks['durable_worker_send_updates_real_connected_chat_without_redis'] = True
                checks['scheduled_body_encrypted_and_only_session_digest_metadata_persisted'] = True
                stage = 'personal reminder and real notification target opening'
                reveal_message_actions(message); message.get_by_role('button',name='Remind me about this message',exact=True).click()
                panel.get_by_role('button',name='Remind me',exact=True).click()
                expect(panel.get_by_text('Reminder saved.',exact=True)).to_be_visible()
                panel.get_by_role('button',name='Close scheduling',exact=True).click()
                with sqlite3.connect(database) as connection:
                    reminder_id = connection.execute('SELECT id FROM messagereminder').fetchone()[0]
                    connection.execute('UPDATE messagereminder SET due_at=? WHERE id=?',((datetime.utcnow()-timedelta(seconds=2)).isoformat(' '),reminder_id))
                worker()
                page.get_by_role('button',name='Notifications',exact=True).click()
                notice = page.locator('.fc-notification-item').filter(has_text='Your message reminder')
                expect(notice).to_have_count(1,timeout=20000); notice.click()
                expect(page.get_by_role('dialog',name='Notifications',exact=True)).to_have_count(0)
                assert any('/reminders/'+reminder_id+'/target' in request for request in requests)
                checks['generic_personal_reminder_notification_opens_reauthorized_exact_message'] = True
                stage = 'private saved completion persists'
                reveal_message_actions(message); message.get_by_role('button',name='Save message',exact=True).click()
                open_chat_tool(page, 'Saved items and decisions')
                notebook = page.get_by_role('dialog',name='Channel notebook',exact=True)
                notebook.get_by_role('button',name='Saved',exact=True).click()
                notebook.get_by_role('button',name='Mark complete',exact=True).click()
                expect(notebook.get_by_role('button',name='Completed',exact=True)).to_have_attribute('aria-pressed','true')
                assert p.api(base,'GET','/bookmarks',owner)[0]['completed_at']
                assert p.api(base,'GET','/bookmarks',peers[0]) == []
                notebook.get_by_role('button',name='Close channel notebook',exact=True).click()
                checks['saved_completion_is_durable_and_private'] = True
                stage = 'encrypted trusted-device schedule vault and no early send'
                local_text = 'Private on-device scheduled context '+secrets.token_hex(8)
                local_id = page.evaluate('''async ({channelId,text})=>{
                  const {mobileOutbox}=await import('/src/mobile/index.ts');
                  const row=await mobileOutbox.schedule(channelId,text,[],new Date(Date.now()+3600000).toISOString());
                  await mobileOutbox.flush();return row.id;
                }''',{'channelId':channel['id'],'text':local_text})
                raw = page.evaluate('''()=>new Promise((resolve,reject)=>{const open=indexedDB.open('freecollab-mobile-v1');open.onsuccess=()=>{const db=open.result,r=db.transaction('vault').objectStore('vault').getAll();r.onsuccess=()=>{resolve(r.result.map(row=>({id:row.id,kind:row.kind,bytes:[...new Uint8Array(row.ciphertext)]})));db.close();};r.onerror=reject;};open.onerror=reject;})''')
                assert any(local_id in row['id'] and row['kind']=='outbox' for row in raw)
                assert local_text not in json.dumps(raw)
                assert not any(record['content']==local_text for record in p.api(base,'GET','/messages/channels/'+channel['id'],owner))
                checks['device_schedule_uses_real_aes_gcm_vault_and_never_sends_before_due'] = True
                stage = 'recover an actual missed websocket message from the native cursor'
                page.evaluate("async()=>{const {getSocket}=await import('/src/lib/socket.ts');getSocket().removeAllListeners('message');}")
                missed='Recovered from the durable cursor '+secrets.token_hex(6)
                record=p.api(base,'POST','/messages/channels/'+channel['id'],peers[0],{'content':missed})
                expect(page.locator('#message-'+record['id'])).to_contain_text(missed,timeout=22000)
                checks['missed_live_event_recovered_from_authorized_bounded_native_cursor']=True
                stage = 'narrow scheduling layout'
                page.set_viewport_size({'width':320,'height':800})
                page.get_by_role('button',name='Schedule & reminders',exact=True).click()
                expect(panel).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                panel.get_by_role('button',name='Close scheduling',exact=True).click()
                assert not errors, 'Browser runtime errors: '+','.join(errors)
                checks['320px_scheduling_without_horizontal_overflow_or_runtime_errors'] = True
                browser.close()
        except Exception:
            print('Productivity proof failed during '+stage,flush=True)
            for name in ['api.log','vite.log']:
                path=private/name
                if path.exists():print(path.read_text()[-3500:],flush=True)
            raise
        finally:
            p.stop(processes)
            for stream in streams:stream.close()
            REPORT.parent.mkdir(parents=True,exist_ok=True)
            REPORT.write_text(json.dumps({'stage':stage,'checks':checks},indent=2))
    print(json.dumps(checks,indent=2),flush=True)


if __name__=='__main__':main()
