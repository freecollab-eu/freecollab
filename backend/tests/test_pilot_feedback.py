"""Voluntary workspace feedback is private, current-grant scoped and erasable."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from app.api import pilot_feedback
from app.models.account_security import AccountSecurity
from app.models.pilot_feedback import PilotFeedback
from app.models.session import ActiveSession
from app.models.user import User
from app.models.workforce import WorkforcePrincipal
from app.models.workspace import WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.services import pilot_feedback as service
from app.services.auth import create_token, token_digest
from tests.test_remote_sync import team


@pytest.fixture
def feedback(team):
    team.client.app.include_router(pilot_feedback.router)
    return team


def request(team, method="get", task=None, who="member", workspace="a", **kwargs):
    path = f"/workspaces/{workspace}/pilot-feedback" + ("/" + task if task else "")
    return getattr(team.client, method)(path, headers=team.headers[who], **kwargs)


def write(team, task="first_message", who="member", workspace="a", **fields):
    return request(team, "put", task, who, workspace, json={"expected_revision": 0, "outcome": "completed", "consent": True, **fields})


def test_explicit_create_update_own_read_tombstone_recreate_and_stale_fences(feedback):
    assert request(feedback).json()["feedback"] == []
    row = write(feedback, rating=4, note="  PRIVATE own note  ")
    assert row.status_code == 200, row.text
    assert row.json()["note"] == "PRIVATE own note" and row.json()["revision"] == 1
    assert request(feedback).headers["cache-control"] == "private, no-store"
    assert request(feedback).json()["can_manage"] is False
    assert request(feedback, who="owner").json()["feedback"] == []
    assert write(feedback, note="stale").status_code == 409
    changed = write(feedback, expected_revision=1, outcome="blocked", rating=None, note="Later blocker")
    assert changed.json()["revision"] == 2
    assert request(feedback, "delete", "first_message", params={"expected_revision": 1}).status_code == 409
    deleted = request(feedback, "delete", "first_message", params={"expected_revision": 2})
    assert deleted.json() == {"task": "first_message", "revision": 3, "deleted": True}
    assert request(feedback, "delete", "first_message", params={"expected_revision": 2}).json() == deleted.json()
    state = request(feedback).json()
    assert state["feedback"] == [] and state["revisions"]["first_message"] == 3
    with Session(feedback.engine) as session:
        row = session.get(PilotFeedback, ("a", "member", "first_message"))
        assert row.deleted and all(getattr(row, key) is None for key in ("encrypted_note", "rating", "outcome", "created_at", "updated_at"))
    assert write(feedback, expected_revision=2).status_code == 409
    assert write(feedback, expected_revision=3, note="Deliberate new feedback").json()["revision"] == 4


@pytest.mark.parametrize("fields", [
    {"consent": False}, {"consent": 1}, {"rating": True}, {"rating": 0}, {"rating": 6}, {"rating": "3"},
    {"expected_revision": True}, {"note": "a" * 1001}, {"outcome": "verified"}, {"user_id": "owner"},
    {"workspace_id": "b"}, {"channel_id": "private"}, {"provider_id": "secret"}, {"url": "https://example.invalid"},
])
def test_invalid_or_implicit_or_extra_identity_content_is_rejected(feedback, fields):
    assert write(feedback, **fields).status_code == 422
    with Session(feedback.engine) as session: assert session.exec(select(PilotFeedback)).all() == []


def test_fixed_tasks_and_noop_missing_delete_do_not_create_feedback(feedback):
    assert write(feedback, "unknown_task").status_code == 422
    assert request(feedback, "delete", "file_share", params={"expected_revision": 0}).json()["revision"] == 0
    assert request(feedback, "delete", "file_share", params={"expected_revision": 1}).status_code == 409
    with Session(feedback.engine) as session: assert not session.exec(select(PilotFeedback)).all()


@pytest.mark.parametrize("method,task,body", [("get",None,None),("get","summary",None),("put","first_message",{"expected_revision":0,"outcome":"completed","consent":True}),("delete","first_message",None)])
def test_tenant_outsider_denied_and_member_aggregate_denied(feedback,method,task,body):
    kwargs = {"json": body} if body else ({"params":{"expected_revision":0}} if method == "delete" else {})
    assert request(feedback,method,task,"outsider",**kwargs).status_code == 403
    assert request(feedback,method,task,"member",workspace="b",**kwargs).status_code == 403
    assert request(feedback, "get", "summary").status_code == 403


def test_summary_latest_counts_without_individual_notes_or_verified_verdict(feedback, monkeypatch):
    for task in service.TASKS:
        assert write(feedback, task["id"], rating=5, note="PRIVATE participant details").status_code == 200
    assert write(feedback, who="owner", rating=2, outcome="blocked", note="PRIVATE owner note").status_code == 200
    assert write(feedback, expected_revision=1, outcome="blocked", rating=3).status_code == 200
    monkeypatch.setattr(service, "private_note", lambda *_: (_ for _ in ()).throw(AssertionError("aggregate must not decrypt")))
    result = request(feedback, task="summary", who="owner")
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["distinct_participants"] == 2 and data["feedback_count"] == 6
    assert data["self_reported"] is True and data["pilot_verdict"] is None and data["notes_included"] is False
    first = data["tasks"][0]
    assert first == {"task":"first_message","completed":0,"blocked":2,"rating_count":2,"rating_total":5}
    assert set(data) == {"workspace_id","self_reported","pilot_verdict","distinct_participants","feedback_count","tasks","notes_included"}
    for secret in ("PRIVATE", "@", "member", "user_id", "created_at", "encrypted", "owner"):
        assert secret not in result.text


@pytest.mark.parametrize("change", ["removed", "blocked", "workforce_inactive", "workforce_foreign"])
def test_summary_excludes_former_or_inactive_participants(feedback, change):
    assert write(feedback).status_code == 200
    with Session(feedback.engine) as session:
        if change == "removed": session.delete(session.get(WorkspaceMember, ("a", "member")))
        elif change == "blocked":
            row=session.get(User,"member");row.is_blocked=True;session.add(row)
        else: session.add(WorkforcePrincipal(user_id="member", workspace_id="b" if change == "workforce_foreign" else "a", external_id="fixture", active=change != "workforce_inactive"))
        session.commit()
    assert request(feedback, task="summary", who="owner").json()["distinct_participants"] == 0
    assert request(feedback).status_code in {401,403}


def test_gets_are_select_only_no_passive_rows_or_outgoing_provider_calls(feedback):
    statements=[]
    def observed(_c,_cu,sql,*_): statements.append(sql.strip().split()[0].upper())
    event.listen(feedback.engine,"before_cursor_execute",observed)
    try:
        assert request(feedback).status_code == 200
        assert request(feedback,task="summary",who="owner").status_code == 200
    finally: event.remove(feedback.engine,"before_cursor_execute",observed)
    assert statements and set(statements) <= {"SELECT"}
    assert feedback.remote.calls == 0
    with Session(feedback.engine) as session: assert not session.exec(select(PilotFeedback)).all()


def test_note_ciphertext_bound_to_author_task_revision_and_metadata(feedback):
    assert write(feedback,note="PRIVATE original note",rating=4).status_code == 200
    assert write(feedback,who="owner",note="PRIVATE owner note").status_code == 200
    with Session(feedback.engine) as session:
        member=session.get(PilotFeedback,("a","member","first_message"))
        owner=session.get(PilotFeedback,("a","owner","first_message"))
        ciphertext=member.encrypted_note
        assert "PRIVATE" not in ciphertext and service.private_note(member)=="PRIVATE original note"
        owner.encrypted_note=ciphertext;session.add(owner);session.commit()
    response=request(feedback,who="owner")
    assert response.status_code == 503 and "PRIVATE" not in response.text and ciphertext not in response.text
    assert request(feedback,task="summary",who="owner").status_code == 200
    assert request(feedback).json()["feedback"][0]["note"] == "PRIVATE original note"
    with Session(feedback.engine) as session:
        row=session.get(PilotFeedback,("a","member","first_message"));row.rating=5;session.add(row);session.commit()
    assert request(feedback).status_code == 503


@pytest.mark.parametrize("revoke", ["membership", "blocked", "session", "auth_version"])
def test_fresh_actor_and_membership_rechecked_after_writer_lock(feedback, monkeypatch, revoke):
    original=service.lock_workspace
    def changed(session,workspace_id):
        # Commit the independent authority change just before acquisition of
        # the real SQLite/PG writer fence, emulating a revocation while waiting.
        with Session(feedback.engine) as other:
            if revoke=="membership": other.delete(other.get(WorkspaceMember,("a","member")))
            elif revoke=="blocked":
                row=other.get(User,"member");row.is_blocked=True;other.add(row)
            elif revoke=="auth_version":
                row=other.get(AccountSecurity,"member");row.auth_version+=1;other.add(row)
            else:
                row=other.exec(select(ActiveSession).where(ActiveSession.token_hash==token_digest(feedback.tokens["member"]))).one()
                row.revoked_at=datetime.utcnow();other.add(row)
            other.commit()
        original(session,workspace_id)
    monkeypatch.setattr(service,"lock_workspace",changed)
    assert write(feedback,note="must not persist").status_code in {401,403}
    with Session(feedback.engine) as session: assert not session.exec(select(PilotFeedback)).all()


def test_same_workspace_scoped_sso_allowed_and_scope_or_provider_revocation_denied(feedback):
    with Session(feedback.engine) as session:
        session.add(WorkspaceOIDCProvider(workspace_id="a", name="Customer", issuer_url="https://identity.example.invalid",client_id="fixture",created_by="owner",enabled=True))
        session.commit()
        token=create_token("member",session,auth_methods=["sso"],workspace_scope={"workspace_id":"a","provider_revision":1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(token),workspace_id="a",provider_revision=1));session.commit()
    feedback.headers["scoped"]={"Authorization":"Bearer "+token}
    assert write(feedback,who="scoped",note="Same tenant own note").status_code == 200
    assert request(feedback,who="scoped").status_code == 200
    assert request(feedback,who="scoped",workspace="b").status_code == 403
    with Session(feedback.engine) as session:
        row=session.get(WorkspaceOIDCProvider,"a");row.revision+=1;session.add(row);session.commit()
    assert request(feedback,who="scoped").status_code == 401
    assert write(feedback,who="scoped",expected_revision=1).status_code == 401


def test_concurrent_expected_revision_can_commit_only_one_current_report(feedback):
    assert write(feedback).status_code == 200
    with ThreadPoolExecutor(max_workers=2) as workers:
        results=list(workers.map(lambda note: write(feedback, expected_revision=1, note=note), ("writer one", "writer two")))
    assert sorted(result.status_code for result in results)==[200,409]
    state=request(feedback).json()["feedback"]
    assert len(state)==1 and state[0]["revision"]==2 and state[0]["note"] in {"writer one","writer two"}


def test_erasure_remains_possible_at_revision_limit_and_no_stale_revival(feedback):
    assert write(feedback,note="Private limit note").status_code == 200
    with Session(feedback.engine) as session:
        row=session.get(PilotFeedback,("a","member","first_message"));row.revision=service.MAX_REVISION;session.add(row);session.commit()
    assert write(feedback,expected_revision=service.MAX_REVISION).status_code == 409
    result=request(feedback,"delete","first_message",params={"expected_revision":service.MAX_REVISION})
    assert result.status_code==200
    assert write(feedback,expected_revision=service.MAX_REVISION).status_code==409
    assert request(feedback).json()["feedback"]==[]


@pytest.mark.parametrize("change", ["auth_version", "membership", "summary_role"])
def test_authority_revoked_during_read_prevents_note_or_aggregate_response(feedback, change):
    who="owner" if change=="summary_role" else "member"
    assert write(feedback,who=who,note="PRIVATE note must not leak after revocation").status_code==200
    with feedback.engine.connect() as connection: connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    triggered=[]
    def after_query(_c,_cu,statement,*_):
        if triggered or not statement.lstrip().upper().startswith("SELECT") or "FROM pilotfeedback" not in statement:return
        triggered.append(True)
        with Session(feedback.engine) as session:
            if change=="auth_version":
                row=session.get(AccountSecurity,who);row.auth_version+=1;session.add(row)
            elif change=="membership": session.delete(session.get(WorkspaceMember,("a",who)))
            else:
                row=session.get(WorkspaceMember,("a",who));row.role="member";session.add(row)
            session.commit()
    event.listen(feedback.engine,"after_cursor_execute",after_query)
    try: response=request(feedback,who=who,task="summary" if change=="summary_role" else None)
    finally: event.remove(feedback.engine,"after_cursor_execute",after_query)
    assert triggered and response.status_code in {401,403}
    assert "PRIVATE note" not in response.text and "distinct_participants" not in response.text


def test_untracked_token_cannot_bootstrap_a_session_through_read_or_write(feedback):
    digest=token_digest(feedback.tokens["member"])
    with Session(feedback.engine) as session:
        session.delete(session.exec(select(ActiveSession).where(ActiveSession.token_hash==digest)).one());session.commit()
    assert request(feedback).status_code==401
    assert write(feedback).status_code==401
    with Session(feedback.engine) as session:
        assert session.exec(select(ActiveSession).where(ActiveSession.token_hash==digest)).first() is None
        assert not session.exec(select(PilotFeedback)).all()
