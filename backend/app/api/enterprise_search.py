"""Local content sources and explicit personal federated provider search."""
from datetime import datetime
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import update
from sqlmodel import select

from ..config import settings
from ..database import get_session
from ..models.enterprise_search import EnterpriseSearchConnection
from ..models.user import User
from ..services import search_providers as providers, search_sources
from ..services.account_security import account_limit, seal_private, unseal_private
from ..services.auth import get_current_user
from ..services.request_guard import recheck_actor
from ..services.workspaces import require_workspace_member


def private(response: Response):
    response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"})
    if not getattr(settings, "enterprise_search_enabled", True): raise HTTPException(404, "Enterprise search is disabled")


class PrivateInputRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request):
            try: return await original(request)
            except RequestValidationError:
                # Pydantic error inputs can contain a submitted provider token.
                return JSONResponse({"detail":"Check the search request fields"},status_code=422,
                    headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer"})
        return handler


router = APIRouter(tags=["enterprise search"], dependencies=[Depends(private)], route_class=PrivateInputRoute)


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReferenceInput(StrictInput):
    reference: str = Field(min_length=1, max_length=12000)


class ConnectionInput(StrictInput):
    name: str = Field(min_length=1, max_length=80)
    provider: Literal["google", "microsoft", "custom"]
    config: dict
    access_token: SecretStr = Field(min_length=1, max_length=16000)
    delegated_access_acknowledged: bool


class QueryInput(StrictInput):
    query: str = Field(min_length=1, max_length=500)
    connection_revision: int = Field(ge=1)
    limit: int = Field(default=10, ge=1, le=20)
    share_query_with_provider: bool


def authorize(session, workspace_id, actor_id):
    recheck_actor(session, actor_id)
    require_workspace_member(session, workspace_id, actor_id)


def connection(session, workspace_id, actor_id, identifier, revision=None):
    authorize(session, workspace_id, actor_id)
    row = session.get(EnterpriseSearchConnection, identifier, populate_existing=True)
    if not row or row.workspace_id != workspace_id or row.user_id != actor_id:
        raise HTTPException(404, "Personal search connection not found")
    if revision is not None and row.revision != revision: raise HTTPException(409, "Search delegation changed; reconnect")
    return row


def public(row, *, oauth=False):
    return {"id": row.id, "name": row.name, "provider": row.provider, "revision": row.revision,
        "config": json.loads(row.config_json), "scope": "personal-delegation", "connected": True,
        "authentication": "customer-oauth" if oauth else "manual-token"}


def provider_call(session, workspace_id, actor_id, identifier, revision, operation):
    row = connection(session, workspace_id, actor_id, identifier, revision)
    provider, config = row.provider, json.loads(row.config_json)
    from ..services import personal_oauth
    grant = personal_oauth.grant_for_target(session, "search", row.id, actor_id)
    grant_id = grant.id if grant else None
    token = (personal_oauth.access_token(session, grant_id, user_id=actor_id) if grant_id else
        unseal_private(row.encrypted_credentials, "enterprise-search-credentials", row.id))
    session.commit()
    budget = providers.transport.begin_budget(25)
    try:
        try:
            try:
                result = operation(provider, config, token)
            except providers.ProviderError as error:
                if not grant_id or error.status != 401:
                    raise
                connection(session, workspace_id, actor_id, identifier, revision)
                token = personal_oauth.access_token(session, grant_id, user_id=actor_id, force_refresh=True)
                session.commit()
                result = operation(provider, config, token)
        except providers.ProviderError as error:
            session.expire_all(); connection(session, workspace_id, actor_id, identifier, revision)
            status = 403 if error.status in {401, 403} else 404 if error.status == 404 else 422
            raise HTTPException(status, {"code": error.code, "message": "Provider search unavailable; check your personal delegation"}) from None
    finally: providers.transport.end_budget(budget)
    session.expire_all()
    connection(session, workspace_id, actor_id, identifier, revision)
    if grant_id:
        personal_oauth.current_grant(session, grant_id, user_id=actor_id)
    return result


def external_reference(actor_id, workspace_id, row, item):
    return seal_private(json.dumps({"workspace": workspace_id, "actor": actor_id, "connection": row.id,
        "revision": row.revision, "id": item["id"], "expires": datetime.utcnow().timestamp() + 600}),
        "enterprise-search-external", actor_id)


@router.get("/channels/{channel_id}/search/sources")
def sources(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "search-sources", user.id, 30, 60)
    return search_sources.sources(session, channel_id, user.id)


@router.post("/search/resolve")
def resolve(data: ReferenceInput, user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "search-resolve", user.id, 60, 60)
    return search_sources.resolve(session, user.id, data.reference)


@router.get("/workspaces/{workspace_id}/enterprise-search/connections")
def connections(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    authorize(session, workspace_id, user.id)
    from ..services import personal_oauth
    result = []
    for row in session.exec(select(EnterpriseSearchConnection).where(
            EnterpriseSearchConnection.workspace_id == workspace_id, EnterpriseSearchConnection.user_id == user.id)).all():
        grant = personal_oauth.grant_for_target(session, "search", row.id, user.id)
        if grant:
            try:
                personal_oauth.current_grant(session, grant.id, user_id=user.id)
            except HTTPException:
                continue
            result.append(public(row, oauth=True))
        elif row.encrypted_credentials:
            result.append(public(row))
    return result


@router.post("/workspaces/{workspace_id}/enterprise-search/connections")
def connect(workspace_id: str, data: ConnectionInput, user=Depends(get_current_user), session=Depends(get_session)):
    authorize(session, workspace_id, user.id)
    account_limit(session, "search-connect", user.id, 10, 60)
    # Serialize personal secret creation against identity erasure. Recheck the
    # current session after acquiring the same account row lock as closure.
    session.execute(update(User).where(User.id == user.id).values(name=User.name))
    session.expire_all(); authorize(session, workspace_id, user.id)
    if not data.delegated_access_acknowledged:
        raise HTTPException(422, "Use your own delegated token; application-wide administrator tokens are unsupported")
    existing = session.exec(select(EnterpriseSearchConnection.id).where(EnterpriseSearchConnection.user_id == user.id,
        EnterpriseSearchConnection.encrypted_credentials != "").limit(21)).all()
    if len(existing) >= 20: raise HTTPException(409, "Personal search connection limit reached")
    try:
        config = providers.config(data.provider, data.config)
        providers.clean(data.access_token.get_secret_value(), 16000)
    except providers.ProviderError: raise HTTPException(422, "Invalid personal provider configuration") from None
    row = EnterpriseSearchConnection(workspace_id=workspace_id, user_id=user.id, name=data.name.strip(),
        provider=data.provider, config_json=json.dumps(config), encrypted_credentials="")
    row.encrypted_credentials = seal_private(data.access_token.get_secret_value(), "enterprise-search-credentials", row.id)
    session.add(row); session.commit()
    return public(row)


@router.delete("/workspaces/{workspace_id}/enterprise-search/connections/{connection_id}")
def disconnect(workspace_id: str, connection_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    row = connection(session, workspace_id, user.id, connection_id)
    from ..services import personal_oauth
    grant = personal_oauth.grant_for_target(session, "search", row.id, user.id)
    remote_token = personal_oauth.revocation_token(grant) if grant else None
    if grant:
        personal_oauth.invalidate(session, grant)
    session.delete(row); session.commit()
    return {"disconnected": True, **({"provider_revocation": personal_oauth.revoke_at_provider(grant.provider, remote_token)} if grant else {})}


@router.post("/workspaces/{workspace_id}/enterprise-search/connections/{connection_id}/query")
def query(workspace_id: str, connection_id: str, data: QueryInput, user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "search-provider", user.id, 20, 60)
    if not data.share_query_with_provider: raise HTTPException(422, "Explicitly share this query with the selected provider")
    if not data.query.strip(): raise HTTPException(422, "Enter a provider search query")
    result = provider_call(session, workspace_id, user.id, connection_id, data.connection_revision,
        lambda provider, config, token: providers.search(provider, config, token, data.query.strip(), data.limit))
    row = connection(session, workspace_id, user.id, connection_id, data.connection_revision)
    for item in result["items"]: item["reference"] = external_reference(user.id, workspace_id, row, item)
    return {**result, "scope": "personal-provider-acl", "indexed": False}


@router.post("/enterprise-search/resolve")
def resolve_external(data: ReferenceInput, user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "search-provider-resolve", user.id, 40, 60)
    try:
        claim = json.loads(unseal_private(data.reference, "enterprise-search-external", user.id))
        if claim["actor"] != user.id or claim["expires"] < datetime.utcnow().timestamp(): raise ValueError()
    except Exception: raise HTTPException(404, "Provider citation expired or unavailable") from None
    return provider_call(session, claim["workspace"], user.id, claim["connection"], claim["revision"],
        lambda provider, config, token: providers.resolve(provider, config, token, claim["id"]))
