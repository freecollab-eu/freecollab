#!/usr/bin/env python3
"""Real Chromium/SQLite/API proof of local content facets and verified citations.

Runs only private processes with temporary storage and keys, never pilot services.
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

import y_py
from playwright.sync_api import expect, sync_playwright

ROOT=Path(__file__).resolve().parents[2]
PYTHON=os.getenv('BACKEND_PYTHON') or (str(ROOT/'backend/.venv/bin/python') if (ROOT/'backend/.venv/bin/python').is_file() else sys.executable)
spec=importlib.util.spec_from_file_location('search_proof_helpers',Path(__file__).with_name('remote-sync-browser.py'))
helpers=importlib.util.module_from_spec(spec);spec.loader.exec_module(helpers)
PASSWORD='Private-search-browser!2026'
REPORT=Path(os.getenv('ENTERPRISE_SEARCH_PROOF_REPORT',str(ROOT.parent/'.freecollab/proof-results/enterprise-search-browser.json')))


def main():
    results={};processes=[];streams=[];stage='startup'
    with tempfile.TemporaryDirectory(prefix='freecollab-enterprise-search-') as directory:
        private=Path(directory)
        try:
            api_port,app_port=helpers.port(),helpers.port()
            base,app='http://127.0.0.1:'+str(api_port),'http://127.0.0.1:'+str(app_port)
            environment=os.environ.copy()
            environment.update(ENVIRONMENT='test',DATABASE_URL='sqlite:///'+str(private/'proof.db'),STORAGE_PATH=str(private/'files'),
                JWT_SECRET=secrets.token_urlsafe(48),ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',
                MESSAGE_ENCRYPTION_KEY_FILE=str(private/'message-keys.json'),ACCOUNT_SECURITY_KEY_FILE=str(private/'account.key'),
                MATRIX_ENABLED='false',MAIL_DELIVERY_MODE='disabled',PUBLIC_APP_URL=app,CORS_ORIGINS=json.dumps([app]),
                OIDC_ENABLED='false',OTRUST_ENABLED='false',SYNC_ENABLED='false',PYTHONPATH=str(ROOT/'backend'))
            environment['NO_PROXY']=','.join(filter(None,[environment.get('NO_PROXY',''),'localhost','127.0.0.1','::1']))
            server=private/'api.py';server.write_text('import uvicorn\nfrom app.main import socket_app\nuvicorn.run(socket_app,host="127.0.0.1",port='+str(api_port)+',proxy_headers=False,access_log=False)\n')
            frontend=private/'frontend';shutil.copytree(ROOT/'frontend',frontend,ignore=shutil.ignore_patterns('node_modules','dist','__pycache__','.git'))
            (frontend/'node_modules').symlink_to(ROOT/'frontend/node_modules',target_is_directory=True)
            vite=helpers.vite_launcher(private,base,app_port)
            vite.write_text(vite.read_text().replace('root:'+json.dumps(str(ROOT/'frontend')),'root:'+json.dumps(str(frontend)))
                .replace(json.dumps(str(private/'vite-cache')),json.dumps(str(frontend/'.vite-cache')))
                .replace('server:{host:','server:{fs:{allow:'+json.dumps([str(private),str(ROOT/'frontend')])+'},host:'))
            for command,name,cwd in [([PYTHON,str(server)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',frontend)]:
                stream=(private/name).open('w');streams.append(stream)
                processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            helpers.ready(base+'/ready',processes);helpers.ready(app+'/',processes)
            stage='seed real content'
            email='search-'+secrets.token_hex(6)+'@example.com'
            actor=helpers.api(base,'POST','/auth/register',data={'name':'Search designer','email':email,'password':PASSWORD})
            workspace=helpers.api(base,'POST','/workspaces',actor,{'name':'Search studio'})
            channel=helpers.api(base,'POST','/channels',actor,{'name':'Design research','workspace_id':workspace['id']})
            channel_id=channel['id'];doc=y_py.YDoc()
            with doc.begin_transaction() as transaction:
                doc.get_text('title').insert(transaction,0,'Living source')
                doc.get_text('body').insert(transaction,0,'Document-only Scandinavian cobalt')
            document=helpers.api(base,'POST','/channels/'+channel_id+'/shared-documents',actor,{'id':'browser-doc','update_id':'browser-update','title':'Living source','update_base64':base64.b64encode(y_py.encode_state_as_update(doc)).decode()})
            project=helpers.api(base,'POST','/channels/'+channel_id+'/projects',actor,{'id':'browser-project','fields':{'title':'Launch','description':'Project-only spruce','color':'sage'}})
            helpers.api(base,'POST','/channels/'+channel_id+'/projects/'+project['id']+'/tasks',actor,{'id':'browser-task','fields':{'title':'Review prototype','description':'Task-only forest','status':'todo','priority':'normal','assignee_user_id':None,'due_date':None,'checklist':[{'id':'a','title':'Checklist-only birch','done':False}],'file_ids':[],'decision_ids':[]}})
            helpers.api(base,'POST','/channels/'+channel_id+'/activity-feed',actor,{'id':'a'*32,'expected_revision':0,'fields':{'title':'Studio update','body':'Feed-only stone','poll':None}})
            helpers.upload(base,actor,channel_id,'Brief.txt',b'File-only linen')
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),args=['--no-sandbox'])
                context=browser.new_context(viewport={'width':1440,'height':1100},reduced_motion='reduce');page=context.new_page();page.set_default_timeout(40000)
                bodies=[];page.on('request',lambda request:bodies.append(request.post_data or '') if '/api/' in request.url else None)
                page.goto(app,wait_until='domcontentloaded');page.evaluate('([k,v])=>localStorage.setItem(k,v)',['freecollab.workspace:'+actor['user_id'],workspace['id']])
                page.get_by_role('navigation').get_by_role('button',name='Open App',exact=True).click()
                page.get_by_label('Email',exact=True).fill(email);page.get_by_label('Password',exact=True).fill(PASSWORD);page.get_by_role('button',name='Sign In',exact=True).click()
                page.get_by_role('button',name='Search all history',exact=True).click()
                dialog=page.get_by_role('dialog',name='Search all history');expect(dialog.get_by_text('Loading full history',exact=False)).to_have_count(0,timeout=60000)
                query=dialog.get_by_role('textbox',name='Search messages',exact=True)
                stage='local document task feed file facets'
                for needle,kind in [('cobalt','document'),('birch','task'),('stone','feed'),('linen','file-content'),('spruce','project')]:
                    dialog.get_by_label('Search content type',exact=True).select_option(kind);query.fill(needle)
                    expect(dialog.locator('.fc-history-results .fc-history-result')).to_have_count(1,timeout=15000)
                results['actual_yjs_task_checklist_feed_file_body_and_type_facets']=True
                assert not any(any(value in body for value in ['cobalt','birch','stone','linen','spruce']) for body in bodies)
                results['local_queries_never_sent_to_native_api_or_provider']=True
                stage='source citation and document facet'
                dialog.get_by_label('Search content type',exact=True).select_option('document');query.fill('cobalt')
                dialog.get_by_label('Search document',exact=True).select_option(document['id'])
                dialog.locator('.fc-history-results .fc-history-result').click()
                preview=dialog.get_by_label('Verified search source',exact=True);expect(preview).to_contain_text('Document-only Scandinavian cobalt')
                expect(preview.get_by_text('Current access and source revision verified.',exact=True)).to_be_visible()
                results['actor_bound_citation_open_verified_again_and_document_filter']=True
                # Archive on the actual backend; periodic current-source refresh
                # clears both the result and already-open read-only source.
                helpers.api(base,'PATCH','/channels/'+channel_id+'/shared-documents/'+document['id'],actor,{'expected_version':1,'archived':True})
                expect(preview).to_have_count(0,timeout=25000)
                expect(dialog.locator('.fc-history-results .fc-history-result')).to_have_count(0)
                results['archived_source_removed_from_results_and_open_preview']=True
                assert page.evaluate('()=>!Object.values(localStorage).some(value=>value.includes("Scandinavian cobalt"))&&!Object.values(sessionStorage).some(value=>value.includes("Scandinavian cobalt"))')
                results['decrypted_source_not_written_to_browser_storage']=True
                page.screenshot(path=str(private/'search.png'));browser.close()
        except Exception:
            print('Search proof failed during '+stage,flush=True)
            for name in ['api.log','vite.log']:
                path=private/name
                if path.exists():print(path.read_text()[-4000:],flush=True)
            raise
        finally:
            helpers.stop(processes)
            for stream in streams:stream.close()
            REPORT.parent.mkdir(parents=True,exist_ok=True);REPORT.write_text(json.dumps({'stage':stage,'results':results},indent=2))
    print(json.dumps(results,indent=2),flush=True)


if __name__=='__main__':main()
