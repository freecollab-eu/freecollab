"""Setup is SELECT-only, tenant/channel confined and honest about saved consent."""
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
import pytest
from sqlalchemy import event
from sqlmodel import Session, select
from app import main as application
from app.api import connection_setup as api
from app.config import settings
from app.models.account_security import AccountSecurity
from app.models.channel import ChannelMember
from app.models.cloud_files import CloudFileConnection
from app.models.copilot import CopilotConfig
from app.models.external_meetings import ExternalMeetingConnection
from app.models.remote_sync import RemoteSyncConnection
from app.models.scim import SCIMDirectory
from app.models.secure_channel import SecureChannel
from app.models.session import ActiveSession
from app.models.user import User
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.models.workspace import WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.services import connection_setup as service
from app.services.auth import create_token, token_digest
from tests.test_remote_sync import team

BASE = "/workspaces/a/connection-setup"
PRIVATE = "forbidden-provider-material"


@pytest.fixture
def center(team, monkeypatch):
    for flag in ("sync_enabled", "cloud_files_enabled", "external_meetings_enabled", "copilot_enabled", "webhooks_enabled"):
        monkeypatch.setattr(settings, flag, True)
    monkeypatch.setattr(settings, "public_app_url", "https://collab.customer.example")
    team.client.app.include_router(api.router)
    now = datetime.utcnow()
    with Session(team.engine) as session:
        session.add(User(id="grantor", name="Grantor", email="grantor@example.com", password_hash="unused"));session.commit()
        session.add(WorkspaceMember(workspace_id="a", user_id="grantor", role="admin"))
        session.add(ChannelMember(channel_id="room", user_id="grantor", role="admin"));session.commit()
        for identifier, workspace, channel, grantor, provider, kind, status, enabled, tested in (
            ("google-files","a","room","grantor","google","files","connected",False,None),
            ("calendar-source","a","room","grantor","google","calendar","idle",True,3),
            ("hidden-file","a","private","private-admin","google","files","idle",True,3),
            ("foreign-file","b","other","outsider","google","files","idle",True,3)):
            session.add(RemoteSyncConnection(id=identifier,workspace_id=workspace,channel_id=channel,created_by=grantor,grant_user_id=grantor,
                name="HIDDEN secret channel name" if identifier=="hidden-file" else "FOREIGN private connection" if identifier=="foreign-file" else identifier,
                provider=provider,kind=kind,config_json=PRIVATE,encrypted_credentials=PRIVATE,status=status,enabled=enabled,revision=3,tested_revision=tested,tested_at=now if tested else None))
        session.add_all([CloudFileConnection(id="box",workspace_id="a",channel_id="room",created_by="grantor",grant_user_id="grantor",name="Customer Box",provider="box",config_json=PRIVATE,encrypted_credentials=PRIVATE,status="oauth_required"),
            CloudFileConnection(id="dropbox",workspace_id="a",channel_id="room",created_by="grantor",grant_user_id="grantor",name="Customer Dropbox",provider="dropbox",config_json=PRIVATE,encrypted_credentials=PRIVATE,status="connected")])
        session.add_all([ExternalMeetingConnection(id="teams",workspace_id="a",channel_id="room",created_by="grantor",grant_user_id="grantor",name="Customer Teams",provider="microsoft_teams",config_json=PRIVATE,encrypted_credentials=PRIVATE,status="connected"),
            ExternalMeetingConnection(id="meet",workspace_id="a",channel_id="room",created_by="grantor",grant_user_id="grantor",name="Customer Meet",provider="google_meet",config_json=PRIVATE,status="ready",enabled=True,tested_revision=1,tested_at=now,source_connection_id="calendar-source",source_revision=3)])
        session.add(CopilotConfig(workspace_id="a",encrypted_config=PRIVATE,enabled=True,updated_by="owner"))
        session.add(WorkspaceOIDCProvider(workspace_id="a",name="Customer SSO",issuer_url="https://issuer.private.example/",client_id=PRIVATE,encrypted_client_secret=PRIVATE,
            scopes="openid profile email groups",created_by="owner",enabled=True,revision=4,tested_revision=4,tested_at=now-timedelta(days=2)))
        session.add_all([WebhookEndpoint(id="webhook",workspace_id="a",created_by="grantor",granted_by="grantor",name="Receiver",origin=PRIVATE,encrypted_url=PRIVATE,encrypted_secret=PRIVATE,
            channel_ids_json='["room"]',event_types_json=PRIVATE),
            WebhookEndpoint(id="hidden-hook",workspace_id="a",created_by="owner",granted_by="owner",name="HIDDEN webhook",origin=PRIVATE,encrypted_url=PRIVATE,encrypted_secret=PRIVATE,
            channel_ids_json='["room","private"]',event_types_json=PRIVATE)])
        session.commit()
    return team


def fetch(center, who="owner", channel="room"):
    return center.client.get(BASE + ("?channel_id="+channel if channel is not None else ""),headers=center.headers[who])


def items(response):
    assert response.status_code==200,response.text
    return {row["id"]:row for row in response.json()["recipes"]}


def test_exact_catalog_fields_callbacks_scopes_and_no_secret_or_cross_channel_config(center):
    response=fetch(center); data=response.json(); catalog=items(response)
    assert len(catalog)==15 and data["origin_status"]=="valid"
    assert catalog["files.google"]["callback_url"]=="https://collab.customer.example/api/sync/oauth/callback"
    assert catalog["sso.oidc"]["callback_url"]=="https://collab.customer.example/api/auth/oidc/callback"
    assert catalog["files.box"]["callback_url"].endswith("/api/cloud-files/oauth/callback")
    assert catalog["meetings.zoom"]["callback_url"].endswith("/api/external-meetings/oauth/callback")
    assert catalog["files.google"]["scopes"]==["https://www.googleapis.com/auth/drive"]
    assert catalog["files.microsoft"]["scopes"]==["offline_access","Files.ReadWrite.All"]
    assert catalog["calendar.microsoft"]["scopes"]==["offline_access","Calendars.ReadWrite"]
    assert catalog["files.dropbox"]["scopes"]==["files.metadata.read","files.content.read"]
    assert catalog["files.box"]["scopes"]==[]
    assert catalog["sso.oidc"]["scopes"]==["openid","profile","email","groups"]
    assert catalog["files.nextcloud"]["callback_url"] is None and catalog["directory.scim"]["callback_url"] is None
    assert PRIVATE not in response.text and "HIDDEN" not in response.text and "FOREIGN" not in response.text and "config_json" not in response.text
    assert "no-store" in response.headers["cache-control"] and response.headers["referrer-policy"]=="no-referrer"
    assert "two-way" in catalog["files.google"]["description"] and "read/write" in catalog["files.google"]["description"]
    for row in catalog.values():
        assert "_callback" not in row and isinstance(row["can_configure"],bool)
        for field in row["fields"]:
            assert set(field)=={"key","label","required","secret"}
            assert field["key"] not in {"password","code"} and "value" not in field


def test_saved_local_statuses_are_current_and_actions_remain_configurable(center):
    catalog=items(fetch(center))
    assert catalog["files.google"]["status"]=="verification_required" and catalog["files.google"]["can_configure"]
    assert catalog["files.box"]["status"]=="authorization_required" and catalog["files.box"]["can_configure"]
    assert catalog["files.dropbox"]["status"]=="ready"
    assert catalog["calendar.google"]["status"]=="ready"
    assert catalog["meetings.google_meet"]["status"]=="ready"
    assert catalog["meetings.microsoft_teams"]["status"]=="verification_required"
    assert catalog["ai.openai_compatible"]["status"]=="verification_required" and catalog["ai.openai_compatible"]["can_configure"]
    assert catalog["sso.oidc"]["status"]=="ready"  # enabled OIDC does not expire every hour
    assert catalog["automation.webhook"]["status"]=="verification_required"
    assert catalog["files.google"]["connections"][0]["revision"]==3


def test_no_channel_never_aggregates_another_channels_configuration(center):
    catalog=items(fetch(center,channel=None))
    for row in catalog.values():
        if row["requires_channel"]:
            assert row["connections"]==[] and row["status"]=="not_configured" and not row["can_configure"]
            assert service.CHANNEL_REQUIRED in row["blockers"]
    assert catalog["sso.oidc"]["status"]=="ready"


@pytest.mark.parametrize("who,channel",[("member","room"),("private-admin","room"),("outsider","room"),("owner","private"),("owner","other")])
def test_native_workspace_admin_does_not_imply_other_channel_access(center,who,channel):
    assert fetch(center,who,channel).status_code in {403,404}


def test_scoped_sso_cannot_prepare_native_customer_connections(center):
    with Session(center.engine) as session:
        token=create_token("owner",session,auth_methods=["sso"],workspace_scope={"workspace_id":"a","provider_revision":4})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(token),workspace_id="a",provider_revision=4));session.commit()
    response=center.client.get(BASE+"?channel_id=room",headers={"Authorization":"Bearer "+token})
    assert response.status_code==403


@pytest.mark.parametrize("field",["sync_enabled","cloud_files_enabled","external_meetings_enabled","copilot_enabled","webhooks_enabled"])
def test_disabled_installation_flag_blocks_action_but_keeps_static_recipe_downloadable(center,monkeypatch,field):
    monkeypatch.setattr(settings,field,False)
    prefix={"sync_enabled":"files.google","cloud_files_enabled":"files.box","external_meetings_enabled":"meetings.microsoft_teams","copilot_enabled":"ai.openai_compatible","webhooks_enabled":"automation.webhook"}[field]
    row=items(fetch(center))[prefix]
    assert not row["enabled"] and row["status"]=="unavailable" and not row["can_configure"]
    assert row["fields"] and row["steps"] and row["docs_url"]


def test_grant_revocation_and_reused_calendar_revision_never_claim_readiness(center):
    with Session(center.engine) as session:
        row=session.get(RemoteSyncConnection,"calendar-source");row.revision+=1;session.add(row)
        session.delete(session.get(ChannelMember,("room","grantor")));session.commit()
    catalog=items(fetch(center))
    for name in ("files.google","files.box","files.dropbox","calendar.google","meetings.google_meet","meetings.microsoft_teams","automation.webhook"):
        assert catalog[name]["status"]=="attention" and catalog[name]["can_configure"]
    assert service.RECONNECT_CALENDAR in catalog["meetings.google_meet"]["blockers"]


def test_preparing_secure_channel_blocks_native_adapters_but_standalone_meetings_remain_available(center):
    with Session(center.engine) as session:
        session.add(SecureChannel(channel_id="room",created_by="owner",status="preparing"));session.commit()
    catalog=items(fetch(center))
    for name in ("files.google","files.box","files.dropbox","calendar.google"):
        assert catalog[name]["status"]=="unavailable" and not catalog[name]["can_configure"]
    assert catalog["meetings.microsoft_teams"]["secure_supported"] and catalog["meetings.microsoft_teams"]["can_configure"]
    assert catalog["meetings.google_meet"]["status"]=="attention"  # source calendar native grant is fenced


@pytest.mark.parametrize("environment,url,origin,status,sso_callback",[
    ("test","http://localhost:3000","http://localhost:3000","local_development",True),
    ("development","http://127.0.0.1:3000","http://127.0.0.1:3000","local_development",False),
    ("production","http://localhost:3000",None,"invalid",False),
    ("test","https://user:secret@customer.example",None,"invalid",False),
    ("test","https://customer.example:bad",None,"invalid",False),
    ("test","https://customer.example?secret=private",None,"invalid",False)])
def test_exact_origin_rules_and_sso_local_http_exception(center,monkeypatch,environment,url,origin,status,sso_callback):
    monkeypatch.setattr(settings,"environment",environment);monkeypatch.setattr(settings,"public_app_url",url)
    response=fetch(center); catalog=items(response)
    assert response.json()["public_origin"]==origin and response.json()["origin_status"]==status
    assert bool(catalog["sso.oidc"]["callback_url"])==sso_callback
    if status=="local_development": assert catalog["files.google"]["callback_url"]==origin+"/api/sync/oauth/callback"
    else: assert catalog["files.google"]["callback_url"] is None and not catalog["files.google"]["can_configure"]


def test_get_executes_select_only_and_never_loads_or_decrypts_configuration(center,monkeypatch):
    from app.services import account_security, crypto, remote_transport, oidc_transport
    def forbidden(*args,**kwargs):raise AssertionError("Setup must never decrypt or contact providers")
    for module,name in ((account_security,"unseal_private"),(crypto,"decrypt_message"),(remote_transport,"request"),(oidc_transport,"public_addresses")):
        monkeypatch.setattr(module,name,forbidden)
    statements=[]
    def capture(_conn,_cursor,statement,_parameters,_context,_many):statements.append(statement)
    event.listen(center.engine,"before_cursor_execute",capture)
    try: response=fetch(center)
    finally:event.remove(center.engine,"before_cursor_execute",capture)
    assert response.status_code==200,response.text
    assert statements and all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    for statement in statements:
        select_clause=statement.split("FROM",1)[0]
        assert "config_json" not in select_clause and "encrypted_config" not in select_clause
        assert "encrypted_url" not in select_clause and "encrypted_secret" not in select_clause and "encrypted_client_secret" not in select_clause
    with Session(center.engine) as session:assert session.exec(select(SCIMDirectory)).all()==[]


def test_legacy_untracked_or_missing_security_rows_are_not_bootstrapped_by_get(center):
    with Session(center.engine) as session:
        session.delete(session.get(AccountSecurity,"owner"));session.commit()
    response=fetch(center)
    assert response.status_code==200
    with Session(center.engine) as session:
        assert session.get(AccountSecurity,"owner") is None
        row=session.exec(select(ActiveSession).where(ActiveSession.token_hash==token_digest(center.tokens["owner"]))).one()
        session.delete(row);session.commit()
    assert fetch(center).status_code==401
    with Session(center.engine) as session:
        assert session.exec(select(ActiveSession).where(ActiveSession.token_hash==token_digest(center.tokens["owner"]))).first() is None
        assert session.get(AccountSecurity,"owner") is None


def test_webhook_verification_requires_delivered_current_generation_and_selected_channel(center):
    with Session(center.engine) as session:
        session.add(WebhookDelivery(webhook_id="webhook",event_key="one",event_id="one",event_type="test",channel_id="room",payload=PRIVATE,generation=1,status="delivered",delivered_at=datetime.utcnow()));session.commit()
    assert items(fetch(center))["automation.webhook"]["status"]=="ready"
    with Session(center.engine) as session:
        row=session.get(WebhookEndpoint,"webhook");row.generation=2;session.add(row);session.commit()
    row=items(fetch(center))["automation.webhook"]
    assert row["status"]=="verification_required" and row["connections"][0]["verified_at"] is None


def test_scim_requires_current_owner_issuer_without_exposing_token_hash(center):
    with Session(center.engine) as session:
        session.add(SCIMDirectory(workspace_id="a",created_by="owner",enabled=True,token_hash=PRIVATE,token_version=2,token_issued_by="owner"));session.commit()
    row=items(fetch(center))["directory.scim"]
    assert row["status"]=="ready" and row["fields"]==[]
    with Session(center.engine) as session:
        member=session.get(WorkspaceMember,("a","owner"));member.role="admin";session.add(member);session.commit()
    row=items(fetch(center))["directory.scim"]
    assert row["status"]=="attention" and not row["can_configure"]
    assert items(fetch(center))["ai.openai_compatible"]["can_configure"] is False


@pytest.mark.parametrize("revoke",["auth_version","session","workspace_role","channel_role"])
def test_authority_revocation_during_read_is_rechecked_before_return(center,revoke):
    # WAL permits the second connection to revoke while a SELECT cursor is open.
    with center.engine.connect() as connection: connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    triggered=[]
    def after_query(_connection,_cursor,statement,_parameters,_context,_many):
        if triggered or not statement.lstrip().upper().startswith("SELECT") or "FROM remotesyncconnection" not in statement:return
        triggered.append(True)
        with Session(center.engine) as session:
            if revoke=="auth_version":
                row=session.get(AccountSecurity,"owner");row.auth_version+=1
            elif revoke=="session":
                row=session.exec(select(ActiveSession).where(ActiveSession.token_hash==token_digest(center.tokens["owner"]))).one();row.revoked_at=datetime.utcnow()
            elif revoke=="workspace_role":
                row=session.get(WorkspaceMember,("a","owner"));row.role="member"
            else:
                row=session.get(ChannelMember,("room","owner"));row.role="member"
            session.add(row);session.commit()
    event.listen(center.engine,"after_cursor_execute",after_query)
    try: response=fetch(center)
    finally:event.remove(center.engine,"after_cursor_execute",after_query)
    assert triggered and response.status_code in {401,403} and PRIVATE not in response.text
