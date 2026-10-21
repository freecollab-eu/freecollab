"""Customer OAuth scopes, source authority, one-use consent and token rotation."""
from datetime import datetime, timedelta
import json
import logging
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import bcrypt
from fastapi import HTTPException
import pytest
from sqlmodel import Session, select

from app.api import app_platform, enterprise_search, personal_oauth
from app.config import settings
from app.models.app_platform import AppInstallation, AppJob
from app.models.enterprise_search import EnterpriseSearchConnection
from app.models.personal_oauth import PersonalOAuthAttempt, PersonalOAuthClient, PersonalOAuthGrant
from app.models.session import ActiveSession
from app.models.user import User
from app.models.workspace import WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.services import app_platform as apps, personal_oauth as service, search_providers
from app.services.auth import revoke_token, token_digest
from app.services.capability_logs import CapabilityLogFilter
from tests.test_project_tasks import board, headers

REAL_TRANSPORT_REQUEST = service.transport.request

BASE = "/workspaces/wa/personal-oauth"
PASSWORD = "synthetic-operator-password"
CLIENT_SECRET = "fixture-customer-client-secret"
TENANT = "01234567-89ab-cdef-0123-456789abcdef"


@pytest.fixture
def oauth(board, monkeypatch):
    for router in (personal_oauth.router, enterprise_search.router, app_platform.router):
        board.client.app.include_router(router)
    monkeypatch.setattr(settings, "public_app_url", "http://localhost:3000")
    monkeypatch.setattr(apps.webhooks, "public_addresses", lambda host: ["8.8.8.8"])
    with Session(board.engine) as session:
        user = session.get(User, "owner")
        user.password_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
        session.add(user); session.commit()
    state = SimpleNamespace(requests=[], hook=None, scope=None, token_error=None, access="synthetic-personal-access", refresh="synthetic-personal-refresh")
    def request(method, url, **kwargs):
        assert method == "POST"
        payload = parse_qs(kwargs["body"].decode())
        state.requests.append((url, payload, kwargs.get("headers")))
        if state.hook:
            hook, state.hook = state.hook, None
            hook()
        if state.token_error:
            raise service.transport.ProviderError(state.token_error, status=400)
        value = {"access_token": state.access, "refresh_token": state.refresh,
                 "token_type": "Bearer", "expires_in": 3600}
        if state.scope is not None: value["scope"] = state.scope
        return service.transport.Response(200, {}, json.dumps(value).encode())
    monkeypatch.setattr(service.transport, "request", request)
    board.provider = state
    return board


def registration(oauth, provider="google", purpose="search", **changes):
    data = {"client_id": "customer-app-client", "tenant_id": TENANT if provider == "microsoft" else "",
        "client_secret": CLIENT_SECRET, "password": PASSWORD, "expected_revision": 0}
    data.update(changes)
    result = oauth.client.put(BASE + "/clients/" + provider + "/" + purpose, headers=headers(oauth), json=data)
    assert result.status_code == 200, result.text
    return result.json()


def start(oauth, provider="google", who="peer", purpose="search", **changes):
    body = {"purpose": purpose, "name": "My private provider", "consent": True,
        "config": {"folder_id": "chosen-folder", **({"drive_id": "chosen-drive"} if provider == "microsoft" else {})}}
    body.update(changes)
    return oauth.client.post(BASE + "/" + provider + "/start", headers=headers(oauth, who), json=body)


def complete(oauth, initiated):
    payload = initiated.json() if hasattr(initiated, "json") else initiated
    state = parse_qs(urlsplit(payload["authorization_url"]).query)["state"][0]
    return oauth.client.get("/personal-oauth/callback", params={"state": state, "code": "synthetic-authorization-code"}, follow_redirects=False)


def connected(oauth, provider="google", who="peer", **changes):
    registration(oauth, provider)
    initiated = start(oauth, provider, who=who, **changes)
    assert initiated.status_code == 200, initiated.text
    result = complete(oauth, initiated)
    assert "provider_connected=" in result.headers["location"], result.headers
    with Session(oauth.engine) as session:
        grant = session.get(PersonalOAuthGrant, initiated.json()["grant_id"])
        return grant.id, grant.target_id


def expire(oauth, grant_id):
    with Session(oauth.engine) as session:
        row = session.get(PersonalOAuthGrant, grant_id)
        row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        session.add(row); session.commit()


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_customer_app_real_scope_pkce_and_secret_storage(oauth, provider):
    registration(oauth, provider)
    initiated = start(oauth, provider)
    assert initiated.status_code == 200, initiated.text
    query = parse_qs(urlsplit(initiated.json()["authorization_url"]).query)
    assert query["code_challenge_method"] == ["S256"] and len(query["code_challenge"][0]) == 43
    assert query["scope"][0].split()[0] == service.scope(provider, "search")
    assert "Write" not in query["scope"][0] and "client_secret" not in query
    assert complete(oauth, initiated).status_code == 303
    url, payload, outgoing = oauth.provider.requests[0]
    assert url == ("https://oauth2.googleapis.com/token" if provider == "google" else "https://login.microsoftonline.com/" + TENANT + "/oauth2/v2.0/token")
    assert payload["grant_type"] == ["authorization_code"] and payload["client_secret"] == [CLIENT_SECRET]
    assert len(payload["code_verifier"][0]) >= 43 and outgoing["Content-Type"] == "application/x-www-form-urlencoded"
    with Session(oauth.engine) as session:
        client = session.exec(select(PersonalOAuthClient)).one()
        grant = session.exec(select(PersonalOAuthGrant)).one()
        attempt = session.exec(select(PersonalOAuthAttempt)).one()
        target = session.get(EnterpriseSearchConnection, grant.target_id)
        assert CLIENT_SECRET not in client.encrypted_secret and oauth.provider.access not in grant.encrypted_tokens
        assert oauth.provider.refresh not in grant.encrypted_tokens and oauth.tokens["peer"] not in grant.encrypted_source_claims
        assert target.encrypted_credentials == "" and attempt.encrypted_verifier == ""
        assert grant.source_hash == token_digest(oauth.tokens["peer"])
    overview = oauth.client.get(BASE, headers=headers(oauth, "peer")).json()
    assert overview["grants"][0]["connected"] and overview["can_manage"] is False
    serialized = json.dumps(overview)
    for secret in (CLIENT_SECRET, oauth.provider.access, oauth.provider.refresh, payload["code_verifier"][0]):
        assert secret not in serialized


def test_registration_owner_reauthentication_revision_and_cross_workspace_fences(oauth):
    data = {"client_id": "app", "client_secret": CLIENT_SECRET, "password": PASSWORD}
    assert oauth.client.put(BASE + "/clients/google/search", headers=headers(oauth,"peer"), json=data).status_code == 403
    assert oauth.client.put(BASE + "/clients/google/search", headers=headers(oauth), json={**data,"password":"wrong"}).status_code == 403
    assert oauth.client.put(BASE + "/clients/google/search", headers=headers(oauth,"scoped"), json=data).status_code == 403
    assert oauth.client.put(BASE + "/clients/microsoft/search", headers=headers(oauth), json={**data,"tenant_id":"common/evil"}).status_code == 422
    assert oauth.client.get("/workspaces/wb/personal-oauth", headers=headers(oauth,"peer")).status_code == 403
    registration(oauth)
    assert oauth.client.put(BASE + "/clients/google/search", headers=headers(oauth), json=data).status_code == 409
    response = oauth.client.put(BASE + "/clients/google/search", headers=headers(oauth), json={**data,"client_secret":12345})
    assert response.status_code == 422 and CLIENT_SECRET not in response.text and "12345" not in response.text


def test_start_requires_installed_customer_registration_and_explicit_consent(oauth):
    assert start(oauth).status_code == 409
    registration(oauth)
    assert start(oauth, consent=False).status_code == 422
    assert start(oauth, config={"folder_id":"chosen", "token_url":"https://evil.example/token"}).status_code == 422
    assert start(oauth, installation_id="unrelated").status_code == 422
    assert start(oauth, who="foreign").status_code == 403


def test_native_renderer_gets_browser_setup_guidance_and_can_use_existing_browser_grant(oauth,monkeypatch):
    import base64
    import hashlib
    import secrets
    from app.api import native_clients
    grant_id,target=connected(oauth)
    oauth.client.app.include_router(native_clients.router)
    verifier=secrets.token_urlsafe(48)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    callback="freecollab://native/complete"
    issued=oauth.client.post("/native-clients/connect",headers=headers(oauth,"peer"),json={
        "code_challenge":challenge,"redirect_uri":callback,"client_kind":"mobile"})
    assert issued.status_code==200,issued.text
    child=oauth.client.post("/native-clients/exchange",json={"code":issued.json()["code"],
        "code_verifier":verifier,"redirect_uri":callback})
    assert child.status_code==200,child.text
    oauth.tokens["native"]=child.json()["access_token"]
    refused=start(oauth,who="native")
    assert refused.status_code==409 and "system browser" in refused.text
    monkeypatch.setattr(search_providers,"search",lambda *args:{"items":[],"complete":True})
    query=oauth.client.post("/workspaces/wa/enterprise-search/connections/"+target+"/query",headers=headers(oauth,"native"),
        json={"query":"explicit native query","connection_revision":1,"share_query_with_provider":True})
    assert query.status_code==200,query.text
    with Session(oauth.engine) as session:
        assert len(session.exec(select(PersonalOAuthGrant)).all())==1
        assert session.get(PersonalOAuthGrant,grant_id).status=="active"


def test_start_rechecks_source_after_waiting_for_erasure_owner_lock(oauth,monkeypatch):
    registration(oauth)
    original=service.lock_owner
    def closure_wins(session,user_id):
        if user_id=="peer":
            with Session(oauth.engine) as other:
                service.close_account_resources(other,user_id)
                revoke_token(oauth.tokens["peer"],other)
                other.commit()
        original(session,user_id)
    monkeypatch.setattr(service,"lock_owner",closure_wins)
    response=start(oauth)
    assert response.status_code==401
    with Session(oauth.engine) as session:
        assert not session.exec(select(PersonalOAuthGrant)).all()
        assert not session.exec(select(PersonalOAuthAttempt)).all()


def test_read_only_search_and_write_actions_require_separate_customer_clients(oauth):
    registration(oauth)
    response=oauth.client.put(BASE+"/clients/google/calendar-action",headers=headers(oauth),json={
        "client_id":"customer-app-client","client_secret":CLIENT_SECRET,"password":PASSWORD})
    assert response.status_code==422
    registration(oauth,purpose="calendar-action",client_id="separate-calendar-client")


@pytest.mark.parametrize("change", ["cookie", "replay", "expiry", "logout", "member", "registration"])
def test_one_use_callback_rejects_browser_source_and_registration_changes(oauth, change):
    registration(oauth)
    initiated = start(oauth)
    assert initiated.status_code == 200
    if change == "cookie": oauth.client.cookies.clear()
    elif change == "replay": assert "provider_connected" in complete(oauth, initiated).headers["location"]
    elif change == "expiry":
        with Session(oauth.engine) as session:
            attempt = session.exec(select(PersonalOAuthAttempt)).one(); attempt.expires_at = datetime.utcnow()-timedelta(seconds=1)
            session.add(attempt); session.commit()
    elif change == "logout":
        with Session(oauth.engine) as session: revoke_token(oauth.tokens["peer"], session)
    elif change == "member":
        with Session(oauth.engine) as session:
            session.delete(session.get(WorkspaceMember,("wa","peer"))); session.commit()
    else: registration(oauth, expected_revision=1, client_id="replacement-app")
    before = len(oauth.provider.requests)
    result = complete(oauth, initiated)
    assert "provider_error=" in result.headers["location"] and "synthetic-authorization-code" not in result.headers["location"]
    assert len(oauth.provider.requests) == before


@pytest.mark.parametrize("change", ["logout", "membership", "registration", "grant"])
def test_provider_reply_cannot_resurrect_consent_after_concurrent_revocation(oauth, change):
    registration(oauth)
    initiated = start(oauth)
    grant_id = initiated.json()["grant_id"]
    def revoke():
        with Session(oauth.engine) as session:
            if change == "logout": revoke_token(oauth.tokens["peer"], session)
            elif change == "membership": session.delete(session.get(WorkspaceMember,("wa","peer")))
            elif change == "registration":
                client = session.exec(select(PersonalOAuthClient)).one(); client.revision += 1; session.add(client)
            else: service.invalidate(session, session.get(PersonalOAuthGrant, grant_id))
            session.commit()
    oauth.provider.hook = revoke
    assert "provider_error=" in complete(oauth, initiated).headers["location"]
    with Session(oauth.engine) as session:
        assert session.get(PersonalOAuthGrant, grant_id).encrypted_tokens == ""
        assert session.exec(select(EnterpriseSearchConnection)).all() == []


def test_scope_validation_rejects_broader_or_missing_search_permission(oauth):
    registration(oauth)
    for returned in ("https://www.googleapis.com/auth/drive", "openid", service.scope("google","search") + " https://www.googleapis.com/auth/calendar.events"):
        oauth.provider.scope = returned
        assert "provider_error=" in complete(oauth, start(oauth)).headers["location"]
    with Session(oauth.engine) as session:
        assert not session.exec(select(EnterpriseSearchConnection)).all()


def test_search_uses_refreshed_token_persists_rotation_and_current_source(oauth, monkeypatch):
    grant_id, target = connected(oauth)
    expire(oauth, grant_id)
    oauth.provider.access, oauth.provider.refresh = "rotated-access-token", "rotated-refresh-token"
    seen = []
    def search(provider, config, token, query, limit):
        seen.append(token); return {"items":[],"complete":True}
    monkeypatch.setattr(search_providers, "search", search)
    response = oauth.client.post("/workspaces/wa/enterprise-search/connections/"+target+"/query", headers=headers(oauth,"peer"),
        json={"query":"private query", "connection_revision":1, "share_query_with_provider":True})
    assert response.status_code == 200, response.text
    assert seen == ["rotated-access-token"]
    assert oauth.provider.requests[-1][1]["refresh_token"] == ["synthetic-personal-refresh"]
    with Session(oauth.engine) as session:
        row = session.get(PersonalOAuthGrant,grant_id)
        assert service.tokens(row)["refresh_token"] == "rotated-refresh-token" and row.refresh_claim is None
        assert row.revision == 2
        revoke_token(oauth.tokens["peer"],session)
    with Session(oauth.engine) as session:
        with pytest.raises(HTTPException): service.access_token(session, grant_id)
    assert len(seen) == 1


def test_refresh_401_retries_read_once_and_rotation_survives_subsequent_read_failure(oauth, monkeypatch):
    grant_id, target = connected(oauth)
    calls = []
    def fail(provider, config, token, query, limit):
        calls.append(token)
        if len(calls) == 1:
            oauth.provider.access, oauth.provider.refresh = "fresh-after-401", "refresh-after-401"
            raise search_providers.ProviderError("reauthorization_required",status=401)
        raise search_providers.ProviderError("provider_unavailable",status=503)
    monkeypatch.setattr(search_providers,"search",fail)
    response=oauth.client.post("/workspaces/wa/enterprise-search/connections/"+target+"/query",headers=headers(oauth,"peer"),
        json={"query":"private", "connection_revision":1,"share_query_with_provider":True})
    assert response.status_code == 422 and calls == ["synthetic-personal-access","fresh-after-401"]
    with Session(oauth.engine) as session:
        assert service.tokens(session.get(PersonalOAuthGrant,grant_id))["refresh_token"] == "refresh-after-401"


@pytest.mark.parametrize("change", ["logout", "registration", "revocation"])
def test_rotating_refresh_cannot_restore_secrets_after_inflight_change(oauth, change):
    grant_id, _ = connected(oauth); expire(oauth,grant_id)
    def mutate():
        with Session(oauth.engine) as session:
            if change == "logout": revoke_token(oauth.tokens["peer"],session)
            elif change == "registration":
                client=session.exec(select(PersonalOAuthClient)).one();client.revision+=1;session.add(client)
            else: service.invalidate(session,session.get(PersonalOAuthGrant,grant_id))
            session.commit()
    oauth.provider.hook=mutate
    with Session(oauth.engine) as session:
        with pytest.raises(HTTPException):service.access_token(session,grant_id)
    with Session(oauth.engine) as session:
        assert session.get(PersonalOAuthGrant,grant_id).encrypted_tokens == ""


def test_refresh_claim_serializes_competitors_and_abandoned_rotation_requires_new_consent(oauth):
    grant_id,_=connected(oauth);expire(oauth,grant_id)
    with Session(oauth.engine) as session:
        row=session.get(PersonalOAuthGrant,grant_id);row.refresh_claim="another-request";row.refresh_started_at=datetime.utcnow();session.add(row);session.commit()
    before=len(oauth.provider.requests)
    with Session(oauth.engine) as session:
        with pytest.raises(HTTPException) as busy:service.access_token(session,grant_id)
        assert busy.value.status_code==409
        row=session.get(PersonalOAuthGrant,grant_id);row.refresh_started_at=datetime.utcnow()-timedelta(seconds=61);session.add(row);session.commit()
    with Session(oauth.engine) as session:
        with pytest.raises(HTTPException):service.access_token(session,grant_id)
    with Session(oauth.engine) as session:
        row=session.get(PersonalOAuthGrant,grant_id)
        assert row.status=="reauthorization_required" and not row.encrypted_tokens
    assert len(oauth.provider.requests)==before


def test_actual_competing_refresh_requests_use_one_rotation_lease(oauth,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    grant_id,_=connected(oauth);expire(oauth,grant_id)
    original=service.configuration;barrier=Barrier(2)
    def synchronized_configuration(client,**kwargs):
        result=original(client,**kwargs)
        barrier.wait(timeout=15)
        return result
    monkeypatch.setattr(service,"configuration",synchronized_configuration)
    def refresh():
        with Session(oauth.engine) as session:
            try:return service.access_token(session,grant_id)
            except HTTPException as error:return error.status_code
    before=len(oauth.provider.requests)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(lambda _:refresh(),range(2)))
    assert sorted(str(value) for value in results)==["409","synthetic-personal-access"]
    assert len(oauth.provider.requests)==before+1


@pytest.mark.parametrize("provider",["google","microsoft"])
def test_revocation_erases_local_tokens_and_other_people_cannot_disconnect(oauth,provider):
    grant_id,target=connected(oauth,provider)
    path=BASE+"/grants/"+grant_id
    assert oauth.client.delete(path,headers=headers(oauth)).status_code==404
    before=len(oauth.provider.requests)
    response=oauth.client.delete(path,headers=headers(oauth,"peer"))
    assert response.status_code==200 and response.json()["local_credentials_erased"]
    listed=oauth.client.get("/workspaces/wa/enterprise-search/connections",headers=headers(oauth,"peer"))
    assert listed.status_code==200 and listed.json()==[]
    if provider=="google":
        assert response.json()["provider_revocation"]=="requested"
        assert oauth.provider.requests[-1][0]=="https://oauth2.googleapis.com/revoke"
        assert oauth.provider.requests[-1][1]=={"token":["synthetic-personal-refresh"]}
    else:
        assert len(oauth.provider.requests)==before and response.json()["provider_revocation"]=="not-supported"
    with Session(oauth.engine) as session:
        row=session.get(PersonalOAuthGrant,grant_id)
        assert row.encrypted_tokens==row.encrypted_source_claims=="" and row.status=="revoked"
        with pytest.raises(HTTPException):service.access_token(session,grant_id)


def test_search_disconnect_also_erases_oauth_refresh_and_requests_google_revoke(oauth):
    grant_id,target=connected(oauth)
    response=oauth.client.delete("/workspaces/wa/enterprise-search/connections/"+target,headers=headers(oauth,"peer"))
    assert response.status_code==200 and response.json()=={"disconnected":True,"provider_revocation":"requested"}
    assert oauth.provider.requests[-1][0]=="https://oauth2.googleapis.com/revoke"
    with Session(oauth.engine) as session:
        assert session.get(EnterpriseSearchConnection,target) is None
        row=session.get(PersonalOAuthGrant,grant_id)
        assert row.status=="revoked" and not row.encrypted_tokens and not row.encrypted_source_claims


def test_scoped_search_consent_retains_provider_revision_and_missing_sidecar_fails_closed(oauth):
    grant_id,_=connected(oauth,who="scoped")
    overview=oauth.client.get(BASE,headers=headers(oauth,"scoped")).json()
    assert overview["purposes"]==["search"] and not overview["can_manage"]
    with Session(oauth.engine) as session:
        session.delete(session.get(WorkspaceOIDCSession,token_digest(oauth.tokens["scoped"])))
        session.commit()
    with Session(oauth.engine) as session:
        with pytest.raises(HTTPException):service.access_token(session,grant_id)


@pytest.mark.parametrize("provider",["google","microsoft"])
def test_installed_calendar_actions_use_invoking_persons_oauth_and_never_blind_retry(oauth,provider):
    registration(oauth,provider,purpose="calendar-action")
    recipe=next(row for row in apps.catalog() if row["id"]==provider+".calendar-event")
    path="/workspaces/wa/apps"
    payload={"manifest":recipe["manifest"],"channel_ids":["a"],"endpoint":recipe["endpoint_example"],"authorization":""}
    bad=oauth.client.post(path,headers=headers(oauth),json={**payload,"endpoint":"https://evil.example/"})
    assert bad.status_code==422
    bad=oauth.client.post(path,headers=headers(oauth),json={**payload,"authorization":"Bearer synthetic-manual-token"})
    assert bad.status_code==422
    installed=oauth.client.post(path,headers=headers(oauth),json=payload)
    assert installed.status_code==201,installed.text
    app_id=installed.json()["installation"]["id"]
    invoke=path+"/"+app_id+"/commands"
    data={"request_id":"calendar-action-123","channel_id":"a","command":"create-event","arguments":{
        "title":"Reviewed private event","start":"2026-12-01T10:00:00+01:00","end":"2026-12-01T11:00:00+01:00"},"confirm":True,"disclose_to_app":True}
    assert oauth.client.post(invoke,headers=headers(oauth,"peer"),json=data).status_code==409
    initiated=start(oauth,provider,purpose="calendar-action",config={},installation_id=app_id,installation_generation=1)
    assert initiated.status_code==200,initiated.text
    assert "provider_connected=" in complete(oauth,initiated).headers["location"]
    queued=oauth.client.post(invoke,headers=headers(oauth,"peer"),json=data)
    assert queued.status_code==202,queued.text
    captured=[]
    def sender(url,body,headers):
        captured.append((url,json.loads(body),headers))
        raise OSError("simulated lost provider acknowledgement")
    with Session(oauth.engine) as session:
        assert apps.run_pending(session,send=sender)==0
        job=session.get(AppJob,queued.json()["id"])
        assert job.status=="uncertain"
        assert apps.run_pending(session,send=sender)==0
    assert len(captured)==1 and captured[0][0]==recipe["endpoint_example"]
    assert captured[0][2]["Authorization"]=="Bearer synthetic-personal-access"
    request=captured[0][1]
    assert "attendees" not in request and "channel_id" not in request
    assert request["start"]["dateTime"] in {"2026-12-01T09:00:00+00:00","2026-12-01T09:00:00"}
    # Another member cannot borrow this consent to create an event in peer's account.
    assert oauth.client.post(invoke,headers=headers(oauth),json={**data,"request_id":"another-user-request"}).status_code==409


def calendar_app(oauth):
    registration(oauth,purpose="calendar-action")
    recipe=next(row for row in apps.catalog() if row["id"]=="google.calendar-event")
    created=oauth.client.post("/workspaces/wa/apps",headers=headers(oauth),json={"manifest":recipe["manifest"],
        "channel_ids":["a"],"endpoint":recipe["endpoint_example"],"authorization":""})
    assert created.status_code==201
    app_id=created.json()["installation"]["id"]
    initiated=start(oauth,purpose="calendar-action",config={},installation_id=app_id,installation_generation=1)
    assert initiated.status_code==200 and "provider_connected=" in complete(oauth,initiated).headers["location"]
    data={"request_id":"immutable-consent-action","channel_id":"a","command":"create-event","arguments":{
        "title":"Approved for original provider account","start":"2026-12-01T10:00:00Z","end":"2026-12-01T11:00:00Z"},
        "confirm":True,"disclose_to_app":True}
    return app_id,initiated.json()["grant_id"],data


@pytest.mark.parametrize("transition",["revoke-and-reconsent","replace-consent"])
def test_queued_write_is_bound_to_exact_approved_consent_and_never_rebound(oauth,transition):
    app_id,original_grant,data=calendar_app(oauth)
    path="/workspaces/wa/apps/"+app_id+"/commands"
    queued=oauth.client.post(path,headers=headers(oauth,"peer"),json=data)
    assert queued.status_code==202
    with Session(oauth.engine) as session:
        job=session.get(AppJob,queued.json()["id"])
        payload=json.loads(apps.unseal_private(job.encrypted_payload,"app-job",job.id))
        assert payload["provider_grant_id"]==original_grant
    if transition=="revoke-and-reconsent":
        assert oauth.client.delete(BASE+"/grants/"+original_grant,headers=headers(oauth,"peer")).status_code==200
    oauth.provider.access="different-provider-account-token"
    replacement=start(oauth,purpose="calendar-action",config={},installation_id=app_id,installation_generation=1)
    assert replacement.status_code==200
    assert "provider_connected=" in complete(oauth,replacement).headers["location"]
    assert replacement.json()["grant_id"]!=original_grant
    # Idempotency includes approved consent, so a reused request identifier
    # cannot silently become the same action in another provider account.
    reused=oauth.client.post(path,headers=headers(oauth,"peer"),json=data)
    assert reused.status_code==409
    effects=[]
    def sender(url,body,headers):effects.append(headers["Authorization"]);return 201,None
    with Session(oauth.engine) as session:
        assert apps.run_pending(session,send=sender)==0
        job=session.get(AppJob,queued.json()["id"])
        assert job.status=="cancelled" and job.encrypted_payload==""
    assert effects==[]
    approved=oauth.client.post(path,headers=headers(oauth,"peer"),json={**data,"request_id":"newly-reviewed-consent-action"})
    assert approved.status_code==202
    with Session(oauth.engine) as session:
        assert apps.run_pending(session,send=sender)==1
    assert effects==["Bearer different-provider-account-token"]


def test_queued_write_keeps_same_consent_across_legitimate_token_rotation(oauth):
    app_id,grant_id,data=calendar_app(oauth)
    queued=oauth.client.post("/workspaces/wa/apps/"+app_id+"/commands",headers=headers(oauth,"peer"),json=data)
    assert queued.status_code==202
    expire(oauth,grant_id)
    oauth.provider.access,oauth.provider.refresh="same-consent-fresh-access","same-consent-fresh-refresh"
    effects=[]
    with Session(oauth.engine) as session:
        assert apps.run_pending(session,send=lambda url,body,headers:(effects.append(headers["Authorization"]) or 201,None))==1
        assert session.get(PersonalOAuthGrant,grant_id).revision==2
        assert session.get(AppJob,queued.json()["id"]).status=="delivered"
    assert effects==["Bearer same-consent-fresh-access"]


def test_closure_erases_grants_registration_secret_and_pending_verifiers(oauth):
    grant_id,_=connected(oauth)
    assert start(oauth).status_code==200
    with Session(oauth.engine) as session:
        service.close_account_resources(session,"owner");session.commit()
        client=session.exec(select(PersonalOAuthClient)).one()
        assert not client.enabled and client.encrypted_secret==""
        assert all(row.encrypted_tokens==row.encrypted_source_claims=="" for row in session.exec(select(PersonalOAuthGrant)).all())
        assert all(row.encrypted_verifier=="" for row in session.exec(select(PersonalOAuthAttempt)).all())


def test_disable_registration_erases_shared_secret_and_invalidates_pending_consent(oauth):
    grant_id,_=connected(oauth)
    pending=start(oauth)
    response=oauth.client.post(BASE+"/clients/google/search/disable",headers=headers(oauth),json={"password":PASSWORD})
    assert response.status_code==200 and response.json()["local_credentials_erased"]
    with Session(oauth.engine) as session:
        client=session.exec(select(PersonalOAuthClient)).one()
        assert client.enabled is False and client.encrypted_secret=="" and client.revision==2
        assert all(not row.encrypted_tokens and not row.encrypted_source_claims for row in session.exec(select(PersonalOAuthGrant)).all())
        assert all(not row.encrypted_verifier for row in session.exec(select(PersonalOAuthAttempt)).all())
    assert "provider_error=" in complete(oauth,pending).headers["location"]
    assert start(oauth).status_code==409


def test_worker_cleanup_erases_expired_source_credentials_and_scans_fairly(oauth):
    grant_id,_=connected(oauth)
    pending=start(oauth)
    with Session(oauth.engine) as session:
        revoke_token(oauth.tokens["peer"],session)
        attempt=session.exec(select(PersonalOAuthAttempt).where(PersonalOAuthAttempt.grant_id==pending.json()["grant_id"])).one()
        attempt.expires_at=datetime.utcnow()-timedelta(seconds=1);session.add(attempt);session.commit()
        assert service.reap_grants(session,limit=1)==1
        assert service.reap_grants(session,limit=1)==1
        assert service.reap_grants(session,limit=1)==0
        assert all(row.status=="consent_expired" and row.encrypted_tokens==row.encrypted_source_claims=="" for row in session.exec(select(PersonalOAuthGrant)).all())
        assert all(row.encrypted_verifier=="" for row in session.exec(select(PersonalOAuthAttempt)).all())


def test_temporary_key_unavailability_is_not_treated_as_grant_revocation(oauth,monkeypatch):
    grant_id,_=connected(oauth)
    def unavailable(*args,**kwargs):raise HTTPException(503,"Fixture key unavailable")
    monkeypatch.setattr(service,"unseal_private",unavailable)
    with Session(oauth.engine) as session:
        with pytest.raises(HTTPException) as error:service.reap_grants(session)
        assert error.value.status_code==503
        session.rollback()
        row=session.get(PersonalOAuthGrant,grant_id)
        assert row.status=="active" and row.encrypted_tokens and row.encrypted_source_claims


def test_callback_expiring_during_exchange_cannot_store_provider_credentials(oauth):
    registration(oauth);initiated=start(oauth)
    def expire_attempt():
        with Session(oauth.engine) as session:
            attempt=session.exec(select(PersonalOAuthAttempt)).one();attempt.expires_at=datetime.utcnow()-timedelta(seconds=1)
            session.add(attempt);session.commit()
    oauth.provider.hook=expire_attempt
    assert "provider_error=" in complete(oauth,initiated).headers["location"]
    with Session(oauth.engine) as session:
        assert session.exec(select(PersonalOAuthGrant)).one().encrypted_tokens==""


def test_callback_access_logging_redacts_query_without_changing_uvicorn_shape():
    args=("peer","GET","/api/personal-oauth/callback?code=private-code&state=private-state","1.1",303)
    record=logging.LogRecord("uvicorn.access",logging.INFO,"",0,"%s %s %s %s %s",args,None)
    assert CapabilityLogFilter().filter(record)
    assert len(record.args)==5 and record.args[2]=="/api/personal-oauth/callback?[redacted]"
    assert "private-code" not in record.getMessage() and "private-state" not in record.getMessage()


def test_security_actions_are_metadata_only_and_anonymous_callback_is_excluded():
    from app.services.security_audit import action_for
    assert action_for("PUT","/workspaces/{workspace_id}/personal-oauth/clients/{provider}/{purpose}")=="provider.application_configure"
    assert action_for("POST","/workspaces/{workspace_id}/personal-oauth/clients/{provider}/{purpose}/disable")=="provider.application_disable"
    assert action_for("POST","/workspaces/{workspace_id}/personal-oauth/{provider}/start")=="provider.consent_start"
    assert action_for("DELETE","/workspaces/{workspace_id}/personal-oauth/grants/{grant_id}")=="provider.consent_revoke"
    assert action_for("GET","/personal-oauth/callback") is None


@pytest.fixture
def provider_https(oauth, tmp_path, monkeypatch):
    """Real TLS + hostname verification + CONNECT, with only fixture DNS routing."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    import select as socket_select
    import socket
    import socketserver
    import ssl
    import subprocess
    import threading
    hosts = {"oauth2.googleapis.com", "login.microsoftonline.com", "www.googleapis.com", "graph.microsoft.com"}
    key, certificate = tmp_path / "oauth-private.pem", tmp_path / "oauth-certificate.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
        "-out", str(certificate), "-days", "1", "-subj", "/CN=oauth2.googleapis.com", "-addext",
        "subjectAltName=" + ",".join("DNS:" + host for host in sorted(hosts))], check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state = {"requests": [], "connects": [], "sni": [], "scope": None, "mode": "normal", "refreshes": 0, "effects": []}
    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def reply(self, code, value):
            body = json.dumps(value).encode(); self.send_response(code)
            self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            host = self.headers["Host"]; body = self.rfile.read(int(self.headers.get("Content-Length",0)))
            assert host in hosts
            state["requests"].append((host, self.path, dict(self.headers), body))
            if host in {"www.googleapis.com", "graph.microsoft.com"}:
                assert self.headers["Authorization"] == "Bearer tls-personal-access"
                state["effects"].append(json.loads(body)); return self.reply(201,{"id":"provider-event"})
            assert self.headers.get("Authorization") is None
            assert self.headers["Content-Type"] == "application/x-www-form-urlencoded"
            values = parse_qs(body.decode())
            if self.path == "/revoke":
                assert host == "oauth2.googleapis.com" and values["token"][0].startswith("tls-personal-refresh")
                return self.reply(200,{})
            assert self.path == ("/token" if host == "oauth2.googleapis.com" else "/"+TENANT+"/oauth2/v2.0/token")
            assert values["client_id"] == ["customer-app-client"] and values["client_secret"] == [CLIENT_SECRET]
            if state["mode"] == "redirect":
                self.send_response(302); self.send_header("Location","https://evil.example/token"); self.end_headers(); return
            if values["grant_type"] == ["authorization_code"]:
                assert values["code"] == ["synthetic-authorization-code"] and len(values["code_verifier"][0]) >= 43
                assert values["redirect_uri"] == ["http://localhost:3000/api/personal-oauth/callback"]
                access, refresh = "tls-personal-access", "tls-personal-refresh"
            else:
                assert values["grant_type"] == ["refresh_token"] and values["refresh_token"][0].startswith("tls-personal-refresh")
                state["refreshes"] += 1
                access, refresh = "tls-personal-access", "tls-personal-refresh-"+str(state["refreshes"])
            self.reply(200,{"access_token":access,"refresh_token":refresh,"expires_in":3600,"token_type":"Bearer",
                "scope": state["scope"] or service.scope("google" if host == "oauth2.googleapis.com" else "microsoft","search")})
    server = ThreadingHTTPServer(("127.0.0.1",0),Receiver)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(certificate,key)
    context.set_servername_callback(lambda socket, hostname, initial: state["sni"].append(hostname))
    server.socket = context.wrap_socket(server.socket,server_side=True)
    class Proxy(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3); header=b""
            while b"\r\n\r\n" not in header:
                data=self.request.recv(4096)
                if not data:return
                header+=data
                if len(header)>8192:return
            first=header.split(b"\r\n",1)[0];state["connects"].append(first)
            assert first==b"CONNECT 8.8.8.8:443 HTTP/1.1"
            with socket.create_connection(("127.0.0.1",server.server_port),timeout=3) as upstream:
                self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                try:
                    while True:
                        ready,_,_=socket_select.select([self.request,upstream],[],[],3)
                        if not ready:return
                        for source in ready:
                            block=source.recv(16384)
                            if not block:return
                            (upstream if source is self.request else self.request).sendall(block)
                except (OSError,TimeoutError):pass
    proxy=socketserver.ThreadingTCPServer(("127.0.0.1",0),Proxy);proxy.daemon_threads=True
    threads=[threading.Thread(target=endpoint.serve_forever,daemon=True) for endpoint in (server,proxy)]
    for thread in threads:thread.start()
    trust=tmp_path/"oauth-trust.pem"; inherited=ssl.get_default_verify_paths().cafile
    trust.write_bytes((Path(inherited).read_bytes() if inherited else b"")+b"\n"+certificate.read_bytes())
    monkeypatch.setenv("SSL_CERT_FILE",str(trust))
    monkeypatch.setattr(service.transport,"request",REAL_TRANSPORT_REQUEST)
    monkeypatch.setattr(service.transport.pinned,"getproxies",lambda:{"https":"http://127.0.0.1:"+str(proxy.server_address[1])})
    monkeypatch.setattr(apps.webhooks,"getproxies",lambda:{"https":"http://127.0.0.1:"+str(proxy.server_address[1])})
    def addresses(host,timeout=5):
        assert host in hosts
        return ["8.8.8.8"]
    monkeypatch.setattr(service.transport.pinned,"public_addresses",addresses)
    try:yield state
    finally:
        for endpoint in (proxy,server):endpoint.shutdown();endpoint.server_close()
        for thread in threads:thread.join(3)


@pytest.mark.parametrize("provider",["google","microsoft"])
def test_actual_https_code_exchange_rotation_revocation_and_pinned_host(oauth,provider_https,provider):
    grant_id,_=connected(oauth,provider)
    expire(oauth,grant_id)
    with Session(oauth.engine) as session:
        assert service.access_token(session,grant_id)=="tls-personal-access"
        assert service.tokens(session.get(PersonalOAuthGrant,grant_id))["refresh_token"]=="tls-personal-refresh-1"
    assert provider_https["refreshes"]==1
    response=oauth.client.delete(BASE+"/grants/"+grant_id,headers=headers(oauth,"peer"))
    assert response.status_code==200
    host="oauth2.googleapis.com" if provider=="google" else "login.microsoftonline.com"
    assert all(request[0]==host for request in provider_https["requests"])
    assert set(provider_https["sni"])=={host} and provider_https["connects"]
    assert response.json()["provider_revocation"]==("requested" if provider=="google" else "not-supported")


def test_actual_https_redirect_never_receives_personal_credentials(oauth,provider_https):
    registration(oauth);provider_https["mode"]="redirect"
    assert "provider_error=" in complete(oauth,start(oauth)).headers["location"]
    assert len(provider_https["requests"])==1 and provider_https["requests"][0][0]=="oauth2.googleapis.com"
    with Session(oauth.engine) as session:
        assert session.exec(select(PersonalOAuthGrant)).one().encrypted_tokens==""


@pytest.mark.parametrize("provider",["google","microsoft"])
def test_actual_https_installed_calendar_write_uses_exact_personal_consent(oauth,provider_https,provider):
    registration(oauth,provider,purpose="calendar-action")
    provider_https["scope"]=service.scope(provider,"calendar-action")
    recipe=next(row for row in apps.catalog() if row["id"]==provider+".calendar-event")
    installed=oauth.client.post("/workspaces/wa/apps",headers=headers(oauth),json={"manifest":recipe["manifest"],
        "channel_ids":["a"],"endpoint":recipe["endpoint_example"],"authorization":""})
    assert installed.status_code==201,installed.text
    app_id=installed.json()["installation"]["id"]
    consent=start(oauth,provider,purpose="calendar-action",config={},installation_id=app_id,installation_generation=1)
    assert consent.status_code==200 and "provider_connected=" in complete(oauth,consent).headers["location"]
    queued=oauth.client.post("/workspaces/wa/apps/"+app_id+"/commands",headers=headers(oauth,"peer"),json={
        "request_id":"tls-calendar-action","channel_id":"a","command":"create-event","arguments":{
            "title":"Explicit TLS calendar event","start":"2026-12-01T10:00:00Z","end":"2026-12-01T11:00:00Z"},
        "confirm":True,"disclose_to_app":True})
    assert queued.status_code==202,queued.text
    with Session(oauth.engine) as session:
        assert apps.run_pending(session)==1
        assert session.get(AppJob,queued.json()["id"]).status=="delivered"
    assert len(provider_https["effects"])==1
    host="www.googleapis.com" if provider=="google" else "graph.microsoft.com"
    outgoing=[row for row in provider_https["requests"] if row[0]==host]
    assert len(outgoing)==1 and outgoing[0][1]==urlsplit(recipe["endpoint_example"]).path
    assert outgoing[0][2]["Authorization"]=="Bearer tls-personal-access"
    payload=provider_https["effects"][0]
    assert payload["summary" if provider=="google" else "subject"]=="Explicit TLS calendar event"
    assert not ({"attendees","channel_id","actor_id","provider_grant_id"}&payload.keys())
    assert host in provider_https["sni"] and provider_https["connects"]
