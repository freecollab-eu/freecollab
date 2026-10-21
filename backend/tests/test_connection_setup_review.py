"""Independent negative checks for the SELECT-only setup authentication path."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from app.models.session import ActiveSession, RevokedToken
from app.models.user import User
from app.models.workspace_oidc import WorkspaceOIDCSession
from app.services.auth import create_token, token_digest
from tests.test_connection_setup import BASE, PRIVATE, center, fetch
from tests.test_remote_sync import team


@pytest.mark.parametrize("revocation", ["different_tracked_user", "expired_tracking", "revoked_tracking", "revoked_digest", "blocked_account", "missing_tracking"])
def test_setup_rejects_invalid_tracked_identity_without_bootstrapping_or_mutation(center, revocation):
    digest = token_digest(center.tokens["owner"])
    with Session(center.engine) as session:
        tracked = session.exec(select(ActiveSession).where(ActiveSession.token_hash == digest)).one()
        if revocation == "different_tracked_user": tracked.user_id = "member"; session.add(tracked)
        elif revocation == "expired_tracking": tracked.expires_at = datetime.utcnow() - timedelta(seconds=1); session.add(tracked)
        elif revocation == "revoked_tracking": tracked.revoked_at = datetime.utcnow(); session.add(tracked)
        elif revocation == "revoked_digest": session.add(RevokedToken(token_hash=digest, expires_at=tracked.expires_at))
        elif revocation == "blocked_account":
            actor = session.get(User, "owner"); actor.is_blocked = True; session.add(actor)
        else: session.delete(tracked)
        session.commit()
    statements = []
    def capture(_connection, _cursor, statement, _parameters, _context, _many): statements.append(statement)
    event.listen(center.engine, "before_cursor_execute", capture)
    try: response = fetch(center)
    finally: event.remove(center.engine, "before_cursor_execute", capture)
    assert response.status_code == 401, response.text
    assert PRIVATE not in response.text and "recipes" not in response.json()
    assert statements and all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    if revocation == "missing_tracking":
        with Session(center.engine) as session:
            assert session.exec(select(ActiveSession).where(ActiveSession.token_hash == digest)).first() is None


def test_deleted_workspace_scope_sidecar_never_upgrades_a_scoped_token_to_native_setup(center):
    with Session(center.engine) as session:
        token = create_token("owner", session, auth_methods=["sso"], workspace_scope={"workspace_id": "a", "provider_revision": 4})
        scoped = WorkspaceOIDCSession(token_hash=token_digest(token), workspace_id="a", provider_revision=4)
        session.add(scoped); session.commit(); session.delete(scoped); session.commit()
    response = center.client.get(BASE + "?channel_id=room", headers={"Authorization": "Bearer " + token})
    assert response.status_code == 401 and PRIVATE not in response.text
