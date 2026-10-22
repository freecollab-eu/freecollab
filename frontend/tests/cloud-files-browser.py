#!/usr/bin/env python3
"""Actual Chromium picker, live native API and four pinned private HTTPS providers.

Every account, database, key, TLS server and Vite process is private and removed.
No external provider, real OAuth consent or retained installation is contacted.
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('cloud_proof_helpers', Path(__file__).with_name('remote-sync-browser.py'))
helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(helpers)
PASSWORD = 'Private-cloud-picker!2026'
REPORT = ROOT.parent / '.freecollab/proof-results/cloud-files-browser.json'


def main():
    processes, streams, results, stage = [], [], {}, 'private startup'
    with tempfile.TemporaryDirectory(prefix='freecollab-cloud-picker-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = helpers.port(), helpers.port()
            base, app = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
            env = os.environ.copy()
            env.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(private / 'proof.db'), STORAGE_PATH=str(private / 'files'), JWT_SECRET=secrets.token_urlsafe(48),
                ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof': base64.b64encode(secrets.token_bytes(32)).decode()}),
                MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof', ACCOUNT_SECURITY_KEY_FILE=str(private / 'account.key'), MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'message.keys'), MATRIX_ENABLED='false',
                MAIL_DELIVERY_MODE='disabled', PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]), OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', OTRUST_ENABLED='false',
                SYNC_ENABLED='true', CLOUD_FILES_ENABLED='true', PYTHONPATH=str(ROOT / 'backend'))
            env['NO_PROXY'] = ','.join(filter(None, [env.get('NO_PROXY', ''), '127.0.0.1', 'localhost', '::1']))
            python = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)
            launcher = private / 'api.py'
            launcher.write_text('''import json
from pathlib import Path
import pytest
import uvicorn
from tests.test_cloud_file_providers import https
patch=pytest.MonkeyPatch()
fixture=https.__wrapped__(Path(__file__).parent,patch)
state=next(fixture)
from app.main import app,socket_app
@app.get('/__cloud-proof-evidence')
def evidence(): return {'requests':[{'host':r['host'],'method':r['method'],'path':r['path'],'authorized':'Authorization' in r['headers']} for r in state['requests']], 'connects':state['connects']}
@app.post('/__cloud-proof-reconfigure')
def reconfigure():
    from sqlmodel import Session
    from app.database import engine
    from app.models.cloud_files import CloudFileConnection
    with Session(engine) as session:
        row=session.get(CloudFileConnection,'dropbox');row.revision+=1
        value=json.loads(row.config_json);value['client_id']='replacement-customer-app';row.config_json=json.dumps(value)
        session.add(row);session.commit()
    return {'ok':True}
try: uvicorn.run(socket_app,host="127.0.0.1",port=''' + str(api_port) + ''',proxy_headers=False,access_log=False)
finally:
    Path(__file__).with_name('provider-evidence.json').write_text(json.dumps({'requests':[{'host':r['host'],'method':r['method'],'path':r['path'],'authorized':'Authorization' in r['headers']} for r in state['requests']], 'connects':state['connects']}))
    fixture.close();patch.undo()
''')
            snapshot = private / 'frontend'
            shutil.copytree(ROOT / 'frontend', snapshot, ignore=shutil.ignore_patterns('node_modules','dist','tests','.vite'))
            (snapshot/'node_modules').symlink_to(ROOT/'frontend/node_modules', target_is_directory=True)
            vite = helpers.vite_launcher(private, base, app_port)
            source = vite.read_text().replace(json.dumps(str(ROOT / 'frontend')), json.dumps(str(snapshot))).replace("strictPort:true,proxy:", "strictPort:true,fs:{allow:" + json.dumps([str(ROOT / 'frontend'), str(private)]) + "},proxy:")
            source = 'import {readFileSync} from "node:fs";\n' + source.replace('plugins:[react()]', 'plugins:[react(),{name:"private-cloud-proof",configureServer(server){server.middlewares.use("/cloud-proof",async(req,res)=>{res.setHeader("Content-Type","text/html");res.end(await server.transformIndexHtml("/cloud-proof.html",readFileSync(' + json.dumps(str(private / 'cloud-proof.html')) + ',"utf8")));});}}]')
            vite.write_text(source)
            for command, name, cwd in [([python, str(launcher)], 'api.log', ROOT / 'backend'), (['node', str(vite)], 'vite.log', ROOT / 'frontend')]:
                stream = (private / name).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=env, stdout=stream, stderr=stream, start_new_session=True))
            helpers.ready(base + '/ready', processes); helpers.ready(app, processes)
            stage = 'private account and customer grants'
            owner = helpers.api(base, 'POST', '/auth/register', data={'name': 'Cloud owner', 'email': 'cloud-' + secrets.token_hex(5) + '@example.com', 'password': PASSWORD})
            workspace = helpers.api(base, 'POST', '/workspaces', owner, {'name': 'Cloud studio'})
            channel = helpers.api(base, 'POST', '/channels', owner, {'name': 'Selected files', 'workspace_id': workspace['id']})
            seed = '''import json,os
from sqlmodel import Session
from app.main import app
from app.database import engine
from app.models.remote_sync import RemoteSyncConnection
from app.models.cloud_files import CloudFileConnection
from app.services import cloud_files
from tests.test_cloud_file_providers import CONFIGS
with Session(engine) as session:
    for provider,config in CONFIGS.items():
        model=RemoteSyncConnection if provider in {'google','microsoft'} else CloudFileConnection
        row=model(id=provider,workspace_id=os.environ['PROOF_WORKSPACE'],channel_id=os.environ['PROOF_CHANNEL'],created_by=os.environ['PROOF_OWNER'],grant_user_id=os.environ['PROOF_OWNER'],name='Private '+provider.title(),provider=provider,kind='files',config_json=json.dumps(config),status='connected')
        cloud_files.save_credentials(row,{'access_token':'private-fixture-bearer'})
        session.add(row)
    session.commit()
'''
            seeded = subprocess.run([python, '-c', seed], cwd=ROOT / 'backend', env={**env, 'PROOF_WORKSPACE': workspace['id'], 'PROOF_CHANNEL': channel['id'], 'PROOF_OWNER': owner['user_id']}, capture_output=True, text=True, timeout=45)
            assert seeded.returncode == 0, 'Private fixture grant failed: ' + seeded.stderr[-1500:]
            html = private / 'cloud-proof.html'
            html.write_text('<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1" /></head><body><div id="root"></div><script type="module">import React from "/node_modules/.vite/deps/react.js";import{createRoot}from"/node_modules/.vite/deps/react-dom_client.js";import{api}from"/src/lib/api.ts";import{applyLocale}from"/src/i18n/index.tsx";import{CloudFilesPicker}from"/src/components/CloudFilesPicker.tsx";import"/src/index.css";api.applySession(' + json.dumps(owner) + ');applyLocale("en");createRoot(document.getElementById("root")).render(React.createElement("main",{style:{maxWidth:"740px",margin:"24px auto",padding:"20px"}},React.createElement(CloudFilesPicker,{workspaceId:' + json.dumps(workspace['id']) + ',channelId:' + json.dumps(channel['id']) + ',onImported:file=>{window.proofImported=file}})));</script></body></html>')
            # Import bare dependency names so Vite performs normal optimization.
            html.write_text(html.read_text().replace('"/node_modules/.vite/deps/react.js"','"react"').replace('"/node_modules/.vite/deps/react-dom_client.js"','"react-dom/client"'))
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), args=['--no-sandbox'])
                context = browser.new_context(viewport={'width': 1200, 'height': 1000}, reduced_motion='reduce')
                page = context.new_page(); page.set_default_timeout(30000); errors=[]; page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(app + '/cloud-proof', wait_until='domcontentloaded')
                panel = page.get_by_role('region', name='Cloud files', exact=True)
                expect(panel.get_by_role('heading', name='Cloud files', exact=True)).to_be_visible()
                for provider in ['Google', 'Microsoft', 'Dropbox', 'Box']:
                    stage = provider + ' actual browse and import'
                    row = panel.locator('.cloud-connection').filter(has_text='Private ' + provider)
                    row.get_by_role('button', name='Browse', exact=True).click()
                    selected = panel.get_by_role('button', name='notes.txt', exact=False)
                    expect(selected).to_have_count(1); selected.click()
                    expect(panel.get_by_role('button', name='Import file', exact=True)).to_be_disabled()
                    previous_import=page.evaluate('window.proofImported?.id || null')
                    panel.get_by_role('checkbox').check(); panel.get_by_role('button', name='Import file', exact=True).click()
                    page.wait_for_function('(previous)=>window.proofImported && window.proofImported.id!==previous',arg=previous_import)
                    expect(panel.get_by_role('status')).to_contain_text('now available')
                    identifier = page.evaluate('window.proofImported.id')
                    raw = helpers.api(base, 'GET', '/files/' + identifier, owner, raw=True)
                    assert raw == b'actual four-provider HTTPS bytes'
                    results[provider.lower() + '_browse_select_confirm_import_exact_bytes'] = True
                stage = 'stale displayed customer consent'
                panel.locator('.cloud-connection').filter(has_text='Private Dropbox').get_by_role('button',name='Browse',exact=True).click()
                panel.get_by_role('button',name='notes.txt',exact=False).click()
                panel.get_by_role('checkbox').check()
                before_requests=len(helpers.api(base,'GET','/__cloud-proof-evidence')['requests'])
                before_files=len(helpers.api(base,'GET','/files/channels/'+channel['id'],owner))
                helpers.api(base,'POST','/__cloud-proof-reconfigure',data={})
                panel.get_by_role('button',name='Import file',exact=True).click()
                expect(panel.get_by_role('alert')).to_contain_text('The cloud connection changed')
                assert len(helpers.api(base,'GET','/__cloud-proof-evidence')['requests'])==before_requests
                assert len(helpers.api(base,'GET','/files/channels/'+channel['id'],owner))==before_files
                panel.get_by_role('button',name='Reload cloud connections',exact=True).click()
                expect(panel.get_by_text('Import selected file',exact=True)).to_have_count(0)
                results['reconfigured_customer_consent_blocks_old_selection_before_provider_io'] = True
                stage = 'immutable version import' 
                panel.locator('.cloud-connection').filter(has_text='Private Dropbox').get_by_role('button', name='Browse', exact=True).click()
                panel.get_by_role('button', name='notes.txt', exact=False).click()
                expect(panel.get_by_text('Import selected file', exact=True)).to_be_visible()
                destination=panel.get_by_label('Destination', exact=True)
                destination.select_option(index=1); panel.get_by_role('checkbox').check(); panel.get_by_role('button', name='Import file', exact=True).click()
                expect(panel.get_by_role('status')).to_contain_text('now available')
                page.wait_for_function('window.proofImported?.version_number === 2')
                assert page.evaluate('window.proofImported.version_number') == 2
                results['existing_document_import_creates_version_two'] = True
                stage = 'deliberate provider link'
                panel.get_by_role('tab', name='Provider links', exact=True).click()
                panel.get_by_label('Provider', exact=True).select_option('box'); panel.get_by_label('File title', exact=True).fill('Selected external design')
                panel.get_by_label('Selected file sharing link', exact=True).fill('https://app.box.com/file/20')
                expect(panel.get_by_role('button', name='Share provider link', exact=True)).to_be_disabled()
                panel.get_by_role('checkbox').check(); panel.get_by_role('button', name='Share provider link', exact=True).click()
                link = panel.get_by_role('link', name='Selected external design', exact=False)
                expect(link).to_have_attribute('href', 'https://app.box.com/file/20'); expect(link).to_have_attribute('rel', 'noopener noreferrer')
                results['explicit_provider_link_and_external_permissions_notice'] = True
                stage = 'mobile and language'
                page.set_viewport_size({'width':320,'height':850}); expect(panel.get_by_role('button',name='Share provider link',exact=True)).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= 320')
                page.evaluate('async()=>{const m=await import("/src/i18n/index.tsx");m.applyLocale("sv")}')
                expect(page.get_by_role('region',name='Molnfiler',exact=True).get_by_role('heading', name='Molnfiler', exact=True)).to_be_visible()
                results['swedish_and_320px_no_horizontal_overflow'] = True
                REPORT.parent.mkdir(parents=True, exist_ok=True); page.screenshot(path=str(REPORT.with_suffix('.png')), full_page=True)
                assert not errors, str(errors)
                results['no_browser_javascript_errors'] = True
                context.close(); browser.close()
            evidence=helpers.api(base,'GET','/__cloud-proof-evidence')
            helpers.stop(processes); processes=[]
            assert set(r['host'] for r in evidence['requests']) >= {'www.googleapis.com','graph.microsoft.com','api.dropboxapi.com','content.dropboxapi.com','api.box.com','dl.fixture.boxcloud.com','files.fixture.1drv.com'}
            assert all(not row['authorized'] for row in evidence['requests'] if row['host'] in {'dl.fixture.boxcloud.com','files.fixture.1drv.com'})
            results['actual_verified_https_all_four_and_no_bearer_to_content_cdns'] = True
            REPORT.write_text(json.dumps({'ok':True,'checks':results,'provider_request_count':len(evidence['requests']),'public_provider_calls':0},indent=2))
            print(json.dumps({'ok':True,'checks':results,'report':str(REPORT)},indent=2))
        except Exception as error:
            REPORT.parent.mkdir(parents=True,exist_ok=True); REPORT.write_text(json.dumps({'ok':False,'stage':stage,'error':str(error),'checks':results},indent=2))
            for filename in ['api.log','vite.log']:
                if (private/filename).exists(): print(filename,(private/filename).read_text()[-3000:],file=sys.stderr)
            raise
        finally:
            helpers.stop(processes)
            for stream in streams: stream.close()

if __name__=='__main__': main()
