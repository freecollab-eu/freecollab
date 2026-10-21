"""Native clients use the system browser; provider credentials stay with its IdP."""
from typing import Literal
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from ..database import get_session
from ..services.auth import get_current_user, security
from ..services.native_clients import issue_grant, exchange_grant
from ..services.rate_limit import auth_rate_limit

router = APIRouter(prefix="/native-clients", tags=["native clients"])


class Connect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code_challenge: str = Field(min_length=43, max_length=43)
    redirect_uri: str = Field(max_length=256)
    client_kind: Literal["desktop", "mobile"]


class Exchange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=43, max_length=43)
    code_verifier: str = Field(min_length=43, max_length=128)
    redirect_uri: str = Field(max_length=256)


@router.post("/connect", dependencies=[Depends(auth_rate_limit)])
def connect(data: Connect, response: Response, user=Depends(get_current_user),
            credentials=Depends(security), session=Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return issue_grant(session, user.id, credentials.credentials, challenge=data.code_challenge,
        callback=data.redirect_uri, kind=data.client_kind)


@router.post("/exchange", dependencies=[Depends(auth_rate_limit)])
def exchange(data: Exchange, response: Response, session=Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return exchange_grant(session, data.code, data.code_verifier, data.redirect_uri)
