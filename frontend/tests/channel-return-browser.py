#!/usr/bin/env python3
"""Actual App return summary, receipt snapshot and exact authorized source links.

Uses disposable native API/Vite processes and real production handlers. The one
delivery gate holds a genuine GET across a visible channel switch. No business
response, product route, receipt handler or resource projection is substituted.
"""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from ui_navigation import choose_chat_channel, close_channels, close_work_card, open_chat_tool, saved_document_reading

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('channel_return_helpers', Path(__file__).with_name('customer-sso-browser.py'))
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
PYTHON = os.getenv('BACKEND_PYTHON') or str(ROOT / 'backend/.venv/bin/python')
REPORT = Path(os.getenv('CHANNEL_RETURN_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/channel-return-browser.json')))


def button(summary, text):
    return summary.get_by_role('button').filter(has=summary.page.get_by_text(text, exact=True))


def main():
    expect.set_options(timeout=15000)
    checks, processes, streams, stage = {}, [], [], 'private startup'
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='freecollab-channel-return-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = p.port(), p.port()
            base, app = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
            database = private / 'proof.db'; environment = os.environ.copy()
            environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(database), STORAGE_PATH=str(private/'files'),
                STORAGE_BACKEND='local', REDIS_URL='', JWT_SECRET=secrets.token_urlsafe(48),
                ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), ACCOUNT_SECURITY_KEY_FILE=str(private/'account.key'),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',
                MESSAGE_ENCRYPTION_KEY_FILE=str(private/'message.keys'), PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]), MATRIX_ENABLED='false',
                MAIL_DELIVERY_MODE='disabled', OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', SYNC_ENABLED='false', OTRUST_ENABLED='false',
                COPILOT_ENABLED='false', CLOUD_FILES_ENABLED='false', EXTERNAL_MEETINGS_ENABLED='false', PYTHONPATH=str(ROOT/'backend'))
            launcher = private/'api.py'
            launcher.write_text('from app.main import socket_app\nimport uvicorn\nuvicorn.run(socket_app,host="127.0.0.1",port='+str(api_port)+',access_log=False,proxy_headers=False)\n')
            vite = p.create_vite(private, base, app_port)
            vite.write_text(vite.read_text().replace('server:{host:', 'server:{fs:{allow:' + json.dumps([str(private/'frontend'),str(ROOT/'frontend')]) + '},host:'))
            for command, name, cwd in [([PYTHON,str(launcher)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',ROOT/'frontend')]:
                stream = (private/name).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            p.ready(base+'/ready',processes); p.ready(app+'/',processes)
            owner = p.native_user(base,database,'Return Reader'); peer = p.native_user(base,database,'Return Colleague')
            workspace = p.api(base,'POST','/workspaces',owner,{'name':'Return studio'})
            invite = p.api(base,'POST','/workspaces/'+workspace['id']+'/invites',owner,{'role':'member'})
            p.api(base,'POST','/workspaces/invites/accept',peer,{'token':invite['token']})
            home = p.api(base,'POST','/channels',owner,{'name':'Quiet starting point','workspace_id':workspace['id']})
            channel = p.api(base,'POST','/channels',owner,{'name':'Work while you were away','workspace_id':workspace['id'],'member_ids':[peer['user_id']]})
            channel_id = channel['id']
            updates = json.loads(subprocess.check_output(['node','--input-type=module','-e',
                "import * as Y from 'yjs';const d=new Y.Doc();d.getText('title').insert(0,'Updated return plan');d.getText('body').insert(0,'Original shared passage.');const first=Y.encodeStateAsUpdate(d),vector=Y.encodeStateVector(d);d.getText('body').insert(d.getText('body').length,' A real peer update.');console.log(JSON.stringify({first:Buffer.from(first).toString('base64'),second:Buffer.from(Y.encodeStateAsUpdate(d,vector)).toString('base64')}));d.destroy();"],cwd=ROOT/'frontend',text=True))
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or '/usr/bin/chromium',headless=True,args=['--no-sandbox'])
                context = browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce',locale='en-US')
                page = context.new_page(); page.set_default_timeout(45000); errors = []
                page.on('pageerror',lambda failure:errors.append(str(failure)))
                p.login(page,app,owner,workspace); choose_chat_channel(page,home['name']); close_channels(page)
                stage = 'actual previous read and peer changes'
                root = p.api(base,'POST','/messages/channels/'+channel_id,owner,{'content':'The previous reading position.'})
                p.api(base,'POST','/channels/'+channel_id+'/shared-documents',peer,{'id':'return-document','update_id':'initial','title':'Updated return plan','update_base64':updates['first']})
                p.api(base,'POST','/read-receipts/channels/'+channel_id,owner,{'message_id':root['id']})
                original = next(row for row in p.api(base,'GET','/read-receipts/channels/'+channel_id,owner) if row['user_id']==owner['user_id'])
                decision = p.api(base,'POST','/channels/'+channel_id+'/decisions',peer,{'title':'A reviewed return decision','content':'Keep the agreed release scope.','status':'accepted'})
                p.api(base,'POST','/channels/'+channel_id+'/shared-documents/return-document/updates',peer,{'update_id':'peer-change','title':'Updated return plan','update_base64':updates['second']})
                p.api(base,'POST','/channels/'+channel_id+'/projects',owner,{'id':'return-project','fields':{'title':'Return project','description':'','color':'sage'}})
                p.api(base,'POST','/channels/'+channel_id+'/projects/return-project/tasks',peer,{'id':'return-task','fields':{'title':'A current open assignment','description':'Assigned explicitly.','status':'doing','priority':'normal','assignee_user_id':owner['user_id'],'due_date':None,'checklist':[],'file_ids':[],'decision_ids':[]}})
                mention_text = '@'+owner['name']+' review the current plan'
                mention = p.api(base,'POST','/messages/channels/'+channel_id,peer,{'content':mention_text,'references':[{'kind':'person','id':owner['user_id'],'start':0,'end':len(owner['name'])+1}]})
                reply = p.api(base,'POST','/threads/messages/'+root['id']+'/reply',peer,{'content':'The requested reply is ready.'})
                newest = p.api(base,'POST','/messages/channels/'+channel_id,peer,{'content':'Return Reader, a question? This prose has no selected mention.'})
                stage = 'real Chat entry summary'
                choose_chat_channel(page,channel['name']); close_channels(page)
                expect(page.get_by_role('region',name='Since your last read',exact=True)).to_be_visible()
                summary = page.locator('.fc-channel-return')
                summary.locator('summary').click()
                expect(summary.locator('summary')).to_contain_text('Decisions: 1')
                expect(summary.locator('summary')).to_contain_text('Updated documents: 1')
                expect(summary.locator('summary')).to_contain_text('For your attention: 3')
                expect(button(summary,'A reviewed return decision')).to_be_visible()
                expect(button(summary,'Updated return plan')).to_be_visible()
                checks['real_entry_counts_latest_decision_changed_existing_yjs_doc_and_three_explicit_attention_sources'] = True
                # A second device/normal reader may advance the real receipt;
                # the return panel retains the original entry snapshot.
                p.api(base,'POST','/read-receipts/channels/'+channel_id,owner,{'message_id':newest['id']})
                expect(summary.locator('summary')).to_contain_text('For your attention: 3')
                assert original['last_read_message_id'] == root['id']
                checks['captured_read_baseline_survives_real_receipt_advancement'] = True
                before = next(row for row in p.api(base,'GET','/read-receipts/channels/'+channel_id,owner) if row['user_id']==owner['user_id'])
                summary.locator('summary').click(); summary.locator('summary').click()
                after = next(row for row in p.api(base,'GET','/read-receipts/channels/'+channel_id,owner) if row['user_id']==owner['user_id'])
                assert after == before
                checks['opening_and_closing_return_preview_does_not_write_receipts'] = True
                page.screenshot(path=str(REPORT.with_name(REPORT.stem+'-entry.png')))
                stage = 'exact decision document task message and thread links'
                button(summary,'A reviewed return decision').click()
                expect(page.locator('.fc-decision-card').filter(has=page.get_by_role('heading',name='A reviewed return decision',exact=True))).to_be_visible()
                close_work_card(page)
                button(summary,'Updated return plan').click()
                saved_document_reading(page, title='Updated return plan',text='Original shared passage. A real peer update.')
                close_work_card(page)
                button(summary,'A current open assignment').click()
                expect(page.locator('.fc-project-card[data-task-id="return-task"]')).to_be_visible()
                expect(page.locator('.fc-project-card[data-task-id="return-task"]').get_by_role('heading',name='A current open assignment',exact=True)).to_be_visible()
                close_work_card(page)
                button(summary,'You were mentioned').click()
                expect(page.locator('[data-message-source-id='+json.dumps(mention['id'])+']').first).to_be_visible()
                button(summary,'Reply to your message').click()
                expect(page.locator('[data-thread-reply='+json.dumps(reply['id'])+']')).to_be_visible()
                page.get_by_role('button',name='Close thread',exact=True).click()
                checks['all_five_links_open_exact_current_authorized_resources_in_actual_app'] = True
                stage = 'personal language and bounded mobile layout'
                for width in (1440,390,320):
                    page.set_viewport_size({'width':width,'height':950})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    expect(page.locator('.fc-chat-composer textarea')).to_be_visible()
                    assert page.locator('.fc-chat-composer textarea').evaluate('node=>{const r=node.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight+1}')
                    assert summary.evaluate('node=>node.getBoundingClientRect().height<=innerHeight*.45+2')
                    page.screenshot(path=str(REPORT.with_name(REPORT.stem+'-en-'+str(width)+'.png')))
                open_chat_tool(page,'Account settings'); page.get_by_label('Language',exact=True).select_option('sv')
                expect(page.locator('.fc-language-status')).to_have_text('Språket har sparats.')
                page.locator('.fc-account-panel .fc-account-header button').click()
                expect(page.get_by_role('region',name='Sedan du läste senast',exact=True)).to_be_visible()
                expect(summary.locator('summary')).to_contain_text('För din uppmärksamhet: 3')
                page.screenshot(path=str(REPORT.with_name(REPORT.stem+'-sv-320.png')))
                open_chat_tool(page,'Kontoinställningar'); page.get_by_label('Språk',exact=True).select_option('en')
                expect(page.locator('.fc-language-status')).to_have_text('Language saved.')
                page.locator('.fc-account-panel .fc-account-header button').click()
                checks['en_sv_320_390_1440_summary_stays_bounded_and_leaves_composer_visible'] = True
                stage = 'genuine delayed summary across channel switch'
                choose_chat_channel(page,home['name']); close_channels(page)
                p.api(base,'POST','/channels/'+channel_id+'/decisions',peer,{'title':'A later exact decision','content':'Only in the returning channel.','status':'proposed'})
                held = {}
                def gate(route):
                    if not held and route.request.method == 'GET':
                        held['route'] = route; held['response'] = route.fetch()
                    else: route.continue_()
                pattern = '**/api/read-receipts/channels/'+channel_id+'/return-summary*'
                page.route(pattern,gate)
                choose_chat_channel(page,channel['name']); close_channels(page)
                deadline = time.monotonic()+20
                while 'response' not in held:
                    assert time.monotonic()<deadline,'The real summary request was not captured'
                    page.wait_for_timeout(25)
                assert held['response'].ok
                choose_chat_channel(page,home['name']); close_channels(page)
                held['route'].fulfill(response=held['response']); page.unroute(pattern,gate)
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                expect(page.get_by_role('region',name='Since your last read',exact=True)).to_have_count(0)
                expect(page.get_by_text('A later exact decision',exact=True)).to_have_count(0)
                checks['late_genuine_summary_is_discarded_after_visible_channel_switch'] = True
                assert not errors, errors
                browser.close()
                stage = 'passed'
        except Exception:
            for name in ['api.log', 'vite.log']:
                if (private/name).is_file(): shutil.copyfile(private/name, REPORT.with_name(REPORT.stem+'.'+name))
            raise
        finally:
            p.stop(processes)
            for stream in streams: stream.close()
            source = ['frontend/src/components/Chat.tsx','frontend/src/components/ChannelReturnSummary.tsx',
                'frontend/src/lib/channel-return-summary.ts','frontend/src/lib/channel-return-loader.ts',
                'frontend/src/lib/encrypted-work-api.ts','frontend/src/lib/shared-documents-api.ts',
                'frontend/src/lib/message-navigation.ts','frontend/src/lib/secure/index.ts',
                'frontend/src/styles/channel-return-summary.css','frontend/src/i18n/channel-return.sv.ts',
                'backend/app/api/read_receipts.py','backend/app/services/channel_return.py','frontend/tests/channel-return-browser.py']
            bindings = {name:hashlib.sha256(((private/'frontend'/Path(name).relative_to('frontend')) if name.startswith('frontend/') and not name.startswith('frontend/tests/') else ROOT/name).read_bytes()).hexdigest() for name in source}
            REPORT.write_text(json.dumps({'stage':stage,'checks':checks,'source_sha256':bindings,
                'limits':['Native full-App browser proof only; secure records, read-only storage and malformed/locked gaps have separate focused transport cases.',
                          'The delivery gate holds a genuine unchanged authorized response; it does not synthesize business results.',
                          'Only disposable accounts, SQLite/key files, API/Vite processes and browser context were used and removed.']},indent=2)+'\n')
    print('PASS: '+str(len(checks))+' actual channel-return App groups',flush=True)


if __name__=='__main__': main()
