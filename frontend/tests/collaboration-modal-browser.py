#!/usr/bin/env python3
"""Real Chromium proof of the shared modal parking/continuation contract.

An explicit local React host exercises the production provider, registration
hook and focus stack. Full product command handoff has separate actual-API proof.
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
REPORT = Path(os.getenv('COLLABORATION_MODAL_REPORT', str(ROOT.parent / '.freecollab/proof-results/universal-collaboration-20261024/modal-collaboration/hooks-browser.json')))
SOURCES = ['frontend/src/lib/collaboration-context.tsx', 'frontend/src/lib/useCollaborationModal.tsx', 'frontend/src/lib/useModalFocus.ts', 'frontend/tests/collaboration-modal-browser.py']
ENTRY = r'''import React,{useEffect,useRef,useState}from'react';import{createRoot}from'react-dom/client';import{createPortal}from'react-dom';
import{api}from'./src/lib/api';import{CollaborationProvider}from'./src/lib/collaboration-context';import{ModalContinuationProvider,useCollaborationModal}from'./src/lib/useCollaborationModal';import{useModalFocus}from'./src/lib/useModalFocus';
api.applySession({access_token:'modal-fixture-session',user_id:'modal-fixture-owner',token_type:'bearer',public_key:'',encrypted_private_key:null});
const entries=new Map();let retained=[],source=null;window.fixture={entries,retained:()=>retained,current:()=>retained.map(entry=>entry.isCurrent()),rotate:()=>api.applySession({access_token:'modal-fixture-rotated',user_id:'another-owner',token_type:'bearer',public_key:'',encrypted_private_key:null})};
function Modal({name,close,children}){const ref=useRef(null),modal=useCollaborationModal(ref,name);useModalFocus(ref,close,true,{suspended:modal.parked});useEffect(()=>{window.mounts[name]=(window.mounts[name]||0)+1;return()=>{window.unmounts[name]=(window.unmounts[name]||0)+1}},[]);return createPortal(<ModalContinuationProvider modalId={modal.modalId}><section ref={ref} id={name} hidden={modal.parked} style={{display:modal.parked?'none':undefined}} role="dialog" aria-modal="true"><button onClick={close}>Close {name}</button>{children}</section></ModalContinuationProvider>,document.body)}
function App(){const[outer,setOuter]=useState(true),[inner,setInner]=useState(false),[target,setTarget]=useState(false),[revision,setRevision]=useState(0);const[text,setText]=useState('The same modal draft.');window.mounts=window.mounts||{};window.unmounts=window.unmounts||{};window.fixture.closeOuter=()=>setOuter(false);window.fixture.renderHost=()=>setRevision(value=>value+1);
function openTarget(){source=document.getElementById('source');window.sourceNode=source;window.sourceSelection=[source.selectionStart,source.selectionEnd,source.selectionDirection];let entry=[...entries.values()].find(entry=>entry.root.contains(source));retained=[];while(entry){retained.push(entry);entry=entries.get(entry.parentId)};for(const modal of retained)modal.park();setTarget(true)}
function resume(){for(const modal of retained)modal.resume();requestAnimationFrame(()=>{source.focus();source.setSelectionRange(...window.sourceSelection)})}
return <CollaborationProvider value={{surfaceId:'original-surface',workspaceId:'modal-workspace',channelId:'modal-channel',commandContext:{hasChannel:true,hasWorkspace:true,secure:false,embedded:false,nativeSession:true,hasAdmin:false},onCommand:()=>true,onRegisterModal:entry=>{entries.set(entry.id,entry);return()=>{if(entries.get(entry.id)===entry)entries.delete(entry.id)}}}}><main data-revision={revision}><input id="outside" defaultValue="Independent draft"/>{target&&<section id="target"><label>Task title<input id="task-title"/></label><button id="continue" onClick={resume}>Continue source</button><button id="close-target" onClick={()=>{setTarget(false);resume()}}>Close task</button></section>}</main>{outer&&<Modal name="outer" close={()=>setOuter(false)}><button id="open-inner" onClick={()=>setInner(true)}>Open inner</button>{inner&&<Modal name="inner" close={()=>setInner(false)}><div id="source-scroll"><textarea id="source" value={text} onChange={event=>setText(event.target.value)}/><div style={{height:900}}>Retained scroll</div></div><button id="open-target" onMouseDown={event=>event.preventDefault()} onClick={openTarget}>Open task</button></Modal>}</Modal>}</CollaborationProvider>}
createRoot(document.getElementById('root')).render(<App/>);'''
STYLE = 'body{margin:12px;overflow:auto}button,input,textarea{font:16px sans-serif;min-height:44px}section[role=dialog]{position:fixed;inset:70px 15%;padding:20px;background:#fff;border:2px solid #555}#inner{inset:110px 20%}#source-scroll{height:210px;overflow:auto}#source{height:90px;width:90%}#target{padding:20px}#target input{display:block}[hidden]{display:none!important}'


def main():
    hashes = lambda: {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
    before, checks, errors, unexpected, process, stage = hashes(), {}, [], [], None, 'startup'
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='freecollab-modal-proof-') as directory:
        private = Path(directory)
        try:
            with socket.socket() as connection:
                connection.bind(('127.0.0.1', 0)); port = connection.getsockname()[1]
            app = f'http://127.0.0.1:{port}'
            web = private / 'web'; web.mkdir(); shutil.copytree(ROOT / 'frontend/src', web / 'src')
            (web / 'node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
            (web / 'index.html').write_text('<!doctype html><html><head><meta charset="utf-8"><style>' + STYLE + '</style></head><body><div id="root"></div><script type="module" src="/entry.tsx"></script></body></html>')
            (web / 'entry.tsx').write_text(ENTRY)
            launcher = private / 'vite.mjs'
            launcher.write_text('import{createServer}from ' + json.dumps(str(ROOT / 'frontend/node_modules/vite/dist/node/index.js')) + ';import react from ' + json.dumps(str(ROOT / 'frontend/node_modules/@vitejs/plugin-react/dist/index.js')) + ';const server=await createServer({configFile:false,root:' + json.dumps(str(web)) + ',plugins:[react()],cacheDir:' + json.dumps(str(private / 'vite-cache')) + ',resolve:{alias:{react:' + json.dumps(str(ROOT / 'frontend/node_modules/react')) + ',"react-dom":' + json.dumps(str(ROOT / 'frontend/node_modules/react-dom')) + '}},server:{host:"127.0.0.1",port:' + str(port) + ',strictPort:true,hmr:false,watch:{ignored:["**"]},fs:{allow:[' + json.dumps(str(ROOT / 'frontend')) + ',' + json.dumps(str(web)) + ']}}});await server.listen();')
            with (private / 'vite.log').open('w') as stream:
                process = subprocess.Popen(['node', str(launcher)], cwd=ROOT / 'frontend', stdout=stream, stderr=stream, start_new_session=True)
                deadline = time.monotonic() + 30
                while True:
                    try:
                        with urlopen(app, timeout=1) as response:
                            if response.status == 200: break
                    except Exception:
                        if process.poll() is not None or time.monotonic() >= deadline: raise
                        time.sleep(.1)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True, executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), args=['--no-sandbox'])
                    context = browser.new_context(viewport={'width':1100, 'height':800})
                    def route(request):
                        if urlsplit(request.request.url).hostname not in ['127.0.0.1', 'localhost'] or '/api/' in request.request.url:
                            unexpected.append(request.request.url); request.abort()
                        else: request.continue_()
                    context.route('**/*', route)
                    page = context.new_page(); page.on('pageerror', lambda error: errors.append(str(error)))
                    settle = lambda: page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    page.goto(app, wait_until='networkidle'); expect(page.locator('#outer')).to_be_visible()
                    page.locator('#open-inner').click(); expect(page.locator('#inner')).to_be_visible()
                    page.wait_for_function('()=>fixture.entries.size===2')
                    stage = 'retained source and parent portal ancestry'
                    page.locator('#source').fill('The same modal draft.\nUnrelated writing survives.')
                    page.evaluate("()=>{source.focus();source.setSelectionRange(4,14,'backward');document.getElementById('source-scroll').scrollTop=7}")
                    page.locator('#open-target').click(); settle()
                    expect(page.locator('#inner')).to_be_hidden(); expect(page.locator('#outer')).to_be_hidden()
                    assert page.evaluate("()=>fixture.retained().length===2&&fixture.retained().every(entry=>entry.surfaceId==='original-surface'&&entry.isCurrent())")
                    retained_source = page.evaluate("()=>({same:sourceNode===document.getElementById('source'),value:sourceNode.value,selection:[sourceNode.selectionStart,sourceNode.selectionEnd],mounts:mounts.inner,unmounts:unmounts.inner||0})")
                    assert retained_source == {'same':True, 'value':'The same modal draft.\nUnrelated writing survives.', 'selection':[4,14], 'mounts':1, 'unmounts':0}, retained_source
                    checks['nested_portal_parent_and_exact_source_dom_draft_selection_and_scroll_are_retained'] = True
                    stage = 'background accessibility and registration stability'
                    page.locator('#task-title').fill('Reachable related task')
                    expect(page.locator('#task-title')).to_be_focused()
                    assert page.evaluate("()=>getComputedStyle(document.body).overflow==='auto'&&!document.getElementById('root').inert")
                    page.evaluate('()=>fixture.renderHost()'); settle()
                    assert page.evaluate('()=>fixture.entries.size===2&&fixture.current().every(Boolean)')
                    checks['parked_modals_release_focus_isolation_and_scroll_lock_without_reregistering'] = True
                    stage = 'nested stack order and exact source restoration'
                    page.locator('#continue').click(); settle(); expect(page.locator('#source')).to_be_focused()
                    restored_source = page.evaluate("()=>({start:sourceNode.selectionStart,end:sourceNode.selectionEnd,direction:sourceNode.selectionDirection,scroll:document.getElementById('source-scroll').scrollTop,overflow:document.body.style.overflow})")
                    assert restored_source == {'start':4, 'end':14, 'direction':'backward', 'scroll':7, 'overflow':'hidden'}, restored_source
                    page.evaluate("()=>document.getElementById('open-inner').focus()")
                    assert page.evaluate("()=>document.getElementById('inner').contains(document.activeElement)")
                    page.keyboard.press('Escape'); expect(page.locator('#inner')).to_have_count(0); expect(page.locator('#outer')).to_be_visible()
                    expect(page.locator('#open-inner')).to_be_focused()
                    checks['resuming_child_before_parent_keeps_child_top_and_escape_restores_parent_focus'] = True
                    stage = 'stale scope invalidation'
                    page.locator('#open-inner').click(); page.wait_for_function('()=>fixture.entries.size===2')
                    page.locator('#open-target').click(); settle()
                    page.evaluate('()=>fixture.rotate()')
                    assert page.evaluate('()=>fixture.current().every(current=>!current)')
                    page.locator('#continue').click(); settle(); expect(page.locator('#inner')).to_be_hidden(); expect(page.locator('#outer')).to_be_hidden()
                    page.evaluate('()=>fixture.closeOuter()'); settle()
                    assert page.evaluate('()=>fixture.entries.size===0&&fixture.current().every(current=>!current)')
                    checks['rotated_identity_and_unmounted_source_cancel_parked_continuations'] = True
                    assert not errors and not unexpected, {'errors':errors, 'unexpected':unexpected}
                    assert hashes() == before, 'Source changed during the isolated proof'
                    browser.close()
            REPORT.write_text(json.dumps({'ok':True, 'scope':__doc__, 'checks':checks, 'sourceHashes':before, 'pageErrors':errors, 'unexpectedRequests':unexpected}, indent=2))
            print(json.dumps({'ok':True, 'checks':checks}))
        except Exception as failure:
            REPORT.write_text(json.dumps({'ok':False, 'stage':stage, 'failure':repr(failure), 'checks':checks, 'sourceHashes':before, 'pageErrors':errors, 'unexpectedRequests':unexpected}, indent=2))
            raise
        finally:
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL)


if __name__ == '__main__': main()
