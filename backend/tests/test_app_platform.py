"""Real app protocol fixtures, exact grants, replay fences and reviewed AI."""
from datetime import datetime, timedelta
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import httpx
import pytest
from sqlmodel import Session, select

from app.api import app_platform as routes
from app.models.app_platform import AppAIDraft, AppCard, AppInstallation, AppJob, ChannelRecapSchedule
from app.models.channel import ChannelMember
from app.models.copilot import CopilotConfig, CopilotConsent
from app.models.enterprise_governance import NativeDLPPolicy
from app.models.message import Message
from app.models.user import User
from app.models.workflow import WorkflowDefinition
from app.services import app_platform as service
from app.services.account_security import seal_private, unseal_private
from app.services.auth import hash_password
from tests.test_webhooks import team
from tests.test_copilot import provider, KEY

PREFIX = "/workspaces/team/apps"
CHANNEL = "/workspaces/team/channels/room/apps"


@pytest.fixture
def platform(team, monkeypatch):
    client, engine, headers = team
    client.app.include_router(routes.router)
    monkeypatch.setattr(service.copilot, "enabled", lambda: True)
    with Session(engine) as session:
        fields = service.copilot.Provider(endpoint="https://ai.example.com/v1/chat/completions", model="fixture", api_key=KEY)
        session.add(CopilotConfig(workspace_id="team", enabled=True, encrypted_config=service.copilot.seal(fields.model_dump(), "config", "team"), updated_by="owner"))
        session.add_all([CopilotConsent(workspace_id="team", user_id=name, config_revision=1, enabled=True) for name in ("owner", "member")])
        session.add(Message(id="source", channel_id="room", author_id="owner", content="REVIEWED NATIVE SOURCE", encrypted=False))
        session.commit()
    return team


def install(platform, recipe="signed-webhook", **changes):
    client, _, headers = platform
    item = next(row for row in service.catalog() if row["id"] == recipe)
    data = {"manifest": item["manifest"], "channel_ids": ["room"],
            "endpoint": "https://apps.example.com/hook" if recipe == "signed-webhook" else item["endpoint_example"].replace("YOUR-TENANT", "fixture").replace("YOUR-INSTANCE", "fixture").replace("OWNER/REPOSITORY", "owner/repo"),
            "authorization": "" if recipe == "signed-webhook" else "Bearer synthetic-customer-provider-token"}
    data.update(changes)
    response = client.post(PREFIX, headers=headers["owner"], json=data)
    assert response.status_code == 201, response.text
    return response.json()


def invoke(platform, made, **changes):
    command = made["installation"]["manifest"]["commands"][0]
    arguments = {row["name"]: "Explicit " + row["name"] for row in command["arguments"]}
    data = {"request_id": "request_12345", "channel_id": "room", "command": command["name"], "arguments": arguments,
            "expected_generation": made["installation"]["generation"], "confirm": True, "disclose_to_app": True}
    data.update(changes)
    return platform[0].post(PREFIX + "/" + made["installation"]["id"] + "/commands", headers=platform[2]["member"], json=data)


def callback(platform, made, **changes):
    packet = {"event_id": "callback_12345", "generation": made["installation"]["generation"], "channel_id": "room",
              "card": {"title": "A real app card", "text": "Safe declarative content", "url": "https://example.com/issue/1"}}
    packet.update(changes)
    body = service.dump(packet).encode()
    headers = service.webhooks.signature_headers(made["signing_secret"], body, packet["event_id"], "app.card")
    return platform[0].post(made["callback_path"], content=body, headers=headers)


def generate(platform, **changes):
    data = {"expected_config_revision": 1, "kind": "summary", "sources": [{"kind": "message", "id": "source"}], "disclose_to_provider": True}
    data.update(changes)
    return platform[0].post(CHANNEL + "/ai/drafts", headers=platform[2]["owner"], json=data)


def test_install_exact_grants_native_fence_secret_once_and_no_bot_account(platform):
    client, engine, headers = platform
    made = install(platform)
    assert made["installation"]["bot_id"].startswith("bot_")
    listed = client.get(PREFIX, headers=headers["member"])
    assert made["signing_secret"] not in listed.text and "encrypted" not in listed.text
    assert listed.json()["worker_ready"] is False
    item = next(row for row in service.catalog() if row["id"] == "signed-webhook")
    for channel, status in (("secure", 409), ("hidden", 403), ("direct", 403)):
        response = client.post(PREFIX, headers=headers["owner"], json={"manifest": item["manifest"], "channel_ids": [channel], "endpoint": "https://apps.example.com/hook"})
        assert response.status_code == status, response.text
    assert client.post(PREFIX, headers=headers["member"], json={"manifest": item["manifest"], "channel_ids": ["room"], "endpoint": "https://apps.example.com/hook"}).status_code == 403
    with Session(engine) as session:
        row = session.get(AppInstallation, made["installation"]["id"])
        assert made["signing_secret"] not in row.encrypted_secret and "apps.example.com/hook" not in row.encrypted_config


def test_manifest_scopes_arguments_and_explicit_disclosure_are_enforced(platform):
    made = install(platform)
    assert invoke(platform, made, disclose_to_app=False).status_code == 422
    assert invoke(platform, made, command="not-installed").status_code == 404
    assert invoke(platform, made, arguments={"text": "okay", "code": "arbitrary"}).status_code == 422
    assert invoke(platform, made, channel_id="hidden").status_code == 403
    first = invoke(platform, made)
    assert first.status_code == 202, first.text
    assert invoke(platform, made).json()["id"] == first.json()["id"]
    assert invoke(platform, made, arguments={"text": "different"}).status_code == 409
    with Session(platform[1]) as session:
        job = session.get(AppJob, first.json()["id"])
        assert "Explicit text" not in job.encrypted_payload


def test_actual_receiver_verifies_hmac_exact_json_and_stable_retry_id(platform):
    made = install(platform)
    queued = invoke(platform, made).json()
    received = []
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            stamp = self.headers["X-FreeCollab-Timestamp"]
            expected = "v1=" + hmac.new(made["signing_secret"].encode(), stamp.encode()+b"."+body, hashlib.sha256).hexdigest()
            assert hmac.compare_digest(expected, self.headers["X-FreeCollab-Signature"])
            packet = json.loads(body)
            assert packet["id"] == queued["id"] == self.headers["Idempotency-Key"]
            assert packet["arguments"] == {"text": "Explicit text"}
            assert "REVIEWED NATIVE SOURCE" not in body.decode()
            received.append(packet)
            self.send_response(503 if len(received) == 1 else 204)
            self.end_headers()
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    def sender(_url, body, headers):
        with httpx.Client(trust_env=False) as client:
            response = client.post("http://127.0.0.1:"+str(server.server_port), content=body, headers=headers)
        return response.status_code, None
    try:
        with Session(platform[1]) as session:
            assert service.run_pending(session, send=sender) == 0
            job = session.get(AppJob, queued["id"])
            assert job.status == "pending" and job.attempts == 1
            job.next_attempt_at = datetime.utcnow()-timedelta(seconds=1); session.add(job); session.commit()
            assert service.run_pending(session, send=sender) == 1
            assert service.run_pending(session, send=sender) == 0
            assert session.get(AppJob, queued["id"]).encrypted_payload == ""
        assert len(received) == 2 and received[0] == received[1]
    finally:
        server.shutdown(); server.server_close(); thread.join(3)


@pytest.mark.parametrize("recipe,expected", [
    ("github.issue", {"title": "Explicit title", "body": "Explicit body"}),
    ("jira.issue", {"fields": {"project": {"key": "Explicit project"}, "summary": "Explicit summary", "issuetype": {"name": "Task"}, "description": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Explicit description"}]}]}}}),
    ("zendesk.ticket", {"ticket": {"subject": "Explicit subject", "comment": {"body": "Explicit body"}}}),
    ("salesforce.case", {"Subject": "Explicit subject", "Description": "Explicit description"}),
])
def test_customer_owned_provider_recipes_use_real_create_protocols(platform, recipe, expected):
    made = install(platform, recipe)
    queued = invoke(platform, made).json()
    captured = []
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.headers["Authorization"] == "Bearer synthetic-customer-provider-token"
            captured.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(201); self.end_headers()
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    def sender(url, body, headers):
        assert "https://" in url
        with httpx.Client(trust_env=False) as client:
            response = client.post("http://127.0.0.1:"+str(server.server_port), content=body, headers=headers)
        return response.status_code, None
    try:
        with Session(platform[1]) as session:
            assert service.run_pending(session, send=sender) == 1
            assert session.get(AppJob, queued["id"]).status == "delivered"
        assert captured == [expected]
    finally:
        server.shutdown(); server.server_close(); thread.join(3)


def test_ambiguous_provider_writes_are_not_automatically_repeated(platform):
    made = install(platform, "github.issue")
    queued = invoke(platform, made).json()
    count = []
    def disconnected(*_):
        count.append(1)
        raise TimeoutError()
    with Session(platform[1]) as session:
        service.run_pending(session, send=disconnected)
        assert session.get(AppJob, queued["id"]).status == "uncertain"
        service.run_pending(session, send=disconnected)
    assert len(count) == 1
    path = PREFIX+"/"+made["installation"]["id"]+"/jobs/"+queued["id"]+"/retry"
    assert platform[0].post(path, headers=platform[2]["owner"], json={"confirm_receiver_has_not_processed": False}).status_code == 409
    assert platform[0].post(path, headers=platform[2]["owner"], json={"confirm_receiver_has_not_processed": True}).status_code == 200
    # A crashed provider worker cannot know whether the create request succeeded.
    with Session(platform[1]) as session:
        job = session.get(AppJob, queued["id"]); job.status = "processing"; job.claimed_at = datetime.utcnow()-timedelta(minutes=3)
        session.add(job); session.commit(); service.run_pending(session, send=disconnected)
        assert session.get(AppJob, queued["id"]).status == "uncertain"
    assert len(count) == 1


def test_callbacks_signature_replay_generation_scope_and_safe_cards(platform):
    made = install(platform)
    first = callback(platform, made)
    assert first.status_code == 201, first.text
    assert callback(platform, made).json() == {"card_id": first.json()["card_id"], "duplicate": True}
    assert callback(platform, made, card={"title": "Altered"}).status_code == 409
    assert callback(platform, made, event_id="callback_hidden", channel_id="hidden").status_code == 403
    assert callback(platform, made, event_id="callback_script", card={"title": "Bad", "url": "javascript:alert(1)"}).status_code == 401
    assert callback(platform, made, event_id="callback_script", card={"title": "Bad", "html": "<script>"}).status_code == 401
    assert platform[0].post(made["callback_path"], json={"card": {"title": "No signature"}}).status_code == 401
    cards = platform[0].get(CHANNEL+"/cards", headers=platform[2]["member"]).json()["cards"]
    assert cards[0]["bot_id"] == made["installation"]["bot_id"] and cards[0]["card"]["title"] == "A real app card"
    with Session(platform[1]) as session:
        assert "Safe declarative content" not in session.get(AppCard, first.json()["card_id"]).encrypted_card


def test_install_update_rotates_signing_generation_and_revokes_pending(platform):
    made = install(platform)
    queued = invoke(platform, made).json()
    data = {"manifest": made["installation"]["manifest"], "channel_ids": ["room"], "endpoint": "https://apps.example.com/new", "expected_generation": 1}
    data["manifest"]["version"] = "1.1.0"
    changed = platform[0].put(PREFIX+"/"+made["installation"]["id"], headers=platform[2]["owner"], json=data)
    assert changed.status_code == 200, changed.text
    assert changed.json()["signing_secret"] != made["signing_secret"]
    assert callback(platform, made).status_code == 401
    with Session(platform[1]) as session:
        assert session.get(AppJob, queued["id"]).status == "cancelled"
    revisions = platform[0].get(PREFIX+"/"+made["installation"]["id"]+"/revisions", headers=platform[2]["owner"]).json()["revisions"]
    assert [row["generation"] for row in revisions] == [2, 1]
    assert platform[0].delete(PREFIX+"/"+made["installation"]["id"], headers=platform[2]["owner"]).status_code == 200
    assert callback(platform, changed.json()).status_code == 401


def test_worker_rechecks_actor_and_installer_channel_membership(platform):
    made = install(platform)
    queued = invoke(platform, made).json()
    with Session(platform[1]) as session:
        session.delete(session.get(ChannelMember, ("room", "member"))); session.commit()
        assert service.run_pending(session, send=lambda *_: pytest.fail("revoked actor reached provider")) == 0
        assert session.get(AppJob, queued["id"]).status == "cancelled"
    with Session(platform[1]) as session:
        session.delete(session.get(ChannelMember, ("room", "owner"))); session.commit()
    assert callback(platform, made).status_code == 403


def test_real_ai_https_sse_sources_private_preview_citations_and_reviewed_publish(platform, provider):
    endpoint, state = provider
    with Session(platform[1]) as session:
        row = session.get(CopilotConfig, "team"); fields = service.copilot.config_fields(row); fields.endpoint = endpoint
        row.encrypted_config = service.copilot.seal(fields.model_dump(), "config", "team"); session.add(row); session.commit()
    response = generate(platform)
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["text"] == "Private answer [S1]" and draft["citations"][0]["id"] == "source"
    assert "REVIEWED NATIVE SOURCE" in json.dumps(state["requests"]) and KEY not in json.dumps(state["requests"])
    with Session(platform[1]) as session:
        assert session.exec(select(AppCard)).all() == []
        assert "Private answer" not in session.get(AppAIDraft, draft["id"]).encrypted_result
    path = CHANNEL+"/ai/drafts/"+draft["id"]+"/approve"
    assert platform[0].post(path, headers=platform[2]["owner"], json={"confirm": False}).status_code == 422
    published = platform[0].post(path, headers=platform[2]["owner"], json={"confirm": True})
    assert published.status_code == 200, published.text
    assert platform[0].post(path, headers=platform[2]["owner"], json={"confirm": True}).json() == published.json()
    with Session(platform[1]) as session:
        assert len(session.exec(select(AppCard)).all()) == 1


def test_reviewed_provider_revision_fences_immediate_drafts_and_schedule_creation_before_ai(platform, provider):
    old_endpoint, old = provider
    new_endpoint, new = old["create_recipient"]()
    with Session(platform[1]) as session:
        config = session.get(CopilotConfig, "team")
        fields = service.copilot.config_fields(config)
        fields.endpoint, fields.model = new_endpoint, "new-reviewed-model"
        config.revision = 2
        config.encrypted_config = service.copilot.seal(fields.model_dump(), "config", "team")
        consent = session.get(CopilotConsent, ("team", "owner")); consent.config_revision = 2
        session.add_all([config, consent]); session.commit()
    # Another tab has consented to revision2; an old recipient review is still invalid.
    assert generate(platform, expected_config_revision=1).status_code == 409
    schedule_data = {"expected_config_revision": 1, "interval_hours": 24, "disclose_future_native_messages": True}
    assert platform[0].post(CHANNEL+"/ai/recaps", headers=platform[2]["owner"], json=schedule_data).status_code == 409
    assert old["requests"] == new["requests"] == []
    with Session(platform[1]) as session:
        assert session.exec(select(AppAIDraft)).all() == session.exec(select(ChannelRecapSchedule)).all() == []
    response = generate(platform, expected_config_revision=2)
    assert response.status_code == 201, response.text
    assert old["requests"] == [] and len(new["requests"]) == 1 and new["requests"][0]["model"] == "new-reviewed-model"
    scheduled = platform[0].post(CHANNEL+"/ai/recaps", headers=platform[2]["owner"], json={**schedule_data, "expected_config_revision": 2})
    assert scheduled.status_code == 201 and scheduled.json()["provider_revision"] == 2
    assert len(new["requests"]) == 1


def test_reviewed_provider_revision_requires_a_positive_strict_integer_before_any_draft_or_schedule(platform, monkeypatch):
    monkeypatch.setattr(service.copilot, "completion", lambda *_: pytest.fail("Invalid reviewed revision reached provider"))
    for value in (None, True, False, "1", 1.0, 0, -1):
        for suffix, body in (("drafts", {"sources": [{"kind": "message", "id": "source"}], "disclose_to_provider": True}),
                             ("recaps", {"disclose_future_native_messages": True})):
            if value is not None:
                body["expected_config_revision"] = value
            response = platform[0].post(CHANNEL+"/ai/"+suffix, headers=platform[2]["owner"], json=body)
            assert response.status_code == 422, response.text
    with Session(platform[1]) as session:
        assert session.exec(select(AppAIDraft)).all() == session.exec(select(ChannelRecapSchedule)).all() == []


def test_reviewed_native_message_hash_blocks_an_edit_before_app_ai_disclosure(platform, provider):
    endpoint, state = provider
    with Session(platform[1]) as session:
        config = session.get(CopilotConfig, "team"); fields = service.copilot.config_fields(config); fields.endpoint = endpoint
        config.encrypted_config = service.copilot.seal(fields.model_dump(), "config", "team")
        source = session.get(Message, "source"); source.content = "Edited after review: ää 🧭"; session.add_all([config, source]); session.commit()
    reviewed = hashlib.sha256(b"REVIEWED NATIVE SOURCE").hexdigest()
    response = generate(platform, sources=[{"kind": "message", "id": "source", "expected_text_sha256": reviewed}])
    assert response.status_code == 409 and state["requests"] == []
    with Session(platform[1]) as session:
        assert session.exec(select(AppAIDraft)).all() == []
    fresh = hashlib.sha256("Edited after review: ää 🧭".encode()).hexdigest()
    response = generate(platform, sources=[{"kind": "message", "id": "source", "expected_text_sha256": fresh}])
    assert response.status_code == 201, response.text
    assert len(state["requests"]) == 1 and "Edited after review: ää 🧭" in json.dumps(state["requests"], ensure_ascii=False)


def test_context_character_bound_includes_source_separators_before_provider_io(platform, monkeypatch):
    monkeypatch.setattr(service.copilot, "completion", lambda *_: pytest.fail("Oversized joined context reached provider"))
    with Session(platform[1]) as session:
        session.add_all([Message(id='bounded-'+str(index), channel_id='room', author_id='owner', content='x'*7995, encrypted=False) for index in range(3)])
        session.commit()
    # Three [Sx] prefixes bring the individual pieces to24k; joining adds four characters.
    response = generate(platform, sources=[{"kind": "message", "id": 'bounded-'+str(index)} for index in range(3)])
    assert response.status_code == 413
    with Session(platform[1]) as session:
        assert session.exec(select(AppAIDraft)).all() == []


def test_recaps_disclose_only_messages_after_each_explicit_grant_including_provider_regrant(platform, provider):
    first_endpoint, first = provider
    second_endpoint, second = first["create_recipient"]()
    with Session(platform[1]) as session:
        config = session.get(CopilotConfig, 'team'); fields = service.copilot.config_fields(config); fields.endpoint = first_endpoint
        config.encrypted_config = service.copilot.seal(fields.model_dump(), 'config', 'team'); session.add(config); session.commit()
    path = CHANNEL+'/ai/recaps'
    payload = {'expected_config_revision': 1, 'interval_hours': 24, 'disclose_future_native_messages': True}
    response = platform[0].post(path, headers=platform[2]['owner'], json=payload)
    assert response.status_code == 201, response.text
    identifier = response.json()['id']
    with Session(platform[1]) as session:
        row = session.get(ChannelRecapSchedule, identifier)
        assert row.last_run_at is not None and row.last_status is None
        assert row.next_run_at > datetime.utcnow()+timedelta(hours=23)
    def dispatch():
        with Session(platform[1]) as session:
            row = session.get(ChannelRecapSchedule, identifier); row.next_run_at = datetime.utcnow()-timedelta(seconds=1)
            session.add(row); session.commit()
            return service.run_recaps(session)
    def add_message(identifier, text):
        with Session(platform[1]) as session:
            session.add(Message(id=identifier, channel_id='room', author_id='owner', content=text, encrypted=False)); session.commit()
    assert dispatch() == 0 and first['requests'] == []
    add_message('after-first-grant', 'ONLY AFTER FIRST CONSENT')
    assert dispatch() == 1
    assert 'ONLY AFTER FIRST CONSENT' in json.dumps(first['requests'])
    assert 'REVIEWED NATIVE SOURCE' not in json.dumps(first['requests'])
    assert platform[0].delete(path+'/'+identifier, headers=platform[2]['owner']).status_code == 200
    add_message('while-paused', 'PAUSED HISTORY MUST NEVER REACH NEW PROVIDER')
    with Session(platform[1]) as session:
        config = session.get(CopilotConfig, 'team'); fields = service.copilot.config_fields(config)
        fields.endpoint, fields.model = second_endpoint, 'second-future-provider'
        config.revision = 2; config.encrypted_config = service.copilot.seal(fields.model_dump(), 'config', 'team')
        consent = session.get(CopilotConsent, ('team', 'owner')); consent.config_revision = 2
        session.add_all([config, consent]); session.commit()
    response = platform[0].post(path, headers=platform[2]['owner'], json={**payload, 'expected_config_revision': 2, 'interval_hours': 1})
    assert response.status_code == 201 and response.json()['id'] == identifier
    assert dispatch() == 0 and second['requests'] == []
    add_message('after-new-grant', 'ONLY AFTER SECOND PROVIDER CONSENT')
    assert dispatch() == 1 and len(first['requests']) == len(second['requests']) == 1
    actual = json.dumps(second['requests'])
    assert 'ONLY AFTER SECOND PROVIDER CONSENT' in actual
    assert 'PAUSED HISTORY' not in actual and 'ONLY AFTER FIRST CONSENT' not in actual and 'REVIEWED NATIVE SOURCE' not in actual


def test_legacy_recap_without_a_disclosure_cursor_never_backfills_history(platform, monkeypatch):
    monkeypatch.setattr(service.copilot, 'completion', lambda *_: pytest.fail('Missing future-consent cursor disclosed old messages'))
    with Session(platform[1]) as session:
        row = ChannelRecapSchedule(workspace_id='team', channel_id='room', owner_id='owner', provider_revision=1,
                                   interval_hours=24, next_run_at=datetime.utcnow()-timedelta(seconds=1))
        session.add(row); session.commit()
        assert service.run_recaps(session) == 0
        session.refresh(row)
        assert row.last_run_at is not None and row.last_status == 'no_messages' and row.enabled
        assert session.exec(select(AppAIDraft)).all() == []


def test_ai_proposes_cross_app_writes_and_requires_unchanged_reviewed_grants(platform, monkeypatch):
    made = install(platform, "github.issue")
    payload = {"actions": [{"installation_id": made["installation"]["id"], "command": "create-issue", "arguments": {"title": "Reviewed proposal", "body": "Citation [S1]"}}]}
    monkeypatch.setattr(service.copilot, "completion", lambda *_: {"text": json.dumps(payload), "tool_calls": []})
    response = generate(platform, kind="actions", installation_ids=[made["installation"]["id"]])
    assert response.status_code == 201, response.text
    with Session(platform[1]) as session:
        assert session.exec(select(AppJob)).all() == []
    approved = platform[0].post(CHANNEL+"/ai/drafts/"+response.json()["id"]+"/approve", headers=platform[2]["owner"], json={"confirm": True})
    assert approved.status_code == 200, approved.text
    with Session(platform[1]) as session:
        assert len(session.exec(select(AppJob)).all()) == 1
    payload["actions"][0]["installation_id"] = "unselected-app"
    assert generate(platform, kind="actions", installation_ids=[made["installation"]["id"]]).status_code == 502
    payload["actions"][0]["installation_id"] = made["installation"]["id"]
    payload["actions"][0]["arguments"] = ["not", "an", "object"]
    assert generate(platform, kind="actions", installation_ids=[made["installation"]["id"]]).status_code == 502


def test_consent_secure_source_change_and_recap_schedule_pause(platform, monkeypatch):
    monkeypatch.setattr(service.copilot, "completion", lambda *_: {"text": "Summary [S1]", "tool_calls": []})
    assert generate(platform, disclose_to_provider=False).status_code == 422
    assert platform[0].post("/workspaces/team/channels/secure/apps/ai/drafts", headers=platform[2]["owner"], json={"expected_config_revision": 1, "sources": [{"kind": "message", "id": "$event", "text": "NEVER INGEST", "event_ids": ["$event"]}], "disclose_to_provider": True}).status_code == 409
    response = generate(platform)
    with Session(platform[1]) as session:
        source = session.get(Message, "source"); source.content = "CHANGED"; session.add(source); session.commit()
    assert platform[0].post(CHANNEL+"/ai/drafts/"+response.json()["id"]+"/approve", headers=platform[2]["owner"], json={"confirm": True}).status_code == 409
    path = CHANNEL+"/ai/recaps"
    assert platform[0].post(path, headers=platform[2]["owner"], json={"expected_config_revision": 1, "interval_hours": 24, "disclose_future_native_messages": False}).status_code == 422
    scheduled = platform[0].post(path, headers=platform[2]["owner"], json={"expected_config_revision": 1, "interval_hours": 24, "disclose_future_native_messages": True})
    assert scheduled.status_code == 201, scheduled.text
    with Session(platform[1]) as session:
        schedule = session.get(ChannelRecapSchedule, scheduled.json()["id"])
        schedule.next_run_at = datetime.utcnow()-timedelta(seconds=1)
        session.add(schedule)
        session.add(Message(id="future-recap-source", channel_id="room", author_id="owner", content="New after future-only consent", encrypted=False))
        session.commit()
        assert service.run_recaps(session) == 1
        schedule = session.get(ChannelRecapSchedule, scheduled.json()["id"])
        assert schedule.last_status == "draft_ready" and schedule.enabled
        schedule.next_run_at = datetime.utcnow()-timedelta(seconds=1); session.add(schedule)
        consent = session.get(CopilotConsent, ("team", "owner")); consent.enabled = False; session.add(consent); session.commit()
        assert service.run_recaps(session, complete=lambda *_: pytest.fail("revoked consent reached AI")) == 0
        assert session.get(ChannelRecapSchedule, scheduled.json()["id"]).enabled is False


def test_dlp_fences_apps_callbacks_and_ai_disclosure(platform, monkeypatch):
    made = install(platform)
    with Session(platform[1]) as session:
        policy = NativeDLPPolicy(workspace_id="team", name="Block explicit token", detector="literal", action="block", created_by="owner")
        policy.encrypted_literal = seal_private("SENSITIVE-MARKER", "native-dlp", policy.id)
        session.add(policy); session.commit()
    assert invoke(platform, made, arguments={"text": "SENSITIVE-MARKER"}).status_code == 423
    assert callback(platform, made, card={"title": "SENSITIVE-MARKER"}).status_code == 423
    monkeypatch.setattr(service.copilot, "completion", lambda *_: pytest.fail("blocked prompt reached AI"))
    assert generate(platform, prompt="SENSITIVE-MARKER").status_code == 423


def test_ai_workflow_requires_current_admin_password_and_installs_atomically_once(platform, monkeypatch):
    password = "App-platform-workflow-fixture!"
    with Session(platform[1]) as session:
        user = session.get(User, "owner"); user.password_hash = hash_password(password); session.add(user); session.commit()
    fields = {"name": "Reviewed generated workflow", "trigger": "manual", "steps": [{"kind": "notify", "recipients": ["member"]}]}
    monkeypatch.setattr(service.copilot, "completion", lambda *_: {"text": json.dumps(fields), "tool_calls": []})
    response = generate(platform, kind="workflow", recipient_ids=["member"])
    assert response.status_code == 201, response.text
    path = CHANNEL+"/ai/drafts/"+response.json()["id"]+"/approve"
    with Session(platform[1]) as session:
        assert session.exec(select(WorkflowDefinition)).all() == []
    assert platform[0].post(path, headers=platform[2]["owner"], json={"confirm": True}).status_code == 422
    assert platform[0].post(path, headers=platform[2]["owner"], json={"confirm": True, "password": "wrong-password"}).status_code == 403
    approved = platform[0].post(path, headers=platform[2]["owner"], json={"confirm": True, "password": password})
    assert approved.status_code == 200, approved.text
    assert platform[0].post(path, headers=platform[2]["owner"], json={"confirm": True, "password": password}).json() == approved.json()
    with Session(platform[1]) as session:
        definitions = session.exec(select(WorkflowDefinition)).all()
        assert len(definitions) == 1 and definitions[0].id == approved.json()["resource_id"]
        assert service.workflow.definition_fields(session, definitions[0]).steps[0].recipients == ["member"]
    fields["steps"][0]["recipients"] = ["outsider"]
    assert generate(platform, kind="workflow", recipient_ids=["member"]).status_code == 502


def test_ai_rechecks_grants_after_provider_and_stopped_recap_discards_new_draft(platform, monkeypatch):
    path = CHANNEL+"/ai/recaps"
    scheduled = platform[0].post(path, headers=platform[2]["owner"], json={"expected_config_revision": 1, "interval_hours": 24, "disclose_future_native_messages": True}).json()
    with Session(platform[1]) as session:
        row = session.get(ChannelRecapSchedule, scheduled["id"]); row.next_run_at = datetime.utcnow()-timedelta(seconds=1)
        session.add(row); session.add(Message(id="future-stop-source", channel_id="room", author_id="owner", content="Future source selected before stopping", encrypted=False)); session.commit()
    def stop_during_provider(*_):
        with Session(platform[1]) as session:
            row = session.get(ChannelRecapSchedule, scheduled["id"]); row.enabled = False; row.generation += 1
            session.add(row); session.commit()
        return {"text": "Must remain unpublished [S1]", "tool_calls": []}
    with Session(platform[1]) as session:
        assert service.run_recaps(session, complete=stop_during_provider) == 0
        assert session.exec(select(AppAIDraft)).all() == []
    def revoke_during_provider(*_):
        with Session(platform[1]) as session:
            session.delete(session.get(ChannelMember, ("room", "owner"))); session.commit()
        return {"text": "Never persist revoked result [S1]", "tool_calls": []}
    monkeypatch.setattr(service.copilot, "completion", revoke_during_provider)
    assert generate(platform).status_code == 403
    with Session(platform[1]) as session:
        assert session.exec(select(AppAIDraft)).all() == []


def test_stale_signed_callback_is_rejected_and_private_draft_cleanup_survives_consent_revocation(platform, monkeypatch):
    made = install(platform)
    body = service.dump({"event_id": "stale_callback_123", "generation": 1, "channel_id": "room", "card": {"title": "No stale callback"}}).encode()
    headers = service.webhooks.signature_headers(made["signing_secret"], body, "stale_callback_123", "app.card", timestamp=int(time.time())-301)
    assert platform[0].post(made["callback_path"], content=body, headers=headers).status_code == 401
    monkeypatch.setattr(service.copilot, "completion", lambda *_: {"text": "Summary [S1]", "tool_calls": []})
    draft = generate(platform).json()
    with Session(platform[1]) as session:
        consent = session.get(CopilotConsent, ("team", "owner")); consent.enabled = False; session.add(consent); session.commit()
    assert platform[0].delete(CHANNEL+"/ai/drafts/"+draft["id"], headers=platform[2]["member"]).status_code == 404
    assert platform[0].delete(CHANNEL+"/ai/drafts/"+draft["id"], headers=platform[2]["owner"]).status_code == 204


def test_account_resource_cleanup_revokes_apps_erases_private_copies_and_preserves_shared_cards(platform, monkeypatch):
    made = install(platform)
    callback(platform, made)
    invoke(platform, made)
    monkeypatch.setattr(service.copilot, "completion", lambda *_: {"text": "Private summary [S1]", "tool_calls": []})
    generate(platform)
    with Session(platform[1]) as session:
        assert service.export_private_drafts(session, "team", ["room"], "owner")[0]["text"] == "Private summary [S1]"
        assert service.export_private_drafts(session, "team", ["room"], "member") == []
        service.close_account_resources(session, "owner"); session.commit()
        installed = session.get(AppInstallation, made["installation"]["id"])
        assert installed.revoked_at and not installed.enabled and not installed.encrypted_secret and not installed.encrypted_config
        assert session.exec(select(AppAIDraft)).all() == []
        assert len(session.exec(select(AppCard)).all()) == 1
        job = session.exec(select(AppJob)).one()
        assert job.status == "cancelled" and job.encrypted_payload == ""


def test_native_retention_stops_apps_and_collects_old_cards_drafts_and_terminal_requests(platform, monkeypatch):
    from app.services.data_control import build_plan
    from fastapi import HTTPException
    made = install(platform)
    callback(platform, made)
    invoke(platform, made)
    monkeypatch.setattr(service.copilot, "completion", lambda *_: {"text": "Private summary [S1]", "tool_calls": []})
    generate(platform)
    with Session(platform[1]) as session:
        with pytest.raises(HTTPException) as active:
            build_plan(session, "team", "owner", ["room"], datetime.utcnow())
        assert active.value.status_code == 409
    assert platform[0].delete(PREFIX+"/"+made["installation"]["id"], headers=platform[2]["owner"]).status_code == 200
    with Session(platform[1]) as session:
        for model in (AppCard, AppAIDraft, AppJob):
            row = session.exec(select(model)).one(); row.created_at = datetime.utcnow()-timedelta(days=90); session.add(row)
        session.commit()
        plan = build_plan(session, "team", "owner", ["room"], datetime.utcnow()-timedelta(days=30))
        assert all(len(plan[name]) == 1 for name in ("appcard", "appaidraft", "appjob"))
        assert "appcallback" not in plan
