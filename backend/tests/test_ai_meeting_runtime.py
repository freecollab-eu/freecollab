"""Real capture consent/clip publication feeding a deduplicated native AI job."""
from sqlmodel import Session, select

from app.api import clips, copilot
from app.models.account_security import AccountSecurity
from app.models.ai_runtime import AIRun, AIUsage
from app.models.file import File
from app.models.user import User
from app.services import ai_providers, ai_runtime
from app.services.auth import hash_password
from tests.test_ai_runtime import PASSWORD, answer
from tests.test_media_capture import team, setup, vote, control, native

PREFIX = "/workspaces/a/copilot/runtime"


def prepared_recording(team, monkeypatch):
    client, engine, tokens, meeting, member, path = setup(team)
    client.app.include_router(copilot.router)
    client.app.include_router(clips.router)
    monkeypatch.setattr(ai_runtime.copilot, "enabled", lambda: True)
    monkeypatch.setenv("MEETING_CAPTION_MODEL", "whisper-tiny")
    monkeypatch.setenv("MEETING_CAPTION_MODEL_BASE", "/models/")
    with Session(engine) as session:
        user = session.get(User, "owner"); user.password_hash = hash_password(PASSWORD); session.add(user)
        if not session.get(AccountSecurity, "owner"): session.add(AccountSecurity(user_id="owner"))
        session.commit()
    profile = client.put(PREFIX + "/profiles/meeting", headers=native(tokens["owner"]), json={
        "password": PASSWORD, "expected_revision": 0, "name": "Consented meeting model", "protocol": "openai",
        "endpoint": "https://meeting-model.example/v1/chat/completions", "model": "meeting-summary", "api_key": "synthetic-meeting-provider-key"})
    assert profile.status_code == 200, profile.text
    consent = client.post(PREFIX + "/profiles/meeting/consent", headers=native(tokens["owner"]), json={"enabled": True, "expected_revision": 1})
    assert consent.status_code == 200, consent.text
    response = client.post(path, headers=native(tokens["owner"]), json={"captions": True, "save_transcript": True, "caption_audience": "admitted",
        "language": "auto", "ai_profile_id": "meeting", "ai_profile_revision": 1, "ai_disclose": True})
    assert response.status_code == 201, response.text
    capture = response.json()["capture"]
    assert capture["ai"]["recipient"] == "https://meeting-model.example/v1/chat/completions"
    assert vote(client, tokens, path, capture).status_code == 200
    assert control(client, tokens, path, capture, "start").status_code == 200
    receipt = control(client, tokens, path, capture, "stop").json()["publication_receipt"]
    with Session(engine) as session:
        session.add(File(id="a" * 32, channel_id="shared", uploader_id="owner", filename="meeting.webm", content_type="audio/webm", size=100, storage_path="owned-fixture-not-opened")); session.commit()
    publication = client.post("/channels/shared/clips", headers=native(tokens["owner"]), json={"id": "meeting-clip", "file_id": "a" * 32,
        "title": "Reviewed meeting segment", "duration": 20, "transcript": "We agreed on the reviewed launch plan.", "publication_receipt": receipt})
    assert publication.status_code == 201, publication.text
    body = {"request_id": "meeting-first-client-request", "channel_id": "shared", "profile_id": "meeting", "expected_profile_revision": 1,
        "operation": "summarize", "prompt": "Summarize the consented recording", "sources": [{"kind": "clip", "id": "meeting-clip"}],
        "disclose_to_provider": True, "max_steps": 1, "max_tokens": 512,
        "disclosure_scope": {"kind": "meeting_recording", "id": capture["id"], "revision": capture["generation"]}}
    return client, engine, tokens, capture, body


def test_real_meeting_consent_publication_and_two_client_ids_produce_one_summary_call(team, monkeypatch):
    client, engine, tokens, capture, body = prepared_recording(team, monkeypatch)
    first = client.post(PREFIX + "/runs", headers=native(tokens["owner"]), json=body)
    assert first.status_code == 201, first.text
    again = client.post(PREFIX + "/runs", headers=native(tokens["owner"]), json={**body, "request_id": "different-client-request"})
    assert again.status_code == 201 and first.json()["id"] == again.json()["id"], again.text
    calls = []
    monkeypatch.setattr(ai_providers, "complete", lambda provider, messages, **kw: (calls.append(messages) or answer("Reviewed captured summary [S1]")))
    ai_runtime.run_pending(engine); ai_runtime.run_pending(engine)
    assert len(calls) == 1 and "reviewed launch plan" in str(calls)
    with Session(engine) as session:
        assert session.exec(select(AIRun)).one().status == "awaiting_review"
        assert session.exec(select(AIUsage)).one().charged_tokens == 42
    revoked = client.post("/channels/shared/meeting-captures/" + capture["id"] + "/ai/revoke", headers=native(tokens["member"]))
    assert revoked.status_code == 200, revoked.text
    run = client.get(PREFIX + "/runs/" + first.json()["id"], headers=native(tokens["owner"])).json()
    approved = client.post(PREFIX + "/runs/" + run["id"] + "/approve", headers=native(tokens["owner"]), json={"expected_revision": run["revision"], "proposal_id": "accept", "confirm": True})
    assert approved.status_code == 409, approved.text


def test_meeting_participant_can_revoke_queued_disclosure_before_any_provider_call(team, monkeypatch):
    client, engine, tokens, capture, body = prepared_recording(team, monkeypatch)
    queued = client.post(PREFIX + "/runs", headers=native(tokens["owner"]), json=body)
    assert queued.status_code == 201, queued.text
    assert client.post("/channels/shared/meeting-captures/" + capture["id"] + "/ai/revoke", headers=native(tokens["member"])).status_code == 200
    calls = []; monkeypatch.setattr(ai_providers, "complete", lambda *a, **k: calls.append(True))
    ai_runtime.run_pending(engine)
    assert not calls
    with Session(engine) as session:
        assert session.exec(select(AIRun)).one().status == "failed"
        assert not session.exec(select(AIUsage)).all()
