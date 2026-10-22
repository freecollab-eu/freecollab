"""Authenticated appearance snapshots with isolated persistent account data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from threading import Barrier

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import preferences
from app.database import get_session
from app.models.preference import UserAppearance
from app.models.user import User
from app.services.auth import create_token, get_current_user, revoke_token, security


@pytest.fixture
def appearance(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'appearance.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            User(id="alice", name="Alice", email="alice@example.com", password_hash="unused"),
            User(id="bob", name="Bob", email="bob@example.com", password_hash="unused"),
        ])
        session.commit()
    app = FastAPI()
    app.include_router(preferences.router)

    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        yield client, engine, app
    engine.dispose()


def bearer(user_id="alice"):
    return {"Authorization": "Bearer " + create_token(user_id)}


def snapshot(**changes):
    return {**preferences.DEFAULT_APPEARANCE, **changes}


def test_read_defaults_is_private_and_does_not_create_a_row(appearance):
    client, engine, _ = appearance
    response = client.get("/preferences/appearance", headers=bearer())
    assert response.status_code == 200
    assert response.json() == preferences.DEFAULT_APPEARANCE
    assert response.headers["Cache-Control"] == "private, no-store"
    with Session(engine) as session:
        assert session.exec(select(UserAppearance)).all() == []


def test_full_snapshot_roundtrips_in_fresh_sessions_and_stays_account_owned(appearance):
    client, engine, _ = appearance
    settings = snapshot(
        mode="dark", accent_color="#ABCDEF", background_color="#FFFFFF",
        dark_background_color="#000000", font_family="serif", font_size="large",
        density="compact", corner_style="square",
    )
    expected = {**settings, "accent_color": "#abcdef", "background_color": "#ffffff"}
    response = client.put("/preferences/appearance", json=settings, headers=bearer())
    assert response.status_code == 200
    assert response.json() == expected
    assert response.headers["Cache-Control"] == "private, no-store"
    with Session(engine) as first_session:
        stored = first_session.get(UserAppearance, "alice")
        assert json.loads(stored.values) == expected
        assert isinstance(stored.updated_at, datetime)
    with Session(engine) as reloaded_session:
        assert json.loads(reloaded_session.get(UserAppearance, "alice").values) == expected
        assert reloaded_session.get(UserAppearance, "bob") is None
    assert client.get("/preferences/appearance", headers=bearer()).json() == expected
    assert client.get("/preferences/appearance", headers=bearer("bob")).json() == snapshot()
    bob_settings = snapshot(font_family="manrope", mode="system")
    assert client.put("/preferences/appearance", json=bob_settings, headers=bearer("bob")).json() == bob_settings
    assert client.get("/preferences/appearance", headers=bearer()).json() == expected
    # A reset is another complete account-owned snapshot.
    assert client.put("/preferences/appearance", json=snapshot(), headers=bearer()).json() == snapshot()
    assert client.get("/preferences/appearance", headers=bearer("bob")).json() == bob_settings


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("version", "1"), ("version", True),
    ("mode", "automatic"), ("mode", None),
    ("accent_color", "#fff"), ("accent_color", "#12345678"),
    ("accent_color", "#12345g"), ("accent_color", " #123456"),
    ("accent_color", "#123456\n"), ("accent_color", 123456),
    ("accent_color", "red; background:url(https://example.com/track)"),
    ("background_color", "url(javascript:alert(1))"),
    ("dark_background_color", "var(--external-color)"),
    ("font_family", "https://example.com/font.woff2"),
    ("font_family", "Arial, sans-serif"),
    ("font_size", "200px"), ("density", "tiny"), ("corner_style", "pill"),
    ("user_id", "bob"), ("font_url", "https://example.com/font.woff2"),
])
def test_invalid_settings_cannot_replace_existing_snapshot(appearance, field, value):
    client, engine, _ = appearance
    original = snapshot(font_family="manrope")
    assert client.put("/preferences/appearance", json=original, headers=bearer()).status_code == 200
    response = client.put("/preferences/appearance", json={**original, field: value}, headers=bearer())
    assert response.status_code == 422
    assert client.get("/preferences/appearance", headers=bearer()).json() == original
    with Session(engine) as session:
        assert session.get(UserAppearance, "bob") is None


def test_put_requires_every_setting(appearance):
    client, _, _ = appearance
    for missing in preferences.DEFAULT_APPEARANCE:
        incomplete = snapshot()
        del incomplete[missing]
        assert client.put("/preferences/appearance", json=incomplete, headers=bearer()).status_code == 422


@pytest.mark.parametrize("method", ["get", "put"])
def test_preferences_require_an_active_unrevoked_account(appearance, method):
    client, engine, _ = appearance
    request = getattr(client, method)
    payload = {"json": snapshot()} if method == "put" else {}
    assert request("/preferences/appearance", **payload).status_code in (401, 403)
    assert request("/preferences/appearance", headers={"Authorization": "Bearer invalid"}, **payload).status_code == 401
    assert request("/preferences/appearance", headers=bearer("missing-user"), **payload).status_code == 401
    revoked_token = create_token("alice")
    with Session(engine) as session:
        revoke_token(revoked_token, session)
    assert request("/preferences/appearance", headers={"Authorization": "Bearer " + revoked_token}, **payload).status_code == 401
    with Session(engine) as session:
        user = session.get(User, "alice")
        user.is_blocked = True
        session.add(user)
        session.commit()
    assert request("/preferences/appearance", headers=bearer(), **payload).status_code == 403
    with Session(engine) as session:
        assert session.exec(select(UserAppearance)).all() == []


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("access_change", ["logout", "logout_everywhere", "blocked"])
def test_save_rechecks_access_after_authentication(appearance, existing, access_change):
    client, engine, app = appearance
    original = snapshot(font_family="manrope")
    if existing:
        with Session(engine) as session:
            session.add(UserAppearance(user_id="alice", values=json.dumps(original)))
            session.commit()
    token = create_token("alice")

    def authenticate_then_change_access(
        credentials=Depends(security), session=Depends(get_session), request: Request = None,
    ):
        actor = get_current_user(credentials, session, request)
        # A separate committed transaction changes authority after the normal
        # dependency accepted this request, before its handler can mutate data.
        with Session(engine) as changed:
            if access_change == "logout":
                revoke_token(token, changed)
            elif access_change == "logout_everywhere":
                from app.services.account_security import revoke_all_sessions
                revoke_all_sessions(changed, actor.id)
            else:
                account = changed.get(User, actor.id)
                account.is_blocked = True
                changed.add(account)
            changed.commit()
        return actor

    app.dependency_overrides[get_current_user] = authenticate_then_change_access
    response = client.put(
        "/preferences/appearance", headers={"Authorization": "Bearer " + token},
        json=snapshot(font_family="serif"),
    )
    assert response.status_code == 401
    with Session(engine) as session:
        stored = session.get(UserAppearance, "alice")
        if existing:
            assert json.loads(stored.values) == original
        else:
            assert stored is None


def test_simultaneous_first_saves_resolve_to_one_valid_snapshot(appearance):
    client, engine, _ = appearance
    start = Barrier(2, timeout=10)
    settings = [snapshot(font_family="serif"), snapshot(font_family="manrope")]

    def save(data):
        # Start both real requests together. Account-row serialization must
        # still return each acknowledged snapshot and persist exactly one row.
        start.wait()
        return client.put("/preferences/appearance", json=data, headers=bearer())

    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(save, settings))
    assert [response.status_code for response in responses] == [200, 200]
    assert [response.json() for response in responses] == settings
    with Session(engine) as session:
        rows = session.exec(select(UserAppearance)).all()
        assert len(rows) == 1
        assert json.loads(rows[0].values) in settings
