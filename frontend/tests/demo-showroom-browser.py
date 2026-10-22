#!/usr/bin/env python3
"""Actual public showroom interactions in Chromium, with zero native/provider calls.
Runs a private source snapshot; all demo data stays in browser memory.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import urlopen
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(os.getenv('DEMO_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/demo-showroom-browser.json')))
SOURCE = ['frontend/src/assets/open-studio/courtyard.png','frontend/src/assets/open-studio/portraits.png','frontend/src/components/Landing.tsx','frontend/src/components/Demo.tsx','frontend/src/App.tsx','frontend/src/website-entry.tsx','frontend/src/lib/demo-showroom.ts','frontend/src/styles/demo-showroom.css','frontend/src/i18n/demo-showroom.sv.ts','frontend/src/i18n/studio-refinement.sv.ts','frontend/src/i18n/demo-journey.sv.ts','frontend/tests/demo-showroom.test.mjs','frontend/tests/demo-showroom-browser.py']

def port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));return listener.getsockname()[1]

def main():
    checks, stage, process = {}, 'private Vite startup', None
    source_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE}
    with tempfile.TemporaryDirectory(prefix='freecollab-showroom-') as directory:
        private=Path(directory); app_port=port(); app=f'http://127.0.0.1:{app_port}'
        web=private/'web'; shutil.copytree(ROOT/'frontend',web,ignore=shutil.ignore_patterns('node_modules','dist','dist-widget','tests','.vite'))
        (web/'node_modules').symlink_to(ROOT/'frontend/node_modules',target_is_directory=True)
        (web/'index.html').write_text('<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width, initial-scale=1" /></head><body><div id="root"></div><script type="module" src="/entry.tsx"></script></body></html>')
        for entry, source in [('app', 'main'), ('website', 'website-entry')]:
            (web/(entry+'.html')).write_text('<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width, initial-scale=1" /></head><body><div id="root"></div><script type="module" src="/src/'+source+'.tsx"></script></body></html>')
        (web/'entry.tsx').write_text('''import React from 'react';import{createRoot}from'react-dom/client';import{Demo}from'./src/components/Demo';import{applyLocale}from'./src/i18n';import'@fontsource-variable/inter';import'@fontsource-variable/manrope';import'@fontsource-variable/source-serif-4';import'./src/index.css';window.showroomLocale=applyLocale;window.showroomBack=0;window.showroomOpen=0;createRoot(document.getElementById('root')).render(<React.StrictMode><Demo onBack={()=>window.showroomBack++} onOpenApp={()=>window.showroomOpen++}/></React.StrictMode>);''')
        launcher=private/'serve.mjs'; launcher.write_text('import{createServer}from'+json.dumps(str(ROOT/'frontend/node_modules/vite/dist/node/index.js'))+';import react from'+json.dumps(str(ROOT/'frontend/node_modules/@vitejs/plugin-react/dist/index.js'))+';const server=await createServer({configFile:false,root:'+json.dumps(str(web))+',plugins:[react()],server:{host:"127.0.0.1",port:'+str(app_port)+',strictPort:true,hmr:false,watch:{ignored:["**"]},fs:{allow:'+json.dumps([str(private),str(ROOT/'frontend')])+'}}});await server.listen();')
        stream=(private/'vite.log').open('w')
        try:
            process=subprocess.Popen(['node',str(launcher)],cwd=ROOT/'frontend',stdout=stream,stderr=stream,start_new_session=True)
            for _ in range(100):
                if process.poll() is not None: raise RuntimeError((private/'vite.log').read_text()[-2500:])
                try:
                    with urlopen(app,timeout=1):break
                except Exception:time.sleep(.15)
            else:raise RuntimeError('Private demo server did not start')
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),args=['--no-sandbox'])
                context=browser.new_context(viewport={'width':1440,'height':1100},reduced_motion='reduce',accept_downloads=True)
                page=context.new_page();page.set_default_timeout(20000);errors=[];unexpected=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('request',lambda request:unexpected.append(request.url) if urlsplit(request.url).hostname not in ['127.0.0.1','localhost'] or '/api/' in urlsplit(request.url).path else None)
                page.goto(app,wait_until='networkidle')
                def tools():
                    toggle=page.get_by_role('button',name='Tools',exact=True)
                    if not toggle.count():toggle=page.get_by_role('button',name='Verktyg',exact=True)
                    if toggle.get_attribute('aria-expanded')!='true':toggle.click()
                def section(name):
                    tools();page.get_by_role('tab',name=name,exact=True).click()
                def reset_menu():
                    tools();toggle=page.get_by_role('button',name='Reset demo',exact=True)
                    if not toggle.count():toggle=page.get_by_role('button',name='Återställ demo',exact=True)
                    toggle.click()
                stage='public guest showroom and literal local message'
                expect(page.get_by_role('heading',name='Shall we open up to the courtyard?',exact=True)).to_be_visible()
                expect(page.get_by_text('Sample workspace · fictional people · changes stay in this tab',exact=True)).to_be_visible()
                expect(page.get_by_role('img',name='A sunlit brick courtyard with trees, an open doorway and a shared table',exact=True)).to_be_visible()
                stage='real focus and accessible bounded personal layout'
                materials=page.locator('.fc-studio-materials')
                page.get_by_role('button',name='Focus',exact=True).click();expect(materials).not_to_be_visible()
                expect(page.get_by_role('log',name='Sample conversation')).to_be_visible()
                page.get_by_role('button',name='Leave focus',exact=True).click();expect(materials).to_be_visible()
                page.get_by_role('button',name='Arrange',exact=True).click()
                earlier=page.get_by_role('button',name='Move Shared brief earlier',exact=True)
                earlier.focus();page.keyboard.press('Enter')
                assert materials.locator('[data-material]').evaluate_all('(items)=>items.map(item=>item.dataset.material)')==['brief','courtyard']
                page.get_by_role('button',name='Pin Courtyard image',exact=True).click()
                expect(page.get_by_role('button',name='Pin Courtyard image',exact=True)).to_have_attribute('aria-pressed','true')
                assert materials.locator('[data-material]').first.get_attribute('data-material')=='courtyard'
                page.get_by_label('Material position',exact=True).select_option('left')
                expect(page.locator('.fc-studio-scene')).to_have_attribute('data-material-position','left')
                page.get_by_role('button',name='Reset layout',exact=True).click()
                expect(page.locator('.fc-studio-scene')).to_have_attribute('data-material-position','right')
                expect(page.get_by_role('button',name='Pin Courtyard image',exact=True)).to_have_attribute('aria-pressed','false')
                page.get_by_role('button',name='Close layout controls',exact=True).click()
                page.get_by_label('Edit courtyard brief',exact=True).fill('## Shared edit from the conversation')
                section('Documents');expect(page.get_by_label('Edit demo document',exact=True)).to_have_value('## Shared edit from the conversation')
                section('Conversation')
                checks['real_focus_reorder_pin_position_reset_and_shared_document']=True
                initial=page.get_by_role('log',name='Sample conversation').locator('article').count()
                text='Local note <img src="https://invalid.example/tracker">'
                page.get_by_label('Your demo message',exact=True).fill(text);page.get_by_role('button',name='Send demo message',exact=True).click()
                expect(page.get_by_role('log').get_by_text(text,exact=True)).to_be_visible()
                assert page.get_by_role('log').locator('img').count()==0
                page.wait_for_timeout(1100)
                assert page.get_by_role('log').locator('article').count()==initial+1
                page.get_by_label('Demo channel',exact=True).select_option('design')
                expect(page.get_by_role('log').get_by_text(text,exact=True)).to_have_count(0)
                page.get_by_label('Demo channel',exact=True).select_option('studio')
                expect(page.get_by_role('log').get_by_text(text,exact=True)).to_be_visible()
                checks['guest_literal_channel_scoped_memory_chat_no_bot']=True
                stage='independent unsent drafts survive channel and tool navigation'
                draft='An unsent courtyard thought. Keep the exact wording.'
                other_draft='An independent design thought.'
                page.get_by_label('Your demo message',exact=True).fill(draft)
                expect(page.get_by_text('Unsent draft · kept in this tab',exact=True)).to_be_visible()
                REPORT.parent.mkdir(parents=True,exist_ok=True)
                draft_image=REPORT.with_name('demo-showroom-draft.png');page.screenshot(path=str(draft_image),full_page=True)
                page.get_by_label('Demo channel',exact=True).select_option('design')
                expect(page.get_by_label('Your demo message',exact=True)).to_have_value('')
                page.get_by_label('Your demo message',exact=True).fill(other_draft)
                page.get_by_label('Demo channel',exact=True).select_option('studio')
                expect(page.get_by_label('Your demo message',exact=True)).to_have_value(draft)
                page.get_by_role('button',name='Send demo message',exact=True).click()
                expect(page.get_by_label('Your demo message',exact=True)).to_have_value('')
                page.get_by_label('Demo channel',exact=True).select_option('design')
                expect(page.get_by_label('Your demo message',exact=True)).to_have_value(other_draft)
                section('Projects');section('Conversation')
                expect(page.get_by_label('Your demo message',exact=True)).to_have_value(other_draft)
                page.get_by_label('Demo channel',exact=True).select_option('studio')
                checks['independent_memory_drafts_survive_navigation_and_only_sent_draft_clears']=True
                stage='actual project checkbox/add/filter'
                section('Projects')
                box=page.get_by_role('checkbox',name='Review the courtyard materials',exact=True);box.check();expect(box).to_be_checked()
                page.get_by_label('New demo task',exact=True).fill('My new courtyard step');page.get_by_role('button',name='Add task',exact=True).click()
                expect(page.get_by_role('checkbox',name='My new courtyard step',exact=True)).to_be_visible()
                page.get_by_role('button',name='Done',exact=True).click();expect(page.get_by_role('checkbox',name='My new courtyard step',exact=True)).to_have_count(0)
                page.get_by_role('button',name='All tasks',exact=True).click()
                checks['actual_project_add_toggle_and_filter']=True
                stage='actual edited Markdown file download'
                section('Documents')
                edited='## My courtyard idea\n\nLocal **edited text**, preserved exactly.'
                page.get_by_label('Edit demo document',exact=True).fill(edited)
                with page.expect_download() as downloaded:page.get_by_role('button',name='Download Markdown',exact=True).click()
                download=downloaded.value;assert download.suggested_filename=='freecollab-demo-brief.md'
                assert Path(download.path()).read_text()=='# Project brief\n\n'+edited+'\n'
                page.get_by_role('button',name='Studio notes',exact=True).click();assert page.get_by_label('Edit demo document').input_value()!=edited
                page.get_by_role('button',name='Project brief',exact=True).click();expect(page.get_by_label('Edit demo document')).to_have_value(edited)
                checks['edited_document_markdown_actual_download_and_independent_documents']=True
                stage='one conversation, the same shared brief, and a real local decision/owned task'
                journey=page.get_by_role('navigation',name='Follow the courtyard project',exact=True)
                journey.get_by_role('button',name='01 The conversation',exact=True).click()
                expect(page.get_by_label('Edit courtyard brief',exact=True)).to_have_value(edited)
                journey_brief='## A shared courtyard direction\n\nKeep the doorway open and compare two timber samples.'
                page.get_by_label('Edit courtyard brief',exact=True).fill(journey_brief)
                journey.get_by_role('button',name='02 The shared brief',exact=True).click()
                expect(page.get_by_label('Edit demo document',exact=True)).to_have_value(journey_brief)
                page.get_by_role('button',name='Agree a next step',exact=True).click()
                task_count=page.get_by_role('checkbox').count()
                decision='Choose warm timber <script>literal decision text</script>'
                next_step='Bring two courtyard timber samples'
                page.get_by_label('Sample decision',exact=True).fill(decision)
                page.get_by_label('Next step',exact=True).fill(next_step)
                page.get_by_label('Owner',exact=True).select_option('Noor')
                page.get_by_role('button',name='Record sample decision',exact=True).click()
                expect(page.get_by_text(decision,exact=True)).to_be_visible()
                expect(page.get_by_text('Recorded in this demo',exact=True)).to_be_visible()
                assert page.locator('.fc-studio-decision script').count()==0
                follow_up=page.get_by_role('checkbox',name=next_step,exact=True)
                expect(follow_up).to_have_count(1);follow_up.check()
                assert page.get_by_role('checkbox').count()==task_count+1
                expect(page.locator('.fc-studio-decision-record')).to_contain_text('Noor')
                page.get_by_role('button',name='Revise sample decision',exact=True).click()
                page.get_by_label('Sample decision',exact=True).fill('Keep this direction, with a clearer explanation.')
                page.get_by_role('button',name='Record sample decision',exact=True).click()
                expect(follow_up).to_be_checked()
                page.get_by_role('button',name='Revise sample decision',exact=True).click()
                revised_step='Bring the revised material samples'
                page.get_by_label('Next step',exact=True).fill(revised_step)
                page.get_by_label('Owner',exact=True).select_option('Elias')
                page.get_by_role('button',name='Record sample decision',exact=True).click()
                expect(follow_up).to_have_count(0)
                expect(page.get_by_role('checkbox',name=revised_step,exact=True)).not_to_be_checked()
                assert page.get_by_role('checkbox').count()==task_count+1
                page.get_by_role('button',name='View shared brief',exact=True).click()
                expect(page.get_by_label('Edit demo document',exact=True)).to_have_value(journey_brief)
                journey.get_by_role('button',name='03 Decision & next step',exact=True).click()
                expect(page.get_by_role('checkbox',name=revised_step,exact=True)).to_be_visible()
                expect(journey.get_by_role('button',name='03 Decision & next step',exact=True)).to_have_attribute('aria-current','step')
                checks['same_shared_brief_local_decision_owned_task_revision_and_completion']=True
                stage='Microsoft and Google preparation only'
                section('Connections')
                for provider in ['Microsoft 365','Google Workspace']:
                    page.get_by_role('button',name=provider+' Preparation guide',exact=True).click()
                    if provider=='Microsoft 365': expect(page.get_by_text('Files.ReadWrite.All · offline_access (files) / Calendars.ReadWrite · offline_access (calendar)',exact=True)).to_be_visible()
                    for box in page.get_by_role('checkbox').all():
                        expect(box).not_to_be_checked();box.check()
                    expect(page.get_by_text('Checking a box changes this example checklist only. No account is connected, authorized or tested here.',exact=True)).to_be_visible()
                    expect(page.get_by_label('Example callback — replace with your installation’s exact address',exact=True)).to_have_value('https://your-freecollab.example/api/sync/oauth/callback')
                checks['both_provider_guides_no_fake_authorization_or_connectivity']=True
                stage='appearance scoped to showroom; keyboard tabs'
                root_style=page.evaluate('document.documentElement.getAttribute("style")')
                section('Appearance')
                page.get_by_role('button',name='Fjord Cool stone & ocean',exact=True).click()
                page.get_by_label('Demo typeface',exact=True).select_option('manrope')
                assert page.locator('.fc-showroom').evaluate('(element)=>getComputedStyle(element).getPropertyValue("--fc-accent-original").trim()')=='#365f79'
                assert page.evaluate('document.documentElement.getAttribute("style")')==root_style
                assert page.locator('.fc-showroom').evaluate('(element)=>getComputedStyle(element).fontFamily').startswith('"Manrope Variable"')
                tools();page.get_by_role('tab',name='Appearance',exact=True).focus();page.keyboard.press('Home')
                expect(page.get_by_role('tab',name='Conversation',exact=True)).to_be_focused()
                expect(page.get_by_role('tab',name='Conversation',exact=True)).to_have_attribute('aria-selected','true')
                checks['appearance_scoped_without_account_mutation_and_keyboard_tabs']=True
                page.keyboard.press('Escape')
                REPORT.parent.mkdir(parents=True,exist_ok=True)
                desktop=REPORT.with_name('demo-showroom-desktop.png');page.screenshot(path=str(desktop),full_page=True)
                stage='reset focus trap and all local data cleared'
                page.get_by_role('button',name='Arrange',exact=True).click()
                page.get_by_role('button',name='Pin Shared brief',exact=True).click()
                page.get_by_label('Material position',exact=True).select_option('left')
                page.get_by_role('button',name='Close layout controls',exact=True).click()
                page.get_by_role('button',name='Focus',exact=True).click()
                reset_menu()
                dialog=page.get_by_role('alertdialog');expect(dialog).to_be_visible()
                page.keyboard.press('Shift+Tab');assert page.evaluate('document.querySelector("[role=alertdialog]").contains(document.activeElement)')
                page.keyboard.press('Escape');expect(dialog).to_have_count(0)
                reset_menu();page.get_by_role('button',name='Reset this demo',exact=True).click()
                expect(page.get_by_label('Your demo message',exact=True)).to_have_value('')
                page.get_by_label('Demo channel',exact=True).select_option('design')
                expect(page.get_by_label('Your demo message',exact=True)).to_have_value('')
                page.get_by_label('Demo channel',exact=True).select_option('studio')
                expect(page.get_by_role('log').get_by_text(text,exact=True)).to_have_count(0)
                expect(page.locator('.fc-studio-materials')).to_be_visible()
                expect(page.locator('.fc-studio-scene')).to_have_attribute('data-material-position','right')
                assert page.locator('.fc-studio-pinned').count()==0
                assert page.locator('.fc-showroom').evaluate('(element)=>getComputedStyle(element).getPropertyValue("--fc-accent-original").trim()')=='#ee593f'
                section('Connections')
                for provider in ['Microsoft 365','Google Workspace']:
                    page.get_by_role('button',name=provider+' Preparation guide',exact=True).click()
                    for box in page.get_by_role('checkbox').all():expect(box).not_to_be_checked()
                section('Projects');expect(page.get_by_role('checkbox',name='My new courtyard step',exact=True)).to_have_count(0)
                expect(page.get_by_role('checkbox',name=revised_step,exact=True)).to_have_count(0)
                expect(page.get_by_label('Sample decision',exact=True)).to_have_value('Use warm, durable materials and keep the courtyard open.')
                expect(page.get_by_label('Owner',exact=True)).to_have_value('Maja')
                expect(page.get_by_role('checkbox',name='Review the courtyard materials',exact=True)).not_to_be_checked()
                section('Documents');assert page.get_by_label('Edit demo document').input_value()!=edited
                checks['focus_trapped_reset_clears_messages_tasks_documents_checklists_theme_and_layout']=True
                section('Conversation');page.screenshot(path=str(desktop),full_page=True)
                stage='Swedish and 320 px every section'
                tools();page.get_by_role('button',name='SV',exact=True).click();page.keyboard.press('Escape');page.set_viewport_size({'width':390,'height':844})
                expect(page.get_by_role('heading',name='Ska vi öppna upp mot gården?',exact=True)).to_be_visible()
                assert page.evaluate('document.documentElement.lang')=='en', 'demo language mutated app language'
                for width in [390,320]:
                    page.set_viewport_size({'width':width,'height':844})
                    for name in ['Samtal','Projekt','Dokument','Anslutningar','Utseende']:
                        section(name)
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),name+' horizontal overflow at '+str(width)
                section('Projekt')
                expect(page.get_by_role('heading',name='Gör nästa steg tydligt.',exact=True)).to_be_visible()
                page.get_by_label('Exempelbeslut',exact=True).fill('Behåll den öppna gården.')
                page.get_by_label('Nästa steg',exact=True).fill('Ta med två materialprover')
                page.get_by_label('Ansvarig',exact=True).select_option('Nora')
                page.get_by_role('button',name='Spara exempelbeslut',exact=True).click()
                expect(page.get_by_role('checkbox',name='Ta med två materialprover',exact=True)).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                journey_image=REPORT.with_name('demo-showroom-decision-mobile.png');page.screenshot(path=str(journey_image),full_page=True)
                checks['swedish_decision_owner_and_next_task_at_320px']=True
                section('Samtal')
                mobile=REPORT.with_name('demo-showroom-mobile.png');page.screenshot(path=str(mobile),full_page=True)
                reset_menu();expect(page.get_by_role('alertdialog')).to_be_visible();page.keyboard.press('Escape')
                section('Samtal');page.get_by_label('Ditt demomeddelande',exact=True).fill('Ett lokalt meddelande');page.get_by_role('button',name='Skicka demomeddelande',exact=True).click()
                page.reload(wait_until='networkidle');expect(page.get_by_text('Ett lokalt meddelande',exact=True)).to_have_count(0)
                section('Projects');expect(page.get_by_role('checkbox',name='Ta med två materialprover',exact=True)).to_have_count(0)
                expect(page.get_by_label('Sample decision',exact=True)).to_have_value('Use warm, durable materials and keep the courtyard open.')
                checks['scoped_swedish_all_sections_390_320px_and_reload_discards_demo_data']=True
                page.get_by_role('button',name='Back to FreeCollab',exact=True).click();tools();page.get_by_role('button',name='Open the app',exact=True).click()
                assert page.evaluate('window.showroomBack')==1 and page.evaluate('window.showroomOpen')==1
                assert page.evaluate('localStorage.length')==0
                assert page.evaluate('sessionStorage.length')==0
                assert not errors,errors
                assert not unexpected,unexpected
                checks['explicit_exit_callbacks_no_storage_api_provider_or_browser_errors']=True
                stage='actual App and public website provider destinations, history, direct hashes and reload'
                for entry in ['app','website']:
                    routed=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                    try:
                        visitor=routed.new_page();visitor.set_default_timeout(25000)
                        visitor.on('pageerror',lambda error:errors.append(str(error)))
                        visitor.on('request',lambda request:unexpected.append(request.url) if urlsplit(request.url).hostname not in ['127.0.0.1','localhost'] or '/api/' in urlsplit(request.url).path else None)
                        address=app+'/'+entry+'.html'
                        for provider,name in [('microsoft','Microsoft 365'),('google','Google Workspace')]:
                            visitor.goto(address,wait_until='networkidle')
                            card=visitor.locator('.fc-public-provider-grid article').filter(has=visitor.get_by_role('heading',name=name,exact=True))
                            card.get_by_role('button',name='Explore connection setup',exact=True).click()
                            guide=visitor.get_by_role('button',name=name+' Preparation guide',exact=True)
                            expect(guide).to_have_attribute('aria-pressed','true')
                            expect(visitor.get_by_role('tabpanel',name='Connections',exact=True)).to_be_visible()
                            assert visitor.evaluate('location.hash')=='#demo=connections&provider='+provider
                            visitor.go_back(wait_until='domcontentloaded')
                            expect(visitor.locator('#public-heading')).to_be_visible()
                            visitor.go_forward(wait_until='domcontentloaded')
                            expect(guide).to_have_attribute('aria-pressed','true')
                            visitor.reload(wait_until='networkidle');expect(guide).to_have_attribute('aria-pressed','true')
                            visitor.get_by_role('button',name='Back to FreeCollab',exact=True).click()
                            expect(visitor.locator('#public-heading')).to_be_visible();assert visitor.evaluate('location.hash')==''
                        visitor.goto(address+'#demo=documents',wait_until='networkidle')
                        routed_edit='A shared brief kept while following an explicit provider route.'
                        visitor.get_by_label('Edit demo document',exact=True).fill(routed_edit)
                        visitor.evaluate("location.hash='demo=connections&provider=google'")
                        expect(visitor.get_by_role('button',name='Google Workspace Preparation guide',exact=True)).to_have_attribute('aria-pressed','true')
                        visitor.evaluate("location.hash='demo=documents'")
                        expect(visitor.get_by_label('Edit demo document',exact=True)).to_have_value(routed_edit)
                        visitor.reload(wait_until='networkidle')
                        expect(visitor.get_by_label('Edit demo document',exact=True)).not_to_have_value(routed_edit)
                        if entry=='website':
                            assert visitor.evaluate('localStorage.length')==0 and visitor.evaluate('sessionStorage.length')==0
                        checks[entry+'_actual_entry_provider_buttons_history_direct_hash_reload_and_shared_local_state']=True
                    finally:routed.close()
                assert not errors,errors
                assert not unexpected,unexpected
                browser.close()
            REPORT.write_text(json.dumps({'ok':True,'scope':'private Chromium synthetic showroom and actual guest App/public website entries; no real users/accounts/providers. Credential-ignored/no-storage contract applies to standalone website, not signed-in full-App bootstrap.','checks':checks,'api_provider_requests':unexpected,'screenshots':[str(desktop),str(mobile),str(draft_image),str(journey_image)],'source_sha256':source_hashes},indent=2)+'\n')
            print('Demo showroom browser proof passed: '+str(len(checks))+' checks.')
        except Exception:
            print('Failed stage: '+stage)
            print((private/'vite.log').read_text()[-3500:]);raise
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=8)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            stream.close()

if __name__=='__main__':main()
