#!/usr/bin/env python3
"""Actual public showroom interactions in Chromium, with zero native/provider calls.
Runs a private source snapshot; all demo data stays in browser memory.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import urlopen
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(os.getenv('DEMO_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/demo-showroom-browser.json')))
SOURCE = ['frontend/src/assets/open-studio/courtyard.png','frontend/src/assets/open-studio/portraits.png','frontend/src/components/Landing.tsx','frontend/src/components/Demo.tsx','frontend/src/components/DemoChannelSwitcher.tsx','frontend/src/components/DemoDocumentDesk.tsx','frontend/src/App.tsx','frontend/src/website-entry.tsx','frontend/src/lib/demo-showroom.ts','frontend/src/lib/useModalFocus.ts','frontend/src/styles/demo-showroom.css','frontend/src/styles/demo-channel-switcher.css','frontend/src/styles/demo-document-desk.css','frontend/src/i18n/demo-showroom.sv.ts','frontend/src/i18n/studio-refinement.sv.ts','frontend/src/i18n/demo-journey.sv.ts','frontend/src/i18n/demo-natural-flow.sv.ts','frontend/tests/demo-showroom.test.mjs','frontend/tests/demo-showroom-browser.py']
SOURCE += ['frontend/src/components/DemoConnections.tsx','frontend/src/lib/demo-connections.ts','frontend/src/styles/demo-connections.css']
SOURCE += ['frontend/src/components/DemoMessageActions.tsx','frontend/src/components/DemoTaskFromMessage.tsx','frontend/src/styles/demo-message-actions.css']
SOURCE += ['frontend/src/lib/demo-ai.ts','frontend/src/i18n/demo-ai.sv.ts']
SOURCE += ['frontend/src/components/DemoInlineAssist.tsx','frontend/src/lib/demo-assist.ts','frontend/src/styles/demo-inline-assist.css','frontend/src/i18n/demo-assist.sv.ts']

def channel_trigger(page, swedish=False):
    return page.get_by_role('button',name='Välj kanal' if swedish else 'Choose channel',exact=True)

def expect_channel(page, channel, swedish=False):
    labels={'studio':'gården' if swedish else 'courtyard','product':'planering' if swedish else 'planning','design':'design'}
    expect(channel_trigger(page,swedish)).to_contain_text(labels[channel])

def choose_channel(page, channel, swedish=False):
    """Use the real visible channel picker, preserving its focus contract."""
    labels={'studio':'gården' if swedish else 'courtyard','product':'planering' if swedish else 'planning','design':'design'}
    trigger=channel_trigger(page,swedish);trigger.click()
    dialog=page.get_by_role('dialog',name='Kanaler' if swedish else 'Channels',exact=True)
    expect(dialog).to_be_visible()
    dialog.get_by_role('button',name=re.compile('^'+re.escape(labels[channel])+r'\b')).click()
    expect(dialog).to_have_count(0);expect(trigger).to_be_focused()
    expect_channel(page,channel,swedish)

def expand_stack(page, title):
    desk=page.locator('.fc-demo-desk:visible')
    cover=desk.locator('.fc-demo-stack-cover').filter(has=page.get_by_text(title,exact=True))
    if cover.get_attribute('aria-expanded')!='true':cover.click()
    return desk.get_by_role('region',name=title,exact=True)

def usability_checks(browser, app, checks, errors, unexpected):
    """Real channel management, paper moves, undo and original-message navigation."""
    images=[]
    for width in [1440,320]:
        visitor=browser.new_context(viewport={'width':width,'height':1000},reduced_motion='reduce')
        try:
            page=visitor.new_page();page.set_default_timeout(20000)
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:unexpected.append(request.url) if urlsplit(request.url).hostname not in ['127.0.0.1','localhost'] or '/api/' in urlsplit(request.url).path else None)
            page.goto(app,wait_until='networkidle')
            composer=page.get_by_label('Your demo message',exact=True)
            draft='Keep this draft while arranging the work.'
            composer.fill(draft);composer.evaluate('(node)=>{window.usabilityComposer=node;node.setSelectionRange(3,14)}')
            trigger=channel_trigger(page)
            expect(trigger).to_contain_text('Channels')
            assert trigger.bounding_box()['height']>=44
            trigger.focus();page.keyboard.press('Enter')
            dialog=page.get_by_role('dialog',name='Channels',exact=True)
            expect(dialog.get_by_label('Search channels',exact=True)).to_be_focused()
            dialog.get_by_label('Search channels',exact=True).fill('planning')
            expect(dialog.locator('.fc-demo-channel-list button')).to_have_count(1)
            expect(dialog.locator('.fc-demo-channel-list button')).to_contain_text('planning')
            dialog.get_by_role('button',name='Close channel picker',exact=True).focus()
            page.keyboard.press('Shift+Tab')
            assert dialog.evaluate('(node)=>node.contains(document.activeElement)')
            page.keyboard.press('Tab')
            assert dialog.evaluate('(node)=>node.contains(document.activeElement)')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.keyboard.press('Escape');expect(dialog).to_have_count(0);expect(trigger).to_be_focused()
            trigger.click();page.mouse.click(4,4)
            expect(dialog).to_have_count(0);expect(trigger).to_be_focused()
            assert composer.evaluate('(node)=>node===window.usabilityComposer&&node.selectionStart===3&&node.selectionEnd===14')
            expect(composer).to_have_value(draft)

            trigger.click();dialog.get_by_role('button',name=re.compile(r'^Manage channel')).click()
            manage=page.get_by_role('dialog',name='Manage channel',exact=True)
            expect(manage.get_by_label('Channel name',exact=True)).to_be_focused()
            expect(manage).to_contain_text('You manage only this demo channel. Changes stay in this tab.')
            custom_channel='courtyard <i>local</i>'
            custom_description='A description written only for this browser tab.'
            manage.get_by_label('Channel name',exact=True).fill(custom_channel)
            manage.get_by_role('textbox',name='Channel description',exact=True).fill(custom_description)
            manage.get_by_role('checkbox',name='Nora',exact=True).uncheck()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            manage.get_by_role('button',name='Save channel',exact=True).click()
            expect(manage).to_have_count(0);expect(trigger).to_be_focused();expect(trigger).to_contain_text(custom_channel)
            assert trigger.locator('i').count()==0
            trigger.click();dialog.get_by_label('Search channels',exact=True).fill(custom_channel)
            row=dialog.locator('.fc-demo-channel-list button')
            expect(row).to_have_count(1);expect(row).to_contain_text(custom_description);expect(row).to_contain_text('3 members')
            page.keyboard.press('Escape')
            choose_channel(page,'design')
            trigger.click();dialog.locator('.fc-demo-channel-list button').filter(has=page.get_by_text(custom_channel,exact=True)).click()
            expect(trigger).to_contain_text(custom_channel);expect(composer).to_have_value(draft)
            trigger.click();dialog.get_by_role('button',name=re.compile(r'^Manage channel')).click()
            manage.get_by_label('Channel name',exact=True).fill('An edit that is cancelled')
            manage.get_by_role('button',name='Cancel',exact=True).click()
            expect(trigger).to_contain_text(custom_channel)
            checks[str(width)+'px_visible_searchable_channel_picker_focus_trap_escape_outside_local_manage_and_cancel']=True

            desk=page.locator('.fc-demo-desk:visible')
            desk.get_by_role('button',name='New',exact=True).click()
            desk.get_by_role('button',name='Stack',exact=True).click()
            desk.get_by_label('Stack name',exact=True).fill('Walkthrough')
            desk.get_by_role('button',name='Create stack',exact=True).click()
            cover=desk.locator('.fc-demo-stack-cover').filter(has=page.get_by_text('Walkthrough',exact=True))
            expect(cover).to_be_focused();expect(cover).to_have_attribute('aria-expanded','true')
            expand_stack(page,'Walkthrough').get_by_role('button',name='Add document',exact=True).click()
            title='A locally drafted brief'
            desk.get_by_label('Document title',exact=True).fill(title)
            desk.get_by_role('combobox',name='In this stack',exact=True).select_option(label='Walkthrough')
            desk.get_by_role('button',name='Create document',exact=True).click()
            editor=page.get_by_label('Edit demo document',exact=True)
            expect(editor).to_be_focused();editor.fill('## A real local document\n\nKeep this text after moving it.')
            desk=page.locator('.fc-demo-desk:visible')
            expect(expand_stack(page,'Walkthrough').get_by_role('button',name=title,exact=True)).to_be_visible()
            desk.get_by_label('Move '+title+' to',exact=True).select_option(label='Sketches')
            expect(expand_stack(page,'Sketches').get_by_role('button',name=title,exact=True)).to_be_visible()
            edited='## Edited after the move\n\nUndo must preserve this ongoing work.'
            editor.fill(edited)
            desk.get_by_role('button',name='Undo',exact=True).click()
            expect(expand_stack(page,'Walkthrough').get_by_role('button',name=title,exact=True)).to_be_visible()
            expect(editor).to_have_value(edited)
            expect(desk.get_by_role('button',name='Undo',exact=True)).to_have_count(0)
            desk.get_by_role('button',name='List',exact=True).click()
            expect(desk.get_by_role('button',name=title,exact=True)).to_be_visible()
            expect(desk.get_by_label('Move '+title+' to',exact=True)).to_have_value(re.compile(r'^stack-'))
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            checks[str(width)+'px_real_stack_document_creation_accessible_move_list_and_undo_preserve_edits']=True

            page.get_by_role('button',name='Back to conversation',exact=True).click()
            expect(composer).to_have_value(draft)
            reference='A useful reference from the actual conversation.'
            composer.fill(reference);page.get_by_role('button',name='Send demo message',exact=True).click()
            composer.fill(draft);composer.evaluate('(node)=>node.setSelectionRange(3,14)')
            # Mobile context hides this same DOM; an unscoped role locator would
            # deliberately exclude it while the reference is being saved.
            original=page.locator('.fc-showroom-transcript article').filter(has=page.get_by_text(reference,exact=True))
            original.evaluate('(node)=>{window.usabilityOriginalMessage=node}')
            original.get_by_role('button',name='Keep message from You',exact=True).click()
            desk=page.locator('.fc-demo-desk:visible')
            desk.get_by_role('combobox',name='Choose a stack',exact=True).select_option(label='Walkthrough')
            desk.get_by_role('button',name='Save reference',exact=True).click()
            stack=expand_stack(page,'Walkthrough')
            expect(stack.locator('blockquote')).to_have_text(reference)
            expect(original).to_have_count(1)
            assert original.evaluate('(node)=>node===window.usabilityOriginalMessage')
            stack.get_by_role('button',name='Remove reference from Walkthrough',exact=True).click()
            expect(stack.locator('blockquote')).to_have_count(0)
            desk.get_by_role('button',name='Undo',exact=True).click()
            expect(stack.locator('blockquote')).to_have_text(reference)
            stack.get_by_role('button',name='Show original',exact=True).click()
            expect(page.locator('.fc-studio-context-panel')).not_to_be_visible();expect(original).to_be_focused()
            expect(composer).to_have_value(draft)
            assert composer.evaluate('(node)=>node===window.usabilityComposer&&node.selectionStart===3&&node.selectionEnd===14')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            image=REPORT.with_name('demo-usability-'+str(width)+'.png');page.screenshot(path=str(image),full_page=True);images.append(str(image))
            checks[str(width)+'px_conversation_reference_remove_undo_and_original_jump_keep_single_message_and_draft']=True

            choose_channel(page,'design')
            expect(page.locator('.fc-demo-desk:visible').get_by_text('Walkthrough',exact=True)).to_have_count(0)
            expect(page.locator('.fc-demo-desk:visible').get_by_role('button',name='Undo',exact=True)).to_have_count(0)
            trigger.click();dialog.locator('.fc-demo-channel-list button').filter(has=page.get_by_text(custom_channel,exact=True)).click()
            expect(expand_stack(page,'Walkthrough').get_by_role('button',name=title,exact=True)).to_be_visible()
            page.get_by_role('button',name='Tools',exact=True).click();page.get_by_role('button',name='Reset demo',exact=True).click()
            page.get_by_role('button',name='Reset this demo',exact=True).click()
            expect_channel(page,'studio')
            expect(page.locator('.fc-demo-desk:visible').get_by_text('Walkthrough',exact=True)).to_have_count(0)
            expect(page.get_by_role('log').get_by_text(reference,exact=True)).to_have_count(0)
            expect(composer).to_have_value('')
            assert page.evaluate('localStorage.length')==0 and page.evaluate('sessionStorage.length')==0
            checks[str(width)+'px_channel_scoped_stacks_and_reset_clear_channel_names_documents_references_and_drafts']=True
        finally:visitor.close()
    return images

def connection_checks(browser, app, checks, errors, unexpected):
    """Exercise content-based suggestions through explicit local UI actions."""
    images=[]
    for width in [1440,320]:
        visitor=browser.new_context(viewport={'width':width,'height':1000},reduced_motion='reduce')
        try:
            page=visitor.new_page();page.set_default_timeout(20000)
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:unexpected.append(request.url) if urlsplit(request.url).hostname not in ['127.0.0.1','localhost'] or '/api/' in urlsplit(request.url).path else None)
            page.goto(app,wait_until='networkidle')
            composer=page.get_by_label('Your demo message',exact=True)
            transcript=page.locator('.fc-showroom-transcript')
            trigger=page.get_by_role('button',name='Suggested connections',exact=True)
            initial_count=trigger.locator('small').text_content()
            draft='Materials & sketches. Keep this matching thought unsent.'
            composer.fill(draft);composer.evaluate('(node)=>{window.connectionComposer=node;node.setSelectionRange(3,17)}')
            expect(trigger.locator('small')).to_have_text(initial_count)
            transcript.evaluate('(node)=>{window.connectionTranscript=node;window.connectionGeometry={width:node.clientWidth,height:node.clientHeight,scrollHeight:node.scrollHeight,top:node.scrollTop}}')
            trigger.focus();page.keyboard.press('Enter')
            context=page.locator('.fc-studio-context-panel')
            connections=page.locator('.fc-demo-connections:visible')
            expect(context).to_be_focused()
            expect(connections).to_contain_text('Suggestions use the words and titles in this channel. You choose what to connect.')
            expect(connections.get_by_text('A message from You',exact=True)).to_have_count(0)
            assert transcript.evaluate('(node)=>node===window.connectionTranscript')
            if width>760:
                assert transcript.evaluate('(node)=>node.clientWidth===window.connectionGeometry.width&&node.clientHeight===window.connectionGeometry.height&&node.scrollHeight===window.connectionGeometry.scrollHeight&&node.scrollTop===window.connectionGeometry.top')
            assert composer.evaluate('(node)=>node===window.connectionComposer&&node.selectionStart===3&&node.selectionEnd===17')
            switch=connections.get_by_role('switch',name='Suggest connections',exact=True)
            switch.click();expect(switch).to_have_attribute('aria-checked','false')
            expect(connections.locator('.fc-demo-connection')).to_have_count(0)
            expect(connections).to_contain_text('Suggestions are paused in this channel.')
            switch.click();expect(switch).to_have_attribute('aria-checked','true')
            message='I’ve shared the new sketch here. The long table can sit by the open doors.'
            card=connections.locator('[data-connection-kind="message_stack"]').filter(has=page.get_by_text(message,exact=True))
            expect(card).to_contain_text('Sketches')
            expect(card.locator('.fc-demo-connection-reason')).to_contain_text('sketch')
            card.get_by_role('button',name=re.compile(r'^Dismiss suggestion')).click()
            expect(connections.get_by_role('heading',name=re.compile(r'^Suggested connections'))).to_be_focused()
            expect(card).to_have_count(0)
            connections.get_by_role('button',name='Restore dismissed (1)',exact=True).click()
            expect(card).to_be_visible()
            original=page.locator('.fc-showroom-transcript [data-message-id="studio-3"]')
            original.evaluate('(node)=>{window.connectionOriginal=node}')
            card.get_by_role('button',name='Save reference in Sketches',exact=True).click()
            expect(card).to_have_count(0)
            expect(connections).to_contain_text('Reference kept with Sketches.')
            connections.get_by_role('button',name='Undo connection',exact=True).click()
            expect(card).to_be_visible()
            card.get_by_role('button',name='Save reference in Sketches',exact=True).click()
            assert original.evaluate('(node)=>node===window.connectionOriginal')
            page.get_by_role('navigation',name='Follow the courtyard project',exact=True).get_by_role('button',name=re.compile(r'^Documents')).click()
            expect(expand_stack(page,'Sketches').locator('blockquote')).to_have_text(message)
            expect(original).to_have_count(1)
            page.get_by_role('button',name='Back to conversation',exact=True).click()
            expect(trigger).to_be_focused();expect(composer).to_have_value(draft)
            checks[str(width)+'px_suggestions_ignore_drafts_explain_current_content_and_confirm_reference_dismiss_restore_pause_undo_without_remount']=True

            trigger.click()
            pair=connections.locator('[data-connection-kind="document_link"]').filter(has=page.get_by_text('Project brief',exact=True)).filter(has=page.get_by_text('The direction we agreed',exact=True))
            expect(pair).to_be_visible()
            pair.get_by_role('button',name=re.compile(r'^Link ')).click()
            expect(pair).to_have_count(0)
            expect(connections).to_contain_text('Documents linked. The originals stay in place.')
            connections.get_by_role('button',name='Undo connection',exact=True).click()
            expect(pair).to_be_visible()
            pair.get_by_role('button',name=re.compile(r'^Link ')).click()
            page.get_by_role('navigation',name='Follow the courtyard project',exact=True).get_by_role('button',name=re.compile(r'^Documents')).click()
            saved=connections.locator('.fc-demo-connections-saved')
            saved.get_by_role('button',name='The direction we agreed',exact=True).click()
            expect(page.get_by_role('region',name='Document editor',exact=True)).to_contain_text('The direction we agreed')
            expect(page.get_by_label('Edit demo document',exact=True)).to_be_focused()
            saved.get_by_role('button',name='Unlink Project brief',exact=True).click()
            expect(saved).to_have_count(0)
            connections.get_by_role('button',name='Undo connection',exact=True).click()
            expect(saved.get_by_role('button',name='Project brief',exact=True)).to_be_visible()
            expect(page.get_by_role('navigation',name='Follow the courtyard project',exact=True).get_by_role('button',name=re.compile(r'^Documents'))).to_contain_text('4')
            expect(composer).to_have_value(draft)
            checks[str(width)+'px_explicit_document_links_keep_originals_in_place_open_related_documents_and_support_unlink_undo']=True

            expand_stack(page,'Sketches').get_by_role('button',name='Materials & sketches',exact=True).click()
            editor=page.get_by_label('Edit demo document',exact=True)
            editor.fill('## Moonstone acoustic lantern\n\nMoonstone acoustic lantern.')
            page.get_by_role('button',name='Back to conversation',exact=True).click()
            matching='Moonstone acoustic lantern needs a careful review.'
            composer.fill(matching);page.get_by_role('button',name='Send demo message',exact=True).click()
            composer.fill(draft);composer.evaluate('(node)=>node.setSelectionRange(3,17)')
            trigger.click()
            actual=connections.locator('[data-connection-kind="message_stack"]').filter(has=page.get_by_text(matching,exact=True))
            expect(actual).to_be_visible();expect(actual.locator('.fc-demo-connection-reason')).to_contain_text('Moonstone')
            page.get_by_role('navigation',name='Follow the courtyard project',exact=True).get_by_role('button',name=re.compile(r'^Documents')).click()
            expand_stack(page,'Sketches').get_by_role('button',name='Materials & sketches',exact=True).click()
            editor.fill('## Cedar pottery\n\nOnly cedar pottery belongs here now.')
            trigger.click();expect(actual).to_have_count(0)
            expect(composer).to_have_value(draft)
            assert composer.evaluate('(node)=>node===window.connectionComposer&&node.selectionStart===3&&node.selectionEnd===17')
            page.get_by_role('navigation',name='Follow the courtyard project',exact=True).get_by_role('button',name=re.compile(r'^Documents')).click()
            desk=page.locator('.fc-demo-desk:visible')
            desk.get_by_role('button',name='New',exact=True).click()
            moving_title='Sketches for the acoustics'
            desk.get_by_label('Document title',exact=True).fill(moving_title)
            desk.get_by_role('combobox',name='In this stack',exact=True).select_option(label='Reference material')
            desk.get_by_role('button',name='Create document',exact=True).click()
            trigger.click()
            move=connections.locator('[data-connection-kind="document_stack"]').filter(has=page.get_by_text(moving_title,exact=True))
            expect(move).to_be_visible()
            move.get_by_role('button',name='Move '+moving_title+' to Sketches',exact=True).click()
            expect(move).to_have_count(0)
            connections.get_by_role('button',name='Undo connection',exact=True).click()
            expect(move).to_be_visible()
            page.get_by_role('navigation',name='Follow the courtyard project',exact=True).get_by_role('button',name=re.compile(r'^Documents')).click()
            expect(expand_stack(page,'Reference material').get_by_role('button',name=moving_title,exact=True)).to_be_visible()
            trigger.click()
            checks[str(width)+'px_stack_title_suggestion_requires_explicit_move_and_undo_restores_original_stack']=True
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            image=REPORT.with_name('demo-connections-'+str(width)+'.png');page.screenshot(path=str(image),full_page=True);images.append(str(image))
            choose_channel(page,'design');trigger.click()
            expect(connections.get_by_text(matching,exact=True)).to_have_count(0)
            expect(connections.get_by_text('Project brief',exact=True)).to_have_count(0)
            connections.get_by_role('switch',name='Suggest connections',exact=True).click()
            choose_channel(page,'studio');trigger.click()
            expect(connections.get_by_role('switch',name='Suggest connections',exact=True)).to_have_attribute('aria-checked','true')
            page.get_by_role('button',name='Tools',exact=True).click();page.get_by_role('button',name='Reset demo',exact=True).click()
            page.get_by_role('button',name='Reset this demo',exact=True).click()
            trigger.click()
            expect(connections.get_by_role('button',name=re.compile(r'^Restore dismissed'))).to_have_count(0)
            expect(connections.locator('.fc-demo-connections-saved')).to_have_count(0)
            expect(page.locator('.fc-showroom-transcript').get_by_text(matching,exact=True)).to_have_count(0)
            expect(composer).to_have_value('')
            choose_channel(page,'design');trigger.click()
            expect(connections.get_by_role('switch',name='Suggest connections',exact=True)).to_have_attribute('aria-checked','true')
            assert page.evaluate('localStorage.length')==0 and page.evaluate('sessionStorage.length')==0
            checks[str(width)+'px_content_edits_remove_stale_matches_channel_settings_and_content_stay_isolated_and_reset_clears_links']=True
        finally:visitor.close()
    return images

def message_task_checks(browser, app, checks, errors, unexpected):
    """Sent text becomes one source-linked task, with no typing or inferred owner required."""
    images=[]
    for width,swedish in [(1440,False),(320,True)]:
        visitor=browser.new_context(viewport={'width':width,'height':1000},reduced_motion='reduce')
        try:
            page=visitor.new_page();page.set_default_timeout(20000)
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:unexpected.append(request.url) if urlsplit(request.url).hostname not in ['127.0.0.1','localhost'] or '/api/' in urlsplit(request.url).path else None)
            page.goto(app,wait_until='networkidle')
            if swedish:
                page.get_by_role('button',name='Tools',exact=True).click();page.get_by_role('button',name='SV',exact=True).click();page.keyboard.press('Escape')
            names={'tools':'Verktyg' if swedish else 'Tools','message':'Ditt demomeddelande' if swedish else 'Your demo message','send':'Skicka demomeddelande' if swedish else 'Send demo message','make':'Skapa en uppgift från det här meddelandet' if swedish else 'Make a task from this message','view':'Visa uppgiften från det här meddelandet' if swedish else 'View task for this message','title':'Uppgiftens titel' if swedish else 'Task title','unassigned':'Ingen ansvarig' if swedish else 'Unassigned','create':'Skapa uppgift' if swedish else 'Create task','cancel':'Avbryt' if swedish else 'Cancel','back_tasks':'Tillbaka till uppgifter' if swedish else 'Back to tasks','undo':'Ångra skapandet av uppgiften' if swedish else 'Undo task creation','source':'Visa källmeddelandet' if swedish else 'View source message','original':'Visa originalmeddelandet' if swedish else 'View original message','full':'Läs hela meddelandet' if swedish else 'Read the full message'}
            journey=page.get_by_role('navigation',name='Följ gårdsprojektet' if swedish else 'Follow the courtyard project',exact=True)
            def daily(section):journey.get_by_role('button',name=re.compile('^'+section)).click()
            def tools_section(section):
                page.get_by_role('button',name=names['tools'],exact=True).click();page.get_by_role('tab',name=section,exact=True).click()
            composer=page.get_by_label(names['message'],exact=True)
            for index in range(7):
                composer.fill('Reading continuity '+str(index)+'. '+('A neutral line remains in the same place. '*4))
                page.get_by_role('button',name=names['send'],exact=True).click()
            prefix=('Bring the annotated courtyard materials to the walkthrough. '*4)[:179]
            source_text=prefix+'😀 Full source survives the title boundary. Åäö <img src="https://invalid.example/source">'
            composer.fill(source_text);page.get_by_role('button',name=names['send'],exact=True).click()
            original=page.locator('.fc-showroom-transcript article').filter(has=page.get_by_text(source_text,exact=True))
            original.evaluate('(node)=>{window.taskOriginal=node;window.taskSourceId=node.dataset.messageId}')
            draft='A separate unfinished conversation draft. Åäö.'
            composer.fill(draft);composer.evaluate('(node)=>{window.taskComposer=node;node.setSelectionRange(4,16)}')
            trigger=original.get_by_role('button',name=names['make'],exact=True)
            trigger.focus()
            transcript=page.locator('.fc-showroom-transcript')
            transcript.evaluate('(node)=>{window.taskTranscript=node;if(innerWidth>760)node.scrollTop=120;window.taskReading={top:node.scrollTop,width:node.clientWidth,height:node.clientHeight,scrollHeight:node.scrollHeight}}')
            trigger.evaluate('(button)=>button.click()')
            review=page.locator('.fc-demo-task-from-message:visible')
            expect(review.get_by_role('heading')).to_be_focused()
            title=review.get_by_label(names['title'],exact=True)
            expect(title).to_have_value(prefix)
            title.input_value().encode('utf-16-le')  # A clipped surrogate is invalid text.
            expect(review.get_by_role('button',name=names['unassigned'],exact=True)).to_have_attribute('aria-pressed','true')
            review.get_by_text(names['full'],exact=True).click()
            expect(review.locator('details blockquote')).to_have_text(source_text)
            assert review.locator('img').count()==0
            title_text='Bring the annotated courtyard materials to the walkthrough'
            title.fill(title_text);review.get_by_role('button',name='Noor',exact=True).click()
            daily('Dokument' if swedish else 'Documents');daily('Uppgifter' if swedish else 'Tasks')
            expect(title).to_have_value(title_text);expect(review.get_by_role('button',name='Noor',exact=True)).to_have_attribute('aria-pressed','true')
            tools_section('Utseende' if swedish else 'Appearance');tools_section('Samtal' if swedish else 'Conversation')
            trigger.evaluate('(button)=>button.click()')
            expect(title).to_have_value(title_text)
            assert composer.evaluate('(node)=>node===window.taskComposer&&node.selectionStart===4&&node.selectionEnd===16')
            assert original.evaluate('(node)=>node===window.taskOriginal')
            choose_channel(page,'design',swedish);choose_channel(page,'studio',swedish)
            trigger.evaluate('(button)=>button.click()')
            expect(title).to_have_value(title_text);expect(review.get_by_role('button',name='Noor',exact=True)).to_have_attribute('aria-pressed','true')
            assert original.evaluate('(node)=>node.dataset.messageId===window.taskSourceId')
            original.evaluate('(node)=>{window.taskOriginal=node}')
            expect(composer).to_have_value(draft)
            # Per-channel conversation draft values are retained; native caret
            # position is separately established for the following task actions.
            composer.evaluate('(node)=>node.setSelectionRange(4,16)')
            review.get_by_role('button',name=names['cancel'],exact=True).click()
            expect(review).to_have_count(0);expect(trigger).to_be_focused();expect(composer).to_have_value(draft)
            assert composer.evaluate('(node)=>node===window.taskComposer&&node.selectionStart===4&&node.selectionEnd===16')
            assert transcript.evaluate('(node)=>node===window.taskTranscript')
            if width>760:assert transcript.evaluate('(node)=>node.scrollTop===window.taskReading.top&&node.clientWidth===window.taskReading.width&&node.clientHeight===window.taskReading.height&&node.scrollHeight===window.taskReading.scrollHeight')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            checks[str(width)+'px_source_task_review_reuses_valid_text_unassigned_owner_full_source_and_keeps_drafts_caret_reading_across_detours']=True

            trigger.evaluate('(button)=>button.click()')
            expect(title).to_have_value(prefix)
            title.fill(title_text);review.get_by_role('button',name='Noor',exact=True).click()
            review.locator('form').evaluate('(form)=>{form.requestSubmit();form.requestSubmit()}')
            expect(review.get_by_role('heading')).to_be_focused()
            expect(review.get_by_role('button',name=names['create'],exact=True)).to_have_count(0)
            assert composer.evaluate('(node)=>node===window.taskComposer&&node.selectionStart===4&&node.selectionEnd===16')
            assert transcript.evaluate('(node)=>node===window.taskTranscript')
            if width>760:assert transcript.evaluate('(node)=>node.scrollTop===window.taskReading.top&&node.clientWidth===window.taskReading.width&&node.clientHeight===window.taskReading.height&&node.scrollHeight===window.taskReading.scrollHeight')
            checkbox=page.get_by_role('checkbox',name=title_text,exact=True)
            expect(checkbox).to_have_count(1)
            row=page.locator('[data-demo-task-id]').filter(has=checkbox)
            expect(row).to_contain_text('Noor')
            review.get_by_role('button',name=names['back_tasks'],exact=True).click();expect(checkbox).to_be_focused()
            row.get_by_role('button',name=names['source'],exact=True).click();expect(original).to_be_focused()
            assert original.evaluate('(node)=>node===window.taskOriginal')
            view=original.get_by_role('button',name=names['view'],exact=True)
            view.evaluate('(button)=>button.click()')
            expect(checkbox).to_have_count(1);expect(review).to_contain_text('Noor')
            review.get_by_role('button',name=names['undo'],exact=True).click()
            expect(checkbox).to_have_count(0);expect(title).to_have_value(title_text)
            expect(review.get_by_role('button',name=names['unassigned'],exact=True)).to_have_attribute('aria-pressed','false')
            review.get_by_role('button',name=names['create'],exact=True).click()
            expect(checkbox).to_have_count(1);checkbox.check()
            expect(review.get_by_role('button',name=names['undo'],exact=True)).to_have_count(0)
            review.get_by_role('button',name=names['back_tasks'],exact=True).click();expect(checkbox).to_be_focused()
            row.get_by_role('button',name=names['source'],exact=True).click();view.evaluate('(button)=>button.click()')
            expect(checkbox).to_be_checked();expect(checkbox).to_have_count(1)
            expect(review.get_by_role('button',name=names['undo'],exact=True)).to_have_count(0)
            review.get_by_role('button',name=names['original'],exact=True).click();expect(original).to_be_focused()
            expect(composer).to_have_value(draft)
            assert composer.evaluate('(node)=>node===window.taskComposer&&node.selectionStart===4&&node.selectionEnd===16')
            checks[str(width)+'px_source_task_double_submit_creates_one_cited_task_reopens_it_undoes_only_matching_unfinished_creation_and_retains_original']=True

            choose_channel(page,'design',swedish);daily('Uppgifter' if swedish else 'Tasks')
            expect(page.get_by_role('checkbox',name=title_text,exact=True)).to_have_count(0)
            page.get_by_role('button',name='Tillbaka till samtalet' if swedish else 'Back to conversation',exact=True).click()
            design_source='A design-only next step from its own conversation.'
            composer.fill(design_source);page.get_by_role('button',name=names['send'],exact=True).click()
            design_original=page.locator('.fc-showroom-transcript article').filter(has=page.get_by_text(design_source,exact=True))
            design_original.get_by_role('button',name=names['make'],exact=True).click()
            expect(title).to_have_value(design_source)
            review.get_by_role('button',name=names['create'],exact=True).click()
            expect(review).to_contain_text(names['unassigned'])
            expect(page.get_by_role('checkbox',name=design_source,exact=True)).to_have_count(1)
            choose_channel(page,'studio',swedish);daily('Uppgifter' if swedish else 'Tasks')
            expect(page.get_by_role('checkbox',name=title_text,exact=True)).to_be_checked()
            expect(page.get_by_role('checkbox',name=design_source,exact=True)).to_have_count(0)
            image=REPORT.with_name('demo-message-task-'+str(width)+('.sv' if swedish else '')+'.png');page.screenshot(path=str(image),full_page=True);images.append(str(image))
            page.reload(wait_until='networkidle')
            expect(page.get_by_label('Your demo message',exact=True)).to_have_value('')
            page.get_by_role('button',name='Tools',exact=True).click();page.get_by_role('tab',name='Projects',exact=True).click()
            expect(page.get_by_role('checkbox',name=title_text,exact=True)).to_have_count(0)
            expect(page.get_by_role('checkbox',name=design_source,exact=True)).to_have_count(0)
            expect(page.locator('.fc-demo-task-from-message')).to_have_count(0)
            page.get_by_role('button',name='Tools',exact=True).click();page.get_by_role('tab',name='Conversation',exact=True).click()
            page.locator('.fc-showroom-transcript article').first.get_by_role('button',name='Make a task from this message',exact=True).click()
            page.locator('.fc-demo-task-from-message:visible').get_by_role('button',name='Create task',exact=True).click()
            page.get_by_role('button',name='Tools',exact=True).click();page.get_by_role('button',name='Reset demo',exact=True).click();page.get_by_role('button',name='Reset this demo',exact=True).click()
            page.get_by_role('button',name='Tools',exact=True).click();page.get_by_role('tab',name='Projects',exact=True).click()
            expect(page.locator('[data-task-source="true"]')).to_have_count(0)
            expect(page.locator('.fc-demo-task-from-message')).to_have_count(0)
            assert page.evaluate('localStorage.length')==0 and page.evaluate('sessionStorage.length')==0
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            checks[str(width)+'px_source_tasks_remain_channel_scoped_and_optional_owner_reset_reload_discard_tasks_reviews_and_drafts']=True
        finally:visitor.close()
    return images

def public_ai_checks(browser, app, checks, errors, unexpected):
    """Assistance stays with its exact source; every write is deliberately approved."""
    images=[]
    for width,swedish in [(1440,False),(320,True)]:
        visitor=browser.new_context(viewport={'width':width,'height':1000},reduced_motion='reduce')
        try:
            page=visitor.new_page();page.set_default_timeout(20000)
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:unexpected.append(request.url) if urlsplit(request.url).hostname not in ['127.0.0.1','localhost'] or '/api/' in urlsplit(request.url).path else None)
            page.goto(app,wait_until='networkidle')
            if swedish:
                page.get_by_role('button',name='Tools',exact=True).click();page.get_by_role('button',name='SV',exact=True).click();page.keyboard.press('Escape')
            def text(en,sv):return sv if swedish else en
            def scope(kind,container=None):return (container or page).locator('[data-demo-assist][data-assist-scope="'+kind+'"]:visible')
            def open_help(help):
                trigger=help.locator('[data-assist-trigger]')
                expect(trigger).to_be_visible();assert trigger.bounding_box()['height']>=44
                if trigger.get_attribute('aria-expanded')!='true':trigger.evaluate('(button)=>button.click()')
                card=help.locator('.fc-inline-assist-card')
                expect(card).to_be_visible();expect(card.locator('header h4')).to_be_focused()
                expect(card.get_by_text(text('Uses selected text; no AI request.','Använder vald text; inget AI-anrop.'),exact=True)).to_be_visible()
                return card
            def prepare(card,en,sv):
                card.get_by_role('button',name=text(en,sv),exact=True).click()
                card.get_by_role('button',name=text('Prepare example','Förbered exempel'),exact=True).click()
                result=card.locator('[data-assist-preview="current"]')
                expect(result).to_be_visible();expect(result.locator('h5')).to_be_focused()
                return result
            def apply(result,en,sv):
                button=result.get_by_role('button',name=re.compile('^'+re.escape(text(en,sv))+r'\b'))
                expect(button).to_be_enabled();button.click()
            def close_help(card,help):
                card.get_by_role('button',name=text('Cancel','Avbryt'),exact=True).click()
                expect(help.locator('[data-assist-trigger]')).to_be_focused()
            def undo(help=None):(help.locator('..') if help is not None else scope('composer').locator('..')).get_by_role('button',name=text('Undo last assisted change','Ångra senast använda förslag'),exact=True).click()
            def journey(en,sv):page.get_by_role('navigation',name=text('Follow the courtyard project','Följ gårdsprojektet'),exact=True).get_by_role('button',name=re.compile('^'+re.escape(text(en,sv)))).click()
            composer=page.get_by_label(text('Your demo message','Ditt demomeddelande'),exact=True)
            source_text='Please bring cobalt bridge samples to the walkthrough.'
            composer.fill(source_text);page.get_by_role('button',name=text('Send demo message','Skicka demomeddelande'),exact=True).click()
            original=page.locator('.fc-showroom-transcript article').filter(has=page.get_by_text(source_text,exact=True))
            source_id=original.get_attribute('data-message-id')
            dirty='   Keep   cobalt samples   ready.   Ask   the team about the bridge.  '
            composer.fill(dirty);composer.evaluate('(node)=>{window.inlineComposer=node;node.setSelectionRange(3,15)}')
            transcript=page.locator('.fc-showroom-transcript');transcript.evaluate('(node)=>{window.inlineTranscript=node}')
            sent_count=transcript.locator('article').count()
            helper=scope('composer');card=open_help(helper)
            result=prepare(card,'Tidy my draft','Putsa mitt utkast')
            expect(composer).to_have_value(dirty)
            expect(result.locator('.fc-inline-assist-recipe blockquote')).to_have_text('Keep cobalt samples ready. Ask the team about the bridge.')
            changed=dirty+' Updated.';composer.fill(changed)
            expect(card.locator('[data-assist-preview="stale"]')).to_be_visible()
            expect(card.locator('.fc-inline-assist-recipe>button')).to_be_disabled()
            result=prepare(card,'Tidy my draft','Putsa mitt utkast')
            apply(result,'Use tidy draft','Använd det putsade utkastet')
            expect(composer).to_have_value('Keep cobalt samples ready. Ask the team about the bridge. Updated.')
            assert transcript.locator('article').count()==sent_count
            undo();expect(composer).to_have_value(changed)
            result=prepare(card,'Shorten my draft','Korta mitt utkast')
            expect(composer).to_have_value(changed)
            apply(result,'Use shorter draft','Använd kortare utkast')
            expect(composer).to_have_value('Keep cobalt samples ready.')
            assert transcript.locator('article').count()==sent_count
            undo();expect(composer).to_have_value(changed)
            close_help(card,helper)
            assert composer.evaluate('(node)=>node===window.inlineComposer') and transcript.evaluate('(node)=>node===window.inlineTranscript')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            checks[str(width)+'px_contextual_composer_preview_is_local_stale_safe_explicit_undoable_and_never_sends_a_draft']=True

            helper=scope('message',original);card=open_help(helper)
            result=prepare(card,'Summarize this message','Sammanfatta meddelandet')
            expect(result).to_contain_text(source_text);expect(result).not_to_contain_text(changed)
            expect(result.locator('.fc-inline-assist-citations button')).to_have_count(1)
            card.get_by_text(text('What is shared?','Vad delas?'),exact=True).click()
            expect(card.locator('.fc-inline-assist-sharing')).to_contain_text('api.x.ai')
            expect(card.locator('.fc-inline-assist-sharing')).to_contain_text(text('No service receives your sources.','Ingen tjänst tar emot dina källor.'))
            result=prepare(card,'Draft a reply','Skriv ett svarsutkast')
            expect(composer).to_have_value(changed)
            apply(result,'Add reply to my draft','Lägg till svaret i mitt utkast')
            reply=composer.input_value();assert reply.startswith(changed) and source_text in reply
            assert transcript.locator('article').count()==sent_count
            undo();expect(composer).to_have_value(changed)
            result=prepare(card,'Prepare a task','Förbered en uppgift')
            expect(page.locator('.fc-demo-task-from-message')).to_have_count(0)
            apply(result,'Review task from this message','Granska uppgiften från meddelandet')
            review=page.locator('.fc-demo-task-from-message:visible')
            expect(review.get_by_label(text('Task title','Uppgiftens titel'),exact=True)).to_have_value(source_text)
            expect(review.get_by_role('button',name=text('Unassigned','Ingen ansvarig'),exact=True)).to_have_attribute('aria-pressed','true')
            expect(page.get_by_role('checkbox',name=source_text,exact=True)).to_have_count(0)
            task_title='  Review   cobalt samples  '
            review.get_by_label(text('Task title','Uppgiftens titel'),exact=True).fill(task_title)
            review.get_by_role('button',name='Noor',exact=True).click()
            review.get_by_role('button',name=text('Create task','Skapa uppgift'),exact=True).click()
            expect(page.get_by_role('checkbox',name=task_title.strip(),exact=True)).to_have_count(1)
            review.get_by_role('button',name=text('Back to tasks','Tillbaka till uppgifter'),exact=True).click()
            task_row=page.locator('[data-demo-task-id]').filter(has=page.get_by_role('checkbox',name=task_title.strip(),exact=True))
            task_id=task_row.get_attribute('data-demo-task-id')
            task_row.get_by_role('button',name=text('View source message','Visa källmeddelandet'),exact=True).click();expect(original).to_be_focused()
            expect(composer).to_have_value(changed)
            assert composer.evaluate('(node)=>node===window.inlineComposer')
            checks[str(width)+'px_contextual_message_uses_only_its_cited_text_appends_reply_without_sending_and_requires_real_task_review']=True

            edited='## Cobalt bridge\n\nThe exact current document.\n\n### Material review\n\n'+('Keep this material detail literal. '*12)+'TAIL_A <img src="https://invalid.example/inline-source">'
            page.get_by_label(text('Edit courtyard brief','Redigera gårdens projektbeskrivning'),exact=True).fill(edited)
            journey('Documents','Dokument')
            stack_title=text('Reference material','Underlag')
            stack=expand_stack(page,stack_title)
            document_help=stack.locator('[data-assist-document="brief"] [data-demo-assist]')
            card=open_help(document_help);result=prepare(card,'Outline this document','Skapa en dokumentöversikt')
            editor=page.get_by_label(text('Edit demo document','Redigera demodokument'),exact=True)
            expect(editor).to_have_value(edited)
            expected=edited+'\n\n## '+text('Outline from this document','Översikt från dokumentet')+'\n\n- Cobalt bridge\n- Material review'
            expect(result.locator('.fc-inline-assist-recipe blockquote')).to_contain_text('Cobalt bridge')
            modified=edited.replace('TAIL_A','TAIL_B');editor.fill(modified)
            expect(card.locator('[data-assist-preview="stale"]')).to_be_visible()
            expect(card.locator('.fc-inline-assist-recipe>button')).to_be_disabled()
            for citation in card.locator('.fc-inline-assist-citations button').all():expect(citation).to_be_disabled()
            result=prepare(card,'Outline this document','Skapa en dokumentöversikt')
            apply(result,'Append this outline','Lägg till översikten')
            expect(editor).to_have_value(expected.replace('TAIL_A','TAIL_B'))
            assert editor.input_value().startswith(modified) and page.locator('.fc-demo-desk img').count()==0
            undo(document_help);expect(editor).to_have_value(modified)
            close_help(card,document_help)
            stack_help=stack.locator('[data-assist-stack="studio-reference"] [data-demo-assist]')
            card=open_help(stack_help);result=prepare(card,'Summarize this stack','Sammanfatta högen')
            expect(result).to_contain_text('Cobalt bridge');expect(result).not_to_contain_text(changed)
            before=stack.locator('.fc-demo-desk-move select').evaluate_all('(nodes)=>nodes.map(node=>node.value)')
            result=prepare(card,'Suggest a reading order','Föreslå läsordning')
            expect(result.locator('.fc-inline-assist-recipe')).to_have_count(0)
            assert stack.locator('.fc-demo-desk-move select').evaluate_all('(nodes)=>nodes.map(node=>node.value)')==before
            image=REPORT.with_name('demo-inline-ai-'+str(width)+('.sv' if swedish else '')+'.png');page.screenshot(path=str(image),full_page=True);images.append(str(image))
            result.locator('.fc-inline-assist-citations button').first.click()
            expect(editor).to_be_focused();expect(composer).to_have_value(changed)
            assert composer.evaluate('(node)=>node===window.inlineComposer')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            checks[str(width)+'px_contextual_document_outline_preserves_original_checks_full_text_and_undo_while_stack_reading_order_never_moves_files']=True

            journey('Tasks','Uppgifter')
            task_row=page.locator('[data-demo-task-id="'+task_id+'"]')
            helper=scope('task',task_row);card=open_help(helper)
            result=prepare(card,'Find a next step','Hitta nästa steg')
            expect(result).to_contain_text(source_text);expect(result.locator('.fc-inline-assist-recipe')).to_have_count(0)
            result=prepare(card,'Clarify this task','Förtydliga uppgiften')
            apply(result,'Use the source wording','Använd källans formulering')
            expect(task_row.get_by_role('checkbox',name=source_text,exact=True)).not_to_be_checked();expect(task_row).to_contain_text('Noor')
            undo(helper);expect(task_row.get_by_role('checkbox',name=task_title.strip(),exact=True)).not_to_be_checked();expect(task_row).to_contain_text('Noor')
            page.get_by_role('button',name=text('Back to conversation','Tillbaka till samtalet'),exact=True).click()
            helper=scope('channel');card=open_help(helper);result=prepare(card,'Recap this channel','Sammanfatta kanalen')
            expect(result).to_contain_text(source_text);expect(result).not_to_contain_text(changed)
            choose_channel(page,'design',swedish)
            helper=scope('channel');card=open_help(helper);result=prepare(card,'Recap this channel','Sammanfatta kanalen')
            expect(result).not_to_contain_text(source_text);expect(result).not_to_contain_text('Cobalt bridge')
            choose_channel(page,'studio',swedish);expect(composer).to_have_value(changed)
            page.get_by_role('button',name=text('Tools','Verktyg'),exact=True).click();page.get_by_role('tab',name=text('Connections','Anslutningar'),exact=True).click()
            settings=page.locator('[data-demo-ai-settings]')
            provider=settings.get_by_role('combobox',name=text('AI provider','AI-leverantör'),exact=True)
            model=settings.get_by_role('textbox',name=text('AI model','AI-modell'),exact=True)
            expect(provider).to_have_value('grok');provider.select_option('openai');expect(model).to_have_value('gpt-4.1-mini')
            provider.select_option('custom');model.fill('workspace-inline-model')
            settings.get_by_role('button',name=text('Use AI settings','Använd AI-inställningarna'),exact=True).click()
            page.get_by_role('button',name=text('Tools','Verktyg'),exact=True).click();page.get_by_role('tab',name=text('Conversation','Samtal'),exact=True).click()
            helper=scope('channel');card=open_help(helper)
            expect(card.locator('.fc-inline-assist-provider')).to_contain_text('workspace-inline-model')
            card.get_by_text(text('What is shared?','Vad delas?'),exact=True).click()
            expect(card.locator('.fc-inline-assist-sharing')).to_contain_text(text('Your own installation (placeholder)','Din egen installation (platshållare)'))
            close_help(card,helper)
            page.get_by_role('button',name=text('Tools','Verktyg'),exact=True).click();page.get_by_role('button',name=text('Reset demo','Återställ demo'),exact=True).click();page.get_by_role('button',name=text('Reset this demo','Återställ den här demon'),exact=True).click()
            expect(page.locator('.fc-showroom-transcript').get_by_text(source_text,exact=True)).to_have_count(0)
            expect(scope('channel').locator('.fc-inline-assist-card')).to_have_count(0)
            page.reload(wait_until='networkidle')
            fresh=scope('channel');fresh.locator('[data-assist-trigger]').click();card=fresh.locator('.fc-inline-assist-card')
            expect(card.locator('.fc-inline-assist-provider')).to_contain_text('grok-4.20-0309-non-reasoning')
            expect(card.locator('[data-assist-preview]')).to_have_count(0)
            assert page.evaluate('localStorage.length')==0 and page.evaluate('sessionStorage.length')==0
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            checks[str(width)+'px_contextual_task_edits_are_cited_explicit_and_undoable_channel_sources_stay_separate_provider_preferences_and_reset_are_local']=True
        finally:visitor.close()
    return images

def port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));return listener.getsockname()[1]

def same_turn_context_reading_check(page, composer, transcript, brief, context, draft):
    """A scroll and the real context click can precede the native scroll event."""
    composer.fill('A real new message establishes following the latest conversation.')
    page.get_by_role('button',name='Send demo message',exact=True).click()
    page.wait_for_function('() => {const n=document.querySelector(".fc-showroom-transcript");return n.scrollTop+n.clientHeight>=n.scrollHeight-2}')
    composer.fill(draft);composer.evaluate('(node)=>node.setSelectionRange(3,17)')
    transcript.evaluate('(node)=>{window.naturalRaceTranscript=node}')
    # Both operations intentionally share one native event turn. No scroll handler,
    # React state, API response or product implementation is replaced or delayed.
    brief.evaluate('''button=>{const node=window.naturalRaceTranscript;window.naturalRaceBefore={height:node.scrollHeight,client:node.clientHeight,width:node.clientWidth};node.scrollTop=120;button.click()}''')
    expect(context).to_be_visible();expect(context).to_be_focused()
    assert transcript.evaluate('(node)=>node===window.naturalRaceTranscript&&Math.abs(node.scrollTop-120)<=1')
    assert transcript.evaluate('(node)=>node.scrollHeight===window.naturalRaceBefore.height&&node.clientHeight===window.naturalRaceBefore.client&&node.clientWidth===window.naturalRaceBefore.width')
    expect(composer).to_have_value(draft)
    assert composer.evaluate('(node)=>node.selectionStart===3&&node.selectionEnd===17')
    context.get_by_role('button',name='Back to conversation',exact=True).click()
    expect(context).not_to_be_visible();expect(brief).to_be_focused()
    assert transcript.evaluate('(node)=>node===window.naturalRaceTranscript&&Math.abs(node.scrollTop-120)<=1')

def natural_flow_checks(browser, app, checks, errors, unexpected):
    """Exercise context work with actual message, document and task interactions."""
    images=[]
    flow=browser.new_context(viewport={'width':1440,'height':1100},reduced_motion='reduce')
    try:
        page=flow.new_page();page.set_default_timeout(20000)
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('request',lambda request:unexpected.append(request.url) if urlsplit(request.url).hostname not in ['127.0.0.1','localhost'] or '/api/' in urlsplit(request.url).path else None)
        page.goto(app,wait_until='networkidle')
        composer=page.get_by_label('Your demo message',exact=True)
        transcript=page.get_by_role('log',name='Sample conversation',exact=True)
        journey=page.get_by_role('navigation',name='Follow the courtyard project',exact=True)
        context=page.locator('.fc-studio-context-panel')
        for index in range(8):
            composer.fill('Reading-place example '+str(index)+'. '+('Keep the detail beside this conversation. '*4))
            page.get_by_role('button',name='Send demo message',exact=True).click()
        page.wait_for_function('() => {const node=document.querySelector(".fc-showroom-transcript");return node.scrollHeight-node.clientHeight>300&&node.scrollTop+node.clientHeight>=node.scrollHeight-2}')
        draft='An unfinished thought stays exactly here. Åäö.'
        composer.fill(draft)
        composer.evaluate('(node)=>{window.naturalComposer=node;node.setSelectionRange(3,17)}')
        transcript.evaluate('(node)=>{window.naturalTranscript=node;node.scrollTop=120}')
        page.wait_for_function('() => Math.abs(window.naturalTranscript.scrollTop-120)<=1')
        brief=journey.get_by_role('button',name=re.compile(r'^Documents'))
        brief.focus();page.keyboard.press('Enter')
        expect(context).to_be_visible();expect(context).to_be_focused()
        expect(page.locator('.fc-studio-conversation')).to_be_visible()
        expect(brief).to_have_attribute('aria-pressed','true')
        assert composer.evaluate('(node)=>node===window.naturalComposer&&node.value==="An unfinished thought stays exactly here. Åäö."&&node.selectionStart===3&&node.selectionEnd===17')
        assert transcript.evaluate('(node)=>node===window.naturalTranscript&&Math.abs(node.scrollTop-120)<=1')
        document='## Keep the context\n\nThe conversation and this brief belong together.'
        context.get_by_label('Edit demo document',exact=True).fill(document)
        image=REPORT.with_name('demo-natural-brief-desktop.png');page.screenshot(path=str(image),full_page=True);images.append(str(image))
        context.get_by_role('button',name='Back to conversation',exact=True).click()
        expect(context).not_to_be_visible();expect(brief).to_be_focused()
        expect(page.get_by_label('Edit courtyard brief',exact=True)).to_have_value(document)
        assert composer.evaluate('(node)=>node===window.naturalComposer&&node.selectionStart===3&&node.selectionEnd===17')
        assert transcript.evaluate('(node)=>node===window.naturalTranscript&&Math.abs(node.scrollTop-120)<=1')
        checks['context_brief_keeps_actual_conversation_composer_dom_draft_selection_reading_and_origin_focus']=True

        same_turn_context_reading_check(page,composer,transcript,brief,context,draft)
        checks['same_turn_native_scroll_and_real_context_click_preserve_reading_without_waiting_for_scroll_event']=True

        brief.focus();page.keyboard.press('Enter')
        context.get_by_role('button',name='Agree a next step',exact=True).click()
        expect(context).to_be_focused()
        task_count=context.get_by_role('checkbox').count()
        decision='Continue the shared plan without leaving the conversation.'
        next_step='Bring the annotated courtyard brief'
        context.get_by_label('Sample decision',exact=True).fill(decision)
        context.get_by_label('Next step',exact=True).fill(next_step)
        context.get_by_label('Owner',exact=True).select_option('Elias')
        context.get_by_role('button',name='View shared brief',exact=True).click()
        expect(context.get_by_label('Edit demo document',exact=True)).to_have_value(document)
        journey.get_by_role('button',name=re.compile(r'^Tasks')).click()
        expect(context.get_by_label('Sample decision',exact=True)).to_have_value(decision)
        expect(context.get_by_label('Next step',exact=True)).to_have_value(next_step)
        expect(context.get_by_label('Owner',exact=True)).to_have_value('Elias')
        assert context.get_by_role('checkbox').count()==task_count
        context.get_by_role('button',name='Record sample decision',exact=True).click()
        expect(context.locator('.fc-studio-decision-record')).to_be_focused()
        assert context.get_by_role('checkbox').count()==task_count+1
        image=REPORT.with_name('demo-natural-decision-desktop.png');page.screenshot(path=str(image),full_page=True);images.append(str(image))
        context.get_by_role('button',name='Back to conversation',exact=True).click()
        outcome=page.get_by_role('region',name='Agreed next step',exact=True)
        expect(outcome).to_be_visible();expect(outcome).to_contain_text(decision);expect(outcome).to_contain_text('Elias')
        expect(outcome.get_by_role('checkbox',name=next_step,exact=True)).to_have_count(1)
        outcome.get_by_role('checkbox',name=next_step,exact=True).check()
        outcome.get_by_role('button',name='View decision',exact=True).click()
        expect(context.get_by_role('checkbox',name=next_step,exact=True)).to_be_checked()
        assert context.get_by_role('checkbox').count()==task_count+1
        context.get_by_role('checkbox',name=next_step,exact=True).uncheck()
        context.get_by_role('button',name='Back to conversation',exact=True).click()
        expect(outcome.get_by_role('checkbox',name=next_step,exact=True)).not_to_be_checked()
        checks['context_decision_draft_survives_brief_switch_and_compact_next_step_updates_the_same_single_task']=True

        for channel in ['design','product']:
            choose_channel(page,channel)
            value='An unfinished '+channel+' conversation.'
            composer.fill(value);composer.evaluate('(node)=>node.setSelectionRange(2,12)')
            trigger=journey.get_by_role('button',name=re.compile(r'^Tasks'))
            trigger.focus();page.keyboard.press('Enter');expect(context).to_be_focused()
            expect_channel(page,channel)
            journey.get_by_role('button',name='Conversation',exact=True).click()
            expect(context).not_to_be_visible();expect(trigger).to_be_focused()
            expect_channel(page,channel)
            expect(composer).to_have_value(value)
            assert composer.evaluate('(node)=>node===window.naturalComposer&&node.selectionStart===2&&node.selectionEnd===12')
        choose_channel(page,'studio')
        expect(composer).to_have_value(draft)
        assert transcript.evaluate('(node)=>node===window.naturalTranscript&&Math.abs(node.scrollTop-120)<=1')
        checks['context_conversation_return_preserves_design_product_channels_and_channel_scoped_drafts_and_reading']=True

        for name in ['Connections','Appearance']:
            page.get_by_role('button',name='Tools',exact=True).click();page.get_by_role('tab',name=name,exact=True).click()
            page.get_by_role('button',name='Tools',exact=True).click();page.get_by_role('tab',name='Conversation',exact=True).click()
            expect(transcript).to_be_visible();expect(composer).to_have_value(draft)
            assert transcript.evaluate('(node)=>node===window.naturalTranscript&&Math.abs(node.scrollTop-120)<=1')
            assert composer.evaluate('(node)=>node===window.naturalComposer')
        brief.focus();page.keyboard.press('Enter');expect(context).to_be_focused();page.keyboard.press('Escape')
        expect(context).not_to_be_visible();expect(brief).to_be_focused()
        expect(composer).to_have_value(draft)
        composer.fill('My own next message follows the latest conversation.')
        page.get_by_role('button',name='Send demo message',exact=True).click()
        page.wait_for_function('() => {const node=window.naturalTranscript;return node.scrollTop+node.clientHeight>=node.scrollHeight-2}')
        expect(composer).to_have_value('')
        checks['settings_navigation_and_escape_preserve_conversation_dom_reading_then_own_send_follows_latest']=True
        assert page.evaluate('localStorage.length')==0 and page.evaluate('sessionStorage.length')==0
    finally:flow.close()

    for width in [390,320]:
        mobile=browser.new_context(viewport={'width':width,'height':844},reduced_motion='reduce')
        try:
            page=mobile.new_page();page.set_default_timeout(20000)
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:unexpected.append(request.url) if urlsplit(request.url).hostname not in ['127.0.0.1','localhost'] or '/api/' in urlsplit(request.url).path else None)
            page.goto(app,wait_until='networkidle')
            page.get_by_role('button',name='Tools',exact=True).click();page.get_by_role('button',name='SV',exact=True).click();page.keyboard.press('Escape')
            choose_channel(page,'design',True)
            composer=page.get_by_label('Ditt demomeddelande',exact=True)
            value='Ett oskickat utkast i designkanalen. Behåll markeringen.'
            composer.fill(value);composer.evaluate('(node)=>{window.naturalMobileComposer=node;node.setSelectionRange(4,15)}')
            source=page.get_by_role('navigation',name='Följ gårdsprojektet',exact=True).get_by_role('button',name=re.compile(r'^Dokument'))
            source.scroll_into_view_if_needed();source.focus()
            position=page.evaluate('({x:window.scrollX,y:window.scrollY})')
            source.press('Enter')
            context=page.locator('.fc-studio-context-panel')
            expect(context).to_be_visible();expect(context).to_be_focused()
            expect(page.locator('.fc-studio-conversation')).not_to_be_visible()
            assert composer.evaluate('(node)=>node===window.naturalMobileComposer&&node.value==="Ett oskickat utkast i designkanalen. Behåll markeringen."&&node.selectionStart===4&&node.selectionEnd===15')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            context.get_by_label('Redigera demodokument',exact=True).focus();page.keyboard.press('Escape')
            expect(context).not_to_be_visible();expect(source).to_be_focused()
            expect_channel(page,'design',True)
            expect(composer).to_have_value(value)
            assert composer.evaluate('(node)=>node===window.naturalMobileComposer&&node.selectionStart===4&&node.selectionEnd===15')
            page.wait_for_function('(position)=>Math.abs(window.scrollY-Math.min(position.y,Math.max(0,document.documentElement.scrollHeight-innerHeight)))<=1',arg=position)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            source.press('Enter');expect(context).to_be_focused()
            page.get_by_role('navigation',name='Följ gårdsprojektet',exact=True).get_by_role('button',name=re.compile(r'^Uppgifter')).click()
            expect(context.get_by_role('heading',name='Gör nästa steg tydligt.',exact=True)).to_be_visible()
            context.get_by_label('Exempelbeslut',exact=True).fill('Följ upp designförslaget vid teamets genomgång.')
            next_step='Ta med designförslaget vid '+str(width)+' pixlar'
            context.get_by_label('Nästa steg',exact=True).fill(next_step)
            context.get_by_label('Ansvarig',exact=True).select_option('Nora')
            context.get_by_role('button',name='Spara exempelbeslut',exact=True).click()
            expect(context.locator('.fc-studio-decision-record')).to_be_focused()
            image=REPORT.with_name('demo-natural-context-'+str(width)+'-sv.png');page.screenshot(path=str(image),full_page=True);images.append(str(image))
            context.get_by_role('button',name='Tillbaka till samtalet',exact=True).click()
            expect(source).to_be_focused();expect(composer).to_have_value(value)
            outcome=page.get_by_role('region',name='Överenskommet nästa steg',exact=True)
            expect(outcome.get_by_role('checkbox',name=next_step,exact=True)).to_have_count(1)
            outcome.get_by_role('checkbox',name=next_step,exact=True).check()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            image=REPORT.with_name('demo-natural-outcome-'+str(width)+'-sv.png');page.screenshot(path=str(image),full_page=True);images.append(str(image))
            checks['swedish_'+str(width)+'px_context_escape_and_return_restore_actual_composer_channel_selection_window_scroll_and_focus']=True
            source.scroll_into_view_if_needed();source.focus();position=page.evaluate('({x:window.scrollX,y:window.scrollY})');source.press('Enter');expect(context).to_be_focused()
            page.get_by_role('button',name='Verktyg',exact=True).click();page.get_by_role('tab',name='Utseende',exact=True).click()
            page.get_by_role('button',name='Verktyg',exact=True).click();page.get_by_role('tab',name='Samtal',exact=True).click()
            expect(composer).to_have_value(value);expect_channel(page,'design',True)
            page.wait_for_function('(position)=>Math.abs(window.scrollY-Math.min(position.y,Math.max(0,document.documentElement.scrollHeight-innerHeight)))<=1',arg=position)
            assert composer.evaluate('(node)=>node===window.naturalMobileComposer&&node.selectionStart===4&&node.selectionEnd===15')
            source.press('Enter');expect(context).to_be_focused();choose_channel(page,'product',True)
            expect(context).not_to_be_visible();expect(composer).to_have_value('')
            choose_channel(page,'design',True)
            expect(composer).to_have_value(value)
            page.wait_for_function('(position)=>Math.abs(window.scrollY-Math.min(position.y,Math.max(0,document.documentElement.scrollHeight-innerHeight)))<=1',arg=position)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            checks['swedish_'+str(width)+'px_open_context_settings_and_channel_roundtrip_keep_original_conversation_window_position']=True
        finally:mobile.close()
    return images

def main():
    checks, stage, process = {}, 'private Vite startup', None
    source_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE}
    with tempfile.TemporaryDirectory(prefix='freecollab-showroom-') as directory:
        private=Path(directory); app_port=port(); app=f'http://127.0.0.1:{app_port}'
        web=private/'web'; shutil.copytree(ROOT/'frontend',web,ignore=shutil.ignore_patterns('node_modules','dist','dist-widget','tests','.vite'))
        (web/'node_modules').symlink_to(ROOT/'frontend/node_modules',target_is_directory=True)
        (web/'index.html').write_text('<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width, initial-scale=1" /></head><body><div id="root"></div><script type="module" src="/entry.tsx"></script></body></html>')
        for entry, source in [('app', 'main'), ('website', 'website-entry')]:
            (web/(entry+'.html')).write_text('<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width, initial-scale=1" /></head><body><div id="root"></div><script type="module" src="/src/'+source+'.tsx"></script></body></html>')
        (web/'entry.tsx').write_text('''import React from 'react';import{createRoot}from'react-dom/client';import{Demo}from'./src/components/Demo';import{applyLocale}from'./src/i18n';import'@fontsource-variable/inter';import'@fontsource-variable/manrope';import'@fontsource-variable/source-serif-4';import'./src/index.css';window.showroomLocale=applyLocale;window.showroomBack=0;window.showroomOpen=0;createRoot(document.getElementById('root')).render(<React.StrictMode><Demo onBack={()=>window.showroomBack++} onOpenApp={()=>window.showroomOpen++}/></React.StrictMode>);''')
        launcher=private/'serve.mjs'; launcher.write_text('import{createServer}from'+json.dumps(str(ROOT/'frontend/node_modules/vite/dist/node/index.js'))+';import react from'+json.dumps(str(ROOT/'frontend/node_modules/@vitejs/plugin-react/dist/index.js'))+';const server=await createServer({configFile:false,root:'+json.dumps(str(web))+',cacheDir:'+json.dumps(str(private/'vite-cache'))+',plugins:[react()],server:{host:"127.0.0.1",port:'+str(app_port)+',strictPort:true,hmr:false,watch:{ignored:["**"]},fs:{allow:'+json.dumps([str(private),str(ROOT/'frontend')])+'}}});await server.listen();')
        stream=(private/'vite.log').open('w')
        try:
            process=subprocess.Popen(['node',str(launcher)],cwd=ROOT/'frontend',stdout=stream,stderr=stream,start_new_session=True)
            for _ in range(100):
                if process.poll() is not None: raise RuntimeError((private/'vite.log').read_text()[-2500:])
                try:
                    with urlopen(app,timeout=1):break
                except Exception:time.sleep(.15)
            else:raise RuntimeError('Private demo server did not start')
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),args=['--no-sandbox'])
                context=browser.new_context(viewport={'width':1440,'height':1100},reduced_motion='reduce',accept_downloads=True)
                page=context.new_page();page.set_default_timeout(20000);errors=[];unexpected=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('request',lambda request:unexpected.append(request.url) if urlsplit(request.url).hostname not in ['127.0.0.1','localhost'] or '/api/' in urlsplit(request.url).path else None)
                page.goto(app,wait_until='networkidle')
                def tools():
                    toggle=page.get_by_role('button',name='Tools',exact=True)
                    if not toggle.count():toggle=page.get_by_role('button',name='Verktyg',exact=True)
                    if toggle.get_attribute('aria-expanded')!='true':toggle.click()
                def section(name):
                    tools();page.get_by_role('tab',name=name,exact=True).click()
                def reset_menu():
                    tools();toggle=page.get_by_role('button',name='Reset demo',exact=True)
                    if not toggle.count():toggle=page.get_by_role('button',name='Återställ demo',exact=True)
                    toggle.click()
                stage='public guest showroom and literal local message'
                expect(page.get_by_role('heading',name='Shall we open up to the courtyard?',exact=True)).to_be_visible()
                expect(page.get_by_text('Sample workspace · fictional people · changes stay in this tab',exact=True)).to_be_visible()
                expect(page.get_by_role('img',name='A sunlit brick courtyard with trees, an open doorway and a shared table',exact=True)).to_be_visible()
                page.evaluate('document.fonts.ready')
                fresh_log=page.get_by_role('log',name='Sample conversation',exact=True)
                opening=page.get_by_role('heading',name='Shall we open up to the courtyard?',exact=True)
                assert fresh_log.evaluate('(node)=>node.scrollTop')==0,'A fresh conversation must start at its opening.'
                assert opening.bounding_box()['y']>=fresh_log.bounding_box()['y']-1,'The opening heading must not be clipped above the conversation.'
                stage='real focus and accessible bounded personal layout'
                materials=page.locator('.fc-studio-materials')
                page.get_by_role('button',name='Focus',exact=True).click();expect(materials).not_to_be_visible()
                expect(page.get_by_role('log',name='Sample conversation')).to_be_visible()
                page.get_by_role('button',name='Leave focus',exact=True).click();expect(materials).to_be_visible()
                page.get_by_role('button',name='Arrange',exact=True).click()
                earlier=page.get_by_role('button',name='Move Shared brief earlier',exact=True)
                earlier.focus();page.keyboard.press('Enter')
                assert materials.locator('[data-material]').evaluate_all('(items)=>items.map(item=>item.dataset.material)')==['brief','courtyard']
                page.get_by_role('button',name='Pin Courtyard image',exact=True).click()
                expect(page.get_by_role('button',name='Pin Courtyard image',exact=True)).to_have_attribute('aria-pressed','true')
                assert materials.locator('[data-material]').first.get_attribute('data-material')=='courtyard'
                page.get_by_label('Material position',exact=True).select_option('left')
                expect(page.locator('.fc-studio-scene')).to_have_attribute('data-material-position','left')
                page.get_by_role('button',name='Reset layout',exact=True).click()
                expect(page.locator('.fc-studio-scene')).to_have_attribute('data-material-position','right')
                expect(page.get_by_role('button',name='Pin Courtyard image',exact=True)).to_have_attribute('aria-pressed','false')
                page.get_by_role('button',name='Close layout controls',exact=True).click()
                page.get_by_label('Edit courtyard brief',exact=True).fill('## Shared edit from the conversation')
                section('Documents');expect(page.get_by_label('Edit demo document',exact=True)).to_have_value('## Shared edit from the conversation')
                section('Conversation')
                checks['real_focus_reorder_pin_position_reset_and_shared_document']=True
                initial=page.get_by_role('log',name='Sample conversation').locator('article').count()
                text='Local note <img src="https://invalid.example/tracker">'
                page.get_by_label('Your demo message',exact=True).fill(text);page.get_by_role('button',name='Send demo message',exact=True).click()
                expect(page.get_by_role('log').get_by_text(text,exact=True)).to_be_visible()
                assert page.get_by_role('log').locator('img').count()==0
                page.wait_for_timeout(1100)
                assert page.get_by_role('log').locator('article').count()==initial+1
                choose_channel(page,'design')
                expect(page.get_by_role('log').get_by_text(text,exact=True)).to_have_count(0)
                choose_channel(page,'studio')
                expect(page.get_by_role('log').get_by_text(text,exact=True)).to_be_visible()
                checks['guest_literal_channel_scoped_memory_chat_no_bot']=True
                stage='independent unsent drafts survive channel and tool navigation'
                draft='An unsent courtyard thought. Keep the exact wording.'
                other_draft='An independent design thought.'
                page.get_by_label('Your demo message',exact=True).fill(draft)
                expect(page.get_by_text('Unsent draft · kept in this tab',exact=True)).to_be_visible()
                REPORT.parent.mkdir(parents=True,exist_ok=True)
                draft_image=REPORT.with_name('demo-showroom-draft.png');page.screenshot(path=str(draft_image),full_page=True)
                choose_channel(page,'design')
                expect(page.get_by_label('Your demo message',exact=True)).to_have_value('')
                page.get_by_label('Your demo message',exact=True).fill(other_draft)
                choose_channel(page,'studio')
                expect(page.get_by_label('Your demo message',exact=True)).to_have_value(draft)
                page.get_by_role('button',name='Send demo message',exact=True).click()
                expect(page.get_by_label('Your demo message',exact=True)).to_have_value('')
                choose_channel(page,'design')
                expect(page.get_by_label('Your demo message',exact=True)).to_have_value(other_draft)
                section('Projects');section('Conversation')
                expect(page.get_by_label('Your demo message',exact=True)).to_have_value(other_draft)
                choose_channel(page,'studio')
                checks['independent_memory_drafts_survive_navigation_and_only_sent_draft_clears']=True
                stage='actual project checkbox/add/filter'
                section('Projects')
                box=page.get_by_role('checkbox',name='Review the courtyard materials',exact=True);box.check();expect(box).to_be_checked()
                page.get_by_label('New demo task',exact=True).fill('My new courtyard step');page.get_by_role('button',name='Add task',exact=True).click()
                expect(page.get_by_role('checkbox',name='My new courtyard step',exact=True)).to_be_visible()
                page.get_by_role('button',name='Done',exact=True).click();expect(page.get_by_role('checkbox',name='My new courtyard step',exact=True)).to_have_count(0)
                page.get_by_role('button',name='All tasks',exact=True).click()
                checks['actual_project_add_toggle_and_filter']=True
                stage='actual edited Markdown file download'
                section('Documents')
                edited='## My courtyard idea\n\nLocal **edited text**, preserved exactly.'
                page.get_by_label('Edit demo document',exact=True).fill(edited)
                with page.expect_download() as downloaded:page.get_by_role('button',name='Download Markdown',exact=True).click()
                download=downloaded.value;assert download.suggested_filename=='freecollab-demo-brief.md'
                assert Path(download.path()).read_text()=='# Project brief\n\n'+edited+'\n'
                expand_stack(page,'Reference material').get_by_role('button',name='Studio notes',exact=True).click();assert page.get_by_label('Edit demo document').input_value()!=edited
                expand_stack(page,'Reference material').get_by_role('button',name='Project brief',exact=True).click();expect(page.get_by_label('Edit demo document')).to_have_value(edited)
                checks['edited_document_markdown_actual_download_and_independent_documents']=True
                stage='one conversation, the same shared brief, and a real local decision/owned task'
                journey=page.get_by_role('navigation',name='Follow the courtyard project',exact=True)
                journey.get_by_role('button',name='Conversation',exact=True).click()
                expect(page.get_by_label('Edit courtyard brief',exact=True)).to_have_value(edited)
                journey_brief='## A shared courtyard direction\n\nKeep the doorway open and compare two timber samples.'
                page.get_by_label('Edit courtyard brief',exact=True).fill(journey_brief)
                journey.get_by_role('button',name=re.compile(r'^Documents')).click()
                expect(page.get_by_label('Edit demo document',exact=True)).to_have_value(journey_brief)
                page.get_by_role('button',name='Agree a next step',exact=True).click()
                task_count=page.get_by_role('checkbox').count()
                decision='Choose warm timber <script>literal decision text</script>'
                next_step='Bring two courtyard timber samples'
                page.get_by_label('Sample decision',exact=True).fill(decision)
                page.get_by_label('Next step',exact=True).fill(next_step)
                page.get_by_label('Owner',exact=True).select_option('Noor')
                page.get_by_role('button',name='Record sample decision',exact=True).click()
                expect(page.locator('.fc-studio-context-panel').get_by_text(decision,exact=True)).to_be_visible()
                expect(page.get_by_text('Recorded in this demo',exact=True)).to_be_visible()
                assert page.locator('.fc-studio-decision script').count()==0
                follow_up=page.get_by_role('checkbox',name=next_step,exact=True)
                expect(follow_up).to_have_count(1);follow_up.check()
                assert page.get_by_role('checkbox').count()==task_count+1
                expect(page.locator('.fc-studio-decision-record')).to_contain_text('Noor')
                page.get_by_role('button',name='Revise sample decision',exact=True).click()
                page.get_by_label('Sample decision',exact=True).fill('Keep this direction, with a clearer explanation.')
                page.get_by_role('button',name='Record sample decision',exact=True).click()
                expect(follow_up).to_be_checked()
                page.get_by_role('button',name='Revise sample decision',exact=True).click()
                revised_step='Bring the revised material samples'
                page.get_by_label('Next step',exact=True).fill(revised_step)
                page.get_by_label('Owner',exact=True).select_option('Elias')
                page.get_by_role('button',name='Record sample decision',exact=True).click()
                expect(follow_up).to_have_count(0)
                expect(page.get_by_role('checkbox',name=revised_step,exact=True)).not_to_be_checked()
                assert page.get_by_role('checkbox').count()==task_count+1
                page.get_by_role('button',name='View shared brief',exact=True).click()
                expect(page.get_by_label('Edit demo document',exact=True)).to_have_value(journey_brief)
                journey.get_by_role('button',name=re.compile(r'^Tasks')).click()
                expect(page.get_by_role('checkbox',name=revised_step,exact=True)).to_be_visible()
                expect(journey.get_by_role('button',name=re.compile(r'^Tasks'))).to_have_attribute('aria-pressed','true')
                checks['same_shared_brief_local_decision_owned_task_revision_and_completion']=True
                stage='Microsoft and Google preparation only'
                section('Connections')
                for provider in ['Microsoft 365','Google Workspace']:
                    page.get_by_role('button',name=provider+' Preparation guide',exact=True).click()
                    if provider=='Microsoft 365': expect(page.get_by_text('Files.ReadWrite.All · offline_access (files) / Calendars.ReadWrite · offline_access (calendar)',exact=True)).to_be_visible()
                    for box in page.get_by_role('checkbox').all():
                        expect(box).not_to_be_checked();box.check()
                    expect(page.get_by_text('Checking a box changes this example checklist only. No account is connected, authorized or tested here.',exact=True)).to_be_visible()
                    expect(page.get_by_label('Example callback — replace with your installation’s exact address',exact=True)).to_have_value('https://your-freecollab.example/api/sync/oauth/callback')
                checks['both_provider_guides_no_fake_authorization_or_connectivity']=True
                stage='appearance scoped to showroom; keyboard tabs'
                root_style=page.evaluate('document.documentElement.getAttribute("style")')
                section('Appearance')
                page.get_by_role('button',name='Fjord Cool stone & ocean',exact=True).click()
                page.get_by_label('Demo typeface',exact=True).select_option('manrope')
                assert page.locator('.fc-showroom').evaluate('(element)=>getComputedStyle(element).getPropertyValue("--fc-accent-original").trim()')=='#365f79'
                assert page.evaluate('document.documentElement.getAttribute("style")')==root_style
                assert page.locator('.fc-showroom').evaluate('(element)=>getComputedStyle(element).fontFamily').startswith('"Manrope Variable"')
                tools();page.get_by_role('tab',name='Appearance',exact=True).focus();page.keyboard.press('Home')
                expect(page.get_by_role('tab',name='Conversation',exact=True)).to_be_focused()
                expect(page.get_by_role('tab',name='Conversation',exact=True)).to_have_attribute('aria-selected','true')
                checks['appearance_scoped_without_account_mutation_and_keyboard_tabs']=True
                page.keyboard.press('Escape')
                REPORT.parent.mkdir(parents=True,exist_ok=True)
                desktop=REPORT.with_name('demo-showroom-desktop.png');page.screenshot(path=str(desktop),full_page=True)
                stage='reset focus trap and all local data cleared'
                page.get_by_role('button',name='Arrange',exact=True).click()
                page.get_by_role('button',name='Pin Shared brief',exact=True).click()
                page.get_by_label('Material position',exact=True).select_option('left')
                page.get_by_role('button',name='Close layout controls',exact=True).click()
                page.get_by_role('button',name='Focus',exact=True).click()
                reset_menu()
                dialog=page.get_by_role('alertdialog');expect(dialog).to_be_visible()
                page.keyboard.press('Shift+Tab');assert page.evaluate('document.querySelector("[role=alertdialog]").contains(document.activeElement)')
                page.keyboard.press('Escape');expect(dialog).to_have_count(0)
                reset_menu();page.get_by_role('button',name='Reset this demo',exact=True).click()
                expect(page.get_by_label('Your demo message',exact=True)).to_have_value('')
                choose_channel(page,'design')
                expect(page.get_by_label('Your demo message',exact=True)).to_have_value('')
                choose_channel(page,'studio')
                expect(page.get_by_role('log').get_by_text(text,exact=True)).to_have_count(0)
                expect(page.locator('.fc-studio-materials')).to_be_visible()
                expect(page.locator('.fc-studio-scene')).to_have_attribute('data-material-position','right')
                assert page.locator('.fc-studio-pinned').count()==0
                assert page.locator('.fc-showroom').evaluate('(element)=>getComputedStyle(element).getPropertyValue("--fc-accent-original").trim()')=='#ee593f'
                section('Connections')
                for provider in ['Microsoft 365','Google Workspace']:
                    page.get_by_role('button',name=provider+' Preparation guide',exact=True).click()
                    for box in page.get_by_role('checkbox').all():expect(box).not_to_be_checked()
                section('Projects');expect(page.get_by_role('checkbox',name='My new courtyard step',exact=True)).to_have_count(0)
                expect(page.get_by_role('checkbox',name=revised_step,exact=True)).to_have_count(0)
                expect(page.get_by_label('Sample decision',exact=True)).to_have_value('Use warm, durable materials and keep the courtyard open.')
                expect(page.get_by_label('Owner',exact=True)).to_have_value('Maja')
                expect(page.get_by_role('checkbox',name='Review the courtyard materials',exact=True)).not_to_be_checked()
                section('Documents');assert page.get_by_label('Edit demo document').input_value()!=edited
                checks['focus_trapped_reset_clears_messages_tasks_documents_checklists_theme_and_layout']=True
                section('Conversation');page.screenshot(path=str(desktop),full_page=True)
                stage='Swedish and 320 px every section'
                tools();page.get_by_role('button',name='SV',exact=True).click();page.keyboard.press('Escape');page.set_viewport_size({'width':390,'height':844})
                expect(page.get_by_role('heading',name='Ska vi öppna upp mot gården?',exact=True)).to_be_visible()
                assert page.evaluate('document.documentElement.lang')=='en', 'demo language mutated app language'
                for width in [390,320]:
                    page.set_viewport_size({'width':width,'height':844})
                    for name in ['Samtal','Projekt','Dokument','Anslutningar','Utseende']:
                        section(name)
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),name+' horizontal overflow at '+str(width)
                section('Projekt')
                expect(page.get_by_role('heading',name='Gör nästa steg tydligt.',exact=True)).to_be_visible()
                page.get_by_label('Exempelbeslut',exact=True).fill('Behåll den öppna gården.')
                page.get_by_label('Nästa steg',exact=True).fill('Ta med två materialprover')
                page.get_by_label('Ansvarig',exact=True).select_option('Nora')
                page.get_by_role('button',name='Spara exempelbeslut',exact=True).click()
                expect(page.get_by_role('checkbox',name='Ta med två materialprover',exact=True)).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                journey_image=REPORT.with_name('demo-showroom-decision-mobile.png');page.screenshot(path=str(journey_image),full_page=True)
                checks['swedish_decision_owner_and_next_task_at_320px']=True
                section('Samtal')
                mobile=REPORT.with_name('demo-showroom-mobile.png');page.screenshot(path=str(mobile),full_page=True)
                reset_menu();expect(page.get_by_role('alertdialog')).to_be_visible();page.keyboard.press('Escape')
                section('Samtal');page.get_by_label('Ditt demomeddelande',exact=True).fill('Ett lokalt meddelande');page.get_by_role('button',name='Skicka demomeddelande',exact=True).click()
                page.reload(wait_until='networkidle');expect(page.get_by_text('Ett lokalt meddelande',exact=True)).to_have_count(0)
                section('Projects');expect(page.get_by_role('checkbox',name='Ta med två materialprover',exact=True)).to_have_count(0)
                expect(page.get_by_label('Sample decision',exact=True)).to_have_value('Use warm, durable materials and keep the courtyard open.')
                checks['scoped_swedish_all_sections_390_320px_and_reload_discards_demo_data']=True
                page.get_by_role('button',name='Back to FreeCollab',exact=True).click();tools();page.get_by_role('button',name='Open the app',exact=True).click()
                assert page.evaluate('window.showroomBack')==1 and page.evaluate('window.showroomOpen')==1
                assert page.evaluate('localStorage.length')==0
                assert page.evaluate('sessionStorage.length')==0
                assert not errors,errors
                assert not unexpected,unexpected
                checks['explicit_exit_callbacks_no_storage_api_provider_or_browser_errors']=True
                stage='actual App and public website provider destinations, history, direct hashes and reload'
                for entry in ['app','website']:
                    routed=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                    try:
                        visitor=routed.new_page();visitor.set_default_timeout(25000)
                        visitor.on('pageerror',lambda error:errors.append(str(error)))
                        visitor.on('request',lambda request:unexpected.append(request.url) if urlsplit(request.url).hostname not in ['127.0.0.1','localhost'] or '/api/' in urlsplit(request.url).path else None)
                        address=app+'/'+entry+'.html'
                        for provider,name in [('microsoft','Microsoft 365'),('google','Google Workspace')]:
                            visitor.goto(address,wait_until='networkidle')
                            card=visitor.locator('.fc-public-provider-grid article').filter(has=visitor.get_by_role('heading',name=name,exact=True))
                            card.get_by_role('button',name='Explore connection setup',exact=True).click()
                            guide=visitor.get_by_role('button',name=name+' Preparation guide',exact=True)
                            expect(guide).to_have_attribute('aria-pressed','true')
                            expect(visitor.get_by_role('tabpanel',name='Connections',exact=True)).to_be_visible()
                            assert visitor.evaluate('location.hash')=='#demo=connections&provider='+provider
                            visitor.go_back(wait_until='domcontentloaded')
                            expect(visitor.locator('#public-heading')).to_be_visible()
                            visitor.go_forward(wait_until='domcontentloaded')
                            expect(guide).to_have_attribute('aria-pressed','true')
                            visitor.reload(wait_until='networkidle');expect(guide).to_have_attribute('aria-pressed','true')
                            visitor.get_by_role('button',name='Back to FreeCollab',exact=True).click()
                            expect(visitor.locator('#public-heading')).to_be_visible();assert visitor.evaluate('location.hash')==''
                        visitor.goto(address+'#demo=documents',wait_until='networkidle')
                        routed_edit='A shared brief kept while following an explicit provider route.'
                        visitor.get_by_label('Edit demo document',exact=True).fill(routed_edit)
                        visitor.evaluate("location.hash='demo=connections&provider=google'")
                        expect(visitor.get_by_role('button',name='Google Workspace Preparation guide',exact=True)).to_have_attribute('aria-pressed','true')
                        visitor.evaluate("location.hash='demo=documents'")
                        expect(visitor.get_by_label('Edit demo document',exact=True)).to_have_value(routed_edit)
                        visitor.reload(wait_until='networkidle')
                        expect(visitor.get_by_label('Edit demo document',exact=True)).not_to_have_value(routed_edit)
                        if entry=='website':
                            assert visitor.evaluate('localStorage.length')==0 and visitor.evaluate('sessionStorage.length')==0
                        checks[entry+'_actual_entry_provider_buttons_history_direct_hash_reload_and_shared_local_state']=True
                        visitor.goto(address+'#demo=assistant&provider=grok',wait_until='networkidle')
                        direct_ai=visitor.locator('[data-demo-assist][data-assist-scope="channel"]:visible')
                        expect(visitor.get_by_role('log')).to_be_visible()
                        expect(visitor.locator('[data-ai-preview]')).to_have_count(0)
                        direct_ai.locator('[data-assist-trigger]').click()
                        expect(direct_ai.get_by_text('Uses selected text; no AI request.',exact=True)).to_be_visible()
                        expect(visitor.get_by_role('button',name='Microsoft 365 Preparation guide',exact=True)).to_have_count(0)
                        direct_ai.get_by_role('button',name='Prepare example',exact=True).click()
                        expect(direct_ai.locator('[data-assist-preview="current"]')).to_be_visible()
                        visitor.reload(wait_until='networkidle')
                        expect(direct_ai).to_be_visible()
                        expect(direct_ai.locator('.fc-inline-assist-card')).to_have_count(0)
                        checks[entry+'_legacy_ai_hash_returns_to_conversation_with_contextual_help_no_sso_misroute_and_no_persisted_result']=True
                    finally:routed.close()
                assert not errors,errors
                assert not unexpected,unexpected
                stage='natural context, reading, draft, focus, channel and compact outcome continuity'
                natural_images=natural_flow_checks(browser,app,checks,errors,unexpected)
                stage='visible channels, local management, paper stacks, move undo and reference continuity'
                usability_images=usability_checks(browser,app,checks,errors,unexpected)
                stage='content-derived suggestions, explicit links, stale matches, privacy and continuity'
                connection_images=connection_checks(browser,app,checks,errors,unexpected)
                stage='source-aware message tasks, less typing, provenance and channel continuity'
                message_task_images=message_task_checks(browser,app,checks,errors,unexpected)
                stage='public local AI preview, exact selected sources, stale guards and explicit task review'
                public_ai_images=public_ai_checks(browser,app,checks,errors,unexpected)
                assert not errors,errors
                assert not unexpected,unexpected
                browser.close()
            REPORT.write_text(json.dumps({'ok':False,'scope':'private Chromium synthetic showroom and actual guest App/public website entries; no real users/accounts/providers. Credential-ignored/no-storage contract applies to standalone website, not signed-in full-App bootstrap.','checks':checks,'api_provider_requests':unexpected,'screenshots':[str(desktop),str(mobile),str(draft_image),str(journey_image)]+natural_images+usability_images+connection_images+message_task_images+public_ai_images,'source_sha256':source_hashes},indent=2)+'\n')
        except Exception:
            print('Failed stage: '+stage)
            print((private/'vite.log').read_text()[-3500:]);raise
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=8)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            stream.close()
    assert process is not None and process.poll() is not None and not private.exists()
    final_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE}
    assert final_hashes==source_hashes,'Bound demo source changed during the proof; repeat after source freeze.'
    result=json.loads(REPORT.read_text());result['ok']=True;result['source_unchanged_after_run']=True;result['cleanup']='Own Vite process exited; private source snapshot/cache/browser contexts removed; no API/database/container fixture or builds used.'
    REPORT.write_text(json.dumps(result,indent=2)+'\n')
    print('Demo showroom browser proof passed: '+str(len(checks))+' checks.')

if __name__=='__main__':main()
