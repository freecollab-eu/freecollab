#!/usr/bin/env python3
"""Actual retained workbench/React/Chromium proof with local controlled Yjs fields.

No product API, provider or customer workspace is used. Full-product sessions and
permissions are covered separately; this checks the reusable concurrent host.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(os.getenv('COLLABORATION_WORKBENCH_REPORT', str(ROOT.parent / '.freecollab/proof-results/universal-collaboration-20261024/workbench/browser.json')))
SOURCES = ['frontend/src/components/CollaborationWorkbench.tsx', 'frontend/src/lib/useCollaborationWorkbench.ts', 'frontend/src/styles/collaboration-workbench.css', 'frontend/src/i18n/collaboration-workbench.sv.ts', 'frontend/src/components/ConversationWorkControls.tsx', 'frontend/src/lib/useModalFocus.ts', 'frontend/tests/collaboration-workbench-browser.py']
ENTRY = r'''import React,{useEffect,useLayoutEffect,useRef,useState}from'react';import{createRoot}from'react-dom/client';import{createPortal,flushSync}from'react-dom';import*as Y from'yjs';
import{api}from'./src/lib/api';import{CollaborationWorkbench}from'./src/components/CollaborationWorkbench';import{ConversationWorkControls}from'./src/components/ConversationWorkControls';import{useModalFocus}from'./src/lib/useModalFocus';
api.applySession({access_token:'workbench-proof-a',user_id:'workbench-owner-a',token_type:'bearer',public_key:'',encrypted_private_key:null});window.workbenchApi=api;window.docs=new Map;window.mounts={};window.unmounts={};window.inputEvents=0;
function Nested({close}){const ref=useRef(null);useModalFocus(ref,close);return createPortal(<section id="nested" ref={ref} role="dialog" aria-modal="true"><input id="nested-field"/><button onClick={close}>Close nested</button></section>,document.body)}
function Editor({entry,layout,remove,add}){const[text,setText]=useState(entry.initial||entry.title+'\n'.repeat(2)),[nested,setNested]=useState(false),[reading,setReading]=useState(false);const doc=useRef(null),field=useRef(null),reader=useRef(null);useLayoutEffect(()=>{if(reading)reader.current?.focus({preventScroll:true})},[reading]);if(!doc.current){doc.current=new Y.Doc;doc.current.getText('body').insert(0,entry.initial||entry.title+'\n'.repeat(2))}useEffect(()=>{window.docs.set(entry.id,doc.current);window.mounts[entry.id]=(window.mounts[entry.id]||0)+1;return()=>{window.unmounts[entry.id]=(window.unmounts[entry.id]||0)+1;doc.current.destroy()}},[]);
return <section className="fc-conversation-work"><header className="fc-conversation-work-header"><h2>{entry.title}</h2><ConversationWorkControls layout={layout} onClose={()=>{if(!text.includes('dirty')||confirm('Keep unsaved work? Discard this card?'))remove(entry.id)}}/></header><div className="fc-conversation-work-body" data-work-scroll><label htmlFor={'editor-'+entry.id}>{entry.title} body</label><textarea hidden={reading} ref={field} id={'editor-'+entry.id} value={text} onChange={e=>{++window.inputEvents;setText(e.target.value);doc.current.transact(()=>{const body=doc.current.getText('body');body.delete(0,body.length);body.insert(0,e.target.value)})}}/><article ref={reader} hidden={!reading} id={'reader-'+entry.id} tabIndex={-1} data-work-focus data-work-scroll aria-label={entry.title+' reading page'} style={{maxHeight:180,overflow:'auto',whiteSpace:'pre-wrap'}}>{text}</article><button id={'read-'+entry.id} onClick={()=>setReading(true)}>Done editing</button><button id={'add-'+entry.id} onClick={()=>add('task-'+Math.random().toString(16).slice(2),field.current)}>Open another task here</button><button id={'nested-'+entry.id} onClick={()=>setNested(true)}>Open nested</button><div style={{height:600}}>Retained {entry.kind} scroll</div></div>{nested&&<Nested close={()=>setNested(false)}/>}</section>}
function App(){const[scope,setScope]=useState('workspace-one:channel-one'),[entries,setEntries]=useState([{id:'document',kind:'documents',title:'Document',scope:'workspace-one:channel-one',initial:'retained document\n'.repeat(80)},{id:'task',kind:'tasks',title:'Task',scope:'workspace-one:channel-one',initial:'independent task draft'}]),[active,setActive]=useState(null),[activation,setActivation]=useState(0);const timeline=useRef(null),composer=useRef(null);const add=(id,returnFocus)=>setEntries(old=>[...old,{id,kind:id.startsWith('task')?'tasks':'feed',title:id==='feed'?'Feed':'Another task',scope,initial:'new retained '+id,returnFocus}]);const remove=id=>setEntries(old=>old.filter(e=>e.id!==id));window.workbenchProof={setScope,setEntries,add,remove,flushSync,reveal:id=>{setActive(id);setActivation(v=>v+1)}};
return <><button id="add-feed" onClick={()=>add('feed')}>Open feed</button><button id="outside">Outside</button><div id="timeline" ref={timeline}><div style={{height:2400}}>Conversation remains independent</div></div><main><CollaborationWorkbench scope={scope} entries={entries} activeId={active} activationKey={activation} onActiveChange={setActive} timelineRef={timeline} composerRef={composer} renderEntry={(entry,{layoutActions,scopeId})=><div data-safe-namespace={scopeId}><Editor entry={entry} layout={layoutActions} add={add} remove={remove}/></div>}/></main><textarea id="composer" ref={composer} defaultValue="independent message draft"/></>}
createRoot(document.getElementById('root')).render(<App/>);'''


def main():
    hashes = lambda: {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
    before, checks, errors, unexpected = hashes(), {}, [], []
    process, stage, browser = None, 'startup', None
    started = time.monotonic()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='freecollab-concurrent-workbench-') as directory:
        private = Path(directory)
        try:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
            web = private / 'web'; web.mkdir()
            shutil.copytree(ROOT / 'frontend/src', web / 'src')
            shutil.copyfile(ROOT / 'frontend/package.json', web / 'package.json')
            (web / 'node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
            (web / 'entry.tsx').write_text(ENTRY)
            (web / 'index.html').write_text('''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>
:root{--fc-bg:#f3f0eb;--fc-surface:#fff;--fc-text:#242824;--fc-muted:#666;--fc-border:#d7d5d0;--fc-accent:#ba442f;--fc-accent-soft:#fcece7;--fc-radius:12px;--fc-font:Arial,sans-serif;--fc-body-size:16px;--fc-caption-size:12px;--fc-detail-size:14px}*{box-sizing:border-box}body{margin:16px;font:16px Arial;background:var(--fc-bg)}button,input,textarea{font:inherit}button{min-height:44px}main{margin-left:30%;width:70%;padding:8px}.fc-conversation-work{border:1px solid #ccc;border-radius:12px;background:#fff}.fc-conversation-work-header{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px}.fc-conversation-work-header h2{min-width:0;font-size:18px;overflow-wrap:anywhere}.fc-conversation-work-body{height:330px;padding:12px;overflow:auto}.fc-conversation-work-body label{display:block}.fc-conversation-work-body [hidden]{display:none}.fc-conversation-work-body textarea{display:block;width:100%;height:150px;resize:none}.fc-collaboration-work-surface[data-workspace-mode=expanded] .fc-conversation-work-body{height:auto}#timeline{position:fixed;left:16px;top:80px;width:26%;height:500px;overflow:auto}#composer{position:fixed;left:16px;bottom:16px;width:26%;height:70px}#nested{position:fixed;z-index:100;inset:25%;background:white;border:1px solid black;padding:20px}@media(max-width:700px){main{margin-left:0;width:100%;padding:0}#timeline{display:none}#composer{position:static;width:100%;margin-top:16px}body{margin:12px}}
</style></head><body><div id="root"></div><script type="module" src="/entry.tsx"></script></body></html>''')
            launcher = private / 'vite.mjs'
            launcher.write_text('import{createServer}from '+json.dumps(str(ROOT/'frontend/node_modules/vite/dist/node/index.js'))+';import react from '+json.dumps(str(ROOT/'frontend/node_modules/@vitejs/plugin-react/dist/index.js'))+';const server=await createServer({configFile:false,root:'+json.dumps(str(web))+',plugins:[react()],cacheDir:'+json.dumps(str(private/'vite-cache'))+',resolve:{alias:{react:'+json.dumps(str(ROOT/'frontend/node_modules/react'))+',"react-dom":'+json.dumps(str(ROOT/'frontend/node_modules/react-dom'))+'}},server:{host:"127.0.0.1",port:'+str(port)+',strictPort:true,hmr:false,watch:{ignored:["**"]},fs:{allow:['+json.dumps(str(web))+','+json.dumps(str(ROOT/'frontend'))+']}}});await server.listen();')
            with (private / 'vite.log').open('w') as log:
                process = subprocess.Popen(['node', str(launcher)], cwd=ROOT/'frontend', stdout=log, stderr=log, start_new_session=True)
                origin = f'http://127.0.0.1:{port}'
                deadline = time.monotonic()+30
                while True:
                    try:
                        with urlopen(origin, timeout=1) as response:
                            if response.status==200: break
                    except Exception:
                        if process.poll() is not None or time.monotonic()>deadline: raise AssertionError('Owned workbench Vite did not become ready')
                        time.sleep(.05)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True, executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), args=['--no-sandbox'])
                    context = browser.new_context(viewport={'width':1360,'height':1000})
                    def local_only(route):
                        if urlsplit(route.request.url).hostname not in ('127.0.0.1','localhost') or '/api/' in route.request.url:
                            unexpected.append(route.request.url); route.abort()
                        else: route.continue_()
                    context.route('**/*',local_only)
                    page=context.new_page(); page.on('pageerror',lambda error:errors.append(str(error)))
                    settle=lambda:page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    def reload():
                        page.goto(origin,wait_until='networkidle');expect(page.locator('#editor-document')).to_be_visible();expect(page.locator('#editor-task')).to_be_visible();settle()
                    def card(identifier):return page.locator('[data-collaboration-work-id='+json.dumps(identifier)+']')
                    reload()
                    stage='three independent live editors retain DOM and local Yjs objects'
                    page.locator('#editor-task').fill('independent task dirty draft')
                    page.evaluate('''()=>{window.docEditor=document.getElementById('editor-document');window.taskEditor=document.getElementById('editor-task');window.originalDoc=docs.get('document');window.originalTask=docs.get('task');window.initialMounts={...mounts}}''')
                    page.locator('#add-feed').click();expect(page.locator('#editor-feed')).to_be_visible();settle()
                    expect(page.locator('#editor-task')).to_have_value('independent task dirty draft')
                    assert page.evaluate("()=>docEditor===document.getElementById('editor-document')&&taskEditor===document.getElementById('editor-task')&&originalDoc===docs.get('document')&&originalTask===docs.get('task')&&mounts.document===initialMounts.document&&mounts.task===initialMounts.task")
                    checks['three_live_editors_same_dom_sessions_and_independent_drafts']=True
                    stage='expanding and restoring one card preserves selection and other drafts'
                    page.evaluate("()=>{docEditor.focus();docEditor.setSelectionRange(18,31,'backward');docEditor.scrollTop=85;document.getElementById('timeline').scrollTop=115;window.eventsBefore=inputEvents}")
                    card('document').get_by_role('button',name='Expand work area',exact=True).click();settle()
                    expect(card('document')).to_have_attribute('data-workspace-mode','expanded')
                    assert page.evaluate("()=>document.activeElement===docEditor&&docEditor.selectionStart===18&&docEditor.selectionEnd===31&&docEditor.selectionDirection==='backward'&&docEditor.scrollTop===85&&document.getElementById('timeline').scrollTop===115")
                    assert page.locator('#editor-task').evaluate("node=>!!node.closest('[inert]')")
                    card('document').get_by_role('button',name='Restore beside conversation',exact=True).click();settle()
                    expect(card('document')).to_have_attribute('data-workspace-mode','split')
                    expect(page.locator('#editor-task')).to_have_value('independent task dirty draft')
                    assert page.evaluate("()=>inputEvents===eventsBefore&&docEditor===document.getElementById('editor-document')&&originalDoc===docs.get('document')")
                    checks['per_card_fullscreen_selection_scroll_and_other_editor_state']=True
                    stage='minimized cards stay mounted and resume exact field from dock'
                    page.locator('#editor-task').focus();page.locator('#editor-task').evaluate("node=>node.setSelectionRange(3,12)")
                    card('task').get_by_role('button',name='Minimize work',exact=True).click();settle()
                    expect(card('task')).to_be_hidden();expect(page.locator('#composer')).to_be_focused()
                    assert page.evaluate("()=>taskEditor.isConnected&&taskEditor===document.getElementById('editor-task')&&originalTask===docs.get('task')")
                    page.get_by_role('button',name='Continue Task',exact=True).click();settle()
                    expect(page.locator('#editor-task')).to_be_focused()
                    assert page.locator('#editor-task').evaluate('node=>node.selectionStart===3&&node.selectionEnd===12')
                    checks['minimize_dock_resume_retains_same_editor_selection_and_session']=True
                    stage='opening work from fullscreen reveals another card and origin focus'
                    page.locator('#editor-document').focus();card('document').get_by_role('button',name='Expand work area',exact=True).click();settle()
                    page.locator('#add-document').click();settle()
                    expect(card('document')).to_have_attribute('data-workspace-mode','split')
                    extra=page.locator('[data-collaboration-work-id^="task-"]').first
                    expect(extra).to_be_visible();expect(extra).to_have_attribute('data-work-active','true')
                    expect(extra.locator('textarea')).to_have_value(__import__('re').compile('new retained task-'))
                    extra.get_by_role('button',name='Minimize work',exact=True).click();settle()
                    expect(page.locator('#editor-document')).to_be_focused()
                    checks['new_work_from_fullscreen_reveals_without_remount_and_returns_origin']=True
                    stage='nested modal escape and explicit close guard remain local'
                    card('task').get_by_role('button',name='Expand work area',exact=True).click();settle()
                    page.locator('#nested-task').click();expect(page.locator('#nested-field')).to_be_focused()
                    page.keyboard.press('Escape');expect(page.locator('#nested')).to_have_count(0);expect(card('task')).to_have_attribute('data-workspace-mode','expanded')
                    page.keyboard.press('Escape');settle();expect(card('task')).to_have_attribute('data-workspace-mode','split')
                    page.once('dialog',lambda dialog:dialog.dismiss())
                    card('task').get_by_role('button',name='Close this work',exact=True).click();expect(card('task')).to_be_visible();expect(page.locator('#editor-task')).to_have_value('independent task dirty draft')
                    page.once('dialog',lambda dialog:dialog.accept())
                    card('task').get_by_role('button',name='Close this work',exact=True).click();expect(card('task')).to_have_count(0)
                    assert page.evaluate("()=>docEditor===document.getElementById('editor-document')&&originalDoc===docs.get('document')")
                    checks['nested_escape_and_guarded_close_do_not_discard_sibling_work']=True
                    stage='later focus cancels queued restoration from another card'
                    page.evaluate("()=>{docEditor.focus();workbenchProof.flushSync(()=>workbenchProof.reveal('document'));document.getElementById('editor-feed').focus();document.getElementById('timeline').scrollTop=217}")
                    settle();expect(page.locator('#editor-feed')).to_be_focused();assert page.locator('#timeline').evaluate('node=>node.scrollTop===217')
                    checks['later_card_focus_wins_over_queued_reveal_and_scroll']=True
                    stage='retained reading page resumes expanded before modal defaults can take its focus'
                    page.locator('#read-document').click();reader=page.locator('#reader-document');expect(reader).to_be_focused()
                    page.evaluate("()=>{window.originalReader=document.getElementById('reader-document');originalReader.scrollTop=35;window.readingEvents=inputEvents}")
                    card('document').get_by_role('button',name='Expand work area',exact=True).click();settle();expect(reader).to_be_focused()
                    card('document').get_by_role('button',name='Minimize work',exact=True).click();settle();expect(card('document')).to_be_hidden();expect(page.locator('#composer')).to_be_focused()
                    page.locator('[data-collaboration-resume="document"]').click();settle()
                    expect(card('document')).to_have_attribute('data-workspace-mode','expanded');expect(reader).to_be_focused()
                    assert page.evaluate("()=>originalReader===document.getElementById('reader-document')&&docEditor===document.getElementById('editor-document')&&docEditor.hidden&&originalDoc===docs.get('document')&&originalReader.scrollTop===35&&inputEvents===readingEvents")
                    checks['retained_read_page_expanded_minimize_keyed_resume_preserves_exact_field_and_session']=True
                    stage='later sibling focus still vetoes a reading page resume and its queued scroll'
                    card('document').get_by_role('button',name='Restore beside conversation',exact=True).click();settle();expect(reader).to_be_focused()
                    card('document').get_by_role('button',name='Minimize work',exact=True).click();settle()
                    page.evaluate("()=>{workbenchProof.flushSync(()=>document.querySelector('[data-collaboration-resume=document]').click());document.getElementById('editor-feed').focus();document.getElementById('timeline').scrollTop=247}")
                    settle();expect(page.locator('#editor-feed')).to_be_focused();assert page.locator('#timeline').evaluate('node=>node.scrollTop===247')
                    checks['later_sibling_field_vetoes_reading_resume_and_scroll']=True
                    stage='mobile cards and controls stay inside320 and390 viewport'
                    for width in (390,320):
                        page.set_viewport_size({'width':width,'height':850});settle()
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                        for node in page.locator('.fc-collaboration-work-card:not([hidden]) .fc-work-view-control').all():
                            box=node.bounding_box();assert box and box['width']>=43 and box['height']>=43 and box['x']>=-1 and box['x']+box['width']<=width+1,box
                        page.screenshot(path=str(REPORT.with_name('workbench-'+str(width)+'.png')),full_page=True)
                    checks['mobile_multicard_bounds_and44px_controls']=True
                    stage='authority changes purge old fields before parent scope update'
                    page.evaluate("()=>workbenchProof.flushSync(()=>workbenchApi.applySession({access_token:'workbench-proof-b',user_id:'workbench-owner-b',token_type:'bearer',public_key:'',encrypted_private_key:null}))")
                    expect(page.locator('[data-collaboration-work-id]')).to_have_count(0)
                    expect(page.locator('[data-collaboration-resume]')).to_have_count(0)
                    assert page.evaluate("()=>!docEditor.isConnected&&!taskEditor.isConnected")
                    checks['account_change_removes_old_fields_and_parked_handles_immediately']=True
                    assert page.evaluate("()=>originalDoc.isDestroyed&&originalTask.isDestroyed")
                    stage='workspace or channel change removes stale scoped entries'
                    reload()
                    page.evaluate("()=>{window.previousDocument=document.getElementById('editor-document');workbenchProof.flushSync(()=>workbenchProof.setScope('workspace-two:channel-two'))}")
                    expect(page.locator('[data-collaboration-work-id]')).to_have_count(0)
                    expect(page.locator('[data-collaboration-resume]')).to_have_count(0)
                    assert page.evaluate('()=>!previousDocument.isConnected')
                    checks['workspace_channel_scope_change_does_not_remount_old_payload']=True
                    assert not errors,errors
                    assert not unexpected,unexpected
                    checks['no_backend_provider_or_layout_text_writes']=True
                    context.close();browser.close();browser=None
        except Exception:
            if browser:
                try:page.screenshot(path=str(REPORT.with_name('workbench-failure.png')),full_page=True)
                except Exception:pass
            raise
        finally:
            if browser:
                try:browser.close()
                except Exception:pass
            if process and process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=8)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=8)
            after=hashes()
            result={'status':'pass' if len(checks)==12 and not errors and not unexpected else 'fail','stage':stage,'checks':checks,'source_before':before,'source_after':after,'mismatched_sources':[name for name in SOURCES if before[name]!=after[name]],'errors':errors,'unexpected_requests':unexpected,'owned_process_stopped':not process or process.poll()is not None,'seconds':round(time.monotonic()-started,2),'scope':__doc__}
            REPORT.write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps({key:result[key] for key in ('status','stage','checks','mismatched_sources','owned_process_stopped','seconds')},indent=2))
    assert result['status']=='pass' and not result['mismatched_sources'],'Workbench proof failed'

if __name__=='__main__':main()
