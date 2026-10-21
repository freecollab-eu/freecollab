import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

from app import database
from app.main import app


@pytest.fixture
def pilot(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    monkeypatch.setattr(database, "engine", engine)
    with TestClient(app) as client:
        yield client
    engine.dispose()


def test_pilot_has_database_readiness_and_honest_capabilities(pilot):
    assert pilot.get("/ready").json() == {"status": "ready"}
    capabilities = pilot.get("/capabilities").json()
    assert capabilities["invitations"] is True
    assert capabilities["message_encryption"] == "server-side"
    assert capabilities["file_encryption"] is False
    assert capabilities["experimental_features"] is False
    assert capabilities["demo_ai"] is False


@pytest.mark.parametrize("path", ["/posts/flows/example", "/comments/post/example"])
def test_experimental_routes_not_exposed_in_default_pilot(pilot, path):
    assert pilot.get(path).status_code == 404


def test_external_demo_provider_not_exposed_by_default(pilot):
    assert pilot.post("/demo/chat", json={"message": "private data"}).status_code == 404
    assert pilot.post("/api/demo/chat", json={"message": "private data"}).status_code == 404


def test_core_mentions_and_account_workspace_routes_require_authentication(pilot):
    for path in ("/mentions/me", "/workspaces", "/account/security", "/secure/status"):
        assert pilot.get(path).status_code in {401, 403}


@pytest.mark.parametrize("path", ["/health", "/capabilities", "/auth/me", "/missing-private-route"])
def test_api_successes_and_errors_are_excluded_from_browser_and_shared_caches(pilot, path):
    response = pilot.get(path)
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"
