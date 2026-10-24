"""Bootstrap only; all example interaction uses the normal product API."""
from fastapi import APIRouter, Depends, Request, Response
from ..database import get_session
from ..services.auth import decode_token_claims, get_current_user, security
from ..services.public_demo import create_demo_session, revoke_demo_session


router = APIRouter(prefix="/demo", tags=["sample workspace"])


@router.post("/session", status_code=201)
def begin_example(request: Request, response: Response, session=Depends(get_session)):
    response.headers["Cache-Control"] = "private, no-store"
    return create_demo_session(session, request)


@router.delete("/session", status_code=204)
def end_example(user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    claims = decode_token_claims(credentials.credentials) or {}
    revoke_demo_session(session, user.id, claims.get("demo_visit_id"))
    return Response(status_code=204, headers={"Cache-Control": "private, no-store"})
