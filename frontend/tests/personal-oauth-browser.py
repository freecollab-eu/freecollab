#!/usr/bin/env python3
"""Actual App/API customer-app setup, personal consent, search and revoke proof.

All accounts, keys, database and browser profiles are temporary. A private API
launcher supplies bounded synthetic provider responses; the browser's provider
consent navigation is explicitly intercepted. Independent backend tests exercise
the real verified HTTPS/CONNECT transport. No customer/vendor credential is used.
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlencode, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PYTHON = os.getenv('BACKEND_PYTHON') or (str(ROOT/'backend/.venv/bin/python') if (ROOT/'backend/.venv/bin/python').is_file() else sys.executable)
spec = importlib.util.spec_from_file_location('oauth_browser_helpers', ROOT/'frontend/tests/connection-setup-browser.py')
f = importlib.util.module_from_spec(spec); spec.loader.exec_module(f)
REPORT = Path(os.getenv('PERSONAL_OAUTH_REPORT',str(ROOT.parent/'.freecollab/proof-results/personal-oauth-browser.json')))
CLIENT_SECRET = 'synthetic-browser-customer-client-secret'
ACCESS = 'synthetic-browser-personal-provider-access'
REFRESH = 'synthetic-browser-personal-provider-refresh'


def main():
    processes, streams, checks, blocked = [], [], f.ProofChecks(), []
    with tempfile.TemporaryDirectory(prefix='freecollab-personal-oauth-browser-') as directory:
        private = Path(directory)
        try:
            api_port,web_port = f.port(),f.port()
            base,origin = 'http://127.0.0.1:'+str(api_port),'http://127.0.0.1:'+str(web_port)
            environment = os.environ.copy()
            environment.update(ENVIRONMENT='test',DATABASE_URL='sqlite:///'+str(private/'proof.db'),STORAGE_PATH=str(private/'files'),
                JWT_SECRET=secrets.token_urlsafe(48),ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',
                ACCOUNT_SECURITY_KEY_FILE=str(private/'account.key'),MESSAGE_ENCRYPTION_KEY_FILE=str(private/'message.keys'),
                MATRIX_ENABLED='false',MAIL_DELIVERY_MODE='disabled',PUBLIC_APP_URL=origin,CORS_ORIGINS=json.dumps([origin]),
                OIDC_ENABLED='false',WEBHOOKS_ENABLED='false',OTRUST_ENABLED='false',SYNC_ENABLED='false',COPILOT_ENABLED='false',
                CLOUD_FILES_ENABLED='false',EXTERNAL_MEETINGS_ENABLED='false',PUSH_ENABLED='false',NATIVE_PUSH_ENABLED='false',PYTHONPATH=str(ROOT/'backend'))
            environment['NO_PROXY']=','.join(filter(None,[environment.get('NO_PROXY',''),'127.0.0.1','localhost','::1']))
            f.SENSITIVE.extend([CLIENT_SECRET,ACCESS,REFRESH]+[environment[key] for key in ('JWT_SECRET','ACCOUNT_SECURITY_KEY','MESSAGE_ENCRYPTION_KEYS')])
            launcher=private/'api.py'
            launcher.write_text('''import json, uvicorn
from datetime import datetime,timedelta
from app.main import socket_app
from app.services import personal_oauth
def token(provider,purpose,config,fields):
    assert provider=='google' and purpose=='search'
    assert config['client_id']=='browser-customer-app' and config['client_secret']=='''+repr(CLIENT_SECRET)+'''
    assert fields['grant_type']=='authorization_code' and len(fields['code_verifier'])>=43
    return {'access_token':'''+repr(ACCESS)+''','refresh_token':'''+repr(REFRESH)+''','expires_at':(datetime.utcnow()+timedelta(hours=1)).isoformat()}
personal_oauth.token_request=token
def provider_request(method,url,**kwargs):
    if method=='POST' and url=='https://oauth2.googleapis.com/revoke':
        return personal_oauth.transport.Response(200,{},b'{}')
    if method=='GET' and url.startswith('https://www.googleapis.com/drive/v3/files'):
        assert kwargs['headers']['Authorization']=='Bearer '''+ACCESS+''''
        row={'id':'fixture-note','name':'Provider design note','mimeType':'text/plain','webViewLink':'https://drive.google.com/file/d/fixture-note/view','parents':['selected-folder']}
        value={'files':[row]} if '/files?' in url else row
        return personal_oauth.transport.Response(200,{},json.dumps(value).encode())
    raise AssertionError('Unexpected synthetic provider destination')
personal_oauth.transport.request=provider_request
uvicorn.run(socket_app,host='127.0.0.1',port='''+str(api_port)+''',proxy_headers=False,access_log=False)
''')
            web,vite=f.frontend_snapshot(private,base,web_port)
            for command,name,cwd in [([PYTHON,str(launcher)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',web)]:
                stream=(private/name).open('w');streams.append(stream)
                processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            f.ready(base+'/ready',processes);f.ready(origin,processes)
            email='oauth-owner-'+secrets.token_hex(5)+'@example.com'
            owner=f.api(base,'POST','/auth/register',data={'name':'OAuth proof owner','email':email,'password':f.PASSWORD})
            f.SENSITIVE.append(owner['access_token'])
            workspace=f.api(base,'POST','/workspaces',owner,{'name':'OAuth customer studio'})
            f.api(base,'POST','/channels',owner,{'name':'OAuth channel','workspace_id':workspace['id']})
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
                context=browser.new_context(viewport={'width':1440,'height':1100},reduced_motion='reduce')
                def route(request):
                    url=urlsplit(request.request.url)
                    if url.hostname=='accounts.google.com':
                        query=parse_qs(url.query)
                        assert query['client_id']==['browser-customer-app'] and query['code_challenge_method']==['S256']
                        assert query['scope']==['https://www.googleapis.com/auth/drive.readonly']
                        assert 'client_secret' not in query
                        destination=origin+'/api/personal-oauth/callback?'+urlencode({'state':query['state'][0],'code':'synthetic-browser-code'})
                        return request.fulfill(content_type='text/html',body='<script>location.replace('+json.dumps(destination)+')</script>')
                    if url.scheme in {'data','blob'} or url.hostname in {'127.0.0.1','localhost','::1'}:return request.continue_()
                    blocked.append(url.hostname);request.abort()
                context.route('**/*',route)
                page=context.new_page();page.set_default_timeout(45000)
                page_errors=[];consent_starts=[]
                page.on('pageerror',lambda error:page_errors.append(f.redacted(error)))
                page.on('request',lambda request:consent_starts.append(urlsplit(request.url).path) if request.method=='POST' and '/personal-oauth/' in request.url and urlsplit(request.url).path.endswith('/start') else None)
                try:
                    dialog,_=f.login(page,origin,owner,email,workspace['id'])
                except Exception:
                    page.screenshot(path='/tmp/freecollab-personal-oauth-failure.png')
                    print(json.dumps({'initial_page_errors':page_errors,'public_page':page.locator('body').inner_text()[:2500]}),flush=True)
                    raise
                dialog.get_by_role('button',name='Provider accounts',exact=True).click()
                panel=dialog.get_by_role('region',name='Provider accounts',exact=True)
                expect(panel.get_by_text('A workspace administrator must configure the customer application for this provider and purpose first.',exact=True)).to_be_visible()
                expect(panel.get_by_role('button',name='Continue to Google Workspace',exact=True)).to_be_disabled()
                checks['actual_readiness_blocks_unconfigured_provider']=True
                panel.locator('.fc-oauth-registration summary').click()
                expect(panel.get_by_label('Exact redirect URI',exact=True)).to_have_value(origin+'/api/personal-oauth/callback')
                panel.get_by_label('Application client ID',exact=True).fill('browser-customer-app')
                panel.get_by_label('Application client secret',exact=True).fill(CLIENT_SECRET)
                panel.get_by_label('Your password',exact=True).fill(f.PASSWORD)
                panel.get_by_role('button',name='Save customer application',exact=True).click()
                expect(panel.get_by_text('Customer application saved. Existing consents for this purpose require a fresh connection.',exact=True)).to_be_visible()
                expect(panel.get_by_label('Application client secret',exact=True)).to_have_value('')
                expect(panel.get_by_label('Your password',exact=True)).to_have_value('')
                checks['real_registration_reauthentication_exact_redirect_and_secret_clear']=True
                panel.get_by_label('Connection name',exact=True).fill('My design search')
                panel.get_by_label('Folder ID',exact=True).fill('selected-folder')
                expect(panel.get_by_role('button',name='Continue to Google Workspace',exact=True)).to_be_disabled()
                panel.get_by_label('I grant personal read-only search within the selected folder. My query is sent only when I explicitly search this provider.',exact=True).check()
                panel.get_by_role('button',name='Continue to Google Workspace',exact=True).click()
                expect(page.locator('.fc-chat')).to_be_visible(timeout=60000)
                expect(page).to_have_url(origin+'/',timeout=60000)
                assert page.evaluate('([a,b,c])=>![...Object.values(localStorage),...Object.values(sessionStorage)].some(v=>v.includes(a)||v.includes(b)||v.includes(c))',[ACCESS,REFRESH,CLIENT_SECRET])
                checks['provider_navigation_pkce_one_use_callback_and_token_free_browser_storage']=True
                page.get_by_role('button',name='Integrations',exact=True).click()
                dialog=page.get_by_role('dialog',name='Integrations',exact=True)
                dialog.get_by_role('button',name='Provider accounts',exact=True).click()
                panel=dialog.get_by_role('region',name='Provider accounts',exact=True)
                expect(panel.locator('.fc-oauth-grants article').get_by_text('My design search',exact=True)).to_be_visible()
                expect(panel.locator('.fc-oauth-grants article').get_by_text('Connected',exact=True)).to_be_visible()
                dialog.get_by_role('button',name='Done',exact=True).click()
                page.get_by_role('button',name='Search all history',exact=True).click()
                search=page.get_by_role('dialog',name='Search all history',exact=True)
                search.locator('.fc-enterprise-search>summary').click()
                search.get_by_label('Provider query',exact=True).fill('design')
                search.get_by_role('button',name='Send query to provider',exact=True).click()
                expect(search.get_by_role('button',name='Provider design note',exact=False)).to_be_visible()
                checks['oauth_delegation_consumed_by_real_personal_search_ui']=True
                search.get_by_role('button',name='Close history search',exact=True).click()
                f.api(base,'PUT','/preferences/language',owner,{'locale':'sv'})
                page.reload(wait_until='domcontentloaded');expect(page.locator('.fc-chat')).to_be_visible()
                page.get_by_role('button',name='Integrationer',exact=True).click()
                dialog=page.get_by_role('dialog',name='Integrationer',exact=True)
                dialog.get_by_role('button',name='Leverantörskonton',exact=True).click()
                panel=dialog.get_by_role('region',name='Leverantörskonton',exact=True)
                expect(panel.get_by_role('heading',name='Ditt konto. Din organisations applikation.',exact=True)).to_be_visible()
                page.set_viewport_size({'width':320,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert panel.evaluate('el=>el.scrollWidth<=el.clientWidth+1')
                REPORT.parent.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(REPORT.parent/'personal-oauth-mobile-sv.png'))
                checks['saved_swedish_and_320px_customer_account_layout']=True
                dialog.get_by_role('button',name='Klart',exact=True).click()
                page.set_viewport_size({'width':1440,'height':1100})
                page.get_by_role('button',name='Sök i all historik',exact=True).click()
                search=page.get_by_role('dialog',name='Sök i all historik',exact=True)
                search.locator('.fc-enterprise-search>summary').click()
                search.get_by_label('Sökfråga till leverantören',exact=True).fill('design')
                search.get_by_role('button',name='Skicka frågan till leverantören',exact=True).click()
                expect(search.get_by_role('button',name='Provider design note',exact=False)).to_be_visible()
                search.get_by_text('Anslut med Google eller Microsoft OAuth',exact=True).click()
                panel=search.get_by_role('region',name='Leverantörskonton',exact=True)
                panel.get_by_role('button',name='Koppla från',exact=True).click()
                panel.get_by_role('button',name='Bekräfta frånkoppling',exact=True).click()
                expect(panel.locator('.fc-oauth-grants article').get_by_text('Återkallad',exact=True)).to_be_visible()
                expect(search.get_by_role('button',name='Provider design note',exact=False)).to_have_count(0)
                expect(search.get_by_label('Personlig leverantör',exact=True)).to_have_count(0)
                checks['revoking_in_nested_panel_removes_current_provider_and_visible_hits']=True
                with sqlite3.connect(private/'proof.db') as database:
                    tokens,source,status=database.execute('select encrypted_tokens,encrypted_source_claims,status from personaloauthgrant').fetchone()
                    assert tokens==source=='' and status=='revoked'
                checks['actual_disconnect_erases_provider_tokens_and_original_claims']=True
                previous_starts=len(consent_starts)
                page.evaluate("window.freecollabNative={kind:'desktop'}")
                panel.get_by_label('Leverantör',exact=True).select_option('microsoft')
                expect(panel.get_by_text('Anslut leverantörskonton i din vanliga webbläsare',exact=True)).to_be_visible()
                expect(panel.get_by_label('Webbläsaradress',exact=True)).to_have_value(origin+'/')
                expect(panel.get_by_role('button',name='Fortsätt till Microsoft 365',exact=True)).to_be_disabled()
                assert len(consent_starts)==previous_starts
                checks['native_context_shows_browser_setup_without_starting_cross_cookie_consent']=True
                page.evaluate('delete window.freecollabNative')
                assert not blocked and not page_errors
                checks['no_unexpected_external_browser_requests_or_runtime_errors']=True
                context.close();browser.close()
            REPORT.write_text(json.dumps({'ok':True,'checks':checks,'provider_acceptance':'synthetic-browser-consent; verified-https-tested-in-backend'},indent=2)+'\n')
            print(json.dumps({'ok':True,'checks':list(checks),'report':str(REPORT)}),flush=True)
        except Exception as failure:
            REPORT.parent.mkdir(parents=True,exist_ok=True)
            REPORT.write_text(json.dumps({'ok':False,'checks':checks,'failure_type':type(failure).__name__},indent=2)+'\n')
            if 'page' in locals():
                try:page.screenshot(path='/tmp/freecollab-personal-oauth-failure.png')
                except Exception:pass
            print(json.dumps({'failure':f.redacted(failure)[:7000],'completed':list(checks)}),flush=True)
            raise AssertionError('Private OAuth browser proof failed: '+type(failure).__name__) from None
        finally:
            f.stop(processes)
            for stream in streams:stream.close()


if __name__=='__main__':main()
