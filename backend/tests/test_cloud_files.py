"""Picker imports retain current native grants, exact revisions and opaque E2EE refs."""
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlmodel import Session, select

from app import main as application  # Register the complete released model graph.
from app.api import cloud_files as api
from app.models.channel import ChannelMember
from app.models.cloud_files import CloudFileConnection, CloudFileImport, CloudFileLink
from app.models.file import File
from app.models.file_version import FileDocument, FileVersion
from app.models.secure_channel import SecureChannel
from app.models.session import ActiveSession
from app.models.user import User
from app.models.workspace import WorkspaceMember
from app.services import cloud_files as service, cloud_file_providers as providers
from app.services.auth import token_digest
from tests.test_remote_sync import team, PASSWORD

BASE = "/workspaces/a/channels/room/cloud-files"
SELECTION = {"connection_revision": 1, "request_id": "selected-one", "file_id": "id:one", "parent_id": "/chosen", "etag": "rev1", "consent": True}


@pytest.fixture
def picker(team, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "cloud_files_enabled", True)
    team.client.app.include_router(api.router); team.client.app.include_router(api.oauth_router)
    monkeypatch.setattr(service, "storage_service", team.storage)
    row = CloudFileConnection(id="one", workspace_id="a", channel_id="room", created_by="owner", grant_user_id="owner",
        name="Customer Dropbox", provider="dropbox", config_json=json.dumps({"client_id": "customer", "folder_id": "/chosen"}), status="connected")
    service.save_credentials(row, {"access_token": "private-access-token", "refresh_token": "private-refresh-token", "client_secret": "private-app-secret"})
    with Session(team.engine) as session: session.add(row); session.commit()
    state = SimpleNamespace(before=None, calls=0, revision="rev1")
    def browse(*args):
        state.calls += 1
        if state.before: state.before()
        return {"folder_id": "/chosen", "items": [{"id": "id:one", "name": "notes.txt", "parent_id": "/chosen", "etag": state.revision,
            "mime_type": "text/plain", "size": 12, "folder": False}], "cursor": None}
    def read(provider, config, secret, identity, parent, etag):
        state.calls += 1
        if state.before: state.before()
        if etag != state.revision: raise providers.ProviderError("conflict", status=412)
        return {"id": identity, "name": "notes.txt", "content": b"actual cloud bytes", "etag": etag, "mime_type": "text/plain"}
    monkeypatch.setattr(providers, "browse", browse); monkeypatch.setattr(providers, "read_file", read)
    team.state = state
    return team


def post(picker, route, body=None, who="owner"):
    return picker.client.post(BASE + route, json={**({"connection_revision": 1} if route.endswith("/browse") else {}), **(body or {})}, headers=picker.headers[who])


def test_native_selected_import_is_immutable_versioned_and_retry_safe(picker):
    result = post(picker, "/connections/cloud:one/browse")
    assert result.status_code == 200 and result.json()["items"][0]["etag"] == "rev1"
    first = post(picker, "/connections/cloud:one/import", SELECTION)
    assert first.status_code == 200, first.text
    info = first.json(); assert info["version_number"] == 1
    assert picker.storage.get_file_path(info["id"]).read_bytes() == b"actual cloud bytes"
    calls = picker.state.calls
    retry = post(picker, "/connections/cloud:one/import", SELECTION)
    assert retry.json()["id"] == info["id"] and picker.state.calls == calls
    reused = post(picker, "/connections/cloud:one/import", {**SELECTION, "etag": "other"})
    assert reused.status_code == 409
    newer = post(picker, "/connections/cloud:one/import", {**SELECTION, "request_id": "second", "document_id": info["document_id"], "expected_version": 1})
    assert newer.status_code == 200 and newer.json()["version_number"] == 2
    assert newer.json()["id"] != info["id"] and picker.storage.get_file_path(info["id"]).read_bytes() == b"actual cloud bytes"
    stale = post(picker, "/connections/cloud:one/import", {**SELECTION, "request_id": "stale", "document_id": info["document_id"], "expected_version": 1})
    assert stale.status_code == 409
    with Session(picker.engine) as session:
        assert len(session.exec(select(CloudFileImport)).all()) == 2
        assert len(session.exec(select(File)).all()) == 2
        assert len(session.exec(select(FileVersion)).all()) == 2
        assert "private-access-token" not in session.get(CloudFileConnection, "one").encrypted_credentials


@pytest.mark.parametrize("who", ["member", "private-admin", "outsider"])
def test_picker_requires_current_workspace_and_private_channel_admin(picker, who):
    for route, body in (("/connections/cloud:one/browse", {}), ("/connections/cloud:one/import", SELECTION)):
        response = post(picker, route, body, who)
        assert response.status_code == 403
    assert picker.state.calls == 0


@pytest.mark.parametrize("change", ["member", "workspace", "blocked", "session", "connection", "secure"])
def test_revocation_while_provider_is_awaited_discards_both_bytes_and_metadata(picker, change):
    def revoke():
        with Session(picker.engine) as session:
            if change == "member": session.delete(session.get(ChannelMember, ("room", "owner")))
            elif change == "workspace": session.delete(session.get(WorkspaceMember, ("a", "owner")))
            elif change == "blocked":
                row = session.get(User, "owner"); row.is_blocked = True; session.add(row)
            elif change == "session":
                from datetime import datetime
                row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(picker.tokens["owner"]))).one(); row.revoked_at = datetime.utcnow(); session.add(row)
            elif change == "connection":
                row = session.get(CloudFileConnection, "one"); row.revision += 1; session.add(row)
            else: session.add(SecureChannel(channel_id="room", created_by="owner", status="preparing"))
            session.commit()
    picker.state.before = revoke
    response = post(picker, "/connections/cloud:one/import", SELECTION)
    assert response.status_code in {401, 403, 409}, response.text
    assert "actual cloud bytes" not in response.text
    with Session(picker.engine) as session:
        assert not session.exec(select(File)).all() and not session.exec(select(CloudFileImport)).all()
    assert not list(picker.storage.storage_path.iterdir())


def test_secure_and_preparing_channels_never_make_provider_requests(picker):
    with Session(picker.engine) as session: session.add(SecureChannel(channel_id="room", created_by="owner", status="preparing")); session.commit()
    for route, body in (("/connections/cloud:one/browse", {}), ("/connections/cloud:one/import", SELECTION)):
        assert post(picker, route, body).status_code == 409
    assert picker.state.calls == 0
    listed = picker.client.get(BASE + "/connections", headers=picker.headers["owner"]).json()
    assert listed["secure"] is True and listed["connections"] == []
    plaintext = post(picker, "/links", {"id": "link", "fields": {"provider": "google", "title": "Private", "url": "https://drive.google.com/file/d/one/view", "external_access_acknowledged": True}})
    assert plaintext.status_code == 409


def test_exact_provider_revision_is_required_before_native_import(picker):
    response = post(picker, "/connections/cloud:one/import", {**SELECTION, "etag": "rev0"})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "conflict"
    assert not list(picker.storage.storage_path.iterdir())


def test_native_link_metadata_is_encrypted_and_explicit_external_access(picker):
    fields = {"provider": "dropbox", "title": "Private external file", "url": "https://www.dropbox.com/scl/fi/one/notes.txt?rlkey=chosen-share", "external_access_acknowledged": True}
    response = post(picker, "/links", {"id": "link", "fields": fields}, "member")
    assert response.status_code == 200 and response.json()["fields"] == fields
    with Session(picker.engine) as session:
        stored = session.get(CloudFileLink, "link").encrypted_payload
        assert fields["title"] not in stored and fields["url"] not in stored
    listed = picker.client.get(BASE + "/links", headers=picker.headers["member"])
    assert listed.status_code == 200 and "no-store" in listed.headers["cache-control"]
    assert listed.json()["items"][0]["fields"] == fields
    assert post(picker, "/links", {"id": "unacknowledged", "fields": {**fields, "external_access_acknowledged": False}}).status_code == 422
    assert post(picker, "/links", {"id": "oauth-secret", "fields": {**fields, "url": fields["url"] + "&access_token=secret"}}).status_code == 422
    assert picker.client.delete(BASE + "/links/link", headers=picker.headers["member"]).status_code == 200


@pytest.mark.parametrize("provider,url", [("google", "https://drive.google.com.evil.example/file/d/one"), ("box", "https://app.box.com/oauth2/authorize"),
    ("microsoft", "https://evilsharepoint.com/file"), ("dropbox", "http://www.dropbox.com/s/one/file"), ("dropbox", "https://user:pass@www.dropbox.com/s/one/file")])
def test_link_allowlist_rejects_deceptive_or_credential_urls(provider, url):
    with pytest.raises(ValueError): service.validate_link(provider, url)


def test_secure_links_store_only_verified_opaque_sender_channel_events(picker, monkeypatch):
    with Session(picker.engine) as session: session.add(SecureChannel(channel_id="room", created_by="owner", matrix_room_id="!room:fixture", status="active")); session.commit()
    checked = []
    monkeypatch.setattr(api, "validate_secure_event", lambda session, channel, event, actor: checked.append((channel, event, actor)))
    result = post(picker, "/links", {"id": "opaque", "matrix_event_id": "$encrypted"}, "member")
    assert result.status_code == 200 and checked == [("room", "$encrypted", "member")]
    assert "fields" not in result.json()
    with Session(picker.engine) as session:
        row = session.get(CloudFileLink, "opaque")
        assert row.encrypted_payload is None and row.matrix_event_id == "$encrypted"
    def reject(*args):
        from fastapi import HTTPException
        raise HTTPException(400, "Wrong sender or channel")
    monkeypatch.setattr(api, "validate_secure_event", reject)
    assert post(picker, "/links", {"id": "wrong", "matrix_event_id": "$foreign"}).status_code == 400


def test_customer_oauth_pkce_single_use_browser_and_session_binding(picker, monkeypatch):
    monkeypatch.setattr(providers, "exchange_code", lambda *args: {"access_token": "new-private-token", "refresh_token": "new-private-refresh"})
    start = post(picker, "/connections/one/oauth/start", {"password": PASSWORD})
    assert start.status_code == 200, start.text
    query = parse_qs(urlsplit(start.json()["authorization_url"]).query)
    assert query["code_challenge_method"] == ["S256"] and query["scope"] == ["files.metadata.read files.content.read"]
    assert "private-app-secret" not in start.text and "code_verifier" not in start.text
    callback = picker.client.get("/cloud-files/oauth/callback", params={"state": query["state"][0], "code": "fixture-code"}, follow_redirects=False)
    assert "cloud_files_connected=one" in callback.headers["location"]
    replay = picker.client.get("/cloud-files/oauth/callback", params={"state": query["state"][0], "code": "fixture-code"}, follow_redirects=False)
    assert "cloud_files_error" in replay.headers["location"]
    with Session(picker.engine) as session:
        row = session.get(CloudFileConnection, "one")
        assert service.credentials(row)["access_token"] == "new-private-token" and "new-private-token" not in row.encrypted_credentials


def test_feature_flag_blocks_all_picker_routes_before_provider_io(picker, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "cloud_files_enabled", False)
    assert post(picker, "/connections/cloud:one/browse").status_code == 404
    assert picker.client.get(BASE + "/links", headers=picker.headers["owner"]).status_code == 404
    assert picker.state.calls == 0


def test_erased_import_receipt_is_a_tombstone_and_never_resurrects_bytes(picker):
    imported = post(picker, "/connections/cloud:one/import", SELECTION)
    assert imported.status_code == 200
    with Session(picker.engine) as session:
        receipt = session.get(CloudFileImport, ("owner", SELECTION["request_id"]))
        receipt.file_id = None; session.add(receipt); session.commit()
    count = picker.state.calls
    replay = post(picker, "/connections/cloud:one/import", SELECTION)
    assert replay.status_code == 410 and picker.state.calls == count


@pytest.mark.parametrize("change", ["browser", "session", "revision", "grant", "secure"])
def test_oauth_callback_rejects_missing_browser_and_consent_revocation(picker, monkeypatch, change):
    called=[]
    monkeypatch.setattr(providers, "exchange_code", lambda *args: called.append(args) or {"access_token": "must-not-save"})
    started = post(picker, "/connections/one/oauth/start", {"password": PASSWORD})
    assert started.status_code == 200
    state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
    with Session(picker.engine) as session:
        if change == "browser": picker.client.cookies.clear()
        elif change == "session":
            from datetime import datetime
            row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(picker.tokens["owner"]))).one()
            row.revoked_at=datetime.utcnow();session.add(row)
        elif change == "revision":
            row=session.get(CloudFileConnection,"one");row.revision+=1;session.add(row)
        elif change == "grant":session.delete(session.get(ChannelMember,("room","owner")))
        else:session.add(SecureChannel(channel_id="room",created_by="owner",status="preparing"))
        session.commit()
    response=picker.client.get("/cloud-files/oauth/callback",params={"state":state,"code":"fixture"},follow_redirects=False)
    assert "cloud_files_error" in response.headers["location"] and called==[]
    with Session(picker.engine) as session: assert "access_token" not in service.credentials(session.get(CloudFileConnection,"one"))


def test_oauth_callback_rechecks_current_native_session_after_token_exchange(picker, monkeypatch):
    def exchange(*args):
        from datetime import datetime
        with Session(picker.engine) as session:
            row=session.exec(select(ActiveSession).where(ActiveSession.token_hash==token_digest(picker.tokens["owner"]))).one()
            row.revoked_at=datetime.utcnow();session.add(row);session.commit()
        return {"access_token":"late-private-token"}
    monkeypatch.setattr(providers,"exchange_code",exchange)
    start=post(picker,"/connections/one/oauth/start",{"password":PASSWORD})
    state=parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]
    response=picker.client.get("/cloud-files/oauth/callback",params={"state":state,"code":"fixture"},follow_redirects=False)
    assert "cloud_files_error" in response.headers["location"]
    with Session(picker.engine) as session: assert "access_token" not in service.credentials(session.get(CloudFileConnection,"one"))


def test_scoped_sso_cannot_browse_native_provider_credentials_but_can_read_channel_links(picker):
    from app.models.workspace_oidc import WorkspaceOIDCProvider,WorkspaceOIDCSession
    from app.services.auth import create_token
    with Session(picker.engine) as session:
        session.add(WorkspaceOIDCProvider(workspace_id="a",name="Customer",issuer_url="https://idp.example.com",client_id="customer",created_by="owner",enabled=True));session.commit()
        token=create_token("owner",session,auth_methods=["sso"],workspace_scope={"workspace_id":"a","provider_revision":1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(token),workspace_id="a",provider_revision=1));session.commit()
    headers={"Authorization":"Bearer "+token}
    assert picker.client.post(BASE+"/connections/cloud:one/browse",json={"connection_revision":1},headers=headers).status_code==403
    assert picker.client.get(BASE+"/links",headers=headers).status_code==200
    assert picker.state.calls==0


def test_ordinary_file_delete_tombstones_picker_acknowledgement(picker):
    result=post(picker,"/connections/cloud:one/import",SELECTION)
    assert result.status_code==200
    identifier=result.json()["id"]
    removed=picker.client.delete("/files/"+identifier,headers=picker.headers["owner"])
    assert removed.status_code==200,removed.text
    with Session(picker.engine) as session: assert session.get(CloudFileImport,("owner",SELECTION["request_id"])).file_id is None
    count=picker.state.calls
    assert post(picker,"/connections/cloud:one/import",SELECTION).status_code==410
    assert picker.state.calls==count and not picker.storage.get_file_path(identifier).exists()


def test_rotating_refresh_credentials_survive_a_failed_provider_read_retry(picker, monkeypatch):
    calls=[]
    def read(provider,config,secret,*args):
        calls.append(secret["access_token"])
        if len(calls)==1:raise providers.ProviderError("reauthorization_required",status=401)
        assert secret["refresh_token"]=="rotated-private-refresh"
        raise providers.ProviderError("provider_unavailable",status=503)
    def refresh(provider,config,refresh_token):
        assert provider=="dropbox" and refresh_token=="private-refresh-token"
        return {"access_token":"rotated-private-access","refresh_token":"rotated-private-refresh"}
    monkeypatch.setattr(providers,"read_file",read);monkeypatch.setattr(providers,"refresh_tokens",refresh)
    response=post(picker,"/connections/cloud:one/import",SELECTION)
    assert response.status_code==422 and calls==["private-access-token","rotated-private-access"]
    with Session(picker.engine) as session:
        secret=service.credentials(session.get(CloudFileConnection,"one"))
        assert secret["refresh_token"]=="rotated-private-refresh" and secret["access_token"]=="rotated-private-access"
        assert not session.exec(select(File)).all() and not session.exec(select(CloudFileImport)).all()


def test_lost_channel_grant_after_401_prevents_sending_a_refresh_token(picker, monkeypatch):
    refreshed=[]
    def read(*args):
        with Session(picker.engine) as session:
            session.delete(session.get(ChannelMember,("room","owner")));session.commit()
        raise providers.ProviderError("reauthorization_required",status=401)
    monkeypatch.setattr(providers,"read_file",read)
    monkeypatch.setattr(providers,"refresh_tokens",lambda *args:refreshed.append(args) or {"access_token":"must-not-use"})
    assert post(picker,"/connections/cloud:one/import",SELECTION).status_code==403
    assert refreshed==[]


@pytest.mark.parametrize("action", ["browse", "import"])
def test_displayed_connection_revision_rejects_replaced_account_before_provider_io(picker, action):
    # The replacement account deliberately exposes the same remote ID and ETag.
    # Selection must remain bound to the old displayed consent, not those values.
    with Session(picker.engine) as session:
        row=session.get(CloudFileConnection,"one");row.revision=2
        row.config_json=json.dumps({"client_id":"replacement-customer","folder_id":"/chosen"})
        service.save_credentials(row,{"access_token":"replacement-account-token"});session.add(row);session.commit()
    payload={"connection_revision":1} if action=="browse" else SELECTION
    response=post(picker,"/connections/cloud:one/"+action,payload)
    assert response.status_code==409 and picker.state.calls==0
    with Session(picker.engine) as session:
        assert not session.exec(select(File)).all() and not session.exec(select(CloudFileImport)).all()


def test_browse_response_binds_each_selectable_entry_to_displayed_connection_consent(picker):
    response=post(picker,"/connections/cloud:one/browse",{"connection_revision":1})
    assert response.status_code==200
    page=response.json()
    assert page["connection_revision"]==1 and page["connection_id"]=="cloud:one"
    assert all(entry["connection_revision"]==1 and entry["connection_id"]=="cloud:one" for entry in page["items"])
    assert picker.client.post(BASE+"/connections/cloud:one/browse",json={},headers=picker.headers["owner"]).status_code==422
    assert picker.client.post(BASE+"/connections/cloud:one/import",json={key:value for key,value in SELECTION.items() if key!="connection_revision"},headers=picker.headers["owner"]).status_code==422
