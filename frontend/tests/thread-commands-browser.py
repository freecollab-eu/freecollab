#!/usr/bin/env python3
"""Thread commands in actual disposable native and example installations.

All interaction uses the production App/Chat/SlashCommands, real API-issued
sessions, SQLite, Yjs documents and unchanged demo handlers. No business
responses, product state or event handlers are replaced. Short mobile browser
viewports exercise available space; this is not physical OS-keyboard evidence.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import re
import signal
import subprocess
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from ui_navigation import choose_chat_channel, reveal_message_actions, capture_work_card, close_work_card


ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(os.getenv('THREAD_COMMANDS_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/thread-commands-20261024/thread-commands-browser.json')))
spec = importlib.util.spec_from_file_location('thread_command_fixtures', Path(__file__).with_name('commands-references-browser.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.REPORT = REPORT

THREAD = re.compile(r'^(Thread|Tråd)$')
REPLY = re.compile(r'^(Reply in thread|Svara i tråd)$')
CLOSE_THREAD = re.compile(r'^(Close thread|Stäng tråden)$')
SEND_REPLY = re.compile(r'^(Send reply|Skicka svar)$')
ALL_FEATURES = re.compile(r'^(Show all features|Visa alla funktioner)$')
CLOSE_WORK = re.compile(r'^(Close this work|Stäng arbetet)$')
TASK_TITLE = re.compile(r'^(Task title|Uppgiftens titel)$')
TASK_SAVE = re.compile(r'^(Save |Spara )task$')
TASK_CANCEL = re.compile(r'^(Cancel|Avbryt)$')

checks, observations, screenshots, snapshots = {}, [], {}, {}
stage = 'startup'


def mark(name):
    checks[name] = True
    print('PASS: ' + name, flush=True)


def snapshot(private, label, fixture_mode):
    values = m.source_hashes(private)
    name = 'frontend/tests/thread-commands-browser.py'
    values[name] = hashlib.sha256((private / name).read_bytes()).hexdigest()
    snapshots[label] = values
    m.snapshots.pop(fixture_mode, None)


def capture(page, label):
    path = REPORT.with_name(REPORT.stem + '-' + label + '.png')
    page.screenshot(path=str(path), full_page=True)
    screenshots[label] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def thread(page):
    panel = page.get_by_role('dialog', name=THREAD, exact=True)
    expect(panel).to_be_visible()
    return panel


def reply(page):
    return thread(page).get_by_label(REPLY, exact=True)


def open_thread(page, parent):
    row = page.locator('#message-' + parent['id'])
    expect(row).to_be_visible()
    if row.locator('.fc-chat-thread-link').count():
        row.locator('.fc-chat-thread-link').click()
    else:
        reveal_message_actions(row)
        row.get_by_role('button', name=REPLY, exact=True).click()
    panel = thread(page)
    expect(panel.locator('.fc-chat-thread-parent')).to_contain_text(parent['content'])
    return panel


def palette(page):
    result = thread(page).locator('.fc-slash-commands')
    expect(result).to_be_visible()
    expect(page.locator('.fc-chat-composer-row > .fc-slash-commands')).to_have_count(0)
    return result


def command_option(panel, identifier):
    list_id = panel.get_by_role('listbox').get_attribute('id')
    option = panel.locator('[role="option"][id=' + json.dumps(list_id + '-' + identifier) + ']')
    expect(option).to_have_count(1)
    return option


def work(page, kind):
    result = capture_work_card(page).locator('.fc-conversation-work[data-work-kind=' + json.dumps(kind) + ']')
    expect(result).to_be_visible()
    return result


def close_work(page, parent, text=''):
    close_work_card(page)
    panel = thread(page)
    expect(panel.locator('.fc-chat-thread-parent')).to_contain_text(parent['content'])
    expect(panel.get_by_label(REPLY, exact=True)).to_have_value(text)
    expect(panel.get_by_label(REPLY, exact=True)).to_be_focused()
    return panel


def assert_no_delivery_or_write(writes, since):
    assert m.no_resource_writes(writes, since), 'A command or pointer gesture saved work, called a provider or delivered a message'


def settle(page):
    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')


def bounds(page, item, minimum_height=0):
    expect(item).to_be_visible()
    geometry = item.evaluate('''node=>{
      const box=node.getBoundingClientRect(),v=visualViewport;
      return {left:box.left,right:box.right,top:box.top,bottom:box.bottom,height:box.height,
        viewportLeft:v?.offsetLeft||0,viewportTop:v?.offsetTop||0,
        viewportWidth:v?.width||innerWidth,viewportHeight:v?.height||innerHeight};
    }''')
    assert geometry['left'] >= geometry['viewportLeft'] - 1, geometry
    assert geometry['right'] <= geometry['viewportLeft'] + geometry['viewportWidth'] + 1, geometry
    assert geometry['top'] >= geometry['viewportTop'] - 1, geometry
    assert geometry['bottom'] <= geometry['viewportTop'] + geometry['viewportHeight'] + 1, geometry
    assert geometry['height'] >= minimum_height, geometry
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), geometry
    return geometry


def touch_context(browser, app, user=None, workspace=None):
    context = browser.new_context(viewport={'width': 390, 'height': 850}, locale='sv-SE',
                                  is_mobile=True, has_touch=True, reduced_motion='reduce')
    if user:
        context.add_init_script('if(!sessionStorage.getItem("threadCommandFixtureSeeded")){' +
            'localStorage.setItem("freecollab_token",' + json.dumps(user['access_token']) + ');' +
            'localStorage.setItem("freecollab_user_id",' + json.dumps(user['user_id']) + ');' +
            'localStorage.setItem(' + json.dumps('freecollab.workspace:' + user['user_id']) + ',' + json.dumps(workspace['id']) + ');' +
            'sessionStorage.setItem("threadCommandFixtureSeeded","1");}')
    errors, external, writes = [], [], []
    def boundary(route):
        parsed = urlsplit(route.request.url)
        if parsed.scheme not in ('blob', 'data') and parsed.netloc != urlsplit(app).netloc:
            external.append(parsed.scheme + '://' + parsed.netloc + parsed.path)
            route.abort()
        else:
            route.continue_()
    context.route('**/*', boundary)
    page = context.new_page()
    page.set_default_timeout(30000)
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('request', lambda request: writes.append({'method': request.method, 'path': urlsplit(request.url).path})
            if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') else None)
    page.on('response', lambda response: observations.append({'rate_limit': 429, 'path': urlsplit(response.url).path}) if response.status == 429 else None)
    return context, page, errors, external, writes


def seed_document(base, channel, owner, title):
    # Use the real shipped Yjs library to create the same supported title/body
    # document structure as the product; never seed an invalid opaque update.
    source = '''import * as Y from './frontend/node_modules/yjs/dist/yjs.mjs';
      import {readFileSync} from 'node:fs';
      const {title}=JSON.parse(readFileSync(0,'utf8')),d=new Y.Doc();
      d.getText('title').insert(0,title);d.getText('body').insert(0,'Actual native thread reference material.');
      process.stdout.write(Buffer.from(Y.encodeStateAsUpdate(d)).toString('base64'));'''
    update = subprocess.check_output(['node', '--input-type=module', '-e', source], cwd=ROOT,
                                     input=json.dumps({'title': title}).encode()).decode()
    return m.api(base, 'POST', '/channels/' + channel['id'] + '/shared-documents', owner,
                 {'id': 'thread-document-' + m.RUN, 'update_id': 'thread-initial-' + m.RUN, 'update_base64': update, 'title': title})


def native(browser):
    global stage
    stage = 'native setup'
    m.stage = stage
    with m.fixture('native') as (base, app, private):
        snapshot(private, 'native', 'native')
        owner = m.register(base, 'Maja Berg', 'thread-commands-owner')
        peer = m.register(base, 'Eva Lind', 'thread-commands-peer')
        workspace = m.api(base, 'GET', '/workspaces', owner)[0]
        invitation = m.api(base, 'POST', '/workspaces/' + workspace['id'] + '/invites', owner, {'role': 'member'})
        m.api(base, 'POST', '/workspaces/invites/accept', peer, {'token': invitation['token']})
        channel = m.api(base, 'POST', '/channels', owner, {'name': 'Thread command studio', 'workspace_id': workspace['id'], 'member_ids': [peer['user_id']]})
        other = m.api(base, 'POST', '/channels', owner, {'name': 'Other thread channel', 'workspace_id': workspace['id'], 'member_ids': [peer['user_id']]})
        parent = m.api(base, 'POST', '/messages/channels/' + channel['id'], owner, {'content': 'Shall we open up to the courtyard?'})
        second_parent = m.api(base, 'POST', '/messages/channels/' + channel['id'], owner, {'content': 'An independent discussion keeps its own draft.'})
        for item in (parent, second_parent):
            m.api(base, 'POST', '/threads/messages/' + item['id'] + '/reply', peer, {'content': 'A real initial reply for ' + item['id']})
        document = seed_document(base, channel, owner, 'Courtyard material ' + m.RUN)
        project = m.api(base, 'POST', '/channels/' + channel['id'] + '/projects', owner,
                        {'id': 'thread-project-' + m.RUN, 'expected_revision': 0, 'archived': False,
                         'fields': {'title': 'Courtyard steps', 'description': '', 'color': 'sage'}})
        m.api(base, 'PUT', '/preferences/language', owner, {'locale': 'sv'})
        context, page, errors, external, writes = touch_context(browser, app, owner, workspace)
        try:
            page.goto(app, wait_until='domcontentloaded')
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            expect(page.locator('html')).to_have_attribute('lang', 'sv')
            choose_chat_channel(page, channel['name'])
            main = page.locator('.fc-chat-composer textarea')
            main_draft = 'Ett separat huvudutkast ska inte ändras av en tråd.'
            main.fill(main_draft)
            open_thread(page, parent)

            stage = 'typed native thread docs uses the real palette and explicit keyboard opening'
            m.stage = stage
            since = len(writes)
            field = reply(page)
            field.fill('/docs')
            index = palette(page)
            option = command_option(index, 'document')
            expect(field).to_be_focused()
            expect(field).to_have_attribute('aria-expanded', 'true')
            expect(field).to_have_attribute('aria-controls', index.get_by_role('listbox').get_attribute('id'))
            expect(thread(page).get_by_role('button', name=SEND_REPLY, exact=True)).to_be_disabled()
            expect(option).to_have_attribute('aria-selected', 'true')
            capture(page, 'native-swedish390-thread-docs')
            field.press('Enter')
            docs = work(page, 'documents')
            expect(docs.locator('.fc-shared-document-card[data-document-id=' + json.dumps(document['id']) + ']')).to_be_visible()
            expect(docs.locator('[id$="shared-document-new-title"]')).to_have_count(0)
            assert_no_delivery_or_write(writes, since)
            expect(main).to_have_value(main_draft)
            close_work(page, parent)
            mark('native_thread_docs_keyboard_opens_same_document_list_without_delivery_and_returns_to_exact_thread')

            stage = 'native thread touch selection survives actual outside-pointer handling'
            m.stage = stage
            since = len(writes)
            reply(page).fill('/docs')
            index = palette(page)
            command_option(index, 'document').tap()
            work(page, 'documents')
            assert_no_delivery_or_write(writes, since)
            close_work(page, parent)
            expect(main).to_have_value(main_draft)
            mark('native_touch_selection_does_not_close_thread_palette_before_command_activation')

            stage = 'browse from thread preserves ordinary unsent draft separately from main'
            m.stage = stage
            ordinary = 'Ett oskickat trådsvar medan jag bläddrar bland funktionerna.'
            reply(page).fill(ordinary)
            since = len(writes)
            thread(page).get_by_role('button', name=ALL_FEATURES, exact=True).tap()
            index = palette(page)
            search = index.get_by_role('combobox')
            expect(search).to_be_focused()
            command_option(index, 'document').tap()
            work(page, 'documents')
            expect(main).to_have_value(main_draft)
            assert_no_delivery_or_write(writes, since)
            close_work(page, parent, ordinary)
            mark('thread_browse_preserves_ordinary_thread_draft_main_draft_and_exact_parent_return')

            stage = 'new main reference intent wins over automatic return to an older thread'
            m.stage = stage
            since = len(writes)
            thread(page).get_by_role('button', name=ALL_FEATURES, exact=True).tap()
            command_option(palette(page), 'document').tap()
            docs = work(page, 'documents')
            page.locator('.fc-chat-composer-area').get_by_role('button', name=re.compile(r'^(Mention or reference|Omnämn eller referera)$'), exact=True).tap()
            reference_picker = page.locator('.fc-mention-references')
            expect(reference_picker).to_be_visible()
            # Use a different person from the later thread assignee, so those
            # commands cannot accidentally read the main composer's bindings.
            m.reference_option(page, 'person', owner['user_id']).tap()
            expect(reference_picker).to_have_count(0)
            main_draft = main.input_value()
            assert '@' in main_draft and 'Maja' in main_draft
            close_work_card(page, docs)
            expect(page.get_by_role('dialog', name=THREAD, exact=True)).to_have_count(0)
            expect(main).to_have_value(main_draft)
            expect(main).to_be_focused()
            assert_no_delivery_or_write(writes, since)
            open_thread(page, parent)
            expect(reply(page)).to_have_value(ordinary)
            mark('new_main_at_reference_intent_keeps_main_focus_without_reopening_old_thread_or_erasing_its_draft')

            stage = 'native thread task arguments and references open review before explicit save'
            m.stage = stage
            task_title = 'Granska virkesprovet ' + m.RUN
            field = reply(page)
            field.fill('/task ' + task_title + ' ')
            since = len(writes)
            m.choose_reference(page, field, 'person', peer['user_id'], 'Eva', keyboard=True)
            expect(palette(page)).to_be_visible()
            field.press('Enter')
            tasks = work(page, 'tasks')
            expect(tasks.get_by_label(TASK_TITLE, exact=True)).to_have_value(task_title)
            expect(tasks.locator('.fc-conversation-work-source')).to_contain_text(parent['content'])
            assert_no_delivery_or_write(writes, since)
            assert not m.api(base, 'GET', '/channels/' + channel['id'] + '/projects/' + project['id'] + '/tasks', owner)['items']
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/channels/' + channel['id'] + '/projects/' + project['id'] + '/tasks') as saved, page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/channels/' + channel['id'] + '/conversation-work') as linked:
                tasks.get_by_role('button', name=TASK_SAVE, exact=True).click()
            assert saved.value.ok
            assert linked.value.ok
            saved_rows = m.api(base, 'GET', '/channels/' + channel['id'] + '/projects/' + project['id'] + '/tasks', owner)['items']
            actual = next(row for row in saved_rows if row['fields']['title'] == task_title)
            assert actual['fields']['assignee_user_id'] == peer['user_id'], 'Task command must use thread reference identities'
            refs = m.api(base, 'GET', '/channels/' + channel['id'] + '/conversation-work', owner)['items']
            assert any(row['resource_kind'] == 'task' and row['resource_id'] == actual['id'] and row['source_message_id'] == parent['id'] for row in refs)
            close_work(page, parent)
            expect(main).to_have_value(main_draft)
            mark('thread_task_arguments_keep_thread_assignee_and_parent_source_and_create_only_on_explicit_save')

            stage = 'ordinary thread references still use the genuine recipient IDs'
            m.stage = stage
            field = reply(page)
            field.fill('Hej ')
            m.choose_reference(page, field, 'person', peer['user_id'], 'Eva', keyboard=True)
            value = field.input_value()
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/threads/messages/' + parent['id'] + '/reply') as sent:
                field.press('Enter')
            assert sent.value.ok
            record = next(row for row in m.api(base, 'GET', '/threads/messages/' + parent['id'], owner)['replies'] if row['content'] == value)
            m.assert_reference_bindings(record, [('person', peer['user_id'])])
            assert record['mentions'] == [peer['user_id']]
            expect(field).to_have_value('')
            mark('thread_at_reference_keyboard_selection_and_real_message_delivery_keep_exact_recipient_binding')

            stage = 'unknown thread commands cannot send until literal opt-out'
            m.stage = stage
            since = len(writes)
            field.fill('/inte-ett-kommando')
            index = palette(page)
            expect(index.get_by_role('option')).to_have_count(0)
            field.press('Enter')
            expect(index).to_be_visible()
            expect(field).to_have_value('/inte-ett-kommando')
            assert_no_delivery_or_write(writes, since)
            field.press('Escape')
            expect(page.locator('.fc-slash-commands')).to_have_count(0)
            expect(thread(page)).to_be_visible()
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/threads/messages/' + parent['id'] + '/reply') as literal:
                field.press('Enter')
            assert literal.value.ok
            records = m.api(base, 'GET', '/threads/messages/' + parent['id'], owner)['replies']
            assert sum(row['content'] == '/inte-ett-kommando' for row in records) == 1
            assert not any(row['content'] == '/docs' for row in records)
            mark('unknown_thread_command_is_guarded_until_escape_explicitly_allows_literal_reply')

            stage = 'thread docs retains an independent dirty task and only its own cancel asks to discard'
            m.stage = stage
            field.fill('/task Ett osparat granskningsutkast')
            palette(page)
            field.press('Enter')
            tasks = work(page, 'tasks')
            task_input = tasks.get_by_label(TASK_TITLE, exact=True)
            expect(task_input).to_have_value('Ett osparat granskningsutkast')
            kept_editor = task_input.element_handle()
            task_card = capture_work_card(page, tasks)
            open_thread(page, parent)
            field = reply(page)
            field.fill('/docs')
            index = palette(page)
            dialogs = []
            def stay(dialog):
                dialogs.append(dialog.message)
                dialog.dismiss()
            page.on('dialog', stay)
            since = len(writes)
            field.press('Enter')
            expect(index).to_have_count(0)
            docs = work(page, 'documents')
            assert capture_work_card(page, docs).get_attribute('data-collaboration-work-id') != task_card.get_attribute('data-collaboration-work-id')
            assert not dialogs, 'A new Documents card must not discard the actual dirty Task card'
            expect(task_input).to_have_value('Ett osparat granskningsutkast')
            assert task_input.evaluate('(node,original)=>node===original&&original.isConnected', kept_editor)
            expect(main).to_have_value(main_draft)
            assert_no_delivery_or_write(writes, since)
            close_work(page, parent)
            assert not dialogs, 'Closing the clean Documents sibling must not ask to discard the Task'
            expect(task_input).to_have_value('Ett osparat granskningsutkast')
            assert task_input.evaluate('(node,original)=>node===original&&original.isConnected', kept_editor)
            thread(page).get_by_role('button', name=CLOSE_THREAD, exact=True).click()
            page.remove_listener('dialog', stay)
            accepted = []
            def discard_task(dialog):
                accepted.append(dialog.message); dialog.accept()
            page.once('dialog', discard_task)
            tasks.get_by_role('button', name=TASK_CANCEL, exact=True).click()
            assert len(accepted) == 1 and ('spar' in accepted[0].lower() or 'saved' in accepted[0].lower()), accepted
            # A deliberately closed newer thread must not return when its Task closes.
            close_work_card(page, tasks)
            expect(page.get_by_role('dialog', name=THREAD, exact=True)).to_have_count(0)
            open_thread(page, parent)
            expect(reply(page)).to_have_value('')
            reply(page).fill('')
            mark('thread_command_retains_both_work_cards_and_own_task_cancel_keeps_deliberate_thread_close')

            stage = 'thread command inventory remains usable in narrow short mobile viewports'
            m.stage = stage
            for width, height in ((390, 850), (320, 850), (320, 520)):
                page.set_viewport_size({'width': width, 'height': height})
                field = reply(page)
                # Escape in the preceding viewport is an intentional literal
                # opt-out; make a genuine edit rather than fill the same '/'.
                field.fill('')
                field.fill('/')
                index = palette(page)
                settle(page)
                field.press('End')
                settle(page)
                active_id = field.get_attribute('aria-activedescendant')
                active = index.locator('[role="option"][id=' + json.dumps(active_id) + ']')
                expect(active).to_have_attribute('aria-selected', 'true')
                assert active.evaluate('''node=>{const r=node.getBoundingClientRect(),b=node.closest('[role="listbox"]').getBoundingClientRect();return r.top>=b.top-2&&r.bottom<=b.bottom+2}''')
                observations.append({'nativeThreadPaletteGeometry': {'width': width, 'height': height, 'palette': bounds(page, index), 'input': bounds(page, field, 44)}})
                capture(page, 'native-swedish-' + str(width) + '-' + str(height))
                field.press('Escape')
            page.set_viewport_size({'width': 390, 'height': 850})
            mark('thread_slash_palette_active_option_and_input_fit_swedish390_320_and_short_mobile_viewports')

            stage = 'closing or changing thread cannot leave old slash activation on another surface'
            m.stage = stage
            field = reply(page)
            field.fill('/docs')
            palette(page)
            thread(page).get_by_role('button', name=CLOSE_THREAD, exact=True).click()
            expect(page.locator('.fc-slash-commands')).to_have_count(0)
            expect(main).to_have_value(main_draft)
            choose_chat_channel(page, other['name'])
            expect(main).to_have_value('')
            expect(page.locator('.fc-slash-commands')).to_have_count(0)
            choose_chat_channel(page, channel['name'])
            expect(main).to_have_value(main_draft)
            open_thread(page, second_parent)
            expect(reply(page)).to_have_value('')
            expect(page.locator('.fc-slash-commands')).to_have_count(0)
            reply(page).fill('/docs')
            palette(page)
            page.evaluate('''async ({session,workspaceId})=>{
              const {api}=await import('/src/lib/api.ts');
              localStorage.setItem('freecollab.workspace:'+session.user_id,workspaceId);
              api.applySession(session);
            }''', {'session': peer, 'workspaceId': workspace['id']})
            expect(page.get_by_role('dialog', name=THREAD, exact=True)).to_have_count(0)
            expect(page.locator('.fc-slash-commands')).to_have_count(0)
            expect(main).not_to_have_value(main_draft)
            assert not errors and not external, [errors, external]
            mark('thread_and_account_changes_scrub_old_command_activation_without_changing_other_drafts')
        except Exception:
            capture(page, 'native-failure')
            raise
        finally:
            context.close()


def demo(browser):
    global stage
    stage = 'actual isolated demo thread command setup'
    m.stage = stage
    with m.fixture('demo') as (base, app, private):
        snapshot(private, 'demo', 'demo')
        context, page, errors, external, writes = touch_context(browser, app)
        visit = None
        try:
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/demo/session') as response:
                page.goto(app + '/product-demo/?locale=sv', wait_until='domcontentloaded')
            assert response.value.ok
            visit = response.value.json()
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            expect(page.locator('html')).to_have_attribute('lang', 'sv')
            history = m.api(base, 'GET', '/messages/channels/' + visit['channel_id'], visit['session'])
            parent = next(row for row in history if not row.get('parent_id') and row.get('thread_count', 0))
            open_thread(page, parent)
            for width in (390, 320):
                stage = 'actual Swedish demo thread docs touch at ' + str(width)
                m.stage = stage
                page.set_viewport_size({'width': width, 'height': 850})
                field = reply(page)
                field.fill('/docs')
                since = len(writes)
                index = palette(page)
                expect(thread(page).get_by_role('button', name=SEND_REPLY, exact=True)).to_be_disabled()
                bounds(page, index)
                capture(page, 'actual-demo-swedish-thread-docs-' + str(width))
                command_option(index, 'document').tap()
                docs = work(page, 'documents')
                expect(docs.locator('.fc-shared-document-card').first).to_be_visible()
                expect(docs.locator('[id$="shared-document-new-title"]')).to_have_count(0)
                assert_no_delivery_or_write(writes, since)
                close_work(page, parent)
            records = m.api(base, 'GET', '/threads/messages/' + parent['id'], visit['session'])['replies']
            assert not any(row['content'] == '/docs' for row in records)
            assert not errors and not external, [errors, external]
            mark('actual_example_installation_uses_same_thread_docs_touch_list_and_return_at_swedish390_and320')
        except Exception:
            capture(page, 'demo-failure')
            raise
        finally:
            if visit:
                m.api(base, 'DELETE', '/demo/session', visit['session'])
                observations.append({'ownedDemoVisitRevoked': 204})
            context.close()


def main():
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt('Owned thread-command proof interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    complete = False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or '/usr/bin/chromium', headless=True, args=['--no-sandbox'])
            try:
                native(browser)
                demo(browser)
                complete = True
            finally:
                browser.close()
    finally:
        mismatches = {mode: [name for name, digest in values.items() if not (ROOT / name).is_file() or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest] for mode, values in snapshots.items()}
        report = {'ok': complete and all(not rows for rows in mismatches.values()), 'scope': __doc__, 'stage': stage,
                  'checks': checks, 'source_sha256': snapshots, 'mismatchedCurrentSources': mismatches,
                  'observations': observations + m.observations, 'screenshots': screenshots,
                  'cleanup': 'Only owned loopback API/Vite/browser contexts, source snapshots, SQLite, files and ephemeral fixture keys were used and removed.'}
        REPORT.write_text(json.dumps(report, indent=2) + '\n')
    assert all(not rows for rows in mismatches.values()), 'Product source changed during thread command verification'
    assert not any(row.get('rate_limit') == 429 for row in observations), 'Thread command proof exceeded unchanged product limits'
    print(json.dumps({'ok': report['ok'], 'checks': checks, 'sourceCounts': {mode: len(values) for mode, values in snapshots.items()}, 'mismatches': mismatches}), flush=True)


if __name__ == '__main__':
    main()
