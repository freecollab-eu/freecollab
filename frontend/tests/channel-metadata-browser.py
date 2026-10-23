#!/usr/bin/env python3
"""Real authenticated channel details and translated Canvas controls.

Private API/Vite/SQLite/source snapshot; never changes an existing installation.
Matrix wire/client authority is covered separately, not accepted by this native proof.
"""
import base64
import hashlib
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
from playwright.sync_api import expect, sync_playwright
from ui_navigation import open_chat_tool, choose_chat_channel
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('metadata_helpers',Path(__file__).with_name('shared-documents-browser.py'))
doc=importlib.util.module_from_spec(spec);spec.loader.exec_module(doc);helpers=doc.helpers
REPORT=Path(os.getenv('CHANNEL_METADATA_PROOF_REPORT',str(ROOT.parent/'.freecollab/proof-results/feature-audit-20261023/channel-metadata-browser.json')))


def main():
    results={};processes=[];streams=[];stage='private services startup'
    with tempfile.TemporaryDirectory(prefix='freecollab-channel-metadata-') as directory:
        private=Path(directory)
        try:
            api_port,app_port=helpers.port(),helpers.port();base='http://127.0.0.1:'+str(api_port);origin='http://127.0.0.1:'+str(app_port)
            environment=os.environ.copy();environment.update(ENVIRONMENT='test',DATABASE_URL='sqlite:///'+str(private/'proof.db'),STORAGE_PATH=str(private/'files'),JWT_SECRET=secrets.token_urlsafe(48),ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',MESSAGE_ENCRYPTION_KEY_FILE=str(private/'message-keys.json'),ACCOUNT_SECURITY_KEY_FILE=str(private/'account.key'),MATRIX_ENABLED='false',MAIL_DELIVERY_MODE='disabled',PUBLIC_APP_URL=origin,CORS_ORIGINS=json.dumps([origin]),OIDC_ENABLED='false',OTRUST_ENABLED='false',SYNC_ENABLED='false',COPILOT_ENABLED='false',REDIS_URL='',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(ROOT/'backend'))
            environment['NO_PROXY']=','.join(filter(None,[environment.get('NO_PROXY',''),'localhost','127.0.0.1','::1']))
            python=os.getenv('BACKEND_PYTHON') or (str(ROOT/'backend/.venv/bin/python') if (ROOT/'backend/.venv/bin/python').is_file() else sys.executable)
            server=private/'api.py';server.write_text('import uvicorn\nfrom app.main import socket_app\nuvicorn.run(socket_app,host="127.0.0.1",port='+str(api_port)+',proxy_headers=False,access_log=False)\n')
            frontend=private/'frontend';shutil.copytree(ROOT/'frontend',frontend,ignore=shutil.ignore_patterns('node_modules','dist','__pycache__','.git'));(frontend/'node_modules').symlink_to(ROOT/'frontend/node_modules',target_is_directory=True)
            source_hashes={name:hashlib.sha256((frontend/name).read_bytes()).hexdigest() for name in ['src/components/Chat.tsx','src/components/ChannelMetadataPanel.tsx','src/components/ChannelTools.tsx','src/lib/channel-metadata.ts','src/lib/secure/index.ts','src/appearance/AppearancePanel.tsx','src/i18n/channel-metadata.sv.ts']}
            vite=helpers.vite_launcher(private,base,app_port);vite.write_text(vite.read_text().replace('root:'+json.dumps(str(ROOT/'frontend')),'root:'+json.dumps(str(frontend))).replace(json.dumps(str(private/'vite-cache')),json.dumps(str(frontend/'.vite-cache'))).replace('server:{host:','server:{fs:{allow:'+json.dumps([str(private),str(ROOT/'frontend')])+'},host:'))
            for command,name,cwd in [([python,str(server)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',frontend)]:
                stream=(private/name).open('w');streams.append(stream);processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            helpers.ready(base+'/ready',processes);helpers.ready(origin+'/',processes)
            users=[];emails=[]
            for name in ['Metadata author','Metadata peer']:
                email='metadata-'+secrets.token_hex(6)+'@example.com';emails.append(email);users.append(helpers.api(base,'POST','/auth/register',data={'name':name,'email':email,'password':doc.PASSWORD}))
            owner,peer=users;workspace=helpers.api(base,'POST','/workspaces',owner,{'name':'Studio Nord'})
            channel=helpers.api(base,'POST','/channels',owner,{'name':'Research','workspace_id':workspace['id']})['id']
            other=helpers.api(base,'POST','/channels',owner,{'name':'Other room','workspace_id':workspace['id']})['id']
            invite=helpers.api(base,'POST','/channels/'+channel+'/invites',owner,{});helpers.api(base,'POST','/channels/invites/accept',peer,{'token':invite['token']})
            endpoint='/channels/'+channel+'/metadata'
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),args=['--no-sandbox']);context=browser.new_context(viewport={'width':1440,'height':1000},locale='en-US',reduced_motion='reduce');page=context.new_page();page.set_default_timeout(30000);errors=[];page.on('pageerror',lambda failure:errors.append(type(failure).__name__))
                try:
                    stage='real sign-in and details discovery';doc.login(page,origin,emails[0],workspace['id'],owner['user_id']);page.get_by_role('button',name='Close workspace tools',exact=True).click();choose_chat_channel(page,'Research')
                    trigger=page.get_by_role('button',name='More channel tools',exact=True)
                    def details():
                        open_chat_tool(page,'Channel details');panel=page.get_by_role('dialog',name='Manage channel',exact=True);expect(panel.get_by_label('Channel name',exact=True)).to_be_editable();return panel
                    panel=details();expect(panel.get_by_role('button',name='Close channel details',exact=True)).to_be_focused()
                    for key in ['Shift+Tab','Tab','Tab']:
                        page.keyboard.press(key);assert panel.evaluate('node=>node.contains(document.activeElement)')
                    panel.get_by_label('Channel name',exact=True).fill('Discarded name');panel.get_by_role('button',name='Cancel',exact=True).click();expect(trigger).to_be_focused();assert helpers.api(base,'GET',endpoint,owner)['version']==0
                    results['visible_tools_entry_keyboard_modal_focus_and_cancel_without_write']=True
                    stage='explicit native save and persistent details';panel=details();panel.get_by_label('Channel name',exact=True).fill('Nord studio');panel.get_by_label('Channel description',exact=True).fill('A calm room for useful work.');panel.get_by_role('button',name='Save channel',exact=True).click();expect(panel).to_have_count(0);expect(page.locator('#chat-channel-name')).to_have_text('Nord studio');saved=helpers.api(base,'GET',endpoint,peer);assert saved['version']==1 and saved['description']=='A calm room for useful work.'
                    page.reload(wait_until='domcontentloaded');expect(page.locator('#chat-channel-name')).to_have_text('Nord studio');panel=details();expect(panel.get_by_label('Channel description',exact=True)).to_have_value(saved['description']);page.keyboard.press('Escape');expect(trigger).to_be_focused()
                    with sqlite3.connect(private/'proof.db') as db:
                        payload=db.execute('SELECT encrypted_payload FROM channelmetadata WHERE channel_id=?',(channel,)).fetchone()[0];assert 'A calm room' not in payload
                    results['acknowledged_save_header_refresh_reload_member_read_and_sealed_native_description']=True
                    stage='stale edits preserve draft until deliberate reload';panel=details();panel.get_by_label('Channel description',exact=True).fill('Keep this unsaved draft')
                    helpers.api(base,'PATCH',endpoint,owner,{'expected_version':1,'name':'Nord studio','description':'A newer shared description'})
                    panel.get_by_role('button',name='Save channel',exact=True).click();expect(panel.get_by_role('alert')).to_contain_text('reload before saving');expect(panel.get_by_label('Channel description',exact=True)).to_have_value('Keep this unsaved draft');expect(panel.get_by_role('alert')).to_contain_text('Your changes are still here.')
                    panel.get_by_role('button',name='Reload channel details',exact=True).click();expect(panel.get_by_label('Channel description',exact=True)).to_have_value('A newer shared description');page.keyboard.press('Escape')
                    results['compare_and_swap_rejection_keeps_draft_and_explicit_reload_gets_new_revision']=True
                    stage='members entry reaches the same real editor';open_chat_tool(page,'Channel members');members=page.get_by_role('dialog',name='Channel members',exact=True);members.get_by_role('button',name='Manage channel',exact=True).click();panel=page.get_by_role('dialog',name='Manage channel',exact=True);expect(panel.get_by_label('Channel name',exact=True)).to_have_value('Nord studio');page.keyboard.press('Escape');results['members_panel_manage_entry_reachable']=True
                    stage='member reads details without admin mutation';peer_context=browser.new_context(viewport={'width':1024,'height':900});peer_context.add_init_script('localStorage.setItem("freecollab_token",'+json.dumps(peer['access_token'])+');localStorage.setItem("freecollab_user_id",'+json.dumps(peer['user_id'])+');localStorage.setItem('+json.dumps('freecollab.workspace:'+peer['user_id'])+','+json.dumps(workspace['id'])+')');member_page=peer_context.new_page();member_page.goto(origin+'/#app',wait_until='domcontentloaded')
                    # App routes through its public navigation when no route fragment is known.
                    if member_page.get_by_role('navigation').get_by_role('button',name='Open App',exact=True).count():member_page.get_by_role('navigation').get_by_role('button',name='Open App',exact=True).click()
                    open_chat_tool(member_page,'Channel details');read=member_page.get_by_role('dialog',name='Channel details',exact=True);expect(read.get_by_label('Channel name',exact=True)).to_have_attribute('readonly','');expect(read.get_by_role('button',name='Save channel',exact=True)).to_have_count(0);expect(read).to_contain_text('Only a channel administrator');peer_context.close();results['ordinary_member_details_are_read_only']=True
                    stage='Swedish mobile details and appearance canvas translation';helpers.api(base,'PUT','/preferences/language',owner,{'locale':'sv'});page.reload(wait_until='domcontentloaded');page.set_viewport_size({'width':320,'height':950});open_chat_tool(page,'Kanaluppgifter');panel=page.get_by_role('dialog',name='Hantera kanal',exact=True);expect(panel.get_by_label('Kanalbeskrivning',exact=True)).to_have_value('A newer shared description');assert panel.evaluate('node=>node.scrollWidth<=node.clientWidth+1');assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1');page.keyboard.press('Escape');expect(page.get_by_role('button',name='Fler kanalverktyg',exact=True)).to_be_focused();results['swedish_320px_form_literal_content_focus_restore_and_no_overflow']=True
                    open_chat_tool(page,'Utseendeinställningar');appearance=page.get_by_role('dialog',name='Gör utrymmet till ditt.',exact=True);expect(appearance.get_by_label('Ljus bakgrund, hexadecimal färgkod',exact=True)).to_be_visible();appearance.get_by_role('radio',name='Mörkt',exact=True).check();expect(appearance.get_by_label('Mörk bakgrund, hexadecimal färgkod',exact=True)).to_be_visible();assert appearance.get_by_text('Canvas · dark',exact=True).count()==0;results['appearance_canvas_labels_localize_in_both_light_and_dark_modes']=True
                    REPORT.parent.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(REPORT.with_suffix('.png')));assert not errors;print('PASS channel metadata: '+str(len(results))+' real browser groups',flush=True)
                finally:context.close();browser.close()
        except Exception as failure:
            detail=re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+','[redacted]',str(failure).replace(doc.PASSWORD,'[redacted]'));print('FAIL channel metadata at '+stage+': '+type(failure).__name__+' '+detail[:2200],file=sys.stderr,flush=True)
            for name in ['api.log','vite.log']:
                if (private/name).exists():print(name+':\n'+(private/name).read_text()[-1500:],file=sys.stderr)
            raise SystemExit(1) from None
        finally:
            helpers.stop(processes)
            for stream in streams:stream.close()
    REPORT.parent.mkdir(parents=True,exist_ok=True);REPORT.write_text(json.dumps({'scope':'Actual isolated native API/Vite/Chromium; Matrix authority unit/wire tests are separate, no live homeserver acceptance','results':results,'source_hashes':source_hashes,'cleanup':'Own services, DB, keys, contexts removed in finally'},indent=2)+'\n')
if __name__=='__main__':main()
