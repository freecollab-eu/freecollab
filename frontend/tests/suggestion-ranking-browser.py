#!/usr/bin/env python3
"""Contextual choices in the actual App with owned native and demo installations.

Uses real API-issued fixture accounts, native persistence, Vite and Chromium.
No response, permission decision, product handler or suggestion is mocked.
Only loopback services and disposable records are used; installed app review
never invokes its registered recipient. Browser storage is observed for the
example product's existing memory-session boundary.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import re
import signal
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from ui_navigation import choose_chat_channel, close_work_card, conversation_work_region

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(os.getenv('SUGGESTION_RANKING_REPORT', str(ROOT.parent / '.freecollab/proof-results/suggestion-ranking-browser.json')))
spec = importlib.util.spec_from_file_location('ranking_product_fixtures', Path(__file__).with_name('commands-references-browser.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.REPORT = REPORT
SOURCES = [
    'frontend/src/components/Chat.tsx', 'frontend/src/components/CollaborationInput.tsx',
    'frontend/src/components/SlashCommands.tsx', 'frontend/src/components/MentionReferences.tsx',
    'frontend/src/lib/suggestion-ranking.ts', 'frontend/src/lib/slash-commands.ts',
    'frontend/src/lib/useComposerReferences.ts', 'frontend/src/lib/composer-commands.ts',
    'frontend/src/lib/message-references.ts', 'frontend/src/lib/message-references-api.ts',
    'frontend/src/i18n/contextual-ranking.sv.ts', 'backend/app/api/mentions.py',
    'backend/app/services/message_references.py', 'frontend/tests/ui_navigation.py',
    'frontend/tests/commands-references-browser.py', 'frontend/tests/suggestion-ranking-browser.py',
]
checks, snapshots, observations, screenshots = {}, {}, [], {}
stage = 'startup'


def mark(name):
    checks[name] = True
    print('PASS: ' + name, flush=True)


def snapshot(private, mode):
    snapshots[mode] = {name: hashlib.sha256((private / name).read_bytes()).hexdigest() for name in SOURCES}


def capture(page, name):
    path = REPORT.with_name(REPORT.stem + '-' + name + '.png')
    page.screenshot(path=str(path), full_page=True)
    screenshots[name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def menu(page):
    result = page.locator('.fc-slash-commands').filter(has=page.get_by_role('listbox'))
    expect(result).to_have_count(1)
    expect(result).to_be_visible()
    return result


def choices(panel):
    identifier = panel.get_by_role('listbox').get_attribute('id')
    return panel.get_by_role('option').evaluate_all('(rows,prefix)=>rows.map(row=>row.id.slice(prefix.length+1))', identifier)


def slash(page, field, text='/'):
    field.fill('')
    field.fill(text)
    return menu(page)


def choose_command(page, field, command):
    slash(page, field, command)
    field.press('Enter')
    expect(page.locator('.fc-slash-commands')).to_have_count(0)


def reference_rows(page, field, text='@'):
    field.fill('')
    field.fill(text)
    panel = menu(page)
    expect(panel).to_have_attribute('class', re.compile('fc-mention-references'))
    expect(panel.get_by_role('listbox')).to_have_attribute('aria-busy', 'false')
    return panel


def native(browser):
    global stage
    with m.fixture('native') as (base, app, private):
        snapshot(private, 'native')
        owner = m.register(base, 'Ranking owner', 'ranking-owner')
        first = m.register(base, 'Alex Rivera', 'ranking-first')
        second = m.register(base, 'Alex Rivera', 'ranking-second')
        workspace = m.api(base, 'GET', '/workspaces', owner)[0]
        for person in (first, second):
            invite = m.api(base, 'POST', '/workspaces/' + workspace['id'] + '/invites', owner, {'role': 'member'})
            m.api(base, 'POST', '/workspaces/invites/accept', person, {'token': invite['token']})
        channel = m.api(base, 'POST', '/channels', owner, {'name': 'Ranking studio', 'workspace_id': workspace['id'], 'member_ids': [first['user_id'], second['user_id']]})
        other = m.api(base, 'POST', '/channels', owner, {'name': 'Other ranking studio', 'workspace_id': workspace['id'], 'member_ids': [first['user_id']]})
        project = m.api(base, 'POST', '/channels/' + channel['id'] + '/projects', owner, {'id': 'ranking-project-' + m.RUN, 'expected_revision': 0, 'archived': False, 'fields': {'title': 'Ranking work', 'description': '', 'color': 'sage'}})
        m.api(base, 'POST', '/channels/' + channel['id'] + '/projects/' + project['id'] + '/tasks', owner, {'id': 'ranking-task-' + m.RUN, 'expected_revision': 0, 'archived': False, 'fields': {'title': 'Review materials', 'description': '', 'status': 'todo', 'priority': 'normal', 'assignee_user_id': None, 'due_date': None, 'checklist': [], 'file_ids': [], 'decision_ids': []}})
        m.upload(base, channel, owner)
        m.api(base, 'POST', '/mentions/channels/' + channel['id'] + '/groups', owner, {'name': 'Review team', 'member_ids': [first['user_id'], second['user_id']]})
        installation = m.api(base, 'POST', '/workspaces/' + workspace['id'] + '/apps', owner, {'manifest': {'app_id': 'ranking.receiver', 'name': 'Ranking receiver', 'version': '1.0.0', 'description': 'Owned review-only fixture', 'scopes': ['commands:invoke'], 'commands': [{'name': 'review', 'description': 'Review explicit text', 'arguments': [{'name': 'text', 'label': 'Reviewed text', 'required': True, 'max_length': 2000}]}]}, 'channel_ids': [channel['id']], 'endpoint': 'https://8.8.8.8/freecollab-ranking-fixture', 'authorization': ''})['installation']
        context, page, errors, external, writes = m.browser_context(browser, app, owner, workspace)
        try:
            page.goto(app, wait_until='domcontentloaded')
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            choose_chat_channel(page, channel['name'])
            field = page.locator('.fc-chat-composer textarea')
            stage = m.stage = 'full permitted inventory and accepted-choice recency'
            panel = slash(page, field)
            app_id = 'app:' + installation['id'] + ':review'
            expect(panel.locator('[role="option"][id$=' + json.dumps('-' + app_id) + ']')).to_have_count(1)
            inventory = choices(panel)
            assert len(inventory) >= 30 and len(inventory) == len(set(inventory))
            assert inventory[0] == 'document'
            field.press('End')
            expect(panel.get_by_role('option').last).to_have_attribute('aria-selected', 'true')
            field.press('Escape')
            choose_command(page, field, '/task')
            tasks = conversation_work_region(page, kind='tasks')
            close_work_card(page, tasks)
            panel = slash(page, field)
            assert choices(panel)[0] == 'task'
            assert sorted(choices(panel)) == sorted(inventory)
            capture(page, 'english-recent')
            panel = slash(page, field, '/document')
            assert choices(panel)[0] == 'document'
            field.press('Enter')
            documents = conversation_work_region(page, kind='documents')
            mark('exact_command_precedes_recent_choice_and_every_permitted_feature_including_installed_commands_remains_reachable')

            stage = m.stage = 'actual document writer context precedes session recency'
            choose_command(page, field, '/task')
            close_work_card(page, conversation_work_region(page, kind='tasks'))
            documents.get_by_role('button', name='New shared document', exact=True).click()
            title = documents.get_by_label('Document title', exact=True)
            body = documents.get_by_label('Document text', exact=True)
            title.fill('Alex')
            panel = slash(page, body)
            assert choices(panel)[0] == 'document'
            assert sorted(choices(panel)) == sorted(inventory)
            body.press('Escape')
            body.fill('Real shared material for reference ranking.')
            documents.get_by_role('button', name='Create document', exact=True).click()
            expect(documents.get_by_label('Title', exact=True)).to_have_value('Alex')
            expect(documents.get_by_role('status')).to_contain_text('All edits synchronized')
            document = next(row for row in m.api(base, 'GET', '/channels/' + channel['id'] + '/shared-documents', owner) if row['title'] == 'Alex')
            close_work_card(page, documents)
            mark('document_field_context_precedes_recent_task_without_removing_any_choices')

            stage = m.stage = 'exact reference match and all real reference kinds'
            panel = reference_rows(page, field, '@Alex')
            expect(panel.get_by_role('option').first).to_have_attribute('data-reference-kind', 'document')
            expect(panel.get_by_role('option').first).to_have_attribute('data-reference-id', document['id'])
            field.press('Escape')
            panel = reference_rows(page, field)
            kinds = set(panel.get_by_role('option').evaluate_all('rows=>rows.map(row=>row.dataset.referenceKind)'))
            assert kinds == {'person', 'group', 'document', 'task', 'file', 'channel'}
            field.press('Escape')
            panel = reference_rows(page, field, '@Alex Rivera')
            second_option = panel.locator('[role="option"][data-reference-kind="person"][data-reference-id=' + json.dumps(second['user_id']) + ']')
            expect(second_option).to_be_visible()
            second_option.click()
            expect(page.locator('.fc-mention-references')).to_have_count(0)
            field.press('Enter')
            expect(field).to_have_value('')
            delivered = next(row for row in m.api(base, 'GET', '/messages/channels/' + channel['id'], owner) if row['content'].strip() == '@Alex Rivera')
            m.assert_reference_bindings(delivered, [('person', second['user_id'])])
            panel = reference_rows(page, field, '@Alex Rivera')
            expect(panel.get_by_role('option').first).to_have_attribute('data-reference-id', second['user_id'])
            field.press('Escape')
            mark('exact_reference_precedes_context_all_six_kinds_remain_and_duplicate_names_keep_the_explicit_durable_person_id')

            stage = m.stage = 'registered app arguments remain literal and require review'
            literal = 'literal @Alex /task https://example.test/?x=1'
            choose_command(page, field, '/ranking.receiver:review text=' + json.dumps(literal))
            expect(page.get_by_role('textbox', name='Reviewed text', exact=True)).to_have_value(literal)
            expect(page.get_by_role('button', name='Approve and run command', exact=True)).to_be_disabled()
            assert m.api(base, 'GET', '/workspaces/' + workspace['id'] + '/apps/' + installation['id'] + '/jobs', owner)['jobs'] == []
            page.once('dialog', lambda dialog: dialog.accept())
            close_work_card(page)
            mark('installed_command_identity_and_literal_arguments_survive_ranking_without_provider_invocation')

            stage = m.stage = 'channel and authenticated actor isolate recent choices'
            choose_chat_channel(page, other['name'])
            assert choices(slash(page, field))[0] == 'document'
            field.press('Escape')
            choose_chat_channel(page, channel['name'])
            assert choices(slash(page, field))[0] == 'task'
            field.press('Escape')
            catalogue_path = '/api/workspaces/' + workspace['id'] + '/channels/' + channel['id'] + '/apps/commands'
            # Chat mounts before its separate installed-command read finishes.
            # Verify this actor's real authority response before testing its full UI inventory.
            with page.expect_response(lambda response: response.request.method == 'GET'
                    and urlsplit(response.url).path == catalogue_path
                    and response.request.headers.get('authorization') == 'Bearer ' + first['access_token']) as catalogue_response:
                page.evaluate('''async ({session,workspace})=>{const {api}=await import('/src/lib/api.ts');localStorage.setItem('freecollab.workspace:'+session.user_id,workspace);api.applySession(session);}''', {'session': first, 'workspace': workspace['id']})
                expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
                choose_chat_channel(page, channel['name'])
            assert catalogue_response.value.ok, 'The current member could not read its installed-command authority'
            member_commands = catalogue_response.value.json()['commands']
            member_command = next((row for row in member_commands if row['key'] == installation['id'] + ':review'), None)
            assert member_command and member_command['installation_id'] == installation['id'] and member_command['generation'] == installation['generation'], 'The actual member catalogue did not authorize the expected installation and generation'
            panel = slash(page, field)
            expect(panel.locator('[role="option"][id$=' + json.dumps('-' + app_id) + ']')).to_have_count(1)
            member_inventory = choices(panel)
            assert member_inventory[0] == 'document'
            assert not {'admin', 'data', 'get-started'} & set(member_inventory)
            assert {'document', 'task', app_id} <= set(member_inventory)
            field.press('Escape')
            mark('channel_recent_choices_are_isolated_and_real_actor_change_clears_history_and_rechecks_permissions')

            stage = m.stage = 'Swedish narrow-screen choices remain keyboard reachable'
            m.api(base, 'PUT', '/preferences/language', first, {'locale': 'sv'})
            page.reload(wait_until='domcontentloaded')
            expect(page.locator('html')).to_have_attribute('lang', 'sv')
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            choose_chat_channel(page, channel['name'])
            page.set_viewport_size({'width': 320, 'height': 800})
            panel = slash(page, field)
            expect(panel).to_contain_text('Relevant här, sedan dina senaste val')
            field.press('End')
            expect(panel.get_by_role('option').last).to_have_attribute('aria-selected', 'true')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            capture(page, 'swedish-320')
            assert not errors and not external, [errors, external]
            assert not any('/apps/' in row['path'] and row['path'].endswith('/commands') for row in writes if row['method'] == 'POST')
            observations.append({'ownerFeatureCount': len(inventory), 'memberFeatureCount': len(member_inventory), 'referenceKinds': sorted(kinds),
                                 'memberCatalogueStatus': catalogue_response.value.status, 'memberCatalogueCommandCount': len(member_commands), 'memberCommandIdentityVerified': True})
            mark('swedish_320px_ranked_catalogue_keeps_the_last_feature_keyboard_reachable')
        except Exception:
            capture(page, 'native-failure')
            raise
        finally:
            context.close()


def demo(browser):
    global stage
    stage = m.stage = 'actual public example memory-session ranking'
    with m.fixture('demo') as (base, app, private):
        snapshot(private, 'demo')
        context, page, errors, external, _writes = m.browser_context(browser, app)
        context.add_init_script('''localStorage.setItem('freecollab_token','retained-standalone-fixture');localStorage.setItem('freecollab_user_id','retained-standalone-actor');window.rankingAccountStorage=[];const get=Storage.prototype.getItem,set=Storage.prototype.setItem,remove=Storage.prototype.removeItem;const keys=['freecollab_token','freecollab_user_id','freecollab_public_key','freecollab_encrypted_private_key'];for(const [name,original]of[['getItem',get],['setItem',set],['removeItem',remove]])Storage.prototype[name]=function(key,...args){if(this===localStorage&&keys.includes(key))window.rankingAccountStorage.push({operation:name,key});return original.call(this,key,...args);};''')
        visit = None
        try:
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/demo/session') as created:
                page.goto(app + '/product-demo/?locale=en', wait_until='domcontentloaded')
            visit = created.value.json()
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            field = page.locator('.fc-chat-composer textarea')
            assert choices(slash(page, field))[0] == 'document'
            choose_command(page, field, '/task')
            close_work_card(page, conversation_work_region(page, kind='tasks'))
            assert choices(slash(page, field))[0] == 'task'
            assert choices(slash(page, field, '/document'))[0] == 'document'
            assert page.evaluate('window.rankingAccountStorage') == []
            assert page.evaluate('localStorage.freecollab_token') == 'retained-standalone-fixture'
            assert page.evaluate('localStorage.freecollab_user_id') == 'retained-standalone-actor'
            assert not errors and not external, [errors, external]
            mark('real_example_ranking_uses_its_own_memory_session_and_never_reads_or_changes_retained_account_credentials')
        except Exception:
            capture(page, 'demo-failure')
            raise
        finally:
            if visit:
                m.api(base, 'DELETE', '/demo/session', visit['session'])
            context.close()


def main():
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt('Owned ranking proof interrupted')
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
        mismatches = {mode: [name for name, digest in values.items() if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest] for mode, values in snapshots.items()}
        report = {'ok': complete and all(not rows for rows in mismatches.values()), 'stage': stage, 'scope': __doc__, 'checks': checks, 'source_sha256': snapshots,
                  'mismatchedCurrentSources': mismatches, 'observations': observations, 'screenshots': screenshots,
                  'limits': 'Real native/demo application, native APIs and browser input; no Matrix deployment, physical keyboard or provider invocation.',
                  'cleanup': 'Only owned disposable loopback processes, browser contexts, SQLite, files and ephemeral fixture credentials were used.'}
        REPORT.write_text(json.dumps(report, indent=2) + '\n')
    assert report['ok'], 'Ranking proof did not complete against the current critical product sources'
    print(json.dumps({'ok': report['ok'], 'checks': checks, 'sourceCounts': {mode: len(values) for mode, values in snapshots.items()}}), flush=True)


if __name__ == '__main__':
    main()
