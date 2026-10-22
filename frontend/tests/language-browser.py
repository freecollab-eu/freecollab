#!/usr/bin/env python3
"""Native Swedish/English preference and stale-response proof.

Starts private production API/Socket.IO and Vite services with a snapshot of the
production App, LocaleProvider, LanguagePicker and Chat. Fixture sessions belong
only to its private database. Latency middleware delays REAL API responses after
their production handlers ran; no language/data/composer API is mocked.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PYTHON = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)
REPORT = Path(os.getenv('LANGUAGE_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/language-browser.json')))


def port():
    with socket.socket() as server: server.bind(('127.0.0.1', 0)); return server.getsockname()[1]


def ready(url, processes):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        assert all(process.poll() is None for process in processes), 'Private language proof service failed to start'
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200: return
        except Exception: time.sleep(.15)
    raise AssertionError('Private language proof readiness timed out')


def saved(base, token):
    with urlopen(Request(base + '/preferences/language', headers={'Authorization':'Bearer ' + token}), timeout=10) as response:
        assert 'no-store' in response.headers.get('Cache-Control','')
        return json.load(response)['locale']


def native(base, token, method, path, body=None):
    request=Request(base+path,method=method,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},data=json.dumps(body).encode()if body is not None else None)
    with urlopen(request,timeout=15)as response:
        value=response.read();return json.loads(value)if value else None


def wait_file(page, path):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.exists(): return
        page.wait_for_timeout(50)
    raise AssertionError('The real native delayed request was not captured')


def main():
    checks, stage, processes, logs = {}, 'private source snapshot and services', [], []
    with tempfile.TemporaryDirectory(prefix='freecollab-language-') as directory:
        private = Path(directory); web = private / 'web'; web.mkdir()
        source = web / 'src'; shutil.copytree(ROOT / 'frontend/src', source)
        shutil.copytree(ROOT/'frontend/public',web/'public');shutil.copyfile(ROOT/'frontend/postcss.config.js',web/'postcss.config.js');shutil.copyfile(ROOT/'frontend/tailwind.config.js',web/'tailwind.config.js')
        (web/'package.json').write_text('{"type":"module"}');(web/'node_modules').symlink_to(ROOT/'frontend/node_modules',target_is_directory=True)
        # Static snapshot prevents unrelated teams' live catalog writes triggering
        # HMR reloads while measuring draft retention. Implementation is unedited.
        source_hashes = {name:hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in ['frontend/src/i18n/index.tsx','frontend/src/i18n/core.ts','frontend/src/components/Chat.tsx','frontend/src/styles/chat.css','frontend/src/i18n/mobile-toolbar.sv.ts','frontend/src/App.tsx','frontend/src/components/WorkHub.tsx','frontend/src/styles/work-hub.css','backend/app/api/preferences.py']}
        api_port, app_port = port(), port(); base, origin = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
        def controls(**values):
            temporary = private / 'controls.next'; temporary.write_text(json.dumps(values)); temporary.replace(private / 'controls.json')
        controls()
        try:
            environment = os.environ.copy(); environment.update(ENVIRONMENT='test',DATABASE_URL='sqlite:///'+str(private/'language.db'),STORAGE_PATH=str(private/'files'),JWT_SECRET=secrets.token_urlsafe(48),ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',MATRIX_ENABLED='false',MAIL_DELIVERY_MODE='disabled',OIDC_ENABLED='false',WEBHOOKS_ENABLED='false',OTRUST_ENABLED='false',SYNC_ENABLED='false',CLOUD_FILES_ENABLED='false',EXTERNAL_MEETINGS_ENABLED='false',COPILOT_ENABLED='false',PUSH_ENABLED='false',PUBLIC_APP_URL=origin,CORS_ORIGINS=json.dumps([origin]),PYTHONPATH=str(ROOT/'backend'),LANGUAGE_FIXTURE_PRIVATE=str(private))
            environment['NO_PROXY'] = ','.join(filter(None,[environment.get('NO_PROXY',''),'localhost','127.0.0.1','::1']))
            launcher=private/'api.py'; launcher.write_text('''import asyncio,json,os
from pathlib import Path
from fastapi import Request
from sqlmodel import Session
from app.main import app,socket_app
from app.database import init_db,engine
from app.models.user import User
from app.models.channel import Channel,ChannelMember
from app.models.workspace import Workspace,WorkspaceMember,WorkspaceChannel
from app.models.preference import UserLanguage
from app.models.message import Message
from app.services.auth import create_token
from app.services.crypto import encrypt_message
import uvicorn
private=Path(os.environ['LANGUAGE_FIXTURE_PRIVATE']);init_db()
with Session(engine) as session:
    names={'alpha':'Linnea Berg','beta':'Oskar Lind'}
    session.add_all([User(id=name,name=names[name],email=name+'@language-proof.example',password_hash='unused')for name in ['alpha','beta']]);session.add(Workspace(id='team',name='Studio Nord'));session.add_all([Channel(id='shared',name='Lansering'),Channel(id='design',name='Design'),Channel(id='studio',name='Studion')]);session.commit()
    session.add_all([WorkspaceMember(workspace_id='team',user_id=name,role='owner'if name=='alpha'else'member')for name in ['alpha','beta']]);session.add_all([WorkspaceChannel(workspace_id='team',channel_id=channel)for channel in ['shared','design','studio']]);session.add_all([ChannelMember(channel_id=channel,user_id=name,role='admin'if name=='alpha'else'member')for channel in ['shared','design','studio']for name in ['alpha','beta']]);session.add_all([UserLanguage(user_id=name,locale='en')for name in ['alpha','beta']]);session.add(Message(id='unchanged-note',channel_id='shared',author_id='alpha',content=encrypt_message('A user-authored English note stays exactly as written.','shared'),encrypted=True));session.add(Message(id='known-source-word',channel_id='shared',author_id='alpha',content=encrypt_message('Settings','shared'),encrypted=True));session.commit()
    tokens={name:create_token(name,session)for name in ['alpha','beta']}
    fd=os.open(private/'sessions.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w')as stream:json.dump(tokens,stream)
@app.middleware('http')
async def private_response_latency(request:Request,call_next):
    identity=next((name for name,token in tokens.items()if request.headers.get('authorization')=='Bearer '+token),None)
    try:config=json.loads((private/'controls.json').read_text())
    except (OSError,ValueError):config={}
    hold=None
    if request.url.path=='/preferences/language':
        if request.method=='GET'and config.get('get')==identity:hold='get'
        if request.method=='PUT'and config.get('put')==identity:hold='put'
    response=await call_next(request)
    if hold:
        (private/(hold+'-'+identity+'.captured')).touch()
        for _ in range(1200):
            if(private/(hold+'.release')).exists():break
            await asyncio.sleep(.05)
    return response
uvicorn.run(socket_app,host='127.0.0.1',port='''+str(api_port)+''',lifespan='off',access_log=False,proxy_headers=False)
''')
            (web/'index.html').write_text('<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>FreeCollab native language proof</title></head><body><div id="root"></div><script type="module" src="/entry.tsx"></script></body></html>')
            (web/'product.html').write_text('<!doctype html><html lang="sv"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Studio Nord · FreeCollab</title></head><body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body></html>')
            (web/'base.css').write_text('\n'.join(line for line in(source/'index.css').read_text().splitlines()if not line.lstrip().startswith(('@import "tailwindcss"','@config ')))+'\n.proof-language-controls{display:flex;align-items:center;gap:20px;padding:8px 20px;min-height:96px;background:var(--fc-surface);border-bottom:1px solid var(--fc-border)}.proof-language-controls .fc-language{margin:0;flex:1;max-width:600px}.proof-language-controls p{margin:0}.fc-chat{height:calc(100dvh - 96px)}@media(max-width:480px){.proof-language-controls{padding:8px 12px}}\n')
            entry='''import React from'react';import{createRoot}from'react-dom/client';import{LocaleProvider,LanguagePicker,useI18n,currentLocale}from'./src/i18n';import{AppearanceProvider}from'./src/appearance/AppearanceProvider';import{AccountProvider}from'./src/account/AccountProvider';import App from'./src/App';import{api}from'./src/lib/api';import'./base.css';
(window as any).switchLanguageAccount=(id:string)=>{const token=(window as any).__languageSessions[id];if(!token){api.logout();return}api.applySession({access_token:token,user_id:id,token_type:'bearer',public_key:'',encrypted_private_key:null})};
function Controls(){const{locale}=useI18n();return <div className="proof-language-controls"><LanguagePicker/><output data-testid="active-language">{locale}</output></div>}
createRoot(document.querySelector('#root')!).render(<React.StrictMode><LocaleProvider><AppearanceProvider><AccountProvider><Controls/><App/></AccountProvider></AppearanceProvider></LocaleProvider></React.StrictMode>);
(window as any).activeLanguage=()=>currentLocale();
'''
            (web/'entry.tsx').write_text(entry)
            dependencies=json.loads((ROOT/'frontend/package.json').read_text())['dependencies']; aliases={name:str(ROOT/'frontend/node_modules'/name)for name in dependencies}
            vite=private/'vite.mjs';vite.write_text('import{createServer}from '+json.dumps(str(ROOT/'frontend/node_modules/vite/dist/node/index.js'))+';import react from '+json.dumps(str(ROOT/'frontend/node_modules/@vitejs/plugin-react/dist/index.js'))+';const server=await createServer({configFile:false,root:'+json.dumps(str(web))+',plugins:[react()],resolve:{alias:'+json.dumps(aliases)+'},cacheDir:'+json.dumps(str(private/'vite-cache'))+',server:{host:"127.0.0.1",port:'+str(app_port)+',strictPort:true,fs:{allow:['+json.dumps(str(private))+','+json.dumps(str(ROOT/'frontend'))+']},proxy:{"/api":{target:'+json.dumps(base)+',changeOrigin:true,rewrite:p=>p.replace(/^\\/api/,"")},"/socket.io":{target:'+json.dumps(base)+',ws:true}}}});await server.listen();')
            for command,name,cwd in [([PYTHON,str(launcher)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',ROOT/'frontend')]:
                stream=(private/name).open('w');logs.append(stream);processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            ready(base+'/ready',processes);ready(origin,processes);sessions=json.loads((private/'sessions.json').read_text())
            with sync_playwright()as playwright:
                browser=playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH')or shutil.which('chromium'),headless=True,args=['--no-sandbox']);context=browser.new_context(viewport={'width':1440,'height':1050},locale='en-US');page=context.new_page();page.set_default_timeout(45000);errors=[];page.on('pageerror',lambda failure:errors.append(str(failure)))
                context.add_init_script('window.__languageSessions='+json.dumps(sessions)+';for(const name of["alpha","beta"])localStorage.setItem("freecollab.workspace:"+name,"team");if(!localStorage.getItem("freecollab_token")){localStorage.setItem("freecollab_token",window.__languageSessions.alpha);localStorage.setItem("freecollab_user_id","alpha")}')
                try:
                    stage='native initial English and pending real GET';controls(get='alpha');page.goto(origin,wait_until='domcontentloaded');wait_file(page,private/'get-alpha.captured')
                    picker=page.locator('.proof-language-controls select');composer=page.locator('.fc-chat-composer textarea');page.locator('.fc-chat-channel').filter(has_text='Lansering').click();expect(composer).to_be_visible();expect(picker).to_have_value('auto');assert page.evaluate('document.documentElement.lang')=='en';checks['native_english_browser_fallback_and_real_language_get']=True
                    stage='Swedish switch keeps native composer DOM, value, selection and user content';draft='Keep this unsent English draft exactly. Åäö.';composer.fill(draft);composer.evaluate('(node)=>{window.languageComposer=node;node.setSelectionRange(3,11)}');picker.select_option('sv');controls();expect(picker).to_have_value('sv');expect(page.locator('.proof-language-controls')).to_contain_text('Språk');expect(composer).to_have_value(draft);assert composer.evaluate('(node)=>node===window.languageComposer&&node.selectionStart===3&&node.selectionEnd===11');expect(page.get_by_text('A user-authored English note stays exactly as written.',exact=True)).to_be_visible();expect(page.locator('.fc-chat-message-content').get_by_text('Settings',exact=True)).to_be_visible();assert page.evaluate('document.documentElement.lang')=='sv';assert saved(base,sessions['alpha'])=='sv';checks['swedish_catalog_and_html_lang_preserve_native_draft_dom_selection_and_user_content']=True
                    stage='stale real GET response cannot replace successful newer selection';(private/'get.release').touch();page.wait_for_timeout(150);expect(picker).to_have_value('sv');assert page.evaluate('localStorage.getItem("freecollab.language:alpha")')=='sv';checks['stale_get_response_cannot_overwrite_newer_language_put']=True
                    stage='server preference survives reload without local language cache';page.evaluate('localStorage.removeItem("freecollab.language:alpha")');page.reload(wait_until='networkidle');expect(picker).to_have_value('sv');assert page.evaluate('document.documentElement.lang')=='sv';checks['server_saved_swedish_survives_reload_without_local_preference_cache']=True
                    stage='hanging Alpha PUT does not block Beta save and stale PUT cannot repaint Beta';controls(put='alpha');picker.select_option('en');wait_file(page,private/'put-alpha.captured');controls();page.evaluate('window.switchLanguageAccount("beta")');expect(picker).to_have_value('en');picker.select_option('sv');expect(page.locator('.proof-language-controls')).to_contain_text('Språket har sparats',timeout=10000);assert saved(base,sessions['beta'])=='sv';assert not(private/'put.release').exists();checks['hanging_alpha_save_does_not_block_beta_independent_account_queue']=True
                    (private/'put.release').touch();page.wait_for_timeout(200);expect(picker).to_have_value('sv');assert page.evaluate('document.documentElement.lang')=='sv';assert page.evaluate('localStorage.getItem("freecollab.language:beta")')=='sv';assert saved(base,sessions['alpha'])=='en';checks['stale_alpha_put_response_cannot_change_beta_locale_or_cache']=True
                    stage='same-account latest selection preserves serialized server order';page.evaluate('window.switchLanguageAccount("alpha")');expect(picker).to_have_value('en');(private/'put.release').unlink();(private/'put-alpha.captured').unlink();controls(put='alpha');picker.select_option('sv');wait_file(page,private/'put-alpha.captured');controls();picker.select_option('en');expect(picker).to_have_value('en');(private/'put.release').touch();expect(page.locator('.proof-language-controls')).to_contain_text('Language saved.');assert saved(base,sessions['alpha'])=='en';checks['latest_same_account_selection_wins_slow_previous_put']=True
                    stage='account isolation, locale-only switch and native 320px layout';page.evaluate('window.switchLanguageAccount("beta")');expect(picker).to_have_value('sv');expect(page.locator('.fc-chat-account-person strong')).to_have_text('Oskar Lind');page.locator('.fc-chat-channel').filter(has_text='Lansering').click();expect(page.locator('.fc-chat-message-content').get_by_text('Settings',exact=True)).to_be_visible();page.set_viewport_size({'width':320,'height':900});expect(picker).to_be_visible();expect(composer).to_be_visible();page.wait_for_function('document.querySelector(".fc-chat-sidebar").getBoundingClientRect().width<.5');assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');checks['account_isolated_swedish_preference_and_native_320px_no_overflow']=True
                    stage='anonymous language is independent of signed-in accounts';page.evaluate('window.switchLanguageAccount("guest")');expect(picker).to_have_value('auto');picker.select_option('sv');assert page.evaluate('localStorage.getItem("freecollab.language:guest")')=='sv';assert saved(base,sessions['alpha'])=='en'and saved(base,sessions['beta'])=='sv';checks['anonymous_language_cache_does_not_mutate_account_preferences']=True
                    stage='clean normal product screenshots with realistic Studio Nord content'
                    for identifier in['unchanged-note','known-source-word']:native(base,sessions['alpha'],'DELETE','/messages/'+identifier)
                    messages=[('alpha','God morgon! Utkastet till vårens kampanj är klart. Kan vi samla feedback här innan torsdag?'),('beta','Absolut. Jag granskar tillgängligheten och uppdaterar checklistan i eftermiddag.'),('alpha','Tack! Nästa steg är att välja bildmanér och bekräfta publiceringsdatumet.'),('beta','Jag gillar den lugnare riktningen. Vi visar första versionen för teamet på fredag.')]
                    for who,content in messages:native(base,sessions[who],'POST','/messages/channels/shared',{'content':content})
                    for title,body in[('Veckans fokus','Vi färdigställer landningssidan och samlar återkoppling från teamet. Målet är ett tydligt förslag på fredag.'),('En gemensam riktning','Lugna färger, tydlig typografi och mer utrymme för innehållet. Det här blir utgångspunkten för nästa version.')]:native(base,sessions['alpha'],'POST','/channels/shared/activity-feed',{'id':secrets.token_hex(16),'expected_revision':0,'archived':False,'fields':{'title':title,'body':body,'poll':None}})
                    product_context=browser.new_context(viewport={'width':1440,'height':1000},locale='sv-SE');product_context.add_init_script('localStorage.setItem("freecollab_token",'+json.dumps(sessions['beta'])+');localStorage.setItem("freecollab_user_id","beta");localStorage.setItem("freecollab.workspace:beta","team")');product=product_context.new_page();product.set_default_timeout(45000);product.on('pageerror',lambda failure:errors.append(str(failure)))
                    try:
                        product.goto(origin+'/product.html',wait_until='networkidle');expect(product.locator('.fc-chat-account-person strong')).to_have_text('Oskar Lind');product.locator('.fc-chat-channel').filter(has_text='Lansering').click();expect(product.locator('.fc-chat-message-content').get_by_text(messages[-1][1],exact=True)).to_be_visible();product.wait_for_function('document.documentElement.lang==="sv"&&document.fonts.status==="loaded"');product.wait_for_function('document.querySelector(".fc-chat-sidebar").getBoundingClientRect().width>263.5');assert product.locator('.proof-language-controls').count()==0;expect(product.get_by_role('button',name='Fler kanalverktyg',exact=True)).not_to_be_visible();assert all(product.locator('.fc-chat-secondary-action').nth(i).is_visible()for i in range(4));checks['desktop_channel_toolbar_all_four_existing_secondary_actions_stay_visible']=True;assert product.evaluate('document.documentElement.scrollWidth<=innerWidth');product.screenshot(path=str(REPORT.with_name('product-swedish-desktop.png')),full_page=True)
                        product.locator('.fc-chat-topbar-action').filter(has_text='Arbete').click();product.get_by_role('tab',name='Aktivitetsflöde',exact=True).click();expect(product.get_by_role('heading',name='Veckans fokus',exact=True)).to_be_visible();product.screenshot(path=str(REPORT.with_name('product-workhub-feed-desktop.png')),full_page=True);product.keyboard.press('Escape');expect(product.get_by_role('dialog')).to_have_count(0)
                        product.set_viewport_size({'width':390,'height':844});product.wait_for_function('document.querySelector(".fc-chat-sidebar").getBoundingClientRect().width<.5');expect(product.locator('.fc-chat-composer textarea')).to_be_visible();assert product.evaluate('document.documentElement.scrollWidth<=innerWidth');product.locator('.fc-chat-channel-heading').click(position={'x':64,'y':20});product.screenshot(path=str(REPORT.with_name('product-swedish-mobile.png')),full_page=True);checks['clean_actual_production_entry_swedish_chat_and_workhub_screenshots']=True
                        for width in[390,320]:
                            stage='normal mobile channel toolbar keyboard actions and visible return focus at '+str(width)+'px';product.set_viewport_size({'width':width,'height':844});channel_title=product.locator('.fc-chat-topbar-channel strong');expect(channel_title).to_have_text('Lansering');assert channel_title.evaluate('(node)=>node.clientWidth>=node.scrollWidth');trigger=product.get_by_role('button',name='Fler kanalverktyg',exact=True);expect(trigger).to_be_visible();assert all(not product.locator('.fc-chat-secondary-action').nth(i).is_visible()for i in range(4))
                            actions=[('Inställningar för mobil och offline','Mobil och utan nätverk'),('Sparade objekt och beslut','Kanalens anteckningsbok'),('Kanalmedlemmar','Kanalmedlemmar'),('Kanalfiler','Kanalfiler')]
                            for index,(action_name,dialog_name)in enumerate(actions):
                                trigger.focus();trigger.press('Enter');expect(trigger).to_have_attribute('aria-expanded','true');group=product.get_by_role('group',name='Fler kanalverktyg',exact=True);expect(group).to_be_visible();assert group.get_by_role('button').count()==4;assert group.evaluate('(node)=>{const box=node.getBoundingClientRect();return box.left>=0&&box.right<=innerWidth}')
                                for _ in range(index+1):product.keyboard.press('Tab')
                                expect(group.get_by_role('button',name=action_name,exact=True)).to_be_focused();product.keyboard.press('Enter');expect(group).to_have_count(0);expect(product.get_by_role('dialog',name=dialog_name,exact=True)).to_be_visible();product.keyboard.press('Escape');expect(product.get_by_role('dialog')).to_have_count(0);expect(trigger).to_be_focused();expect(trigger).to_have_attribute('aria-expanded','false')
                            trigger.press('Enter');product.keyboard.press('Escape');expect(trigger).to_be_focused();expect(trigger).to_have_attribute('aria-expanded','false');trigger.press('ArrowDown');expect(product.locator('.fc-chat-mobile-tool').first).to_be_focused();product.keyboard.press('Shift+Tab');expect(trigger).to_be_focused();product.keyboard.press('Shift+Tab');expect(trigger).to_have_attribute('aria-expanded','false');trigger.focus();trigger.press('Enter')
                            if width==390:product.screenshot(path=str(REPORT.with_name('product-mobile-channel-tools.png')),full_page=True)
                            product.locator('.fc-chat-composer textarea').click();expect(trigger).to_have_attribute('aria-expanded','false');expect(product.locator('.fc-chat-composer textarea')).to_be_focused();assert product.evaluate('document.documentElement.scrollWidth<=innerWidth');checks['mobile_'+str(width)+'px_full_channel_name_four_keyboard_tool_actions_escape_outside_tab_and_visible_focus_restoration']=True
                            stage='normal mobile WorkHub selector and unsaved draft guard at '+str(width)+'px';product.locator('.fc-chat-topbar-action').filter(has_text='Arbete').click();tool=product.get_by_role('combobox',name='Verktyg',exact=True);expect(tool).to_be_visible();assert tool.locator('option').count()==10;tool.select_option('feed');expect(product.get_by_role('heading',name='Veckans fokus',exact=True)).to_be_visible();assert product.evaluate('document.documentElement.scrollWidth<=innerWidth&&document.querySelector(".fc-work-hub").scrollWidth<=document.querySelector(".fc-work-hub").clientWidth');tool.select_option('tasks');expect(product.locator('.fc-projects')).to_be_visible();tool.select_option('feed');expect(product.get_by_role('heading',name='Veckans fokus',exact=True)).to_be_visible()
                            if width==390:product.screenshot(path=str(REPORT.with_name('product-workhub-feed-mobile.png')),full_page=True)
                            product.get_by_role('button',name='Skriv en uppdatering',exact=True).click();product.locator('#feed-title').fill('Ett osparat utkast');product.locator('#feed-body').fill('Behåll den här texten när jag väljer att stanna.');dialogs=[]
                            def cancel_navigation(dialog):dialogs.append(dialog.message);dialog.dismiss()
                            product.once('dialog',cancel_navigation);tool.select_option('tasks');assert len(dialogs)==1 and 'Ändringarna har inte sparats' in dialogs[0];expect(tool).to_have_value('feed');expect(product.locator('#feed-title')).to_have_value('Ett osparat utkast');expect(product.locator('#feed-body')).to_have_value('Behåll den här texten när jag väljer att stanna.');assert product.evaluate('document.documentElement.scrollWidth<=innerWidth');product.once('dialog',lambda dialog:dialog.accept());product.keyboard.press('Escape');expect(product.get_by_role('dialog')).to_have_count(0);checks['mobile_'+str(width)+'px_all_ten_tools_tasks_feed_and_cancel_preserves_unsaved_update_without_overflow']=True
                    finally:product_context.close()
                    assert not errors,'Unexpected native language browser page error';checks['zero_native_browser_page_errors']=True
                finally:context.close();browser.close()
        except Exception:
            print('FAILED language proof stage: '+stage,file=sys.stderr)
            raise
        finally:
            (private/'get.release').touch();(private/'put.release').touch()
            for process in reversed(processes):
                if process.poll()is None:process.terminate()
            for process in reversed(processes):
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            for stream in logs:stream.close()
    REPORT.parent.mkdir(parents=True,exist_ok=True);REPORT.write_text(json.dumps({'scope':'Production App/LocaleProvider/LanguagePicker/Chat source snapshot with private real API/SQLite/Socket.IO/Vite and fixture native sessions; real native API responses delayed after production handlers, no external service','checks':checks,'source_sha256':source_hashes,'cleanup':'Own API/Vite/database/keys/session fixtures/browser context removed'},indent=2)+'\n');print('Native language browser proof passed: '+str(len(checks))+' checks; Swedish/English, real server persistence, stale GET/PUT, account queues and composer preservation.')

if __name__=='__main__':main()
