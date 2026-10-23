"""Real, accessible Chat navigation shared by the browser proof harnesses.

These helpers only wait for the rendered product and click its visible controls.
They do not alter application state, seed sessions, dispatch events or bypass a
dialog. Call them at Chat entry points, not for controls inside another panel.
"""
import re


_MORE_TOOLS = re.compile(r'^(More channel tools|Fler kanalverktyg)$')
_CHANNEL_TOGGLE = re.compile(r'^(Toggle channels|Visa eller dölj kanaler)$')
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


def open_conversation_work(page, kind, locale='en'):
    """Open an actual inline work action; the conversation stays available."""
    chat_ready(page)
    close_channels(page)
    names = {
        'documents': ('Add a document', 'Lägg till ett dokument'),
        'tasks': ('Next step', 'Nästa steg'),
        'decisions': ('Decision', 'Beslut'),
        'meetings': ('Meet', 'Mötas'),
        'feed': ('Channel activity', 'Aktivitet i samtalet'),
        'copilot': ('AI settings', 'AI-inställningar'),
        'cloud': ('Cloud files', 'Molnfiler'),
        'external-meetings': ('Meeting services', 'Mötestjänster'),
        'workflows': ('Workflows', 'Arbetsflöden'),
        'apps': ('Apps & automation', 'Appar och automatisering'),
        'data': ('Data & retention', 'Data och lagring'),
        'mobile': ('Mobile & offline', 'Mobil och offline'),
    }
    position = 1 if locale == 'sv' else 0
    actions = page.locator('.fc-chat-flow-actions')
    name = names[kind][position]
    if kind in ('documents', 'tasks', 'decisions', 'meetings'):
        actions.get_by_role('button', name=name, exact=True).click()
    else:
        actions.get_by_role('button', name=('Mer' if position else 'More'), exact=True).click()
        actions.get_by_role('menuitem', name=name, exact=True).click()
    region = page.get_by_role('region', name=('Arbete i det här samtalet' if position else 'Work in this conversation'), exact=True)
    region.wait_for(state='visible')
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
