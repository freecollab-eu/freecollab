"""Validated, private appearance preferences for the authenticated account."""
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from ..database import get_session
from ..models.preference import UserAppearance, UserLanguage
from ..models.user import User
from ..services.auth import get_current_user

router = APIRouter(prefix="/preferences", tags=["preferences"])

HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
Mode = Literal["light", "dark", "system"]
FontFamily = Literal["inter", "manrope", "serif", "system"]
FontSize = Literal["small", "medium", "large"]
Density = Literal["comfortable", "compact"]
CornerStyle = Literal["soft", "rounded", "square"]


class AppearancePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1] = 1
    mode: Mode = "light"
    accent_color: HexColor = "#ee593f"
    background_color: HexColor = "#f1eee8"
    dark_background_color: HexColor = "#252321"
    font_family: FontFamily = "inter"
    font_size: FontSize = "medium"
    density: Density = "comfortable"
    corner_style: CornerStyle = "soft"

    @field_validator("version", mode="before")
    @classmethod
    def require_integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("Version must be the integer 1")
        return value

    @field_validator("accent_color", "background_color", "dark_background_color")
    @classmethod
    def canonical_color(cls, value: str) -> str:
        return value.lower()


class AppearanceUpdate(AppearancePreferences):
    """PUT replaces the complete snapshot; omitted settings are not reset silently."""

    version: Literal[1] = Field(...)
    mode: Mode = Field(...)
    accent_color: HexColor = Field(...)
    background_color: HexColor = Field(...)
    dark_background_color: HexColor = Field(...)
    font_family: FontFamily = Field(...)
    font_size: FontSize = Field(...)
    density: Density = Field(...)
    corner_style: CornerStyle = Field(...)


DEFAULT_APPEARANCE = AppearancePreferences().model_dump()


class LanguagePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    locale: Literal["auto", "en", "sv"]


def lock_preference_account(session: Session, user_id: str):
    """Serialize saves with account erasure and recheck the live sign-in."""
    from ..services.request_guard import recheck_actor
    session.execute(update(User).where(User.id == user_id).values(name=User.name))
    recheck_actor(session, user_id)


@router.get("/language", response_model=LanguagePreferences)
def get_language(response: Response, user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "private, no-store"
    preference = session.get(UserLanguage, user.id)
    return LanguagePreferences(locale=preference.locale if preference else "auto")


@router.put("/language", response_model=LanguagePreferences)
def put_language(data: LanguagePreferences, response: Response, user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "private, no-store"
    # Serialize the first insert on the account row, and recheck the live grant
    # after acquiring it. Scoped SSO accounts can change their own UI language.
    lock_preference_account(session, user.id)
    preference = session.get(UserLanguage, user.id) or UserLanguage(user_id=user.id)
    preference.locale, preference.updated_at = data.locale, datetime.utcnow()
    session.add(preference)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        lock_preference_account(session, user.id)
        preference = session.get(UserLanguage, user.id)
        if preference is None:
            raise
        preference.locale, preference.updated_at = data.locale, datetime.utcnow()
        session.add(preference)
        session.commit()
    return data


@router.get("/appearance", response_model=AppearancePreferences)
def get_appearance(
    response: Response,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    response.headers["Cache-Control"] = "private, no-store"
    appearance = session.get(UserAppearance, user.id)
    if appearance is None:
        return AppearancePreferences()
    return AppearancePreferences.model_validate_json(appearance.values)


@router.put("/appearance", response_model=AppearancePreferences)
def put_appearance(
    data: AppearanceUpdate,
    response: Response,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    response.headers["Cache-Control"] = "private, no-store"
    user_id = user.id
    lock_preference_account(session, user_id)
    values = data.model_dump_json()
    appearance = session.get(UserAppearance, user_id)
    if appearance is None:
        appearance = UserAppearance(user_id=user_id, values=values)
    else:
        appearance.values = values
        appearance.updated_at = datetime.utcnow()
    session.add(appearance)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        lock_preference_account(session, user_id)
        # Two clients may save an account's first settings simultaneously.
        # The winner creates the row; the other request becomes an update.
        appearance = session.get(UserAppearance, user_id)
        if appearance is None:
            raise
        appearance.values = values
        appearance.updated_at = datetime.utcnow()
        session.add(appearance)
        session.commit()
    return data
