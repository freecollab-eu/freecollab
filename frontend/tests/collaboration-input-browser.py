#!/usr/bin/env python3
"""Actual shared authoring component with explicit host and HTTP fixtures.

This checks the shared input/provider contract, not business persistence or a
native/Matrix deployment. The actual product flows have separate browser tests.
"""
import hashlib
import json
import os
from pathlib import Path
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
REPORT = Path(os.getenv('COLLABORATION_INPUT_REPORT', str(ROOT.parent / '.freecollab/proof-results/collaboration-input-browser.json')))
SOURCES = ['frontend/src/components/CollaborationInput.tsx', 'frontend/src/lib/collaboration-context.tsx',
           'frontend/src/lib/useComposerReferences.ts', 'frontend/src/components/MentionReferences.tsx',
           'frontend/src/styles/collaboration-input.css', 'frontend/src/styles/panels-files.css',
           'frontend/src/styles/file-experience.css', 'frontend/tests/collaboration-input-browser.py']
ENTRY = '''import React,{useState,useRef}from'react';import{createRoot}from'react-dom/client';import{flushSync}from'react-dom';
import './src/styles/panels-files.css';import './src/styles/file-experience.css';import{api}from'./src/lib/api';import{CollaborationInput}from'./src/components/CollaborationInput';import{CollaborationProvider}from'./src/lib/collaboration-context';
api.applySession({access_token:'isolated-writing-session',user_id:'writing-owner',token_type:'bearer',public_key:'',encrypted_private_key:null});
const commands={hasChannel:true,hasWorkspace:true,secure:false,embedded:false,nativeSession:true,workspaceRole:'member',hasAdmin:false};
function App(){const[state,setState]=useState({text:'',refs:[],instance:0,mode:'accept',type:'textarea',max:50000,transform:false,fileModal:false}),[native,setNative]=useState(''),[sensitive,setSensitive]=useState('');const live=useRef(state);live.current=state;
const records=useRef([]),saved=useRef(null),resolver=useRef(null),submits=useRef(0),paletteActive=useRef(false),explicitSaves=useRef(0);
window.writingFixture={reset:(next={})=>flushSync(()=>{records.current=[];saved.current=null;setState({...live.current,text:'',refs:[],mode:'accept',type:'textarea',max:50000,transform:false,fileModal:false,...next,instance:live.current.instance+1})}),set:(text,refs=[])=>flushSync(()=>setState(s=>({...s,text,refs}))),snapshot:()=>({text:live.current.text,refs:live.current.refs,records:records.current,submits:submits.current,paletteActive:paletteActive.current,explicitSaves:explicitSaves.current,origin:saved.current?{value:saved.current.value,selectionStart:saved.current.selectionStart,selectionEnd:saved.current.selectionEnd,current:saved.current.isCurrent()}:null}),resolve:accepted=>resolver.current?.(accepted),restore:()=>saved.current?.restore(),invalidate:()=>api.applySession({access_token:'rotated-writing-session',user_id:'another-writing-owner',token_type:'bearer',public_key:'',encrypted_private_key:null})};
return <><button id="other">Independent work</button><section id="modal" className={state.fileModal?'fc-file-preview-layer':undefined} role="dialog" aria-modal="true" style={state.transform?{transform:state.transform===true?'translate(17px, 13px)':state.transform}:{}}><section className={state.fileModal?'fc-file-preview':undefined}>{state.fileModal&&<><header>File preview layout fixture</header><div className="fc-file-preview-content">Preview material fixture</div></>}<div className={state.fileModal?'fc-file-experience-scroll':undefined}><section className={state.fileModal?'fc-file-experience':undefined}><section className={state.fileModal?'fc-file-discussion':undefined}><CollaborationProvider value={{surfaceId:'writing-surface',workspaceId:'writing-workspace',channelId:'writing-channel',commandContext:commands,onCommand:(command,intent,origin)=>{records.current.push({command:command.id,intent,people:origin.personIds,context:origin.contextId});saved.current=origin;if(live.current.mode==='delay')return new Promise(resolve=>resolver.current=resolve);return live.current.mode!=='reject'}}}>
<form onSubmit={event=>{event.preventDefault();submits.current++}}><CollaborationInput id="writing" aria-label="Shared writing" as={state.type} contextId={'document:'+state.instance} value={state.text} references={state.refs} maxLength={state.max} toolbar submissionScope="writing-save" onPaletteActiveChange={active=>paletteActive.current=active} onValueChange={(text,refs)=>setState(s=>({...s,text,refs}))}/><button id="save" type="submit">Save fixture</button><button id="explicit-save" type="button" data-collaboration-submit="writing-save" onClick={()=>{if(!paletteActive.current)explicitSaves.current++}}>Publish fixture</button></form>
<CollaborationInput id="sensitive" aria-label="Private fixture" as="input" type="password" contextId="private" value={sensitive} onValueChange={setSensitive}/>
</CollaborationProvider></section></section></div>{state.fileModal&&<footer>File preview layout fixture</footer>}</section></section><CollaborationInput id="native" aria-label="Standalone writing" as="input" contextId="standalone" value={native} onValueChange={setNative}/></>}
createRoot(document.getElementById('root')).render(<React.StrictMode><App/></React.StrictMode>);'''
STYLE = ':root{--fc-bg:#fff;--fc-surface:#fafafa;--fc-text:#222;--fc-muted:#555;--fc-border:#ccc;--fc-accent:#8c3e30;--fc-accent-soft:#f5eee9;--fc-font:sans-serif;--fc-heading-font:sans-serif;--fc-radius:8px;--fc-body-size:16px;--fc-caption-size:12px;--fc-detail-size:13px}*{box-sizing:border-box}body{font-family:sans-serif;margin:0;padding:8px}#modal{position:relative;max-width:650px;margin:20px 0}#writing{height:150px;font:16px sans-serif}#modal form{padding-top:240px}#modal.fc-file-preview-layer{position:fixed;max-width:none;margin:0}#modal.fc-file-preview-layer form{padding-top:0}#native,#sensitive{display:block;max-width:200px}button{min-height:44px}'


def main():
    hashes = lambda: {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
    before, checks, errors, unexpected, requests, stage = hashes(), {}, [], [], [], 'startup'
    process, browser, context = None, None, None
    started = time.monotonic(); REPORT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='freecollab-writing-') as directory:
        private = Path(directory)
        try:
            with socket.socket() as connection:
                connection.bind(('127.0.0.1', 0)); port = connection.getsockname()[1]
            app = f'http://127.0.0.1:{port}'
            web = private / 'web'; web.mkdir(); shutil.copytree(ROOT / 'frontend/src', web / 'src')
            (web / 'node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
            (web / 'index.html').write_text('<!doctype html><html lang="en"><head><meta charset="UTF-8"><style>' + STYLE + '</style></head><body><div id="root"></div><script type="module" src="/entry.tsx"></script></body></html>')
            (web / 'entry.tsx').write_text(ENTRY)
            launcher = private / 'vite.mjs'
            launcher.write_text('import{createServer}from ' + json.dumps(str(ROOT / 'frontend/node_modules/vite/dist/node/index.js')) + ';import react from ' + json.dumps(str(ROOT / 'frontend/node_modules/@vitejs/plugin-react/dist/index.js')) + ';const server=await createServer({configFile:false,root:' + json.dumps(str(web)) + ',plugins:[react()],cacheDir:' + json.dumps(str(private / 'vite-cache')) + ',resolve:{alias:{react:' + json.dumps(str(ROOT / 'frontend/node_modules/react')) + ',"react-dom":' + json.dumps(str(ROOT / 'frontend/node_modules/react-dom')) + '}},server:{host:"127.0.0.1",port:' + str(port) + ',strictPort:true,hmr:false,watch:{ignored:["**"]},fs:{allow:[' + json.dumps(str(ROOT / 'frontend')) + ',' + json.dumps(str(web)) + ']}}});await server.listen();')
            with (private / 'vite.log').open('w') as stream:
                process = subprocess.Popen(['node', str(launcher)], cwd=ROOT / 'frontend', env={**os.environ, 'VITE_API_URL': '/api'}, stdout=stream, stderr=stream, start_new_session=True)
                deadline = time.monotonic() + 45
                while True:
                    assert process.poll() is None, 'Owned writing fixture exited before readiness'
                    try:
                        with urlopen(app, timeout=1) as response:
                            if response.status == 200: break
                    except Exception:
                        if time.monotonic() >= deadline: raise
                        time.sleep(.1)
                with sync_playwright() as playwright:
                    launch = {'headless': True, 'args': ['--no-sandbox']}
                    executable = os.getenv('CHROMIUM_PATH') or shutil.which('chromium')
                    if executable: launch['executable_path'] = executable
                    browser = playwright.chromium.launch(**launch)
                    context = browser.new_context(viewport={'width': 900, 'height': 1000}, reduced_motion='reduce')
                    def route(request):
                        url = request.request.url; path = urlsplit(url).path
                        if not url.startswith(app + '/'):
                            unexpected.append(url); request.abort(); return
                        if not path.startswith('/api/'):
                            request.continue_(); return
                        requests.append({'method': request.request.method, 'path': path})
                        assert request.request.method == 'GET' and path == '/api/mentions/channels/writing-channel/suggestions', 'Unexpected shared-writing API request'
                        request.fulfill(status=200, json=[{'kind': 'person', 'id': 'colleague-' + str(index), 'label': 'Colleague ' + str(index).zfill(2), 'channel_id': 'writing-channel'} for index in range(40)])
                    context.route('**/*', route)
                    page = context.new_page(); page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(app, wait_until='domcontentloaded'); field = page.locator('#writing'); expect(field).to_be_visible(timeout=90000)
                    def reset(values=None):
                        page.evaluate('values=>writingFixture.reset(values)', values or {})
                        expect(page.locator('.fc-collaboration-popup')).to_have_count(0)
                    def snapshot(): return page.evaluate('()=>writingFixture.snapshot()')
                    def command(text):
                        field.fill(text); expect(page.locator('.fc-collaboration-popup .fc-slash-command').first).to_be_visible(); field.press('Enter')
                    try:
                        stage = 'current command consumes only its line and restores the exact source caret'
                        prefix = '@Earlier remains.\n'; suffix = '\nAfter remains.'; text = prefix + '/task Review this' + suffix
                        reset({'text': text, 'refs': [{'kind': 'person', 'id': 'earlier-account', 'start': 0, 'end': 8}]})
                        field.focus(); field.evaluate('(node,end)=>node.setSelectionRange(end,end)', len(prefix + '/task Review this'))
                        field.press('ArrowLeft'); field.press('ArrowRight')
                        expect(page.locator('.fc-slash-command')).to_be_visible(); field.press('Enter')
                        expect(field).to_have_value(prefix + suffix)
                        assert snapshot()['paletteActive'] is False
                        state = snapshot(); assert state['records'][0]['command'] == 'task' and state['records'][0]['people'] == []
                        assert state['refs'] == [{'kind': 'person', 'id': 'earlier-account', 'start': 0, 'end': 8}]
                        page.locator('#other').focus(); assert page.evaluate('()=>writingFixture.restore()')
                        expect(field).to_be_focused(); assert field.evaluate('node=>node.selectionStart') == len(prefix)
                        checks['accepted_current_line_and_exact_origin'] = True

                        stage = 'rejected host and Escape keep literal source'
                        reset({'mode': 'reject'}); command('/docs'); expect(field).to_have_value('/docs')
                        field.press('Escape'); expect(page.locator('.fc-collaboration-popup')).to_have_count(0); expect(field).to_have_value('/docs')
                        checks['rejection_and_escape_keep_source'] = True

                        stage = 'asynchronous host cannot erase a coauthor edit or a different context'
                        reset({'mode': 'delay'}); command('/docs'); assert len(snapshot()['records']) == 1
                        page.evaluate('()=>writingFixture.set("Coauthor text remains")'); page.evaluate('()=>writingFixture.resolve(true)')
                        expect(field).to_have_value('Coauthor text remains'); assert not page.evaluate('()=>writingFixture.restore()')
                        reset({'mode': 'delay'}); command('/docs'); reset({'text': '/docs'})
                        page.evaluate('()=>writingFixture.resolve(true)'); expect(field).to_have_value('/docs')
                        reset({'mode': 'delay'}); command('/docs'); field.press('Escape')
                        page.evaluate('()=>writingFixture.resolve(true)'); expect(field).to_have_value('/docs')
                        reset({'mode': 'delay', 'text': '@Earlier\n/docs', 'refs': [{'kind': 'person', 'id': 'before-person', 'start': 0, 'end': 8}]})
                        field.focus(); field.evaluate('node=>node.setSelectionRange(node.value.length,node.value.length)'); field.press('ArrowLeft'); field.press('ArrowRight'); field.press('Enter')
                        assert len(snapshot()['records']) == 1
                        page.evaluate('()=>writingFixture.set("@Earlier\\n/docs",[{kind:"person",id:"coauthor-person",start:0,end:8}])')
                        page.evaluate('()=>writingFixture.resolve(true)'); expect(field).to_have_value('@Earlier\n/docs')
                        assert snapshot()['refs'][0]['id'] == 'coauthor-person'
                        checks['stale_async_host_preserves_changed_source'] = True

                        stage = 'feature browsing preserves document text and native form cannot submit a command'
                        reset({'text': 'A complete document.'}); page.get_by_role('button', name='Browse collaboration features', exact=True).click()
                        page.get_by_role('combobox', name='Search features', exact=True).fill('docs')
                        page.locator('.fc-slash-command').first.click(); expect(field).to_have_value('A complete document.')
                        reset({'type': 'input'}); field.fill('/docs'); expect(page.locator('.fc-slash-command')).to_be_visible()
                        assert page.locator('form').evaluate('node=>node.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}))') is False
                        page.locator('#save').click(); expect(field).to_have_value('/docs'); assert snapshot()['submits'] == 0
                        page.locator('#explicit-save').click(); expect(field).to_have_value('/docs'); assert snapshot()['explicitSaves'] == 0 and snapshot()['paletteActive'] is True
                        field.focus()
                        field.press('Enter'); expect(field).to_have_value(''); assert snapshot()['submits'] == 0
                        checks['browse_preserves_document_and_form_guard'] = True

                        stage = 'actual reference list retains keyboard identity under a stationary pointer'
                        reset(); field.fill('@C'); options = page.locator('.fc-reference-candidate'); expect(options).to_have_count(40)
                        page.locator('#save').click(); assert snapshot()['submits'] == 0 and snapshot()['paletteActive'] is True
                        page.locator('#explicit-save').click(); assert snapshot()['explicitSaves'] == 0 and snapshot()['paletteActive'] is True
                        field.focus()
                        options.first.hover(); field.press('Home')
                        for index in range(1, 19):
                            field.press('ArrowDown'); expect(options.nth(index)).to_have_attribute('aria-selected', 'true')
                        field.press('Enter'); expect(field).to_have_value('@Colleague 18 ')
                        assert snapshot()['refs'] == [{'kind': 'person', 'id': 'colleague-18', 'start': 0, 'end': 13}]
                        checks['reference_keyboard_identity_with_stationary_pointer'] = True

                        stage = 'exact selected person in this command becomes the only proposed owner'
                        reset(); field.fill('/task Review @C'); expect(options).to_have_count(40)
                        field.press('ArrowDown'); field.press('Enter'); expect(field).to_have_value('/task Review @Colleague 01 ')
                        expect(page.locator('.fc-slash-command')).to_be_visible(); field.press('Enter'); expect(field).to_have_value('')
                        assert snapshot()['records'][0]['people'] == ['colleague-1']
                        checks['command_uses_only_explicit_selected_person'] = True

                        stage = 'native fallback and sensitive inputs do not intercept writing'
                        reset(); page.locator('#native').fill('/docs @C'); page.locator('#sensitive').fill('/docs @C')
                        expect(page.locator('.fc-collaboration-popup')).to_have_count(0); assert snapshot()['records'] == []
                        checks['native_fallback_and_sensitive_inputs'] = True

                        stage = 'menu stays in its modal and within narrow viewport bounds'
                        page.set_viewport_size({'width': 320, 'height': 560}); reset()
                        field.fill('/'); menu = page.locator('.fc-collaboration-popup > section'); expect(menu).to_be_visible()
                        bounds = menu.bounding_box(); assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 320.5 and bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 560.5, bounds
                        assert menu.evaluate('node=>!!node.closest("[role=dialog]")')
                        field.press('Escape'); reset({'transform': True}); field.fill('/')
                        expect(menu).to_be_visible(); bounds = menu.bounding_box()
                        assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 320.5 and bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 560.5, bounds
                        field.press('Escape'); reset({'transform': 'translate(17px, 13px) scale(.85)'}); field.fill('/')
                        expect(menu).to_be_visible(); bounds = menu.bounding_box()
                        assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 320.5 and bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 560.5, bounds
                        menu.locator('[role="option"][id$="-task"]').click(); expect(field).to_have_value('')
                        checks['narrow_modal_and_transformed_ancestor_geometry'] = True

                        stage = 'actual file CSS nested scroll and backdrop layout retains pointer-accessible options'
                        page.set_viewport_size({'width': 1440, 'height': 1100}); reset({'fileModal': True})
                        field.fill('The file discussion stays.\n/task "A task from this file"')
                        expect(menu).to_be_visible(); bounds = menu.bounding_box()
                        assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 1440.5 and bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 1100.5, bounds
                        menu.locator('[role="option"][id$="-task"]').click(); expect(field).to_have_value('The file discussion stays.\n')
                        assert snapshot()['records'][0]['command'] == 'task' and snapshot()['submits'] == 0
                        field.fill('@C'); expect(page.locator('.fc-reference-candidate')).to_have_count(40)
                        page.locator('.fc-reference-candidate').nth(2).click(); expect(field).to_have_value('@Colleague 02 ')
                        assert snapshot()['refs'][0]['id'] == 'colleague-2'
                        checks['actual_file_css_scroller_backdrop_pointer_choices'] = True
                        assert not errors and not unexpected
                    finally:
                        context.close(); browser.close(); context = browser = None
        finally:
            if context: context.close()
            if browser: browser.close()
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try: process.wait(timeout=8)
                except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=8)
            after = hashes()
            result = {'ok': len(checks) == 9 and not errors and not unexpected, 'stage': stage, 'checks': checks, 'source_before': before, 'source_after': after,
                      'source_mismatch': [name for name in SOURCES if before[name] != after[name]], 'errors': errors, 'unexpected_requests': unexpected,
                      'requests': requests, 'owned_process_stopped': not process or process.poll() is not None, 'duration_seconds': round(time.monotonic() - started, 2),
                      'scope': 'Actual shared React input/provider and reference HTTP fixtures; not product persistence, native/Matrix or external provider acceptance.'}
            REPORT.write_text(json.dumps(result, indent=2) + '\n'); print(json.dumps({'ok': result['ok'], 'stage': stage, 'checks': len(checks), 'report': str(REPORT)}))
    assert result['ok'] and not result['source_mismatch'], 'Shared writing contract proof failed'


if __name__ == '__main__': main()
