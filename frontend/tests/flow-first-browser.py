#!/usr/bin/env python3
"""Actual conversation-first native/demo flows in disposable production fixtures.

No product route, source, state handler or business response is replaced. The
native history and resource relations use real APIs; user actions use the real
App. Delivery gates delay genuine reads or fail their transport; all delivered
business responses and all writes still use unchanged production handlers.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from ui_navigation import (activate_chat_feature, choose_chat_channel, open_channels,
    close_channels, edit_saved_document, saved_document_reading,
    reveal_discussion_work, reveal_message_actions, show_channel_introduction)

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(os.getenv('FLOW_FIRST_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/flow-first-six-20261024/flow-first-browser.json')))
spec = importlib.util.spec_from_file_location('flow_first_fixtures', Path(__file__).with_name('workspace-modes-browser.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.REPORT = REPORT
checks, observations, stage = {}, [], 'startup'
request_times = []


def api(*args, **kwargs):
    request_times.append(time.monotonic())
    return m.api(*args, **kwargs)


def pace(page):
    """Leave real API headroom; do not change installation request limits."""
    deadline = time.monotonic() + 70
    while sum(stamp > time.monotonic() - 60 for stamp in request_times) > 180:
        assert time.monotonic() < deadline, 'Private proof did not recover its normal API headroom'
        page.wait_for_timeout(1000)
    assert not any(row.get('status') == 429 for row in m.observations)


def mark(name):
    checks[name] = True
    print('PASS: ' + name, flush=True)


def snapshot(private, label, mode):
    hashes = m.source_hashes(private)
    name = 'frontend/tests/flow-first-browser.py'
    hashes[name] = hashlib.sha256((private / name).read_bytes()).hexdigest()
    m.snapshots[label] = hashes
    m.snapshots.pop(mode, None)


def bounded(page, node, minimum=0):
    expect(node).to_be_visible()
    box = node.bounding_box()
    assert box and box['x'] >= -1 and box['x'] + box['width'] <= page.viewport_size['width'] + 1
    assert box['height'] >= minimum
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    return box


def message_anchor(page):
    return page.locator('.fc-chat-timeline').evaluate('''node=>{
      const r=node.getBoundingClientRect(),m=[...node.querySelectorAll('.fc-chat-message')].find(m=>m.getBoundingClientRect().bottom>r.top+1);
      return {id:m?.id,top:node.scrollTop,offset:m?m.getBoundingClientRect().top-r.top:null};
    }''')


def same_message_anchor(page, before):
    page.wait_for_function('''before=>{const n=document.querySelector('.fc-chat-timeline'),r=n.getBoundingClientRect(),m=[...n.querySelectorAll('.fc-chat-message')].find(m=>m.getBoundingClientRect().bottom>r.top+1);return m?.id===before.id&&Math.abs(m.getBoundingClientRect().top-r.top-before.offset)<=2}''', arg=before)


class ActualCloudReadGate:
    """Hold actual GET requests, then continue unchanged production handlers."""
    def __init__(self, page, workspace, channel):
        self.page, self.routes = page, []
        self.pattern = '**/api/workspaces/' + workspace + '/channels/' + channel + '/cloud-files/**'
    def __enter__(self):
        def hold(route):
            if route.request.method == 'GET': self.routes.append(route)
            else: route.continue_()
        self.handler = hold
        self.page.route(self.pattern, hold)
        return self
    def release(self):
        assert len(self.routes) >= 2, 'Both genuine cloud reads must be delayed'
        while self.routes: self.routes.pop(0).continue_()
    def __exit__(self, *_failure):
        while self.routes:
            try: self.routes.pop(0).continue_()
            except Exception: pass
        self.page.unroute(self.pattern, self.handler)


class ActualHistoryReadGate:
    """Delay a channel's genuine history delivery, never construct its response."""
    def __init__(self, page, channel_id):
        self.page, self.routes, self.held = page, [], []
        self.path = '/api/messages/channels/' + channel_id
        self.pattern = '**/api/messages/channels/' + channel_id + '*'
        self.released = False
    def __enter__(self):
        def hold(route):
            if route.request.method == 'GET' and urlsplit(route.request.url).path == self.path and not self.released:
                self.routes.append(route)
                self.held.append(route.request.url)
            else:
                route.continue_()
        self.handler = hold
        self.page.route(self.pattern, hold)
        return self
    def release(self):
        assert self.routes, 'A genuine initial history GET must actually be pending'
        self.released = True
        while self.routes: self.routes.pop(0).continue_()
    def __exit__(self, *_failure):
        self.released = True
        while self.routes:
            try: self.routes.pop(0).continue_()
            except Exception: pass
        self.page.unroute(self.pattern, self.handler)


class ActualOlderReceiptWriteGate:
    """Hold one real preceding read write to witness serialized peer receipts."""
    def __init__(self, page, channel_id, message_id):
        self.page, self.routes, self.message_id = page, [], message_id
        self.pattern = '**/api/read-receipts/channels/' + channel_id
        self.released = False
    def __enter__(self):
        def hold(route):
            if route.request.method == 'POST' and route.request.post_data_json == {'message_id': self.message_id} and not self.released:
                self.routes.append(route)
            else:
                route.continue_()
        self.handler = hold
        self.page.route(self.pattern, hold)
        return self
    def release(self):
        assert len(self.routes) == 1, 'Exactly one actual preceding receipt write must be held'
        self.released = True
        self.routes.pop(0).continue_()
    def __exit__(self, *_failure):
        self.released = True
        while self.routes:
            try: self.routes.pop(0).continue_()
            except Exception: pass
        self.page.unroute(self.pattern, self.handler)


def receipt_id(base, channel, reader):
    rows = api(base, 'GET', '/read-receipts/channels/' + channel['id'], reader)
    return next((row['last_read_message_id'] for row in rows if row['user_id'] == reader['user_id']), None)


def wait_for_receipt(page, base, channel, reader, message_id):
    deadline = time.monotonic() + 15
    observed = receipt_id(base, channel, reader)
    while observed != message_id:
        if time.monotonic() >= deadline:
            observations.append({'actualReceiptTimeout': {'channel': channel['id'], 'expected': message_id, 'actual': observed,
                'browser': page.evaluate('''()=>{const n=document.querySelector('.fc-chat-timeline');return {
                  visibility:document.visibilityState,hasFocus:document.hasFocus(),modals:[...document.querySelectorAll('[aria-modal="true"]')].map(x=>({role:x.getAttribute('role'),label:x.getAttribute('aria-label'),hidden:x.hidden})),
                  scrollTop:n?.scrollTop,scrollHeight:n?.scrollHeight,clientHeight:n?.clientHeight,
                  lastMessage:n?.querySelector('.fc-chat-message:last-child')?.id,
                  entryNotice:document.querySelector('.fc-flow-entry-notice')?.textContent};}''')}})
            raise AssertionError({'expectedReceipt': message_id, 'actualReceipt': observed, 'channel': channel['id']})
        page.wait_for_timeout(250)
        observed = receipt_id(base, channel, reader)
    return observed


def at_latest(page):
    page.wait_for_function("()=>{const n=document.querySelector('.fc-chat-timeline');return n&&n.scrollHeight-n.clientHeight-n.scrollTop<=1}")


def create_history_channel(base, owner, reader, workspace, name, count=30):
    channel = api(base, 'POST', '/channels', owner, {'name': name, 'workspace_id': workspace['id'], 'member_ids': [reader['user_id']]})
    history = [api(base, 'POST', '/messages/channels/' + channel['id'], owner, {
        'content': name + ' — real peer update %02d. This ordinary message keeps enough readable history to distinguish the first unread entry from the latest.' % index}) for index in range(count)]
    return channel, history


def entry_network_races(browser):
    """Three real native network cases; no fabricated history, receipts or UI."""
    global stage
    stage = 'fresh native fixtures for actual entry receipt and history races'
    with m.fixture('native') as (base, app, private):
        request_times.clear()
        snapshot(private, 'native-entry-network-races', 'native')
        owner = m.register(base, 'Mira Holm', 'flow-entry-mira')
        reader = m.register(base, 'Sam Berg', 'flow-entry-sam')
        workspace = api(base, 'GET', '/workspaces', owner)[0]
        invite = api(base, 'POST', '/workspaces/' + workspace['id'] + '/invites', owner, {'role': 'member'})
        api(base, 'POST', '/workspaces/invites/accept', reader, {'token': invite['token']})
        known, history = create_history_channel(base, owner, reader, workspace, 'Following a genuinely read conversation')
        api(base, 'POST', '/read-receipts/channels/' + known['id'], reader, {'message_id': history[-1]['id']})
        context, page, errors, external, writes = m.browser_context(browser, app, reader, workspace)
        receipt_network = []
        def observe_entry_request(request):
            path = urlsplit(request.url).path
            if path.startswith('/api/'): request_times.append(time.monotonic())
            if path.startswith('/api/read-receipts/channels/'):
                receipt_network.append({'event': 'request', 'at': time.monotonic(), 'method': request.method, 'path': path, 'body': request.post_data_json if request.method == 'POST' else None})
        page.on('request', observe_entry_request)
        def observe_entry_response(response):
            path = urlsplit(response.url).path
            if path.startswith('/api/read-receipts/channels/'):
                receipt_network.append({'event': 'response', 'at': time.monotonic(), 'method': response.request.method, 'path': path, 'status': response.status, 'body': response.json()})
        page.on('response', observe_entry_response)
        observations.append({'actualEntryReceiptRequests': receipt_network})
        dialogs, _answers = m.install_dialog_answers(page)
        try:
            stage = 'known latest passive real peer arrival advances its actual read receipt'
            with ActualOlderReceiptWriteGate(page, known['id'], history[-1]['id']) as older_receipt:
                page.goto(app, wait_until='domcontentloaded')
                expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
                choose_chat_channel(page, known['name'])
                expect(page.locator('#message-' + history[-1]['id'])).to_be_in_viewport(ratio=.7)
                at_latest(page)
                page.wait_for_function("async()=>{const{getSocket}=await import('/src/lib/socket.ts');return getSocket().connected;}")
                deadline = time.monotonic() + 15
                while not older_receipt.routes:
                    assert time.monotonic() < deadline, 'The initial actual known-latest receipt write was not held'
                    page.wait_for_timeout(25)
                observations.append({'initialKnownLatestBrowser': page.evaluate('''()=>{const n=document.querySelector('.fc-chat-timeline');return {
                  visibility:document.visibilityState,hasFocus:document.hasFocus(),modals:[...document.querySelectorAll('[aria-modal="true"]')].map(x=>({role:x.getAttribute('role'),label:x.getAttribute('aria-label'),hidden:x.hidden})),
                  scrollTop:n?.scrollTop,scrollHeight:n?.scrollHeight,clientHeight:n?.clientHeight,
                  entryNotice:document.querySelector('.fc-flow-entry-notice')?.textContent};}''')})
                # No reader gesture follows entry. The latest peer must remain
                # queued while the preceding real read write is in flight.
                peer = api(base, 'POST', '/messages/channels/' + known['id'], owner, {'content': 'A new peer message while Sam passively follows the live conversation.'})
                expect(page.locator('#message-' + peer['id'])).to_be_in_viewport(ratio=.7)
                at_latest(page)
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                assert not any(row['event'] == 'request' and row['method'] == 'POST' and row['body'] == {'message_id': peer['id']} for row in receipt_network), 'A new receipt must not race the held preceding read write'
                assert receipt_id(base, known, reader) == history[-1]['id']
                with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/read-receipts/channels/' + known['id'] and response.request.post_data_json == {'message_id': peer['id']}) as peer_read:
                    older_receipt.release()
                assert peer_read.value.ok
                actual = wait_for_receipt(page, base, known, reader, peer['id'])
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                assert receipt_id(base, known, reader) == peer['id'], 'The completed preceding receipt must not regress the actual peer receipt'
                observations.append({'passivePeerEntry': {'channelId': known['id'], 'previousReceipt': history[-1]['id'], 'peerMessageId': peer['id'], 'actualReceipt': actual, 'precedingWriteHeldUntilPeerVisible': True}})
            mark('genuinely_read_latest_entry_passively_follows_a_real_peer_and_advances_the_actual_reader_receipt')
            pace(page)

            stage = 'failed real receipt transport stays unread until explicit jump'
            unavailable, unavailable_history = create_history_channel(base, owner, reader, workspace, 'Unavailable reading position stays honest')
            original_receipt = unavailable_history[4]['id']
            api(base, 'POST', '/read-receipts/channels/' + unavailable['id'], reader, {'message_id': original_receipt})
            receipt_path = '/api/read-receipts/channels/' + unavailable['id']
            pattern = '**' + receipt_path
            aborted = []
            def unavailable_transport(route):
                if route.request.method == 'GET':
                    aborted.append(route.request.url)
                    route.abort('failed')
                else:
                    route.continue_()
            page.route(pattern, unavailable_transport)
            try:
                # Native API creation does not broadcast a new drawer record.
                # Refresh genuinely after installing the failure gate, so this
                # target has never had a successful browser entry snapshot.
                page.reload(wait_until='domcontentloaded')
                choose_chat_channel(page, unavailable['name'])
                expect(page.locator('.fc-flow-entry-notice')).to_contain_text('Your reading position is unavailable. Loaded history is shown without marking everything read.')
                assert aborted, 'The genuine browser receipt GET must actually fail in transport'
                expect(page.locator('#message-' + unavailable_history[-1]['id'])).to_be_in_viewport(ratio=.7)
                assert receipt_id(base, unavailable, reader) == original_receipt
                since = len(writes)
                # Genuine generic pointer and textarea-resize events cannot
                # silently convert an unknown read position into read-all.
                page.locator('#message-' + unavailable_history[-1]['id'] + ' .fc-chat-message-body').click()
                composer = page.locator('.fc-chat-composer textarea')
                draft = '\n'.join('An unsent real thought, line ' + str(index) for index in range(6))
                composer.fill(draft)
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                assert receipt_id(base, unavailable, reader) == original_receipt
                assert not any(row['method'] == 'POST' and row['path'] == receipt_path for row in writes[since:])
                timeline = page.locator('.fc-chat-timeline')
                box = timeline.bounding_box()
                page.mouse.move(box['x'] + 60, box['y'] + 50)
                page.mouse.wheel(0, -350)
                jump = page.get_by_role('button', name='Jump to latest messages', exact=True)
                expect(jump).to_be_visible()
                assert receipt_id(base, unavailable, reader) == original_receipt
                jump.click()
                at_latest(page)
                actual = wait_for_receipt(page, base, unavailable, reader, unavailable_history[-1]['id'])
                expect(page.locator('.fc-flow-entry-notice')).to_have_count(0)
                expect(composer).to_have_value(draft)
                observations.append({'unavailableReceiptEntry': {'channelId': unavailable['id'], 'failedActualReads': len(aborted), 'receiptBeforeExplicitJump': original_receipt, 'receiptAfterExplicitJump': actual}})
            finally:
                page.unroute(pattern, unavailable_transport)
            mark('real_receipt_transport_failure_survives_generic_pointer_and_composer_resize_then_explicit_jump_advances_receipt')
            pace(page)

            stage = 'real own send wins over delayed initial history and its older unread plan'
            delayed, delayed_history = create_history_channel(base, owner, reader, workspace, 'A send while genuine initial history is delayed')
            api(base, 'POST', '/read-receipts/channels/' + delayed['id'], reader, {'message_id': delayed_history[4]['id']})
            with ActualHistoryReadGate(page, delayed['id']) as gate:
                page.reload(wait_until='domcontentloaded')
                choose_chat_channel(page, delayed['name'])
                deadline = time.monotonic() + 15
                while not gate.routes:
                    assert time.monotonic() < deadline, 'The initial native history GET was not intercepted'
                    page.wait_for_timeout(25)
                composer = page.locator('.fc-chat-composer textarea')
                expect(composer).to_be_editable()
                own_text = 'My explicit send takes precedence over the pending older unread plan ' + m.RUN
                composer.fill(own_text)
                with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/mobile/offline/channels/' + delayed['id'] + '/messages') as sent:
                    composer.press('Enter')
                assert sent.value.ok
                sent_payload = sent.value.json()
                saved = m.last_message(base, delayed['id'], reader, own_text)
                assert saved['author_id'] == reader['user_id']
                expect(composer).to_have_value('')
                gate.release()
                expect(page.locator('#message-' + saved['id'])).to_be_in_viewport(ratio=.7)
                at_latest(page)
                expect(page.locator('#message-' + delayed_history[5]['id'])).not_to_be_in_viewport()
                actual = wait_for_receipt(page, base, delayed, reader, saved['id'])
                observations.append({'ownSendDuringHeldInitialHistory': {'channelId': delayed['id'], 'heldHistoryReads': len(gate.held), 'previousReceipt': delayed_history[4]['id'], 'firstUnreadBeforeExplicitSend': delayed_history[5]['id'], 'ownMessageId': saved['id'], 'actualReceipt': actual, 'deliveryStatus': sent_payload.get('status')}})
            mark('actual_own_send_during_held_initial_history_wins_over_the_late_unread_plan_and_marks_only_the_explicit_latest_choice')
            assert not dialogs and not errors and not external, [dialogs, errors, external]
            assert not any(row.get('status') == 429 for row in m.observations)
        except Exception:
            m.capture_failure(page, 'flow-first-entry-races')
            raise
        finally:
            context.close()


def honest_ai(page, scope):
    card = scope.locator('.fc-contextual-copilot-card')
    expect(card).to_be_visible()
    expect(card).to_contain_text(re.compile(r'not enabled|not configured|disabled|must enable and connect|inte aktiver|inte konfigur|inaktiver|måste aktiver', re.I))
    expect(card.get_by_role('button', name=re.compile(r'^(Send to provider|Skicka till leverantören)$'))).to_have_count(0)
    card.get_by_role('button', name=re.compile(r'^(Close AI help|Stäng AI-hjälpen)$')).click()
    expect(card).to_have_count(0)


def native(browser):
    global stage
    stage = 'fresh native real history and account fixtures'
    with m.fixture('native') as (base, app, private):
        request_times.clear()
        snapshot(private, 'native-flow-first', 'native')
        owner = m.register(base, 'Lina Berg', 'flow-first-lina')
        reader = m.register(base, 'Noor Lind', 'flow-first-noor')
        workspace = api(base, 'GET', '/workspaces', owner)[0]
        invite = api(base, 'POST', '/workspaces/' + workspace['id'] + '/invites', owner, {'role': 'member'})
        api(base, 'POST', '/workspaces/invites/accept', reader, {'token': invite['token']})
        channel = api(base, 'POST', '/channels', owner, {'name': 'Product studio — materials and decisions', 'workspace_id': workspace['id'], 'member_ids': [reader['user_id']]})
        other = api(base, 'POST', '/channels', owner, {'name': 'Design notes — a separate conversation', 'workspace_id': workspace['id'], 'member_ids': [reader['user_id']]})
        history = [api(base, 'POST', '/messages/channels/' + channel['id'], owner,
            {'content': 'Studio update %02d. Keep the materials and the next small decision close to the conversation.' % index}) for index in range(65)]
        other_messages = [api(base, 'POST', '/messages/channels/' + other['id'], owner, {'content': 'Read design note ' + str(index)}) for index in range(3)]
        api(base, 'POST', '/read-receipts/channels/' + channel['id'], reader, {'message_id': history[7]['id']})
        api(base, 'POST', '/read-receipts/channels/' + other['id'], reader, {'message_id': other_messages[-1]['id']})
        context, page, errors, external, writes = m.browser_context(browser, app, reader, workspace)
        reads = []
        def observe_request(request):
            path = urlsplit(request.url).path
            if path.startswith('/api/'):
                request_times.append(time.monotonic())
                if request.method == 'GET': reads.append(path)
        page.on('request', observe_request)
        dialogs, _answers = m.install_dialog_answers(page)
        try:
            stage = 'cold first unread beyond first history page and actual remembered reading'
            page.goto(app, wait_until='domcontentloaded')
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            choose_chat_channel(page, channel['name'])
            expect(page.locator('#message-' + history[8]['id'])).to_be_in_viewport(ratio=.7)
            expect(page.locator('.fc-chat-message.is-lead')).to_have_count(0)
            expect(page.get_by_role('region', name='Channel introduction', exact=True)).to_have_count(0)
            receipt = next(row for row in api(base, 'GET', '/read-receipts/channels/' + channel['id'], reader) if row['user_id'] == reader['user_id'])
            assert receipt['last_read_message_id'] != history[-1]['id'], 'Entering unread history must not mark the entire channel read'
            timeline = page.locator('.fc-chat-timeline')
            box = timeline.bounding_box(); page.mouse.move(box['x'] + 60, box['y'] + 60); page.mouse.wheel(0, 360)
            page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            remembered = message_anchor(page)
            choose_chat_channel(page, other['name'])
            expect(page.locator('#message-' + other_messages[-1]['id'])).to_be_in_viewport(ratio=.7)
            page.wait_for_function("()=>{const n=document.querySelector('.fc-chat-timeline');return n.scrollHeight-n.clientHeight-n.scrollTop<=1}")
            choose_chat_channel(page, channel['name'])
            same_message_anchor(page, remembered)
            observations.append({'nativeEntry': {'firstUnread': history[8]['id'], 'receiptBefore': history[7]['id'], 'remembered': remembered, 'readChannelLatest': other_messages[-1]['id']}})
            m.screenshot(page, 'native-unread-and-remembered-desktop')
            mark('native_paged_first_unread_real_receipt_remembered_reading_and_genuinely_read_latest_entry')
            pace(page)

            stage = 'quiet composer native mentions files and late disabled-cloud focus'
            composer = page.locator('.fc-chat-composer textarea')
            expect(page.locator('.fc-chat-flow-actions')).to_have_count(0)
            draft = 'Please keep this real unsent thought while I choose what to do.'
            composer.fill(draft)
            since = len(writes)
            activate_chat_feature(page, 'task', keyboard=True)
            pane = m.workspace_pane(page, 'split')
            expect(composer).to_have_value(draft)
            m.close_work_area(page)
            expect(page.get_by_role('button', name='Show all features', exact=True)).to_be_focused()
            composer.fill(draft + ' ')
            m.choose_reference(page, composer, 'person', owner['user_id'], 'Lina', keyboard=True)
            rich_draft = composer.input_value()
            assert m.no_resource_writes(writes, since)
            page.get_by_role('button', name='Add or open shared files', exact=True).click()
            files = page.get_by_role('dialog', name='Channel files', exact=True)
            expect(files.get_by_label('Choose a file', exact=True)).to_be_visible()
            files.get_by_role('button', name='Close channel panel', exact=True).click()
            expect(composer).to_have_value(rich_draft)
            with ActualCloudReadGate(page, workspace['id'], channel['id']) as gate:
                activate_chat_feature(page, 'cloud', keyboard=True)
                deadline = time.monotonic() + 15
                while len(gate.routes) < 2:
                    assert time.monotonic() < deadline
                    page.wait_for_timeout(25)
                def delayed_error(panel):
                    gate.release()
                    expect(page.locator('.cloud-picker .cloud-error')).to_be_visible()
                    expect(panel.get_by_role('combobox')).to_be_focused()
                activate_chat_feature(page, 'external-meetings', keyboard=True, before_select=delayed_error)
            expect(m.workspace_pane(page, 'split').locator('[data-work-kind="external-meetings"]')).to_be_visible()
            m.close_work_area(page)
            expect(composer).to_have_value(rich_draft)
            assert m.no_resource_writes(writes, since)
            mark('quiet_native_composer_real_keyboard_features_mentions_files_and_late_cloud_error_keep_the_draft_and_focus')
            pace(page)

            stage = 'real saved page defaults read with explicit same-session editing'
            title = 'Studio handbook ' + m.RUN
            body = '<img src=x onerror=alert(1)> stays literal.\n\n' + '\n'.join('Shared note %02d — materials, people and decisions.' % index for index in range(50))
            m.command(page, composer, '/document "' + title + '"\n' + body)
            pane = m.workspace_pane(page, 'split')
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/channels/' + channel['id'] + '/shared-documents') as created:
                pane.get_by_role('button', name='Create document', exact=True).click()
            assert created.value.ok; document = created.value.json()
            editor = pane.get_by_label('Document text', exact=True); expect(editor).to_have_value(body)
            expect(editor).to_be_editable()
            node = editor.element_handle()
            pane.get_by_role('button', name='Done editing', exact=True).click()
            reader_page = saved_document_reading(pane, title=title, text=body)
            reader_node = reader_page.element_handle()
            expect(reader_page.locator('img,script,iframe')).to_have_count(0)
            since = len(writes)
            m.expand_work(page); m.minimize_work(page); m.continue_work(page, 'expanded', work=pane)
            expect(reader_page).to_be_focused()
            m.restore_work(page); m.minimize_work(page); m.continue_work(page, 'split', work=pane)
            expect(reader_page).to_be_focused()
            assert reader_page.evaluate('(current,previous)=>current===previous&&previous.isConnected', reader_node)
            assert editor.evaluate('(current,previous)=>current===previous&&previous.isConnected', node)
            assert m.no_resource_writes(writes, since)
            edit_saved_document(pane, title=title, text=body)
            final_body = body + '\nA deliberate synchronized edit.'
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path.endswith('/shared-documents/' + document['id'] + '/updates')) as updated:
                editor.fill(final_body)
            assert updated.value.ok
            expect(pane.locator('.fc-shared-sync')).to_have_text('All edits synchronized')
            pane.get_by_role('button', name='Done editing', exact=True).click()
            saved_document_reading(pane, title=title, text=final_body)
            assert reader_page.evaluate('(current,previous)=>current===previous&&previous.isConnected', reader_node)
            pane.get_by_label('Add a comment', exact=True).fill('A real comment written while reading.')
            pane.get_by_role('button', name='Post comment', exact=True).click()
            expect(pane.get_by_text('A real comment written while reading.', exact=True)).to_be_visible()
            expect(pane.get_by_label('Add a comment', exact=True)).to_have_value('')
            expect(pane.get_by_role('button', name='Resolve comment', exact=True)).to_be_enabled()
            m.close_work_area(page)
            activate_chat_feature(page, 'document', keyboard=True)
            pane = m.workspace_pane(page, 'split')
            pane.locator('.fc-shared-document-card[data-document-id=' + json.dumps(document['id']) + ']').first.click()
            saved_document_reading(pane, title=title, text=final_body)
            m.screenshot(page, 'native-canonical-document-read')
            m.close_work_area(page)
            mark('saved_native_document_read_edit_done_comments_safe_text_same_dom_read_focus_and_real_yjs_persistence')
            pace(page)

            stage = 'canonical work on an unopened reply aggregates at its actual discussion'
            parent = history[-4]
            reply = api(base, 'POST', '/threads/messages/' + parent['id'] + '/reply', owner, {'content': 'A real reply with shared work.'})
            project = api(base, 'POST', '/channels/' + channel['id'] + '/projects', owner, {'id': 'flow-first-project-' + m.RUN, 'expected_revision': 0, 'archived': False, 'fields': {'title': 'Studio next steps', 'description': '', 'color': 'sage'}})
            task = api(base, 'POST', '/channels/' + channel['id'] + '/projects/' + project['id'] + '/tasks', owner, {'id': 'flow-first-task-' + m.RUN, 'expected_revision': 0, 'archived': False, 'fields': {'title': 'Review the materials', 'description': '', 'status': 'todo', 'priority': 'normal', 'assignee_user_id': None, 'due_date': None, 'checklist': [], 'file_ids': [], 'decision_ids': []}})
            decision = api(base, 'POST', '/channels/' + channel['id'] + '/decisions', owner, {'title': 'Keep the shared doorway open', 'content': 'A canonical reply decision.', 'status': 'accepted', 'source_message_id': reply['id']})
            references = []
            for source, kind, resource in [(parent['id'], 'document', document['id']), (reply['id'], 'document', document['id']), (reply['id'], 'task', task['id'])]:
                references.append(api(base, 'POST', '/channels/' + channel['id'] + '/conversation-work', owner, {'source_message_id': source, 'resource_kind': kind, 'resource_id': resource}))
            assert all(row['discussion_message_id'] == parent['id'] for row in references)
            assert decision['discussion_message_id'] == parent['id']
            reads_before = len(reads)
            page.reload(wait_until='domcontentloaded')
            expect(page.locator('.fc-chat')).to_be_visible()
            choose_chat_channel(page, channel['name'])
            root = page.locator('#message-' + parent['id'])
            summary = root.locator('.fc-discussion-work')
            for kind in ('document', 'task', 'decision'):
                expect(summary.locator('[data-discussion-work-kind=' + kind + ']')).to_contain_text('1 ')
            assert not any('/threads/messages/' in path for path in reads[reads_before:]), 'Unopened reply work must not require fetching every thread'
            expect(summary.get_by_role('button', name='Work in this discussion', exact=True)).to_have_attribute('aria-expanded', 'false')
            root.locator('.fc-chat-thread-link').click()
            thread = page.get_by_role('dialog', name='Thread', exact=True)
            expect(thread.locator('[data-thread-reply=' + json.dumps(reply['id']) + ']')).to_be_visible()
            for kind in ('document', 'task', 'decision'):
                expect(thread.locator('.fc-discussion-work [data-discussion-work-kind=' + kind + ']')).to_contain_text('1 ')
            thread_draft = 'An unsent reply stays in its real discussion.'
            thread.get_by_label('Reply in thread', exact=True).fill(thread_draft)
            since = len(writes)
            reveal_discussion_work(thread)
            doc_reference = next(row for row in references if row['resource_kind'] == 'document' and row['source_message_id'] == parent['id'])
            candidate = thread.locator('[data-work-reference=' + json.dumps(doc_reference['id']) + ']')
            if candidate.count() == 0:
                doc_reference = next(row for row in references if row['resource_kind'] == 'document' and row['source_message_id'] == reply['id'])
                candidate = thread.locator('[data-work-reference=' + json.dumps(doc_reference['id']) + ']')
            expect(thread.locator('[data-work-reference]')).to_have_count(3)
            candidate.click()
            pane = m.workspace_pane(page, 'split')
            saved_document_reading(pane, title=title, text=final_body)
            m.close_work_area(page)
            thread = page.get_by_role('dialog', name='Thread', exact=True)
            expect(thread).to_be_visible()
            expect(thread.locator('[data-thread-reply=' + json.dumps(reply['id']) + ']')).to_be_visible()
            expect(thread.get_by_label('Reply in thread', exact=True)).to_have_value(thread_draft)
            page.keyboard.press('Escape')
            assert m.no_resource_writes(writes, since)
            observations.append({'discussion': {'root': parent['id'], 'reply': reply['id'], 'resourceIds': [document['id'], task['id'], decision['id']], 'counts': {'document': 1, 'task': 1, 'decision': 1}}})
            mark('real_unopened_reply_work_counts_deduplicate_canonical_resources_and_open_the_same_page_without_losing_thread_draft')
            pace(page)

            stage = 'concrete contextual ai actions remain explicit honest and within current scope'
            since = len(writes)
            reveal_message_actions(root)
            root.get_by_role('button', name='Draft a reply', exact=True).click()
            honest_ai(page, root)
            root.locator('.fc-chat-thread-link').click()
            thread = page.get_by_role('dialog', name='Thread', exact=True)
            thread.get_by_role('button', name='Summarize replies', exact=True).click()
            honest_ai(page, thread)
            page.keyboard.press('Escape')
            composer.fill('An unsent draft that needs a little care.')
            original_draft = composer.input_value()
            page.get_by_role('button', name='Message options', exact=True).click()
            page.get_by_role('menuitem', name='Tidy my draft', exact=True).click()
            honest_ai(page, page.locator('.fc-chat-composer-area'))
            expect(composer).to_have_value(original_draft)
            activate_chat_feature(page, 'document')
            pane = m.workspace_pane(page, 'split')
            pane.locator('.fc-shared-document-card[data-document-id=' + json.dumps(document['id']) + ']').first.click()
            saved_document_reading(pane, title=title, text=final_body)
            pane.get_by_role('button', name=re.compile(r'^Summarize document for ')).click()
            honest_ai(page, pane)
            m.close_work_area(page)
            assert m.no_resource_writes(writes, since)
            assert not any(row['method'] == 'POST' and '/copilot/' in row['path'] for row in writes[since:])
            mark('contextual_message_thread_draft_and_reading_ai_actions_are_concrete_honest_and_never_call_a_provider_or_write_automatically')
            pace(page)

            stage = 'saved preferences and clear actual channel navigation at390 and320'
            prefs = {'version': 1, 'mode': 'dark', 'accent_color': '#eddccf', 'background_color': '#f1f4f5', 'dark_background_color': '#121a19', 'font_family': 'serif', 'font_size': 'large', 'density': 'comfortable', 'corner_style': 'rounded'}
            api(base, 'PUT', '/preferences/appearance', reader, prefs)
            page.reload(wait_until='domcontentloaded')
            expect(page.locator('.fc-chat')).to_be_visible()
            expect(page.locator('html')).to_have_attribute('data-mode', 'dark')
            expect(page.locator('html')).to_have_attribute('data-font', 'serif')
            page.locator('.fc-chat-account-trigger').click()
            account = page.locator('.fc-account-panel')
            with page.expect_response(lambda response: response.request.method == 'PUT' and urlsplit(response.url).path == '/api/preferences/language') as language:
                account.get_by_role('combobox', name='Language', exact=True).select_option('sv')
            assert language.value.ok
            page.keyboard.press('Escape')
            expect(page.locator('html')).to_have_attribute('lang', 'sv')
            for width in (390, 320):
                page.set_viewport_size({'width': width, 'height': 850})
                switch = page.get_by_role('button', name='Byt kanal', exact=True)
                bounded(page, switch, 44)
                channel_title = page.locator('.fc-chat-topbar-channel strong')
                expect(channel_title).to_have_text(channel['name'])
                title_geometry = channel_title.evaluate('''node=>{
                  const range=document.createRange();range.selectNodeContents(node);
                  const box=node.getBoundingClientRect(),style=getComputedStyle(node);
                  return {lines:[...range.getClientRects()].map(r=>({top:r.top,bottom:r.bottom,left:r.left,right:r.right})),
                    box:{left:box.left,right:box.right,top:box.top,bottom:box.bottom},
                    clientHeight:node.clientHeight,scrollHeight:node.scrollHeight,whiteSpace:style.whiteSpace,textOverflow:style.textOverflow};
                }''')
                assert title_geometry['whiteSpace'] == 'normal' and title_geometry['textOverflow'] == 'clip', title_geometry
                assert len({round(row['top'], 1) for row in title_geometry['lines']}) >= 2, title_geometry
                assert title_geometry['scrollHeight'] <= title_geometry['clientHeight'] + 1, title_geometry
                assert all(row['left'] >= title_geometry['box']['left'] - 1 and row['right'] <= title_geometry['box']['right'] + 1 and row['bottom'] <= title_geometry['box']['bottom'] + 1 for row in title_geometry['lines']), title_geometry
                observations.append({'actualMobileLongChannelTitle': {'width': width, 'name': channel['name'], **title_geometry}})
                switch.focus(); switch.press('Enter')
                drawer = page.get_by_role('dialog', name='Arbetsytans kanaler', exact=True)
                expect(drawer).to_be_visible()
                for name in (channel['name'], other['name']):
                    expect(drawer.get_by_role('navigation', name='Kanaler', exact=True).get_by_role('button').filter(has=page.get_by_text(name, exact=True))).to_be_visible()
                for _ in range(4):
                    page.keyboard.press('Tab')
                    assert drawer.evaluate('node=>node.contains(document.activeElement)')
                page.keyboard.press('Escape'); expect(drawer).to_have_count(0); expect(switch).to_be_focused()
                introduction = show_channel_introduction(page)
                expect(introduction.get_by_role('heading', level=1)).to_have_text(channel['name'])
                bounded(page, introduction)
                introduction.get_by_role('button', name='Stäng kanalintroduktionen', exact=True).click()
                expect(introduction).to_have_count(0)
                composer.fill('Ett naturligt flöde medan jag väljer en funktion. ')
                draft_before = composer.input_value()
                activate_chat_feature(page, 'task', keyboard=True)
                m.close_work_area(page)
                expect(page.get_by_role('button', name='Visa alla funktioner', exact=True)).to_be_focused()
                expect(composer).to_have_value(draft_before)
                m.choose_reference(page, composer, 'person', owner['user_id'], 'Lina', keyboard=True)
                bounded(page, page.locator('.fc-chat-composer'))
                m.screenshot(page, 'native-swedish-dark-' + str(width))
            api(base, 'DELETE', '/channels/' + channel['id'] + '/members/' + reader['user_id'], owner)
            expect(page.locator('#message-' + parent['id'])).to_have_count(0)
            expect(page.locator('.fc-discussion-work')).to_have_count(0)
            expect(page.locator('.fc-shared-reading-page')).to_have_count(0)
            try:
                api(base, 'GET', '/channels/' + channel['id'] + '/conversation-work', reader)
                raise AssertionError('Revoked member could still read the work index')
            except HTTPError as failure:
                assert failure.code in (403, 404)
            assert not dialogs
            mark('clear_channel_switch_menu_optional_intro_saved_theme_locale_mobile_bounds_and_authoritative_scope_revocation')
            assert not errors and not external, [errors, external]
            assert not any(row.get('status') == 429 for row in m.observations)
        except Exception:
            observations.append({'nativeDialogs': dialogs})
            m.capture_failure(page, 'flow-first-native')
            raise
        finally:
            context.close()


def demo(browser):
    global stage
    stage = 'actual demo uses the same quiet conversation reading and contextual actions'
    with m.fixture('demo') as (base, app, private):
        request_times.clear()
        snapshot(private, 'actual-demo-flow-first', 'demo')
        context, page, errors, external, writes = m.browser_context(browser, app, viewport={'width': 320, 'height': 850})
        visit = None
        try:
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/demo/session') as session:
                page.goto(app + '/product-demo/?locale=sv', wait_until='domcontentloaded')
            assert session.value.ok; visit = session.value.json()
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            expect(page.locator('html')).to_have_attribute('lang', 'sv')
            expect(page.locator('.fc-chat-flow-actions,.fc-chat-message.is-lead')).to_have_count(0)
            expect(page.get_by_role('region', name='Kanalintroduktion', exact=True)).to_have_count(0)
            source = page.locator('#message-' + visit['examples']['message_id'])
            expect(source).to_be_in_viewport(ratio=.7)
            composer = page.locator('.fc-chat-composer textarea')
            draft = 'Ett verkligt oskickat utkast i exempelmiljön.'
            composer.fill(draft)
            since = len(writes)
            activate_chat_feature(page, 'document', keyboard=True)
            pane = m.workspace_pane(page, 'split')
            document_id = visit['examples']['document_ids'][0]
            pane.locator('.fc-shared-document-card[data-document-id=' + json.dumps(document_id) + ']').first.click()
            reader_page = saved_document_reading(pane)
            reader_node = reader_page.element_handle()
            before = reader_page.locator('.fc-shared-reading-body').text_content()
            editor = edit_saved_document(pane, text=before)
            final = before + '\nEn uttrycklig ändring i den riktiga exempelprodukten.'
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path.endswith('/shared-documents/' + document_id + '/updates')) as saved:
                editor.fill(final)
            assert saved.value.ok
            expect(pane.locator('.fc-shared-sync')).to_have_text('Alla ändringar är synkroniserade')
            pane.get_by_role('button', name='Klar med redigering', exact=True).click()
            saved_document_reading(pane, text=final)
            m.expand_work(page); m.minimize_work(page); m.continue_work(page, 'expanded', work=pane)
            expect(reader_page).to_be_focused()
            assert reader_page.evaluate('(current,previous)=>current===previous&&previous.isConnected', reader_node)
            m.restore_work(page); m.close_work_area(page)
            expect(composer).to_have_value(draft)
            expect(source.locator('.fc-discussion-work [data-discussion-work-kind=document]')).to_contain_text('1 ')
            reveal_discussion_work(source)
            expect(source.locator('[data-work-reference]')).to_have_count(1)
            source.locator('[data-work-reference]').click()
            pane = m.workspace_pane(page, 'split')
            saved_document_reading(pane, text=final)
            m.close_work_area(page)
            show_channel_introduction(page)
            page.get_by_role('button', name='Stäng kanalintroduktionen', exact=True).click()
            reveal_message_actions(source)
            source.get_by_role('button', name='Skriv ett svarsutkast', exact=True).click()
            honest_ai(page, source)
            bounded(page, page.locator('.fc-chat-composer'))
            m.screenshot(page, 'actual-demo-swedish-320')
            assert not any(row['method'] == 'POST' and '/copilot/' in row['path'] for row in writes[since:])
            assert not errors and not external
            mark('actual_swedish320_demo_shares_normal_entry_quiet_composer_explicit_read_edit_modes_discussion_work_channel_menu_and_honest_ai')
        except Exception:
            m.capture_failure(page, 'flow-first-demo')
            raise
        finally:
            if visit:
                api(base, 'DELETE', '/demo/session', visit['session'])
                observations.append({'ownedDemoVisitRevoked': 204})
            context.close()


if __name__ == '__main__':
    complete = False
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or '/usr/bin/chromium', headless=True, args=['--no-sandbox'])
            try:
                native(browser)
                entry_network_races(browser)
                demo(browser)
                complete = True
            finally:
                browser.close()
    finally:
        mismatches = {mode: [name for name, digest in hashes.items() if not (ROOT / name).is_file() or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest] for mode, hashes in m.snapshots.items()}
        REPORT.write_text(json.dumps({'ok': complete and not any(mismatches.values()), 'scope': __doc__, 'stage': stage, 'checks': checks,
            'source_sha256': m.snapshots, 'mismatchedCurrentSources': mismatches, 'observations': observations + m.observations,
            'screenshots': m.screenshots, 'cleanup': 'Only owned loopback browser/API/Vite fixtures, SQLite, files, session keys and temporary source copies were used and removed. No customer/provider/Matrix acceptance is inferred.'}, indent=2) + '\n')
    assert complete and not any(mismatches.values())
    print(json.dumps({'ok': True, 'checks': checks, 'sourceCounts': {mode: len(hashes) for mode, hashes in m.snapshots.items()}, 'mismatches': mismatches}), flush=True)
