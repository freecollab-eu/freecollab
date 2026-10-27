#!/usr/bin/env python3
"""Real two-browser product E2EE proof against a disposable secure deployment.

Requires: configured private Synapse + backend, frontend Vite dev server, installed
backend dev dependencies and Chromium. Only normal fixture accounts/API resources
are created. Tokens and recovery keys remain in process memory and are not logged.
"""
import argparse
import json
import os
import secrets
import shutil
import time
import urllib.request
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--api-base', default='http://127.0.0.1:8000')
parser.add_argument('--app-base', default='http://127.0.0.1:3000')
parser.add_argument('--threads-only', action='store_true', help='Stop after the focused encrypted thread and reply-edit checks.')
parser.add_argument('--navigation-only', action='store_true', help='Run focused real encrypted source navigation and authorization acceptance.')
parser.add_argument('--chromium', default=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'))
args = parser.parse_args()


def request(path, data=None, auth=None, method=None):
    req = urllib.request.Request(args.api_base.rstrip('/') + path,
        data=json.dumps(data).encode() if data is not None else None, method=method,
        headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + auth['access_token']} if auth else {})})
    with urllib.request.urlopen(req, timeout=90) as response:
        return json.load(response) if response.status != 204 else None


def poll(callback, predicate, description, attempts=150):
    for _ in range(attempts):
        value = callback()
        if predicate(value):
            return value
        time.sleep(.2)
    raise AssertionError(description)

passwords = [secrets.token_urlsafe(24), secrets.token_urlsafe(24)]
users = [request('/auth/register', {'name': name, 'email': name.lower() + secrets.token_hex(8) + '@example.com', 'password': password})
         for name, password in zip(('E2EEAlice', 'E2EEBob'), passwords)]
assert request('/secure/status', auth=users[0])['enabled'], 'Start the backend with MATRIX_ENABLED=true and a private Synapse.'
channel = request('/channels', {'name': 'Encrypted channel', 'member_ids': [users[1]['user_id']]}, users[0])['id']

with sync_playwright() as playwright:
    launch = {'headless': True, 'args': ['--no-sandbox']}
    if args.chromium:
        launch['executable_path'] = args.chromium
    browser = playwright.chromium.launch(**launch)
    try:
        def page_for(user):
            page = browser.new_context().new_page()
            page.set_default_timeout(90000)
            page.goto(args.app_base, wait_until='domcontentloaded')
            if args.navigation_only:
                # Let the unauthenticated App finish its landing route before
                # binding module-only fixture auth. This keeps the real API's
                # remembered-message map empty until the later actual App reload.
                page.locator('.fc-public-nav').wait_for(state='visible')
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            for attempt in range(4):
                try:
                    page.evaluate('''async ({auth,base,navigation}) => {
                        if(navigation){
                            localStorage.setItem('freecollab_token',auth.access_token);
                            localStorage.setItem('freecollab_user_id',auth.user_id);
                            localStorage.setItem('freecollab_public_key',auth.public_key||'');
                            if(auth.encrypted_private_key)localStorage.setItem('freecollab_encrypted_private_key',auth.encrypted_private_key);
                            const {api}=await import('/src/lib/api.ts');api.syncStoredSession();
                        }
                        const {getSecureMessaging} = await import('/src/lib/secure-client.ts');
                        const adapter = await getSecureMessaging();
                        adapter.configure({baseUrl:base,getAccessToken:()=>auth.access_token,getUserId:()=>auth.user_id});
                        window.secureProof=adapter;
                        window.secureFiles=(await import('/src/lib/secure/files.ts')).secureFileProvider;
                    }''', {'auth': user, 'base': args.api_base.rstrip('/'), 'navigation': args.navigation_only})
                    break
                except Exception as error:
                    if attempt == 3 or 'context was destroyed' not in str(error).lower():
                        raise
                    page.wait_for_load_state('domcontentloaded')
                    page.wait_for_timeout(500)
            return page
        alice, bob = page_for(users[0]), page_for(users[1])
        binding = alice.evaluate('channel=>secureProof.enableChannel(channel,"Private project title")', channel)
        bob.evaluate('channel=>secureProof.getMessages(channel)', channel)
        for page, other in ((alice, users[1]), (bob, users[0])):
            devices = poll(lambda: page.evaluate('channel=>secureProof.getDevices(channel)', channel), lambda rows: any(device['user_id'] == other['user_id'] for device in rows), 'Recipient cryptographic device was not published.')
            matches = [device for device in devices if device['user_id'] == other['user_id']]
            assert matches, 'Both browsers must have a real Matrix cryptographic device.'
            for device in matches:
                # The harness obtains public fingerprints independently from the
                # other isolated device. The product requires out-of-band comparison.
                page.evaluate('device=>secureProof.verifyDevice(device,device.fingerprint)', device)
        poll(lambda: bob.evaluate('channel=>secureProof.getChannelTitle(channel)', channel), lambda title: title == 'Private project title', 'Encrypted channel title was not republished to the verified recipient.')
        message = 'Only recipient devices can read this private product message.'
        sent = alice.evaluate('args=>secureProof.sendMessage(...args)', [channel, message])
        poll(lambda: bob.evaluate('channel=>secureProof.getMessages(channel)', channel), lambda rows: any(row['id'] == sent['id'] and row['content'] == message for row in rows), 'Recipient did not decrypt the message.')
        wire = bob.evaluate('''async ({channel,event})=>{const e=await secureProof.getDecryptedEvent(channel,event);return {type:e.getWireType(),wire:JSON.stringify(e.getWireContent())}}''', {'channel': channel, 'event': sent['id']})
        assert wire['type'] == 'm.room.encrypted' and message not in wire['wire']
        print('PASS two isolated browsers: native-session-bound Matrix E2EE, encrypted title and opaque wire content', flush=True)
        if args.navigation_only:
            # A genuine prior encrypted read is the return summary's baseline.
            alice.evaluate('args=>secureProof.markRead(...args)', [channel, sent['id']])
        reply = bob.evaluate('args=>secureProof.sendMessage(...args)', [channel, 'Private thread reply', sent['id']])
        poll(lambda: alice.evaluate('args=>secureProof.getThread(...args)', [channel, sent['id']]), lambda rows: any(row['id'] == reply['id'] for row in rows), 'Encrypted thread reply missing.')
        if args.navigation_only:
            import importlib.util
            from pathlib import Path
            stage_path = Path(__file__).resolve().parents[1] / 'frontend/tests/secure-message-navigation-browser.py'
            stage_spec = importlib.util.spec_from_file_location('secure_message_navigation_stage', stage_path)
            stage = importlib.util.module_from_spec(stage_spec); stage_spec.loader.exec_module(stage)
            stage.run_navigation_stage(alice, bob, users, channel, sent, reply, args, request, poll)
            print('PASS actual encrypted source navigation, App reply source, and fail-closed authorization', flush=True)
            raise SystemExit(0)
        bob.evaluate('args=>secureProof.editMessage(...args)', [channel, reply['id'], 'Private edited thread reply'])
        poll(lambda: alice.evaluate('args=>secureProof.getThread(...args)', [channel, sent['id']]), lambda rows: any(row['id'] == reply['id'] and row['content'] == 'Private edited thread reply' and row.get('edited_at') for row in rows), 'Encrypted reply edit was not folded into thread history.')
        if args.threads_only:
            print('PASS full joined thread history and authenticated reply edits', flush=True)
            raise SystemExit(0)
        alice.evaluate('args=>secureProof.editMessage(...args)', [channel, sent['id'], 'Private edited text'])
        poll(lambda: bob.evaluate('channel=>secureProof.getMessages(channel)', channel), lambda rows: any(row['id'] == sent['id'] and row['content'] == 'Private edited text' for row in rows), 'Encrypted edit missing.')
        bob.evaluate('args=>secureProof.addReaction(...args)', [channel, sent['id'], '✅'])
        poll(lambda: alice.evaluate('args=>secureProof.getReactions(...args)', [channel, sent['id']]), lambda rows: any(row['emoji'] == '✅' for row in rows), 'Encrypted reaction missing.')
        print('PASS encrypted replies, edits and reactions', flush=True)
        attachment = alice.evaluate('''channel=>secureFiles.upload(channel,new File(['private file bytes'],'private-notes.txt',{type:'text/plain'}),{requestId:'secure-browser-attachment'})''', channel)
        poll(lambda: bob.evaluate('channel=>secureFiles.list(channel)', channel), lambda rows: any(row['id'] == attachment['id'] for row in rows), 'Encrypted attachment metadata missing.')
        plaintext = bob.evaluate('''async file=>(await secureFiles.download(file)).text()''', attachment)
        assert plaintext == 'private file bytes' and attachment['encrypted']
        again = alice.evaluate('''channel=>secureFiles.upload(channel,new File(['private file bytes'],'private-notes.txt',{type:'text/plain'}),{requestId:'secure-browser-attachment'})''', channel)
        assert again['id'] == attachment['id'], 'Retry duplicated the encrypted file.'
        print('PASS encrypted attachment upload, authenticated metadata, recipient hash-checked decrypt and retry deduplication', flush=True)
        recovery = alice.evaluate('()=>secureProof.setupRecovery()')
        assert len(recovery) > 32
        assert alice.evaluate('()=>secureProof.recoveryStatus()')['ready']
        alice.evaluate('()=>secureProof.stop()')
        alice.evaluate('key=>secureProof.unlockRecovery(key)', recovery)
        print('PASS Matrix cross-signing, secret storage, client-only recovery key, lock and unlock', flush=True)
        # Fresh browser + native auth session creates a new Matrix device, proving
        # restoration does not depend on the first browser's IndexedDB keys.
        profile = request('/auth/me', auth=users[0])
        login = request('/auth/login', {'email': profile['email'], 'password': passwords[0]})
        restored = page_for(login)
        restored.evaluate('key=>secureProof.unlockRecovery(key)', recovery)
        poll(lambda: restored.evaluate('channel=>secureProof.getMessages(channel)', channel), lambda rows: any(row['id'] == sent['id'] and row['content'] == 'Private edited text' for row in rows), 'Fresh device could not restore encrypted room history.')
        print('PASS new browser/device restoration from encrypted backup', flush=True)
        request('/channels/' + channel + '/members/' + users[1]['user_id'], auth=users[0], method='DELETE')
        after = alice.evaluate('args=>secureProof.sendMessage(...args)', [channel, 'Content after member removal'])
        try:
            bob.evaluate('''async ({room,base})=>{const c=await secureProof.ensureSession();const r=await fetch(base+'/_matrix/client/v3/rooms/'+encodeURIComponent(room)+'/messages?dir=b',{headers:{Authorization:'Bearer '+c.getAccessToken()}});if(r.status!==403)throw Error('Removed member was not denied');}''', {'room': binding['room_id'], 'base': args.api_base.rstrip('/')})
        except Exception:
            raise AssertionError('Removed member retained remote room history access.') from None
        assert after['id'].startswith('$')
        future_wire = alice.evaluate('''async ({channel,event})=>{const e=await secureProof.getDecryptedEvent(channel,event);return {event_id:e.getId(),room_id:e.getRoomId(),sender:e.getSender(),origin_server_ts:e.getTs(),type:e.getWireType(),content:e.getWireContent()}}''', {'channel': channel, 'event': after['id']})
        revoked = bob.evaluate('''async wire=>{const c=await secureProof.ensureSession();const e=c.getEventMapper()(wire);try{await c.decryptEventIfNeeded(e);}catch{return {failed:true};}return {failed:e.isDecryptionFailure()};}''', future_wire)
        assert revoked['failed'], 'Removed member decrypted a future event when given its ciphertext.'
        print('PASS remote-first removal and future-key rotation with membership epoch enforcement', flush=True)
    finally:
        browser.close()
