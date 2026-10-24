#!/usr/bin/env python3
"""Actual CloudFilesPicker focus ownership with delayed same-origin HTTP errors.

The surrounding composer/menu/dialog controls and HTTP responses are isolated
fixtures. This proves the picker focus contract, not live provider consent or
the complete Chat surface. --baseline reproduces the pre-fix component.
"""
import argparse
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
REPORT = Path(os.getenv('CLOUD_PICKER_FOCUS_REPORT', str(ROOT.parent / '.freecollab/proof-results/cloud-picker-focus-browser.json')))
SOURCES = ['frontend/src/components/CloudFilesPicker.tsx', 'frontend/src/lib/cloud-files-api.ts',
           'frontend/src/lib/api.ts', 'frontend/tests/cloud-picker-focus-browser.py']
BASELINE = '8d82936926285dfc023ff297b014e02cf66d2dc7'
ENTRY = '''import React,{useState}from'react';import{createRoot}from'react-dom/client';import{createPortal,flushSync}from'react-dom';
import{api}from'./src/lib/api';import{CloudFilesPicker}from'./src/components/CloudFilesPicker';
api.applySession({access_token:'private-focus-session',user_id:'focus-owner',token_type:'bearer',public_key:'',encrypted_private_key:null});
function App(){const[mode,setMode]=useState('visible'),[dialog,setDialog]=useState(false),[nested,setNested]=useState(false),[mounted,setMounted]=useState(true);
window.focusFixture={mode:value=>flushSync(()=>setMode(value)),dialog:()=>flushSync(()=>setDialog(true)),nested:()=>flushSync(()=>setNested(true)),unmount:()=>flushSync(()=>setMounted(false)),rotate:()=>api.applySession({access_token:'rotated-private-focus-session',user_id:'another-focus-owner',token_type:'bearer',public_key:'',encrypted_private_key:null})};
return <><textarea id="composer" aria-label="Conversation draft" defaultValue="Unsent conversation draft"/><div id="features" role="listbox" aria-label="Feature fixture"><button role="option" id="feature-option">Meetings</button></div>
<div id="work" role={mode==='host-dialog'?'dialog':undefined} aria-modal={mode==='host-dialog'?true:undefined} hidden={mode==='hidden'} inert={mode==='inert'?'':undefined} aria-hidden={mode==='aria-hidden'?true:undefined} style={mode==='offscreen'?{transform:'translateY(3000px)'}:mode==='css-hidden'?{visibility:'hidden'}:{}}>
{mounted&&<CloudFilesPicker workspaceId="focus-workspace" channelId="focus-channel" onClose={()=>setMounted(false)}/>}</div>
{dialog&&<section id="other-dialog" role="dialog" aria-modal="true"><button id="dialog-control">Another dialog</button></section>}
{nested&&document.querySelector('.cloud-picker')&&createPortal(<section role="dialog" aria-modal="true"><button id="nested-control">Nested dialog</button></section>,document.querySelector('.cloud-picker'))}</>}
createRoot(document.getElementById('root')).render(<App/>);'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', action='store_true')
    options = parser.parse_args()
    hashes = lambda: {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
    before, checks, errors, unexpected, requests, stage = hashes(), {}, [], [], [], 'startup'
    process, browser, context = None, None, None
    started = time.monotonic()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='freecollab-cloud-focus-') as directory:
        private = Path(directory)
        try:
            with socket.socket() as connection:
                connection.bind(('127.0.0.1', 0)); port = connection.getsockname()[1]
            app = f'http://127.0.0.1:{port}'
            web = private / 'web'; web.mkdir()
            shutil.copytree(ROOT / 'frontend/src', web / 'src')
            (web / 'node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
            if options.baseline:
                original = subprocess.check_output(['git', 'show', BASELINE + ':frontend/src/components/CloudFilesPicker.tsx'], cwd=ROOT)
                (web / 'src/components/CloudFilesPicker.tsx').write_bytes(original)
            loaded_component = hashlib.sha256((web / 'src/components/CloudFilesPicker.tsx').read_bytes()).hexdigest()
            (web / 'index.html').write_text('<!doctype html><html lang="en"><head><meta charset="UTF-8"><style>:root{--fc-bg:#fff;--fc-surface:#fafafa;--fc-text:#222;--fc-muted:#555;--fc-border:#ccc;--fc-accent:#8c3e30;--fc-accent-soft:#f5eee9;--fc-font:sans-serif;--fc-heading-font:sans-serif;--fc-radius:8px}body{font-family:sans-serif}#work{max-width:650px}#composer{display:block}#other-dialog{position:fixed;inset:20px;background:white;z-index:100}</style></head><body><div id="root" data-demo-application></div><script type="module" src="/entry.tsx"></script></body></html>')
            (web / 'entry.tsx').write_text(ENTRY)
            launcher = private / 'vite.mjs'
            launcher.write_text('import{createServer}from ' + json.dumps(str(ROOT / 'frontend/node_modules/vite/dist/node/index.js')) + ';import react from ' + json.dumps(str(ROOT / 'frontend/node_modules/@vitejs/plugin-react/dist/index.js')) + ';const server=await createServer({configFile:false,root:' + json.dumps(str(web)) + ',plugins:[react()],cacheDir:' + json.dumps(str(private / 'vite-cache')) + ',resolve:{alias:{react:' + json.dumps(str(ROOT / 'frontend/node_modules/react')) + ',"react-dom":' + json.dumps(str(ROOT / 'frontend/node_modules/react-dom')) + '}},server:{host:"127.0.0.1",port:' + str(port) + ',strictPort:true,hmr:false,watch:{ignored:["**"]},fs:{allow:[' + json.dumps(str(ROOT / 'frontend')) + ',' + json.dumps(str(web)) + ']}}});await server.listen();')
            with (private / 'vite.log').open('w') as stream:
                environment = {**os.environ, 'VITE_API_URL': '/api'}
                process = subprocess.Popen(['node', str(launcher)], cwd=ROOT / 'frontend', env=environment, stdout=stream, stderr=stream, start_new_session=True)
                deadline = time.monotonic() + 45
                while True:
                    assert process.poll() is None, 'Owned focus fixture exited before readiness'
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

                    def start():
                        nonlocal context
                        if context: context.close()
                        context = browser.new_context(viewport={'width': 1000, 'height': 1100}, reduced_motion='reduce')
                        pending = []
                        def route(request):
                            url = request.request.url; path = urlsplit(url).path
                            if not url.startswith(app + '/'):
                                unexpected.append(url); request.abort(); return
                            if not path.startswith('/api/'):
                                request.continue_(); return
                            method = request.request.method
                            requests.append({'method': method, 'path': path})
                            assert method == 'GET', 'Focus fixture attempted a mutation'
                            if path.endswith('/cloud-files/connections'): pending.append(request)
                            elif path.endswith('/cloud-files/links'): request.fulfill(status=200, json={'items': [], 'next_before': None})
                            else: raise AssertionError('Unexpected API path: ' + path)
                        context.route('**/*', route)
                        page = context.new_page(); page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(app, wait_until='domcontentloaded')
                        expect(page.locator('.cloud-picker')).to_be_visible(timeout=90000)
                        deadline = time.monotonic() + 15
                        while not pending:
                            assert time.monotonic() < deadline, 'Actual overview request did not start'
                            page.wait_for_timeout(20)
                        return page, pending

                    def fail(page, pending):
                        pending.pop(0).fulfill(status=503, json={'detail': 'Private delayed cloud configuration unavailable.'})
                        expect(page.locator('.cloud-error')).to_be_visible()
                        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')

                    try:
                        stage = 'owned visible picker retains accessible error focus and real retry'
                        page, pending = start()
                        page.get_by_role('button', name='Close cloud files', exact=True).focus()
                        fail(page, pending); expect(page.get_by_role('alert')).to_be_focused()
                        page.get_by_role('button', name='Reload cloud connections', exact=True).click()
                        deadline = time.monotonic() + 15
                        while not pending:
                            assert time.monotonic() < deadline; page.wait_for_timeout(20)
                        pending.pop(0).fulfill(status=200, json={'secure': False, 'can_manage': False, 'sync_enabled': False, 'callback_url': '', 'sync_callback_url': '', 'connections': []})
                        expect(page.get_by_role('alert')).to_have_count(0)
                        expect(page.get_by_role('tab', name='Provider links', exact=True)).to_be_visible()
                        checks['owned_picker_error_and_actual_retry'] = True

                        stage = 'owned picker inside its host dialog keeps error focus'
                        page, pending = start(); page.evaluate("()=>focusFixture.mode('host-dialog')")
                        page.get_by_role('button', name='Close cloud files', exact=True).focus()
                        fail(page, pending); expect(page.get_by_role('alert')).to_be_focused()
                        checks['owned_picker_inside_host_dialog'] = True
    
                        for name, target in [('composer', '#composer'), ('feature_menu', '#feature-option')]:
                            stage = 'late error preserves ' + name
                            page, pending = start(); page.locator(target).focus()
                            fail(page, pending); expect(page.locator(target)).to_be_focused()
                            expect(page.locator('#composer')).to_have_value('Unsent conversation draft')
                            checks['late_error_preserves_' + name] = True
    
                        for name, control, target in [('other_dialog', 'dialog', '#dialog-control'), ('nested_dialog', 'nested', '#nested-control')]:
                            stage = 'late error preserves ' + name
                            page, pending = start(); page.evaluate('name=>focusFixture[name]()', control); page.locator(target).focus()
                            fail(page, pending); expect(page.locator(target)).to_be_focused()
                            checks['late_error_preserves_' + name] = True
    
                        for mode in ['hidden', 'inert', 'aria-hidden', 'css-hidden', 'offscreen']:
                            stage = 'late error cannot refocus work that is ' + mode
                            page, pending = start(); page.get_by_role('button', name='Close cloud files', exact=True).focus()
                            page.evaluate('mode=>focusFixture.mode(mode)', mode)
                            if mode not in ['offscreen', 'aria-hidden']: page.locator('#composer').focus()
                            pending.pop(0).fulfill(status=503, json={'detail': 'Private delayed cloud configuration unavailable.'})
                            page.wait_for_function('document.querySelector(".cloud-error")?.textContent.includes("Private delayed")')
                            page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                            assert not page.locator('.cloud-error').evaluate('node=>node===document.activeElement')
                            if mode not in ['offscreen', 'aria-hidden']: expect(page.locator('#composer')).to_be_focused()
                            checks['late_error_ignores_' + mode] = True
    
                        for change in ['rotate', 'unmount']:
                            stage = 'late result after ' + change
                            page, pending = start(); page.evaluate('name=>focusFixture[name]()', change); page.locator('#composer').focus()
                            pending.pop(0).fulfill(status=503, json={'detail': 'Private delayed cloud configuration unavailable.'})
                            page.wait_for_timeout(100); expect(page.locator('#composer')).to_be_focused()
                            expect(page.locator('.cloud-error')).to_have_count(0)
                            checks['stale_result_after_' + change + '_ignored'] = True
                        assert not errors and not unexpected
                    finally:
                        if context: context.close()
                        if browser: browser.close()
                        context = browser = None
        finally:
            if context: context.close()
            if browser: browser.close()
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try: process.wait(timeout=8)
                except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=8)
            after = hashes()
            result = {'ok': len(checks) == 13 and not errors and not unexpected, 'stage': stage, 'baseline': options.baseline,
                      'component_sha256': locals().get('loaded_component'), 'checks': checks, 'source_before': before,
                      'source_after': after, 'source_mismatch': [name for name in SOURCES if before[name] != after[name]],
                      'errors': errors, 'unexpected_requests': unexpected, 'requests': requests,
                      'owned_process_stopped': not process or process.poll() is not None,
                      'duration_seconds': round(time.monotonic() - started, 2),
                      'scope': 'Actual React picker, isolated surrounding controls and delayed HTTP fixtures; not full Chat or live provider acceptance.'}
            REPORT.write_text(json.dumps(result, indent=2) + '\n')
            print(json.dumps({'ok': result['ok'], 'stage': stage, 'checks': len(checks), 'report': str(REPORT)}))
    assert result['ok'] and not result['source_mismatch'], 'Cloud picker focus proof failed'


if __name__ == '__main__': main()
