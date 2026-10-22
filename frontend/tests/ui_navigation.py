"""Real, accessible Chat navigation shared by the browser proof harnesses.

These helpers only wait for the rendered product and click its visible controls.
They do not alter application state, seed sessions, dispatch events or bypass a
dialog. Call them at Chat entry points, not for controls inside another panel.
"""
import re


_MORE_TOOLS = re.compile(r'^(More channel tools|Fler kanalverktyg)$')
_CHANNEL_TOGGLE = re.compile(r'^(Toggle channels|Visa eller dölj kanaler)$')
_CHANNELS = re.compile(r'^(Channels|Kanaler)$')
_MESSAGE_ACTIONS = re.compile(r'^(More message actions|Fler meddelandeåtgärder)$')


def chat_ready(page, timeout=90000):
    shell = page.locator('.fc-chat')
    shell.wait_for(state='visible', timeout=timeout)
    return shell


def reveal_chat_tool(page, name, timeout=90000):
    """Reveal a named Chat tool through More when its direct control is hidden."""
    chat_ready(page, timeout)
    # The drawer is modal. Close it through its keyboard contract before using
    # the main toolbar, so the caller gets a visible return target after a panel.
    close_channels(page)
    target = page.get_by_role('button', name=name, exact=True)
    if not target.is_visible():
        page.get_by_role('button', name=_MORE_TOOLS).click()
    target.wait_for(state='visible', timeout=timeout)
    return target


def open_chat_tool(page, name, timeout=90000):
    reveal_chat_tool(page, name, timeout).click()


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
        page.keyboard.press('Escape')
        navigation.wait_for(state='hidden')


def choose_chat_channel(page, name):
    open_channels(page).get_by_role('button').filter(has=page.get_by_text(name, exact=True)).click()


def reveal_message_actions(message):
    """Hover on desktop, or open the real per-message action button on mobile."""
    message.hover()
    toggle = message.get_by_role('button', name=_MESSAGE_ACTIONS)
    if toggle.is_visible() and toggle.get_attribute('aria-expanded') != 'true':
        toggle.click()
