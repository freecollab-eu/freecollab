#!/usr/bin/env python3
"""Real Matrix source navigation acceptance using the existing secure harness.

Requires its disposable Synapse, authenticated gateway and Vite services. Creates
only normal fixture accounts/channels/events. No routes or business responses are
substituted; the native-message observer rejects accidental plaintext fallbacks.
SECURE_MESSAGE_NAVIGATION_PROOF_REPORT selects a JSON report containing booleans,
never bearer credentials, room keys, recovery keys or private message contents.
"""
import base64
import json
import os
from pathlib import Path
import runpy
import sys
from urllib.parse import unquote, urlsplit

from playwright.sync_api import expect


ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(os.getenv('SECURE_MESSAGE_NAVIGATION_PROOF_REPORT', '/tmp/freecollab-secure-message-navigation.json'))


def write_report(checks, status, stage, diagnostics=None):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({'status': status, 'stage': stage, 'checks': checks,
        'boundary': 'Actual private Synapse, Rust crypto, verified browsers and production resolver/App; fixture identities only.',
        **({'diagnostics': diagnostics} if diagnostics is not None else {})}, indent=2) + '\n')


def run_navigation_stage(alice, bob, users, channel_id, root, reply, args, request, poll):
    checks, forbidden, receipt_requests, fixture_identities = {}, [], [], []
    stage = 'authorized real encrypted root and reply'
    channel = request('/channels/' + channel_id, auth=users[0])

    def observe_native(route):
        path = unquote(urlsplit(route.request.url).path)
        single_message = path.startswith('/api/messages/') and not path.startswith('/api/messages/channels/')
        if route.request.method == 'GET' and (path == '/api/messages/channels/' + channel_id or single_message):
            forbidden.append(path)
            route.abort('blockedbyclient')
        else:
            route.continue_()

    def observe_receipt_request(request):
        path = unquote(urlsplit(request.url).path).removeprefix('/api')
        base = '/read-receipts/channels/' + channel_id
        if request.method == 'GET' and path in {base, base + '/unread'}:
            receipt_requests.append('unread' if path.endswith('/unread') else 'receipt')

    for page in (alice, bob):
        page.route('**/api/messages/**', observe_native)
        page.on('request', observe_receipt_request)
        page.evaluate('''async()=>{
          const {api}=await import('/src/lib/api.ts');api.syncStoredSession();
          window.secureProof=await(await import('/src/lib/secure-client.ts')).getSecureMessaging();
          window.navigationProof=(await import('/src/lib/message-navigation.ts')).resolveNavigationMessage;
        }''')
    try:
        # Direct adapter reads above have never populated api.rememberMessage.
        uncached = alice.evaluate('''async id=>{const {api}=await import('/src/lib/api.ts');try{await api.getThread(id);return false}catch(error){return String(error?.message).includes('Open this encrypted conversation')}}''', reply['id'])
        assert uncached, 'The navigation acceptance must begin without a remembered encrypted message ID'
        for index, page in enumerate((alice, bob)):
            stage = 'authorized real encrypted root and reply: device ' + str(index + 1)
            identity = page.evaluate('''async actor=>{const {api}=await import('/src/lib/api.ts');return {
              hasSession:!!api.getAccessToken(),actorMatchesFixture:api.getCurrentUserId()===actor};}''', users[index]['user_id'])
            fixture_identities.append(identity)
            assert identity == {'hasSession': True, 'actorMatchesFixture': True}, 'Fixture native session must be current before source navigation'
            result = page.evaluate('''async({channel,root,reply})=>{
              const parent=await navigationProof(channel,root,()=>true),child=await navigationProof(channel,reply,()=>true);
              return {parent:parent.id===root&&!parent.parent_id&&!parent.deleted&&!parent.locked&&parent.encrypted===true,
                child:child.id===reply&&child.parent_id===root&&!child.deleted&&!child.locked&&child.encrypted===true,
                text:parent.content==='Only recipient devices can read this private product message.'&&child.content==='Private thread reply'};
            }''', {'channel': channel, 'root': root['id'], 'reply': reply['id']})
            assert result == {'parent': True, 'child': True, 'text': True}
        checks['actual_verified_Rust_root_reply_and_exact_parent_without_remembered_ID_cache'] = True
        assert not forbidden, 'Encrypted navigation attempted a native message endpoint'

        stage = 'actual App encrypted return summary reply navigation'
        # Reload the same real device, preserving its actual IndexedDB keys.
        # The supported app-link route selects the channel; the visible summary
        # button then performs the actual source navigation through Chat.
        source = {'workspace_id': channel['workspace_id'], 'channel_id': channel_id, 'kind': 'channel', 'item_id': channel_id}
        fragment = base64.urlsafe_b64encode(json.dumps(source, separators=(',', ':')).encode()).decode().rstrip('=')
        # Observe actual entry call outcomes without changing a response,
        # substituting a projection or adding an artificial delivery gate.
        alice.add_init_script('''{
          const target=''' + json.dumps(channel_id) + ''';
          window.navigationEntryCalls=[];
          performance.setResourceTimingBufferSize(5000);
          void import('/src/lib/api.ts').then(({api})=>{
            for(const name of ['getReadReceipts','getMessages']){
              const original=api[name].bind(api);
              api[name]=(...args)=>{
                const operation=original(...args);
                if(args[0]!==target)return operation;
                return operation.then(rows=>{
                  const own=name==='getReadReceipts'?rows.find(row=>row.user_id===api.getCurrentUserId()):null;
                  window.navigationEntryCalls.push({method:name,fulfilled:true,hasOwnReceipt:!!own,hasReadAt:!!own?.read_at});
                  return rows;
                },error=>{
                  window.navigationEntryCalls.push({method:name,fulfilled:false,status:error?.status||null,
                    category:/signed-in account changed|encryption session changed|encrypted conversation changed/i.test(String(error?.message))?'owner-generation-changed':'other-error'});
                  throw error;
                });
              };
            }
            void import('/src/lib/secure/index.ts').then(({secureMessaging})=>{
              for(const name of ['handles','getReadReceipts']){
                const original=secureMessaging[name].bind(secureMessaging);
                secureMessaging[name]=(...args)=>{
                  const operation=original(...args);
                  if(args[0]!==target)return operation;
                  return operation.then(value=>{
                    const own=name==='getReadReceipts'?value.find(row=>row.user_id===api.getCurrentUserId()):null;
                    window.navigationEntryCalls.push({method:'secure.'+name,fulfilled:true,
                      ...(name==='handles'?{secureClassification:value}:{receiptCount:value.length,hasOwnReceipt:!!own,hasReadAt:!!own?.read_at})});
                    return value;
                  },error=>{window.navigationEntryCalls.push({method:'secure.'+name,fulfilled:false,status:error?.status||null});throw error});
                };
              }
            }).catch(()=>{window.navigationSecureObserverUnavailable=true});
          }).catch(()=>{window.navigationEntryObserverUnavailable=true});
        }''')
        alice.goto(args.app_base.rstrip('/') + '/#open=' + fragment, wait_until='domcontentloaded', timeout=90000)
        # Changing only the fragment is a same-document navigation. The earlier
        # module-only fixture deliberately kept the mounted AccountProvider
        # anonymous; a real reload must initialize it from the stored session.
        alice.reload(wait_until='domcontentloaded', timeout=90000)
        expect(alice.locator('.fc-chat')).to_be_visible(timeout=90000)
        alice.wait_for_function("window.navigationEntryCalls?.some(row=>row.method==='getReadReceipts')", timeout=90000)
        initial_receipt = alice.evaluate("window.navigationEntryCalls.find(row=>row.method==='getReadReceipts')")
        assert initial_receipt['fulfilled'] and initial_receipt['hasOwnReceipt'] and initial_receipt['hasReadAt'], 'Actual encrypted entry did not capture its genuine prior read receipt'
        summary = alice.locator('.fc-channel-return')
        expect(summary).to_be_visible(timeout=90000)
        summary.locator('summary').click()
        target = summary.get_by_role('button').filter(has=alice.get_by_text('Reply to your message', exact=True))
        expect(target).to_have_count(1, timeout=90000)
        target.click()
        actual_reply = alice.locator('[data-thread-reply=' + json.dumps(reply['id']) + ']')
        expect(actual_reply).to_be_visible(timeout=90000)
        expect(actual_reply).to_contain_text('Private thread reply')
        expect(alice.locator('.fc-chat-thread-parent[data-message-source-id=' + json.dumps(root['id']) + ']')).to_be_visible()
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        alice.screenshot(path=str(REPORT.with_name(REPORT.stem + '-app-summary.png')))
        alice.get_by_role('button', name='Close thread', exact=True).click()
        checks['actual_App_encrypted_return_summary_opens_exact_reply_and_root_parent'] = True
        alice.evaluate('''async()=>{window.secureProof=await(await import('/src/lib/secure-client.ts')).getSecureMessaging();window.navigationProof=(await import('/src/lib/message-navigation.ts')).resolveNavigationMessage}''')

        stage = 'real encrypted nonmessages replacements and redactions'
        custom = alice.evaluate('args=>secureProof.sendEvent(...args)', [channel_id, 'eu.freecollab.product.task', {'title': 'Navigation type boundary fixture'}])
        replacement = alice.evaluate('args=>secureProof.sendEvent(...args)', [channel_id, 'm.room.message', {
            'msgtype': 'm.text', 'body': '* Source replacement fixture', 'm.new_content': {'msgtype': 'm.text', 'body': 'Source replacement fixture'},
            'm.relates_to': {'rel_type': 'm.replace', 'event_id': root['id']}}])
        disposable = alice.evaluate('args=>secureProof.sendMessage(...args)', [channel_id, 'Disposable source redaction fixture'])
        alice.evaluate('args=>secureProof.deleteMessage(...args)', [channel_id, disposable['id']])
        for event_id in (custom['event_id'], replacement['event_id'], disposable['id']):
            rejected = alice.evaluate('''async({channel,id})=>{try{await navigationProof(channel,id,()=>true);return false}catch{return true}}''', {'channel': channel, 'id': event_id})
            assert rejected, 'A custom, replacement or redacted real Matrix event became a source message'
        checks['real_homeserver_custom_event_replacement_and_redaction_rejected'] = True

        stage = 'actual Rust decryption failure and foreign room rejection'
        private = request('/channels', {'name': 'Navigation private crypto boundary'}, users[0])['id']
        alice.evaluate('id=>secureProof.enableChannel(id,"Private navigation boundary")', private)
        isolated = alice.evaluate('args=>secureProof.sendMessage(...args)', [private, 'Private-room ciphertext boundary fixture'])
        rejected_foreign = alice.evaluate('''async({channel,id})=>{try{await navigationProof(channel,id,()=>true);return false}catch{return true}}''', {'channel': channel, 'id': isolated['id']})
        assert rejected_foreign, 'A genuine event from another room became the shared-room source'
        # Copy genuine Rust-produced ciphertext, not a mocked decryption result.
        # Its Megolm session/embedded room authority belongs to the private room.
        copied = alice.evaluate('''async({privateChannel,sharedChannel,id})=>{
          const event=await secureProof.getDecryptedEvent(privateChannel,id),{c,roomId,membershipEpoch}=await secureProof.roomFor(sharedChannel,true);
          if(event.getWireType()!=='m.room.encrypted')throw Error('The negative fixture must use actual encrypted wire bytes');
          if(typeof membershipEpoch!=='string'||!membershipEpoch)throw Error('The negative fixture requires a current authorized membership epoch');
          // Explicit adversarial fixture transport to our owned gateway, not
          // an unregistered SDK transaction and not a disabled publish guard.
          // Keep the actual session bearer inside this browser and supply the
          // exact fresh membership fence enforced by the production gateway.
          const endpoint=c.getHomeserverUrl().replace(/\\/$/,'')+'/_matrix/client/v3/rooms/'+encodeURIComponent(roomId)
            +'/send/m.room.encrypted/navigation-room-boundary-'+crypto.randomUUID();
          const response=await fetch(endpoint,{method:'PUT',headers:{'Content-Type':'application/json',
            Authorization:'Bearer '+c.getAccessToken(),'X-FreeCollab-Membership-Epoch':membershipEpoch},body:JSON.stringify(event.getWireContent())});
          if(!response.ok)throw Error('Owned ciphertext fixture transport rejected: '+response.status);
          const result=await response.json();if(typeof result.event_id!=='string')throw Error('Owned ciphertext fixture receipt missing');
          return result;
        }''', {'privateChannel': private, 'sharedChannel': channel_id, 'id': isolated['id']})
        unreadable = bob.evaluate('''async({channel,id})=>{
          let rejected=false;try{await navigationProof(channel,id,()=>true)}catch{rejected=true}
          const {c,room}=await secureProof.roomFor(channel.id),timeline=await c.getEventTimeline(room.getUnfilteredTimelineSet(),id);
          const event=timeline?.getEvents().find(event=>event.getId()===id);
          return {rejected,failed:!!event?.isDecryptionFailure()};
        }''', {'channel': channel, 'id': copied['event_id']})
        assert unreadable == {'rejected': True, 'failed': True}, 'Actual unavailable/wrong-room Megolm ciphertext did not fail closed'
        checks['owned_adversarial_wire_fixture_uses_current_authenticated_membership_epoch'] = True
        checks['actual_Rust_wrong_room_ciphertext_decryption_failure_has_no_plaintext_fallback'] = True

        stage = 'current membership and session revocation'
        request('/channels/' + channel_id + '/members/' + users[1]['user_id'], auth=users[0], method='DELETE')
        rejected = bob.evaluate('''async({channel,id})=>{
          let source=false,adapter=false;
          try{await navigationProof(channel,id,()=>true)}catch{source=true}
          try{await secureProof.getMessage(channel.id,id)}catch{adapter=true}
          return {source,adapter};
        }''', {'channel': channel, 'id': root['id']})
        assert rejected == {'source': True, 'adapter': True}
        checks['committed_membership_removal_blocks_cached_source_and_direct_secure_reader'] = True
        request('/auth/logout', {}, users[0])
        logged_out = alice.evaluate('''async({channel,id})=>{try{await navigationProof(channel,id,()=>true);return false}catch{return true}}''', {'channel': channel, 'id': reply['id']})
        assert logged_out, 'A revoked native session retained encrypted source navigation'
        checks['committed_native_logout_blocks_existing_crypto_device_source_navigation'] = True
        assert not forbidden, 'An encrypted source request reached a native plaintext message endpoint'
        assert not receipt_requests, 'An encrypted entry reached native plaintext receipt or unread-count endpoints'
        checks['no_native_message_content_lookup_or_history_fallback_for_encrypted_sources'] = True
        checks['no_native_receipt_or_unread_count_fallback_for_encrypted_entry'] = True
        write_report(checks, 'passed', 'complete')
    except Exception:
        diagnostics = {}
        try:
            REPORT.parent.mkdir(parents=True, exist_ok=True)
            alice.screenshot(path=str(REPORT.with_name(REPORT.stem + '-failure.png')))
        except Exception:
            diagnostics['screenshot_available'] = False
        try:
            diagnostics.update(alice.evaluate('''async({id,actor,root})=>{
              const {api}=await import('/src/lib/api.ts');
              const output={hasNativeSession:!!api.getAccessToken(),actorMatchesFixture:api.getCurrentUserId()===actor,
                chatVisible:!!document.querySelector('.fc-chat'),summaryPresent:!!document.querySelector('.fc-channel-return'),
                expectedRootPresent:!![...document.querySelectorAll('[data-message-source-id]')].find(node=>node.dataset.messageSourceId===root),
                workspaceControl:document.querySelector('[aria-label^="Switch workspace"]')?.textContent?.trim().slice(0,120)||null,
                channelControl:document.querySelector('[aria-label="Switch channel"]')?.textContent?.trim().slice(0,120)||null,
                entryNotice:document.querySelector('.fc-flow-entry-notice')?.textContent?.trim().slice(0,400)||null,
                returnStatus:document.querySelector('.fc-channel-return-status')?.textContent?.trim().slice(0,400)||null,
                entryCalls:(window.navigationEntryCalls||[]).slice(0,30),
                entryObserverUnavailable:!!window.navigationEntryObserverUnavailable,
                secureObserverUnavailable:!!window.navigationSecureObserverUnavailable,
                nativeReceiptContentReads:performance.getEntriesByType('resource').filter(entry=>{
                  try{return new URL(entry.name).pathname.endsWith('/read-receipts/channels/'+id)}catch{return false}
                }).length,
                messageAlerts:[...document.querySelectorAll('.fc-chat-messages [role="alert"],.fc-chat-main [role="alert"]')].map(node=>node.textContent?.trim().slice(0,400)).slice(0,3)};
              try{const channel=await api.getChannel(id);output.channel={id:channel.id,workspace_id:channel.workspace_id,secure:!!channel.secure}}
              catch(error){output.channelReadStatus=error?.status||'unavailable'}
              try{const receipt=(await api.getReadReceipts(id)).find(row=>row.user_id===actor);output.receipt={present:!!receipt,
                hasReadAt:!!receipt?.read_at,pointsToExpectedRoot:receipt?.last_read_message_id===root}}
              catch(error){output.receiptReadStatus=error?.status||'unavailable'}
              return output;
            }''', {'id': channel_id, 'actor': users[0]['user_id'], 'root': root['id']}))
        except Exception:
            diagnostics['page_metadata_available'] = False
        diagnostics['nativeReceiptRequests'] = receipt_requests
        diagnostics['fixtureIdentities'] = fixture_identities
        write_report(checks, 'failed', stage, diagnostics)
        raise
    finally:
        for page in (alice, bob):
            page.unroute('**/api/messages/**', observe_native)
            page.remove_listener('request', observe_receipt_request)


if __name__ == '__main__':
    harness = ROOT / 'scripts/secure-browser-test.py'
    sys.argv = [str(harness), *sys.argv[1:], '--navigation-only']
    if not any(flag in sys.argv for flag in ('-h', '--help')):
        write_report({}, 'running', 'disposable secure fixture startup')
    try:
        runpy.run_path(str(harness), run_name='__main__')
    except SystemExit:
        raise
    except Exception:
        if not REPORT.exists() or json.loads(REPORT.read_text()).get('status') == 'running':
            write_report({}, 'failed', 'disposable secure fixture startup')
        raise
