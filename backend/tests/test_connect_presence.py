"""Presence delivery uses each recipient's authenticated organization scope."""
import asyncio
from unittest.mock import AsyncMock

from sqlmodel import Session

from app.models.enterprise_governance import Organization, OrganizationWorkspace
from app.realtime import socket
from .test_connect import activate, grant, shared_api
from .test_connect_embed import child, partner


def test_governed_recipient_socket_connect_uses_own_authenticated_scope(partner, monkeypatch):
    api = partner
    connection = activate(api)
    grant(api, connection)
    with Session(api.engine) as session:
        session.add(Organization(id="presence-org", name="Governed B", created_by="a-admin", require_sso=True))
        session.flush()
        session.add(OrganizationWorkspace(workspace_id="b", organization_id="presence-org", linked_by="a-admin"))
        session.commit()
    token = child(api, connection)
    monkeypatch.setattr(socket, "presence_store", {})
    monkeypatch.setattr(socket, "broker", lambda: None)
    monkeypatch.setattr(socket.sio, "save_session", AsyncMock())
    monkeypatch.setattr(socket.sio, "get_session", AsyncMock(return_value={"user_id": "b-member", "token": token}))
    monkeypatch.setattr(socket.sio, "enter_room", AsyncMock())
    monkeypatch.setattr(socket.sio, "emit", AsyncMock())
    monkeypatch.setattr(socket.sio.manager, "get_participants", lambda namespace, room:
        iter([("recipient", "engineio")]) if room == "user:b-member" else iter([]))
    asyncio.run(socket.connect("recipient", {}, {"token": token}))
    assert socket.presence_store["b-member"]["sessions"]["recipient"]["user_id"] == "b-member"
    socket.sio.emit.assert_awaited_once_with("presence", {"user_id": "b-member", "status": "online", "custom_status": None}, room="recipient")


def test_governed_presence_rechecks_each_scope_and_only_explicit_partner_peers(partner, monkeypatch):
    api = partner
    connection = activate(api)
    grant(api, connection)
    grant(api, connection, "dual")
    with Session(api.engine) as session:
        for workspace in "ab":
            session.add(Organization(id="presence-" + workspace, name=workspace, created_by="a-admin", require_sso=True))
            session.flush()
            session.add(OrganizationWorkspace(workspace_id=workspace, organization_id="presence-" + workspace,
                linked_by="a-admin"))
        session.commit()
    token = child(api, connection)
    saved = {
        "recipient": {"user_id": "b-member", "token": token},
        "source": {"user_id": "a-admin", "token": api.tokens["a-admin-scoped-a"]},
        "outsider": {"user_id": "c-admin", "token": api.tokens["c-admin"]},
        "policy-denied": {"user_id": "a-member", "token": api.tokens["a-member"]},
        "dual-native": {"user_id": "dual", "token": api.tokens["dual"]},
        "dual-scoped": {"user_id": "dual", "token": api.tokens["dual-scoped-b"]},
    }
    presence = {row["user_id"]: {"sessions": {sid: {}}} for sid, row in saved.items() if sid != "recipient"}
    monkeypatch.setattr(socket, "presence_store", presence)
    monkeypatch.setattr(socket, "broker", lambda: None)
    monkeypatch.setattr(socket.sio, "save_session", AsyncMock())
    monkeypatch.setattr(socket.sio, "get_session", AsyncMock(side_effect=lambda sid: saved[sid]))
    monkeypatch.setattr(socket.sio, "enter_room", AsyncMock())
    monkeypatch.setattr(socket.sio, "emit", AsyncMock())
    monkeypatch.setattr(socket.sio.manager, "get_participants", lambda namespace, room:
        iter([(sid, "engineio") for sid, row in saved.items() if room == "user:" + row["user_id"]]))
    asyncio.run(socket.connect("recipient", {}, {"token": token}))
    assert {call.kwargs["room"] for call in socket.sio.emit.await_args_list} == {"recipient", "source", "dual-scoped"}
    # Revoke the partner seat through the production API. Its old child must
    # receive no subsequent private presence, while the source keeps its scope.
    response = api.client.delete("/connect/workspaces/b/connections/" + connection["id"] + "/members/b-member",
        headers=api.headers("b-admin-scoped-b"))
    assert response.status_code == 200, response.text
    socket.sio.emit.reset_mock()
    asyncio.run(socket.broadcast_presence("a-admin", {"user_id": "a-admin", "status": "online"}))
    assert {call.kwargs["room"] for call in socket.sio.emit.await_args_list} == {"source", "dual-scoped"}
