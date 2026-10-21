"""Adversarial gateway boundaries with an isolated, deterministic upstream."""
import httpx
import pytest
from sqlmodel import Session

from app.api import secure_messaging
from app.models.workspace import WorkspaceChannel, WorkspaceMember
from app.services.auth import revoke_token
from .test_secure_messaging import secure


def upstream(monkeypatch, payload, mutation=None):
    requests = []
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return False
        async def request(self, method, url, **kwargs):
            requests.append((method, url, kwargs))
            if mutation:
                mutation()
            return httpx.Response(200, json=payload)
    monkeypatch.setattr(secure_messaging.httpx, "AsyncClient", lambda **_: Client())
    return requests


@pytest.mark.parametrize("method,path", [
    ("POST", "/_matrix/client/v3/user_directory/search"),
    ("POST", "/_matrix/client/v3/join/%23unbound%3Alocal.test"),
    ("POST", "/_matrix/client/unstable/org.matrix.msc4140/delayed_events"),
    ("GET", "/_matrix/client/v3/rooms/%21bound%3Alocal.test/event/%252e%252e/messages"),
    ("PUT", "/_matrix/client/v3/rooms/%21bound%3Alocal.test/send/m.room.message/transaction"),
])
def test_unsupported_and_ambiguous_endpoints_never_reach_upstream(secure, monkeypatch, method, path):
    client, _, _, _, _ = secure
    calls = upstream(monkeypatch, {"must_not_escape": True})
    response = client.request(method, path, headers={"Authorization": "Bearer matrix-test-0"}, json={})
    assert response.status_code == 403
    assert calls == []


def test_plaintext_fields_cannot_hide_in_an_encrypted_event_envelope(secure, monkeypatch):
    client, _, _, _, _ = secure
    calls = upstream(monkeypatch, {})
    response = client.put("/_matrix/client/v3/rooms/%21bound%3Alocal.test/send/m.room.encrypted/transaction",
                          headers={"Authorization": "Bearer matrix-test-0"},
                          json={"algorithm": "m.megolm.v1.aes-sha2", "ciphertext": "opaque", "body": "Cleartext message"})
    assert response.status_code == 400 and calls == []


def test_inflight_room_response_is_withheld_after_workspace_revocation(secure, monkeypatch):
    client, engine, users, _, channel = secure
    def remove():
        with Session(engine) as session:
            workspace_id = session.get(WorkspaceChannel, channel).workspace_id
            session.delete(session.get(WorkspaceMember, (workspace_id, users[1]["user_id"])))
            session.commit()
    calls = upstream(monkeypatch, {"chunk": [{"ciphertext": "withheld-room-response"}]}, remove)
    response = client.get("/_matrix/client/v3/rooms/%21bound%3Alocal.test/messages", headers={"Authorization": "Bearer matrix-test-1"})
    assert len(calls) == 1
    assert response.status_code == 403 and "withheld-room-response" not in response.text


@pytest.mark.parametrize("revoke", [False, True])
def test_sync_filters_unknown_and_newly_revoked_rooms(secure, monkeypatch, revoke):
    client, engine, users, _, channel = secure
    def remove():
        if revoke:
            with Session(engine) as session:
                workspace_id = session.get(WorkspaceChannel, channel).workspace_id
                session.delete(session.get(WorkspaceMember, (workspace_id, users[1]["user_id"])))
                session.commit()
    payload = {"next_batch": "opaque-next", "rooms": {
        section: {"!bound:local.test": {"timeline": {"events": []}}, "!unknown:local.test": {"private": "unknown-room-content"}}
        for section in ("join", "invite", "leave", "knock")
    }}
    calls = upstream(monkeypatch, payload, remove)
    response = client.get("/_matrix/client/v3/sync", headers={"Authorization": "Bearer matrix-test-1"})
    assert response.status_code == 200 and len(calls) == 1
    assert "unknown-room-content" not in response.text
    for section in ("join", "invite", "leave", "knock"):
        assert set(response.json()["rooms"][section]) == (set() if revoke else {"!bound:local.test"})


def test_logout_during_upstream_request_withholds_its_result(secure, monkeypatch):
    client, engine, users, _, _ = secure
    def logout():
        with Session(engine) as session:
            revoke_token(users[0]["access_token"], session)
    calls = upstream(monkeypatch, {"next_batch": "withheld-after-logout", "rooms": {}}, logout)
    response = client.get("/_matrix/client/v3/sync", headers={"Authorization": "Bearer matrix-test-0"})
    assert response.status_code == 401 and len(calls) == 1
    assert "withheld-after-logout" not in response.text
