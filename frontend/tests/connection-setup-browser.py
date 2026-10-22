#!/usr/bin/env python3
"""Actual private Chromium connection-center proof; no external provider calls.

Uses the real App, native accounts, live API and private SQLite database. Only
its temporary launcher delays real responses / toggles operator flags; no draft
or readiness response is fabricated. All secrets and processes are disposable.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(os.getenv('CONNECTION_SETUP_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/connection-setup-browser.json')))
PASSWORD = 'Private-connection-center!2026'
SENSITIVE = [PASSWORD]
SCREENSHOTS = (
    'connection-setup-desktop.png', 'connection-setup-guide-desktop.png',
    'connection-setup-swedish-desktop.png', 'connection-setup-swedish-390.png',
    'connection-setup-swedish-320.png', 'connection-setup-swedish-guide-320.png',
)


class ProofChecks(dict):
    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if value is True: print('PASS '+key,flush=True)


def source_hash(folder, pattern='*'):
    digest=hashlib.sha256()
    for path in sorted(folder.rglob(pattern)):
        if path.is_file() and '__pycache__'not in path.parts:
            digest.update(str(path.relative_to(folder)).encode()+b'\0'+path.read_bytes()+b'\0')
    return digest.hexdigest()


def port():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0)); return server.getsockname()[1]


def api(base, method, path, user=None, data=None, raw=False):
    headers = {'Content-Type': 'application/json'}
    if user: headers['Authorization'] = 'Bearer ' + user['access_token']
    with urlopen(Request(base + path, method=method, headers=headers,
                         data=json.dumps(data).encode() if data is not None else None), timeout=45) as response:
        payload = response.read()
        return payload if raw else json.loads(payload) if payload else None


def reject(base, method, path, user=None, data=None, statuses=(401, 403, 404)):
    try: api(base, method, path, user, data)
    except HTTPError as error:
        assert error.code in statuses, 'Unexpected authority rejection'; return error.code
    raise AssertionError('An unauthorized connection-setup request succeeded')


def ready(url, processes):
    until = time.monotonic() + 60
    while time.monotonic() < until:
        assert all(process.poll() is None for process in processes), 'Private service exited before readiness'
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200: return
        except (HTTPError, URLError, TimeoutError): pass
        time.sleep(.2)
    raise AssertionError('Private service did not become ready')


def stop(processes):
    for process in reversed(processes):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)


def redacted(value):
    text = str(value)
    for secret in SENSITIVE:
        if secret: text = text.replace(secret, '[private]')
    return text


def controls(private, **values):
    target = private / 'controls.next'
    target.write_text(json.dumps(values)); target.replace(private / 'controls.json')


def wait_file(page, path):
    until = time.monotonic() + 20
    while time.monotonic() < until:
        if path.exists(): return
        page.wait_for_timeout(50)
    raise AssertionError('The real private API response was not held')


def private_api_launcher(private, api_port):
    source = private / 'api.py'
    source.write_text('''import asyncio,json,os,socket
from pathlib import Path
from fastapi import Request
from app.config import settings
from app.main import app,socket_app
from app.services import oidc_transport,remote_transport
import uvicorn
private=Path(os.environ['CONNECTION_PROOF_PRIVATE'])
network=[];requests=[]
def forbidden(*args,**kwargs):
    network.append('provider-transport');(private/'provider-attempts.json').write_text(json.dumps(network))
    raise RuntimeError('External provider I/O is forbidden in setup proof')
remote_transport.request=forbidden
oidc_transport.request=forbidden
# Only this disposable issuer's DNS answer is deterministic. Production URL
# parsing, global-address validation and configuration persistence stay intact;
# every actual provider transport is independently forbidden above.
original_resolve=oidc_transport._resolve
def fixture_resolve(host,port,timeout):
    if host=='setup-idp.customer.example':return ['8.8.8.8']
    return original_resolve(host,port,timeout)
oidc_transport._resolve=fixture_resolve
original_connect=socket.create_connection
def checked_connect(address,*args,**kwargs):
    if str(address[0]) not in {'127.0.0.1','localhost','::1'}:return forbidden()
    return original_connect(address,*args,**kwargs)
socket.create_connection=checked_connect
@app.middleware('http')
async def proof_controls(request:Request,call_next):
    try:config=json.loads((private/'controls.json').read_text())
    except (OSError,ValueError):config={}
    for key,value in config.get('flags',{}).items():
        if key not in {'sync_enabled','cloud_files_enabled','external_meetings_enabled','copilot_enabled','webhooks_enabled'} or type(value)is not bool:raise RuntimeError('Invalid private proof flag')
        setattr(settings,key,value)
    requests.append({'method':request.method,'path':request.url.path})
    response=await call_next(request)
    hold=config.get('hold')
    if hold and hold.get('path')==request.url.path and hold.get('method')==request.method:
        (private/'held-response').touch()
        for _ in range(600):
            if (private/'release-response').exists():break
            await asyncio.sleep(.05)
        else:raise RuntimeError('Private held response timed out')
    return response
@app.get('/__connection-proof-evidence')
def evidence():return {'provider_attempts':network,'requests':requests}
uvicorn.run(socket_app,host='127.0.0.1',port=''' + str(api_port) + ''',proxy_headers=False,access_log=False)
''')
    return source


def frontend_snapshot(private, base, app_port):
    web = private / 'frontend'
    shutil.copytree(ROOT / 'frontend', web, ignore=shutil.ignore_patterns('node_modules','dist','dist-widget','tests','.vite','.vite-cache','.git','__pycache__'))
    (web / 'node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
    source = private / 'vite.mjs'
    source.write_text('import {createServer} from ' + json.dumps(str(ROOT / 'frontend/node_modules/vite/dist/node/index.js')) + ';\n'
        'import react from ' + json.dumps(str(ROOT / 'frontend/node_modules/@vitejs/plugin-react/dist/index.js')) + ';\n'
        'const server=await createServer({configFile:false,root:' + json.dumps(str(web)) + ',plugins:[react()],cacheDir:' + json.dumps(str(private/'vite-cache')) +
        ",optimizeDeps:{exclude:['@matrix-org/matrix-sdk-crypto-wasm']},server:{host:'127.0.0.1',port:" + str(app_port) + ',strictPort:true,fs:{allow:' + json.dumps([str(private),str(ROOT/'frontend')]) + "},proxy:{'/api':{target:" + json.dumps(base) + ",changeOrigin:true,rewrite:p=>p.replace(/^\\/api/,'')},'/socket.io':{target:" + json.dumps(base) + ',ws:true}}}});await server.listen();\n')
    return web, source


def login(page, origin, owner, email, workspace):
    page.goto(origin, wait_until='domcontentloaded')
    page.evaluate('args=>localStorage.setItem(...args)', ['freecollab.workspace:'+owner['user_id'],workspace])
    page.get_by_role('navigation').get_by_role('button',name='Open App',exact=True).click()
    page.get_by_label('Email',exact=True).fill(email)
    page.get_by_label('Password',exact=True).fill(PASSWORD)
    page.get_by_role('button',name='Sign In',exact=True).click()
    page.get_by_role('button',name='Integrations',exact=True).click()
    dialog=page.get_by_role('dialog',name='Integrations',exact=True)
    dialog.get_by_role('button',name='Connection setup',exact=True).click()
    expect(dialog.locator('.fc-connection-setup')).to_be_visible()
    return dialog,dialog.locator('.fc-connection-setup')


def recipe(center, identifier, channel=None):
    # A real status refresh temporarily replaces the guide with loading state.
    # Choose the current rendered navigation only after that request settles.
    expect(center).to_have_attribute('aria-busy','false')
    picker=center.get_by_label('Connection recipe',exact=True)
    if picker.count(): picker.select_option(identifier)
    else:center.locator('[data-connection-recipe="'+identifier+'"]').click()
    if channel is not None:
        center.get_by_label('Connection channel',exact=True).select_option(channel)


def step(center, name):
    center.locator('[data-connection-step="'+name+'"]').click()


def no_secret_export(value, owner, workspace, channel):
    serialized=json.dumps(value)
    assert all(secret not in serialized for secret in SENSITIVE)
    assert owner['user_id'] not in serialized and workspace not in serialized and channel not in serialized
    forbidden={'connections','workspace_id','channel_id','user_id','access_token','authorization','encrypted_credentials','config_json','password','session','private_config'}
    def visit(item):
        if isinstance(item,dict):
            assert not forbidden.intersection(item), 'Recipe export contains private runtime/configuration fields'
            for child in item.values():visit(child)
        elif isinstance(item,list):
            for child in item:visit(child)
    visit(value)


def screenshot(page, path, width, height):
    page.set_viewport_size({'width':width,'height':height})
    center=page.locator('.fc-connection-setup')
    expect(center).to_have_attribute('aria-busy','false')
    for selector in ('.fc-connection-setup','.fc-account-body'):
        box=page.locator(selector)
        if box.count():assert box.evaluate('el=>el.scrollWidth<=el.clientWidth+1'),'Connection panel overflows internally'
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'Page overflows horizontally'
    page.evaluate('document.fonts.ready')
    page.screenshot(path=str(path),full_page=True,animations='disabled')


def downloaded_recipe(page, center, private, owner, workspace_id, channel_id, expected):
    step(center,'prepare')
    with page.expect_download() as received:
        center.get_by_role('button',name='Download setup JSON',exact=True).click()
    download=received.value;target=private/download.suggested_filename;download.save_as(target)
    value=json.loads(target.read_text())
    assert value['format']=='freecollab.connection-setup' and value['version']==1
    assert value['recipe']==expected['id'] and value['provider']==expected['provider'] and value['feature']==expected['target']
    assert value['callback_url']==expected['callback_url'] and value['scopes']==expected['scopes']
    assert value['prerequisites']==expected['prerequisites'] and value['steps']==expected['steps']
    assert value['documentation_url']==expected['docs_url']
    if 'fields' in expected:assert value['fields']==expected['fields']
    no_secret_export(value,owner,workspace_id,channel_id)
    return value


def native_member(base, owner, workspace_id, channel_id, role='admin'):
    user=api(base,'POST','/auth/register',data={'name':'Oskar Lind','email':'connection-reviewer-'+secrets.token_hex(5)+'@example.com','password':PASSWORD})
    SENSITIVE.append(user['access_token'])
    invitation=api(base,'POST','/workspaces/'+workspace_id+'/invites',owner,{'role':role})
    SENSITIVE.append(invitation['token'])
    api(base,'POST','/workspaces/invites/accept',user,{'token':invitation['token']})
    invitation=api(base,'POST','/channels/'+channel_id+'/invites',owner,{})
    SENSITIVE.append(invitation['token'])
    api(base,'POST','/channels/invites/accept',user,{'token':invitation['token']})
    return user


def sso_journey(page,center,private,base,owner,workspace_id,results):
    recipe(center,'sso.oidc')
    expect(center.get_by_label('Connection recipe',exact=True)).to_have_value('sso.oidc')
    step(center,'configure')
    preset=center.get_by_label('Identity provider preset',exact=True)
    preset.select_option('entra')
    center.get_by_label('Directory (tenant) ID',exact=True).fill('common')
    expect(center.get_by_role('button',name='Save SSO configuration',exact=True)).to_be_disabled()
    expect(center.get_by_label('Issuer URL',exact=True)).to_have_value('')
    tenant='22222222-2222-2222-2222-222222222222'
    center.get_by_label('Directory (tenant) ID',exact=True).fill(tenant)
    expect(center.get_by_label('Issuer URL',exact=True)).to_have_value('https://login.microsoftonline.com/'+tenant+'/v2.0')
    center.get_by_label('Client ID',exact=True).fill('fixture-entra-application')
    discarded='private-discarded-sso-'+secrets.token_hex(20);SENSITIVE.append(discarded)
    center.get_by_label('Client secret',exact=True).fill(discarded)
    preset.select_option('google')
    expect(center.get_by_label('Issuer URL',exact=True)).to_have_value('https://accounts.google.com')
    expect(center.get_by_label('Client ID',exact=True)).to_have_value('')
    expect(center.get_by_label('Client secret',exact=True)).to_have_value('')
    preset.select_option('keycloak')
    center.get_by_label('Keycloak base URL',exact=True).fill('http://localhost:8000')
    center.get_by_label('Realm',exact=True).fill('../invalid')
    expect(center.get_by_role('button',name='Save SSO configuration',exact=True)).to_be_disabled()
    center.get_by_label('Keycloak base URL',exact=True).fill('https://setup-idp.customer.example/auth')
    center.get_by_label('Realm',exact=True).fill('studio')
    expect(center.get_by_label('Issuer URL',exact=True)).to_have_value('https://setup-idp.customer.example/auth/realms/studio')
    center.get_by_label('Provider name',exact=True).fill('Studio customer sign-in')
    sso_client='fixture-keycloak-client-'+secrets.token_hex(4);sso_secret='private-keycloak-secret-'+secrets.token_hex(20);SENSITIVE.extend([sso_client,sso_secret])
    center.get_by_label('Client ID',exact=True).fill(sso_client)
    center.get_by_label('Client secret',exact=True).fill(sso_secret)
    center.get_by_role('button',name='Save SSO configuration',exact=True).click()
    confirmation=center.get_by_role('form',name='Confirm workspace SSO change',exact=True)
    confirmation.get_by_label('Confirm with your FreeCollab password',exact=True).fill(PASSWORD)
    confirmation.get_by_role('button',name='Confirm configuration',exact=True).click()
    # Real password verification and the production public-issuer DNS check can
    # legitimately take longer than Playwright's five-second assertion default.
    expect(confirmation).to_have_count(0,timeout=20000)
    stored=api(base,'GET','/workspaces/'+workspace_id+'/sso',owner)
    assert stored['configured']and stored['provider']['has_secret']and not stored['provider']['enabled']and not stored['provider']['tested']
    assert stored['provider']['issuer_url']=='https://setup-idp.customer.example/auth/realms/studio'
    expect(center.get_by_label('Client secret handling',exact=True)).to_have_value('retain')
    center.get_by_label('Identity provider preset',exact=True).select_option('google')
    expect(center.get_by_label('Client secret handling',exact=True)).to_have_value('replace')
    expect(center.get_by_label('Client secret',exact=True)).to_have_value('')
    expect(center.get_by_label('Client ID',exact=True)).to_have_value('')
    page.once('dialog',lambda prompt:prompt.accept())
    step(center,'status')
    expect(center.locator('.fc-connection-current [data-status="verification_required"]')).to_be_visible()
    row=api(base,'GET','/workspaces/'+workspace_id+'/connection-setup',owner)
    sso=next(value for value in row['recipes']if value['id']=='sso.oidc')
    assert sso['status']=='verification_required'and sso['can_configure']
    downloaded_recipe(page,center,private,owner,workspace_id,'unused-channel',sso)
    with sqlite3.connect(private/'proof.db')as db:
        values=db.execute('SELECT enabled,tested_at,encrypted_client_secret FROM workspaceoidcprovider WHERE workspace_id=?',(workspace_id,)).fetchone()
        assert values and not values[0]and values[1]is None and sso_secret not in values[2]and discarded not in values[2]
    results['sso_entra_keycloak_validation_google_secret_clear_and_real_disabled_draft']=True


def browser_journey(page, context, base, origin, private, owner, outsider, workspace, channels, results):
    ws=workspace['id'];first,second=channels[0]['id'],channels[1]['id'];endpoint='/workspaces/'+ws+'/connection-setup'
    catalog=api(base,'GET',endpoint,owner)
    assert catalog['workspace_id']==ws and catalog['channel_id'] is None and catalog['origin_status']=='local_development'
    assert {'files.google','files.box','meetings.microsoft_teams','sso.oidc','directory.scim','automation.webhook'}<={value['id']for value in catalog['recipes']}
    reject(base,'GET',endpoint,outsider);reject(base,'GET',endpoint+'?channel_id='+first,outsider)
    with urlopen(Request(base+endpoint,headers={'Authorization':'Bearer '+owner['access_token']}),timeout=20)as response:
        assert 'no-store'in response.headers.get('Cache-Control','')
        assert not response.headers.get('Set-Cookie')
    results['real_native_owner_catalog_private_no_store_and_outsider_denied']=True
    dialog,center=login(page,origin,owner,owner['email'],ws)
    token=page.evaluate('async()=>{const{api}=await import("/src/lib/api.ts");return api.getAccessToken()}');SENSITIVE.append(token)
    expect(center.locator('[data-connection-recipe]')).to_have_count(len(catalog['recipes']))
    screenshot(page,REPORT.parent/'connection-setup-desktop.png',1440,1100)

    # Exact static JSON and clipboard values, followed by a real Google draft.
    # Clear the active-channel default to exercise the unscoped validation guard.
    recipe(center,'files.google',channel='')
    expect(center.locator('[data-connection-step="configure"]')).to_be_disabled()
    center.get_by_label('Connection channel',exact=True).select_option(first)
    expect(center.locator('[data-connection-step="configure"]')).to_be_enabled()
    current=api(base,'GET',endpoint+'?channel_id='+first,owner)
    google=next(row for row in current['recipes']if row['id']=='files.google')
    downloaded_recipe(page,center,private,owner,ws,first,google)
    center.get_by_role('button',name='Copy callback',exact=True).click()
    assert page.evaluate('navigator.clipboard.readText()')==google['callback_url']
    scopes=center.locator('.fc-connection-scopes code').all_text_contents();assert scopes==google['scopes']
    results['exact_callback_clipboard_scopes_and_whitelisted_setup_download']=True
    step(center,'configure')
    expect(center.locator('#sync-provider')).to_have_value('google');expect(center.locator('#sync-kind')).to_have_value('files');expect(center.locator('#sync-channel')).to_have_value(first)
    google_client='fixture-google-client-'+secrets.token_hex(4);google_secret='private-google-secret-'+secrets.token_hex(20)
    SENSITIVE.extend([google_client,google_secret])
    center.get_by_label('Connection name',exact=True).fill('Studio Google files')
    center.get_by_label('OAuth client ID',exact=True).fill(google_client)
    center.get_by_label('OAuth client secret',exact=True).fill(google_secret)
    center.get_by_label('Remote folder ID',exact=True).fill('selected-studio-folder')
    page.once('dialog',lambda prompt:prompt.dismiss())
    center.get_by_label('Connection recipe',exact=True).select_option('files.box')
    expect(center.get_by_label('Connection recipe',exact=True)).to_have_value('files.google')
    expect(center.get_by_label('OAuth client secret',exact=True)).to_have_value(google_secret)
    page.once('dialog',lambda prompt:prompt.dismiss())
    center.get_by_label('Connection channel',exact=True).select_option(second)
    expect(center.get_by_label('Connection channel',exact=True)).to_have_value(first)
    page.once('dialog',lambda prompt:prompt.dismiss())
    step(center,'prepare')
    expect(center.get_by_label('OAuth client ID',exact=True)).to_have_value(google_client)
    dialog.get_by_role('button',name='Overview',exact=True).click()
    expect(center).to_be_visible();dialog.get_by_role('button',name='Close integrations',exact=True).click();expect(dialog).to_be_visible()
    results['typed_draft_recipe_channel_step_parent_tab_and_close_guards_preserve_secret']=True
    center.locator('.fc-enterprise-consent input[type="checkbox"]').check()
    center.get_by_role('button',name='Save paused connection',exact=True).click()
    confirmation=center.get_by_role('form',name='Save this cloud connection?',exact=True)
    if not confirmation.count():confirmation=center.locator('.fc-account-reauth')
    confirmation.get_by_label('FreeCollab password',exact=True).fill(PASSWORD)
    controls(private,hold={'method':'POST','path':'/workspaces/'+ws+'/sync'})
    confirmation.get_by_role('button',name='Confirm',exact=True).click()
    wait_file(page,private/'held-response')
    expect(dialog.get_by_role('button',name='Close integrations',exact=True)).to_be_disabled()
    page.keyboard.press('Escape');expect(dialog).to_be_visible()
    expect(center.get_by_label('Connection recipe',exact=True)).to_be_disabled()
    expect(center.get_by_label('Connection channel',exact=True)).to_be_disabled()
    assert all(button.is_disabled()for button in dialog.locator('.fc-account-tabs button').all())
    evidence=api(base,'GET','/__connection-proof-evidence')
    assert len([row for row in evidence['requests']if row=={'method':'POST','path':'/workspaces/'+ws+'/sync'}])==1
    controls(private);(private/'release-response').touch()
    expect(confirmation).to_have_count(0)
    sync=api(base,'GET','/workspaces/'+ws+'/sync',owner)['connections']
    assert len(sync)==1 and sync[0]['provider']=='google'and sync[0]['kind']=='files'and sync[0]['channel_id']==first and not sync[0]['enabled']and not sync[0]['tested']
    results['real_google_files_preset_paused_draft_and_held_real_response_busy_guards']=True
    step(center,'status');expect(center.locator('.fc-connection-current [data-status="authorization_required"]')).to_be_visible()
    expect(center.locator('.fc-connection-records')).to_contain_text('Studio Google files')
    current=api(base,'GET',endpoint+'?channel_id='+first,owner);google=next(row for row in current['recipes']if row['id']=='files.google')
    assert google['status']=='authorization_required'
    downloaded_recipe(page,center,private,owner,ws,first,google)

    recipe(center,'files.box',first);step(center,'configure')
    expect(center.get_by_label('Provider',exact=True)).to_have_value('box')
    box_client='fixture-box-client-'+secrets.token_hex(4);box_secret='private-box-secret-'+secrets.token_hex(20);SENSITIVE.extend([box_client,box_secret])
    center.get_by_label('Connection name',exact=True).fill('Studio Box files')
    center.get_by_label('Application client ID',exact=True).fill(box_client)
    center.get_by_label('Application client secret',exact=True).fill(box_secret)
    center.get_by_label('Selected root folder ID',exact=True).fill('0')
    center.get_by_label('Your FreeCollab password',exact=True).fill(PASSWORD)
    center.locator('form.cloud-form input[type="checkbox"]').check()
    center.get_by_role('button',name='Save application',exact=True).click()
    expect(center.get_by_role('status').filter(has_text='saved').first).to_be_visible()
    box=[row for row in api(base,'GET','/workspaces/'+ws+'/channels/'+first+'/cloud-files/connections',owner)['connections']if row['provider']=='box']
    assert len(box)==1 and box[0]['provider']=='box'and not box[0]['connected']and box[0]['status']=='oauth_required'
    step(center,'status');expect(center.locator('.fc-connection-current [data-status="authorization_required"]')).to_be_visible()
    results['real_box_preset_saved_unconnected_customer_application_draft']=True

    recipe(center,'meetings.microsoft_teams',first);step(center,'configure')
    meeting=center.get_by_role('form',name='Configure customer meeting provider',exact=True)
    expect(meeting.get_by_label('Provider',exact=True)).to_have_value('microsoft_teams')
    teams_client='fixture-teams-client-'+secrets.token_hex(4);teams_secret='private-teams-secret-'+secrets.token_hex(20);SENSITIVE.extend([teams_client,teams_secret])
    meeting.get_by_label('Connection name',exact=True).fill('Studio Teams meetings')
    meeting.get_by_label('Customer OAuth client ID',exact=True).fill(teams_client)
    meeting.get_by_label('Microsoft tenant ID',exact=True).fill('11111111-1111-1111-1111-111111111111')
    meeting.get_by_label('Selected calendar ID',exact=True).fill('selected-studio-calendar')
    meeting.get_by_label('Customer OAuth client secret',exact=True).fill(teams_secret)
    center.get_by_label('Your current FreeCollab password',exact=True).fill(PASSWORD)
    meeting.get_by_role('checkbox').check();meeting.get_by_role('button',name='Save customer provider configuration',exact=True).click()
    expect(center.get_by_role('status').filter(has_text='Customer provider configuration saved').first).to_be_visible()
    expect(center.get_by_label('Your current FreeCollab password',exact=True)).to_have_value('')
    expect(meeting.get_by_label('Customer OAuth client secret',exact=True)).to_have_value('')
    rows=api(base,'GET','/workspaces/'+ws+'/external-meetings/connections',owner)
    assert len(rows)==1 and rows[0]['provider']=='microsoft_teams'and not rows[0]['enabled']and not rows[0]['tested']and rows[0]['status']=='oauth_required'
    step(center,'status');expect(center.locator('.fc-connection-current [data-status="authorization_required"]')).to_be_visible()
    results['real_teams_preset_saved_unconnected_untested_provider_draft']=True

    # Native reauthentication and one-time SCIM token guards, no provider I/O.
    recipe(center,'directory.scim');step(center,'configure')
    center.get_by_role('button',name='Create provisioning token',exact=True).click()
    pending=center.get_by_role('form',name='Create provisioning token?',exact=True)
    # First attempt is immediate after the click, before waiting for paint or a
    # passive dirty effect. Then repeat after the confirmation is visibly open.
    page.once('dialog',lambda prompt:prompt.dismiss())
    center.get_by_label('Connection recipe',exact=True).select_option('files.box')
    expect(pending).to_be_visible()
    expect(center.get_by_label('Connection recipe',exact=True)).to_have_value('directory.scim')
    page.once('dialog',lambda prompt:prompt.dismiss())
    center.get_by_label('Connection recipe',exact=True).select_option('files.box')
    expect(pending).to_be_visible()
    pending.get_by_label('FreeCollab password',exact=True).fill(PASSWORD)
    pending.get_by_role('button',name='Confirm',exact=True).click()
    token_field=center.get_by_label('Provisioning bearer token',exact=True)
    expect(token_field).to_be_visible();scim_token=token_field.input_value();SENSITIVE.append(scim_token)
    assert scim_token.startswith('fc_scim_')
    center.get_by_label('Connection recipe',exact=True).select_option('files.box')
    expect(token_field).to_be_visible()
    dialog.get_by_role('button',name='Close integrations',exact=True).click();expect(dialog).to_be_visible()
    center.get_by_role('checkbox',name='I stored this token in my identity provider’s secure settings.',exact=True).check()
    center.get_by_role('button',name='Hide stored token',exact=True).click();expect(token_field).to_have_count(0)
    results['scim_pending_confirmation_and_actual_one_time_token_block_navigation_until_acknowledged']=True

    # A typed webhook draft blocks departure without contacting its receiver.
    recipe(center,'automation.webhook',first)
    step(center,'configure')
    center.get_by_role('button',name='Create webhook',exact=True).click()
    center.get_by_label('Connection name',exact=True).fill('Studio webhook draft')
    center.get_by_label('HTTPS receiver URL',exact=True).fill('https://hooks.customer.example/unused')
    page.once('dialog',lambda prompt:prompt.dismiss())
    center.get_by_label('Connection recipe',exact=True).select_option('files.box')
    expect(center.get_by_label('HTTPS receiver URL',exact=True)).to_have_value('https://hooks.customer.example/unused')
    center.get_by_role('button',name='Cancel webhook editing',exact=True).click()
    expect(center.get_by_role('form',name='Create webhook',exact=True)).to_have_count(0)
    expect(center.get_by_role('button',name='Discard changes and return to preparation',exact=True)).to_have_count(0)
    results['typed_webhook_draft_guard_without_receiver_contact']=True

    sso_journey(page,center,private,base,owner,ws,results)

    recipe(center,'ai.openai_compatible');step(center,'configure')
    center.get_by_role('combobox',name=re.compile(r'^AI provider preset')).select_option(index=0)
    ai_key='private-never-sent-ai-key-'+secrets.token_hex(20);SENSITIVE.append(ai_key)
    center.get_by_label('Provider API key',exact=True).fill(ai_key)
    center.get_by_label('Current password',exact=True).fill(PASSWORD)
    center.get_by_label('Chat completions endpoint',exact=True).fill('https://provider.customer.example/v1/chat/completions')
    expect(center.get_by_label('Provider API key',exact=True)).to_have_value('')
    expect(center.get_by_label('Current password',exact=True)).to_have_value('')
    page.once('dialog',lambda prompt:prompt.accept())
    step(center,'status')
    results['ai_setup_preset_destination_change_clears_typed_key_and_reauthentication_no_relay']=True

    # A second real administrator owns a separate draft; revocation is real API.
    reviewer=native_member(base,owner,ws,second)
    reject(base,'GET',endpoint+'?channel_id='+second,reviewer)
    api(base,'PATCH','/channels/'+second+'/members/'+reviewer['user_id'],owner,{'role':'admin'})
    revoked_secret='private-revoked-box-'+secrets.token_hex(20);SENSITIVE.append(revoked_secret)
    revoked=api(base,'POST','/workspaces/'+ws+'/channels/'+second+'/cloud-files/connections',reviewer,{'name':'Revoked reviewer draft','provider':'box','client_id':'fixture-reviewer-box','client_secret':revoked_secret,'folder_id':'0','consent':True,'password':PASSWORD})
    recipe(center,'files.box',second);step(center,'status')
    expect(center.locator('.fc-connection-records')).to_contain_text('Revoked reviewer draft')
    api(base,'DELETE','/channels/'+second+'/members/'+reviewer['user_id'],owner)
    center.get_by_role('button',name='Refresh connection status',exact=True).click()
    expect(center.locator('.fc-connection-current [data-status="attention"]')).to_be_visible()
    catalog=api(base,'GET',endpoint+'?channel_id='+second,owner)
    assert next(row for row in catalog['recipes']if row['id']=='files.box')['status']=='attention'
    results['real_nonadmin_channel_denied_and_revoked_provider_grant_status_refreshes_attention']=True

    # Disabled operator flags block Configure while static instructions remain.
    controls(private,flags={'sync_enabled':False,'cloud_files_enabled':False,'external_meetings_enabled':False,'copilot_enabled':False})
    center.get_by_role('button',name='Refresh connection status',exact=True).click()
    expect(center.locator('.fc-connection-current [data-status="unavailable"]')).to_be_visible()
    expect(center.locator('[data-connection-step="configure"]')).to_be_disabled()
    catalog=api(base,'GET',endpoint+'?channel_id='+second,owner)
    box=next(row for row in catalog['recipes']if row['id']=='files.box');assert not box['enabled']and box['status']=='unavailable'
    downloaded_recipe(page,center,private,owner,ws,second,box)
    results['disabled_installation_flag_blocks_configuration_but_static_recipe_still_downloads']=True
    controls(private,flags={'sync_enabled':True,'cloud_files_enabled':True,'external_meetings_enabled':True,'copilot_enabled':True})
    step(center,'status');center.get_by_role('button',name='Refresh connection status',exact=True).click()
    recipe(center,'files.google',first);step(center,'prepare')
    screenshot(page,REPORT.parent/'connection-setup-guide-desktop.png',1440,1100)
    dialog.get_by_role('button',name='Done',exact=True).click()
    page.get_by_role('button',name='Account settings',exact=True).click()
    page.get_by_label('Language',exact=True).select_option('sv')
    expect(page.locator('html')).to_have_attribute('lang','sv')
    expect(page.locator('.fc-language-status')).to_have_text('Språket har sparats.')
    page.locator('.fc-account-panel .fc-account-header button').click()
    page.get_by_role('button',name='Integrationer',exact=True).click()
    dialog=page.get_by_role('dialog',name='Integrationer',exact=True)
    dialog.get_by_role('button',name='Anslutningsguide',exact=True).click()
    center=dialog.locator('.fc-connection-setup')
    expect(center.get_by_role('heading',name='Anslutningar, steg för steg.',exact=True)).to_be_visible()
    screenshot(page,REPORT.parent/'connection-setup-swedish-desktop.png',1440,1100)
    for width in (390,320):screenshot(page,REPORT.parent/('connection-setup-swedish-'+str(width)+'.png'),width,900)
    center.locator('[data-connection-recipe="files.box"]').click()
    center.get_by_label('Anslutningens kanal',exact=True).select_option(first)
    screenshot(page,REPORT.parent/'connection-setup-swedish-guide-320.png',320,900)
    results['personal_swedish_preference_desktop_390_320_and_guide_no_horizontal_overflow']=True

    # Read actual stored rows: ciphertext only, truthful unconnected drafts.
    with sqlite3.connect(private/'proof.db')as db:
        sync=db.execute('SELECT provider,kind,status,enabled,encrypted_credentials FROM remotesyncconnection').fetchall()
        boxes=db.execute('SELECT provider,status,encrypted_credentials FROM cloudfileconnection').fetchall()
        meetings=db.execute('SELECT provider,status,enabled,encrypted_credentials FROM externalmeetingconnection').fetchall()
        assert len(sync)==1 and sync[0][0:2]==('google','files')and not sync[0][3]
        assert len(boxes)==2 and all(row[1]=='oauth_required'for row in boxes)
        assert len(meetings)==1 and meetings[0][0]=='microsoft_teams'and meetings[0][1]=='oauth_required'and not meetings[0][2]
        assert google_secret not in sync[0][4]and all(box_secret not in row[2]and revoked_secret not in row[2]for row in boxes)and teams_secret not in meetings[0][3]
        assert db.execute('SELECT COUNT(*) FROM externalmeeting').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM copilotturn').fetchone()[0]==0
    results['private_sqlite_exact_saved_providers_and_encrypted_secrets_without_meeting_dispatch']=True


def main():
    processes,streams,checks=[],[],ProofChecks()
    stage='private source snapshot and API startup'
    with tempfile.TemporaryDirectory(prefix='freecollab-connection-setup-') as directory:
        private=Path(directory)
        try:
            api_port,app_port=port(),port();base=f'http://127.0.0.1:{api_port}';origin=f'http://127.0.0.1:{app_port}'
            environment=os.environ.copy();environment.update(ENVIRONMENT='test',DATABASE_URL='sqlite:///'+str(private/'proof.db'),STORAGE_PATH=str(private/'files'),
                JWT_SECRET=secrets.token_urlsafe(48),ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),ACCOUNT_SECURITY_KEY_FILE=str(private/'account.key'),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',MESSAGE_ENCRYPTION_KEY_FILE=str(private/'message.keys'),
                MATRIX_ENABLED='false',MAIL_DELIVERY_MODE='disabled',PUBLIC_APP_URL=origin,CORS_ORIGINS=json.dumps([origin]),OIDC_ENABLED='false',WEBHOOKS_ENABLED='true',OTRUST_ENABLED='false',
                SYNC_ENABLED='true',CLOUD_FILES_ENABLED='true',EXTERNAL_MEETINGS_ENABLED='true',COPILOT_ENABLED='true',PUSH_ENABLED='false',PYTHONPATH=str(ROOT/'backend'),CONNECTION_PROOF_PRIVATE=str(private))
            environment['NO_PROXY']=','.join(filter(None,[environment.get('NO_PROXY',''),'127.0.0.1','localhost','::1']))
            controls(private)
            python=os.getenv('BACKEND_PYTHON') or (str(ROOT/'backend/.venv/bin/python') if (ROOT/'backend/.venv/bin/python').is_file() else sys.executable)
            launcher=private_api_launcher(private,api_port)
            web,vite=frontend_snapshot(private,base,app_port)
            frozen={'frontend_src':source_hash(web/'src'),'backend_app':source_hash(ROOT/'backend/app','*.py')}
            for command,name,cwd in [([python,str(launcher)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',web)]:
                stream=(private/name).open('w');streams.append(stream)
                processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            ready(base+'/ready',processes);ready(origin,processes)
            stage='real native accounts and membership'
            email='connection-owner-'+secrets.token_hex(6)+'@example.com'
            owner=api(base,'POST','/auth/register',data={'name':'Linnea Berg','email':email,'password':PASSWORD});owner['email']=email
            outsider=api(base,'POST','/auth/register',data={'name':'Private outsider','email':'connection-outsider-'+secrets.token_hex(6)+'@example.com','password':PASSWORD})
            SENSITIVE.extend([owner['access_token'],outsider['access_token']])
            workspace=api(base,'POST','/workspaces',owner,{'name':'Studio Nord'})
            channels=[api(base,'POST','/channels',owner,{'name':name,'workspace_id':workspace['id']}) for name in ['Design studio','Provider review']]
            stage='actual native App and connection recipes'
            blocked=[];errors=[]
            REPORT.parent.mkdir(parents=True,exist_ok=True)
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
                context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce',permissions=['clipboard-read','clipboard-write'])
                def route(request):
                    parsed=urlsplit(request.request.url)
                    if parsed.scheme in {'data','blob'} or parsed.hostname in {'127.0.0.1','localhost','::1'}:request.continue_()
                    else:blocked.append(parsed.hostname);request.abort()
                context.route('**/*',route)
                page=context.new_page();page.set_default_timeout(30000);page.on('pageerror',lambda error:errors.append(redacted(error)))
                try:browser_journey(page,context,base,origin,private,owner,outsider,workspace,channels,checks)
                except Exception as failure:
                    state=page.evaluate('''()=>({recipe:document.querySelector('[aria-label="Connection recipe"]')?.value,step:document.querySelector('[data-connection-step][aria-current="step"]')?.dataset.connectionStep,alerts:[...document.querySelectorAll('.fc-connection-setup [role="alert"],.fc-connection-setup [role="status"]')].map(el=>el.textContent),forms:[...document.querySelectorAll('.fc-connection-setup form')].map(el=>el.getAttribute('aria-label')),content:document.querySelector('.fc-connection-setup')?.innerText.slice(-2500)})''')
                    raise AssertionError(redacted(failure)+' Rendered state: '+redacted(state))from None
                assert not errors,'Browser JavaScript error: '+redacted(errors)
                assert not blocked,'Unexpected public browser request attempted'
                checks['actual_app_zero_javascript_errors_and_zero_public_browser_requests']=True
                context.close();browser.close()
            evidence=api(base,'GET','/__connection-proof-evidence')
            assert not evidence['provider_attempts'] and not (private/'provider-attempts.json').exists()
            checks['zero_server_provider_network_attempts_no_oauth_or_provider_tests']=True
            assert frozen=={'frontend_src':source_hash(ROOT/'frontend/src'),'backend_app':source_hash(ROOT/'backend/app','*.py')},'Product source changed after the private snapshot; rerun against the final source'
            checks['current_product_source_matches_private_snapshot']=True
            screenshots=[REPORT.parent/name for name in SCREENSHOTS]
            assert all(path.is_file()for path in screenshots),'Expected actual browser screenshot missing'
            REPORT.write_text(json.dumps({'ok':True,'checks':checks,'source_sha256':frozen,'public_provider_calls':0,'screenshots':list(map(str,screenshots)),'cleanup':'Private API/Vite processes, SQLite, accounts, keys and browser context removed; no Docker or retained runtime identities used.','scope':'Actual production App with private real native API/SQLite; fixture-only issuer DNS mapping, provider transport/network tripwires, no external OAuth or connectivity claim.'},indent=2)+'\n')
            print(json.dumps({'ok':True,'checks':checks,'report':str(REPORT)},indent=2))
        except Exception as failure:
            REPORT.parent.mkdir(parents=True,exist_ok=True)
            REPORT.write_text(json.dumps({'ok':False,'stage':stage,'error':redacted(failure),'checks':checks},indent=2)+'\n')
            for name in ('api.log','vite.log'):
                if (private/name).exists():print(name,redacted((private/name).read_text()[-1800:]),file=sys.stderr)
            raise AssertionError('Connection setup browser proof failed: '+redacted(failure)) from None
        finally:
            controls(private)
            stop(processes)
            for stream in streams:stream.close()


if __name__=='__main__':main()
