"""Explicit provider grants, durable uncertainty, encrypted references and revocation."""
import base64
from datetime import datetime, timedelta
import json
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import auth, external_meetings as routes, integrations_content
from app.config import settings
from app.database import get_session
from app.models.account_security import AccountSecurity
from app.models.channel import Channel, ChannelMember
from app.models.external_meetings import ExternalMeeting, ExternalMeetingConnection, ExternalMeetingOAuthAttempt
from app.models.integration_content import IntegrationContent, IntegrationContentRevision
from app.models.remote_sync import RemoteSyncConnection
from app.models.secure_channel import SecureChannel
from app.models.session import ActiveSession
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.services import external_meetings as service, external_meeting_providers as providers
from app.services.auth import create_token, hash_password, token_digest

PASSWORD = "private-provider-fixture-password"
FIELDS = {"title": "Partner planning", "starts_at": "2026-12-01T10:00:00Z", "ends_at": "2026-12-01T11:00:00Z", "timezone": "Europe/Stockholm"}
SCOPES = {"google_meet": "https://www.googleapis.com/auth/calendar.events", "microsoft_teams": "Calendars.ReadWrite offline_access", "zoom": " ".join(sorted(providers.ZOOM_SCOPES))}
CONFIGS = {"google_meet": {"client_id": "fixture-google-app", "calendar_id": "selected-calendar"},
    "microsoft_teams": {"client_id": "fixture-microsoft-app", "calendar_id": "selected-calendar", "tenant_id": "11111111-2222-3333-4444-555555555555"}, "zoom": {"client_id": "fixture-zoom-app"}}


class Provider:
    def __init__(self): self.creates, self.deletes, self.values, self.uncertain, self.before_write = [], [], {}, False, None
    def test(self, *args): return None
    def create(self, provider, public, secret, identity, fields):
        if self.before_write: self.before_write()
        self.creates.append(identity)
        result = {"id": "remote-" + identity, "etag": '"fixture-1"', "title": fields["title"], "start": fields["starts_at"], "end": fields["ends_at"], "deleted": False,
            "join_url": {"google_meet": "https://meet.google.com/abc-defg-hij", "microsoft_teams": "https://teams.microsoft.com/l/meetup-join/fixture?context=fixture", "zoom": "https://customer.zoom.us/j/12345678901?pwd=fixture"}[provider]}
        self.values[result["id"]] = result
        if self.uncertain: raise providers.ProviderError("provider_timeout")
        return result.copy()
    def read(self, provider, public, secret, remote_id):
        if remote_id not in self.values: raise providers.ProviderError("not_found", status=404)
        return self.values[remote_id].copy()
    def reconcile(self, provider, public, secret, identity): return self.read(provider, public, secret, "remote-" + identity)
    def cancel(self, provider, public, secret, remote_id, etag): self.deletes.append(remote_id); self.values.pop(remote_id, None)


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "external_meetings_enabled", True, raising=False)
    monkeypatch.setattr(settings, "public_app_url", "https://collab.example")
    monkeypatch.setattr(settings, "account_security_key", SecretStr(base64.b64encode(b"e" * 32).decode()))
    monkeypatch.setattr(settings, "message_encryption_keys", {"fixture": SecretStr(base64.b64encode(b"m" * 32).decode())})
    monkeypatch.setattr(settings, "message_encryption_active_key_id", "fixture")
    engine = create_engine("sqlite:///" + str(tmp_path / "external.db"), connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([User(id=name, name=name.title(), email=name + "@example.com", password_hash=hash_password(PASSWORD)) for name in ["owner", "member", "outsider", "other"]])
        session.add_all([Workspace(id="team", name="Team"), Workspace(id="other", name="Other"), Channel(id="room", name="Room"), Channel(id="elsewhere", name="Elsewhere")]); session.commit()
        session.add_all([WorkspaceChannel(channel_id="room", workspace_id="team"), WorkspaceChannel(channel_id="elsewhere", workspace_id="other")])
        for name in ["owner", "member", "other"]:
            session.add(WorkspaceMember(workspace_id="team", user_id=name, role="owner" if name == "owner" else "member"))
            session.add(ChannelMember(channel_id="room", user_id=name, role="admin" if name == "owner" else "member"))
        session.add_all([WorkspaceMember(workspace_id="other", user_id="owner", role="owner"), ChannelMember(channel_id="elsewhere", user_id="owner", role="admin")]); session.commit()
        tokens = {name: create_token(name, session) for name in ["owner", "member", "outsider", "other"]}
    app = FastAPI(); app.include_router(routes.router); app.include_router(routes.oauth_router); app.include_router(integrations_content.router); app.include_router(auth.router)
    def sessions():
        with Session(engine) as session: yield session
    app.dependency_overrides[get_session] = sessions
    remote = Provider()
    for method in ["test", "create", "read", "reconcile", "cancel"]: monkeypatch.setattr(providers, method, getattr(remote, method))
    with TestClient(app, base_url="https://collab.example", follow_redirects=False) as client:
        yield client, engine, tokens, remote
    engine.dispose()


def headers(team, user="owner"): return {"Authorization": "Bearer " + team[2][user]}
def draft(provider="google_meet", **changes):
    return {"name": "Customer meetings", "provider": provider, "channel_id": "room", "config": CONFIGS[provider],
        "client_secret": "customer-owned-private-app-secret", "password": PASSWORD, "provider_disclosure": True, **changes}
def connect(team, provider="google_meet"):
    client, engine, _, _ = team
    response = client.post("/workspaces/team/external-meetings/connections", headers=headers(team), json=draft(provider))
    assert response.status_code == 201, response.text
    row = response.json()
    with Session(engine) as session:
        connection = session.get(ExternalMeetingConnection, row["id"])
        service.save_credentials(connection, {"access_token": "provider-private-access-token", "refresh_token": "provider-private-refresh-token", "client_secret": "customer-owned-private-app-secret", "scope": SCOPES[provider]})
        session.add(connection); session.commit()
    tested = client.post(f'/workspaces/team/external-meetings/connections/{row["id"]}/test', headers=headers(team), json={"password": PASSWORD})
    assert tested.status_code == 200, tested.text
    return tested.json()
def create(team, connection, **changes):
    return team[0].post("/channels/room/external-meetings", headers=headers(team, "member"), json={"request_id": "unique-explicit-meeting-123", "connection_id": connection["id"], "connection_revision": connection["revision"], "fields": FIELDS, "provider_disclosure": True, **changes})


@pytest.mark.parametrize("provider", ["google_meet", "microsoft_teams", "zoom"])
def test_customer_provider_create_persists_native_calendar_and_idempotent_replay(team, provider):
    client, engine, _, remote = team
    connection = connect(team, provider)
    created = create(team, connection); assert created.status_code == 201, created.text
    record = created.json(); assert record["state"] == "ready" and record["join_url"].startswith("https://")
    assert create(team, connection).json()["id"] == record["id"] and len(remote.creates) == 1
    changed = dict(FIELDS, title="Changed title")
    assert create(team, connection, fields=changed).status_code == 409
    calendar = client.get("/channels/room/integrations/calendar", headers=headers(team, "other")).json()
    assert len(calendar) == 1 and calendar[0]["fields"]["location"] == record["join_url"]
    assert calendar[0]["fields"]["title"] == FIELDS["title"]
    with Session(engine) as session:
        row = session.get(ExternalMeeting, record["id"])
        assert FIELDS["title"] not in row.encrypted_request and "https://" not in row.encrypted_result
        secret = session.get(ExternalMeetingConnection, connection["id"]).encrypted_credentials
        assert "provider-private" not in secret and "customer-owned" not in secret
    output = client.get("/workspaces/team/external-meetings/connections", headers=headers(team)).text
    assert "provider-private" not in output and "customer-owned" not in output and "encrypted_credentials" not in output
    assert created.headers["cache-control"] == "no-store"


def test_provider_actions_require_disclosure_current_scope_and_tested_grant(team):
    client, _, _, remote = team
    connection = connect(team)
    assert create(team, connection, provider_disclosure=False).status_code == 422
    assert not remote.creates
    assert client.post("/workspaces/team/external-meetings/connections", headers=headers(team, "member"), json=draft()).status_code == 403
    assert client.get("/channels/room/external-meetings", headers=headers(team, "outsider")).status_code == 403
    assert client.post("/channels/elsewhere/external-meetings", headers=headers(team), json={"request_id": "cross-workspace-request", "connection_id": connection["id"], "connection_revision": connection["revision"], "fields": FIELDS, "provider_disclosure": True}).status_code == 404
    assert client.post(f'/workspaces/other/external-meetings/connections/{connection["id"]}/disconnect', headers=headers(team), json={"password": PASSWORD}).status_code == 404


@pytest.mark.parametrize("change", ["connection", "owner_block", "owner_role", "owner_channel", "member_session", "member_block", "member_channel", "member_workspace", "member_version"])
def test_current_authority_is_rechecked_before_provider_dispatch(team, change):
    client, engine, tokens, remote = team
    connection = connect(team)
    with Session(engine) as session:
        if change == "connection":
            row = session.get(ExternalMeetingConnection, connection["id"]); row.enabled = False; session.add(row)
        elif change in {"owner_block", "member_block"}:
            row = session.get(User, change.split("_")[0]); row.is_blocked = True; session.add(row)
        elif change == "owner_role":
            row = session.get(WorkspaceMember, ("team", "owner")); row.role = "member"; session.add(row)
        elif change in {"owner_channel", "member_channel"}: session.delete(session.get(ChannelMember, ("room", change.split("_")[0])))
        elif change == "member_workspace": session.delete(session.get(WorkspaceMember, ("team", "member")))
        elif change == "member_version":
            row = session.get(AccountSecurity, "member"); row.auth_version += 1; session.add(row)
        else:
            row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(tokens["member"]))).one(); row.revoked_at = datetime.utcnow(); session.add(row)
        session.commit()
    assert create(team, connection).status_code in {401, 403, 409}
    assert not remote.creates


def test_uncertain_provider_create_is_recovered_by_reads_without_duplicate_post(team):
    client, _, _, remote = team
    connection = connect(team, "zoom"); remote.uncertain = True
    assert create(team, connection).status_code == 422
    assert create(team, connection).status_code == 409 and len(remote.creates) == 1
    row = client.get("/channels/room/external-meetings", headers=headers(team, "member")).json()["meetings"][0]
    recovered = client.post(f'/channels/room/external-meetings/{row["id"]}/recover', headers=headers(team, "member"), json={"expected_revision": row["revision"], "provider_disclosure": True})
    assert recovered.status_code == 200 and recovered.json()["state"] == "ready" and len(remote.creates) == 1


def test_cancel_is_conditional_and_calendar_tombstone_is_idempotent(team):
    client, _, _, remote = team
    connection = connect(team); record = create(team, connection).json()
    endpoint = f'/channels/room/external-meetings/{record["id"]}/cancel'
    body = {"expected_revision": record["revision"], "expected_calendar_revision": record["calendar_revision"], "provider_disclosure": True}
    assert client.post(endpoint, headers=headers(team, "other"), json=body).status_code == 403
    assert client.post(endpoint, headers=headers(team, "member"), json={**body, "expected_calendar_revision": 99}).status_code == 409
    cancelled = client.post(endpoint, headers=headers(team, "member"), json=body); assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled" and len(remote.deletes) == 1
    assert client.get("/channels/room/integrations/calendar", headers=headers(team, "member")).json() == []
    assert client.post(endpoint, headers=headers(team, "member"), json={**body, "expected_revision": cancelled.json()["revision"]}).status_code == 200
    assert len(remote.deletes) == 1


def test_provider_changed_precondition_never_deletes_or_overwrites_calendar(team):
    client, _, _, remote = team
    connection = connect(team); record = create(team, connection).json()
    remote.values[next(iter(remote.values))]["title"] = "Provider-side edit"
    response = client.post(f'/channels/room/external-meetings/{record["id"]}/cancel', headers=headers(team, "member"), json={"expected_revision": record["revision"], "expected_calendar_revision": record["calendar_revision"], "provider_disclosure": True})
    assert response.status_code == 409 and not remote.deletes
    assert client.get("/channels/room/integrations/calendar", headers=headers(team, "member")).json()[0]["fields"]["title"] == FIELDS["title"]


def test_secure_channel_stores_no_plaintext_request_link_or_server_calendar(team, monkeypatch):
    client, engine, _, _ = team
    connection = connect(team)
    with Session(engine) as session:
        session.add(SecureChannel(channel_id="room", matrix_room_id="!private:fixture", status="active", created_by="owner")); session.commit()
    created = create(team, connection); assert created.status_code == 201, created.text
    record = created.json(); assert record["state"] == "awaiting_secure_calendar" and record["join_url"]
    with Session(engine) as session:
        row = session.get(ExternalMeeting, record["id"])
        assert row.encrypted_request is None
        stored = service.result(row); assert set(stored) == {"id", "etag"}
        assert session.exec(select(IntegrationContent)).all() == []
    listed = client.get("/channels/room/external-meetings", headers=headers(team, "other")).json()["meetings"][0]
    assert "fields" not in listed and "join_url" not in listed and "provider_fields" not in listed
    identity = "meeting_" + record["id"]
    with Session(engine) as session:
        session.add(IntegrationContent(id=identity, channel_id="room", kind="calendar", created_by="member")); session.flush()
        session.add(IntegrationContentRevision(item_id=identity, revision=1, created_by="member", content_mode="matrix", matrix_event_id="$verified-client-ciphertext")); session.commit()
    monkeypatch.setattr(routes, "validate_secure_event", lambda *a: {"type": "m.room.encrypted"})
    attached = client.post(f'/channels/room/external-meetings/{record["id"]}/calendar-link', headers=headers(team, "member"), json={"expected_revision": record["revision"], "calendar_item_id": identity, "calendar_revision": 1, "matrix_event_id": "$verified-client-ciphertext"})
    assert attached.status_code == 200 and attached.json()["state"] == "ready"
    assert "join_url" not in attached.json()


def test_secure_transition_hides_legacy_external_details_and_fences_provider_actions(team):
    client, engine, _, remote = team
    connection = connect(team); record = create(team, connection).json()
    with Session(engine) as session:
        session.add(SecureChannel(channel_id="room", matrix_room_id="!private:fixture", status="active", created_by="owner")); session.commit()
    listed = client.get("/channels/room/external-meetings", headers=headers(team, "member")).json()["meetings"][0]
    assert "fields" not in listed and "join_url" not in listed
    assert client.post(f'/channels/room/external-meetings/{record["id"]}/cancel', headers=headers(team, "member"), json={"expected_revision": record["revision"], "expected_calendar_revision": record["calendar_revision"], "provider_disclosure": True}).status_code == 409
    assert not remote.deletes


def test_calendar_grant_reuse_requires_correct_scopes_and_current_revision(team):
    client, engine, _, remote = team
    with Session(engine) as session:
        source = RemoteSyncConnection(id="calendar-source", workspace_id="team", channel_id="room", created_by="owner", grant_user_id="owner", name="Selected calendar", provider="google", kind="calendar", config_json=json.dumps({**CONFIGS["google_meet"], "kind": "calendar"}), status="connected")
        service.remote_sync.save_credentials(source, {"access_token": "source-private-token", "scope": "https://www.googleapis.com/auth/drive"}); session.add(source); session.commit()
    data = draft(config={}, client_secret=None, source_connection_id="calendar-source")
    assert client.post("/workspaces/team/external-meetings/connections", headers=headers(team), json=data).status_code == 422
    with Session(engine) as session:
        source = session.get(RemoteSyncConnection, "calendar-source"); service.remote_sync.save_credentials(source, {"access_token": "source-private-token", "scope": SCOPES["google_meet"]}); session.add(source); session.commit()
    connection = client.post("/workspaces/team/external-meetings/connections", headers=headers(team), json=data).json()
    assert client.post(f'/workspaces/team/external-meetings/connections/{connection["id"]}/test', headers=headers(team), json={"password": PASSWORD}).status_code == 200
    with Session(engine) as session:
        source = session.get(RemoteSyncConnection, "calendar-source"); source.revision += 1; session.add(source); session.commit()
    assert create(team, connection).status_code == 409 and not remote.creates


def test_oauth_pkce_browser_native_session_and_single_use_state_bindings(team, monkeypatch):
    client, engine, tokens, _ = team
    connection = client.post("/workspaces/team/external-meetings/connections", headers=headers(team), json=draft()).json()
    start = client.post(f'/workspaces/team/external-meetings/connections/{connection["id"]}/oauth/start', headers=headers(team), json={"password": PASSWORD})
    assert start.status_code == 200, start.text
    query = parse_qs(urlsplit(start.json()["authorization_url"]).query); state = query["state"][0]
    assert query["code_challenge_method"] == ["S256"] and "calendar.events" in query["scope"][0]
    with Session(engine) as session:
        attempt = session.get(ExternalMeetingOAuthAttempt, token_digest(state)); assert attempt.state_hash != state and "customer-owned" not in attempt.encrypted_verifier
    calls = []
    monkeypatch.setattr(providers, "exchange_code", lambda *args: calls.append(args) or {"access_token": "oauth-provider-secret", "refresh_token": "oauth-refresh-secret", "scope": SCOPES["google_meet"]})
    response = client.get("/external-meetings/oauth/callback", params={"state": state, "code": "private-fixture-code"})
    assert response.status_code == 303 and "external_meeting_connected=" in response.headers["location"] and len(calls) == 1
    assert "private-fixture-code" not in response.headers["location"] and "oauth-provider-secret" not in response.headers["location"]
    assert "external_meeting_error=" in client.get("/external-meetings/oauth/callback", params={"state": state, "code": "replay"}).headers["location"]
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["browser", "revoked", "version", "role", "connection"])
def test_oauth_callback_rejects_changed_native_or_provider_authority(team, monkeypatch, change):
    client, engine, tokens, _ = team
    connection = client.post("/workspaces/team/external-meetings/connections", headers=headers(team), json=draft()).json()
    start = client.post(f'/workspaces/team/external-meetings/connections/{connection["id"]}/oauth/start', headers=headers(team), json={"password": PASSWORD}).json()
    state = parse_qs(urlsplit(start["authorization_url"]).query)["state"][0]
    with Session(engine) as session:
        if change == "browser": client.cookies.clear()
        elif change == "revoked":
            row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(tokens["owner"]))).one(); row.revoked_at = datetime.utcnow(); session.add(row)
        elif change == "version":
            row = session.get(AccountSecurity, "owner"); row.auth_version += 1; session.add(row)
        elif change == "role":
            row = session.get(WorkspaceMember, ("team", "owner")); row.role = "member"; session.add(row)
        else:
            row = session.get(ExternalMeetingConnection, connection["id"]); row.revision += 1; session.add(row)
        session.commit()
    monkeypatch.setattr(providers, "exchange_code", lambda *_: pytest.fail("Revoked OAuth reached provider"))
    assert "external_meeting_error=" in client.get("/external-meetings/oauth/callback", params={"state": state, "code": "private-fixture-code"}).headers["location"]


@pytest.mark.parametrize("provider,link", [("google_meet", "https://meet.google.com.evil.example/abc-defg-hij"), ("microsoft_teams", "https://127.0.0.1/l/meetup-join/private"), ("zoom", "https://customer.zoom.us.evil.example/j/123456789"), ("zoom", "https://user:password@customer.zoom.us/j/123456789"), ("google_meet", "https://meet.google.com/abc-defg-hij?access_token=private")])
def test_provider_links_reject_spoofing_credentials_and_unsafe_destinations(provider, link):
    with pytest.raises(providers.ProviderError): providers.join_url(provider, link)


def test_create_consent_is_bound_to_the_displayed_connection_revision(team):
    _, engine, _, remote = team
    connection = connect(team)
    with Session(engine) as session:
        row = session.get(ExternalMeetingConnection, connection["id"])
        row.revision += 1; row.tested_revision = row.revision
        session.add(row); session.commit()
    assert create(team, connection).status_code == 409
    assert not remote.creates


def test_native_session_revoked_while_waiting_for_grant_lock_cannot_dispatch(team, monkeypatch):
    _, engine, tokens, remote = team
    connection = connect(team); original = service.connection
    def revoke_after_lock(session, workspace_id, identifier, *, lock=False):
        row = original(session, workspace_id, identifier, lock=lock)
        if lock:
            active = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(tokens["member"]))).one()
            active.revoked_at = datetime.utcnow(); session.add(active); session.commit()
        return row
    monkeypatch.setattr(service, "connection", revoke_after_lock)
    assert create(team, connection).status_code == 401 and not remote.creates


def test_connection_revoked_during_provider_write_does_not_publish_result_or_calendar(team, monkeypatch):
    _, engine, _, remote = team
    connection = connect(team); original = service.call
    def revoke_after_provider(session, row, actor_id, function, *args):
        result = original(session, row, actor_id, function, *args)
        row.enabled = False; row.revision += 1; session.add(row); session.commit()
        return result
    monkeypatch.setattr(service, "call", revoke_after_provider)
    assert create(team, connection).status_code == 409 and len(remote.creates) == 1
    with Session(engine) as session:
        row = session.exec(select(ExternalMeeting)).one()
        assert row.state == "uncertain" and row.encrypted_result is None
        assert session.exec(select(IntegrationContent)).all() == []


def test_oauth_session_revoked_during_token_exchange_does_not_save_delegated_tokens(team, monkeypatch):
    client, engine, tokens, _ = team
    connection = client.post("/workspaces/team/external-meetings/connections", headers=headers(team), json=draft()).json()
    start = client.post(f'/workspaces/team/external-meetings/connections/{connection["id"]}/oauth/start', headers=headers(team), json={"password": PASSWORD}).json()
    state = parse_qs(urlsplit(start["authorization_url"]).query)["state"][0]
    def exchange(*args):
        with Session(engine) as session:
            active = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(tokens["owner"]))).one()
            active.revoked_at = datetime.utcnow(); session.add(active); session.commit()
        return {"access_token": "never-persist-this-revoked-grant", "scope": SCOPES["google_meet"]}
    monkeypatch.setattr(providers, "exchange_code", exchange)
    assert "external_meeting_error=" in client.get("/external-meetings/oauth/callback", params={"state": state, "code": "private-fixture-code"}).headers["location"]
    with Session(engine) as session:
        assert "access_token" not in service.credentials(session.get(ExternalMeetingConnection, connection["id"]))


def test_workspace_scoped_login_cannot_dispatch_to_another_workspace(team):
    client, engine, _, remote = team
    connection = connect(team)
    with Session(engine) as session:
        session.add(WorkspaceOIDCProvider(workspace_id="other", name="Other IdP", issuer_url="https://other-idp.example", client_id="fixture-app", created_by="owner", enabled=True, revision=1)); session.commit()
        token = create_token("owner", session, auth_methods=["sso"], workspace_scope={"workspace_id": "other", "provider_revision": 1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(token), workspace_id="other", provider_revision=1)); session.commit()
    response = client.post("/channels/room/external-meetings", headers={"Authorization": "Bearer " + token}, json={"request_id": "scoped-cross-workspace-request", "connection_id": connection["id"], "connection_revision": connection["revision"], "fields": FIELDS, "provider_disclosure": True})
    assert response.status_code == 403 and not remote.creates


def test_provider_time_offsets_normalize_for_stable_conflict_checks():
    raw = {"id": "provider-event", "etag": '"stable"', "summary": "Planning", "start": {"dateTime": "2026-12-01T11:00:00+01:00"}, "end": {"dateTime": "2026-12-01T12:00:00+01:00"}, "hangoutLink": "https://meet.google.com/abc-defg-hij"}
    first = providers._normal("google_meet", raw)
    raw["start"]["dateTime"], raw["end"]["dateTime"] = "2026-12-01T10:00:00Z", "2026-12-01T11:00:00Z"
    assert providers.fingerprint(first) == providers.fingerprint(providers._normal("google_meet", raw))
