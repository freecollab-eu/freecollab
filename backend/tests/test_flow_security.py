"""Channel isolation for posts, comments and nested replies."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import comments, notifications, posts
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.file import File
from app.models.post import Comment, Post
from app.models.user import User
from app.services.auth import get_current_user


@pytest.fixture
def flow_api(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(posts.router)
    app.include_router(comments.router)
    app.state.user_id = "owner"

    def session_override():
        with Session(engine) as session:
            yield session

    def user_override():
        with Session(engine) as session:
            return session.get(User, app.state.user_id)

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_current_user] = user_override
    for module in (posts, comments):
        monkeypatch.setattr(module, "broadcast_to_channel", AsyncMock())
    monkeypatch.setattr(notifications, "send_to_user", AsyncMock())
    with Session(engine) as session:
        for user_id in ("owner", "member", "outsider"):
            session.add(User(id=user_id, name=user_id, email=user_id + "@example.com", password_hash="unused"))
        for channel_id in ("private", "other"):
            session.add(Channel(id=channel_id, name=channel_id))
            session.add(ChannelMember(channel_id=channel_id, user_id="owner"))
        session.add(ChannelMember(channel_id="private", user_id="member"))
        session.add(ChannelMember(channel_id="other", user_id="outsider"))
        session.add(File(id="file", channel_id="private", uploader_id="owner", filename="note.txt",
                         content_type="text/plain", size=1, storage_path="unused"))
        session.commit()
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, app=app, engine=engine)
    engine.dispose()


def create_post(api, channel="private"):
    response = api.client.post("/posts/flows/" + channel, json={"content": "Private post"})
    assert response.status_code == 200, response.text
    return response.json()


def create_comment(api, post_id, **kwargs):
    response = api.client.post("/comments/post/" + post_id, json={"content": "Private comment", **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_flow_type_cannot_bypass_membership(flow_api, method):
    flow_api.app.state.user_id = "outsider"
    response = flow_api.client.request(method, "/posts/flows/private?flow_type=global",
                                       **({"json": {"content": "Intrusion"}} if method == "POST" else {}))
    assert response.status_code == 400
    response = flow_api.client.request(method, "/posts/flows/private",
                                       **({"json": {"content": "Intrusion"}} if method == "POST" else {}))
    assert response.status_code == 403


@pytest.mark.parametrize("method,suffix", [("GET", ""), ("PUT", ""), ("DELETE", ""),
                                           ("POST", "/like"), ("DELETE", "/like")])
def test_direct_post_routes_deny_outsiders(flow_api, method, suffix):
    post = create_post(flow_api)
    flow_api.app.state.user_id = "outsider"
    response = flow_api.client.request(method, "/posts/" + post["id"] + suffix,
                                       **({"json": {"content": "Intrusion"}} if method == "PUT" else {}))
    assert response.status_code == 403


def test_legacy_nonchannel_post_is_not_public(flow_api):
    with Session(flow_api.engine) as session:
        session.add(Post(id="legacy", flow_id="private", flow_type="global", author_id="owner", content="Private"))
        session.commit()
    assert flow_api.client.get("/posts/legacy").status_code == 400


def test_post_attachment_must_belong_to_flow(flow_api):
    response = flow_api.client.post("/posts/flows/other", json={"content": "Wrong file", "attachments": ["file"]})
    assert response.status_code == 400
    response = flow_api.client.post("/posts/flows/private", json={"content": "Right file", "attachments": ["file"]})
    assert response.status_code == 200


def test_members_can_comment_reply_and_like_without_route_shadowing(flow_api):
    post = create_post(flow_api)
    flow_api.app.state.user_id = "member"
    comment = create_comment(flow_api, post["id"])
    reply = create_comment(flow_api, post["id"], parent_id=comment["id"])
    nested = flow_api.client.post("/comments/comment/" + comment["id"], json={"content": "Another reply"})
    assert nested.status_code == 200, nested.text
    assert nested.json()["parent_id"] == comment["id"]
    response = flow_api.client.get("/comments/" + comment["id"] + "/replies")
    assert response.status_code == 200
    assert {r["id"] for r in response.json()} == {reply["id"], nested.json()["id"]}
    assert flow_api.client.post("/comments/" + comment["id"] + "/like").status_code == 200
    assert flow_api.client.delete("/comments/" + comment["id"] + "/like").status_code == 200
    with Session(flow_api.engine) as session:
        assert session.get(Post, post["id"]).comment_count == 3
        assert session.get(Comment, comment["id"]).reply_count == 2


@pytest.mark.parametrize("method,target", [("GET", "entity"), ("POST", "entity"),
                                           ("GET", "replies"), ("PUT", "comment"),
                                           ("DELETE", "comment"), ("POST", "like"), ("DELETE", "like")])
def test_comment_routes_deny_outsiders(flow_api, method, target):
    post = create_post(flow_api)
    comment = create_comment(flow_api, post["id"])
    flow_api.app.state.user_id = "outsider"
    paths = {"entity": "/comments/post/" + post["id"], "comment": "/comments/" + comment["id"],
             "replies": "/comments/" + comment["id"] + "/replies", "like": "/comments/" + comment["id"] + "/like"}
    response = flow_api.client.request(method, paths[target],
                                       **({"json": {"content": "Intrusion"}} if target == "entity" and method == "POST" or method == "PUT" else {}))
    assert response.status_code == 403


def test_parent_cannot_cross_entity_even_for_member_of_both(flow_api):
    first = create_post(flow_api)
    second = create_post(flow_api, "other")
    comment = create_comment(flow_api, first["id"])
    response = flow_api.client.post("/comments/post/" + second["id"],
                                    json={"content": "Crossed", "parent_id": comment["id"]})
    assert response.status_code == 400
    with Session(flow_api.engine) as session:
        assert session.get(Comment, comment["id"]).reply_count == 0
        assert session.get(Post, second["id"]).comment_count == 0


def test_removed_author_cannot_mutate_posts_or_comments(flow_api):
    post = create_post(flow_api)
    comment = create_comment(flow_api, post["id"])
    with Session(flow_api.engine) as session:
        session.delete(session.get(ChannelMember, ("private", "owner")))
        session.commit()
    assert flow_api.client.put("/posts/" + post["id"], json={"content": "Removed"}).status_code == 403
    assert flow_api.client.delete("/comments/" + comment["id"]).status_code == 403


def test_file_comments_require_membership(flow_api):
    assert flow_api.client.post("/comments/file/file", json={"content": "File note"}).status_code == 200
    flow_api.app.state.user_id = "outsider"
    assert flow_api.client.get("/comments/file/file").status_code == 403
    assert flow_api.client.post("/comments/file/file", json={"content": "Intrusion"}).status_code == 403


def test_unknown_and_cyclic_comment_entities_fail_closed(flow_api):
    assert flow_api.client.post("/comments/global/private", json={"content": "Invalid"}).status_code == 400
    with Session(flow_api.engine) as session:
        session.add(Comment(id="cycle", author_id="owner", entity_type="comment", entity_id="cycle", content="Invalid"))
        session.commit()
    assert flow_api.client.get("/comments/cycle/replies").status_code == 400
    assert flow_api.client.post("/comments/comment/cycle", json={"content": "Invalid"}).status_code == 400


def test_legacy_cross_entity_reply_is_not_exposed(flow_api):
    private_post = create_post(flow_api)
    other_post = create_post(flow_api, "other")
    parent = create_comment(flow_api, private_post["id"])
    with Session(flow_api.engine) as session:
        session.add(Comment(id="legacy-crossed", author_id="outsider", entity_type="post",
                            entity_id=other_post["id"], parent_id=parent["id"], content="Other channel secret"))
        session.commit()
    flow_api.app.state.user_id = "member"
    response = flow_api.client.get("/comments/" + parent["id"] + "/replies")
    assert response.status_code == 200
    assert response.json() == []


def secure_legacy_flow(api):
    """Exercise defensive reads even for preexisting native rows in a binding."""
    from app.models.secure_channel import SecureChannel
    post = create_post(api)
    comment = create_comment(api, post["id"])
    assert api.client.post("/comments/file/file", json={"content": "Legacy file comment"}).status_code == 200
    with Session(api.engine) as session:
        session.add(SecureChannel(channel_id="private", created_by="owner", status="preparing"))
        session.commit()
    return post, comment


@pytest.mark.parametrize("target", ["flow", "post", "comments", "replies", "file_comments"])
def test_secure_binding_hides_all_legacy_experimental_plaintext_reads(flow_api, target):
    post, comment = secure_legacy_flow(flow_api)
    paths = {"flow": "/posts/flows/private", "post": "/posts/" + post["id"],
             "comments": "/comments/post/" + post["id"], "replies": "/comments/" + comment["id"] + "/replies",
             "file_comments": "/comments/file/file"}
    response = flow_api.client.get(paths[target])
    assert response.status_code == 409
    assert "Private post" not in response.text and "Private comment" not in response.text
    flow_api.app.state.user_id = "outsider"
    assert flow_api.client.get(paths[target]).status_code == 403


@pytest.mark.parametrize("method,target", [
    ("POST", "flow"), ("PUT", "post"), ("DELETE", "post"), ("POST", "post_like"), ("DELETE", "post_like"),
    ("POST", "comments"), ("POST", "reply"), ("PUT", "comment"), ("DELETE", "comment"),
    ("POST", "comment_like"), ("DELETE", "comment_like"), ("POST", "file_comments"),
])
def test_secure_binding_rejects_every_experimental_plaintext_mutation(flow_api, method, target):
    post, comment = secure_legacy_flow(flow_api)
    paths = {"flow": "/posts/flows/private", "post": "/posts/" + post["id"], "post_like": "/posts/" + post["id"] + "/like",
             "comments": "/comments/post/" + post["id"], "reply": "/comments/comment/" + comment["id"],
             "comment": "/comments/" + comment["id"], "comment_like": "/comments/" + comment["id"] + "/like",
             "file_comments": "/comments/file/file"}
    kwargs = {"json": {"content": "Must not persist"}} if method == "PUT" or method == "POST" and "like" not in target else {}
    response = flow_api.client.request(method, paths[target], **kwargs)
    assert response.status_code == 409
    with Session(flow_api.engine) as session:
        assert session.get(Post, post["id"]).content == "Private post"
        assert session.get(Post, post["id"]).deleted is False
        assert session.get(Comment, comment["id"]).content == "Private comment"
        assert session.get(Comment, comment["id"]).deleted is False


def test_blocked_direct_pair_can_read_retained_flow_history_but_cannot_send_new_content(flow_api):
    from app.models.direct_message import UserBlock
    post = create_post(flow_api)
    comment = create_comment(flow_api, post["id"])
    with Session(flow_api.engine) as session:
        channel = session.get(Channel, "private")
        channel.type = "direct"
        session.add(channel)
        session.add(UserBlock(blocker_id="member", blocked_id="owner"))
        session.commit()
    assert flow_api.client.get("/posts/flows/private").status_code == 200
    assert flow_api.client.get("/posts/" + post["id"]).status_code == 200
    assert flow_api.client.get("/comments/post/" + post["id"]).status_code == 200
    assert flow_api.client.get("/comments/" + comment["id"] + "/replies").status_code == 200
    assert flow_api.client.post("/posts/flows/private", json={"content": "Blocked"}).status_code == 403
    assert flow_api.client.put("/posts/" + post["id"], json={"content": "Blocked edit"}).status_code == 403
    assert flow_api.client.post("/posts/" + post["id"] + "/like").status_code == 403
    assert flow_api.client.post("/comments/post/" + post["id"], json={"content": "Blocked"}).status_code == 403
    assert flow_api.client.put("/comments/" + comment["id"], json={"content": "Blocked edit"}).status_code == 403
    assert flow_api.client.post("/comments/" + comment["id"] + "/like").status_code == 403


@pytest.mark.parametrize("entity_type", ["post", "file"])
def test_comment_rechecks_root_deleted_while_waiting_for_channel_fence(flow_api, monkeypatch, entity_type):
    post = create_post(flow_api)
    target_id = post["id"] if entity_type == "post" else "file"
    original = posts.lock_channel_writes
    def deleted_before_lock(session, channel_id):
        with Session(flow_api.engine) as concurrent:
            if entity_type == "post":
                record = concurrent.get(Post, target_id)
                record.deleted = True
                concurrent.add(record)
            else:
                concurrent.delete(concurrent.get(File, target_id))
            concurrent.commit()
        original(session, channel_id)
    monkeypatch.setattr(posts, "lock_channel_writes", deleted_before_lock)
    response = flow_api.client.post(f"/comments/{entity_type}/{target_id}", json={"content": "Too late"})
    assert response.status_code == 404
    with Session(flow_api.engine) as session:
        assert session.query(Comment).count() == 0
