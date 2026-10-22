"""Browser integration checks against a running backend, Vite and built widget.

Run against a disposable development database: this creates three unique users,
two channels and test messages. Start backend/Vite first. Python requires the
Playwright package and a Chromium executable (override CHROMIUM_PATH if needed).

From frontend/:
    npm run build:widget -- --outDir /tmp/freecollab-widget
    WIDGET_BUNDLE=/tmp/freecollab-widget/widget.iife.js python tests/widget-browser.py

Alternatively npm run build:widget uses the default ignored dist-widget/ output.
Optional overrides: WIDGET_API_URL, CHAT_BASE_URL, WIDGET_BUNDLE, CHROMIUM_PATH.
"""
import json
import os
from pathlib import Path
import uuid
from urllib.request import Request, urlopen
from playwright.sync_api import sync_playwright, expect

API = os.environ.get("WIDGET_API_URL", "http://127.0.0.1:8000").rstrip("/")
ORIGIN = os.environ.get("CHAT_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
BUNDLE = str(Path(os.environ.get(
    "WIDGET_BUNDLE", Path(__file__).resolve().parents[1] / "dist-widget/widget.iife.js"
)).resolve())
CHROMIUM = os.environ.get("CHROMIUM_PATH") or None

def api(method, path, token=None, data=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(API + path, data=json.dumps(data).encode() if data is not None else None,
                      headers=headers, method=method)
    with urlopen(request, timeout=30) as response:
        return json.load(response)

def register(name):
    return api("POST", "/auth/register", data={"name": name, "email": uuid.uuid4().hex + "@example.com", "password": "Browser-test-password!"})

alice = register("Widget Alice")
bob = register("Widget Bob")
outsider = register("Widget Outsider")
channel = api("POST", "/channels", alice["access_token"], {"name": "Widget private", "type": "group", "member_ids": [bob["user_id"]]})
other = api("POST", "/channels", alice["access_token"], {"name": "Widget second", "type": "group", "member_ids": [bob["user_id"]]})
xss = '<img src=x onerror="window.widgetXss=true">literal message'
api("POST", "/messages/channels/" + channel["id"], alice["access_token"], {"content": xss})
api("POST", "/messages/channels/" + other["id"], alice["access_token"], {"content": "Second channel secret"})

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROMIUM, headless=True, args=["--no-sandbox"], timeout=90000)
    page = browser.new_page()
    page.set_default_timeout(20000)
    page.goto(ORIGIN, wait_until="domcontentloaded")
    page.set_content("<!doctype html><html><head></head><body></body></html>")
    page.add_script_tag(path=BUNDLE)
    assert page.evaluate("typeof window.FreeCollab.init") == "function", "Script-tag API must expose FreeCollab.init"
    page.evaluate("""config => { window.widget = window.FreeCollab.init(config); }""", {
        "apiUrl": API, "channelId": channel["id"], "userToken": bob["access_token"],
        "title": '<img src=x onerror="window.widgetXss=true">title',
        "subtitle": "<script>window.widgetXss=true</script>",
        "welcomeMessage": "<img src=x onerror=alert(1)>welcome",
    })
    page.evaluate("window.widget.open()")
    expect(page.locator(".freecollab-messages")).to_contain_text(xss)
    assert page.locator(".freecollab-widget-container img, .freecollab-widget-container script").count() == 0
    assert not page.evaluate("Boolean(window.widgetXss)")
    print("PASS: script-tag init, authenticated history and literal XSS payloads", flush=True)
    page.locator(".freecollab-input").fill("Browser widget reply")
    page.locator(".freecollab-send-btn").click()
    expect(page.locator(".freecollab-input")).to_have_value("")
    stored = api("GET", "/messages/channels/" + channel["id"], alice["access_token"])
    assert any(message["content"] == "Browser widget reply" and message["author_id"] == bob["user_id"] for message in stored)
    api("POST", "/messages/channels/" + channel["id"], alice["access_token"], {"content": "Alice live polling reply"})
    expect(page.locator(".freecollab-messages")).to_contain_text("Alice live polling reply")
    assert page.locator(".freecollab-message", has_text="Browser widget reply").count() == 1
    print("PASS: acknowledged send, API persistence, polling receive and deduplication", flush=True)
    page.locator(".freecollab-input").fill("Private draft from the previous channel")
    page.evaluate("channel => window.widget.setChannel(channel)", other["id"])
    expect(page.locator(".freecollab-input")).to_have_value("")
    expect(page.locator(".freecollab-messages")).to_contain_text("Second channel secret")
    expect(page.locator(".freecollab-messages")).not_to_contain_text("Alice live polling reply")
    print("PASS: channel switching clears prior history", flush=True)
    page.locator(".freecollab-input").fill("Private draft from the previous account")
    page.evaluate("token => window.widget.setUser(token)", outsider["access_token"])
    expect(page.locator(".freecollab-input")).to_have_value("")
    expect(page.locator(".freecollab-status")).to_contain_text("403")
    expect(page.locator(".freecollab-messages")).not_to_contain_text("Second channel secret")
    page.locator(".freecollab-input").fill("Unauthorized draft")
    page.locator(".freecollab-send-btn").click()
    expect(page.locator(".freecollab-status")).to_contain_text("not sent (403)")
    expect(page.locator(".freecollab-input")).to_have_value("Unauthorized draft")
    expect(page.locator(".freecollab-messages")).not_to_contain_text("Unauthorized draft")
    print("PASS: outsider history/send denied, draft retained, no content leakage", flush=True)
    page.evaluate("window.widget.destroy()")
    assert page.locator(".freecollab-widget-container").count() == 0
    browser.close()
