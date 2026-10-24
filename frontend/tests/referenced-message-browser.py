#!/usr/bin/env python3
"""Actual React reference lifecycle/continuation proof with controlled promises.

The real ReferencedMessage, FieldReferenceText and CollaborationProvider render
in StrictMode. Only source resolution and the host's second await are explicit
fixture boundaries; they deliberately deliver after abort. This verifies local
lifetime/intent leases, not native API permissions, Matrix or provider delivery.
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
REPORT = Path(os.environ.get('REFERENCED_MESSAGE_REPORT', str(ROOT.parent / '.freecollab/proof-results/universal-collaboration-20261024/referenced-message/browser.json')))
SOURCES = ['frontend/src/components/ReferencedMessage.tsx', 'frontend/src/components/FieldReferenceText.tsx',
           'frontend/src/lib/collaboration-context.tsx', 'frontend/src/lib/useCollaborationModal.tsx',
           'frontend/src/lib/useModalFocus.ts', 'frontend/src/lib/message-references-api.ts',
           'frontend/src/lib/message-references.ts', 'frontend/src/lib/api.ts', 'frontend/src/i18n/index.tsx',
           'frontend/src/i18n/core.ts', 'frontend/src/styles/message-references.css', 'frontend/tests/referenced-message-browser.py']
ENTRY = r'''import React,{StrictMode,useState}from'react';import{createRoot}from'react-dom/client';import{flushSync}from'react-dom';
import{api}from'./src/lib/api';import{ReferencedMessage}from'./src/components/ReferencedMessage';import{FieldReferenceText}from'./src/components/FieldReferenceText';import{CollaborationProvider,useCollaborationContext}from'./src/lib/collaboration-context';import{messageReferencesApi}from'./src/lib/message-references-api';
const session=(actor='reference-owner',token='reference-fixture-session')=>({access_token:token,user_id:actor,token_type:'bearer',public_key:'',encrypted_private_key:null});api.applySession(session());
const pending=[],events=[],captured=[],elements=[];let sequence=0,holdResolve=false,holdHost=false,currentContext=null;
const row=(id='plan',available=true)=>({kind:'document',id,label:id==='plan'?'Plan':'Notes',available,channel_id:'reference-channel'});
function deferred(kind,metadata){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});const item={id:++sequence,kind,...metadata,resolve,reject,settled:false};pending.push(item);return{promise,item}}
messageReferencesApi.resolve=async(channel,references,signal)=>{const rows=references.map(reference=>row(reference.id));if(!holdResolve)return rows;holdResolve=false;const{promise,item}=deferred('resolve',{signal,rows});await promise;return item.available===false?rows.map(value=>({...value,available:false})):rows};
function Reader({state,host}){const context=useCollaborationContext();currentContext=context;const label=state.source==='plan'?'Plan':'Notes',content='Read @'+label,references=[{kind:'document',id:state.source,start:5,end:content.length}];return <article id="source-card"><h2>Actual source card</h2>{state.direct?<ReferencedMessage content={content} references={references} channelId="reference-channel" workspaceId="reference-workspace" onOpen={(reference,lease)=>host(reference,undefined,lease?.isCurrent,lease?.element)}/>:<FieldReferenceText value={content} references={references} channelId="reference-channel"/>}</article>}
function App(){const[state,setState]=useState({shown:true,source:'plan',surface:'surface-a',revision:0,epoch:0,direct:true}),[other,setOther]=useState(0);const host=async(reference,_origin,sourceCurrent,sourceElement)=>{elements.push({exact:!!sourceElement&&sourceElement===window.referenceClickedElement,activeElsewhere:document.activeElement!==sourceElement,disabled:!!sourceElement?.disabled,nodeName:sourceElement?.nodeName,referenceId:sourceElement?.dataset.referenceId,connected:!!sourceElement?.isConnected});if(holdHost){holdHost=false;const{promise}=deferred('host',{sourceCurrent,sourceElement});await promise}if(sourceCurrent&&!sourceCurrent())return;events.push({id:reference.id,label:reference.label,revision:state.revision,surface:state.surface})};
window.referenceProbe={reset:(direct=true)=>{holdResolve=false;holdHost=false;pending.length=0;events.length=0;captured.length=0;elements.length=0;api.applySession(session());flushSync(()=>setState(value=>({shown:true,source:'plan',surface:'surface-a',revision:0,epoch:value.epoch+1,direct})))},source:value=>flushSync(()=>setState(state=>({...state,source:value}))),surface:value=>flushSync(()=>setState(state=>({...state,surface:value}))),revision:value=>flushSync(()=>setState(state=>({...state,revision:value}))),remove:()=>flushSync(()=>setState(state=>({...state,shown:false}))),show:()=>flushSync(()=>setState(state=>({...state,shown:true}))),hold:kind=>{if(kind==='resolve')holdResolve=true;else holdHost=true},release:(id,available=true)=>{const item=pending.find(row=>row.id===id);if(!item||item.settled)throw Error('Missing owned pending operation');item.available=available;item.settled=true;item.resolve()},reject:id=>{const item=pending.find(row=>row.id===id);item.settled=true;item.reject(Error('Controlled source transport failure'))},snapshot:()=>({events,elements,pending:pending.map(item=>({id:item.id,kind:item.kind,settled:item.settled,aborted:item.signal?.aborted||false,current:item.sourceCurrent?.()})),captured:captured.length}),capture:()=>{captured.push(currentContext.onOpenReference);return captured.length-1},invoke:index=>captured[index](row()),invalidate:()=>api.applySession(session('another-reference-owner','rotated-reference-fixture-session'))};
const value={surfaceId:state.surface,workspaceId:'reference-workspace',channelId:'reference-channel',secure:false,commandContext:{hasChannel:true,hasWorkspace:true,secure:false,embedded:false,nativeSession:true,workspaceRole:'member',hasAdmin:false},onCommand:()=>true,onOpenReference:host};
return <main><h1>Reference lifetime fixture</h1><label htmlFor="independent">Independent writing</label><textarea id="independent"/><button id="other" onClick={()=>setOther(value=>value+1)}>Other control {other}</button>{state.shown&&<CollaborationProvider key={state.epoch} value={value}><Reader state={state} host={host}/></CollaborationProvider>}</main>}
createRoot(document.getElementById('root')).render(<StrictMode><App/></StrictMode>);'''
STYLE = ':root{--fc-accent:#87533c;--fc-accent-soft:#f2e7df;--fc-muted:#645e58;--fc-border:#cfc7bf}body{font:16px sans-serif;margin:32px;color:#282420;background:#f7f4ef}button{min-height:44px}article{margin-top:24px;padding:20px;border:1px solid #ccc}textarea{display:block;margin:8px 0}'


def main():
    before = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
    checks, errors, unexpected, observations = {}, [], [], []
    process = browser = context = None
    started = time.monotonic()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='freecollab-reference-lifetime-') as directory:
        private = Path(directory)
        try:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
            app = 'http://127.0.0.1:' + str(port)
            web = private / 'web'; web.mkdir()
            shutil.copytree(ROOT / 'frontend/src', web / 'src')
            override = os.environ.get('REFERENCED_MESSAGE_BASELINE_FILE')
            if override:
                shutil.copyfile(override, web / 'src/components/ReferencedMessage.tsx')
                observations.append({'baseline_component_sha256': hashlib.sha256(Path(override).read_bytes()).hexdigest(), 'scope': 'Historical ReferencedMessage only; all other current fixture dependencies unchanged.'})
            copied = {name: hashlib.sha256((web / Path(name).relative_to('frontend')).read_bytes()).hexdigest() for name in SOURCES if name.startswith('frontend/src/')}
            (web / 'node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
            (web / 'entry.tsx').write_text(ENTRY)
            (web / 'index.html').write_text('<!doctype html><html lang="en"><head><style>' + STYLE + '</style></head><body><div id="root"></div><script type="module" src="/entry.tsx"></script></body></html>')
            launcher = private / 'vite.mjs'
            launcher.write_text('import{createServer}from ' + json.dumps(str(ROOT / 'frontend/node_modules/vite/dist/node/index.js')) + ';import react from ' + json.dumps(str(ROOT / 'frontend/node_modules/@vitejs/plugin-react/dist/index.js')) + ';const server=await createServer({configFile:false,root:' + json.dumps(str(web)) + ',plugins:[react()],cacheDir:' + json.dumps(str(private / 'cache')) + ',server:{host:"127.0.0.1",port:' + str(port) + ',strictPort:true,hmr:false,watch:{ignored:["**"]},fs:{allow:' + json.dumps([str(private), str(ROOT / 'frontend/node_modules')]) + '}}});await server.listen();')
            with (private / 'vite.log').open('w') as stream:
                process = subprocess.Popen(['node', str(launcher)], cwd=ROOT / 'frontend', env=os.environ.copy(), stdout=stream, stderr=stream, start_new_session=True)
                deadline = time.monotonic() + 45
                while True:
                    assert process.poll() is None, 'Owned reference fixture exited before readiness'
                    try:
                        with urlopen(app, timeout=1) as response:
                            if response.status == 200: break
                    except Exception:
                        if time.monotonic() >= deadline: raise
                        time.sleep(.1)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH') or shutil.which('chromium'), headless=True, args=['--no-sandbox'])
                    context = browser.new_context(viewport={'width': 800, 'height': 650}, locale='en-US', reduced_motion='reduce')
                    def boundary(route):
                        if urlsplit(route.request.url).netloc != urlsplit(app).netloc:
                            unexpected.append(urlsplit(route.request.url).netloc); route.abort()
                        else: route.continue_()
                    context.route('**/*', boundary)
                    page = context.new_page(); page.set_default_timeout(15000)
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(app, wait_until='domcontentloaded')
                    def chip(label='Plan'):
                        return page.get_by_role('button', name='Open reference ' + label, exact=True)
                    def reset(direct=True):
                        page.evaluate('direct=>referenceProbe.reset(direct)', direct)
                        expect(chip()).to_be_visible(); expect(chip()).to_be_enabled()
                        expect(chip().locator('span')).to_have_text('@Plan')
                    def snapshot(): return page.evaluate('referenceProbe.snapshot()')
                    def hold(kind):
                        previous = {item['id'] for item in snapshot()['pending']}
                        page.evaluate('kind=>referenceProbe.hold(kind)', kind)
                        chip().evaluate('node=>{window.referenceClickedElement=node}')
                        chip().click()
                        page.wait_for_function('kind=>referenceProbe.snapshot().pending.some(item=>item.kind===kind&&!item.settled)', arg=kind)
                        return next(item['id'] for item in snapshot()['pending'] if item['kind'] == kind and item['id'] not in previous)
                    def release(identifier):
                        page.evaluate('id=>referenceProbe.release(id)', identifier)
                        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    def no_open():
                        value = snapshot()
                        if value['events']: observations.append({'unexpected_navigation': value})
                        assert not value['events'], 'An invalidated source navigated after its late response'
                    def mark(name): checks[name] = True; print('PASS: ' + name, flush=True)

                    reset(); request = hold('resolve'); release(request)
                    assert snapshot()['events'] == [{'id': 'plan', 'label': 'Plan', 'revision': 0, 'surface': 'surface-a'}]
                    expect(chip()).to_be_enabled()
                    mark('current_actual_chip_remains_visible_and_navigates_once_after_fresh_resolution')

                    reset(); request = hold('resolve'); page.evaluate('referenceProbe.remove()')
                    expect(page.locator('#source-card')).to_have_count(0); release(request); no_open()
                    mark('actual_react_unmount_rejects_late_fresh_resolution_even_when_transport_delivers_after_abort')

                    reset(); old = hold('resolve'); page.evaluate('referenceProbe.remove()'); page.evaluate('referenceProbe.show()')
                    expect(chip()).to_be_enabled(); release(old); no_open()
                    current = hold('resolve'); release(current); assert len(snapshot()['events']) == 1
                    mark('same_source_remount_cannot_revive_old_click_but_new_click_still_works')

                    reset(); old = hold('resolve'); page.evaluate("referenceProbe.source('notes')")
                    expect(chip('Notes')).to_be_enabled(); page.evaluate("referenceProbe.source('plan')"); expect(chip()).to_be_enabled()
                    current = hold('resolve'); release(old); no_open(); expect(chip()).to_be_disabled()
                    release(current); assert len(snapshot()['events']) == 1
                    mark('source_A_B_A_and_old_finally_cannot_navigate_or_release_the_new_opening_state')

                    reset(); request = hold('resolve'); page.evaluate('referenceProbe.revision(7)'); release(request)
                    assert snapshot()['events'][0]['revision'] == 7
                    mark('unchanged_current_source_uses_the_latest_callback_without_stale_parent_state')

                    reset(); request = hold('resolve'); page.evaluate('referenceProbe.invalidate()'); release(request); no_open()
                    mark('actual_actor_token_transition_invalidates_the_old_click')

                    reset(False); request = hold('host')
                    assert snapshot()['elements'][-1]['exact'], 'FieldReferenceText/provider must forward the actual clicked source element'
                    page.evaluate('referenceProbe.remove()'); release(request); no_open()
                    mark('real_FieldReferenceText_lease_blocks_the_hosts_second_await_after_source_unmount')

                    reset(False); old = page.evaluate('referenceProbe.capture()'); page.evaluate('referenceProbe.remove()')
                    page.evaluate('index=>referenceProbe.invoke(index)', old); no_open(); page.evaluate('referenceProbe.show()'); expect(chip()).to_be_visible()
                    page.evaluate('index=>referenceProbe.invoke(index)', old); no_open()
                    current = page.evaluate('referenceProbe.capture()'); page.evaluate('index=>referenceProbe.invoke(index)', current)
                    assert len(snapshot()['events']) == 1
                    mark('actual_StrictMode_provider_remount_rejects_captured_old_callback_and_accepts_new_owner')

                    reset(False); old = page.evaluate('referenceProbe.capture()')
                    page.evaluate("referenceProbe.surface('surface-b')"); page.evaluate("referenceProbe.surface('surface-a')")
                    page.evaluate('index=>referenceProbe.invoke(index)', old); no_open()
                    current = page.evaluate('referenceProbe.capture()'); page.evaluate('index=>referenceProbe.invoke(index)', current)
                    assert len(snapshot()['events']) == 1
                    mark('provider_scope_A_B_A_does_not_revive_old_callback_authority')

                    reset(); request = hold('resolve'); page.locator('#independent').focus(); page.locator('#independent').press('x'); release(request)
                    no_open(); expect(page.locator('#independent')).to_have_value('x'); expect(chip()).to_be_enabled()
                    mark('new_real_typing_intent_cancels_pending_reference_without_erasing_either_label_or_text')

                    reset(); request = hold('resolve'); page.get_by_role('button', name='Other control', exact=False).click(); release(request); no_open()
                    expect(chip()).to_be_visible(); expect(chip()).to_be_enabled()
                    mark('new_real_pointer_control_intent_cancels_a_late_reference_open')

                    reset(False); request = hold('host'); page.locator('#independent').focus(); page.locator('#independent').press('y'); release(request); no_open()
                    mark('new_typing_during_the_hosts_second_await_invalidates_the_forwarded_source_lease')

                    reset(); request = hold('resolve'); expect(chip()).to_be_disabled(); page.locator('#independent').focus(); release(request)
                    assert len(snapshot()['events']) == 1
                    source = snapshot()['elements'][-1]
                    assert source == {'exact': True, 'activeElsewhere': True, 'disabled': True, 'nodeName': 'BUTTON', 'referenceId': 'plan', 'connected': True}, 'A late source must use click currentTarget even after disabled focus moved elsewhere'
                    mark('programmatic_layout_focus_preserves_current_open_and_the_exact_disabled_clicked_element')
                    page.screenshot(path=str(REPORT.with_name('reference-lifetime-current.png')))
                    assert not errors and not unexpected
                    context.close(); context = None; browser.close(); browser = None
        except Exception:
            if browser:
                try: page.screenshot(path=str(REPORT.with_name('reference-lifetime-failure.png')), full_page=True)
                except Exception: pass
            raise
        finally:
            if context:
                try: context.close()
                except Exception as failure: observations.append({'context_cleanup_after_driver_exit': type(failure).__name__})
            if browser:
                try: browser.close()
                except Exception as failure: observations.append({'browser_cleanup_after_driver_exit': type(failure).__name__})
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
            after = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
            result = {'checks': checks, 'source_before': before, 'source_after': after, 'source_mismatches': [name for name in SOURCES if before[name] != after[name]], 'actual_copied_sources': locals().get('copied', {}), 'observations': observations,
                      'errors': errors, 'unexpected_requests': unexpected, 'owned_process_stopped': process is None or process.poll() is not None,
                      'seconds': round(time.monotonic() - started, 2), 'scope': __doc__}
            REPORT.write_text(json.dumps(result, indent=2) + '\n')
            print(json.dumps({'checks': len(checks), 'source_mismatches': result['source_mismatches'], 'owned_process_stopped': result['owned_process_stopped']}), flush=True)
    assert len(checks) == 13 and not result['source_mismatches']


if __name__ == '__main__': main()
