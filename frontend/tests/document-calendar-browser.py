"""Real document/calendar UI, ICS and encrypted metadata proof.

Requires disposable Matrix-enabled services, Chromium and a matching database:
CONTENT_DATABASE_URL=sqlite:////.../test.db CHROMIUM_PATH=/usr/bin/chromium \
  backend/.venv/bin/python frontend/tests/document-calendar-browser.py
CHAT_BASE_URL defaults3000; CONTENT_API_URL defaults the app's /api proxy.
Only fixture accounts are created. No provider URL is fetched, no external
calendar opens, and no tokens or content keys are logged.
"""
from ui_navigation import open_chat_tool, reveal_chat_tool
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright
from sqlmodel import Session, create_engine, select

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.models.integration_content import IntegrationContentRevision

BASE = os.getenv("CHAT_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
API = os.getenv("CONTENT_API_URL", BASE + "/api").rstrip("/")
DB = os.getenv("CONTENT_DATABASE_URL") or os.getenv("DATABASE_URL")
assert DB, "Set CONTENT_DATABASE_URL to the matching disposable API database for the storage assertion"
RUN = secrets.token_hex(6)
PASSWORD = secrets.token_urlsafe(24)


def api(method, path, user=None, data=None):
    req = Request(API + path, method=method, headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + user["access_token"]} if user else {})},
                  data=json.dumps(data).encode() if data is not None else None)
    with urlopen(req, timeout=90) as response: return json.load(response)


def register(name):
    return api("POST", "/auth/register", data={"name": name, "email": name.lower() + RUN + "@example.com", "password": PASSWORD})


def poll(callback, predicate, description):
    for _ in range(150):
        result = callback()
        if predicate(result): return result
        time.sleep(.2)
    raise AssertionError(description)


alice, bob = register("ContentAlice"), register("ContentBob")
assert api("GET", "/secure/status", alice)["enabled"], "Enable Matrix on the disposable API; secure checks never skip"
workspace = api("POST", "/workspaces", alice, {"name": "Content Studio " + RUN})
invite = api("POST", "/workspaces/" + workspace["id"] + "/invites", alice, {"role": "member"})
api("POST", "/workspaces/invites/accept", bob, {"token": invite["token"]})
native = api("POST", "/channels", alice, {"name": "Content room " + RUN, "workspace_id": workspace["id"], "member_ids": [bob["user_id"]]})
native_path = "/channels/" + native["id"] + "/integrations/"
print("Content proof: disposable workspace and native channel ready.", flush=True)


def init(context, user):
    context.add_init_script("localStorage.setItem('freecollab_token', " + json.dumps(user["access_token"]) + "); localStorage.setItem('freecollab_user_id', " + json.dumps(user["user_id"]) + ");")
    page = context.new_page(); page.set_default_timeout(60000)
    if API != BASE + "/api":
        page.route("**/api/**", lambda route: route.fulfill(response=route.fetch(url=API + route.request.url.split("/api", 1)[1])))
    page.goto(BASE, wait_until="domcontentloaded", timeout=90000)
    reveal_chat_tool(page, 'Account settings')
    expect(page.get_by_role("button", name="Account settings", exact=True)).to_be_visible(timeout=90000)
    page.get_by_role("button", name=re.compile("^Switch workspace, ")).click()
    page.get_by_role("menuitemradio", name=re.compile("Content Studio " + RUN)).click()
    expect(page.get_by_role('textbox', name="Message #" + native["name"], exact=True)).to_be_visible(timeout=90000)
    return page


def integrations(page, tab, channel):
    if not page.get_by_role("dialog", name="Integrations", exact=True).is_visible(): open_chat_tool(page, 'Integrations')
    panel = page.get_by_role("dialog", name="Integrations", exact=True)
    panel.get_by_role("navigation", name="Integration sections").get_by_role("button", name=tab, exact=True).click()
    panel.get_by_label("Channel", exact=True).select_option(channel)
    return panel


def add_document(panel, title, url):
    panel.get_by_role("button", name="Add document link", exact=True).click()
    form = panel.get_by_role("form", name="Add document link")
    form.get_by_label("Document title", exact=True).fill(title)
    form.get_by_label("Document URL", exact=True).fill(url)
    form.get_by_label("Notes", exact=True).fill("Private source note <img src=x onerror=alert(1)>")
    form.get_by_role("button", name="Save document link", exact=True).click()
    expect(panel.get_by_role("heading", name=title, exact=True)).to_be_visible(timeout=90000)


def add_event(panel, title, all_day=False):
    panel.get_by_role("button", name="Add calendar event", exact=True).click()
    form = panel.get_by_role("form", name="Add calendar event")
    form.get_by_label("Event title", exact=True).fill(title)
    if all_day:
        form.get_by_label("All-day event", exact=True).check()
        form.get_by_label("Starts", exact=True).fill("2026-11-01")
        form.get_by_label("Through", exact=True).fill("2026-11-02")
    else:
        form.get_by_label("Starts", exact=True).fill("2026-10-25T02:30")
        form.get_by_label("Ends", exact=True).fill("2026-10-25T03:30")
        form.get_by_label("Time zone", exact=True).fill("Europe/Stockholm")
    form.get_by_label("Location", exact=True).fill("Private studio")
    form.get_by_label("Event details", exact=True).fill("Private planning details & next steps")
    form.get_by_role("button", name="Save calendar event", exact=True).click()
    expect(panel.get_by_role("heading", name=title, exact=True)).to_be_visible(timeout=90000)


def download_calendar(page, panel):
    with page.expect_download() as pending: panel.get_by_role("button", name="Export ICS", exact=True).click()
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "calendar.ics"; pending.value.save_as(str(target)); return target.read_text()


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH") or None, headless=True, args=["--no-sandbox"])
    try:
        contexts = [browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce", accept_downloads=True) for _ in range(2)]
        left, right = init(contexts[0], alice), init(contexts[1], bob)
        print("Content proof: two isolated signed-in browsers ready.", flush=True)
        errors = []
        for page in (left, right): page.on("pageerror", lambda error: errors.append(str(error)))
        panel = integrations(left, "Documents", native["id"])
        title = "Native source " + RUN
        add_document(panel, title, "https://cloud.example.com/s/native" + RUN)
        link = panel.get_by_role("link", name="Open document", exact=True)
        assert "noreferrer" in link.get_attribute("rel") and link.get_attribute("target") == "_blank"
        panel.get_by_role("button", name="Edit", exact=True).click()
        panel.get_by_label("Document title", exact=True).fill(title + " revised")
        panel.get_by_role("button", name="Save document link", exact=True).click()
        expect(panel.get_by_role("heading", name=title + " revised", exact=True)).to_be_visible()
        panel.get_by_role("button", name="History", exact=True).click()
        expect(panel.get_by_role("list", name="Document link history").get_by_text(title, exact=True)).to_be_visible()
        native_docs = api("GET", native_path + "documents", alice)
        assert native_docs[0]["revision"] == 2
        print("PASS native document UI: scoped provider link, safe navigation, immutable edit/history", flush=True)

        panel = integrations(left, "Calendar", native["id"])
        event_title = "Native planning " + RUN
        add_event(panel, event_title)
        ics = download_calendar(left, panel)
        assert "DTSTART:20261025T003000Z" in ics and "DTEND:20261025T023000Z" in ics and "TRIGGER:-PT15M" in ics
        extra = "BEGIN:VEVENT\r\nUID:imported-" + RUN + "@example.com\r\nDTSTART;VALUE=DATE:20261101\r\nDTEND;VALUE=DATE:20261103\r\nSUMMARY:Imported team plan\r\nEND:VEVENT\r\n"
        source = ics.replace("END:VCALENDAR", extra + "END:VCALENDAR")
        panel.get_by_label("Import ICS file", exact=True).set_input_files({"name": "team.ics", "mimeType": "text/calendar", "buffer": source.encode()})
        panel.get_by_role("button", name="Add 2 events to channel", exact=True).click()
        expect(panel.get_by_text("1 event imported; 1 existing event skipped. No invitations were sent.", exact=True)).to_be_visible()
        assert len(api("GET", native_path + "calendar", alice)) == 2
        left.set_viewport_size({"width": 320, "height": 700})
        assert left.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Mobile integration panel overflows viewport"
        left.set_viewport_size({"width": 1440, "height": 1000})
        panel.get_by_role("button", name="Close integrations", exact=True).click()
        print("PASS calendar UI: DST-aware event, real ICS download/import, duplicate UID skipped,320px layout", flush=True)

        secure_channel = api("POST", "/channels", alice, {"name": "Encrypted channel", "workspace_id": workspace["id"], "member_ids": [bob["user_id"]]})
        channel = secure_channel["id"]
        for page in (left, right):
            page.evaluate("""async()=>{window.contentProofApi=(await import('/src/lib/document-calendar-api.ts')).documentCalendarApi; window.contentProofSecure=await(await import('/src/lib/secure-client.ts')).getSecureMessaging();}""")
        left.evaluate("channel=>contentProofSecure.enableChannel(channel,'Private content vault')", channel)
        right.evaluate("channel=>contentProofSecure.getMessages(channel)", channel)
        for page, other in ((left, bob), (right, alice)):
            devices = poll(lambda: page.evaluate("channel=>contentProofSecure.getDevices(channel)", channel), lambda rows: any(row["user_id"] == other["user_id"] for row in rows), "Recipient device was not published")
            for device in devices:
                if device["user_id"] == other["user_id"]: page.evaluate("device=>contentProofSecure.verifyDevice(device,device.fingerprint)", device)
        left.reload(wait_until="domcontentloaded")
        reveal_chat_tool(left, 'Account settings')
        expect(left.get_by_role("button", name="Account settings", exact=True)).to_be_visible(timeout=90000)
        wire_requests = []
        left.on("request", lambda req: wire_requests.append(req.post_data or "") if req.method == "POST" and "/channels/" + channel + "/integrations/" in req.url else None)
        panel = integrations(left, "Documents", channel)
        secret_title = "Secret source " + RUN
        secret_url = "https://cloud.example.com/s/private" + RUN
        add_document(panel, secret_title, secret_url)
        secure_docs = api("GET", "/channels/" + channel + "/integrations/documents", alice)
        received_docs = poll(lambda: right.evaluate("channel=>contentProofApi.documents(channel)", channel), lambda rows: any(row.get("fields", {}).get("title") == secret_title for row in rows), "Peer did not decrypt linked document")
        assert secure_docs[0]["content_mode"] == "matrix" and "fields" not in secure_docs[0]
        panel = integrations(left, "Calendar", channel)
        secret_event = "Secret planning " + RUN
        add_event(panel, secret_event, all_day=True)
        secure_events = api("GET", "/channels/" + channel + "/integrations/calendar", alice)
        poll(lambda: right.evaluate("channel=>contentProofApi.calendar(channel)", channel), lambda rows: any(row.get("fields", {}).get("title") == secret_event for row in rows), "Peer did not decrypt calendar event")
        secure_ics = download_calendar(left, panel)
        assert secret_event in secure_ics and "DTEND;VALUE=DATE:20261103" in secure_ics
        assert len(wire_requests) == 2
        for body in wire_requests:
            assert "fields" not in json.loads(body) and secret_title not in body and secret_url not in body and secret_event not in body
        for kind, records in (("documents", secure_docs), ("calendar", secure_events)):
            wire = right.evaluate("""async args=>{const event=await contentProofSecure.getDecryptedEvent(args.channel,args.id); return {type:event.getWireType(),content:JSON.stringify(event.getWireContent())}}""", {"channel": channel, "id": records[0]["matrix_event_id"]})
            assert wire["type"] == "m.room.encrypted" and secret_title not in wire["content"] and secret_event not in wire["content"] and secret_url not in wire["content"]
        engine = create_engine(DB)
        with Session(engine) as session:
            for record in secure_docs + secure_events:
                revision = session.exec(select(IntegrationContentRevision).where(IntegrationContentRevision.item_id == record["id"])).one()
                assert revision.encrypted_payload is None and revision.matrix_event_id == record["matrix_event_id"]
            revision = session.exec(select(IntegrationContentRevision).where(IntegrationContentRevision.item_id == native_docs[0]["id"], IntegrationContentRevision.revision == 1)).one()
            assert title not in revision.encrypted_payload and "cloud.example.com" not in revision.encrypted_payload
        engine.dispose()
        assert not errors, errors
        print("PASS two real Rust devices: document/calendar metadata only encrypted events, peer decrypts, nativeAPI+DB opaque, local secure ICS export", flush=True)
    finally:
        browser.close()
