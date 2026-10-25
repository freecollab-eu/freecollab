"""Actual API/SQL worker lifecycle, consent races and provider protocol contracts."""
from datetime import datetime, timedelta
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ssl
import threading
from urllib.parse import urlsplit

from fastapi import HTTPException
import pytest
from sqlmodel import Session, select

from app.models.ai_runtime import AIBudget, AIModelProfile, AIPersonalKey, AIProfileConsent, AIRun, AIRunApproval, AIRunStep, AIUsage
from app.models.copilot import CopilotConsent
from app.models.message import Message
from app.models.project_tasks import ProjectBoard, TaskCard
from app.models.session import ActiveSession
from app.services import ai_providers, ai_runtime as service
from app.services.crypto import encrypt_message
from tests.test_copilot import ai, conversation, ask
from tests.test_data_controls import controls, headers, PASSWORD

PREFIX = "/workspaces/a/copilot/runtime"
KEY = "synthetic-private-runtime-provider-key"


def profile(ai, identifier="fast", **changes):
    body = {"password": PASSWORD, "expected_revision": 0, "name": "Reviewed model", "protocol": "openai",
        "endpoint": "https://model.example.com/v1/chat/completions", "model": "model-one", "api_key": KEY, "key_source": "workspace",
        "enabled": True, "max_output_tokens": 2048, "input_cost_per_million_micros": 1000000, "output_cost_per_million_micros": 2000000, **changes}
    response = ai[0].put(PREFIX + "/profiles/" + identifier, headers=headers(ai), json=body)
    assert response.status_code == 200, response.text
    revision = response.json()["revision"]
    consent = ai[0].post(PREFIX + "/profiles/" + identifier + "/consent", headers=headers(ai), json={"enabled": True, "expected_revision": revision})
    assert consent.status_code == 200, consent.text
    return revision


def queue(ai, **changes):
    response = ai[0].post(PREFIX + "/runs", headers=headers(ai), json={"request_id": "runtime-private-request", "channel_id": "native", "profile_id": "fast",
        "expected_profile_revision": 1, "operation": "summarize", "prompt": "Summarize the reviewed sources", "sources": [{"kind": "message", "id": "old-root"}],
        "selected_text": "Selected private unsaved draft", "max_steps": 1, "max_tokens": 512, "disclose_to_provider": True, **changes})
    return response


def current(ai, identifier):
    response = ai[0].get(PREFIX + "/runs/" + identifier, headers=headers(ai)); assert response.status_code == 200, response.text
    return response.json()


def answer(text="A reviewed answer [S1]", calls=None):
    return {"text": text, "tool_calls": calls or [], "usage": {"input_tokens": 30, "output_tokens": 12, "usage_reported": True}}


def test_actual_three_step_worker_run_is_private_persistent_idempotent_and_human_reviewed(ai, monkeypatch):
    profile(ai); sent = []
    monkeypatch.setattr(ai_providers, "complete", lambda provider, messages, **kw: (sent.append((provider, messages, kw)) or answer()))
    response = queue(ai, max_steps=3); assert response.status_code == 201, response.text
    row = response.json(); assert row["status"] == "queued"
    assert queue(ai, max_steps=3).json()["id"] == row["id"]
    assert queue(ai, max_steps=3, prompt="Different request").status_code == 409
    for _ in range(3): service.run_pending(ai[1])
    finished = current(ai, row["id"]); assert finished["status"] == "awaiting_review" and len(sent) == 3
    assert [step["status"] for step in finished["steps"]] == ["completed"] * 3
    assert all(step["attempts"] == 1 for step in finished["steps"])
    assert finished["result"]["text"] == "A reviewed answer [S1]"
    assert "OLD PRIVATE CONTENT" in json.dumps(sent[0][1]) and "HIDDEN MUST STAY PRIVATE" not in json.dumps([item[1] for item in sent])
    with Session(ai[1]) as session:
        stored = session.get(AIRun, row["id"])
        assert "Selected private" not in stored.encrypted_payload and "reviewed answer" not in stored.encrypted_result
        assert "access_token" not in stored.encrypted_authority and ai[2]["owner"] not in stored.encrypted_authority
        assert not session.exec(select(TaskCard)).all()
        usage = session.exec(select(AIUsage)).all(); assert len(usage) == 3 and sum(item.charged_tokens for item in usage) == 126
        assert all(item.usage_reported and item.status == "settled" for item in usage)
    accepted = ai[0].post(PREFIX + "/runs/" + row["id"] + "/approve", headers=headers(ai), json={"expected_revision": finished["revision"], "proposal_id": "accept", "confirm": True})
    assert accepted.status_code == 200, accepted.text
    again = ai[0].post(PREFIX + "/runs/" + row["id"] + "/approve", headers=headers(ai), json={"expected_revision": finished["revision"], "proposal_id": "accept", "confirm": True})
    assert again.status_code == 200 and again.json()["already_approved"] and again.json()["completed"]
    assert current(ai, row["id"])["status"] == "completed"


def test_real_task_proposal_has_no_write_until_exact_idempotent_approval(ai, monkeypatch):
    profile(ai)
    with Session(ai[1]) as session: session.add(ProjectBoard(id="project", channel_id="native", created_by="owner")); session.commit()
    monkeypatch.setattr(ai_providers, "complete", lambda *a, **k: answer("Review this task", [{"name": "create_task", "arguments": json.dumps({"project_id": "project", "title": "Reviewed only", "description": "Private"})}]))
    queued = queue(ai, operation="task_plan", target_project_id="project").json(); service.run_pending(ai[1]); row = current(ai, queued["id"])
    with Session(ai[1]) as session: assert not session.exec(select(TaskCard)).all()
    body = {"expected_revision": row["revision"], "proposal_id": "p1", "confirm": True}
    path = PREFIX + "/runs/" + row["id"] + "/approve"
    result = ai[0].post(path, headers=headers(ai), json=body); assert result.status_code == 200, result.text
    again = ai[0].post(path, headers=headers(ai), json=body); assert again.json()["task_id"] == result.json()["task_id"]
    with Session(ai[1]) as session: assert len(session.exec(select(TaskCard)).all()) == 1 and len(session.exec(select(AIRunApproval)).all()) == 1


@pytest.mark.parametrize("change", ["cancel", "consent", "source", "logout", "encryption"])
def test_changed_authority_during_call_cannot_publish_result_or_execute_a_tool(ai, monkeypatch, change):
    profile(ai)
    queued = queue(ai).json()
    def complete(*args, **kwargs):
        if change == "cancel":
            row = current(ai, queued["id"])
            response = ai[0].post(PREFIX + "/runs/" + row["id"] + "/cancel", headers=headers(ai), json={"expected_revision": row["revision"]})
            assert response.status_code == 200, response.text
        else:
            with Session(ai[1]) as session:
                if change == "consent":
                    consent = session.get(AIProfileConsent, ("a", "fast", "owner")); consent.enabled = False; session.add(consent)
                elif change == "source":
                    message = session.get(Message, "old-root"); message.content = encrypt_message("Changed after review", "native"); session.add(message)
                elif change == "logout":
                    for active in session.exec(select(ActiveSession).where(ActiveSession.user_id == "owner")).all(): active.revoked_at = datetime.utcnow(); session.add(active)
                else:
                    from app.models.secure_channel import SecureChannel
                    session.add(SecureChannel(channel_id="native", matrix_room_id="!changed:test", created_by="owner", status="active"))
                session.commit()
        return answer("Must never be retained")
    monkeypatch.setattr(ai_providers, "complete", complete)
    service.run_pending(ai[1])
    with Session(ai[1]) as session:
        run = session.get(AIRun, queued["id"])
        assert run.status == ("cancelled" if change == "cancel" else "failed") and run.encrypted_result is None
        assert not session.exec(select(TaskCard)).all()
        assert not any(step.encrypted_result for step in session.exec(select(AIRunStep)).all())
        assert session.exec(select(AIUsage)).one().charged_tokens == 42


def test_queued_work_cannot_send_after_personal_key_or_profile_revision_changes(ai, monkeypatch):
    profile(ai, key_source="personal", api_key=None)
    malformed = ai[0].put(PREFIX + "/personal-keys/fast", headers=headers(ai), json={"password": PASSWORD, "expected_profile_revision": 1, "api_key": "invalid\r\nAuthorization: leaked"})
    assert malformed.status_code == 422
    response = ai[0].put(PREFIX + "/personal-keys/fast", headers=headers(ai), json={"password": PASSWORD, "expected_profile_revision": 1, "api_key": KEY}); assert response.status_code == 200
    queued = queue(ai).json()
    response = ai[0].put(PREFIX + "/personal-keys/fast", headers=headers(ai), json={"password": PASSWORD, "expected_profile_revision": 1, "api_key": "different-personal-secret"}); assert response.status_code == 200
    sent = []; monkeypatch.setattr(ai_providers, "complete", lambda *a, **k: sent.append(True))
    service.run_pending(ai[1]); assert not sent
    assert current(ai, queued["id"])["status"] == "failed"
    with Session(ai[1]) as session:
        stored = session.get(AIPersonalKey, ("a", "fast", "owner")); assert KEY not in stored.encrypted_key and "different-personal" not in stored.encrypted_key
    status = ai[0].get(PREFIX, headers=headers(ai)).json(); assert KEY not in json.dumps(status) and "different-personal" not in json.dumps(status)
    assert ai[0].get(PREFIX + "/runs/" + queued["id"], headers=headers(ai, "peer")).status_code == 404


def test_unknown_provider_attempt_needs_explicit_resume_and_has_a_bounded_retry_budget(ai, monkeypatch):
    profile(ai); calls = []
    def failure(*args, **kwargs): calls.append(True); raise HTTPException(502, "Provider uncertain")
    monkeypatch.setattr(ai_providers, "complete", failure)
    queued = queue(ai).json(); service.run_pending(ai[1]); failed = current(ai, queued["id"])
    assert failed["status"] == "failed" and len(calls) == 1
    with Session(ai[1]) as session:
        usage = session.exec(select(AIUsage)).one(); assert usage.status == "unknown" and usage.charged_tokens == usage.reserved_tokens
    path = PREFIX + "/runs/" + queued["id"] + "/resume"
    assert ai[0].post(path, headers=headers(ai), json={"expected_revision": failed["revision"], "acknowledge_possible_duplicate_charge": False}).status_code == 422
    resumed = ai[0].post(path, headers=headers(ai), json={"expected_revision": failed["revision"], "acknowledge_possible_duplicate_charge": True}); assert resumed.status_code == 200
    service.run_pending(ai[1]); again = current(ai, queued["id"])
    assert again["steps"][0]["attempts"] == 2 and not again["can_resume"]
    assert ai[0].post(path, headers=headers(ai), json={"expected_revision": again["revision"], "acknowledge_possible_duplicate_charge": True}).status_code == 409


def test_expired_worker_claim_is_not_automatically_retried_or_falsely_free(ai, monkeypatch):
    profile(ai); queued = queue(ai).json()
    with Session(ai[1]) as session:
        row = session.get(AIRun, queued["id"]); row.status = "running"; row.claim_token = "lost-worker"; row.claim_until = datetime.utcnow() - timedelta(seconds=1); session.add(row)
        step = session.get(AIRunStep, (row.id, 0)); step.status = "running"; step.attempts = 1; session.add(step)
        session.add(AIUsage(workspace_id="a", user_id="owner", execution_id=row.id, profile_id="fast", profile_revision=1, reserved_tokens=100, reserved_cost_micros=50)); session.commit()
    calls = []; monkeypatch.setattr(ai_providers, "complete", lambda *a, **k: calls.append(True))
    service.run_pending(ai[1]); assert not calls
    row = current(ai, queued["id"]); assert row["status"] == "failed" and row["error"] == "interrupted_attempt_review_required"
    with Session(ai[1]) as session:
        ledger = session.exec(select(AIUsage)).one(); assert ledger.status == "unknown" and ledger.charged_tokens == 100


def test_budget_is_reserved_before_calls_and_applies_to_legacy_foreground(ai, monkeypatch):
    profile(ai)
    result = ai[0].put(PREFIX + "/budget", headers=headers(ai), json={"password": PASSWORD, "expected_revision": 0, "max_tokens": 100, "max_cost_micros": 0}); assert result.status_code == 200
    calls = []; monkeypatch.setattr(ai_providers, "complete", lambda *a, **k: calls.append(True))
    queued = queue(ai).json(); service.run_pending(ai[1]); assert not calls and current(ai, queued["id"])["status"] == "failed"
    from app.services import copilot
    monkeypatch.setattr(copilot, "completion", lambda *a, **k: calls.append(True))
    legacy = ask(ai, conversation(ai)); assert legacy.status_code == 409, legacy.text
    assert not calls


def test_secure_completion_is_profile_aware_but_never_a_persistent_plaintext_run(ai, monkeypatch):
    profile(ai); calls = []
    monkeypatch.setattr(ai_providers, "complete", lambda provider, messages, **kw: (calls.append((provider, messages)) or answer("Ephemeral secure answer")))
    assert queue(ai, channel_id="secure", sources=[], selected_text="Secret device content").status_code == 409
    body = {"request_id": "secure-foreground-only", "channel_id": "secure", "profile_id": "fast", "expected_profile_revision": 1,
        "operation": "draft", "prompt": "Polish my draft", "selected_text": "Secret device content", "sources": [], "disclose_to_provider": True,
        "disclose_decrypted_content": False, "max_steps": 1, "max_tokens": 512}
    assert ai[0].post(PREFIX + "/complete", headers=headers(ai), json=body).status_code == 422
    result = ai[0].post(PREFIX + "/complete", headers=headers(ai), json={**body, "disclose_decrypted_content": True}); assert result.status_code == 200, result.text
    assert result.json()["ephemeral"] and calls[0][0].model == "model-one"
    with Session(ai[1]) as session:
        assert not session.exec(select(AIRun)).all() and not session.exec(select(AIRunStep)).all()
        assert "Secret device" not in json.dumps([row.model_dump(mode="json") for row in session.exec(select(AIUsage)).all()])


@pytest.mark.parametrize("foreign", ["missing", "old-root"])
def test_grouping_accepts_only_unique_selected_actual_resource_ids(ai, monkeypatch, foreign):
    profile(ai)
    group = {"title": "Proposal", "source_ids": [foreign] if foreign == "missing" else [foreign, foreign], "reason": "Related subject"}
    monkeypatch.setattr(ai_providers, "complete", lambda *a, **k: answer(json.dumps({"groups": [group]})))
    queued = queue(ai, operation="group").json(); service.run_pending(ai[1]); assert current(ai, queued["id"])["status"] == "failed"


@pytest.mark.parametrize("protocol", ["openai", "xai", "azure", "anthropic", "gemini", "ollama"])
def test_native_provider_adapter_maps_exact_headers_usage_and_bounded_text(monkeypatch, protocol):
    sent = []
    provider = ai_providers.Provider(name="Native", protocol=protocol, endpoint="https://provider.example.com/native", model="native-model", api_key=KEY)
    packets = {
        "openai": {"choices": [{"finish_reason": "stop", "message": {"content": "Native answer"}}], "usage": {"prompt_tokens": 4, "completion_tokens": 2}},
        "anthropic": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Native answer"}], "usage": {"input_tokens": 4, "output_tokens": 2}},
        "gemini": {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "Native answer"}]}}], "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2}},
        "ollama": {"done": True, "done_reason": "stop", "message": {"content": "Native answer"}, "prompt_eval_count": 4, "eval_count": 2}}
    def transport(current, payload, *, headers): sent.append((current, payload, headers)); return packets.get(protocol, packets["openai"])
    monkeypatch.setattr(ai_providers, "request", transport)
    result = ai_providers.complete(provider, [{"role": "system", "content": "Guard"}, {"role": "user", "content": "Reviewed"}], max_tokens=512)
    assert result["text"] == "Native answer" and result["usage"] == {"input_tokens": 4, "output_tokens": 2, "usage_reported": True}
    assert len(sent) == 1 and sent[0][0].model == "native-model"
    expected = "x-api-key" if protocol == "anthropic" else "x-goog-api-key" if protocol == "gemini" else "api-key" if protocol == "azure" else "Authorization"
    assert expected in sent[0][2]


def test_private_ollama_requires_exact_operator_allowlist_and_never_metadata(monkeypatch):
    monkeypatch.delenv("FREECOLLAB_AI_PRIVATE_ORIGINS", raising=False)
    for destination in ["http://localhost:11434/api/chat", "https://127.0.0.1/api/chat", "https://169.254.169.254/latest"]:
        with pytest.raises(ValueError): ai_providers.Provider(name="Local", protocol="ollama", endpoint=destination, model="local")
    monkeypatch.setenv("FREECOLLAB_AI_PRIVATE_ORIGINS", json.dumps(["http://localhost:11434", "http://169.254.169.254"]))
    assert ai_providers.Provider(name="Local", protocol="ollama", endpoint="http://localhost:11434/api/chat", model="local").model == "local"
    with pytest.raises(ValueError): ai_providers.Provider(name="Metadata", protocol="ollama", endpoint="http://169.254.169.254/latest", model="local")
    assert ai_providers.Provider(name="Private compatible", protocol="openai", endpoint="http://localhost:11434/v1/chat/completions", model="local", api_key="").model == "local"
    with pytest.raises(ValueError): ai_providers.Provider(name="Other port", protocol="openai", endpoint="http://localhost:11435/v1/chat/completions", model="local", api_key=KEY)
    for forbidden in ["http://8.8.8.8", "https://[::ffff:169.254.169.254]", "https://[64:ff9b::a9fe:a9fe]", "http://100.100.100.200", "https://[fd00:ec2::254]"]:
        monkeypatch.setenv("FREECOLLAB_AI_PRIVATE_ORIGINS", json.dumps([forbidden]))
        with pytest.raises(ValueError): ai_providers.Provider(name="Forbidden", protocol="ollama", endpoint=forbidden + "/api/chat", model="local")


@pytest.mark.parametrize("protocol", ["openai", "xai", "azure", "anthropic", "gemini", "ollama"])
def test_real_trusted_https_json_native_protocol_without_external_network(tmp_path, monkeypatch, protocol):
    from tests.oidc_mock import tls_fixture
    certificate, key = tls_fixture(tmp_path / "tls")
    captured = []
    packets = {
        "openai": {"choices": [{"finish_reason": "stop", "message": {"content": "Actual HTTPS answer"}}], "usage": {"prompt_tokens": 11, "completion_tokens": 7}},
        "anthropic": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Actual HTTPS answer"}], "usage": {"input_tokens": 11, "output_tokens": 7}},
        "gemini": {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "Actual HTTPS answer"}]}}], "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7}},
        "ollama": {"done": True, "done_reason": "stop", "message": {"content": "Actual HTTPS answer"}, "prompt_eval_count": 11, "eval_count": 7}}
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *_): pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            captured.append((self.path, payload, dict(self.headers)))
            data = json.dumps(packets.get(protocol, packets["openai"])).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers()
            for index in range(0, len(data), 5): self.wfile.write(data[index:index + 5]); self.wfile.flush()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(certificate, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    destination = "https://localhost:" + str(server.server_port) + "/native"
    normal, proxy = ai_providers.endpoint, ai_providers.transport._proxy
    # Only this owned fixture endpoint gets a test policy substitution. TLS,
    # bounded production request framing and all native translators are actual.
    monkeypatch.setattr(ai_providers, "endpoint", lambda value, kind: (urlsplit(value), "localhost", True) if value == destination else normal(value, kind))
    monkeypatch.setattr(ai_providers.transport, "_proxy", lambda host, port: None if host == "localhost" else proxy(host, port))
    monkeypatch.setenv("SSL_CERT_FILE", certificate)
    try:
        provider = ai_providers.Provider(name="Owned HTTPS", protocol=protocol, endpoint=destination, model="actual-model", api_key=KEY)
        result = ai_providers.complete(provider, [{"role": "system", "content": "Explicit policy"}, {"role": "user", "content": "Reviewed private source"}], max_tokens=512)
        assert result["text"] == "Actual HTTPS answer" and result["usage"]["input_tokens"] == 11
        assert len(captured) == 1 and "Reviewed private source" in json.dumps(captured[0][1]) and KEY not in json.dumps(captured[0][1])
        if protocol == "azure": assert captured[0][0].endswith("?api-version=2024-10-21") and captured[0][2]["api-key"] == KEY
        if protocol == "anthropic": assert captured[0][2]["x-api-key"] == KEY and captured[0][2]["anthropic-version"] == "2023-06-01"
        if protocol == "gemini": assert captured[0][2]["x-goog-api-key"] == KEY and "Explicit policy" in json.dumps(captured[0][1]["systemInstruction"])
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def test_shared_execution_projection_uses_real_domain_states_and_never_other_private_prompts(ai, monkeypatch):
    from app.models.app_platform import AppAIDraft, ChannelRecapSchedule
    profile(ai); queued = queue(ai).json()
    with Session(ai[1]) as session:
        session.add(AppAIDraft(id="own-draft", workspace_id="a", channel_id="native", owner_id="owner", kind="summary", provider_revision=1, encrypted_result="not-opened-by-projection"))
        session.add(AppAIDraft(id="other-draft", workspace_id="a", channel_id="native", owner_id="peer", kind="summary", provider_revision=1, encrypted_result="PRIVATE OTHER CONTENT"))
        session.add(ChannelRecapSchedule(id="own-schedule", workspace_id="a", channel_id="native", owner_id="owner", provider_revision=1, interval_hours=24))
        session.commit()
    response = ai[0].get(PREFIX + "/executions", headers=headers(ai)); assert response.status_code == 200, response.text
    items = response.json()["items"]; assert {"run:" + queued["id"], "app-draft:own-draft", "recap:own-schedule"} <= {item["id"] for item in items}
    assert "other-draft" not in response.text and "PRIVATE OTHER" not in response.text and "Selected private" not in response.text
    assert next(item for item in items if item["family"] == "app-draft")["status"] == "awaiting_review"
    assert all(item["content_included"] is False for item in items)


def test_actor_cleanup_and_export_never_commit_the_callers_transaction(ai, monkeypatch):
    profile(ai); queued = queue(ai).json()
    with Session(ai[1]) as session:
        session.add(AIBudget(workspace_id="a", revision=1, max_tokens=12345, updated_by="owner")); session.flush()
        export = service.export_actor(session, "owner", ["native"])
        assert export["runs"][0]["id"] == queued["id"] and not export["credentials_exported"]
        assert service.cleanup_actor(session, "owner") == 1
        session.rollback()
    with Session(ai[1]) as session:
        assert session.get(AIRun, queued["id"]) is not None and session.get(AIBudget, "a") is None
        assert session.get(AIModelProfile, ("a", "fast")).updated_by == "owner"


def test_reviewed_multi_model_plan_hands_off_real_output_without_unreviewed_routing(ai, monkeypatch):
    profile(ai); profile(ai, "careful", model="second-model")
    sent = []
    def complete(provider, messages, **kwargs):
        sent.append((provider.model, messages))
        return answer("First model conclusion [S1]" if len(sent) == 1 else "Second model refinement [S1]")
    monkeypatch.setattr(ai_providers, "complete", complete)
    plan = [{"operation": "summarize", "profile_id": "fast", "expected_profile_revision": 1},
        {"operation": "compare", "profile_id": "careful", "expected_profile_revision": 1, "instructions": "Check the first model's assumptions"}]
    queued = queue(ai, max_steps=2, plan=plan); assert queued.status_code == 201, queued.text
    service.run_pending(ai[1]); service.run_pending(ai[1])
    result = current(ai, queued.json()["id"])
    assert result["status"] == "awaiting_review" and [item[0] for item in sent] == ["model-one", "second-model"]
    assert "First model conclusion" in json.dumps(sent[1][1]) and "Check the first model" in json.dumps(sent[1][1])
    assert result["plan"][1]["profile_id"] == "careful"
    assert queue(ai, request_id="bad-plan-count", max_steps=1, plan=plan).status_code == 422
    assert queue(ai, request_id="bad-plan-first", max_steps=2, plan=list(reversed(plan))).status_code == 422
    with Session(ai[1]) as session:
        consent = session.get(AIProfileConsent, ("a", "careful", "owner")); consent.enabled = False; session.add(consent); session.commit()
    assert queue(ai, request_id="plan-needs-every-recipient", max_steps=2, plan=plan).status_code == 403


def test_removing_private_history_cannot_refund_used_token_budget(ai, monkeypatch):
    profile(ai); monkeypatch.setattr(ai_providers, "complete", lambda *a, **k: answer())
    queued = queue(ai).json(); assert ai[0].delete(PREFIX + "/runs/" + queued["id"], headers=headers(ai)).status_code == 409
    service.run_pending(ai[1])
    before = ai[0].get(PREFIX, headers=headers(ai)).json()["budget"]
    assert ai[0].delete(PREFIX + "/runs/" + queued["id"], headers=headers(ai)).status_code == 204
    after = ai[0].get(PREFIX, headers=headers(ai)).json()["budget"]
    assert after["used_tokens"] == before["used_tokens"] == 42
    assert ai[0].get(PREFIX + "/runs/" + queued["id"], headers=headers(ai)).status_code == 404


@pytest.fixture
def read_endpoint(tmp_path, monkeypatch):
    from tests.oidc_mock import tls_fixture
    from app.models.ai_runtime import AIReadTool
    certificate, key = tls_fixture(tmp_path / "read-tls")
    state = {"requests": [], "change": None}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            state["requests"].append((self.path, dict(self.headers)))
            if state["change"]: state["change"]()
            data = json.dumps({"results": [{"title": "Actual search hit", "url": "https://source.example.com/page", "content": "Real returned snippet " + KEY}], "object": {"status": "ready"}}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler); context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(certificate, key); server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    destination = "https://localhost:" + str(server.server_port) + "/search"
    monkeypatch.setenv("SSL_CERT_FILE", certificate)
    monkeypatch.setenv("FREECOLLAB_AI_TOOL_PRIVATE_ORIGINS", json.dumps(["https://localhost:" + str(server.server_port)]))
    proxy = ai_providers.transport._proxy
    monkeypatch.setattr(ai_providers.transport, "_proxy", lambda host, port: None if host == "localhost" else proxy(host, port))
    try: yield destination, state
    finally: server.shutdown(); server.server_close(); thread.join(timeout=5)


def configure_read(ai, endpoint, **changes):
    response = ai[0].put(PREFIX + "/tools/research", headers=headers(ai), json={"password": PASSWORD, "expected_revision": 0,
        "name": "Reviewed search provider", "kind": "web_search", "endpoint": endpoint, "method": "GET", "api_key": KEY,
        "auth_header": "x-api-key", "query_field": "q", "result_path": ["results"], "max_results": 5, "enabled": True, **changes})
    return response


def test_actual_registered_https_search_requires_its_own_disclosure_and_returns_reviewable_citations(ai, read_endpoint):
    endpoint, state = read_endpoint
    configured = configure_read(ai, endpoint); assert configured.status_code == 200, configured.text
    assert KEY not in configured.text and KEY not in ai[0].get(PREFIX + "/tools", headers=headers(ai)).text
    body = {"channel_id": "native", "expected_revision": 1, "query": "reviewed search query", "disclose_to_tool": False}
    path = PREFIX + "/tools/research/read"
    assert ai[0].post(path, headers=headers(ai), json=body).status_code == 422
    assert ai[0].post(path, headers=headers(ai), json={**body, "disclose_to_tool": True, "expected_revision": 2}).status_code == 409
    assert not state["requests"]
    response = ai[0].post(path, headers=headers(ai), json={**body, "disclose_to_tool": True}); assert response.status_code == 200, response.text
    result = response.json(); assert result["ephemeral"] and result["results"][0]["url"] == "https://source.example.com/page"
    assert "Real returned snippet" in result["text"] and KEY not in response.text and "[credential omitted]" in response.text
    assert state["requests"][0][0] == "/search?q=reviewed+search+query" and state["requests"][0][1]["x-api-key"] == KEY
    with Session(ai[1]) as session:
        assert not session.exec(select(AIRun)).all()
        from app.models.ai_runtime import AIReadTool
        row = session.get(AIReadTool, ("a", "research")); assert KEY not in row.encrypted_config and "returned snippet" not in row.encrypted_config


def test_changed_registered_tool_during_real_search_never_returns_stale_content(ai, read_endpoint):
    from app.models.ai_runtime import AIReadTool
    endpoint, state = read_endpoint; assert configure_read(ai, endpoint).status_code == 200
    def revoke():
        with Session(ai[1]) as session:
            row = session.get(AIReadTool, ("a", "research")); row.enabled = False; session.add(row); session.commit()
    state["change"] = revoke
    response = ai[0].post(PREFIX + "/tools/research/read", headers=headers(ai), json={"channel_id": "native", "expected_revision": 1, "query": "private", "disclose_to_tool": True})
    assert response.status_code == 409 and "Real returned snippet" not in response.text
    assert len(state["requests"]) == 1


def test_http_read_is_registered_get_only_and_secure_queries_need_separate_ack(ai, read_endpoint):
    endpoint, state = read_endpoint
    assert configure_read(ai, endpoint, kind="http_read", method="POST", result_path=["object"]).status_code == 422
    assert configure_read(ai, endpoint, kind="http_read", result_path=["object"]).status_code == 200
    body = {"channel_id": "secure", "expected_revision": 1, "query": "disclosed device query", "disclose_to_tool": True}
    assert ai[0].post(PREFIX + "/tools/research/read", headers=headers(ai), json=body).status_code == 422
    assert not state["requests"]
    response = ai[0].post(PREFIX + "/tools/research/read", headers=headers(ai), json={**body, "disclose_decrypted_content": True})
    assert response.status_code == 200, response.text
    assert response.json()["text"] == '{"status":"ready"}'


def test_actual_planner_uses_only_reviewed_model_and_operation_catalog_then_waits_for_review(ai, monkeypatch):
    profile(ai); profile(ai, "careful", model="reviewed-second-model")
    calls = []
    def complete(provider, messages, **kwargs):
        calls.append((provider.model, messages))
        return answer(json.dumps({"plan": [{"operation": "summarize", "profile_id": "fast", "instructions": "Summarize selected evidence"},
            {"operation": "compare", "profile_id": "careful", "instructions": "Check the first answer"}]}))
    monkeypatch.setattr(ai_providers, "complete", complete)
    body = {"request_id": "bounded-planner-request", "channel_id": "native", "profile_id": "fast", "expected_profile_revision": 1,
        "operation": "answer", "prompt": "Prepare a careful summary", "sources": [{"kind": "message", "id": "old-root"}],
        "max_steps": 2, "max_tokens": 512, "disclose_to_provider": True,
        "allowed_profiles": [{"profile_id": "fast", "expected_profile_revision": 1}, {"profile_id": "careful", "expected_profile_revision": 1}]}
    response = ai[0].post(PREFIX + "/plans", headers=headers(ai), json=body); assert response.status_code == 200, response.text
    assert [step["profile_id"] for step in response.json()["plan"]] == ["fast", "careful"] and response.json()["ephemeral"]
    assert len(calls) == 1 and "reviewed-second-model" in json.dumps(calls[0][1]) and KEY not in json.dumps(calls[0][1])
    with Session(ai[1]) as session: assert not session.exec(select(AIRun)).all() and not session.exec(select(TaskCard)).all()
    assert ai[0].post(PREFIX + "/plans", headers=headers(ai), json=body).status_code == 409 and len(calls) == 1
    monkeypatch.setattr(ai_providers, "complete", lambda *a, **k: answer(json.dumps({"plan": [{"operation": "answer", "profile_id": "unreviewed-model"}]})))
    assert ai[0].post(PREFIX + "/plans", headers=headers(ai), json={**body, "request_id": "invalid-planner-reply"}).status_code == 502


def test_profile_budget_and_recipient_revision_are_checked_before_sending_a_foreground_retry(ai, monkeypatch):
    profile(ai); calls = []
    monkeypatch.setattr(ai_providers, "complete", lambda *a, **k: (calls.append(True) or answer("Reviewed writing")))
    body = {"request_id": "foreground-no-double-billing", "channel_id": "native", "profile_id": "fast", "expected_profile_revision": 1,
        "operation": "draft", "prompt": "Review text", "selected_text": "User draft", "max_steps": 1, "max_tokens": 512, "disclose_to_provider": True}
    assert ai[0].post(PREFIX + "/complete", headers=headers(ai), json=body).status_code == 200
    assert ai[0].post(PREFIX + "/complete", headers=headers(ai), json=body).status_code == 409 and len(calls) == 1
    result = ai[0].put(PREFIX + "/budget", headers=headers(ai), json={"password": PASSWORD, "expected_revision": 0, "max_tokens": 200000, "max_cost_micros": 1})
    assert result.status_code == 200
    assert ai[0].post(PREFIX + "/complete", headers=headers(ai), json={**body, "request_id": "over-monetary-budget"}).status_code == 409
    assert len(calls) == 1


def test_queued_rag_uses_trusted_canonical_descriptor_after_public_reference_expires(ai, monkeypatch):
    from app.services import search_sources
    profile(ai)
    with Session(ai[1]) as session:
        item = search_sources.source(session, "native", "message", session.get(Message, "old-root"))
        capability = search_sources.reference("owner", item)
    response = queue(ai, sources=[{"kind": "resource", "id": "old-root", "reference": capability}]); assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    with Session(ai[1]) as session:
        stored = session.get(AIRun, identifier); payload = service.copilot.opened(stored.encrypted_payload, "run-payload", identifier)
        assert payload["refs"][0]["source_kind"] == "message" and "reference" not in payload["refs"][0]
    original = search_sources.datetime
    class Later:
        @staticmethod
        def utcnow(): return original.utcnow() + timedelta(minutes=20)
    monkeypatch.setattr(search_sources, "datetime", Later)
    with Session(ai[1]) as session:
        with pytest.raises(HTTPException): search_sources.resolve(session, "owner", capability, commit=False)
    sent = []; monkeypatch.setattr(ai_providers, "complete", lambda *a, **k: (sent.append(True) or answer("Durable RAG answer [S1]")))
    service.run_pending(ai[1]); result = current(ai, identifier)
    assert result["status"] == "awaiting_review" and len(sent) == 1 and result["result"]["citations"][0]["reference"] != capability
    with Session(ai[1]) as session:
        session.add(AIBudget(workspace_id="a", max_tokens=999999, updated_by="owner")); session.flush()
        assert service.export_actor(session, "owner", ["native"])["runs"]
        session.rollback()
    with Session(ai[1]) as session: assert session.get(AIBudget, "a") is None


def test_cross_channel_rag_needs_explicit_review_and_never_crosses_workspace_boundary(ai, monkeypatch):
    from app.models.channel import Channel, ChannelMember
    from app.models.workspace import WorkspaceChannel, WorkspaceMember
    from app.services import search_sources
    profile(ai)
    with Session(ai[1]) as session:
        session.add(Channel(id="another-native", name="Another native", type="group")); session.flush()
        session.add(WorkspaceChannel(channel_id="another-native", workspace_id="a")); session.add(ChannelMember(channel_id="another-native", user_id="owner", role="admin"))
        session.add(WorkspaceMember(workspace_id="b", user_id="owner")); session.add(ChannelMember(channel_id="other", user_id="owner", role="admin")); session.flush()
        session.add(Message(id="cross-source", channel_id="another-native", author_id="owner", content=encrypt_message("Reviewed other channel context", "another-native"), encrypted=True))
        session.add(Message(id="other-workspace-source", channel_id="other", author_id="owner", content=encrypt_message("Other workspace context", "other"), encrypted=True)); session.commit()
        native = search_sources.source(session, "another-native", "message", session.get(Message, "cross-source"))
        other = search_sources.source(session, "other", "message", session.get(Message, "other-workspace-source"))
        native_reference, other_reference = search_sources.reference("owner", native), search_sources.reference("owner", other)
    sources = [{"kind": "resource", "id": "cross-source", "reference": native_reference}]
    assert queue(ai, sources=sources).status_code == 422
    allowed = queue(ai, sources=sources, disclose_cross_channel_sources=True); assert allowed.status_code == 201, allowed.text
    sent = []; monkeypatch.setattr(ai_providers, "complete", lambda provider, messages, **kw: (sent.append(messages) or answer()))
    service.run_pending(ai[1]); assert "Reviewed other channel context" in json.dumps(sent)
    denied = queue(ai, request_id="cannot-cross-workspaces", sources=[{"kind": "resource", "id": "other-workspace-source", "reference": other_reference}], disclose_cross_channel_sources=True)
    assert denied.status_code == 422 and len(sent) == 1
    with Session(ai[1]) as session:
        assert service.pending_in_channels(session, ["another-native"])
        assert not service.export_actor(session, "owner", ["native"])["runs"]
        assert len(service.export_actor(session, "owner", ["native", "another-native"])["runs"]) == 1
    row = current(ai, allowed.json()["id"])
    assert ai[0].post(PREFIX + "/runs/" + row["id"] + "/cancel", headers=headers(ai), json={"expected_revision": row["revision"]}).status_code == 200
    with Session(ai[1]) as session:
        assert not service.pending_in_channels(session, ["another-native"])
        assert service.cleanup_channels(session, ["another-native"]) == 1
        session.commit()
        assert session.get(AIRun, row["id"]) is None and service.budget_status(session, "a")["used_tokens"] == 42


def test_account_cleanup_during_provider_call_keeps_anonymous_budget_and_never_recreates_run(ai, monkeypatch):
    profile(ai); queued = queue(ai).json()
    observed = []
    def complete(*args, **kwargs):
        with Session(ai[1]) as session:
            assert service.cleanup_actor(session, "owner") == 1
            budget = service.budget_status(session, "a")
            assert budget["reserved_tokens"] == 0 and budget["used_tokens"] > 0
            ledger = session.exec(select(AIUsage)).one()
            assert ledger.user_id is None and ledger.execution_id.startswith("erased:") and ledger.status == "unknown"
            session.commit(); observed.append(True)
        return answer("This deleted actor's private output must disappear")
    monkeypatch.setattr(ai_providers, "complete", complete)
    service.run_pending(ai[1]); assert observed == [True]
    with Session(ai[1]) as session:
        assert session.get(AIRun, queued["id"]) is None and not session.exec(select(AIRunStep)).all()
        ledger = session.exec(select(AIUsage)).one(); assert ledger.user_id is None and ledger.profile_id == "erased" and ledger.charged_tokens == 42
        budget = service.budget_status(session, "a"); assert budget["used_tokens"] == 42 and budget["reserved_tokens"] == 0
        assert not session.exec(select(TaskCard)).all()


def test_private_compatible_model_uses_real_registered_http_without_api_key(monkeypatch):
    captured = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            captured.append((json.loads(self.rfile.read(int(self.headers["Content-Length"]))), self.headers.get("Authorization")))
            data = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "Private compatible answer"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    origin = "http://localhost:" + str(server.server_port)
    monkeypatch.setenv("FREECOLLAB_AI_PRIVATE_ORIGINS", json.dumps([origin]))
    inherited = ai_providers.proxy_for
    monkeypatch.setattr(ai_providers, "proxy_for", lambda scheme, host, port: None if host == "localhost" else inherited(scheme, host, port))
    try:
        provider = ai_providers.Provider(name="Owned private compatible", protocol="openai", endpoint=origin + "/v1/chat/completions", model="vllm-local")
        result = ai_providers.complete(provider, [{"role": "user", "content": "Only selected local context"}], max_tokens=512)
        assert result["text"] == "Private compatible answer" and captured[0][0]["model"] == "vllm-local" and captured[0][1] is None
    finally: server.shutdown(); server.server_close(); thread.join(timeout=5)


def test_secure_device_group_catalog_is_ephemeral_and_cannot_be_used_for_native_queued_work(ai, monkeypatch):
    profile(ai)
    body = {"request_id": "secure-group-device-catalog", "channel_id": "secure", "profile_id": "fast", "expected_profile_revision": 1,
        "operation": "group", "prompt": "Group the reviewed device sources", "sources": [], "selected_text": "[task:one] First task\n[feed:two] Related discussion",
        "reviewed_source_ids": ["task:one", "feed:two"], "max_steps": 1, "max_tokens": 512, "disclose_to_provider": True, "disclose_decrypted_content": True}
    monkeypatch.setattr(ai_providers, "complete", lambda *a, **k: answer(json.dumps({"groups": [{"title": "Reviewed group", "source_ids": ["task:one", "feed:two"], "reason": "Same selected topic"}]})))
    result = ai[0].post(PREFIX + "/complete", headers=headers(ai), json=body); assert result.status_code == 200, result.text
    assert result.json()["groups"][0]["source_ids"] == ["task:one", "feed:two"]
    assert all(item["kind"] == "device_catalog" and "available" not in item for item in result.json()["citations"])
    assert ai[0].post(PREFIX + "/runs", headers=headers(ai), json={**body, "channel_id": "native"}).status_code == 422
    assert ai[0].post(PREFIX + "/complete", headers=headers(ai), json={**body, "channel_id": "native", "request_id": "native-cannot-fake-catalog"}).status_code == 422
    with Session(ai[1]) as session:
        assert not session.exec(select(AIRun)).all()
        assert "Related discussion" not in json.dumps([item.model_dump(mode="json") for item in session.exec(select(AIUsage)).all()])


def test_default_profile_price_configuration_keeps_legacy_ai_usable_under_money_cap(ai, monkeypatch):
    from app.services import copilot
    response = ai[0].put(PREFIX + "/budget", headers=headers(ai), json={"password": PASSWORD, "expected_revision": 0, "max_tokens": 200000,
        "max_cost_micros": 1000000, "default_input_cost_per_million_micros": 1000000, "default_output_cost_per_million_micros": 2000000})
    assert response.status_code == 200, response.text
    monkeypatch.setattr(copilot, "completion", lambda *a, **k: answer())
    response = ask(ai, conversation(ai)); assert response.status_code == 200, response.text
    state = ai[0].get(PREFIX, headers=headers(ai)).json()
    assert state["budget"]["used_tokens"] == 42 and state["budget"]["used_cost_micros"] == 54
    assert next(item for item in state["profiles"] if item["id"] == "default")["input_cost_per_million_micros"] == 1000000


def test_selected_writing_and_source_snapshots_preserve_exact_whitespace(ai):
    from app.services import copilot
    profile(ai)
    writing = "\n    const value = 3;\n\n"
    response = queue(ai, selected_text=writing); assert response.status_code == 201, response.text
    with Session(ai[1]) as session:
        row = session.get(AIRun, response.json()["id"])
        assert copilot.opened(row.encrypted_payload, "run-payload", row.id)["selected_text"] == writing
    assert copilot.Source(kind="shared_document", id="doc", text=writing).text == writing


def test_workspace_cleanup_preserves_anonymous_numeric_receipts_without_committing_or_exporting_other_workspaces(ai):
    profile(ai)
    queued = queue(ai).json()
    with Session(ai[1]) as session:
        session.add(AIUsage(id="receipt-a", workspace_id="a", user_id="owner", execution_id=queued["id"], profile_id="fast", profile_revision=1,
            reserved_tokens=100, reserved_cost_micros=20))
        session.add(AIUsage(id="receipt-b", workspace_id="b", user_id="owner", execution_id="private-other-workspace", profile_id="private-b", profile_revision=1,
            status="settled", reserved_tokens=100, reserved_cost_micros=20, charged_tokens=75, estimated_cost_micros=15))
        session.commit()
        scoped = service.export_actor(session, "owner", ["native"], workspace_id="a")
        assert len(scoped["usage"]) == 1 and scoped["usage"][0]["profile_id"] == "fast"
        service.cleanup_workspace(session, "a")
        receipt = session.get(AIUsage, "receipt-a")
        assert receipt.workspace_id is None and receipt.user_id is None and receipt.status == "unknown"
        assert receipt.charged_tokens == 100 and receipt.estimated_cost_micros == 20
        assert session.get(AIRun, queued["id"]) is None
        session.rollback()
    with Session(ai[1]) as session:
        assert session.get(AIRun, queued["id"]) is not None
        service.cleanup_workspace(session, "a"); session.commit()
        receipt = session.get(AIUsage, "receipt-a")
        assert receipt.workspace_id is None and receipt.user_id is None and receipt.profile_id == "erased"
        assert session.get(AIUsage, "receipt-b").workspace_id == "b"
        # The held provider can settle its captured receipt ID without reviving
        # the erased run, private identity, or workspace foreign key.
        service.settle(session, receipt.id, ai_providers.Provider(name="Receipt only", protocol="openai", endpoint="https://model.example/v1/chat/completions", model="model", api_key=KEY),
            {"input_tokens": 30, "output_tokens": 12, "usage_reported": True})
        session.commit()
        assert receipt.charged_tokens == 42 and receipt.workspace_id is None and receipt.user_id is None


def test_registered_read_queries_follow_workspace_native_dlp_before_any_external_disclosure(ai, monkeypatch):
    from app.models.enterprise_governance import NativeDLPPolicy
    from app.services.account_security import seal_private
    path = PREFIX + "/tools/search"
    configured = ai[0].put(path, headers=headers(ai), json={"password": PASSWORD, "expected_revision": 0, "name": "Reviewed search", "kind": "web_search",
        "endpoint": "https://search.example.com/query", "method": "GET"})
    assert configured.status_code == 200, configured.text
    with Session(ai[1]) as session:
        policy = NativeDLPPolicy(id="private-query-policy", workspace_id="a", name="Protected query", detector="literal", action="block", surfaces_json='["assistant"]',
            encrypted_literal=seal_private("confidential phrase", "native-dlp", "private-query-policy"), created_by="owner")
        session.add(policy); session.commit()
    calls = []; monkeypatch.setattr(ai_providers, "request", lambda *a, **kw: calls.append(True))
    response = ai[0].post(path + "/read", headers=headers(ai), json={"channel_id": "native", "expected_revision": 1, "query": "Look up confidential phrase", "disclose_to_tool": True})
    assert response.status_code == 423, response.text
    assert not calls


def test_cross_channel_source_changed_before_its_write_fence_cannot_persist_an_old_private_snapshot(ai, monkeypatch):
    from app.models.channel import Channel, ChannelMember
    from app.models.workspace import WorkspaceChannel
    from app.services import access, search_sources
    from sqlalchemy import update
    profile(ai)
    with Session(ai[1]) as session:
        session.add(Channel(id="a-source-channel", name="Reviewed source", type="group")); session.flush()
        session.add(WorkspaceChannel(channel_id="a-source-channel", workspace_id="a"))
        session.add(ChannelMember(channel_id="a-source-channel", user_id="owner", role="admin"))
        session.add(Message(id="source-race", channel_id="a-source-channel", author_id="owner", content=encrypt_message("Private source before erasure", "a-source-channel"), encrypted=True)); session.commit()
        item = search_sources.source(session, "a-source-channel", "message", session.get(Message, "source-race"))
        reference = search_sources.reference("owner", item)
    original = access.lock_channel_writes; observed = []
    def source_writer_wins_before_fence(session, channel):
        observed.append(channel)
        if channel == "a-source-channel" and observed.count(channel) == 1:
            # Adversarial interleaving between public-capability discovery and
            # the actual source fence. A fresh extraction must reject it.
            session.execute(update(Message).where(Message.id == "source-race").values(content=encrypt_message("Source changed by the winning writer", channel)))
        return original(session, channel)
    monkeypatch.setattr(access, "lock_channel_writes", source_writer_wins_before_fence)
    response = queue(ai, sources=[{"kind": "resource", "id": "source-race", "reference": reference}], disclose_cross_channel_sources=True)
    assert response.status_code == 409, response.text
    assert observed[:2] == ["a-source-channel", "native"]
    with Session(ai[1]) as session:
        assert not session.exec(select(AIRun)).all() and not session.exec(select(AIRunStep)).all()


def test_concurrent_worker_reservations_cannot_overspend_the_shared_remaining_budget(ai, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    profile(ai)
    first = queue(ai).json()
    second = queue(ai, request_id="competing-budget-reservation").json()
    entered = threading.Event(); release = threading.Event(); calls = []
    def held(*args, **kwargs):
        calls.append(True); entered.set()
        assert release.wait(15), "Owned provider fixture was not released"
        return answer()
    monkeypatch.setattr(ai_providers, "complete", held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(service.run_pending, ai[1], limit=1)
        assert entered.wait(15), "First worker did not reach the owned provider"
        try:
            with Session(ai[1]) as session:
                reserved = session.exec(select(AIUsage)).one()
                session.add(AIBudget(workspace_id="a", max_tokens=reserved.reserved_tokens, updated_by="owner")); session.commit()
            service.run_pending(ai[1], limit=1)
            assert len(calls) == 1 and current(ai, second["id"])["error"] == "budget_exhausted"
        finally:
            release.set()
        running.result(timeout=15)
    assert current(ai, first["id"])["status"] == "awaiting_review"
    with Session(ai[1]) as session:
        assert len(session.exec(select(AIUsage)).all()) == 1 and service.budget_status(session, "a")["used_tokens"] == 42


def test_shared_timeline_does_not_claim_complete_when_a_domain_window_has_more_real_runs(ai):
    from app.services.ai_execution_timeline import timeline
    profile(ai); assert queue(ai).status_code == 201
    assert queue(ai, request_id="another-real-timeline-run").status_code == 201
    with Session(ai[1]) as session:
        result = timeline(session, "a", "owner", limit=1)
        assert len(result["items"]) == 1 and result["complete"] is False


def test_canonical_rag_export_is_read_only_inside_the_callers_privacy_transaction(ai):
    from app.services import search_sources
    from sqlalchemy import event
    profile(ai)
    with Session(ai[1]) as session:
        item = search_sources.source(session, "native", "message", session.get(Message, "old-root"))
        capability = search_sources.reference("owner", item)
    assert queue(ai, sources=[{"kind": "resource", "id": "old-root", "reference": capability}]).status_code == 201
    statements = []
    def observe(connection, cursor, statement, parameters, context, many):
        statements.append(statement.lstrip().split(None, 1)[0].upper())
    event.listen(ai[1], "before_cursor_execute", observe)
    try:
        with Session(ai[1]) as session:
            assert len(service.export_actor(session, "owner", ["native"], workspace_id="a")["runs"]) == 1
            assert not session.new and not session.dirty and not session.deleted
            session.rollback()
    finally:
        event.remove(ai[1], "before_cursor_execute", observe)
    assert statements and set(statements) == {"SELECT"}
