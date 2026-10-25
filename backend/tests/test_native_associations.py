"""Actual public route/identity validation; signing ownership remains OS verified."""
import base64
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.native_associations import association_documents, router, verified_source


def test_signing_identities_are_explicit_and_bounded():
    assert association_documents({}) == {}
    env = {"NATIVE_APPLICATION_ID": "eu.example.collab", "NATIVE_APPLE_TEAM_ID": "AB123CD456",
           "NATIVE_ANDROID_CERT_SHA256": "ab:" * 31 + "ab"}
    values = association_documents(env)
    assert values["apple-app-site-association"]["applinks"]["details"][0]["appIDs"] == ["AB123CD456.eu.example.collab"]
    assert values["assetlinks.json"][0]["target"]["sha256_cert_fingerprints"] == [env["NATIVE_ANDROID_CERT_SHA256"].upper()]
    for patch in ({"NATIVE_APPLICATION_ID": "../escape"}, {"NATIVE_APPLE_TEAM_ID": "fake"}, {"NATIVE_ANDROID_CERT_SHA256": "fake"}):
        with pytest.raises(ValueError):
            association_documents({**env, **patch})


def test_resource_links_are_ids_only_and_never_open_redirects():
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        source = {"workspace_id": "workspace", "channel_id": "channel", "kind": "task", "item_id": "task", "project_id": "project"}
        response = client.get("/open", params=source, follow_redirects=False)
        assert response.status_code == 307
        location = response.headers["location"]; assert location.startswith("/#open=")
        assert json.loads(base64.urlsafe_b64decode(location.split("=", 1)[1] + "===")) == source
        for params in ({**source, "next": "https://attacker.example"}, {**source, "token": "secret"}, {**source, "project_id": ""}):
            assert client.get("/open", params=params).status_code == 422
        assert client.get("/open?workspace_id=a&workspace_id=b&channel_id=c&kind=message&item_id=m").status_code == 422
        assert client.get("/.well-known/unsupported").status_code == 404


def test_link_target_types_and_exact_task_project_required():
    base = {"workspace_id": "workspace", "channel_id": "channel", "kind": "task", "item_id": "task"}
    with pytest.raises(ValueError): verified_source(base)
    assert verified_source({**base, "project_id": "project"})["project_id"] == "project"
    for value in ({**base, "kind": "execute"}, {**base, "channel_id": "../other"}, {**base, "item_id": "\nsecret"}):
        with pytest.raises(ValueError): verified_source(value)
