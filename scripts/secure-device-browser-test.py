#!/usr/bin/env python3
"""Real lost-own-device proof with three isolated browsers and Rust crypto.

Run against the same private Matrix/backend/Vite setup as secure-browser-test.py.
Fixture accounts only. No auth secrets, private keys or recovery keys are logged.
"""
import argparse
import json
import os
import secrets
import shutil
import time
import urllib.error
import urllib.request
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--api-base',default='http://127.0.0.1:8000')
parser.add_argument('--app-base',default='http://127.0.0.1:3000')
parser.add_argument('--chromium',default=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'))
args=parser.parse_args()

def request(path,data=None,auth=None):
    req=urllib.request.Request(args.api_base.rstrip('/')+path,data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+auth['access_token']} if auth else {})})
    with urllib.request.urlopen(req,timeout=90) as response:return json.load(response)

def poll(callback,predicate,description,attempts=150):
    for _ in range(attempts):
        value=callback()
        if predicate(value):return value
        time.sleep(.2)
    raise AssertionError(description)

password=secrets.token_urlsafe(24)
email='device-alice-'+secrets.token_hex(8)+'@example.com'
alice_auth=request('/auth/register',{'name':'DeviceAlice','email':email,'password':password})
bob_auth=request('/auth/register',{'name':'DeviceBob','email':'device-bob-'+secrets.token_hex(8)+'@example.com','password':secrets.token_urlsafe(24)})
retained_auth=request('/auth/login',{'email':email,'password':password})
channel=request('/channels',{'name':'Encrypted channel','member_ids':[bob_auth['user_id']]},alice_auth)['id']

with sync_playwright() as p:
    launch={'headless':True,'args':['--no-sandbox']}
    if args.chromium:launch['executable_path']=args.chromium
    browser=p.chromium.launch(**launch)
    try:
        def page_for(auth):
            page=browser.new_context().new_page();page.set_default_timeout(90000)
            page.goto(args.app_base,wait_until='domcontentloaded')
            for attempt in range(4):
                try:
                    page.evaluate('''async ({auth,base})=>{const {getSecureMessaging}=await import('/src/lib/secure-client.ts');const a=await getSecureMessaging();a.configure({baseUrl:base,getAccessToken:()=>auth.access_token,getUserId:()=>auth.user_id});window.secureProof=a;}''',{'auth':auth,'base':args.api_base.rstrip('/')})
                    break
                except Exception as error:
                    if attempt==3 or 'context was destroyed' not in str(error).lower():raise
                    page.wait_for_load_state('domcontentloaded');page.wait_for_timeout(500)
            return page
        old,bob,retained=page_for(alice_auth),page_for(bob_auth),page_for(retained_auth)
        old.evaluate('channel=>secureProof.enableChannel(channel,"Private device proof")',channel)
        bob.evaluate('channel=>secureProof.getMessages(channel)',channel)
        retained.evaluate('channel=>secureProof.getMessages(channel)',channel)
        old_id=old.evaluate('async()=>{window.oldCryptoClient=await secureProof.ensureSession();return oldCryptoClient.getDeviceId()}')
        retained_id=retained.evaluate('async()=>(await secureProof.ensureSession()).getDeviceId()')
        for page,recipient,count in ((bob,alice_auth,2),(old,bob_auth,1),(retained,bob_auth,1)):
            devices=poll(lambda:page.evaluate('channel=>secureProof.getDevices(channel)',channel),lambda rows:len([d for d in rows if d['user_id']==recipient['user_id']])>=count,'Recipient devices not published')
            for device in devices:
                if device['user_id']==recipient['user_id']:page.evaluate('device=>secureProof.verifyDevice(device,device.fingerprint)',device)
        before=bob.evaluate('args=>secureProof.sendMessage(...args)',[channel,'Before explicit device removal'])
        poll(lambda:old.evaluate('channel=>secureProof.getMessages(channel)',channel),lambda rows:any(r['id']==before['id'] and r['content']=='Before explicit device removal' for r in rows),'Old device did not decrypt established history')
        poll(lambda:retained.evaluate('channel=>secureProof.getMessages(channel)',channel),lambda rows:any(r['id']==before['id'] and r['content']=='Before explicit device removal' for r in rows),'Retained device did not decrypt established history')
        print('PASS three isolated browsers: peer verified both own recipient devices and both decrypted prior content',flush=True)

        # Keep the peer's real Rust cache stale by withholding only Alice key
        # query responses. Normal /sync continues; no synthetic keys are used.
        hold={'active':True}
        alice_matrix=old.evaluate('()=>oldCryptoClient.getUserId()')
        def intercept(route):
            body=route.request.post_data_json or {}
            if hold['active'] and alice_matrix in body.get('device_keys',{}):route.abort('blockedbyclient')
            else:route.continue_()
        bob.route('**/_matrix/client/v3/keys/query',intercept)
        result=retained.evaluate('args=>secureProof.revokeDevice(...args)',[old_id,password])
        assert result=={'revoked':True,'current':False}
        try:request('/auth/me',auth=alice_auth)
        except urllib.error.HTTPError as error:assert error.code==401
        else:raise AssertionError('Lost device native login survived revocation')
        assert request('/auth/me',auth=retained_auth)['id']==alice_auth['user_id']
        cached=bob.evaluate('''async ({user,id})=>{const c=await secureProof.ensureSession();const devices=await c.getCrypto().getUserDeviceInfo([user],true);return devices.get(user)?.has(id)===true}''',{'user':alice_matrix,'id':old_id})
        assert cached,'The stale-cache adversarial test did not preserve the old device'
        blocked=bob.evaluate('''async channel=>{try{await secureProof.sendEvent(channel,'eu.freecollab.channel.metadata',{name:'Must remain unsent while stale'});return ''}catch(e){return e.message}}''',channel)
        assert 'Device keys are refreshing' in blocked,blocked
        print('PASS confirmed Synapse UIA deletion, linked native JWT401 and stale Rust recipient cache blocks even encrypted metadata',flush=True)
        hold['active']=False
        def send_after():
            return bob.evaluate('''async channel=>{try{return {message:await secureProof.sendMessage(channel,'Future content after explicit device removal')}}catch(e){return {error:e.message}}}''',channel)
        after=poll(send_after,lambda value:'message' in value,'Peer did not refresh its actual Rust recipient cache',attempts=300)['message']
        poll(lambda:retained.evaluate('channel=>secureProof.getMessages(channel)',channel),lambda rows:any(r['id']==after['id'] and r['content']=='Future content after explicit device removal' for r in rows),'Retained device could not decrypt future content')
        wire=bob.evaluate('''async ({channel,event})=>{const e=await secureProof.getDecryptedEvent(channel,event);return {event_id:e.getId(),room_id:e.getRoomId(),sender:e.getSender(),origin_server_ts:e.getTs(),type:e.getWireType(),content:e.getWireContent()}}''',{'channel':channel,'event':after['id']})
        failed=old.evaluate('''async wire=>{const e=oldCryptoClient.getEventMapper()(wire);try{await oldCryptoClient.decryptEventIfNeeded(e)}catch{return true}return e.isDecryptionFailure()}''',wire)
        assert failed,'Revoked own device decrypted future ciphertext supplied directly to its Rust decoder'
        # Existing receiving keys/history remain available on the explicitly lost
        # browser; revocation controls future key sharing without deleting data.
        history=old.evaluate('''id=>{for(const room of oldCryptoClient.getRooms()){const e=room.getLiveTimeline().getEvents().find(e=>e.getId()===id);if(e)return e.getContent().body}return null}''',before['id'])
        assert history=='Before explicit device removal'
        listed=retained.evaluate('()=>secureProof.getDevices()')
        assert any(d['device_id']==retained_id for d in listed) and not any(d['device_id']==old_id for d in listed)
        print('PASS refreshed peer sends rotated ciphertext: retained device decrypts, revoked device fails actual Rust decode, prior keys/history preserved',flush=True)
    finally:browser.close()
