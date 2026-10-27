"""Legacy flow writes obey the same native policies as channel messages."""
from unittest.mock import AsyncMock

import pytest
from sqlmodel import Session, select

from app.api import comments, posts
from app.models.post import Comment, Post
from tests.test_data_controls import controls, headers
from tests.test_enterprise_governance import case, dlp, governance


@pytest.fixture
def legacy_flows(governance, monkeypatch):
    for module in (posts, comments):
        governance[0].app.include_router(module.router)
        monkeypatch.setattr(module, "broadcast_to_channel", AsyncMock())
    return governance


def seed(state, name="owner"):
    client = state[0]
    posted = client.post("/posts/flows/native", headers=headers(state, name), json={"content": "Retain this post"})
    assert posted.status_code == 200, posted.text
    post_id = posted.json()["id"]
    commented = client.post("/comments/post/" + post_id, headers=headers(state, name), json={"content": "Retain this comment"})
    assert commented.status_code == 200, commented.text
    return post_id, commented.json()["id"]


@pytest.mark.parametrize("target", ["post", "comment"])
@pytest.mark.parametrize("method", ["put", "delete"])
def test_preservation_prevents_legacy_flow_content_overwrite_and_deletion(legacy_flows, target, method):
    state = legacy_flows
    post_id, comment_id = seed(state)
    assert case(state, channel_ids=["native"], custodian_ids=["owner"]).status_code == 201
    path = "/posts/" + post_id if target == "post" else "/comments/" + comment_id
    response = getattr(state[0], method)(path, headers=headers(state), **({"json": {"content": "Erase evidence"}} if method == "put" else {}))
    assert response.status_code == 423, response.text
    with Session(state[1]) as session:
        assert session.get(Post, post_id).content == "Retain this post"
        assert session.get(Post, post_id).deleted is False
        assert session.get(Comment, comment_id).content == "Retain this comment"
        assert session.get(Comment, comment_id).deleted is False


@pytest.mark.parametrize("target", ["new_post", "post", "new_comment", "comment", "file_comment", "reply"])
def test_message_dlp_covers_legacy_post_and_comment_writes(legacy_flows, target):
    state = legacy_flows
    post_id, comment_id = seed(state)
    assert dlp(state, surfaces=["message"]).status_code == 201
    paths = {"new_post": "/posts/flows/native", "post": "/posts/" + post_id,
             "new_comment": "/comments/post/" + post_id, "comment": "/comments/" + comment_id,
             "file_comment": "/comments/file/" + "d" * 32, "reply": "/comments/comment/" + comment_id}
    method = "put" if target in {"post", "comment"} else "post"
    response = getattr(state[0], method)(paths[target], headers=headers(state), json={"content": "PRIVATE-RESTRICTED"})
    assert response.status_code == 423, response.text
    with Session(state[1]) as session:
        assert len(session.exec(select(Post)).all()) == 1
        assert len(session.exec(select(Comment)).all()) == 1
        assert session.get(Post, post_id).content == "Retain this post"
        assert session.get(Comment, comment_id).content == "Retain this comment"


def test_custodian_preservation_allows_other_authors_ordinary_edits(legacy_flows):
    state = legacy_flows
    post_id, comment_id = seed(state, "member")
    assert case(state, channel_ids=["native"], custodian_ids=["owner"]).status_code == 201
    for path in ("/posts/" + post_id, "/comments/" + comment_id):
        response = state[0].put(path, headers=headers(state, "member"), json={"content": "Ordinary edit"})
        assert response.status_code == 200, response.text


@pytest.mark.parametrize("target", ["post", "comment"])
def test_legacy_flow_warn_policy_returns_policy_metadata(legacy_flows, target):
    state = legacy_flows
    post_id, _ = seed(state)
    assert dlp(state, surfaces=["message"], action="warn").status_code == 201
    path = "/posts/flows/native" if target == "post" else "/comments/post/" + post_id
    response = state[0].post(path, headers=headers(state), json={"content": "PRIVATE-RESTRICTED"})
    assert response.status_code == 200, response.text
    assert response.json()["policy_warnings"][0]["code"] == "native_dlp_warning"
