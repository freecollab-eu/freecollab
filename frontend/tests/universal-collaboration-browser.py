#!/usr/bin/env python3
"""Actual native/demo proof for universal writing and retained collaboration.

Uses disposable loopback installations and genuine product UI/API/Yjs data.
The existing private fixture is imported without modifying its runtime or tests.
No public deployment, provider request, mocked business response or quota change.
"""
from pathlib import Path
from contextlib import contextmanager
import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
REPORT = Path(os.environ.get('UNIVERSAL_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/universal-collaboration-20261024/current.json')))
RUN = secrets.token_hex(5)
spec = importlib.util.spec_from_file_location('universal_actual_fixture', HERE.with_name('workspace-modes-browser.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
fixture.REPORT = REPORT
api = fixture.api
checks, evidence = {}, []
stage = 'startup'
SKIP_INVENTORY_EXECUTION = False
DOCUMENT_CLIENT_URLS = {}
PRIMARY = '.fc-conversation-workspace:not(.fc-collaboration-work-surface):has(.fc-conversation-work, .fc-reference-groups, .fc-collaboration-panel)'


def mark(name, **details):
    checks[name] = True
    if details:
        evidence.append({'check': name, **details})
    print('PASS: ' + name, flush=True)


def set_stage(value):
    global stage
    stage = value
    fixture.stage = value
    print('STAGE: ' + value, flush=True)


def hashes(root):
    # The copied fixture intentionally excludes generated Python bytecode.
    # Compare the same source domain while other independent checks compile it.
    result = {key: value for key, value in fixture.source_hashes(root).items()
              if '__pycache__' not in Path(key).parts and not key.endswith('.pyc')}
    for filename in ('tests/universal-collaboration-browser.py',):
        path = root / 'frontend' / filename
        result['frontend/' + filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def bind_source(mode, private):
    copied, current = hashes(private), hashes(ROOT)
    evidence.append({'sourceBinding': mode, 'copiedSourceHashes': copied, 'checkoutMatchedAtStart': current == copied,
                     'checkoutDriftAtStart': sorted(key for key in set(current) | set(copied) if current.get(key) != copied.get(key))})
    return copied


def finish_source_binding(mode, private, before):
    assert hashes(private) == before, 'Immutable copied runtime changed during the browser proof'
    current = hashes(ROOT)
    evidence.append({'sourceBindingFinished': mode, 'copiedSnapshotStable': True, 'checkoutMatchedAtFinish': current == before,
                     'checkoutDriftAtFinish': sorted(key for key in set(current) | set(before) if current.get(key) != before.get(key))})


def wait_until(fn, timeout=35, message='Expected actual product state'):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(.15)
    raise AssertionError(message)


def wrapper(field):
    return field.locator('xpath=ancestor::*[contains(concat(" ",normalize-space(@class)," ")," fc-collaboration-input ")][1]')


def card_for(field):
    return field.locator('xpath=ancestor::*[@data-collaboration-work-id][1]')


def cards(page):
    return page.locator('[data-collaboration-work-id]:not([data-collaboration-work-id="primary"]), ' + PRIMARY)


def card_ids(page):
    return cards(page).evaluate_all('(nodes)=>nodes.map(node=>node.dataset.collaborationWorkId || "primary")')


def latest_card(page, before):
    wait_until(lambda: set(card_ids(page)) - set(before), message='Command must open its actual independent work card')
    identifier = next(item for item in card_ids(page) if item not in before)
    return page.locator(PRIMARY if identifier == 'primary' else '[data-collaboration-work-id=' + json.dumps(identifier) + ']')


def scoped_option(popup, kind, identifier):
    list_id = popup.get_by_role('listbox').get_attribute('id')
    return popup.locator('[role="option"][id=' + json.dumps(list_id + '-' + kind + '-' + quote(identifier, safe="~()*!.'-_")) + ']')


def choose_reference(field, kind, identifier, query, *, keyboard=False, touch=False):
    field.press_sequentially('@' + query)
    popup = wrapper(field).locator('.fc-mention-references')
    expect(popup).to_be_visible()
    target = scoped_option(popup, kind, identifier)
    expect(target).to_be_visible()
    if keyboard:
        wanted = target.get_attribute('id')
        for _ in range(popup.get_by_role('option').count() + 1):
            if field.get_attribute('aria-activedescendant') == wanted:
                break
            field.press('ArrowDown')
        assert field.get_attribute('aria-activedescendant') == wanted
        field.press('Enter')
    elif touch:
        target.tap()
    else:
        target.click(timeout=5000)
    expect(popup).to_have_count(0)
    expect(field).to_be_focused()


def browse(field):
    source = wrapper(field)
    source.get_by_role('button', name='Browse collaboration features', exact=True).click()
    popup = source.locator('.fc-slash-commands')
    try:
        expect(popup).to_be_visible()
    except Exception:
        evidence.append({'browseFailed': field.evaluate('''field=>({
          field:{tag:field.tagName,id:field.id,readOnly:field.readOnly,disabled:field.disabled,value:field.value},
          active:document.activeElement?{tag:document.activeElement.tagName,id:document.activeElement.id,label:document.activeElement.getAttribute('aria-label')}:null,
          card:field.closest('[data-collaboration-work-id]')?.dataset,
          sourceMarkup:field.closest('.fc-collaboration-input')?.outerHTML.slice(0,5000),
          openPopups:[...document.querySelectorAll('.fc-slash-commands,.fc-mention-references')].map(node=>({id:node.id,text:node.textContent?.slice(0,300)}))
        })''')})
        raise
    return popup


def choose_command(field, command_id, *, keyboard=False, touch=False):
    popup = wrapper(field).locator('.fc-slash-commands')
    expect(popup).to_be_visible()
    list_id = popup.get_by_role('listbox').get_attribute('id')
    target = popup.locator('[role="option"][id=' + json.dumps(list_id + '-' + command_id) + ']')
    expect(target).to_be_visible()
    if keyboard:
        field.focus()
        field.press('Home')
        wanted = target.get_attribute('id')
        for _ in range(popup.get_by_role('option').count() + 1):
            if field.get_attribute('aria-activedescendant') == wanted:
                break
            field.press('ArrowDown')
        assert field.get_attribute('aria-activedescendant') == wanted
        assert_bounds(field.page, target)
        field.press('Enter')
    elif touch:
        target.tap()
    else:
        target.click(timeout=5000)
    expect(popup).to_have_count(0)


def typed(field, value, command_id=None, *, touch=False):
    field.fill(value)
    popup = wrapper(field).locator('.fc-slash-commands')
    expect(popup).to_be_visible()
    if command_id:
        choose_command(field, command_id, touch=touch)
    else:
        field.press('Enter')
        expect(popup).to_have_count(0)


def main_command(page, value):
    field = page.locator('.fc-chat-composer textarea')
    fixture.command(page, field, value)


def close_card(card):
    card.locator('[data-work-close]').click()
    expect(card).to_have_count(0)


def live_handle(field):
    return field.element_handle()


def same_node(field, previous, value):
    expect(field).to_have_value(value)
    assert field.evaluate('(current,previous)=>current===previous', previous), 'Opening another function remounted a retained source editor'


def assert_bounds(page, target):
    box = target.bounding_box()
    assert box and box['width'] > 0 and box['height'] > 0
    viewport = page.viewport_size
    assert box['x'] >= -.5 and box['y'] >= -.5 and box['x'] + box['width'] <= viewport['width'] + .5 and box['y'] + box['height'] <= viewport['height'] + .5, (box, viewport)


def canonical(page, channel, document):
    parsed = urlsplit(page.url)
    module = DOCUMENT_CLIENT_URLS.get(parsed.scheme + '://' + parsed.netloc, '/src/lib/shared-documents-api.ts')
    return page.evaluate('''async ({channel,document,module})=>{
      const {readSharedDocumentForSearch}=await import(module);
      return await readSharedDocumentForSearch(channel,document);
    }''', {'channel': channel, 'document': document, 'module': module})


def reference_bindings(value, refs, expected):
    assert [(r['kind'], r['id']) for r in refs] == expected, refs
    prior = 0
    for ref in refs:
        assert ref['start'] >= prior and ref['end'] > ref['start']
        assert fixture.utf16_slice(value, ref['start'], ref['end']).startswith('@'), (value, ref)
        prior = ref['end']


def upload(base, channel, actor):
    boundary = 'private-universal-' + secrets.token_hex(12)
    filename = 'Universal-material-' + RUN + '.txt'
    body = ('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="' + filename + '"\r\nContent-Type: text/plain\r\n\r\nActual universal reference material\r\n--' + boundary + '--\r\n').encode()
    request = Request(base + '/files/channels/' + channel, method='POST', data=body, headers={'Authorization': 'Bearer ' + actor['access_token'], 'Content-Type': 'multipart/form-data; boundary=' + boundary})
    with urlopen(request, timeout=45) as response:
        return json.load(response)


def seed_document(base, channel, actor, title, text):
    # A genuine Yjs update, accepted through the normal production create API.
    code = "import * as Y from './node_modules/yjs/dist/yjs.mjs';let d=new Y.Doc();d.getText('title').insert(0,process.argv[1]);d.getText('body').insert(0,process.argv[2]);process.stdout.write(Buffer.from(Y.encodeStateAsUpdate(d)).toString('base64'));"
    update = subprocess.check_output(['node', '--input-type=module', '-e', code, title, text], cwd=ROOT / 'frontend', text=True)
    return api(base, 'POST', '/channels/' + channel + '/shared-documents', actor, {'id': 'universal-doc-' + secrets.token_hex(8), 'update_id': 'universal-initial-' + secrets.token_hex(8), 'update_base64': update, 'title': title})


def setup_native(base):
    owner = fixture.register(base, 'Universal owner', 'universal-owner')
    peer = fixture.register(base, 'Maja Universal', 'universal-maja')
    workspace = api(base, 'GET', '/workspaces', owner)[0]
    invitation = api(base, 'POST', '/workspaces/' + workspace['id'] + '/invites', owner, {'role': 'member'})
    api(base, 'POST', '/workspaces/invites/accept', peer, {'token': invitation['token']})
    channel = api(base, 'POST', '/channels', owner, {'name': 'Universal studio', 'workspace_id': workspace['id'], 'member_ids': [peer['user_id']]})
    other = api(base, 'POST', '/channels', owner, {'name': 'Universal courtyard', 'workspace_id': workspace['id'], 'member_ids': [peer['user_id']]})
    project = api(base, 'POST', '/channels/' + channel['id'] + '/projects', owner, {'id': 'universal-project-' + RUN, 'expected_revision': 0, 'archived': False, 'fields': {'title': 'Universal project', 'description': 'Real task and reference context', 'color': 'sage'}})
    task = api(base, 'POST', '/channels/' + channel['id'] + '/projects/' + project['id'] + '/tasks', owner, {'id': 'universal-target-' + RUN, 'expected_revision': 0, 'archived': False, 'fields': {'title': 'Universal target task', 'description': 'Actual linked task', 'status': 'todo', 'priority': 'normal', 'assignee_user_id': None, 'due_date': None, 'checklist': [], 'file_ids': [], 'decision_ids': []}})
    group = api(base, 'POST', '/mentions/channels/' + channel['id'] + '/groups', owner, {'name': 'Universal group', 'member_ids': [peer['user_id']]})
    document = seed_document(base, channel['id'], owner, 'Universal linked page', 'The second live page stays independent.')
    material = upload(base, channel['id'], owner)
    post = api(base, 'POST', '/channels/' + channel['id'] + '/activity-feed', owner, {'id': 'universal-post-' + RUN, 'expected_revision': 0, 'archived': False, 'fields': {'title': 'Universal activity discussion', 'body': 'A real discussion while other work stays open.', 'poll': None}})
    message = api(base, 'POST', '/messages/channels/' + channel['id'], owner, {'content': 'Actual universal collaboration context ' + RUN})
    return {'actor': owner, 'peer': peer, 'workspace': workspace, 'channel': channel, 'other': other, 'project': project, 'task': task, 'group': group, 'document': document, 'file': material, 'post': post, 'message': message}


INLINE = {'document': 'documents', 'task': 'tasks', 'decision': 'decisions', 'groups': 'groups', 'meetings': 'meetings', 'activity': 'feed', 'workflows': 'workflows', 'cloud': 'cloud', 'external-meetings': 'external-meetings', 'apps': 'apps', 'copilot': 'copilot', 'data': 'data', 'shared-work': 'shared-work', 'pins': 'pins', 'bookmarks': 'bookmarks', 'notification-settings': 'notifications'}
DIALOGS = {
    'files': ('.fc-channel-layer', 'Close channel panel'),
    'members': ('.fc-channel-layer', 'Close channel panel'),
    'channel': ('.fc-channel-metadata', 'Close channel details'),
    'search': ('.fc-history-search', 'Close history search'),
    'mobile': ('[role="dialog"][aria-label="Mobile and offline"]', 'Close mobile settings'),
    'schedule': ('.fc-productivity-panel', 'Close scheduling'),
    'appearance': ('.appearance-panel', 'Close appearance settings'),
    'account': ('[role="dialog"][aria-label="Account settings"]', 'Close account settings'),
    'my-day': ('.fc-day', 'Close my day'),
    'integrations': ('.fc-integrations-panel', 'Close integrations'),
    'direct-message': ('.fc-dm-panel', 'Close direct messages'),
    'new-channel': ('.fc-chat-create-dialog', 'Cancel'),
    'channels': ('#chat-channel-drawer', 'Close channels'),
    'secure-settings': ('.fc-secure-panel', 'Close encryption settings'),
    'status': ('[role="dialog"][aria-label="Set your status"]', 'Close status settings'),
    'notifications': ('.fc-notifications', 'Close notifications'),
    'get-started': ('.fc-launch-panel', 'Close workspace launch guide'),
    'feedback': ('.fc-workspace-feedback-dialog', 'Close workspace feedback'),
}


def inventory(field):
    popup = browse(field)
    list_id = popup.get_by_role('listbox').get_attribute('id')
    result = [identifier[len(list_id) + 1:] for identifier in popup.get_by_role('option').evaluate_all('(nodes)=>nodes.map(node=>node.id)')]
    field.press('Escape')
    expect(popup).to_have_count(0)
    return result


def no_target_writes(writes, since, source_document=None):
    allowed = '/shared-documents/' + source_document + '/updates' if source_document else None
    forbidden = []
    for row in writes[since:]:
        path = row['path']
        if allowed and path.endswith(allowed):
            continue
        if row['method'] in ('POST', 'PUT', 'PATCH', 'DELETE') and any(part in path for part in ('/messages/channels/', '/mobile/offline/channels/', '/threads/messages/', '/shared-documents', '/projects', '/decisions', '/groups', '/activity-feed', '/copilot/', '/invoke', '/scheduled', '/reminders', '/files/', '/file-experience/', '/calendar', '/integrations/', '/clips', '/workflows')):
            forbidden.append(row)
    assert not forbidden, 'Opening a function published or changed a resource: ' + json.dumps(forbidden)


def exercise_inventory(page, source, writes, name, *, ids=None, source_document=None):
    source_text = source.input_value()
    source_node = live_handle(source)
    available = inventory(source)
    assert len(available) >= 29 and len(available) == len(set(available)), available
    if SKIP_INVENTORY_EXECUTION:
        mark(name + '_inventory_visible_only', inventory=available)
        return available
    exercised = []
    for command_id in ids or available:
        if command_id == 'admin':
            # System administration is outside this ordinary workspace fixture.
            raise AssertionError('Unexpected installation-admin authority in a workspace actor')
        set_stage(name + ' opens real /' + command_id)
        before = card_ids(page)
        since = len(writes)
        browse(source)
        choose_command(source, command_id)
        if command_id in INLINE:
            target = latest_card(page, before)
            expect(target).to_be_visible()
            assert target.get_attribute('data-work-kind') == INLINE[command_id], (command_id, target.get_attribute('data-work-kind'))
            close_card(target)
        elif command_id == 'focus':
            source_card = card_for(source)
            if source_card.count() == 0:
                source_card = page.locator(PRIMARY).filter(has=source)
            expect(source_card).to_have_attribute('data-workspace-mode', 'expanded')
            browse(source); choose_command(source, command_id)
            expect(source_card).to_have_attribute('data-workspace-mode', 'split')
        else:
            selector, close = DIALOGS[command_id]
            target = page.locator(selector)
            expect(target).to_be_visible()
            if command_id == 'schedule':
                expect(target.locator('blockquote')).to_have_count(0)
                expect(target.locator('.fc-productivity-form')).to_have_count(0)
            target.get_by_role('button', name=close, exact=True).click()
            expect(target).to_have_count(0) if command_id != 'channels' else expect(target).not_to_be_visible()
        same_node(source, source_node, source_text)
        no_target_writes(writes, since, source_document)
        exercised.append(command_id)
    mark(name + '_real_permission_inventory_opens_intended_functions_without_publishing_source', inventory=available, opened=exercised)
    return available


def open_new_document(page, title, text):
    before = card_ids(page)
    main_command(page, '/document "' + title + '"\n' + text)
    card = latest_card(page, before)
    expect(card.get_by_label('Document title', exact=True)).to_have_value(title)
    expect(card.get_by_label('Document text', exact=True)).to_have_value(text)
    return card


def create_document(page, card, channel):
    with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/channels/' + channel + '/shared-documents') as response:
        card.get_by_role('button', name='Create document', exact=True).click()
    assert response.value.ok
    doc = response.value.json()
    expect(card.get_by_label('Document text', exact=True)).to_be_editable()
    expect(card.locator('.fc-shared-sync')).to_have_text('All edits synchronized')
    return doc


def open_saved_document(page, title, from_field=None):
    before = card_ids(page)
    if from_field:
        browse(from_field); choose_command(from_field, 'document')
    else:
        main_command(page, '/docs')
    card = latest_card(page, before)
    card.locator('.fc-shared-document-card:visible').filter(has=page.get_by_text(title, exact=True)).click()
    expect(card.get_by_role('button', name='Edit document', exact=True)).to_be_visible()
    card.get_by_role('button', name='Edit document', exact=True).click()
    expect(card.get_by_label('Document text', exact=True)).to_be_editable()
    return card


def run_native(browser):
    with fixture.fixture('native') as (base, app, private):
        set_stage('native actual fixture and real collaboration data')
        source_before = bind_source('native', private)
        data = setup_native(base)
        actor, channel = data['actor'], data['channel']['id']
        context, page, errors, unexpected, writes = fixture.browser_context(browser, app, actor, data['workspace'])
        dialogs, answer = fixture.install_dialog_answers(page)
        peer_context = None
        try:
            page.goto(app, wait_until='domcontentloaded')
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            fixture.choose_chat_channel(page, data['channel']['name'])
            doc_card = open_new_document(page, 'Universal working page ' + RUN, 'Opening paragraph.\nClosing paragraph.')
            draft_body = doc_card.get_by_label('Document text', exact=True)
            draft_node = live_handle(draft_body)
            before = card_ids(page); since = len(writes)
            draft_body.fill('Opening paragraph.\n/task "Parallel task ' + RUN + '"\nClosing paragraph.')
            draft_body.press('Control+Home'); draft_body.press('ArrowDown'); draft_body.press('End')
            choose_command(draft_body, 'task', keyboard=True)
            expect(draft_body).to_have_value('Opening paragraph.\n\nClosing paragraph.')
            task_card = latest_card(page, before)
            task_title = task_card.get_by_label('Task title', exact=True)
            expect(task_title).to_have_value('Parallel task ' + RUN)
            task_description = task_card.get_by_label('Description', exact=True)
            task_description.fill('An unsaved task remains with the page.')
            assert draft_body.evaluate('(node,old)=>node===old', draft_node)
            no_target_writes(writes, since)
            assert not dialogs, dialogs
            mark('native_typed_document_command_consumes_only_current_line_opens_unsaved_task_and_keeps_source_dom')

            # Every permission-valid inventory action must execute from each
            # writing surface, rather than merely appearing in its list.
            available = exercise_inventory(page, draft_body, writes, 'native_document')
            task_inventory = inventory(task_description)
            assert task_inventory == available
            exercise_inventory(page, task_description, writes, 'native_task_description')

            set_stage('native six real explicit references in a document draft')
            draft_body.fill('🧭 Plan with ')
            targets = [('person', data['peer']['user_id'], 'Maja'), ('group', data['group']['id'], 'Universal group'), ('document', data['document']['id'], 'Universal linked page'), ('task', data['task']['id'], 'Universal target'), ('file', data['file']['id'], 'Universal-material'), ('channel', channel, data['channel']['name'])]
            for index, (kind, identifier, query) in enumerate(targets):
                choose_reference(draft_body, kind, identifier, query, keyboard=index % 2 == 0)
                draft_body.press_sequentially(' · ')
            draft_text = draft_body.input_value()
            document = create_document(page, doc_card, channel)
            body = doc_card.get_by_label('Document text', exact=True)
            read = canonical(page, channel, document['id'])
            reference_bindings(read['text'], read.get('references') or [], [(kind, identifier) for kind, identifier, _ in targets])
            assert read['text'] == draft_text
            mark('native_document_six_explicit_identity_references_persist_with_utf16_ranges', kinds=[kind for kind, _, _ in targets])

            set_stage('native real document comment references and command boundary')
            comment = doc_card.get_by_label('Add a comment', exact=True)
            comment.fill('Discuss with ')
            choose_reference(comment, 'person', data['peer']['user_id'], 'Maja', keyboard=True)
            comment_value = comment.input_value()
            assert inventory(comment) == available
            exercise_inventory(page, comment, writes, 'native_document_comment', source_document=document['id'])
            expect(comment).to_have_value(comment_value)
            comment.fill('/task "Comment-origin task"')
            before_comment_task = card_ids(page)
            since = len(writes)
            doc_card.get_by_role('button', name='Post comment', exact=True).click()
            expect(comment).to_have_value('/task "Comment-origin task"')
            no_target_writes(writes, since, document['id'])
            comment.focus()
            comment.press('Enter')
            expect(comment).to_have_value('')
            no_target_writes(writes, since, document['id'])
            extra = latest_card(page, before_comment_task)
            expect(extra.get_by_label('Task title', exact=True)).to_have_value('Comment-origin task')
            answer['accept'] = True; close_card(extra); answer['accept'] = False
            dialogs_before_parallel = len(dialogs)
            comment.fill('Discuss with '); choose_reference(comment, 'person', data['peer']['user_id'], 'Maja')
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path.endswith('/shared-documents/' + document['id'] + '/comments')) as posted:
                doc_card.get_by_role('button', name='Post comment', exact=True).click()
            assert posted.value.ok
            stored_comment = posted.value.json()
            reference_bindings(stored_comment['fields']['text'], stored_comment['fields']['references'], [('person', data['peer']['user_id'])])
            mark('native_document_comment_opens_commands_without_posting_then_explicit_save_persists_reference')

            set_stage('native two documents and task and feed drafts retain independent live DOM')
            doc2_card = open_saved_document(page, 'Universal linked page', body)
            doc2 = doc2_card.get_by_label('Document text', exact=True)
            body_node, second_node, task_node = live_handle(body), live_handle(doc2), live_handle(task_description)
            second_text = doc2.input_value() + '\nIndependent second session.'
            doc2.fill(second_text)
            expect(doc2_card.locator('.fc-shared-sync')).to_have_text('All edits synchronized')
            before = card_ids(page); browse(task_description); choose_command(task_description, 'activity')
            feed_card = latest_card(page, before)
            feed_card.get_by_role('button', name='Open discussion', exact=True).click()
            feed_comment = feed_card.get_by_label('Add a comment', exact=True)
            feed_comment.fill('A feed comment stays unsaved alongside everything.')
            feed_node, feed_text = live_handle(feed_comment), feed_comment.input_value()
            task_text = task_description.input_value()
            for retained, node, text in ((body, body_node, draft_text), (doc2, second_node, second_text), (task_description, task_node, task_text), (feed_comment, feed_node, feed_text)):
                same_node(retained, node, text)
            assert len(card_ids(page)) >= 4
            assert len(dialogs) == dialogs_before_parallel, dialogs
            for card, field, node, value in ((doc_card, body, body_node, draft_text), (task_card, task_description, task_node, task_text)):
                card.locator('[data-work-expand]').click()
                expect(card).to_have_attribute('data-workspace-mode', 'expanded')
                same_node(field, node, value)
                card.locator('[data-work-expand]').click()
                expect(card).to_have_attribute('data-workspace-mode', 'split')
                identifier = card.get_attribute('data-collaboration-work-id') or 'primary'
                card.locator('[data-work-minimize]').click()
                expect(card).to_have_attribute('data-workspace-mode', 'minimized')
                assert node.evaluate('(field)=>field.isConnected')
                page.locator('.fc-work-resume' if identifier == 'primary' else '[data-collaboration-resume=' + json.dumps(identifier) + ']').click()
                expect(card).to_have_attribute('data-workspace-mode', 'split')
                same_node(field, node, value)
            for retained, node, text in ((doc2, second_node, second_text), (feed_comment, feed_node, feed_text)):
                same_node(retained, node, text)
            fixture.screenshot(page, 'native-four-simultaneous-workflows')
            mark('native_two_yjs_documents_dirty_task_and_feed_comment_remain_live_through_independent_park_and_expand')

            set_stage('native closing only one card guards only that draft')
            doc2_card.locator('[data-work-close]').click()
            expect(doc2_card).to_have_count(0)
            assert len(dialogs) == dialogs_before_parallel, 'Synchronized clean document close must not ask about sibling drafts'
            task_card.locator('[data-work-close]').click()
            assert len(dialogs) == dialogs_before_parallel + 1
            same_node(task_description, task_node, task_text)
            same_node(feed_comment, feed_node, feed_text)
            answer['accept'] = True; close_card(task_card); answer['accept'] = False
            same_node(feed_comment, feed_node, feed_text)
            assert len(dialogs) == dialogs_before_parallel + 2
            mark('native_close_one_clean_card_ignores_dirty_siblings_and_dirty_close_guards_only_its_owner')

            set_stage('native task title description and actual checklist IDs persist explicit references')
            before = card_ids(page)
            main_command(page, '/task "A referenced task"')
            saved_task_card = latest_card(page, before)
            title_field = saved_task_card.get_by_label('Task title', exact=True)
            title_field.fill('Review with ')
            choose_reference(title_field, 'person', data['peer']['user_id'], 'Maja')
            description_field = saved_task_card.get_by_label('Description', exact=True)
            description_field.fill('See ')
            choose_reference(description_field, 'document', data['document']['id'], 'Universal linked page', keyboard=True)
            saved_task_card.get_by_role('button', name='Add checklist item', exact=True).click()
            checklist_field = saved_task_card.get_by_label('Checklist item 1', exact=True)
            checklist_field.fill('Ask ')
            choose_reference(checklist_field, 'group', data['group']['id'], 'Universal group')
            task_values = [field.input_value() for field in (title_field, description_field, checklist_field)]
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path.endswith('/projects/' + data['project']['id'] + '/tasks')) as saved_task:
                saved_task_card.get_by_role('button', name='Save task', exact=True).click()
            assert saved_task.value.ok
            saved_task_id = saved_task.value.json()['id']
            task_record = next(row for row in api(base, 'GET', '/channels/' + channel + '/projects/' + data['project']['id'] + '/tasks', actor)['items'] if row['id'] == saved_task_id)
            fields = task_record['fields']
            assert [fields['title'], fields['description'], fields['checklist'][0]['title']] == task_values
            reference_bindings(fields['title'], fields['field_references']['title'], [('person', data['peer']['user_id'])])
            reference_bindings(fields['description'], fields['field_references']['description'], [('document', data['document']['id'])])
            reference_bindings(fields['checklist'][0]['title'], fields['field_references']['checklist:' + fields['checklist'][0]['id']], [('group', data['group']['id'])])
            expect(saved_task_card.locator('button.fc-message-reference')).to_have_count(3)
            same_node(feed_comment, feed_node, feed_text)
            mark('native_explicit_task_save_persists_title_description_and_stable_checklist_reference_metadata')

            set_stage('native slash-created task decision and document keep selected document identities')
            for command_id in ('task', 'decision', 'document'):
                source_text = '/' + command_id + ' "🧭 ' + command_id + ' with '
                comment.fill(source_text)
                choose_reference(comment, 'document', data['document']['id'], 'Universal linked page')
                comment.press_sequentially('" Use ')
                choose_reference(comment, 'document', data['document']['id'], 'Universal linked page', keyboard=True)
                comment.press_sequentially(' Reviewed.')
                original_command = comment.input_value()
                before = card_ids(page); since = len(writes)
                choose_command(comment, command_id)
                new_card = latest_card(page, before)
                expect(comment).to_have_value('')
                no_target_writes(writes, since, document['id'])
                if command_id == 'task':
                    command_title = new_card.get_by_label('Task title', exact=True).input_value()
                    command_body = new_card.get_by_label('Description', exact=True).input_value()
                    with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path.endswith('/projects/' + data['project']['id'] + '/tasks')) as command_saved:
                        new_card.get_by_role('button', name='Save task', exact=True).click()
                    assert command_saved.value.ok
                    command_record = next(row for row in api(base, 'GET', '/channels/' + channel + '/projects/' + data['project']['id'] + '/tasks', actor)['items'] if row['id'] == command_saved.value.json()['id'])
                    command_fields = command_record['fields']
                    assert command_fields['title'] == command_title and command_fields['description'] == command_body
                    reference_bindings(command_title, command_fields['field_references']['title'], [('document', data['document']['id'])])
                    reference_bindings(command_body, command_fields['field_references']['description'], [('document', data['document']['id'])])
                elif command_id == 'decision':
                    command_title = new_card.get_by_label('Title', exact=True).input_value()
                    command_body = new_card.get_by_label('Decision and context', exact=True).input_value()
                    with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path.endswith('/channels/' + channel + '/decisions')) as command_saved:
                        new_card.get_by_role('button', name='Record decision', exact=True).click()
                    assert command_saved.value.ok
                    command_fields = next(row for row in api(base, 'GET', '/channels/' + channel + '/decisions', actor) if row['id'] == command_saved.value.json()['id'])
                    assert command_fields['title'] == command_title and command_fields['content'] == command_body
                    reference_bindings(command_title, command_fields['field_references']['title'], [('document', data['document']['id'])])
                    reference_bindings(command_body, command_fields['field_references']['content'], [('document', data['document']['id'])])
                else:
                    command_title = new_card.get_by_label('Document title', exact=True).input_value()
                    command_body = new_card.get_by_label('Document text', exact=True).input_value()
                    created_command_doc = create_document(page, new_card, channel)
                    command_fields = canonical(page, channel, created_command_doc['id'])
                    assert command_fields['title'] == command_title and command_fields['text'] == command_body
                    reference_bindings(command_title, command_fields['titleReferences'], [('document', data['document']['id'])])
                    reference_bindings(command_body, command_fields['references'], [('document', data['document']['id'])])
                    new_card.get_by_role('button', name='Done editing', exact=True).click()
                expect(new_card.locator('button.fc-message-reference[data-reference-kind="document"][data-reference-id=' + json.dumps(data['document']['id']) + ']')).to_have_count(2)
                before_target = card_ids(page)
                new_card.locator('button.fc-message-reference[data-reference-kind="document"][data-reference-id=' + json.dumps(data['document']['id']) + ']').first.click()
                opened_target = latest_card(page, before_target)
                expect(opened_target.locator('.fc-shared-reading-title')).to_have_text('Universal linked page')
                expect(opened_target.locator('.fc-shared-reading-body')).to_have_text(second_text)
                same_node(body, body_node, draft_text)
                expect(comment).to_have_value('')
                close_card(opened_target); close_card(new_card)
                mark('native_slash_' + command_id + '_transfers_selected_title_and_context_ids_through_explicit_save_and_READ', sourceCommand=original_command, targetDocumentId=data['document']['id'])

            set_stage('native feed comment and update poll references use real persistence')
            feed_comment.press('Control+End'); feed_comment.press_sequentially(' With ')
            choose_reference(feed_comment, 'person', data['peer']['user_id'], 'Maja')
            saved_feed_comment_text = feed_comment.input_value()
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path.endswith('/activity-feed/' + data['post']['id'] + '/comments')) as feed_saved:
                feed_card.get_by_role('button', name='Save comment', exact=True).click()
            assert feed_saved.value.ok
            feed_detail = api(base, 'GET', '/channels/' + channel + '/activity-feed/' + data['post']['id'], actor)
            saved_comment = next(row for row in feed_detail['comments'] if row['id'] == feed_saved.value.json()['id'])
            assert saved_comment['fields']['body'] == saved_feed_comment_text
            reference_bindings(saved_comment['fields']['body'], saved_comment['fields']['field_references']['body'], [('person', data['peer']['user_id'])])
            expect(feed_card.locator('.fc-feed-comments button.fc-message-reference')).to_have_count(1)
            feed_card.get_by_role('button', name='Write an update', exact=True).click()
            feed_title = feed_card.get_by_label('Update title', exact=True)
            feed_title.fill('Review ')
            choose_reference(feed_title, 'task', data['task']['id'], 'Universal target')
            feed_body = feed_card.get_by_label('Update text', exact=True)
            feed_body.fill('Read ')
            choose_reference(feed_body, 'document', data['document']['id'], 'Universal linked page')
            feed_card.get_by_label('Include a poll', exact=True).check()
            question = feed_card.get_by_label('Poll question', exact=True)
            question.fill('Ask ')
            choose_reference(question, 'group', data['group']['id'], 'Universal group')
            first_option = feed_card.get_by_label('Option 1', exact=True)
            first_option.fill('Use ')
            choose_reference(first_option, 'file', data['file']['id'], 'Universal-material')
            feed_card.get_by_label('Option 2', exact=True).fill('Discuss first')
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path.endswith('/activity-feed')) as feed_posted:
                feed_card.get_by_role('button', name='Publish update', exact=True).click()
            assert feed_posted.value.ok
            posted_detail = api(base, 'GET', '/channels/' + channel + '/activity-feed/' + feed_posted.value.json()['id'], actor)
            post_fields = posted_detail['post']['fields']
            for key, text, expected in (
                ('title', post_fields['title'], [('task', data['task']['id'])]),
                ('body', post_fields['body'], [('document', data['document']['id'])]),
                ('poll.question', post_fields['poll']['question'], [('group', data['group']['id'])]),
                ('poll.option:' + post_fields['poll']['options'][0]['id'], post_fields['poll']['options'][0]['label'], [('file', data['file']['id'])]),
            ):
                reference_bindings(text, post_fields['field_references'][key], expected)
            mark('native_feed_comment_update_and_poll_fields_persist_real_reference_ids_without_flattening')

            set_stage('native decision title and context references save through actual decision API')
            before = card_ids(page)
            main_command(page, '/decision "Universal referenced decision"\nOur reason stays close.')
            decision_card = latest_card(page, before)
            decision_title = decision_card.get_by_label('Title', exact=True)
            decision_title.fill('Agreed with ')
            choose_reference(decision_title, 'person', data['peer']['user_id'], 'Maja')
            decision_content = decision_card.get_by_label('Decision and context', exact=True)
            decision_content.fill('Use ')
            choose_reference(decision_content, 'document', data['document']['id'], 'Universal linked page')
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path.endswith('/channels/' + channel + '/decisions')) as decision_saved:
                decision_card.get_by_role('button', name='Record decision', exact=True).click()
            assert decision_saved.value.ok
            decision_record = next(row for row in api(base, 'GET', '/channels/' + channel + '/decisions', actor) if row['id'] == decision_saved.value.json()['id'])
            reference_bindings(decision_record['title'], decision_record['field_references']['title'], [('person', data['peer']['user_id'])])
            reference_bindings(decision_record['content'], decision_record['field_references']['content'], [('document', data['document']['id'])])
            expect(decision_card.locator('button.fc-message-reference')).to_have_count(2)
            mark('native_decision_title_and_context_save_and_READ_preserve_explicit_reference_ids')

            set_stage('native real second peer prefixes before durable document references')
            peer_context, peer_page, peer_errors, peer_unexpected, peer_writes = fixture.browser_context(browser, app, data['peer'], data['workspace'])
            peer_page.goto(app, wait_until='domcontentloaded')
            expect(peer_page.locator('.fc-chat')).to_be_visible(timeout=90000)
            fixture.choose_chat_channel(peer_page, data['channel']['name'])
            peer_card = open_saved_document(peer_page, 'Universal working page ' + RUN)
            peer_body = peer_card.get_by_label('Document text', exact=True)
            peer_body.press('Control+Home'); peer_body.press_sequentially('Peer prefix ')
            peer_page.keyboard.insert_text('🪵')
            peer_body.press('Enter')
            prefixed = 'Peer prefix 🪵\n' + draft_text
            expect(body).to_have_value(prefixed, timeout=30000)
            expect(doc_card.locator('.fc-shared-sync')).to_have_text('All edits synchronized')
            read = canonical(page, channel, document['id'])
            reference_bindings(read['text'], read.get('references') or [], [(kind, identifier) for kind, identifier, _ in targets])
            assert read['text'] == prefixed
            # Two real actors edit disconnected replicas before either can
            # observe the other update. The mention anchors must follow the
            # merged Y.Text instead of stale absolute positions.
            context.set_offline(True); peer_context.set_offline(True)
            body.press('Control+End'); body.press('Enter'); body.press_sequentially('Owner disconnected suffix.')
            peer_body.press('Control+Home'); peer_body.press_sequentially('Peer concurrent start.'); peer_body.press('Enter')
            expect(body).to_have_value(prefixed + '\nOwner disconnected suffix.')
            expect(peer_body).to_have_value('Peer concurrent start.\n' + prefixed)
            context.set_offline(False); peer_context.set_offline(False)
            prefixed = 'Peer concurrent start.\n' + prefixed + '\nOwner disconnected suffix.'
            expect(body).to_have_value(prefixed, timeout=40000)
            expect(peer_body).to_have_value(prefixed, timeout=40000)
            expect(doc_card.locator('.fc-shared-sync')).to_have_text('All edits synchronized')
            merged = canonical(page, channel, document['id'])
            assert merged['text'] == prefixed
            reference_bindings(merged['text'], merged.get('references') or [], [(kind, identifier) for kind, identifier, _ in targets])
            mark('native_actual_disconnected_peers_merge_independent_edits_and_all_six_reference_anchors')
            doc_card.locator('.fc-shared-document-options > summary').click()
            doc_card.get_by_role('button', name='Named revisions', exact=True).click()
            doc_card.get_by_label('Revision name', exact=True).fill('References before a later edit')
            doc_card.get_by_role('button', name='Save revision', exact=True).click()
            expect(doc_card.get_by_role('button', name='Restore text', exact=True)).to_be_visible()
            body.press('Control+End'); body.press('Enter'); body.press_sequentially('Later owner text.')
            expect(doc_card.locator('.fc-shared-sync')).to_have_text('All edits synchronized')
            doc_card.get_by_role('button', name='Restore text', exact=True).click()
            doc_card.get_by_role('button', name='Restore revision text', exact=True).click()
            expect(body).to_have_value(prefixed)
            expect(peer_body).to_have_value(prefixed, timeout=30000)
            expect(doc_card.locator('.fc-shared-sync')).to_have_text('All edits synchronized')
            restored = canonical(page, channel, document['id'])
            reference_bindings(restored['text'], restored.get('references') or [], [(kind, identifier) for kind, identifier, _ in targets])
            mark('native_peer_insertion_and_named_revision_restore_preserve_real_reference_ids_and_anchored_ranges')

            set_stage('native READ references resolve current authority and open retained target')
            doc_card.get_by_role('button', name='Done editing', exact=True).click()
            reading = doc_card.get_by_label('Document reading view', exact=True)
            expect(reading.locator('button.fc-message-reference')).to_have_count(6)
            before = card_ids(page)
            reading.locator('button.fc-message-reference[data-reference-kind="document"]').click()
            target = latest_card(page, before)
            expect(target.get_by_role('button', name='Edit document', exact=True)).to_be_visible()
            close_card(target)
            assert not errors and not unexpected, (errors, unexpected)
            assert not peer_errors and not peer_unexpected, (peer_errors, peer_unexpected)
            mark('native_saved_document_READ_resolves_explicit_reference_and_opens_real_target')

            # Fresh-page reload exercises persisted resource metadata, not local
            # editor state or a fabricated response.
            peer_page.reload(wait_until='domcontentloaded')
            expect(peer_page.locator('.fc-chat')).to_be_visible(timeout=90000)
            fixture.choose_chat_channel(peer_page, data['channel']['name'])
            reloaded = open_saved_document(peer_page, 'Universal working page ' + RUN)
            expect(reloaded.get_by_label('Document text', exact=True)).to_have_value(prefixed)
            durable = canonical(peer_page, channel, document['id'])
            reference_bindings(durable['text'], durable.get('references') or [], [(kind, identifier) for kind, identifier, _ in targets])
            mark('native_reload_reconstructs_document_and_all_reference_identities_from_real_persistence')

            set_stage('native real role downgrade removes stale group edit authority')
            api(base, 'PATCH', '/channels/' + channel + '/members/' + data['peer']['user_id'], actor, {'role': 'admin'})
            before = card_ids(peer_page)
            main_command(peer_page, '/groups')
            role_card = latest_card(peer_page, before)
            role_card.get_by_role('button', name='New group', exact=True).click()
            group_name = role_card.get_by_label('Group name', exact=True)
            group_name.fill('This group must not survive role loss')
            role_card.locator('[data-reference-member-id=' + json.dumps(actor['user_id']) + ']').check()
            api(base, 'PATCH', '/channels/' + channel + '/members/' + data['peer']['user_id'], actor, {'role': 'member'})
            expect(role_card.get_by_role('button', name='Create group', exact=True)).to_have_count(0, timeout=30000)
            expect(role_card.get_by_label('Group name', exact=True)).to_have_count(0)
            assert not any(row['name'] == 'This group must not survive role loss' for row in api(base, 'GET', '/mentions/channels/' + channel + '/groups', actor))
            expect(reloaded.get_by_label('Document text', exact=True)).to_have_value(prefixed)
            mark('native_actual_role_downgrade_removes_old_group_form_commands_and_save_authority')

            set_stage('native actual membership revocation purges retained editors')
            revoked_body = reloaded.get_by_label('Document text', exact=True)
            revoked_body.press('Control+End'); revoked_body.press('Enter'); revoked_body.press_sequentially('Last authorized edit.')
            expect(reloaded.locator('.fc-shared-sync')).to_have_text('All edits synchronized')
            main_command(peer_page, '/task "Revoked private draft"\nMust leave with this membership.')
            revoked_task = peer_page.get_by_label('Task title', exact=True)
            expect(revoked_task).to_have_value('Revoked private draft')
            assert api(base, 'GET', '/channels/' + channel + '/shared-documents/' + document['id'] + '/updates', data['peer'])['items']
            api(base, 'DELETE', '/channels/' + channel + '/members/' + data['peer']['user_id'], actor)
            expect(cards(peer_page)).to_have_count(0, timeout=30000)
            expect(peer_page.get_by_label('Task title', exact=True)).to_have_count(0)
            try:
                api(base, 'GET', '/channels/' + channel + '/shared-documents/' + document['id'] + '/updates', data['peer'])
                raise AssertionError('Revoked actor read the document through a stale surface')
            except HTTPError as denied:
                assert denied.code in (403, 404)
            mark('native_real_membership_revocation_purges_saved_document_and_dirty_task_and_blocks_stale_access')

            set_stage('native old READ person reference checks actual current target authority')
            person_reference = reading.locator('[data-reference-kind="person"][data-reference-id=' + json.dumps(data['peer']['user_id']) + ']')
            expect(person_reference).to_have_count(1)
            before_stale_reference = card_ids(page)
            since = len(writes)
            stale_was_button = person_reference.evaluate('(node)=>node.tagName === "BUTTON"')
            if stale_was_button:
                person_reference.click()
            expect(reading.locator('button.fc-message-reference[data-reference-kind="person"][data-reference-id=' + json.dumps(data['peer']['user_id']) + ']')).to_have_count(0)
            expect(person_reference).to_have_class(re.compile(r'\bis-unavailable\b'))
            expect(page.locator('.fc-channel-layer')).to_have_count(0)
            assert card_ids(page) == before_stale_reference
            no_target_writes(writes, since, document['id'])
            still_explicit = canonical(page, channel, document['id'])
            reference_bindings(still_explicit['text'], still_explicit.get('references') or [], [(kind, identifier) for kind, identifier, _ in targets])
            mark('native_stale_READ_reference_cannot_open_removed_person_and_keeps_historical_explicit_identity', staleButtonRechecked=stale_was_button)

            set_stage('native actual delayed reference resolution cannot reopen a closed source surface')
            held_reference_requests = []
            resolve_url = '**/mentions/channels/' + channel + '/references/resolve'
            def hold_selected_reference(route):
                request_data = route.request.post_data_json
                if request_data.get('references') == [{'kind': 'document', 'id': data['document']['id']}]:
                    held_reference_requests.append(route)
                else:
                    route.continue_()
            page.route(resolve_url, hold_selected_reference)
            try:
                expect(reading.locator('button.fc-message-reference[data-reference-kind="document"]')).to_have_count(1)
                reading.locator('button.fc-message-reference[data-reference-kind="document"]').click()
                deadline = time.monotonic() + 10
                while not held_reference_requests and time.monotonic() < deadline:
                    page.wait_for_timeout(25)
                assert held_reference_requests, 'The actual source reference must request fresh authority'
                close_card(doc_card)
                retained_after_close = card_ids(page)
                delayed_route = held_reference_requests.pop(0)
                delayed_request = delayed_route.request
                delayed_route.continue_()
                resolved_after_close = delayed_request.response()
                if resolved_after_close is None:
                    assert delayed_request.failure and 'ABORT' in delayed_request.failure.upper(), delayed_request.failure
                    late_result = {'cancelledBySourceCleanup': delayed_request.failure}
                else:
                    assert resolved_after_close.ok
                    resolved_after_close.finished()
                    late_result = {'actualResponseStatus': resolved_after_close.status}
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                assert card_ids(page) == retained_after_close, 'A stale resolved reference reopened work after its source closed'
                assert not body_node.evaluate('(node)=>node.isConnected')
                mark('native_delayed_real_reference_response_cannot_reopen_closed_document_surface_or_move_siblings', **late_result)
            finally:
                for route in held_reference_requests:
                    route.continue_()
                page.unroute(resolve_url, hold_selected_reference)

            set_stage('native real token revocation purges owner retained work')
            assert cards(page).count() >= 3
            api(base, 'POST', '/auth/logout', actor)
            expect(cards(page)).to_have_count(0, timeout=35000)
            expect(page.locator('.fc-shared-documents')).to_have_count(0)
            expect(page.locator('.fc-slash-commands')).to_have_count(0)
            expect(page.locator('.fc-mention-references')).to_have_count(0)
            try:
                api(base, 'GET', '/channels/' + channel + '/shared-documents/' + document['id'] + '/updates', actor)
                raise AssertionError('A revoked token still authorizes saved work')
            except HTTPError as denied:
                assert denied.code == 401
            mark('native_actual_token_revocation_purges_retained_cards_and_writing_picker_authority')
            finish_source_binding('native', private, source_before)
            evidence.append({'mode': 'native', 'sourceHashes': source_before, 'writes': writes, 'peerWrites': peer_writes})
        except Exception:
            fixture.capture_failure(page, 'universal-native')
            raise
        finally:
            if peer_context:
                peer_context.close()
            context.close()


def run_demo(browser):
    with fixture.fixture('demo') as (base, app, private):
        set_stage('actual demo mobile installation')
        DOCUMENT_CLIENT_URLS[app] = '/product-demo/@fs' + str(private / 'frontend/src/lib/shared-documents-api.ts')
        source_before = bind_source('demo', private)
        context, page, errors, unexpected, writes = fixture.browser_context(browser, app, viewport={'width': 390, 'height': 680})
        # Playwright touch capability is a real browser-context property, so use
        # the same genuine demo bootstrap in an explicitly touch-enabled peer.
        context.close()
        context = browser.new_context(viewport={'width': 390, 'height': 680}, locale='sv-SE', reduced_motion='reduce', has_touch=True, is_mobile=True)
        errors, unexpected, writes = [], [], []
        context.route('**/*', lambda route: route.continue_() if urlsplit(route.request.url).netloc == urlsplit(app).netloc or urlsplit(route.request.url).scheme in ('blob', 'data') else (unexpected.append(urlsplit(route.request.url).path), route.abort()))
        page = context.new_page(); page.set_default_timeout(40000)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: writes.append({'method': request.method, 'path': urlsplit(request.url).path}) if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') else None)
        page.on('response', lambda response: fixture.observations.append({'status': 429, 'path': urlsplit(response.url).path}) if response.status == 429 else None)
        visit = None
        try:
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/demo/session') as boot:
                page.goto(app + '/product-demo/?locale=en', wait_until='domcontentloaded')
            assert boot.value.ok
            visit = boot.value.json()
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            card = open_new_document(page, 'Mobile universal page ' + RUN, 'Keep this mobile page.')
            body = card.get_by_label('Document text', exact=True)
            node = live_handle(body)
            for width, height in ((390, 680), (320, 568), (390, 420)):
                page.set_viewport_size({'width': width, 'height': height})
                body.fill('/task "Touch task ' + str(width) + '-' + str(height) + '"')
                popup = wrapper(body).locator('.fc-slash-commands')
                expect(popup).to_be_visible()
                assert_bounds(page, popup)
                before = card_ids(page); since = len(writes)
                choose_command(body, 'task', touch=True)
                target = latest_card(page, before)
                expect(target.get_by_label('Task title', exact=True)).to_have_value('Touch task ' + str(width) + '-' + str(height))
                expect(body).to_have_value('')
                assert body.evaluate('(node,previous)=>node===previous', node)
                no_target_writes(writes, since)
                target.get_by_label('Task title', exact=True).fill('')
                close_card(target)
            mark('demo_390_320_and_short_viewport_actual_touch_selects_document_command_without_posting_or_source_remount')

            set_stage('demo literal Escape and IME key safety in a real document field')
            body.fill('/docs')
            body.press('Escape')
            expect(wrapper(body).locator('.fc-slash-commands')).to_have_count(0)
            expect(body).to_have_value('/docs')
            since = len(writes)
            # Genuine browser DOM composition events; no React handler or
            # business response is replaced. Keyboard Enter while composing
            # must leave the literal input and not create a target resource.
            body.dispatch_event('compositionstart', {'data': '文'})
            body.dispatch_event('keydown', {'key': 'Enter', 'code': 'Enter', 'isComposing': True, 'keyCode': 229})
            body.dispatch_event('compositionend', {'data': '文'})
            expect(body).to_have_value('/docs')
            no_target_writes(writes, since)
            mark('demo_document_Escape_retains_literal_and_composition_Enter_does_not_publish')

            page.set_viewport_size({'width': 390, 'height': 680})
            body.fill('Mobile reference to ')
            people = api(base, 'GET', '/channels/' + visit['channel_id'] + '/members', visit['session'])
            person = next(person for person in people if person['user_id'] != visit['session']['user_id'])
            choose_reference(body, 'person', person['user_id'], person['name'].split()[0], touch=True)
            document = create_document(page, card, visit['channel_id'])
            read = canonical(page, visit['channel_id'], document['id'])
            reference_bindings(read['text'], read.get('references') or [], [('person', person['user_id'])])
            card.get_by_role('button', name='Done editing', exact=True).tap()
            expect(card.get_by_label('Document reading view', exact=True).locator('button.fc-message-reference')).to_have_count(1)
            fixture.screenshot(page, 'demo-mobile-real-document-reference')
            mark('demo_mobile_at_reference_persists_actual_person_identity_and_READ_affordance')
            assert not errors and not unexpected, (errors, unexpected)
            finish_source_binding('demo', private, source_before)
            evidence.append({'mode': 'demo', 'sourceHashes': source_before, 'writes': writes})
        except Exception:
            fixture.capture_failure(page, 'universal-demo')
            raise
        finally:
            if visit:
                api(base, 'DELETE', '/demo/session', visit['session'])
                evidence.append({'ownedDemoVisitDeleted': True})
            context.close()


def run_modal_writers(browser):
    with fixture.fixture('native') as (base, app, private):
        set_stage('actual file and calendar modal writing fields open reachable simultaneous work')
        source_before = bind_source('modal-writers', private)
        data = setup_native(base)
        actor, channel = data['actor'], data['channel']['id']
        context, page, errors, unexpected, writes = fixture.browser_context(browser, app, actor, data['workspace'])
        dialogs, answer = fixture.install_dialog_answers(page)
        def modal_return_probe(message):
            if message.text.startswith('fc-modal-return-probe'):
                evidence.append({'modalReturnProbe': [argument.json_value() for argument in message.args]})
        page.on('console', modal_return_probe)
        try:
            page.goto(app, wait_until='domcontentloaded')
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            fixture.choose_chat_channel(page, data['channel']['name'])
            composer = page.locator('.fc-chat-composer textarea')
            composer.fill('The original file is ')
            fixture.choose_reference(page, composer, 'file', data['file']['id'], 'Universal-material')
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path in ('/api/messages/channels/' + channel, '/api/mobile/offline/channels/' + channel + '/messages')) as sent:
                composer.press('Enter')
            assert sent.value.ok
            file_reference = page.locator('button.fc-message-reference[data-reference-kind="file"][data-reference-id=' + json.dumps(data['file']['id']) + ']')
            expect(file_reference).to_have_count(1)
            file_reference.click()
            file_modal = page.locator('.fc-file-preview-layer')
            expect(file_modal).to_be_visible()
            file_comment = file_modal.get_by_label('Add a file comment', exact=True)
            file_comment.fill('The file discussion stays.\n/task "A task from this file"')
            file_node = live_handle(file_comment)
            popup = wrapper(file_comment).locator('.fc-slash-commands')
            expect(popup).to_be_visible()
            evidence.append({'fileModalPopupGeometry': popup.evaluate('''node=>({
              viewport:{width:innerWidth,height:innerHeight},
              popup:node.getBoundingClientRect().toJSON(),style:node.parentElement?.getAttribute('style'),
              input:node.closest('.fc-collaboration-input')?.querySelector('textarea,input')?.getBoundingClientRect().toJSON(),
              fixedOrigin:node.closest('.fc-collaboration-input')?.querySelector('.fc-collaboration-fixed-origin')?.getBoundingClientRect().toJSON(),
              anchorComputed:(()=>{const p=node.parentElement,s=getComputedStyle(p);return{position:s.position,left:s.left,top:s.top,right:s.right,bottom:s.bottom,display:s.display,transform:s.transform,translate:s.translate,scale:s.scale,offsetParent:p.offsetParent?.className,offsetLeft:p.offsetLeft,offsetTop:p.offsetTop,offsetWidth:p.offsetWidth,offsetHeight:p.offsetHeight}})(),
              option:node.querySelector('[role=option]')?.getBoundingClientRect().toJSON(),
              ancestors:(()=>{const result=[];for(let parent=node.parentElement;parent;parent=parent.parentElement){const s=getComputedStyle(parent);result.push({tag:parent.tagName,classes:parent.className,rect:parent.getBoundingClientRect().toJSON(),transform:s.transform,filter:s.filter,backdrop:s.backdropFilter,perspective:s.perspective,contain:s.contain,overflow:s.overflow,scrollTop:parent.scrollTop})}return result})()
            })''')})
            assert_bounds(page, popup)
            before = card_ids(page); since = len(writes)
            choose_command(file_comment, 'task')
            task = latest_card(page, before)
            expect(task.get_by_label('Task title', exact=True)).to_have_value('A task from this file')
            task_description = task.get_by_label('Description', exact=True)
            task_description.click(timeout=5000)
            task_description.press_sequentially('Review the original material.')
            page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            expect(task_description).to_be_focused()
            expect(task_description).to_have_value('Review the original material.')
            same_node(file_comment, file_node, 'The file discussion stays.\n')
            no_target_writes(writes, since)
            answer['accept'] = True; close_card(task); answer['accept'] = False
            expect(file_modal).to_be_visible()
            same_node(file_comment, file_node, 'The file discussion stays.\n')
            expect(file_comment).to_be_focused()
            fixture.screenshot(page, 'file-modal-command-return')
            answer['accept'] = True
            file_modal.get_by_role('button', name='Close file preview', exact=True).click()
            answer['accept'] = False
            expect(file_modal).to_have_count(0)
            mark('native_file_comment_command_opens_pointer_reachable_task_and_returns_same_unsaved_modal_field')

            set_stage('actual current-channel calendar modal command reaches its task and returns draft')
            main_command(page, '/integrations')
            integrations = page.locator('.fc-integrations-panel')
            expect(integrations).to_be_visible()
            integrations.get_by_role('navigation', name='Integration sections', exact=True).get_by_role('button', name='Calendar', exact=True).click()
            expect(integrations.get_by_label('Channel', exact=True)).to_have_value(channel)
            integrations.get_by_role('button', name='Add calendar event', exact=True).click()
            integrations.get_by_label('Event title', exact=True).fill('Our unsaved calendar event')
            calendar_details = integrations.get_by_label('Event details', exact=True)
            calendar_details.fill('The calendar context stays.\n/task "A task from the calendar"')
            calendar_node = live_handle(calendar_details)
            before = card_ids(page); since = len(writes)
            choose_command(calendar_details, 'task')
            task = latest_card(page, before)
            expect(task.get_by_label('Task title', exact=True)).to_have_value('A task from the calendar')
            description = task.get_by_label('Description', exact=True)
            description.click(timeout=5000)
            description.press_sequentially('Keep the event with its conversation.')
            page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            expect(description).to_be_focused()
            same_node(calendar_details, calendar_node, 'The calendar context stays.\n')
            expect(integrations.get_by_label('Event title', exact=True)).to_have_value('Our unsaved calendar event')
            no_target_writes(writes, since)
            answer['accept'] = True; close_card(task); answer['accept'] = False
            expect(integrations).to_be_visible()
            same_node(calendar_details, calendar_node, 'The calendar context stays.\n')
            expect(calendar_details).to_be_focused()
            expect(integrations.get_by_label('Event title', exact=True)).to_have_value('Our unsaved calendar event')
            fixture.screenshot(page, 'calendar-modal-command-return')
            answer['accept'] = True
            integrations.get_by_role('button', name='Close integrations', exact=True).click()
            answer['accept'] = False
            expect(integrations).to_have_count(0)
            mark('native_calendar_details_command_opens_pointer_reachable_task_and_returns_same_unsaved_event_fields')

            set_stage('actual posted file comment READ retains source and restores exact reference button')
            file_reference.click()
            file_modal = page.locator('.fc-file-preview-layer')
            expect(file_modal).to_be_visible()
            file_comment = file_modal.get_by_label('Add a file comment', exact=True)
            file_comment.fill('Read ')
            choose_reference(file_comment, 'document', data['document']['id'], 'Universal linked page')
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path.startswith('/api/channels/' + channel + '/file-experience/') and urlsplit(response.url).path.endswith('/comments')) as posted_reference:
                file_modal.get_by_role('button', name='Post comment', exact=True).click()
            assert posted_reference.value.ok
            file_comment.fill('Another unsaved file discussion stays here.')
            file_node = live_handle(file_comment)
            read_button = file_modal.locator('button.fc-message-reference[data-reference-kind="document"][data-reference-id=' + json.dumps(data['document']['id']) + ']')
            expect(read_button).to_have_count(1)
            read_node = live_handle(read_button)
            before = card_ids(page); since = len(writes)
            read_button.click(timeout=5000)
            target_doc = latest_card(page, before)
            expect(target_doc.locator('.fc-shared-reading-title')).to_have_text('Universal linked page')
            target_doc.get_by_role('button', name='Edit document', exact=True).click(timeout=5000)
            target_doc.get_by_label('Document text', exact=True).click(timeout=5000)
            expect(target_doc.get_by_label('Document text', exact=True)).to_be_focused()
            same_node(file_comment, file_node, 'Another unsaved file discussion stays here.')
            no_target_writes(writes, since)
            close_card(target_doc)
            expect(file_modal).to_be_visible()
            same_node(file_comment, file_node, 'Another unsaved file discussion stays here.')
            assert read_button.evaluate('(current,previous)=>current===previous', read_node)
            expect(read_button).to_be_focused()
            answer['accept'] = True
            file_modal.get_by_role('button', name='Close file preview', exact=True).click()
            answer['accept'] = False
            expect(file_modal).to_have_count(0)
            mark('native_file_comment_READ_opens_reachable_original_document_and_restores_same_exact_button_and_draft')

            set_stage('nested real Files preview opens Members without replacing its source or parent')
            main_command(page, '/files')
            files_parent = page.get_by_role('dialog', name='Channel files', exact=True)
            expect(files_parent).to_be_visible()
            files_node = live_handle(files_parent)
            files_parent.get_by_role('button', name='Preview ' + data['file']['filename'], exact=True).click()
            nested_file = page.locator('.fc-file-preview-layer')
            expect(nested_file).to_be_visible()
            file_title = nested_file.get_attribute('aria-label')
            nested_comment = nested_file.get_by_label('Add a file comment', exact=True)
            nested_comment.fill('Nested file draft stays.\n/members')
            nested_node = live_handle(nested_comment)
            since = len(writes)
            choose_command(nested_comment, 'members')
            members = page.get_by_role('dialog', name='Channel members', exact=True)
            expect(members).to_be_visible()
            expect(members.get_by_role('list', name='Channel member list', exact=True)).to_be_visible()
            same_node(nested_comment, nested_node, 'Nested file draft stays.\n')
            assert files_node.evaluate('(node)=>node.isConnected')
            members.get_by_role('button', name='Close channel panel', exact=True).click(timeout=5000)
            expect(members).to_have_count(0)
            continue_file = page.get_by_role('button', name='Continue ' + file_title, exact=True)
            if not nested_file.is_visible():
                continue_file.click(timeout=5000)
            expect(nested_file).to_be_visible()
            same_node(nested_comment, nested_node, 'Nested file draft stays.\n')
            expect(nested_comment).to_be_focused()
            no_target_writes(writes, since)
            mark('native_nested_files_preview_members_target_is_pointer_reachable_and_keeps_parent_and_file_draft')

            set_stage('real parent Files close resumes its retained child file comment without discarding')
            browse(nested_comment); choose_command(nested_comment, 'files')
            expect(files_parent).to_be_visible()
            assert files_parent.evaluate('(current,previous)=>current===previous', files_node)
            prior_dialogs = len(dialogs)
            files_parent.get_by_role('button', name='Close channel panel', exact=True).click(timeout=5000)
            assert len(dialogs) == prior_dialogs, 'A non-destructive close must resume the retained source without asking to discard it'
            expect(nested_file).to_be_visible()
            same_node(nested_comment, nested_node, 'Nested file draft stays.\n')
            expect(nested_comment).to_be_focused()
            prior_dialogs = len(dialogs)
            nested_file.get_by_role('button', name='Close file preview', exact=True).click()
            assert len(dialogs) == prior_dialogs + 1
            expect(nested_file).to_be_visible()
            same_node(nested_comment, nested_node, 'Nested file draft stays.\n')
            answer['accept'] = True
            nested_file.get_by_role('button', name='Close file preview', exact=True).click()
            answer['accept'] = False
            expect(nested_file).to_have_count(0)
            if files_parent.is_visible():
                files_parent.get_by_role('button', name='Close channel panel', exact=True).click()
            mark('native_parent_files_close_restores_same_child_draft_without_prompt_and_real_file_close_guards_its_own_draft')

            set_stage('actual Mobile private local draft opens a reachable retained document and returns unchanged source')
            main_private = 'An actual private draft saved by the composer.'
            composer.fill(main_private)
            launcher = page.get_by_role('button', name='Show all features', exact=True)
            launcher.click()
            main_popup = page.locator('.fc-chat-composer-area .fc-slash-commands')
            expect(main_popup).to_be_visible()
            list_id = main_popup.get_by_role('listbox').get_attribute('id')
            main_popup.locator('[role="option"][id=' + json.dumps(list_id + '-mobile') + ']').click()
            mobile = page.get_by_role('dialog', name='Mobile and offline', exact=True, include_hidden=True)
            expect(mobile).to_be_visible()
            mobile.get_by_role('button', name='Edit draft', exact=True).click()
            private_draft = mobile.get_by_label('Private draft text', exact=True)
            expect(private_draft).to_have_value(main_private)
            private_draft.fill(main_private + '\n/document "A page from the private draft"')
            private_node = live_handle(private_draft)
            before = card_ids(page); since = len(writes)
            choose_command(private_draft, 'document')
            document_draft = latest_card(page, before)
            expect(document_draft.get_by_label('Document title', exact=True)).to_have_value('A page from the private draft')
            doc_text = document_draft.get_by_label('Document text', exact=True)
            doc_text.click(timeout=5000)
            doc_text.press_sequentially('This new page is still an unsaved draft.')
            expect(doc_text).to_be_focused()
            same_node(private_draft, private_node, main_private + '\n')
            no_target_writes(writes, since)
            answer['accept'] = True; close_card(document_draft); answer['accept'] = False
            expect(mobile).to_be_visible()
            same_node(private_draft, private_node, main_private + '\n')
            expect(private_draft).to_be_focused()
            expect(composer).to_have_value(main_private)
            answer['accept'] = True
            mobile.get_by_role('button', name='Close mobile settings', exact=True).click()
            answer['accept'] = False
            expect(mobile).to_have_count(0)
            mark('native_mobile_private_draft_command_opens_pointer_reachable_document_and_restores_same_local_draft')
            assert not errors and not unexpected, (errors, unexpected)
            finish_source_binding('modal-writers', private, source_before)
        except Exception:
            evidence.append({'modalFailureFocus': page.evaluate('''()=>({
              active:document.activeElement?{tag:document.activeElement.tagName,id:document.activeElement.id,label:document.activeElement.getAttribute('aria-label'),text:document.activeElement.textContent?.slice(0,120),dialog:document.activeElement.closest('[role=dialog]')?.getAttribute('aria-label')}:null,
              continues:[...document.querySelectorAll('[data-modal-continue]')].map(node=>({text:node.textContent,visible:!!node.getClientRects().length})),
              dialogs:[...document.querySelectorAll('[role=dialog]')].map(node=>({label:node.getAttribute('aria-label'),hidden:node.hidden,inert:node.inert,visible:!!node.getClientRects().length})),
              fileInputs:[...document.querySelectorAll('.fc-file-preview-layer textarea')].map(node=>({id:node.id,disabled:node.disabled,readonly:node.readOnly,rect:node.getBoundingClientRect().toJSON(),hiddenAncestor:node.closest('[hidden]')?.className,inertAncestor:node.closest('[inert]')?.className,ariaHiddenAncestor:node.closest('[aria-hidden=true]')?.className}))
            })''')})
            fixture.capture_failure(page, 'universal-modal-writers')
            raise
        finally:
            context.close()


@contextmanager
def secure_transition_fixture():
    """Real preparing-binding failure path against an owned unavailable socket.

    This deliberately does not implement a pretend Matrix service. The normal
    enable endpoint commits its fail-closed preparing binding, then receives a
    genuine connection refusal from the reserved, non-listening loopback port.
    """
    processes, streams = [], []
    unavailable = socket.socket()
    unavailable.bind(('127.0.0.1', 0))
    matrix_url = 'http://127.0.0.1:' + str(unavailable.getsockname()[1])
    with tempfile.TemporaryDirectory(prefix='freecollab-universal-security-') as directory:
        private = Path(directory)
        frontend, backend, data = private / 'frontend', private / 'backend', private / 'data'
        backend.mkdir(); data.mkdir()
        shutil.copytree(ROOT / 'backend/app', backend / 'app', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copytree(ROOT / 'frontend', frontend, ignore=shutil.ignore_patterns('node_modules', 'dist*', '.env*', '*.log', '*.tsbuildinfo', '__pycache__', '.vite*'))
        (frontend / 'node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
        fixture.snapshots['secure-transition'] = hashes(private)
        api_port, app_port = fixture.port(), fixture.port()
        base, app = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
        environment = os.environ.copy()
        environment.update(ENVIRONMENT='test', DEMO_INSTANCE='false', DATABASE_URL='sqlite:///' + str(data / 'proof.sqlite'),
            STORAGE_BACKEND='local', STORAGE_PATH=str(data / 'files'), REDIS_URL='', JWT_SECRET=secrets.token_urlsafe(48),
            ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), ACCOUNT_SECURITY_KEY_FILE=str(data / 'account.key'),
            MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof': base64.b64encode(secrets.token_bytes(32)).decode()}),
            MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof', MESSAGE_ENCRYPTION_KEY_FILE=str(data / 'message-keys.json'),
            PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]), MAIL_DELIVERY_MODE='disabled',
            MEETINGS_SFU_PROVIDER='disabled', MEETINGS_ICE_SERVERS='[]', MEETINGS_TURN_URLS='[]',
            MATRIX_ENABLED='true', MATRIX_INTERNAL_URL=matrix_url, MATRIX_SERVER_NAME='universal-proof.invalid',
            MATRIX_PUBLIC_BASE_URL=app, MATRIX_REGISTRATION_SHARED_SECRET=secrets.token_urlsafe(40),
            PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(backend))
        for flag in ('CONNECT_FEDERATION_ENABLED', 'OIDC_ENABLED', 'WEBHOOKS_ENABLED', 'SYNC_ENABLED', 'OTRUST_ENABLED',
                     'CLOUD_FILES_ENABLED', 'EXTERNAL_MEETINGS_ENABLED', 'COPILOT_ENABLED', 'PUSH_ENABLED', 'NATIVE_PUSH_ENABLED',
                     'NATIVE_CALLS_ENABLED', 'ENABLE_EXPERIMENTAL_FEATURES', 'ENTERPRISE_SEARCH_ENABLED', 'DATA_CONTROLS_ENABLED'):
            environment[flag] = 'false'
        environment['NO_PROXY'] = ','.join(filter(None, [environment.get('NO_PROXY', ''), 'localhost', '127.0.0.1', '::1']))
        environment['no_proxy'] = ','.join(filter(None, [environment.get('no_proxy', ''), environment['NO_PROXY']]))
        vite = private / 'vite.mjs'
        vite.write_text('import{createServer}from ' + json.dumps(str(ROOT / 'frontend/node_modules/vite/dist/node/index.js')) +
            ';import react from ' + json.dumps(str(ROOT / 'frontend/node_modules/@vitejs/plugin-react/dist/index.js')) +
            ';const server=await createServer({configFile:false,root:' + json.dumps(str(frontend)) +
            ',plugins:[react()],optimizeDeps:{exclude:["@matrix-org/matrix-sdk-crypto-wasm"]},cacheDir:' + json.dumps(str(private / 'vite-cache')) +
            ',server:{host:"127.0.0.1",port:' + str(app_port) + ',strictPort:true,fs:{allow:' +
            json.dumps([str(private), str(ROOT / 'frontend/node_modules')]) + '},proxy:{"/api":{target:' + json.dumps(base) +
            ',changeOrigin:true,rewrite:p=>p.replace(/^\\/api/,"")},"/socket.io":{target:' + json.dumps(base) + ',ws:true}}}});await server.listen();')
        evidence.append({'secureTransitionEnvironment': {'matrixEnabled': True, 'matrixInternalUrl': matrix_url,
            'matrixServerName': 'universal-proof.invalid', 'provider': 'owned non-listening loopback socket',
            'secretMaterial': 'ephemeral generated values omitted', 'database': 'fresh disposable SQLite', 'quotaOverrides': False}})
        try:
            for command, name, cwd in [([fixture.PYTHON, '-m', 'uvicorn', 'app.main:socket_app', '--host', '127.0.0.1', '--port', str(api_port), '--no-proxy-headers'], 'api', backend),
                                       (['node', str(vite)], 'vite', frontend)]:
                stream = (private / (name + '.log')).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, start_new_session=True))
            fixture.ready(base + '/ready', processes); fixture.ready(app, processes)
            yield base, app, private
        except Exception:
            for name in ('api', 'vite'):
                path = private / (name + '.log')
                if path.is_file():
                    REPORT.with_name(REPORT.stem + '-secure-transition-' + name + '.log').write_text(path.read_text())
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
            for stream in streams:
                stream.close()
            unavailable.close()
            fixture.observations.append({'ownedFixtureStopped': 'secure-transition', 'processExitCodes': [process.returncode for process in processes]})


def run_secure_transition(browser):
    set_stage('real empty-channel secure preparing transition with unsaved native work')
    with secure_transition_fixture() as (base, app, private):
        source_before = bind_source('secure-transition', private)
        actor = fixture.register(base, 'Security transition owner', 'universal-security')
        workspace = api(base, 'GET', '/workspaces', actor)[0]
        channel = api(base, 'POST', '/channels', actor, {'name': 'Empty security transition', 'workspace_id': workspace['id'], 'member_ids': []})
        context, page, errors, unexpected, writes = fixture.browser_context(browser, app, actor, workspace)
        dialogs, _answer = fixture.install_dialog_answers(page)
        try:
            page.goto(app, wait_until='domcontentloaded')
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            fixture.choose_chat_channel(page, channel['name'])
            card = open_new_document(page, 'Unpublished native page', 'Native unsaved text must leave when the channel becomes encrypted.')
            source = card.get_by_label('Document text', exact=True)
            source_node = live_handle(source)
            before = card_ids(page)
            browse(source); choose_command(source, 'decision')
            decision = latest_card(page, before)
            decision.get_by_role('button', name='Record a decision', exact=True).click()
            decision.get_by_label('Title', exact=True).fill('Unpublished native decision')
            decision.get_by_label('Decision and context', exact=True).fill('This local field must never write under a changed encryption mode.')
            decision_node = live_handle(decision.get_by_label('Decision and context', exact=True))
            assert not api(base, 'GET', '/channels/' + channel['id'] + '/shared-documents', actor)
            assert not api(base, 'GET', '/channels/' + channel['id'] + '/decisions', actor)
            assert not api(base, 'GET', '/channels/' + channel['id'] + '/projects', actor)
            assert not api(base, 'GET', '/messages/channels/' + channel['id'], actor)
            assert not dialogs
            since = len(writes)
            try:
                api(base, 'POST', '/secure/channels/' + channel['id'] + '/enable', actor)
                raise AssertionError('An unavailable loopback provider unexpectedly created a real encrypted room')
            except HTTPError as failed_provider:
                assert failed_provider.code == 503
                detail = json.load(failed_provider)
                assert detail.get('detail') == 'Encrypted messaging service is unavailable', detail
            actual = next(row for row in api(base, 'GET', '/channels', actor) if row['id'] == channel['id'])
            assert actual['secure'] is True and not actual.get('matrix_room_id'), actual
            # Existing production event is a reload hint only. No handler, UI
            # state or response is replaced; /channels remains authoritative.
            with page.expect_response(lambda response: urlsplit(response.url).path == '/api/channels' and response.request.method == 'GET') as refreshed:
                page.evaluate("window.dispatchEvent(new Event('freecollab:channels-updated'))")
            assert refreshed.value.ok
            expect(cards(page)).to_have_count(0, timeout=20000)
            assert not source_node.evaluate('(node)=>node.isConnected')
            assert not decision_node.evaluate('(node)=>node.isConnected')
            expect(page.locator('.fc-slash-commands,.fc-mention-references')).to_have_count(0)
            no_target_writes(writes, since)
            assert not dialogs, 'Authority loss must purge stale native leaves without asking to save them in the new mode'
            try:
                seed_document(base, channel['id'], actor, 'Rejected stale native title', 'Rejected stale native body')
                raise AssertionError('Preparing encryption accepted a native plaintext document write')
            except HTTPError as denied:
                assert denied.code == 409
            fixture.screenshot(page, 'secure-preparing-purged-native-work')
            mark('real_secure_preparing_commit_after_provider_failure_purges_primary_and_retained_native_drafts',
                 authority='real enable POST 503 with durable preparing binding; real GET /channels secure=true', refresh='existing production channels-updated event and actual GET; not a UI-only witness')
            mark('real_secure_preparing_binding_rejects_stale_plaintext_document_creation_with_409')
            assert not errors and not unexpected, (errors, unexpected)
            finish_source_binding('secure-transition', private, source_before)
        except Exception:
            fixture.capture_failure(page, 'universal-secure-transition')
            raise
        finally:
            context.close()


def main():
    global SKIP_INVENTORY_EXECUTION
    parser = argparse.ArgumentParser()
    parser.add_argument('--modes', nargs='+', choices=('native', 'demo', 'modal-writers', 'secure-transition'), default=['native', 'demo', 'modal-writers', 'secure-transition'])
    parser.add_argument('--skip-inventory-execution', action='store_true', help='A focused diagnostic run; never claims full command execution coverage.')
    args = parser.parse_args()
    SKIP_INVENTORY_EXECUTION = args.skip_inventory_execution
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    result = {'status': 'running', 'run': RUN, 'modes': args.modes, 'harness': str(HERE)}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'), headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
            try:
                for mode in args.modes:
                    {'native': run_native, 'demo': run_demo, 'modal-writers': run_modal_writers, 'secure-transition': run_secure_transition}[mode](browser)
            finally:
                browser.close()
        assert not any(row.get('status') == 429 for row in fixture.observations), 'Ordinary retained collaboration exhausted an unchanged API quota'
        result['status'] = 'passed'
    except Exception as failure:
        result.update(status='failed', stage=stage, failure=repr(failure))
        raise
    finally:
        finished = [row for row in evidence if 'sourceBindingFinished' in row]
        bindings = [row for row in evidence if 'sourceBinding' in row]
        final_checkout = hashes(ROOT)
        result.update(checks=checks, evidence=evidence, observations=fixture.observations, screenshots=fixture.screenshots, sourceSnapshots=fixture.snapshots,
                      currentSourceVerified=len(finished) == len(args.modes) and len(bindings) == len(args.modes)
                      and all(row['checkoutMatchedAtFinish'] for row in finished)
                      and all(row['copiedSourceHashes'] == final_checkout for row in bindings))
        REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
        print(json.dumps({'status': result['status'], 'checks': len(checks), 'report': str(REPORT)}), flush=True)


if __name__ == '__main__':
    main()
