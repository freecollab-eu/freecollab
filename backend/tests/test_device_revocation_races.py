"""Fresh device-revocation authorization is checked after its account lock."""
from unittest.mock import Mock

import pytest
from sqlmodel import Session

from app.api import secure_messaging
from app.models.account_security import AccountSecurity
from app.models.secure_channel import MatrixAccount, MatrixDeviceRevocation
from app.services.account_security import seal_private
from app.services.crypto import encrypt_message
from .test_secure_messaging import secure


@pytest.mark.parametrize("transition", ["factor", "auth_version"])
def test_device_removal_rechecks_security_changed_while_waiting_for_account_lock(secure, monkeypatch, transition):
    client, engine, users, headers, _ = secure
    owner = users[0]["user_id"]
    with Session(engine) as session:
        if not session.get(MatrixAccount, owner):
            session.add(MatrixAccount(user_id=owner, matrix_user_id="@fc_" + owner + ":local.test", provisioned=True,
                                      encrypted_password=encrypt_message("server-only-test-login", "matrix-auth:" + owner)))
            session.commit()
    remote_delete = Mock()
    monkeypatch.setattr(secure_messaging, "delete_owned_device", remote_delete)
    original_state = secure_messaging.state_for
    original_execute = Session.execute
    held_state = []
    waiting = False
    transitioned = False

    def cache_state(session, user_id):
        nonlocal waiting
        state = original_state(session, user_id)
        # Retain the ORM snapshot explicitly for the factor case: production's
        # discarded weak-reference snapshot currently reloads on its own, while
        # a retained state must be equally safe after the lock is acquired.
        if transition == "factor":
            held_state.append(state)
        waiting = True
        return state

    def execute_after_change(session, statement, *args, **kwargs):
        nonlocal transitioned
        if waiting and not transitioned and getattr(statement, "is_update", False) and statement.table.name == "accountsecurity":
            transitioned = True
            with Session(engine) as concurrent:
                state = concurrent.get(AccountSecurity, owner)
                if transition == "factor":
                    state.totp_secret = seal_private("JBSWY3DPEHPK3PXP", "totp", owner)
                else:
                    # Models the same security-version invalidation performed
                    # by sign-out-all/password/MFA security changes.
                    state.auth_version += 1
                concurrent.add(state)
                concurrent.commit()
        return original_execute(session, statement, *args, **kwargs)

    monkeypatch.setattr(secure_messaging, "state_for", cache_state)
    monkeypatch.setattr(Session, "execute", execute_after_change)
    response = client.post("/secure/devices/D0/revoke", headers=headers[0], json={"password": "secure-test-password"})
    assert transitioned, "Request did not reach the account security lock"
    assert response.status_code == (403 if transition == "factor" else 401), response.text
    remote_delete.assert_not_called()
    with Session(engine) as session:
        assert session.get(MatrixDeviceRevocation, (owner, "D0")) is None


def test_send_rejects_restored_membership_when_recipient_account_was_not_locked(secure, monkeypatch):
    """An unchanged membership hash must not mask a missing recipient lock."""
    import httpx
    from app.models.channel import ChannelMember
    from app.models.workspace import WorkspaceChannel, WorkspaceMember
    from app.services.matrix_bridge import membership_epoch
    client, engine, users, _, channel_id = secure
    returning_peer = users[2]["user_id"]
    with Session(engine) as session:
        workspace_id = session.get(WorkspaceChannel, channel_id).workspace_id
        session.add(WorkspaceMember(workspace_id=workspace_id, user_id=returning_peer, role="guest"))
        session.add(ChannelMember(channel_id=channel_id, user_id=returning_peer))
        session.commit()
        prepared_epoch = membership_epoch(session, channel_id)
        # The sender prepared encryption for A+B+C, then C left before this
        # request captured the accounts it intends to lock.
        session.delete(session.get(ChannelMember, (channel_id, returning_peer)))
        session.commit()
    original_lock = secure_messaging.lock_device_accounts
    captured_locks = []
    upstream_calls = []

    def rejoin_while_waiting(session, user_ids):
        captured_locks.extend(user_ids)
        with Session(engine) as concurrent:
            concurrent.add(ChannelMember(channel_id=channel_id, user_id=returning_peer))
            concurrent.commit()
        original_lock(session, user_ids)

    class Upstream:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def request(self, *args, **kwargs):
            upstream_calls.append((args, kwargs))
            return httpx.Response(200, json={"event_id": "$must-not-send"})

    monkeypatch.setattr(secure_messaging, "lock_device_accounts", rejoin_while_waiting)
    monkeypatch.setattr(secure_messaging.httpx, "Client", lambda **_: Upstream())
    response = client.put("/_matrix/client/v3/rooms/%21bound%3Alocal.test/send/m.room.encrypted/race",
                          headers={"Authorization": "Bearer matrix-test-0", "X-FreeCollab-Membership-Epoch": prepared_epoch},
                          json={"algorithm": "m.megolm.v1.aes-sha2", "ciphertext": "opaque"})
    assert returning_peer not in captured_locks
    with Session(engine) as session:
        assert membership_epoch(session, channel_id) == prepared_epoch
    assert response.status_code == 409, response.text
    assert upstream_calls == []
