#!/usr/bin/env python3
"""Real full-app Slack preview/import and declarative community release proof."""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PYTHON = os.getenv('BACKEND_PYTHON') or str(ROOT/'backend/.venv/bin/python')
spec = importlib.util.spec_from_file_location('library_helpers', ROOT/'frontend/tests/connection-setup-browser.py')
f = importlib.util.module_from_spec(spec); spec.loader.exec_module(f)
REPORT = Path(os.getenv('IMPORT_LIBRARY_REPORT', str(ROOT.parent/'.freecollab/proof-results/import-library-browser.json')))


def main():
    processes, streams, checks, stage = [], [], {}, 'startup'
    with tempfile.TemporaryDirectory(prefix='freecollab-import-library-') as directory:
        private = Path(directory)
        try:
            api_port, web_port = f.port(), f.port()
            base, origin = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{web_port}'
            database = private/'proof.db'
            environment = os.environ.copy()
            environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///'+str(database), STORAGE_PATH=str(private/'files'),
                STORAGE_BACKEND='local', REDIS_URL='', JWT_SECRET=secrets.token_urlsafe(48),
                ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),
                MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof', ACCOUNT_SECURITY_KEY_FILE=str(private/'account.key'), MESSAGE_ENCRYPTION_KEY_FILE=str(private/'message.keys'),
                MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled', PUBLIC_APP_URL=origin, CORS_ORIGINS=json.dumps([origin]),
                OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', OTRUST_ENABLED='false', SYNC_ENABLED='false', COPILOT_ENABLED='false',
                CLOUD_FILES_ENABLED='false', EXTERNAL_MEETINGS_ENABLED='false', PUSH_ENABLED='false', PYTHONPATH=str(ROOT/'backend'))
            environment['NO_PROXY'] = ','.join(filter(None,[environment.get('NO_PROXY',''),'127.0.0.1','localhost','::1']))
            launcher = private/'api.py'
            launcher.write_text('from app.main import socket_app\nfrom app.services import webhooks\nimport uvicorn\nnormal=webhooks.public_addresses\nwebhooks.public_addresses=lambda host: ["8.8.8.8"] if host=="apps.fixture.example" else normal(host)\nuvicorn.run(socket_app,host="127.0.0.1",port='+str(api_port)+',access_log=False,proxy_headers=False)\n')
            web, vite = f.frontend_snapshot(private,base,web_port)
            for command,name,cwd in [([PYTHON,str(launcher)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',web)]:
                stream=(private/name).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            f.ready(base+'/ready',processes); f.ready(origin,processes)
            email='library-owner-'+secrets.token_hex(4)+'@example.com'
            owner=f.api(base,'POST','/auth/register',data={'name':'Library Owner','email':email,'password':f.PASSWORD})
            f.SENSITIVE.append(owner['access_token'])
            with sqlite3.connect(database) as connection: connection.execute("UPDATE user SET role='admin' WHERE id=?",(owner['user_id'],)); connection.commit()
            workspace=f.api(base,'POST','/workspaces',owner,{'name':'A thoughtful move'})
            room=f.api(base,'POST','/channels',owner,{'name':'The original room','workspace_id':workspace['id']})
            output=io.BytesIO()
            with zipfile.ZipFile(output,'w') as writer:
                writer.writestr('users.json',json.dumps([{'id':'U1','name':'Historical Alice'}]))
                writer.writestr('channels.json',json.dumps([{'id':'C1','name':'old-studio','members':['U1']}]))
                writer.writestr('old-studio/2024-01-01.json',json.dumps([
                    {'type':'message','user':'U1','ts':'1704067200.000001','text':'A real imported root'},
                    {'type':'message','user':'U1','ts':'1704067201.000001','thread_ts':'1704067200.000001','text':'A real imported reply','files':[{'id':'F1','name':'handover.txt','mimetype':'text/plain'}]}]))
                writer.writestr('files/F1',b'Actual portable archive attachment')
            zip_path=private/'slack.zip'; zip_path.write_bytes(output.getvalue())
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
                context=browser.new_context(viewport={'width':1440,'height':1050},reduced_motion='reduce')
                page=context.new_page(); page.set_default_timeout(35000); errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)[:500]))
                try:
                    dialog,_=f.login(page,origin,owner,email,workspace['id'])
                except Exception:
                    # Capture startup diagnostics before Playwright's context
                    # manager closes the browser on the outer exception path.
                    REPORT.parent.mkdir(parents=True,exist_ok=True)
                    REPORT.with_suffix('.startup.json').write_text(json.dumps({'errors':errors,'title':page.title(),
                        'body':page.locator('body').inner_text()[:3000]},indent=2))
                    page.screenshot(path=str(REPORT.with_suffix('.startup.png')),full_page=True)
                    raise
                stage='actual Slack preview'
                dialog.get_by_role('button',name='Import from Slack',exact=True).click()
                panel=dialog.get_by_role('region',name='Import from Slack',exact=True)
                panel.locator('input[type=file]').set_input_files(zip_path)
                expect(panel.get_by_role('checkbox',name='old-studio',exact=False)).to_be_visible()
                assert len(f.api(base,'GET','/channels?workspace_id='+workspace['id'],owner))==1
                checks['actual_multipart_preview_does_not_create_channels_or_messages']=True
                stage='reviewed archive import and threads/files'
                panel.get_by_role('checkbox',name='old-studio',exact=False).check()
                panel.get_by_text('Map historical people to current members',exact=True).click()
                panel.get_by_label('Historical Alice',exact=True).select_option(owner['user_id'])
                panel.get_by_role('checkbox',name='I reviewed the channel selection',exact=False).check()
                panel.get_by_role('button',name='Import selected history',exact=True).click()
                expect(panel.get_by_role('heading',name='Your archive is ready.',exact=True)).to_be_visible()
                channels=f.api(base,'GET','/channels?workspace_id='+workspace['id'],owner)
                imported=next(item for item in channels if item['name']=='slack-old-studio')
                messages=f.api(base,'GET','/messages/channels/'+imported['id'],owner)
                assert len(messages)==2 and any(message['parent_id'] for message in messages)
                assert all('Imported from Slack · Historical Alice' in message['content'] for message in messages)
                files=f.api(base,'GET','/files/channels/'+imported['id'],owner)
                assert len(files)==1 and files[0]['filename']=='handover.txt'
                checks['real_reviewed_import_preserves_attribution_threads_and_bundled_files']=True
                stage='community package publish and moderation'
                # Hold every StrictMode initial catalog response after the server
                # has produced its old snapshot. Later action reloads must win.
                initial_catalog={'hold':True}
                held_catalog=[]
                def hold_catalog(route):
                    if initial_catalog['hold']:
                        response=route.fetch(); held_catalog.append((route,response))
                    else: route.continue_()
                catalog_pattern=re.compile(r'/api/apps/library(?:\?.*)?$')
                page.route(catalog_pattern,hold_catalog)
                dialog.get_by_role('button',name='Integration library',exact=True).click()
                library=dialog.get_by_role('region',name='Integration library',exact=True)
                args={'text':'Reviewed public fixture'}
                fixture={'schema_version':1,'id':'fixture-job','type':'app.command','installation_id':'fixture-installation','generation':1,
                    'workspace_id':'fixture-workspace','channel_id':'fixture-channel','actor_id':'fixture-actor','bot_id':'fixture-bot','command':'send','arguments':args}
                package={'package_schema':1,'manifest':{'schema_version':1,'app_id':'community.browser','name':'Thoughtful connector','version':'1.0.0',
                    'description':'A real declarative fixture package.','scopes':['commands:invoke','cards:write'],
                    'commands':[{'name':'send','description':'Send reviewed text','protocol':'signed-webhook','arguments':[{'name':'text','label':'Text','required':True,'max_length':2000}]}]},
                    'publisher':'Browser proof','license':'MIT','source_url':'https://github.com/freecollab-eu/freecollab','endpoint_example':'https://apps.fixture.example/hook',
                    'setup':'Run a customer-owned signed receiver.','tests':[{'command':'send','arguments':args,'expected_body':fixture}]}
                library.get_by_text('Publish a community integration',exact=True).click()
                library.get_by_label('Integration package JSON',exact=True).fill(json.dumps(package))
                library.get_by_role('checkbox',name='I can publish this source',exact=False).check()
                assert held_catalog, 'The initial catalog snapshot was not held'
                initial_catalog['hold']=False
                library.get_by_role('button',name='Submit version for review',exact=True).click()
                expect(library.get_by_text('Version submitted for administrator review.',exact=True)).to_be_visible()
                assert not any(item['app_id']=='community.browser' for item in f.api(base,'GET','/apps/library')['releases'])
                library.get_by_text('Administrator review queue',exact=True).click()
                library.get_by_role('button',name='Approve public listing',exact=True).click()
                expect(library.get_by_role('button',name='Community reviewed · v1.0.0 Thoughtful connector',exact=False)).to_be_visible()
                checks['actual_package_contract_validation_pending_review_then_public_listing']=True
                stage='late initial reads cannot overwrite reviewed release state'
                for route,response in held_catalog: route.fulfill(response=response)
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                expect(library.get_by_role('button',name='Community reviewed · v1.0.0 Thoughtful connector',exact=False)).to_be_visible()
                expect(library.get_by_role('heading',name='Your submitted versions',exact=True)).to_be_visible()
                page.unroute(catalog_pattern,hold_catalog)
                checks['held_initial_catalog_and_submission_snapshots_cannot_overwrite_newer_approval_reload']=True
                stage='exact release install and one-time secret'
                library.get_by_role('button',name='Community reviewed · v1.0.0 Thoughtful connector',exact=False).click()
                library.get_by_label('Customer-owned HTTPS endpoint',exact=True).fill('https://apps.fixture.example/hook')
                library.get_by_role('checkbox',name='The original room',exact=True).check()
                library.get_by_role('checkbox',name='I reviewed this exact version',exact=False).check()
                library.get_by_role('button',name='Install this exact version',exact=True).click()
                expect(library.get_by_role('heading',name='Store your signing secret',exact=True)).to_be_visible()
                assert len(library.get_by_label('Signing secret',exact=True).input_value())>30
                dialog.get_by_role('button',name='Close integrations',exact=True).click()
                expect(dialog).to_be_visible()
                library.get_by_role('button',name='I stored the secret securely',exact=True).click()
                checks['actual_exact_digest_install_and_one_time_secret_leave_guard']=True
                stage='initial review queue failure is a visible handled error'
                dialog.get_by_role('button',name='Import from Slack',exact=True).click()
                def queue_failure(route): route.fulfill(status=503,content_type='application/json',body=json.dumps({'detail':'Review queue unavailable fixture'}))
                page.route('**/api/admin/integration-library',queue_failure)
                dialog.get_by_role('button',name='Integration library',exact=True).click()
                library=dialog.get_by_role('region',name='Integration library',exact=True)
                expect(library.get_by_role('alert')).to_contain_text('Review queue unavailable fixture')
                page.unroute('**/api/admin/integration-library',queue_failure)
                checks['initial_admin_queue_network_failure_is_announced_without_unhandled_rejection']=True
                stage='actual light and dark appearance variables reach recipe surfaces'
                def palette(region):
                    return region.locator('.fc-app-recipe').first.evaluate('''async node=>{
                      function inspect(){
                        const border=node.matches(':hover,[aria-pressed="true"]')?'--fc-accent':'--fc-border';
                        const sample=document.createElement('span');sample.style.cssText='background:var(--fc-bg);border:1px solid var('+border+')';node.appendChild(sample);
                        const expected=getComputedStyle(sample),actual=getComputedStyle(node);
                        const result={background:actual.backgroundColor===expected.backgroundColor,border:actual.borderTopColor===expected.borderTopColor&&actual.borderTopStyle==='solid'&&actual.borderTopWidth==='1px',font:actual.fontFamily,actualBackground:actual.backgroundColor,expectedBackground:expected.backgroundColor,actualBorder:actual.borderTopColor,expectedBorder:expected.borderTopColor,alias:actual.getPropertyValue('--bg-secondary'),transition:actual.transitionProperty+' '+actual.transitionDuration,animation:actual.animationName};sample.remove();return result;
                      }
                      const deadline=performance.now()+2000;let result;
                      do{result=inspect();if(result.background&&result.border)return result;await new Promise(resolve=>requestAnimationFrame(resolve));}while(performance.now()<deadline);
                      return result;
                    }''')
                light=palette(library); assert light['background'] and light['border'],light
                page.evaluate('async()=>{const {applyAppearance,DEFAULT_APPEARANCE}=await import("/src/lib/appearance.ts");applyAppearance({...DEFAULT_APPEARANCE,mode:"dark",accent_color:"#d28bff",font_family:"manrope"})}')
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                dark=palette(library); assert dark['background'] and dark['border'] and 'Manrope' in dark['font'],dark
                checks['actual_customer_light_dark_color_and_font_preferences_style_library_cards']=True
                stage='existing Apps recipe surfaces and selected accent follow customer theme'
                dialog.get_by_role('button',name='Apps',exact=True).click()
                apps=dialog.get_by_role('region',name='Workspace apps',exact=True)
                # The developer-manifest card exists before recipes finish
                # loading. Select a named loaded recipe rather than that moving
                # first-card position.
                first_recipe=apps.get_by_role('button',name=re.compile(r'^Setup recipe Signed app'))
                expect(first_recipe).to_be_visible(); first_recipe.click()
                expect(first_recipe).to_have_attribute('aria-pressed','true')
                existing=palette(apps)
                assert existing['background'] and existing['border'] and 'Manrope' in existing['font'],existing
                page.evaluate('async()=>{const {applyAppearance,DEFAULT_APPEARANCE}=await import("/src/lib/appearance.ts");applyAppearance(DEFAULT_APPEARANCE)}')
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                existing=palette(apps); assert existing['background'] and existing['border'],existing
                checks['existing_apps_recipe_surface_and_selected_accent_follow_light_dark_customer_theme']=True
                dialog.get_by_role('button',name='Integration library',exact=True).click()
                library=dialog.get_by_role('region',name='Integration library',exact=True)
                stage='Swedish320px theme layout'
                page.evaluate('async()=>{(await import("/src/i18n/core.ts")).applyLocale("sv")}')
                page.set_viewport_size({'width':320,'height':800})
                dialog=page.get_by_role('dialog',name='Integrationer',exact=True)
                library=dialog.get_by_role('region',name='Integrationsbibliotek',exact=True)
                expect(dialog.get_by_role('button',name='Integrationsbibliotek',exact=True)).to_be_visible()
                expect(library.get_by_role('heading',name='Ett bibliotek du kan granska.',exact=True)).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                checks['swedish_and_320px_actual_theme_layout_without_horizontal_overflow']=True
                assert not errors
                checks['no_browser_runtime_errors']=True
                REPORT.parent.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(REPORT.with_suffix('.png')),full_page=True)
                browser.close()
            REPORT.write_text(json.dumps({'passed':True,'checks':checks,'limitations':['Disposable loopback services; no live Slack or community publication service contacted.']},indent=2)+'\n')
            print(json.dumps({'passed':True,'groups':len(checks),'report':str(REPORT)}))
        except Exception as failure:
            REPORT.parent.mkdir(parents=True,exist_ok=True)
            REPORT.write_text(json.dumps({'passed':False,'stage':stage,'checks':checks,'error_type':type(failure).__name__},indent=2)+'\n')
            if 'page' in locals() and not page.is_closed():
                try:
                    page.screenshot(path=str(REPORT.with_suffix('.failure.png')),full_page=True)
                    REPORT.with_suffix('.failure.json').write_text(json.dumps({'errors':errors,'title':page.title(),'body':page.locator('body').inner_text()[:3000]},indent=2))
                except Exception: pass
            print(json.dumps({'passed':False,'stage':stage,'error_type':type(failure).__name__})); raise
        finally:
            f.stop(processes)
            for stream in streams: stream.close()

if __name__=='__main__': main()
