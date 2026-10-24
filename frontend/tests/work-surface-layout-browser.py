#!/usr/bin/env python3
"""Real React/Chromium layout and modal-stack proof, with no backend or providers.

The retained editor is a small controlled fixture; these checks do not claim
coverage of a real document session or the full product layout.
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
REPORT = Path(os.getenv('WORK_SURFACE_LAYOUT_REPORT', str(ROOT.parent / '.freecollab/proof-results/work-surface-layout-20261023/hooks-browser.json')))
SOURCES = ['frontend/src/lib/useModalFocus.ts', 'frontend/src/lib/useWorkSurfaceLayout.ts', 'frontend/tests/work-surface-layout-browser.py']

ENTRY = r'''import React,{useEffect,useRef,useState}from'react';import{createRoot}from'react-dom/client';import{createPortal,flushSync}from'react-dom';
import{api}from API;import{useModalFocus}from MODAL;import{useWorkSurfaceLayout}from LAYOUT;
api.applySession({access_token:'layout-fixture-token',user_id:'layout-fixture-owner',token_type:'bearer',public_key:'',encrypted_private_key:null});
window.layoutApi=api;window.editorMounts=0;window.editorUnmounts=0;window.inputEvents=0;
function Nested({onClose}){const ref=useRef(null);useModalFocus(ref,onClose);return createPortal(<section id="nested" role="dialog" aria-modal="true" ref={ref}><input id="nested-first"/><button onClick={onClose}>Close nested</button></section>,document.body)}
function Surface(){const workRef=useRef(null),timelineRef=useRef(null),composerRef=useRef(null);const[scope,setScope]=useState('first-work'),[enabled,setEnabled]=useState(true),[nested,setNested]=useState(false),[popup,setPopup]=useState(false),[reading,setReading]=useState(false),[text,setText]=useState('retained draft\n'.repeat(90));
const layout=useWorkSurfaceLayout({workRef,timelineRef,composerRef,scope,enabled});
useModalFocus(workRef,layout.toggleExpanded,layout.mode==='expanded',{preserveInsideFocus:true,restoreFocus:false,escapePhase:'bubble'});
useEffect(()=>{++window.editorMounts;return()=>{++window.editorUnmounts}},[]);
window.layoutProof={...layout,setScope,setEnabled,setText,setReading,flushSync};
const before=()=>layout.beforeViewChange();return <><div id="timeline" ref={timelineRef}><div style={{height:2400}}>Conversation scroll fixture</div></div>
<section id="work" ref={workRef} data-mode={layout.mode} tabIndex={-1} onFocusCapture={layout.onFocusCapture}
onKeyDown={event=>{if(!window.skipSurfaceEscape&&event.key==='Escape'&&!event.defaultPrevented&&!event.nativeEvent.isComposing&&event.keyCode!==229&&!(event.target.closest('[role="dialog"]'))&&layout.mode==='expanded'){event.preventDefault();event.stopPropagation();layout.toggleExpanded()}}}>
<header><button id="toggle" onPointerDown={before} onClick={layout.toggleExpanded}>Expand or restore</button><button id="minimize" onPointerDown={before} onClick={layout.minimize}>Minimize</button><button id="resume" hidden={layout.mode!=='minimized'} onPointerDown={before} onClick={layout.resume}>Resume</button></header>
<div id="work-body" className="fc-conversation-work-body" hidden={layout.mode==='minimized'}><textarea id="editor" hidden={reading} value={text} onChange={event=>{++window.inputEvents;setText(event.target.value)}} onKeyDown={event=>{if(event.key==='Escape'&&window.preventEscape)event.preventDefault()}}/><article id="reading-page" data-work-focus hidden={!reading} tabIndex={-1}>A retained reading page with the same hidden editor.</article><input id="readonly" readOnly value="Read only selection"/><button id="open-popup" onClick={()=>setPopup(true)}>Open popup</button>
{popup&&<button id="popup" onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();setPopup(false)}}}>Reference popup</button>}
<button id="open-nested" onClick={()=>setNested(true)}>Open nested modal</button><div id="groups-scroll" className="fc-reference-groups-body" style={{height:80,overflow:'auto'}}><div style={{height:700}}>Group scroll fixture</div></div><div style={{height:1200}}>Work scroll fixture</div><button id="last-control">Last control</button></div></section>
<textarea id="composer" ref={composerRef} defaultValue="independent message draft"/>{nested&&<Nested onClose={()=>setNested(false)}/>}</>}
function App(){const[shown,setShown]=useState(true);window.hideSurface=()=>setShown(false);return <><button id="outside">Outside</button>{shown&&<Surface/>}</>}
createRoot(document.getElementById('root')).render(<App/>);'''


def main():
    hashes = lambda: {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
    before, checks, errors, unexpected, stage, process = hashes(), {}, [], [], 'startup', None
    started = time.time()
    with tempfile.TemporaryDirectory(prefix='freecollab-work-layout-') as directory:
        private = Path(directory)
        try:
            with socket.socket() as server:
                server.bind(('127.0.0.1', 0)); port = server.getsockname()[1]
            web = private / 'web'; web.mkdir()
            (web / 'index.html').write_text('''<!doctype html><html><head><style>
body{margin:20px}button,input,textarea{font:16px sans-serif}#timeline{position:absolute;top:70px;left:20px;width:30%;height:520px;overflow:auto}#work{margin-left:35%;width:55%;background:white;border:1px solid #aaa}#work header{padding:8px;display:flex;gap:5px}#work-body{height:350px;overflow:auto;padding:8px}#editor{display:block;width:90%;height:180px}#work[data-mode=expanded]{position:fixed;inset:20px;width:auto;margin:0;z-index:10}#work[data-mode=expanded] #work-body{height:calc(100vh - 120px)}#work-body[hidden],#editor[hidden],#reading-page[hidden]{display:none}#composer{position:fixed;left:20px;bottom:20px;width:25%;height:60px}#nested{position:fixed;inset:30%;z-index:20;background:#eee;padding:20px}
</style></head><body><div id="root"></div><script type="module" src="/entry.tsx"></script></body></html>''')
            source = ENTRY
            for marker, name in {'API':'api.ts','MODAL':'useModalFocus.ts','LAYOUT':'useWorkSurfaceLayout.ts'}.items():
                source = source.replace('from ' + marker, 'from ' + json.dumps(str(ROOT / 'frontend/src/lib' / name)))
            (web / 'entry.tsx').write_text(source)
            launcher = private / 'vite.mjs'
            launcher.write_text('import{createServer}from ' + json.dumps(str(ROOT / 'frontend/node_modules/vite/dist/node/index.js')) + ';import react from ' + json.dumps(str(ROOT / 'frontend/node_modules/@vitejs/plugin-react/dist/index.js')) + ';const server=await createServer({configFile:false,root:' + json.dumps(str(web)) + ',plugins:[react()],cacheDir:' + json.dumps(str(private / 'vite-cache')) + ',resolve:{alias:{react:' + json.dumps(str(ROOT / 'frontend/node_modules/react')) + ',"react-dom":' + json.dumps(str(ROOT / 'frontend/node_modules/react-dom')) + '}},server:{host:"127.0.0.1",port:' + str(port) + ',strictPort:true,hmr:false,watch:{ignored:["**"]},fs:{allow:[' + json.dumps(str(ROOT / 'frontend')) + ',' + json.dumps(str(web)) + ']}}});await server.listen();')
            with (private / 'vite.log').open('w') as log:
                process = subprocess.Popen(['node', str(launcher)], cwd=ROOT / 'frontend', stdout=log, stderr=log, start_new_session=True)
                app = f'http://127.0.0.1:{port}'
                deadline = time.monotonic() + 30
                while True:
                    try:
                        with urlopen(app, timeout=1) as response:
                            if response.status == 200: break
                    except Exception:
                        if process.poll() is not None or time.monotonic() > deadline: raise RuntimeError('Owned layout Vite process did not become ready')
                        time.sleep(.05)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True, executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), args=['--no-sandbox'])
                    context = browser.new_context(viewport={'width':1300,'height':900})
                    def local_only(route):
                        if urlsplit(route.request.url).hostname not in ['localhost','127.0.0.1'] or '/api/' in route.request.url:
                            unexpected.append(route.request.url); route.abort()
                        else: route.continue_()
                    context.route('**/*', local_only); page = context.new_page(); page.on('pageerror', lambda error: errors.append(str(error)))
                    def settle(): page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    def reload(): page.goto(app, wait_until='networkidle'); expect(page.locator('#work')).to_have_attribute('data-mode','split')
                    reload()
                    stage = 'retained DOM, field selection and scroll across pointer layout changes'
                    page.evaluate('''()=>{window.originalEditor=document.getElementById('editor');originalEditor.focus();originalEditor.setSelectionRange(17,31,'backward');originalEditor.scrollTop=120;document.getElementById('work-body').scrollTop=45;document.getElementById('timeline').scrollTop=130;document.getElementById('groups-scroll').scrollTop=220}''')
                    page.locator('#toggle').click(); settle(); expect(page.locator('#work')).to_have_attribute('data-mode','expanded')
                    assert page.evaluate("()=>document.activeElement===originalEditor&&originalEditor.selectionStart===17&&originalEditor.selectionEnd===31&&originalEditor.selectionDirection==='backward'&&originalEditor.scrollTop===120&&document.getElementById('timeline').scrollTop===130")
                    page.locator('#toggle').click(); settle(); expect(page.locator('#work')).to_have_attribute('data-mode','split')
                    assert page.evaluate("()=>originalEditor===document.getElementById('editor')&&document.activeElement===originalEditor&&document.getElementById('work-body').scrollTop===45&&document.getElementById('groups-scroll').scrollTop===220&&window.editorMounts===1&&window.editorUnmounts===0&&window.inputEvents===0")
                    checks['same_dom_selection_scroll_no_input_or_remount'] = True
                    stage = 'readonly selection and keyboard control focus'
                    page.evaluate("()=>{const field=document.getElementById('readonly');field.focus();field.setSelectionRange(2,8)}")
                    page.locator('#toggle').click(); settle(); expect(page.locator('#readonly')).to_be_focused()
                    assert page.locator('#readonly').evaluate('field=>field.selectionStart===2&&field.selectionEnd===8')
                    # A captured pointer gesture that never activated must not
                    # steal focus when the next action comes from the keyboard.
                    page.evaluate("()=>{layoutProof.beforeViewChange();document.getElementById('toggle').focus()}")
                    page.keyboard.press('Enter'); settle(); expect(page.locator('#toggle')).to_be_focused()
                    expect(page.locator('#work')).to_have_attribute('data-mode','split')
                    checks['readonly_selection_and_keyboard_controls'] = True
                    stage = 'minimize/resume remembers split and expanded modes'
                    for mode in ['split','expanded']:
                        if mode == 'expanded': page.locator('#toggle').click(); settle()
                        page.evaluate("()=>{const field=document.getElementById('editor');field.focus();field.setSelectionRange(40,49);document.getElementById('work-body').scrollTop=70}")
                        page.locator('#minimize').click(); settle(); expect(page.locator('#work')).to_have_attribute('data-mode','minimized'); expect(page.locator('#composer')).to_be_focused()
                        page.locator('#resume').click(); settle(); expect(page.locator('#work')).to_have_attribute('data-mode', mode)
                        assert page.evaluate("()=>document.activeElement.id") == 'editor', {'resume_mode':mode,'dom':page.evaluate("()=>({active:document.activeElement.id,mode:document.getElementById('work').dataset.mode,bodyScroll:document.getElementById('work-body').scrollTop,editorInert:!!document.getElementById('editor').closest('[inert]'),editorRects:document.getElementById('editor').getClientRects().length})")}
                        assert page.locator('#editor').evaluate('field=>field.selectionStart===40&&field.selectionEnd===49')
                        assert page.locator('#work-body').evaluate('body=>body.scrollTop===70')
                    checks['minimize_resume_retains_last_mode_field_and_scroll'] = True
                    stage = 'reading page and stale hidden editor focus across retained work modes'
                    # A disconnected layout is not required: a real hidden
                    # textarea stays mounted behind the focused reading page.
                    page.evaluate("()=>{document.getElementById('editor').focus();layoutProof.flushSync(()=>layoutProof.setReading(true));document.getElementById('open-popup').focus()}")
                    page.locator('#toggle').click(); settle(); expect(page.locator('#work')).to_have_attribute('data-mode','split')
                    expect(page.locator('#open-popup')).to_be_focused(); expect(page.locator('#editor')).to_be_hidden()
                    for mode in ['split','expanded']:
                        if mode == 'expanded': page.locator('#toggle').click(); settle()
                        page.locator('#reading-page').focus()
                        page.evaluate("()=>{window.originalReader=document.getElementById('reading-page');document.getElementById('work-body').scrollTop=35}")
                        page.locator('#minimize').click(); settle(); expect(page.locator('#composer')).to_be_focused()
                        page.locator('#resume').click(); settle(); expect(page.locator('#work')).to_have_attribute('data-mode',mode)
                        expect(page.locator('#reading-page')).to_be_focused()
                        assert page.evaluate("()=>originalReader===document.getElementById('reading-page')&&originalEditor===document.getElementById('editor')&&originalEditor.isConnected&&originalEditor.hidden&&document.getElementById('work-body').scrollTop===35&&window.editorMounts===1&&window.editorUnmounts===0&&window.inputEvents===0")
                    page.evaluate("()=>{layoutProof.flushSync(()=>layoutProof.setReading(false));document.getElementById('editor').focus()}")
                    checks['reading_page_resume_skips_retained_hidden_editor'] = True
                    stage = 'nested popup, IME and modal stack Escape/Tab priority'
                    page.locator('#open-popup').click(); page.locator('#popup').focus(); page.keyboard.press('Escape'); settle()
                    expect(page.locator('#popup')).to_have_count(0); expect(page.locator('#work')).to_have_attribute('data-mode','expanded')
                    page.evaluate("()=>{window.preventEscape=true;document.getElementById('editor').focus()}"); page.keyboard.press('Escape'); settle(); expect(page.locator('#work')).to_have_attribute('data-mode','expanded')
                    page.evaluate("()=>{window.preventEscape=false;const field=document.getElementById('editor');field.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true,isComposing:true}));field.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true,keyCode:229}))}"); settle(); expect(page.locator('#work')).to_have_attribute('data-mode','expanded')
                    page.locator('#open-nested').click(); expect(page.locator('#nested-first')).to_be_focused(); page.keyboard.press('Escape'); settle(); expect(page.locator('#nested')).to_have_count(0); expect(page.locator('#work')).to_have_attribute('data-mode','expanded'); expect(page.locator('#open-nested')).to_be_focused()
                    page.locator('#last-control').focus(); page.keyboard.press('Tab'); expect(page.locator('#toggle')).to_be_focused(); page.keyboard.press('Shift+Tab'); expect(page.locator('#last-control')).to_be_focused()
                    page.evaluate('()=>{window.skipSurfaceEscape=true}')
                    page.keyboard.press('Escape'); settle(); expect(page.locator('#work')).to_have_attribute('data-mode','split'); expect(page.locator('#last-control')).to_be_focused()
                    assert page.locator('#composer').evaluate('element=>!element.closest("[inert]")')
                    checks['nested_modal_popup_default_prevented_ime_and_tab'] = True
                    stage = 'queued restores reject scope, credential and deliberate focus changes'
                    page.evaluate("()=>{document.getElementById('editor').focus();layoutProof.beforeViewChange();layoutProof.flushSync(()=>layoutProof.toggleExpanded());layoutProof.flushSync(()=>layoutProof.setScope('other-work'));document.getElementById('outside').focus()}"); settle(); expect(page.locator('#work')).to_have_attribute('data-mode','split'); expect(page.locator('#outside')).to_be_focused()
                    reload()
                    page.evaluate("()=>{document.getElementById('editor').focus();layoutProof.beforeViewChange();document.getElementById('toggle').focus();layoutProof.flushSync(()=>layoutProof.toggleExpanded());layoutApi.applySession({access_token:'rotated-fixture-token',user_id:'rotated-fixture-owner',token_type:'bearer',public_key:'',encrypted_private_key:null})}"); settle(); expect(page.locator('#toggle')).to_be_focused()
                    reload()
                    page.evaluate("()=>{document.getElementById('editor').focus();layoutProof.beforeViewChange();document.getElementById('toggle').focus();layoutProof.flushSync(()=>layoutProof.toggleExpanded());layoutApi.applySession({access_token:'layout-fixture-token',user_id:'another-fixture-owner',token_type:'bearer',public_key:'',encrypted_private_key:null})}"); settle(); expect(page.locator('#toggle')).to_be_focused()
                    reload()
                    page.evaluate("()=>{document.getElementById('editor').focus();layoutProof.beforeViewChange();document.getElementById('toggle').focus();layoutProof.flushSync(()=>layoutProof.toggleExpanded());layoutProof.flushSync(()=>layoutProof.setEnabled(false));document.getElementById('outside').focus()}"); settle(); expect(page.locator('#outside')).to_be_focused(); expect(page.locator('#work')).to_have_attribute('data-mode','split')
                    reload()
                    page.evaluate("()=>{document.getElementById('editor').focus();layoutProof.beforeViewChange();document.getElementById('toggle').focus();layoutProof.flushSync(()=>layoutProof.toggleExpanded());document.getElementById('last-control').focus()}"); settle(); expect(page.locator('#last-control')).to_be_focused()
                    checks['queued_focus_fenced_by_scope_actor_token_and_new_focus'] = True
                    stage = 'reset and unmount cancel deferred focus without editor mutation'
                    page.evaluate("()=>{layoutProof.flushSync(()=>layoutProof.reset());document.getElementById('outside').focus()}"); settle(); expect(page.locator('#outside')).to_be_focused()
                    assert page.evaluate("()=>window.editorMounts===1&&window.editorUnmounts===0&&window.inputEvents===0")
                    page.evaluate("()=>{document.getElementById('editor').focus();layoutProof.beforeViewChange();layoutProof.flushSync(()=>layoutProof.toggleExpanded());layoutProof.flushSync(()=>hideSurface());document.getElementById('outside').focus()}"); settle(); expect(page.locator('#outside')).to_be_focused(); assert page.evaluate('()=>window.editorUnmounts===1')
                    checks['reset_unmount_cancel_deferred_restore'] = True
                    browser.close()
            assert not errors, errors
            assert not unexpected, unexpected
            checks['loopback_only_no_backend_provider_or_editor_writes'] = True
        finally:
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try: process.wait(timeout=8)
                except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=8)
            after = hashes(); REPORT.parent.mkdir(parents=True, exist_ok=True)
            result = {'status':'pass' if len(checks)==8 and not errors and not unexpected else 'fail','stage':stage,'checks':checks,'source_before':before,'source_after':after,'source_mismatch':[name for name in SOURCES if before[name]!=after[name]],'errors':errors,'unexpected_requests':unexpected,'owned_process_stopped':not process or process.poll() is not None,'duration_seconds':round(time.time()-started,2),'scope':'Real Chromium and actual hooks with retained controlled React editor fixture; no full-product editor/session or deployed service proof.'}
            REPORT.write_text(json.dumps(result, indent=2)+'\n')
            print(json.dumps(result, indent=2))
    assert result['status']=='pass' and not result['source_mismatch'], 'Layout hook proof failed'


if __name__ == '__main__': main()
