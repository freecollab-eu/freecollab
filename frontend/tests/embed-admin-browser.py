#!/usr/bin/env python3
"""Actual integration-admin UI and private production-API embedding proof.

Uses a copied Vite frontend, temporary SQLite/accounts/keys, real password
confirmation, and a private fixture-issued customer SSO session. No external
IdP, provider, public deployment, or shared product runtime is used.
"""
import base64
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('embed_admin_fixture', Path(__file__).with_name('customer-sso-browser.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
REPORT = Path(os.getenv('EMBED_ADMIN_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/embed-admin-browser.json')))
PARENT = 'https://product.customer.example'
VERIFIER = 'E' * 64
PYTHON = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)


def scope_fixture(private, environment, owner, workspace):
    """Issue only an isolated fixture session through the real signing service."""
    token_path = private / 'scope.json'
    script = private / 'scope.py'
    script.write_text('''import json,os
from app.main import app
from sqlmodel import Session
from app.database import engine
from app.models.workspace_oidc import WorkspaceOIDCProvider,WorkspaceOIDCSession
from app.services.auth import create_token,token_digest
with Session(engine) as session:
    session.add(WorkspaceOIDCProvider(workspace_id=WORKSPACE,name='Private fixture identity',issuer_url='https://identity.customer.example',client_id='private-fixture-only',created_by=OWNER,enabled=True,revision=1));session.commit()
    token=create_token(OWNER,session,auth_methods=['sso'],workspace_scope={'workspace_id':WORKSPACE,'provider_revision':1})
    session.add(WorkspaceOIDCSession(token_hash=token_digest(token),workspace_id=WORKSPACE,provider_revision=1));session.commit()
    fd=os.open(OUTPUT,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:json.dump({'access_token':token},stream)
'''.replace('WORKSPACE', repr(workspace['id'])).replace('OWNER', repr(owner['user_id'])).replace('OUTPUT', repr(str(token_path))))
    subprocess.run([PYTHON, str(script)], cwd=ROOT / 'backend', env=environment, check=True, stdout=subprocess.DEVNULL)
    return json.loads(token_path.read_text())


def embedding(page):
    page.get_by_role('button', name='Integrations', exact=True).click()
    dialog = page.get_by_role('dialog', name='Integrations', exact=True)
    expect(dialog.get_by_role('button', name='Embedding', exact=True)).to_be_visible()
    dialog.get_by_role('button', name='Embedding', exact=True).click()
    return dialog, dialog.get_by_role('region', name='Embedded applications', exact=True)


def main():
    checks, processes, streams, stage, source_hashes = {}, [], [], 'private startup', {}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='freecollab-embed-admin-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = p.port(), p.port()
            api_base, app_base = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
            database = private / 'admin.db'
            environment = os.environ.copy()
            environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(database), STORAGE_PATH=str(private / 'files'),
                JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), ACCOUNT_SECURITY_KEY_FILE=str(private / 'account.key'),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'embed-admin':base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='embed-admin', MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'message-keys.json'),
                PUBLIC_APP_URL=app_base, CORS_ORIGINS=json.dumps([app_base]), MATRIX_ENABLED='false', OIDC_ENABLED='false', MAIL_DELIVERY_MODE='disabled', OTRUST_ENABLED='false', WEBHOOKS_ENABLED='false', SYNC_ENABLED='false', CLOUD_FILES_ENABLED='false', EXTERNAL_MEETINGS_ENABLED='false', COPILOT_ENABLED='false', PYTHONPATH=str(ROOT / 'backend'))
            launcher = private / 'api.py'; launcher.write_text("from app.main import socket_app\nimport uvicorn\nuvicorn.run(socket_app,host='127.0.0.1',port=" + str(api_port) + ",proxy_headers=False,access_log=False)\n")
            vite = p.create_vite(private, api_base, app_port)
            for source in ['src/components/EmbedApplicationsSettings.tsx','src/components/IntegrationsPanel.tsx']:
                source_hashes['frontend/' + source] = hashlib.sha256((private / 'frontend' / source).read_bytes()).hexdigest()
            for command, filename, cwd in [([PYTHON,str(launcher)],'api.log',ROOT / 'backend'),(['node',str(vite)],'vite.log',ROOT / 'frontend')]:
                stream = (private / filename).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            p.ready(api_base + '/ready',processes); p.ready(app_base + '/',processes)
            owner = p.native_user(api_base,database,'Embed Owner'); member = p.native_user(api_base,database,'Embed Member')
            workspace = p.api(api_base,'POST','/workspaces',owner,{'name':'Customer Product Studio'})
            invite = p.api(api_base,'POST','/workspaces/' + workspace['id'] + '/invites',owner,{'role':'member'})
            p.api(api_base,'POST','/workspaces/invites/accept',member,{'token':invite['token']})
            channel = p.api(api_base,'POST','/channels',owner,{'name':'Product conversation','type':'group','workspace_id':workspace['id'],'member_ids':[member['user_id']]})
            secure = p.api(api_base,'POST','/channels',owner,{'name':'Private product conversation','type':'group','workspace_id':workspace['id']})
            # Metadata fixture only: this proof exercises example selection, not crypto.
            with sqlite3.connect(database) as connection:
                connection.execute("INSERT INTO securechannel(channel_id,matrix_room_id,created_by,status,created_at) VALUES(?,?,?,?,?)",(secure['id'],'!private:embed-admin.example',owner['user_id'],'active',datetime.utcnow().isoformat(' ')))
            apps_path = '/workspaces/' + workspace['id'] + '/embed/applications'
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or '/usr/bin/chromium',headless=True,args=['--no-sandbox'])
                context = browser.new_context(viewport={'width':1440,'height':1080},permissions=['clipboard-read','clipboard-write'],reduced_motion='reduce')
                page = context.new_page(); page.set_default_timeout(30000); errors=[]; urls=[]
                page.on('pageerror',lambda error:errors.append(type(error).__name__)); page.on('request',lambda request:urls.append(request.url))
                try:
                    stage = 'native workspace owner opens real Integrations embedding tab'
                    p.login(page,app_base,owner,workspace); dialog, region = embedding(page)
                    expect(region.get_by_text('Enable this workspace’s organization sign-in before connecting an embedded application.',exact=True)).to_be_visible()
                    checks['native_owner_actual_integrations_embedding_entry_and_missing_customer_sso_notice'] = True
                    stage = 'same trusted origin is rejected without saving a registration'
                    region.get_by_label('Application name',exact=True).fill('Customer product')
                    region.get_by_label('Application origin',exact=True).fill(app_base)
                    region.get_by_label('Confirm with your FreeCollab password',exact=True).fill(p.PASSWORD)
                    region.get_by_role('button',name='Register application',exact=True).click()
                    expect(region.get_by_role('alert')).to_be_visible(); assert p.api(api_base,'GET',apps_path,owner)['applications'] == []
                    checks['trusted_origin_cannot_register_itself_for_embedding'] = True
                    stage = 'real origin registration and cleared password'
                    region.get_by_label('Application origin',exact=True).fill(PARENT)
                    region.get_by_role('button',name='Register application',exact=True).click()
                    expect(region.get_by_text('Application saved. Copy the integration example for your product.',exact=True)).to_be_visible()
                    expect(region.get_by_label('Confirm with your FreeCollab password',exact=True)).to_have_value('')
                    applications = p.api(api_base,'GET',apps_path,owner)['applications']; assert len(applications) == 1
                    application = applications[0]; assert application['parent_origin'] == PARENT and application['revision'] == 1 and application['enabled']
                    checks['actual_password_confirmed_registration_exact_origin_and_password_cleared'] = True
                    stage = 'copyable native and encrypted examples contain exact scope and no credentials'
                    region.get_by_label('Channel',exact=True).select_option(channel['id'])
                    example = region.locator('pre code').inner_text()
                    assert 'mountWorkspace' in example and all(value in example for value in [application['application_id'],workspace['id'],channel['id'],app_base])
                    assert all(value not in example for value in [owner['access_token'],member['access_token'],p.PASSWORD,'Bearer ','access_token'])
                    region.get_by_label('Channel',exact=True).select_option(secure['id'])
                    encrypted_example = region.locator('pre code').inner_text(); assert 'mountSecureChat' in encrypted_example and secure['id'] in encrypted_example and 'grantFactory: signIn.grantFactory' in encrypted_example
                    region.get_by_role('button',name='Copy integration example',exact=True).click()
                    expect(region.get_by_text('Integration example copied.',exact=True)).to_be_visible(); assert page.evaluate('navigator.clipboard.readText()') == encrypted_example
                    checks['copyable_native_secure_sdk_examples_use_real_application_workspace_channel_ids_without_jwt'] = True
                    stage = 'real source-session fixture launches child session and unused grant'
                    scoped = scope_fixture(private,environment,owner,workspace)
                    challenge = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip('=')
                    launch_body = {'application_id':application['application_id'],'channel_id':channel['id'],'parent_origin':PARENT,'code_challenge':challenge}
                    launched = p.api(api_base,'POST','/workspaces/' + workspace['id'] + '/embed/launch',scoped,launch_body)
                    child = p.api(api_base,'POST','/embed/' + application['application_id'] + '/exchange',data={'code':launched['code'],'code_verifier':VERIFIER,'parent_origin':PARENT,'channel_id':channel['id']})
                    assert p.api(api_base,'GET','/auth/me',child)['id'] == owner['user_id']
                    unused = p.api(api_base,'POST','/workspaces/' + workspace['id'] + '/embed/launch',scoped,launch_body)
                    stage = 'saving disabled application increments revision and invalidates existing child and grant'
                    region.get_by_label('Application name',exact=True).fill('Customer product revised')
                    region.get_by_label('Allow embedding from this origin',exact=True).uncheck()
                    region.get_by_label('Confirm with your FreeCollab password',exact=True).fill(p.PASSWORD)
                    region.get_by_role('button',name='Save application',exact=True).click()
                    expect(region.get_by_text('Application disabled. Its embedding grants and sessions are no longer valid.',exact=True)).to_be_visible()
                    expect(region.get_by_label('Confirm with your FreeCollab password',exact=True)).to_have_value('')
                    revised = p.api(api_base,'GET',apps_path,owner)['applications'][0]; assert not revised['enabled'] and revised['revision'] == 2 and revised['name'] == 'Customer product revised'
                    p.rejects(api_base,'GET','/auth/me',child,statuses=(401,403))
                    p.rejects(api_base,'POST','/embed/' + application['application_id'] + '/exchange',data={'code':unused['code'],'code_verifier':VERIFIER,'parent_origin':PARENT,'channel_id':channel['id']},statuses=(400,401,403,404,409))
                    checks['actual_admin_disable_revision_change_invalidates_preexisting_child_session_and_unused_pkce_grant'] = True
                    stage = 'wrong confirmation password stays rejected and is cleared'
                    region.get_by_label('Confirm with your FreeCollab password',exact=True).fill('Incorrect-private-fixture-password')
                    region.get_by_role('button',name='Save application',exact=True).click(); expect(region.get_by_role('alert')).to_be_visible()
                    expect(region.get_by_label('Confirm with your FreeCollab password',exact=True)).to_have_value('')
                    assert p.api(api_base,'GET',apps_path,owner)['applications'][0]['revision'] == 2
                    checks['failed_password_confirmation_does_not_edit_and_clears_password'] = True
                    stage = 'real embedding admin form at 320px'
                    page.set_viewport_size({'width':320,'height':900}); expect(region.get_by_label('Application origin',exact=True)).to_be_visible()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    assert dialog.evaluate('(node)=>node.scrollWidth<=node.clientWidth')
                    assert region.evaluate('(node)=>node.scrollWidth<=node.clientWidth')
                    page.screenshot(path=str(REPORT.with_name('embed-admin-mobile.png')),full_page=True)
                    checks['actual_embedding_form_and_copyable_example_fit_320px_without_horizontal_overflow'] = True
                    stage = 'native member cannot see or call embedding administration'
                    member_context = browser.new_context(viewport={'width':1280,'height':960},reduced_motion='reduce'); member_page = member_context.new_page(); member_page.on('pageerror',lambda error:errors.append(type(error).__name__))
                    p.login(member_page,app_base,member,workspace); member_page.get_by_role('button',name='Integrations',exact=True).click(); member_dialog = member_page.get_by_role('dialog',name='Integrations',exact=True); expect(member_dialog.get_by_text('A few useful connections.',exact=True)).to_be_visible(); expect(member_dialog.get_by_role('button',name='Embedding',exact=True)).to_have_count(0)
                    p.rejects(api_base,'GET',apps_path,member,statuses=(403,)); member_context.close()
                    checks['ordinary_workspace_member_has_no_embedding_tab_and_api_denies_admin_read'] = True
                    stage = 'customer scoped owner cannot administer embedding through global native settings'
                    scoped_context = browser.new_context(viewport={'width':1280,'height':960},reduced_motion='reduce')
                    scoped_page = scoped_context.new_page(); scoped_page.on('pageerror',lambda error:errors.append(type(error).__name__))
                    scoped_page.add_init_script('localStorage.setItem("freecollab_token",' + json.dumps(scoped['access_token']) + ');localStorage.setItem("freecollab_user_id",' + json.dumps(owner['user_id']) + ');localStorage.setItem(' + json.dumps('freecollab.workspace:' + owner['user_id']) + ',' + json.dumps(workspace['id']) + ');')
                    scoped_page.goto(app_base + '/#app',wait_until='domcontentloaded'); expect(scoped_page.locator('.fc-chat')).to_be_visible(timeout=90000); scoped_page.get_by_role('button',name='Integrations',exact=True).click(); scoped_dialog = scoped_page.get_by_role('dialog',name='Integrations',exact=True); expect(scoped_dialog.get_by_text('A few useful connections.',exact=True)).to_be_visible(); expect(scoped_dialog.get_by_role('button',name='Embedding',exact=True)).to_have_count(0)
                    p.rejects(api_base,'GET',apps_path,scoped,statuses=(403,)); scoped_context.close()
                    checks['customer_scoped_owner_has_no_embedding_tab_and_api_denies_native_admin_surface'] = True
                    assert not errors,'Unexpected browser page errors'
                    assert all(value not in url for url in urls for value in [owner['access_token'],member['access_token'],p.PASSWORD])
                    checks['zero_browser_page_errors_or_credentials_in_request_urls'] = True
                finally: context.close(); browser.close()
        except Exception:
            print('FAILED embed administration browser stage: ' + stage)
            raise
        finally:
            p.stop(processes)
            for stream in streams: stream.close()
    REPORT.write_text(json.dumps({'scope':'Actual copied production React frontend via isolated Vite and private production API with native password confirmation; fixture-issued scoped SSO token only, no external identity/provider/deployment. Synthetic secure channel is metadata-only example selection; this harness does not prove cryptography.','checks':checks,'source_sha256':source_hashes,'cleanup':'Own API/Vite/processes/private SQLite/accounts/keys/scoped-session credentials/browser contexts removed'},indent=2)+'\n')
    print('Embed administration browser proof passed: ' + str(len(checks)) + ' checks.')

if __name__ == '__main__': main()
