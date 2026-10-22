#!/usr/bin/env python3
"""Actual Slack UI, two Rust devices, encrypted provenance/media and resumable ACK loss."""
import base64, hashlib, importlib.util, io, json, os, secrets, shutil, sqlite3, subprocess, tempfile, time, zipfile
from pathlib import Path
import yaml
from playwright.sync_api import Error as PlaywrightError, expect, sync_playwright
ROOT=Path(__file__).resolve().parents[2]
PYTHON=os.getenv('BACKEND_PYTHON') or str(ROOT/'backend/.venv/bin/python')
REPORT=Path(os.getenv('SLACK_MIGRATION_REPORT',str(ROOT.parent/'.freecollab/proof-results/slack-013-secure-browser.json')))
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
f=module('slack_fixture_helpers',ROOT/'frontend/tests/connection-setup-browser.py')
e=module('slack_owned_matrix_helpers',ROOT/'packages/components/tests/embed-browser-proof.py')
SOURCES=['backend/app/api/slack_import.py','backend/app/models/slack_import.py','backend/app/services/slack_import.py','frontend/src/lib/slack-archive.ts','frontend/src/lib/slack-secure-import.ts','frontend/src/components/SlackImportPanel.tsx','frontend/src/components/HistoricalSlackDetails.tsx','frontend/src/lib/secure/index.ts','frontend/src/lib/secure/transaction-retry.ts']
def hashes():return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCES}
def poll(call,accept,description):
 for _ in range(200):
  value=call()
  if accept(value):return value
  time.sleep(.15)
 raise AssertionError(description)
def browser_json(page,code,args=None):
 value=json.loads(page.evaluate('async args=>{try{return JSON.stringify({value:await ('+code+')(args)})}catch(error){return JSON.stringify({error:String(error?.message||error)})}}',args));assert 'error' not in value,value.get('error');return value.get('value')
def main():
 expect.set_options(timeout=45000)
 processes=[];streams=[];checks={};navigation_retries=0;container='freecollab-slack-013-'+secrets.token_hex(5);before=hashes();stage='startup'
 with tempfile.TemporaryDirectory(prefix='freecollab-slack-013-') as location:
  private=Path(location)
  try:
   matrix=e.start_matrix(private,container)
   # The fixture exercises bulk import, with an explicitly generous private burst budget.
   e.docker(['stop',container]);config=private/'matrix/homeserver.yaml';values=yaml.safe_load(config.read_text());values['rc_message']={'per_second':10000,'burst_count':10000};config.write_text(yaml.safe_dump(values));config.chmod(0o600);e.docker(['start',container]);e.ready(matrix['MATRIX_INTERNAL_URL']+'/_matrix/client/versions',[],timeout=90)
   api_port,web_port=f.port(),f.port();base=f'http://127.0.0.1:{api_port}';origin=f'http://127.0.0.1:{web_port}';database=private/'proof.db'
   env=dict(os.environ);env.update(ENVIRONMENT='test',DATABASE_URL='sqlite:///'+str(database),STORAGE_PATH=str(private/'files'),STORAGE_BACKEND='local',REDIS_URL='',JWT_SECRET=secrets.token_urlsafe(48),ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',ACCOUNT_SECURITY_KEY_FILE=str(private/'account.key'),MESSAGE_ENCRYPTION_KEY_FILE=str(private/'message.keys'),MAIL_DELIVERY_MODE='disabled',PUBLIC_APP_URL=origin,CORS_ORIGINS=json.dumps([origin]),OIDC_ENABLED='false',WEBHOOKS_ENABLED='false',OTRUST_ENABLED='false',SYNC_ENABLED='false',COPILOT_ENABLED='false',CLOUD_FILES_ENABLED='false',EXTERNAL_MEETINGS_ENABLED='false',PUSH_ENABLED='false',NATIVE_PUSH_ENABLED='false',PYTHONPATH=str(ROOT/'backend'),**matrix)
   env['NO_PROXY']=','.join(filter(None,[env.get('NO_PROXY',''),'127.0.0.1','localhost','::1']))
   launcher=private/'api.py';launcher.write_text('import uvicorn\nfrom app.main import socket_app\nuvicorn.run(socket_app,host="127.0.0.1",port='+str(api_port)+',access_log=False,proxy_headers=False)\n')
   web,vite=f.frontend_snapshot(private,base,web_port)
   vite.write_text(vite.read_text().replace(json.dumps(str(private/'vite-cache')),json.dumps(str(web/'.vite-cache'))))
   for command,name,cwd in [([PYTHON,str(launcher)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',web)]:
    stream=(private/name).open('w');streams.append(stream);processes.append(subprocess.Popen(command,cwd=cwd,env=env,stdout=stream,stderr=stream,start_new_session=True))
   f.ready(base+'/ready',processes);f.ready(origin,processes)
   users=[];emails=[]
   for name in ['Migration Owner','Migration Peer']:
    email='slack-'+secrets.token_hex(5)+'@example.com';emails.append(email);users.append(f.api(base,'POST','/auth/register',data={'name':name,'email':email,'password':f.PASSWORD}))
   alice,bob=users
   with sqlite3.connect(database) as connection:connection.execute("UPDATE user SET role='admin' WHERE id=?",(alice['user_id'],));connection.commit()
   workspace=f.api(base,'POST','/workspaces',alice,{'name':'Migration studio'});original=f.api(base,'POST','/channels',alice,{'name':'Original room','workspace_id':workspace['id']})
   invite=f.api(base,'POST','/channels/'+original['id']+'/invites',alice,{});f.api(base,'POST','/channels/invites/accept',bob,{'token':invite['token']});f.api(base,'PATCH','/workspaces/'+workspace['id']+'/members/'+bob['user_id'],alice,{'role':'member'})
   old_text='Earlier source revision '+('preserved-old-text-'*800)
   output=io.BytesIO()
   with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as writer:
    writer.writestr('users.json',json.dumps([{'id':'U1','name':'Original Alice'},{'id':'U2','name':'Original Bob'}]));writer.writestr('channels.json',json.dumps([{'id':'C1','name':'history','members':['U1','U2']}]))
    writer.writestr('history/2024-01-01.json',json.dumps([{'type':'message','user':'U1','ts':'1704067200.000001','text':'Encrypted imported root secret','edited':{'ts':'1704067400.000001'},'edit_history':[{'ts':'1704067200.000001','user':'U1','text':old_text}],'reactions':[{'name':'thumbsup','count':2,'users':['U1','U2']}],'files':[{'id':'F1','name':'original.txt','mimetype':'text/plain'}]},{'type':'message','user':'U2','ts':'1704067201.000001','thread_ts':'1704067200.000001','text':'Encrypted imported reply secret'}]));writer.writestr('files/F1',b'Encrypted portable file secret')
   archive=private/'slack.zip';archive.write_bytes(output.getvalue())
   with sync_playwright() as playwright:
    browser=playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
    contexts=[browser.new_context(viewport={'width':1440,'height':1050},reduced_motion='reduce') for _ in users];pages=[context.new_page() for context in contexts];errors=[];console=[];failed=[]
    try:
     for page in pages:
      page.set_default_timeout(90000);page.on('pageerror',lambda error:errors.append(str(error)[:300]));page.on('console',lambda message:console.append(message.text[:300]) if message.type=='error' else None);page.on('requestfailed',lambda request:failed.append({'path':request.url.split(origin)[-1],'failure':request.failure}))
     stage='actual account login and Rust session initialization';print(stage,flush=True)
     for page,user,email in zip(pages,users,emails):
      for attempt in range(3):
       first_failure=len(failed)
       try:
        page.goto(origin,wait_until='domcontentloaded');page.get_by_role('navigation').get_by_role('button',name='Open App',exact=True).wait_for(state='visible',timeout=20000);break
       except PlaywrightError:
        if attempt==2 or not any(row['failure']=='net::ERR_NETWORK_CHANGED' for row in failed[first_failure:]):raise
        navigation_retries+=1;print('Retrying initial page after disposable Docker network change',flush=True)
      page.evaluate('args=>localStorage.setItem(...args)',['freecollab.workspace:'+user['user_id'],workspace['id']]);page.get_by_role('navigation').get_by_role('button',name='Open App',exact=True).click();page.get_by_label('Email',exact=True).fill(email);page.get_by_label('Password',exact=True).fill(f.PASSWORD);page.get_by_role('button',name='Sign In',exact=True).click();page.wait_for_function('id=>localStorage.getItem("freecollab_user_id")===id',arg=user['user_id'])
     pages[0].get_by_role('button',name='Integrations',exact=True).click();dialog=pages[0].get_by_role('dialog',name='Integrations',exact=True)
     for page in pages:browser_json(page,"async()=>{window.secure=await(await import('/src/lib/secure-client.ts')).getSecureMessaging();await secure.ensureSession();return true}")
     stage='actual local-only encrypted ZIP preview';print(stage,flush=True)
     posts=[];pages[0].on('request',lambda req:posts.append({'path':req.url.split(origin)[-1],'body':req.post_data_buffer or b''}) if req.method in ['POST','PUT'] else None)
     dialog.get_by_role('button',name='Import from Slack',exact=True).click();panel=dialog.get_by_role('region',name='Import from Slack',exact=True)
     panel.get_by_label('Migration storage',exact=True).select_option('secure');panel.locator('input[type=file]').set_input_files(archive)
     expect(panel.get_by_role('checkbox',name='history',exact=False)).to_be_visible(timeout=20000)
     assert not any('/preview' in row['path'] for row in posts)
     panel.get_by_role('checkbox',name='history',exact=False).check();panel.get_by_text('Map historical people to current members',exact=True).click();panel.get_by_label('Original Alice',exact=True).select_option(alice['user_id']);panel.get_by_label('Original Bob',exact=True).select_option(bob['user_id']);panel.get_by_role('checkbox',name='I reviewed these destinations',exact=False).check();panel.get_by_role('button',name='Import selected history',exact=True).click()
     expect(panel.get_by_role('alert')).to_be_visible();channel=poll(lambda:f.api(base,'GET','/channels?workspace_id='+workspace['id'],alice),lambda rows:len(rows)==2,'opaque reserved channel missing');cid=next(row['id'] for row in channel if row['id']!=original['id'])
     for page in pages:
      def join_ready():
       result=page.evaluate('async channel=>{try{await secure.getMessages(channel);return {ready:true}}catch(error){return {error:String(error?.message||error)}}}',cid)
       if not result.get('ready'):assert result.get('error')=='Encrypted room state is unavailable. Sending is blocked.',result
       return result.get('ready')
      poll(join_ready,bool,'real Matrix join/encryption state did not synchronize')
     for page,other in [(pages[0],bob),(pages[1],alice)]:
      devices=poll(lambda:browser_json(page,'async channel=>secure.getDevices(channel)',cid),lambda rows:any(row['user_id']==other['user_id'] for row in rows),'real peer device missing')
      for device in devices:
       if device['user_id']==other['user_id']:browser_json(page,'async device=>{await secure.verifyDevice(device,device.fingerprint);return true}',device)
     checks['real_local_zip_review_current_mapped_members_and_two_verified_rust_devices']=True
     # Hold the accepted second message response, then pause. Resume must reuse its transaction.
     pages[0].evaluate('''()=>{const normal=window.fetch.bind(window);window.fixtureCiphertexts=[];window.fixtureMessageCount=0;window.fetch=async(input,init)=>{const url=new URL(typeof input==='string'?input:input instanceof URL?input.href:input.url,location.href);if((init?.method||'GET')==='PUT'&&/\\/send\\/m.room.encrypted\\/slack-message-/.test(url.pathname)){window.fixtureCiphertexts.push(init.body);const response=await normal(input,init);if(++window.fixtureMessageCount===2){window.fixtureHeld=true;await new Promise(resolve=>window.fixtureRelease=resolve);window.fixtureDropped=true;throw new TypeError('Private fixture lost the accepted response')}return response}return normal(input,init)}}''')
     stage='encrypted history chunks, attachment and accepted-response pause';print(stage,flush=True)
     panel.get_by_role('button',name='Import selected history',exact=True).click();pages[0].wait_for_function('window.fixtureHeld===true',timeout=90000);panel.get_by_role('button',name='Pause after this batch',exact=True).click();pages[0].evaluate('window.fixtureRelease()');expect(panel.get_by_text('Migration paused. Choose the exact same ZIP to resume safely.',exact=True)).to_be_visible()
     panel.get_by_role('button',name='Continue migration',exact=True).click();expect(panel.get_by_role('heading',name='Your archive is ready.',exact=True)).to_be_visible()
     messages=poll(lambda:browser_json(pages[1],'async channel=>secure.getMessages(channel)',cid),lambda rows:len(rows)==2,'peer did not decrypt exactly two migrated messages')
     root=next(row for row in messages if not row.get('parent_id'));reply=next(row for row in messages if row.get('parent_id'));assert reply['parent_id']==root['id'] and 'Encrypted imported root secret' in root['content']
     history=browser_json(pages[1],"async args=>(await import('/src/lib/slack-secure-import.ts')).readSlackHistory(args.channel,args.message,true)",{'channel':cid,'message':root['id']});assert history['history'][0]['text']==old_text and history['reactions'][0]['count']==2 and history['reactions'][0]['users'][1]['name']=='Original Bob'
     checks['chunked_export_history_reactions_full_text_and_cross_device_thread_decrypt']=True
     files=browser_json(pages[1],"async channel=>(await import('/src/lib/secure/files.ts')).secureFileProvider.list(channel)",cid);assert len(files)==1
     downloaded=browser_json(pages[1],"async file=>(await(await import('/src/lib/secure/files.ts')).secureFileProvider.download(file)).text()",files[0]);assert downloaded=='Encrypted portable file secret'
     checks['actual_client_encrypted_attachment_upload_and_peer_download']=True
     assert pages[0].evaluate('window.fixtureDropped===true&&window.fixtureMessageCount>=2')
     owner_messages=browser_json(pages[0],'async channel=>secure.getMessages(channel)',cid);assert len(owner_messages)==len({row['id'] for row in owner_messages})==2
     assert next(row for row in owner_messages if not row.get('parent_id'))['thread_count']==1 and root['thread_count']==1
     for row in posts:
      assert b'Encrypted imported root secret' not in row['body'] and b'Earlier source revision' not in row['body'] and b'Encrypted portable file secret' not in row['body']
     with sqlite3.connect(database) as connection:
      assert connection.execute('SELECT count(*) FROM message WHERE channel_id=?',(cid,)).fetchone()[0]==0
      assert connection.execute('SELECT count(*) FROM slackimportdetail').fetchone()[0]==0
      assert all(value=='' for value, in connection.execute('SELECT encrypted_archive FROM slackimport'))
     assert len(browser_json(pages[1],'async channel=>secure.getMessages(channel)',cid))==2
     checks['accepted_response_loss_resume_exactly_once_no_native_plaintext_or_zip_upload']=True
     state=pages[0].evaluate('''()=>new Promise((resolve,reject)=>{const request=indexedDB.open('freecollab-mobile-v1');request.onsuccess=()=>{const db=request.result;const result=db.transaction('vault').objectStore('vault').getAll();result.onsuccess=()=>{resolve(result.result.filter(v=>v.kind==='slack-import').map(v=>({kind:v.kind,hasCiphertext:v.ciphertext instanceof ArrayBuffer,hasPlainValue:'value' in v})));db.close()};result.onerror=reject};request.onerror=reject})''');assert state and all(v['hasCiphertext'] and not v['hasPlainValue'] for v in state)
     checks['resumable_checkpoint_real_indexeddb_ciphertext']=True
     dialog.get_by_role('button',name='Done',exact=True).click();pages[0].get_by_role('button',name='slack-history',exact=False).click()
     expect(pages[0].locator('.fc-chat-thread-link').first).to_contain_text('1 reply')
     disclosure=pages[0].locator('.fc-chat-message .fc-slack-history').first;disclosure.locator('summary').first.click();expect(disclosure.get_by_label('Historical Slack reactions',exact=True)).to_contain_text('thumbsup');expect(disclosure.get_by_label('Exported edit history',exact=True)).to_contain_text('Earlier source revision')
     checks['actual_chat_historical_badges_and_browsable_edit_disclosure']=True
     pages[0].evaluate("async()=>{(await import('/src/i18n/core.ts')).applyLocale('sv')}");expect(disclosure.locator('summary').first).to_contain_text('Importerad Slack-historik');pages[0].set_viewport_size({'width':320,'height':780});assert pages[0].evaluate('document.documentElement.scrollWidth<=innerWidth+1');assert not errors,errors
     checks['actual_swedish_narrow_ui_without_page_errors']=True
     f.api(base,'DELETE','/channels/'+cid+'/members/'+bob['user_id'],alice)
     f.reject(base,'POST','/secure/channels/'+cid+'/sync',bob,{},statuses=(403,404))
     denied=pages[1].evaluate('async channel=>{try{await secure.sendEvent(channel,"m.room.message",{msgtype:"m.text",body:"Denied post-removal fixture"});return false}catch{return true}}',cid);assert denied
     after=browser_json(pages[0],'async channel=>secure.sendEvent(channel,"m.room.message",{msgtype:"m.text",body:"Fresh current-member-only content"},"slack-after-removal")',cid)
     rejected=pages[1].evaluate('async args=>{try{await secure.getDecryptedEvent(args.channel,args.event);return {denied:false}}catch(error){return {denied:true,status:error.httpStatus||error.statusCode||null}}}',{'channel':cid,'event':after['event_id']});assert rejected['denied'] and rejected['status'] in (403,404),rejected
     checks['current_member_removal_blocks_new_server_access_writes_and_new_event_reads']=True
    except Exception:
     REPORT.parent.mkdir(parents=True,exist_ok=True);REPORT.with_suffix('.failure.json').write_text(json.dumps({'stage':stage,'completed_checks':checks,'source_sha256':before,'runtime_source_unchanged':before==hashes(),'errors':errors,'console':console,'failed':failed,'navigation_retries':navigation_retries,'body':pages[0].locator('body').inner_text()[-3500:]},indent=2));pages[0].screenshot(path=str(REPORT.with_suffix('.failure.png')),full_page=True);raise
    finally:
     for context in contexts:context.close()
     browser.close()
  finally:
   f.stop(processes)
   for stream in streams:stream.close()
   try:e.docker(['rm','-f',container])
   except Exception:pass
   for name in ['api.log','vite.log']:
    if (private/name).exists() and not checks:REPORT.parent.mkdir(parents=True,exist_ok=True);(REPORT.parent/('slack-013-'+name)).write_text((private/name).read_text()[-12000:])
 assert before==hashes(),'Owned runtime files changed during proof'
 REPORT.parent.mkdir(parents=True,exist_ok=True);REPORT.write_text(json.dumps({'checks':checks,'source_sha256':before,'runtime_source_unchanged':True,'initial_navigation_transport_retries':navigation_retries,'matrix':'Owned pinnedSynapse; actual Rust crypto and verified devices; bulk fixture explicitlyconfigured10000events/sec','cleanup':'OwnedAPI/Vite/browser/Synapse/binddata removed; originalservices retained','limits':['Synthetic accounts/export; no liveSlackAPI or customer identity provider','Same original sign-in/device and exactZIP required for encrypted resume','Previously decrypted local SDK history is retained after removal; new server access, writes and event fetches are denied']},indent=2)+'\n');print(json.dumps({'groups':len(checks),'passed':all(checks.values())}),flush=True)
if __name__=='__main__':main()
