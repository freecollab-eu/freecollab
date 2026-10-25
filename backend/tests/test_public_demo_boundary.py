"""Boundaries for the actual product running on disposable example data."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlmodel import Session, SQLModel, create_engine, select

from app import database
from app.api import auth, chat_productivity, messages, push, content_completion, meetings, media_capture, app_platform, slack_livebridge
from app.config import settings
from app.database import get_session
from app.main import app as _registered_models  # Register every additive FK table.
from app.models.channel import Channel, ChannelMember
from app.models.public_demo import PublicDemoVisit
from app.models.session import ActiveSession
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from app.realtime import socket as realtime
from app.services.auth import track_session
from app.services import public_demo_boundary as boundary


@pytest.fixture
def demo(tmp_path, monkeypatch):
    root = tmp_path / "public-demo"
    root.mkdir()
    for key, value in {
        "demo_instance": True, "demo_data_path": str(root),
        "database_url": "sqlite:///" + str(root / "public-demo.sqlite"),
        "storage_path": str(root / "files"), "message_encryption_key_file": str(root / "message-keys.json"),
        "account_security_key_file": str(root / "account-security.key"),
        "storage_backend": "local", "mail_delivery_mode": "disabled", "redis_url": None,
        "meetings_sfu_provider": "disabled", "meetings_ice_servers": [], "meetings_turn_urls": [],
        "demo_session_rate_limit_per_minute": 6,
    }.items():
        monkeypatch.setattr(settings, key, value)
    for key in ("matrix_enabled", "connect_federation_enabled", "oidc_enabled", "webhooks_enabled",
                "sync_enabled", "otrust_enabled", "cloud_files_enabled", "external_meetings_enabled",
                "copilot_enabled", "push_enabled", "native_push_enabled", "native_calls_enabled",
                "enable_experimental_features", "enterprise_search_enabled", "data_controls_enabled"):
        monkeypatch.setattr(settings, key, False)
    monkeypatch.setattr(boundary, "_buckets", boundary._Buckets())
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(realtime, "engine", engine)
    SQLModel.metadata.create_all(engine)
    visitors = []
    for index in range(2):
        uid, wid, cid, vid = [uuid4().hex for _ in range(4)]
        now = datetime.utcnow()
        expiry = now + timedelta(hours=1)
        with Session(engine) as session:
            session.add(User(id=uid, name="Example visitor " + str(index), email=uid + "@demo.invalid",
                             password_hash="!no-password", role="user"))
            session.add(Workspace(id=wid, name="Private example " + str(index)))
            session.add(Channel(id=cid, name="courtyard"))
            session.commit()
            session.add(WorkspaceMember(workspace_id=wid, user_id=uid, role="owner"))
            session.add(WorkspaceChannel(workspace_id=wid, channel_id=cid))
            session.add(ChannelMember(channel_id=cid, user_id=uid, role="admin"))
            session.add(PublicDemoVisit(id=vid, user_id=uid, workspace_id=wid, channel_id=cid,
                                       participant_ids_json=json.dumps([uid]), expires_at=expiry))
            session.commit()
            claims = {"sub": uid, "iat": now, "exp": expiry, "jti": uuid4().hex,
                      "av": 0, "amr": [], "demo_visit_id": vid}
            token = jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
            track_session(token, session)
        visitors.append({"user": uid, "workspace": wid, "channel": cid, "visit": vid,
                         "claims": claims, "token": token, "headers": {"Authorization": "Bearer " + token}})
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(messages.router)
    app.include_router(chat_productivity.router)
    app.include_router(push.router)
    app.include_router(content_completion.router)
    app.include_router(meetings.router)
    app.include_router(media_capture.router)
    app.include_router(app_platform.router)
    app.include_router(slack_livebridge.router)
    app.add_middleware(boundary.PublicDemoBoundaryMiddleware)
    calls = []
    @app.post("/demo/session")
    async def bootstrap(request: Request):
        calls.append("start")
        return {"started": True}
    @app.delete("/demo/session")
    async def leave():
        calls.append("leave")
        return {"ended": True}
    @app.post("/files/channels/{channel_id}")
    async def uploaded(channel_id: str, request: Request):
        size = len(await request.body())
        calls.append(("upload", size))
        return {"bytes": size}
    @app.post("/admin/users")
    async def unsafe():
        calls.append("unsafe")
        return {"unsafe": True}
    def dependency():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = dependency
    with TestClient(app) as client:
        yield client, engine, visitors, calls
    engine.dispose()


def test_default_product_has_no_demo_policy_interception(demo, monkeypatch):
    client, _, _, calls = demo
    monkeypatch.setattr(settings, "demo_instance", False)
    assert client.post("/admin/users").json() == {"unsafe": True}
    assert calls == ["unsafe"]


@pytest.mark.parametrize("path", [
    "/auth/register", "/auth/login", "/account/verification/request", "/account/password",
    "/account/mfa/setup", "/admin/users", "/workspaces/room/oidc/test",
    "/workspaces/room/copilot/config", "/workspaces/room/cloud-files/connections",
    "/workspaces/room/webhooks", "/meetings/room/invites", "/meeting-access/room/guest",
    "/meeting-access/room/resume", "/meetings/sfu/room/capture", "/channels/room/calendar/provider-delivery",
    "/office-edit/session/secret/file.docx", "/channels/room/file-experience/file/office-sessions",
    "/secure/unlock", "/_matrix/client/v3/register", "/native-clients/connect", "/push/subscribe",
    "/operations/security", "/operations/metrics", "/workspaces/room/data-controls/export",
    "/workspaces/room/slack-imports/preview", "/connect/federation/inbox", "/unknown-future-feature",
])
def test_external_or_installation_actions_never_reach_handlers(demo, path):
    client, _, people, calls = demo
    response = client.post(path, headers=people[0]["headers"], content=b'{"secret":"never-consumed"}')
    assert response.status_code == 403
    assert response.json()["code"] == "public_demo_limit"
    assert "public sample" in response.json()["detail"]
    assert response.headers["Cache-Control"] == "private, no-store"
    assert calls == []


def test_actual_sample_calendar_collections_and_semantic_search_stay_visit_scoped(demo):
    client, _, people, _ = demo
    first, other = people
    path = f"/workspaces/{first['workspace']}/personal-calendar"
    fields = {"title": "My sample appointment", "description": "Private example", "location": "",
        "starts_at": "2026-11-02T09:00:00Z", "ends_at": "2026-11-02T10:00:00Z",
        "timezone": "UTC", "all_day": False, "reminder_minutes": None, "external_uid": None}
    saved = client.post(path, headers=first["headers"], json={"id": "own-example", "fields": fields})
    assert saved.status_code == 200, saved.text
    assert client.get(path, headers=first["headers"]).json()[0]["fields"]["description"] == "Private example"
    assert client.get(path, headers=other["headers"]).status_code == 403
    collections = f"/channels/{first['channel']}/content-collections"
    saved = client.post(collections, headers=first["headers"], json={"id": "sample-materials", "title": "Sample materials", "items": []})
    assert saved.status_code == 201, saved.text
    assert client.get(collections, headers=other["headers"]).status_code == 403
    search = client.post(f"/workspaces/{first['workspace']}/semantic-search", headers=first["headers"], json={"query": "planning"})
    assert search.status_code == 200, search.text
    assert search.json()["items"] == []
    assert client.post(f"/workspaces/{first['workspace']}/semantic-search", headers=other["headers"], json={"query": "planning"}).status_code == 403


def test_actual_sample_app_metadata_is_readable_without_external_dispatch(demo, monkeypatch):
    client, _, people, _ = demo
    first, other = people
    def forbidden(*_args, **_kwargs): raise AssertionError('Sample metadata must not call an external provider')
    monkeypatch.setattr(slack_livebridge.service, 'slack_request', forbidden)
    path = f"/workspaces/{first['workspace']}"
    assert client.get(path + '/apps/catalog', headers=first['headers']).status_code == 200
    assert client.get(path + '/apps', headers=first['headers']).json()['installations'] == []
    local = path + f"/channels/{first['channel']}"
    for suffix, field in (('/apps/cards', 'cards'), ('/apps/commands', 'commands'), ('/apps/ai/drafts', 'drafts'), ('/apps/ai/recaps', 'schedules'), ('/slack-bridges', 'bridges')):
        result = client.get(local + suffix, headers=first['headers'])
        assert result.status_code == 200, result.text
        assert result.json()[field] == []
        assert client.get(local + suffix, headers=other['headers']).status_code == 403
    assert client.get(local + '/slack-bridges', headers=first['headers']).json()['public_example'] is True
    for suffix in ('/apps', '/apps/catalog'):
        assert client.get(path + suffix, headers=other['headers']).status_code == 403
    # Metadata visibility must not enable installation or external command writes.
    assert client.post(path + '/apps', headers=first['headers'], json={}).status_code == 403
    assert client.post(local + '/apps/commands/example', headers=first['headers'], json={}).status_code == 403


def test_actual_sample_meeting_capture_and_capabilities_belong_to_one_visit(demo):
    client, _, people, _ = demo
    first, other = people
    joined = client.post(f"/channels/{first['channel']}/meetings", headers=first["headers"], json={})
    assert joined.status_code == 201, joined.text
    state = joined.json(); meeting = state["meeting"]["id"]
    own = {**first["headers"], "X-Meeting-Token": state["meeting_token"]}
    assert client.get(f"/meeting-access/{meeting}/poll", headers=own).status_code == 200
    # Even a known foreign capability cannot bypass the public visit boundary.
    stolen = {**other["headers"], "X-Meeting-Token": state["meeting_token"]}
    assert client.get(f"/meeting-access/{meeting}/poll", headers=stolen).status_code == 403
    capture_path = f"/meetings/mesh/{meeting}/capture"
    created = client.post(capture_path, headers=first["headers"], json={})
    assert created.status_code == 201, created.text
    capture = created.json()["capture"]
    body = {"capture_id": capture["id"], "generation": capture["generation"], "action": "start"}
    started = client.post(capture_path + "/control", headers=first["headers"], json=body)
    assert started.status_code == 200, started.text
    assert started.json()["capture"]["state"] == "recording"
    stopped = client.post(capture_path + "/control", headers=first["headers"], json={**body, "action": "stop"})
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["publication_receipt"]
    assert client.get(capture_path, headers=other["headers"]).status_code in {403, 404}
    assert client.post(f"/meetings/{meeting}/end", headers=first["headers"], json={}).status_code == 200


def test_actual_native_product_handlers_retain_identity_and_membership_checks(demo):
    client, _, people, _ = demo
    first, other = people
    assert client.get("/auth/me", headers=first["headers"]).json()["id"] == first["user"]
    response = client.post("/messages/channels/" + first["channel"], headers=first["headers"], json={"content": "Actual product message"})
    assert response.status_code == 200, response.text
    item = response.json()
    assert item["author_id"] == first["user"]
    assert client.get("/messages/channels/" + first["channel"], headers=first["headers"]).json()[0]["content"] == "Actual product message"
    assert client.get("/messages/" + item["id"], headers=other["headers"]).status_code == 403
    assert client.post("/messages/channels/" + first["channel"], headers=other["headers"], json={"content": "intrusion"}).status_code == 403


def test_actual_chat_outbox_delivery_is_idempotent_and_stays_visit_scoped(demo):
    client, _, people, _ = demo
    first, other = people
    body = {"client_id": uuid4().hex, "content": "Actual shared Chat outbox", "mentions": []}
    path = "/mobile/offline/channels/" + first["channel"] + "/messages"
    response = client.post(path, headers=first["headers"], json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["replayed"] is False and result["content"] == body["content"]
    repeated = client.post(path, headers=first["headers"], json=body)
    assert repeated.status_code == 200 and repeated.json()["id"] == result["id"] and repeated.json()["replayed"] is True
    assert client.post(path, headers=other["headers"], json=body).status_code == 403


def test_actual_native_worker_delivers_scheduled_message_without_outbound_batches(demo):
    from app.models.chat_productivity import ScheduledMessage
    from app.services import integration_worker
    client, engine, people, _ = demo
    person = people[0]
    body = {"client_id": uuid4().hex, "content": "Delivered by the actual worker",
            "due_at": (datetime.utcnow() + timedelta(minutes=2)).isoformat() + "Z"}
    response = client.post("/channels/" + person["channel"] + "/scheduled-messages", headers=person["headers"], json=body)
    assert response.status_code == 201, response.text
    row_id = response.json()["id"]
    with Session(engine) as session:
        row = session.get(ScheduledMessage, row_id)
        row.due_at = datetime.utcnow() - timedelta(seconds=1)
        session.add(row)
        session.commit()
    def must_not_dispatch(_):
        pytest.fail("Public sample must not dispatch external/provider/app work")
    result = integration_worker.run_once(engine, webhook_batch=must_not_dispatch, proof_batch=must_not_dispatch,
        sync_batch=must_not_dispatch, calendar_batch=must_not_dispatch, data_batch=must_not_dispatch,
        push_batch=must_not_dispatch, apps_batch=must_not_dispatch, recaps_batch=must_not_dispatch)
    assert result["apps"] == result["recaps"] == result["webhooks"] == "disabled"
    assert result["chat"] != "error"
    with Session(engine) as session:
        row = session.get(ScheduledMessage, row_id)
        assert row.status == "sent" and row.message_id
    messages = client.get("/messages/channels/" + person["channel"], headers=person["headers"]).json()
    assert any(item["content"] == body["content"] for item in messages)


def test_worker_rejects_an_operator_database_before_writing_heartbeat(demo):
    from app.services import integration_worker
    from app.models.operations import WorkerHeartbeat
    _, _, _, _ = demo
    other = create_engine("sqlite://")
    SQLModel.metadata.create_all(other)
    with pytest.raises(HTTPException) as failure:
        integration_worker.run_once(other)
    assert failure.value.status_code == 503
    with Session(other) as session:
        assert session.exec(select(WorkerHeartbeat)).all() == []
    other.dispose()


@pytest.mark.parametrize("change", ["expired-visit", "revoked-visit", "revoked-token", "untracked-token", "missing-visit-claim", "other-visit-claim"])
def test_only_current_tracked_visit_tokens_reach_native_routes(demo, change):
    client, engine, people, _ = demo
    person = people[0]
    headers = person["headers"]
    with Session(engine) as session:
        if change in {"expired-visit", "revoked-visit"}:
            row = session.get(PublicDemoVisit, person["visit"])
            if change == "expired-visit": row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            else: row.revoked_at = datetime.utcnow()
            session.add(row)
        elif change == "revoked-token":
            row = session.exec(select(ActiveSession).where(ActiveSession.user_id == person["user"])).one()
            row.revoked_at = datetime.utcnow()
            session.add(row)
        else:
            claims = {**person["claims"], "jti": uuid4().hex}
            if change == "missing-visit-claim": claims.pop("demo_visit_id")
            if change == "other-visit-claim": claims["demo_visit_id"] = people[1]["visit"]
            token = jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
            if change != "untracked-token": track_session(token, session)
            headers = {"Authorization": "Bearer " + token}
        session.commit()
    assert client.get("/messages/channels/" + person["channel"], headers=headers).status_code == 401


def test_content_and_upload_limits_enforced_before_downstream_work(demo):
    client, _, people, calls = demo
    person = people[0]
    response = client.post("/files/channels/" + person["channel"], headers=person["headers"], content=b"x" * (boundary.UPLOAD_LIMIT + 1))
    assert response.status_code == 413
    assert calls == []
    response = client.post("/messages/channels/" + person["channel"], headers=person["headers"], content=b"x" * (boundary.JSON_LIMIT + 1))
    assert response.status_code == 413
    assert calls == []


def test_persistent_byte_budget_isolated_between_visits_and_reset_remains_possible(demo):
    client, engine, people, calls = demo
    first, other = people
    with Session(engine) as session:
        visit = session.get(PublicDemoVisit, first["visit"])
        visit.mutation_bytes = boundary.VISIT_MUTATION_BYTES - 3
        session.add(visit)
        session.commit()
    path = "/files/channels/" + first["channel"]
    assert client.post(path, headers=first["headers"], content=b"abc").status_code == 200
    assert client.post(path, headers=first["headers"], content=b"d").status_code == 429
    assert client.post("/files/channels/" + other["channel"], headers=other["headers"], content=b"d").status_code == 200
    assert client.delete("/demo/session", headers=first["headers"]).status_code == 200
    assert calls == [("upload", 3), ("upload", 1), "leave"]
    with Session(engine) as session:
        assert session.get(PublicDemoVisit, first["visit"]).mutation_bytes == boundary.VISIT_MUTATION_BYTES
        assert session.get(PublicDemoVisit, other["visit"]).mutation_bytes == 1


def test_concurrent_last_mutation_budget_has_one_winner(demo):
    _, engine, people, _ = demo
    visit_id = people[0]["visit"]
    with Session(engine) as session:
        row = session.get(PublicDemoVisit, visit_id)
        row.mutation_count = boundary.VISIT_MUTATIONS - 1
        session.add(row)
        session.commit()
    def reserve():
        try:
            boundary.reserve_public_demo_mutation(visit_id, 2)
            return 200
        except HTTPException as failure:
            return failure.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: reserve(), range(2))) == [200, 429]
    with Session(engine) as session:
        row = session.get(PublicDemoVisit, visit_id)
        assert row.mutation_count == boundary.VISIT_MUTATIONS
        assert row.mutation_bytes == 2


def test_begin_rate_has_bounded_memory_and_does_not_trust_forwarded_header(demo):
    client, _, _, calls = demo
    for index in range(6):
        assert client.post("/demo/session", headers={"X-Forwarded-For": "192.0.2." + str(index)}).status_code == 200
    assert client.post("/demo/session", headers={"X-Forwarded-For": "198.51.100.50"}).status_code == 429
    assert len(calls) == 6
    buckets = boundary._Buckets(maximum=2)
    assert buckets.allow("one", 1, now=0) and buckets.allow("two", 1, now=0)
    assert not buckets.allow("three", 1, now=1)
    assert buckets.allow("three", 1, now=61)
    assert len(buckets.rows) == 1


def test_cross_site_form_cannot_consume_a_sample_slot(demo):
    client, _, _, calls = demo
    response = client.post("/demo/session", headers={"Origin": "https://unrelated.invalid"}, content="form=data")
    assert response.status_code == 403 and calls == []
    response = client.post("/demo/session", headers={"Origin": settings.public_app_url.rstrip("/"), "Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403 and calls == []
    assert client.post("/demo/session", headers={"Origin": settings.public_app_url.rstrip("/")}).status_code == 200


@pytest.mark.parametrize("path", ["/channels/room/../admin/users", "/channels//members", "/channels/room\\admin", "/workspaces/room/copilot/config"])
def test_no_normalization_or_prefix_policy_escape(path):
    assert not boundary.allowed_public_demo_route("POST", path)


def test_streamed_oversize_body_and_huge_length_fail_before_handler(demo):
    _, _, people, _ = demo
    async def run(headers, chunks):
        events = iter(chunks)
        async def receive(): return next(events)
        scope = {"headers": headers}
        return await boundary._buffer_body(scope, receive, 8)
    with pytest.raises(HTTPException) as error:
        asyncio.run(run([], [{"type": "http.request", "body": b"12345", "more_body": True}, {"type": "http.request", "body": b"6789"}]))
    assert error.value.status_code == 413
    with pytest.raises(HTTPException) as error:
        asyncio.run(run([(b"content-length", b"9" * 10000)], []))
    assert error.value.status_code == 413


@pytest.mark.parametrize("setting", ["smtp_password", "oidc_client_secret", "matrix_registration_shared_secret", "push_vapid_private_key", "native_push_fcm_service_account", "meetings_sfu_api_secret", "enterprise_vault_token", "s3_secret_access_key"])
def test_external_credentials_fail_startup_without_disclosing_values(demo, monkeypatch, setting):
    monkeypatch.setattr(settings, setting, SecretStr("synthetic-private-value-never-returned"))
    with pytest.raises(RuntimeError) as failure:
        boundary.assert_public_demo_safe_configuration()
    assert "synthetic-private-value" not in str(failure.value)


def test_socket_wrapper_bounds_frames_and_preserves_other_native_api_dispatch(demo):
    called, sent = [], []
    async def native(scope, receive, send):
        called.append(scope["path"])
        if scope["type"] == "websocket":
            assert (await receive())["type"] == "websocket.connect"
            await send({"type": "websocket.accept"})
            assert (await receive())["type"] == "websocket.disconnect"
        else:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"native"})
    wrapper = boundary.PublicDemoSocketBoundaryMiddleware(native)
    async def run():
        events = iter([{"type": "websocket.connect"}, {"type": "websocket.receive", "text": "x" * (boundary.SOCKET_PACKET_LIMIT + 1)}])
        async def receive(): return next(events)
        async def send(event): sent.append(event)
        await wrapper({"type": "websocket", "path": "/socket.io/", "client": ("peer", 1)}, receive, send)
        await wrapper({"type": "http", "path": "/messages/channels/example", "client": ("peer", 1)}, receive, send)
    asyncio.run(run())
    assert called == ["/socket.io/", "/messages/channels/example"]
    assert sum(event["type"] == "websocket.close" for event in sent) == 1
    assert sent[1] == {"type": "websocket.close", "code": 1008}


def test_socket_polling_upload_cannot_bypass_packet_limit(demo):
    calls, sent = [], []
    async def native(scope, receive, send): calls.append(True)
    async def run():
        async def receive(): return {"type": "http.request", "body": b"x" * (boundary.SOCKET_PACKET_LIMIT + 1)}
        async def send(event): sent.append(event)
        await boundary.PublicDemoSocketBoundaryMiddleware(native)({"type": "http", "method": "POST", "path": "/socket.io/", "headers": [], "client": ("peer", 1)}, receive, send)
    asyncio.run(run())
    assert calls == []
    assert sent[0]["status"] == 413


def test_actual_engineio_dispatch_cannot_open_sessions_through_suffixed_paths(demo):
    import socketio
    downstream = []
    async def native(scope, receive, send):
        downstream.append(scope["path"])
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b"native"})
    server = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=[])
    # Exercise the installed Engine.IO dispatcher, whose matching uses a
    # startswith check rather than pretending it behaves like FastAPI routing.
    transport = socketio.ASGIApp(server, other_asgi_app=native)
    wrapped = boundary.PublicDemoSocketBoundaryMiddleware(transport)
    async def request(path, *, body=b"", method="GET", query=b"EIO=4&transport=polling"):
        sent = []
        scope = {"type": "http", "http_version": "1.1", "method": method, "path": path,
                 "raw_path": path.encode(), "root_path": "", "scheme": "http",
                 "query_string": query, "headers": [(b"host", b"testserver")],
                 "client": ("engineio-test", 1), "server": ("testserver", 80)}
        async def receive(): return {"type": "http.request", "body": body, "more_body": False}
        async def send(event): sent.append(event)
        await wrapped(scope, receive, send)
        status = next(event["status"] for event in sent if event["type"] == "http.response.start")
        payload = b"".join(event.get("body", b"") for event in sent if event["type"] == "http.response.body")
        return status, payload
    async def run():
        try:
            status, payload = await request("/socket.io/")
            assert status == 200 and payload.startswith(b"0{"), payload
            session_id = json.loads(payload[1:])["sid"]
            assert set(server.eio.sockets) == {session_id}
            for path in ("/socket.io/anything", "/socket.io//", "/socket.io/../health"):
                status, _ = await request(path)
                assert status == 404
                assert set(server.eio.sockets) == {session_id}
            status, _ = await request("/socket.io/", method="POST", body=b"x" * (boundary.SOCKET_PACKET_LIMIT + 1),
                                      query=b"EIO=4&transport=polling&sid=" + session_id.encode())
            assert status == 413 and set(server.eio.sockets) == {session_id}
            assert downstream == []
        finally:
            # This in-process dispatcher test has no subsequent polling client
            # to acknowledge a queued CLOSE packet. Tear its test socket down
            # without waiting for that nonexistent network consumer.
            for socket in list(server.eio.sockets.values()):
                await socket.close(wait=False, abort=True)
            server.eio.sockets.clear()
            await server.shutdown()
    asyncio.run(run())
