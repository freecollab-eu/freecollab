"""Personal cross-workspace home; actions keep their original resource guards."""
from fastapi import APIRouter, Depends, Response

from ..database import get_session
from ..services.auth import get_current_user
from ..services.my_day import snapshot

router = APIRouter(prefix="/my-day", tags=["my day"])


@router.get("")
def my_day(response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return snapshot(session, user.id)
