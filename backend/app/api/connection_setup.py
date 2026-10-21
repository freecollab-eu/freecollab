"""Preparation never grants consent, mutates configuration or tests a provider."""
from fastapi import APIRouter, Depends, Query, Request, Response
from ..database import get_session
from ..services.auth import active_user_for_token, decode_token_claims, security, token_digest
from ..services.request_guard import clear_request, remember_request, recheck_actor
from ..services.sso_scope import require_request_scope
from fastapi import HTTPException
from ..services.connection_setup import catalog

router = APIRouter(prefix="/workspaces/{workspace_id}/connection-setup", tags=["connection setup"])


def readonly_actor(request: Request, credentials=Depends(security), session=Depends(get_session)):
    # The general auth dependency bootstraps legacy security/session rows. This
    # preparation endpoint is deliberately SELECT-only and requires an already
    # tracked, current native session instead of performing that bootstrap.
    clear_request(session)
    claims = decode_token_claims(credentials.credentials)
    actor = active_user_for_token(credentials.credentials, session) if claims else None
    if actor is None: raise HTTPException(401, "Sign in with your current FreeCollab session to prepare connections")
    remember_request(session, token_digest(credentials.credentials), claims)
    recheck_actor(session, actor.id)
    require_request_scope(session, request.url.path)
    from ..services.security_audit import authenticated_actor
    authenticated_actor(request, actor.id)
    return actor


@router.get("")
def connection_setup(workspace_id: str, response: Response, channel_id: str | None = Query(default=None, min_length=1, max_length=128),
                     user=Depends(readonly_actor), session=Depends(get_session)):
    response.headers.update({"Cache-Control": "private, no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"})
    return catalog(session, workspace_id, user.id, channel_id)
