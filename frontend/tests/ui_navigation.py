"""Real, accessible Chat navigation shared by the browser proof harnesses.

These helpers only wait for the rendered product and click its visible controls.
They do not alter application state, seed sessions, dispatch events or bypass a
dialog. Call them at Chat entry points, not for controls inside another panel.
"""
import re
import json
from playwright.sync_api import expect


_MORE_TOOLS = re.compile(r'^(More channel tools|Fler kanalverktyg)$')
_CHANNEL_TOGGLE = re.compile(r'^(Switch channel|Byt kanal|Toggle channels|Visa eller dölj kanaler)$')
_CLOSE_CHANNELS = re.compile(r'^(Close channels|Stäng kanaler)$')
_CHANNELS = re.compile(r'^(Channels|Kanaler)$')
_MESSAGE_ACTIONS = re.compile(r'^(More message actions|Fler meddelandeåtgärder)$')


def chat_ready(page, timeout=90000):
    shell = page.locator('.fc-chat')
    shell.wait_for(state='visible', timeout=timeout)
    return shell


def reveal_chat_tool(page, name, timeout=90000):
    """Reveal a named Chat tool through More when its direct control is hidden."""
    chat_ready(page, timeout)
    # The drawer is modal. Close it through its visible control before using
    # the main toolbar, so the caller gets a visible return target after a panel.
    close_channels(page)
    target = page.get_by_role('button', name=name, exact=True)
    if not target.is_visible():
        page.get_by_role('button', name=_MORE_TOOLS).click()
    target.wait_for(state='visible', timeout=timeout)
    return target


def open_chat_tool(page, name, timeout=90000):
    reveal_chat_tool(page, name, timeout).click()


def activate_chat_feature(page, feature_id, keyboard=False, expect_closed=True, before_select=None):
    """Choose the real feature list without replacing an unsent conversation draft."""
    chat_ready(page)
    close_channels(page)
    launcher = page.get_by_role('button', name=re.compile(r'^(Show all features|Visa alla funktioner)$'), exact=True)
    if keyboard:
        launcher.focus()
        launcher.press('Enter')
    else:
        launcher.click()
    panel = page.locator('.fc-slash-commands')
    expect(panel).to_be_visible()
    search = panel.get_by_role('combobox')
    expect(search).to_be_focused()
    list_id = panel.get_by_role('listbox').get_attribute('id')
    option = panel.locator('[role="option"][id=' + json.dumps(list_id + '-' + feature_id) + ']')
    expect(option).to_have_count(1)
    if before_select is not None:
        before_select(panel)
    if keyboard:
        wanted = option.get_attribute('id')
        search.press('Home')
        for _ in range(panel.get_by_role('option').count() + 1):
            if search.get_attribute('aria-activedescendant') == wanted:
                break
            search.press('ArrowDown')
        expect(search).to_have_attribute('aria-activedescendant', wanted)
        expect(option).to_have_attribute('aria-selected', 'true')
        expect(search).to_be_focused()
        page.wait_for_function('''id=>{
          const option=document.getElementById(id),list=option?.closest('[role="listbox"]');
          if(!option||!list)return false;
          const row=option.getBoundingClientRect(),box=list.getBoundingClientRect();
          return row.top>=box.top-2&&row.bottom<=box.bottom+2&&row.left>=-1&&row.right<=innerWidth+1;
        }''', arg=wanted)
        search.press('Enter')
    else:
        option.click()
    if expect_closed:
        expect(panel).to_have_count(0)
    return launcher


def open_conversation_work(page, kind, locale='en', keyboard=False):
    """Open actual work through /; keep the conversation and its draft available."""
    feature_ids = {
        'documents': 'document', 'tasks': 'task', 'decisions': 'decision',
        'meetings': 'meetings', 'feed': 'activity', 'copilot': 'copilot',
        'cloud': 'cloud', 'external-meetings': 'external-meetings',
        'workflows': 'workflows', 'apps': 'apps', 'data': 'data',
        'mobile': 'mobile',
    }
    activate_chat_feature(page, feature_ids[kind], keyboard=keyboard)
    region = page.get_by_role('region', name=('Arbete i det här samtalet' if locale == 'sv' else 'Work in this conversation'), exact=True)
    region.wait_for(state='visible')
    return region


def saved_document_reading(panel, title=None, text=None):
    """Assert the actual canonical saved page opens for reading before editing."""
    reader = panel.get_by_role('article', name=re.compile(r'^(Document reading view|Dokumentets läsvy)$'), exact=True)
    expect(reader).to_be_visible()
    expect(reader).to_have_attribute('aria-busy', 'false')
    assert reader.evaluate('node=>node.closest("[data-document-view]")?.dataset.documentView==="read"')
    if title is not None:
        expect(reader.locator('.fc-shared-reading-title')).to_have_text(title)
    if text is not None:
        assert reader.locator('.fc-shared-reading-body').text_content() == text
    editor = panel.locator('#shared-document-body')
    expect(editor).to_be_hidden()
    expect(editor).to_have_js_property('readOnly', True)
    return reader


def edit_saved_document(panel, title=None, text=None):
    """Enter the same native Yjs editor through its explicit visible Edit action."""
    saved_document_reading(panel, title=title, text=text)
    panel.get_by_role('button', name=re.compile(r'^(Edit document|Redigera dokument)$'), exact=True).click()
    editor = panel.locator('#shared-document-body')
    expect(editor).to_be_visible()
    expect(editor).to_be_editable()
    return editor


def show_channel_introduction(page):
    """Opt in to the real channel introduction; normal conversation entry stays quiet."""
    chat_ready(page)
    close_channels(page)
    trigger = page.get_by_role('button', name=re.compile(r'^(Channel menu|Kanalmeny)$'), exact=True)
    trigger.click()
    introduction = page.get_by_role('menuitemcheckbox', name=re.compile(r'^(Channel introduction|Kanalintroduktion)$'), exact=True)
    expect(introduction).to_be_visible()
    if introduction.get_attribute('aria-checked') != 'true':
        introduction.click()
    else:
        introduction.press('Escape')
    region = page.get_by_role('region', name=re.compile(r'^(Channel introduction|Kanalintroduktion)$'), exact=True)
    expect(region).to_be_visible()
    return region


def open_channels(page, timeout=90000):
    chat_ready(page, timeout)
    navigation = page.get_by_role('navigation', name=_CHANNELS)
    if not navigation.is_visible():
        page.get_by_role('button', name=_CHANNEL_TOGGLE).click()
    navigation.wait_for(state='visible', timeout=timeout)
    return navigation


def close_channels(page):
    navigation = page.get_by_role('navigation', name=_CHANNELS)
    if navigation.is_visible():
        page.get_by_role('button', name=_CLOSE_CHANNELS).click()
        navigation.wait_for(state='hidden')


def choose_chat_channel(page, name):
    open_channels(page).get_by_role('button').filter(has=page.get_by_text(name, exact=True)).click()


def reveal_message_actions(message):
    """Hover on desktop, or open the real per-message action button on mobile."""
    message.hover()
    toggle = message.get_by_role('button', name=_MESSAGE_ACTIONS)
    if toggle.is_visible() and toggle.get_attribute('aria-expanded') != 'true':
        toggle.click()


def reveal_discussion_work(message):
    """Expand authorized related work through its real compact discussion summary."""
    summary = message.get_by_role('button', name=re.compile(r'^(Work in this discussion|Arbete i den här diskussionen)$'), exact=True)
    expect(summary).to_be_visible()
    if summary.get_attribute('aria-expanded') != 'true':
        summary.click()
    expect(summary).to_have_attribute('aria-expanded', 'true')
    return summary
