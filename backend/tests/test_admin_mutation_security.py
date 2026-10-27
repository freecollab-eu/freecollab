"""Installation privilege changes must use current, serialized authority."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import admin
from app.database import get_session
from app.models.user import User
from app.services.account_security import revoke_all_sessions
from app.services.auth import create_token, get_current_user, revoke_token


@pytest.fixture
def installation(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'installation.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([User(id="operator", name="Operator", email="operator@example.com", password_hash="unused", role="admin"),
                         User(id="member", name="Member", email="member@example.com", password_hash="unused")])
        session.commit()
        token = create_token("operator", session)
    app = FastAPI()
    app.include_router(admin.router)

    def sessions():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        yield client, engine, token
    engine.dispose()


@pytest.mark.parametrize("change", ["demoted", "blocked", "logged-out", "security-version"])
def test_account_creation_cannot_restore_privileges_after_authority_changes(installation, monkeypatch, change):
    client, engine, token = installation
    original = admin.hash_password

    def change_authority_during_hash(password):
        result = original(password)
        with Session(engine) as session:
            actor = session.get(User, "operator")
            if change == "demoted":
                actor.role = "user"
                session.add(actor)
            elif change == "blocked":
                actor.is_blocked = True
                session.add(actor)
            elif change == "logged-out":
                revoke_token(token, session)
            else:
                revoke_all_sessions(session, actor.id)
            session.commit()
        return result

    monkeypatch.setattr(admin, "hash_password", change_authority_during_hash)
    response = client.post("/admin/users", headers={"Authorization": "Bearer " + token}, json={
        "name": "Replacement administrator", "email": "replacement@example.com",
        "password": "valid-password", "role": "admin",
    })
    assert response.status_code in {401, 403}
    with Session(engine) as session:
        assert session.exec(select(User).where(User.email == "replacement@example.com")).first() is None


def test_concurrent_admin_creation_cannot_exceed_installation_limit(installation, monkeypatch):
    client, engine, token = installation
    with Session(engine) as session:
        for number in range(admin.MAX_ADMINS - 2):
            session.add(User(id=f"existing-{number}", name="Existing", email=f"existing-{number}@example.com",
                             password_hash="unused", role="admin"))
        session.commit()
        other_token = create_token("existing-0", session)
    barrier = Barrier(2)

    def simultaneous_hash(_):
        barrier.wait(timeout=10)
        return "unused"

    monkeypatch.setattr(admin, "hash_password", simultaneous_hash)

    def create(number):
        credential = [token, other_token][number]
        return client.post("/admin/users", headers={"Authorization": "Bearer " + credential}, json={
            "name": "Administrator", "email": f"new-{number}@example.com",
            "password": "valid-password", "role": "admin",
        }).status_code

    with ThreadPoolExecutor(max_workers=2) as workers:
        statuses = list(workers.map(create, range(2)))
    assert sorted(statuses) == [200, 400]
    with Session(engine) as session:
        assert len(session.exec(select(User).where(User.role == "admin")).all()) == admin.MAX_ADMINS


@pytest.mark.parametrize("operation", ["role", "block"])
@pytest.mark.parametrize("change", ["demoted", "blocked", "logged-out"])
def test_user_management_rechecks_after_dependency_authorization(installation, operation, change):
    client, engine, token = installation

    def change_after_authorization(user=Depends(get_current_user), session=Depends(get_session)):
        admin.require_admin(user, session)
        with Session(engine) as other:
            if change == "logged-out":
                revoke_token(token, other)
            else:
                actor = other.get(User, user.id)
                if change == "demoted":
                    actor.role = "user"
                else:
                    actor.is_blocked = True
                other.add(actor)
            other.commit()
        return user

    dependency = admin.require_admin if operation == "role" else admin.require_staff
    client.app.dependency_overrides[dependency] = change_after_authorization
    response = client.patch(f"/admin/users/member/{operation}", params={"role": "admin"},
                            headers={"Authorization": "Bearer " + token})
    assert response.status_code in {401, 403}
    with Session(engine) as session:
        member = session.get(User, "member")
        assert member.role == "user" and not member.is_blocked


def test_current_staff_can_manage_users_and_moderators_cannot_block_staff(installation):
    client, engine, token = installation
    headers = {"Authorization": "Bearer " + token}
    assert client.patch("/admin/users/member/role", params={"role": "admin"}, headers=headers).status_code == 200
    assert client.patch("/admin/users/member/block", headers=headers).status_code == 200
    assert client.patch("/admin/users/member/block", headers=headers).status_code == 200
    with Session(engine) as session:
        actor = session.get(User, "operator")
        actor.role = "moderator"
        session.add(actor)
        session.commit()
    assert client.patch("/admin/users/member/block", headers=headers).status_code == 403
    with Session(engine) as session:
        assert not session.get(User, "member").is_blocked
