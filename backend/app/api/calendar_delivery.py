"""Explicit native calendar delivery and bearer subscription capabilities."""
from datetime import datetime
import hashlib
import hmac
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select

from ..database import get_session
from ..models.calendar_delivery import CalendarProviderJob, CalendarProviderPublication, CalendarSubscription
from ..models.remote_sync import RemoteSyncConnection
from ..services import calendar_delivery as delivery, calendar_subscriptions as subscriptions
from ..services.auth import get_current_user
from ..services.account_security import account_limit, ensure_reauthentication, state_for
from ..services.sso_scope import require_unrestricted
from ..services.request_guard import recheck_actor

router = APIRouter(tags=['calendar delivery and subscriptions'])


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Review(Strict):
    connection_id: str = Field(min_length=1, max_length=128)
    connection_revision: int = Field(ge=1)
    event_revision: int = Field(ge=1)
    publication_revision: int = Field(default=0, ge=0)
    action: Literal['publish', 'cancel', 'respond', 'refresh', 'reconcile']
    attendees: list[dict] = Field(default_factory=list, max_length=50)
    response: Literal['accepted', 'tentative', 'declined'] | None = None
    acknowledge_uncertain: bool = False


class Approval(Review):
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{16,80}$')
    review_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    provider_disclosure: bool = False
    send_invitations: bool = False


class FeedCreate(Strict):
    password: str = Field(min_length=1, max_length=1024)
    code: str | None = Field(default=None, max_length=64)
    days: int = Field(default=30, ge=1, le=90)
    include_titles: bool = False
    public_disclosure: bool = False


class FeedRevoke(Strict):
    password: str = Field(min_length=1, max_length=1024)
    code: str | None = Field(default=None, max_length=64)


def private(response):
    response.headers.update({'Cache-Control': 'private, no-store', 'Pragma': 'no-cache', 'Referrer-Policy': 'no-referrer'})


@router.get('/channels/{channel_id}/calendar/provider-delivery')
def state(channel_id: str, response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    require_unrestricted(session)
    subscriptions.grant(session, channel_id, user.id)
    private(response)
    sources = session.exec(select(RemoteSyncConnection).where(RemoteSyncConnection.channel_id == channel_id,
        RemoteSyncConnection.grant_user_id == user.id, RemoteSyncConnection.kind == 'calendar',
        RemoteSyncConnection.provider.in_(['google', 'microsoft']))).all()
    publications = session.exec(select(CalendarProviderPublication).where(CalendarProviderPublication.channel_id == channel_id,
        CalendarProviderPublication.user_id == user.id).order_by(CalendarProviderPublication.updated_at.desc()).limit(100)).all()
    jobs = session.exec(select(CalendarProviderJob).where(CalendarProviderJob.channel_id == channel_id,
        CalendarProviderJob.user_id == user.id).order_by(CalendarProviderJob.created_at.desc()).limit(30)).all()
    feeds = session.exec(select(CalendarSubscription).where(CalendarSubscription.channel_id == channel_id,
        CalendarSubscription.user_id == user.id).order_by(CalendarSubscription.created_at.desc()).limit(30)).all()
    return {'connections': [{'id': row.id, 'name': row.name, 'provider': row.provider, 'revision': row.revision,
                'ready': row.enabled and row.tested_revision == row.revision and bool(row.encrypted_credentials)} for row in sources],
            'publications': [delivery.publication_data(row) for row in publications],
            'jobs': [delivery.job_data(row) for row in jobs], 'subscriptions': [subscriptions.public(row) for row in feeds]}


@router.post('/channels/{channel_id}/calendar/{item_id}/provider/preview')
def preview(channel_id: str, item_id: str, data: Review, response: Response,
            user=Depends(get_current_user), session=Depends(get_session)):
    private(response)
    account_limit(session, 'calendar-provider-preview', user.id, 60, 300)
    return delivery.preview(session, user.id, channel_id, item_id, data.model_dump())


@router.post('/channels/{channel_id}/calendar/{item_id}/provider/actions', status_code=202)
def approve(channel_id: str, item_id: str, data: Approval, response: Response,
            user=Depends(get_current_user), session=Depends(get_session)):
    private(response)
    account_limit(session, 'calendar-provider-action', user.id, 30, 300)
    return delivery.queue(session, user.id, channel_id, item_id, data.model_dump())


@router.post('/channels/{channel_id}/calendar/subscriptions', status_code=201)
def create_feed(channel_id: str, data: FeedCreate, response: Response,
                user=Depends(get_current_user), session=Depends(get_session)):
    private(response)
    require_unrestricted(session)
    subscriptions.grant(session, channel_id, user.id)
    state_for(session, user.id)
    account_limit(session, 'calendar-feed-config', user.id, 20, 300)
    if data.public_disclosure is not True:
        raise HTTPException(422, 'Explicit public disclosure consent is required; anyone holding the subscription URL can read its selected schedule')
    ensure_reauthentication(session, user, data.password, data.code)
    return subscriptions.create(session, channel_id, user.id, data.days, data.include_titles)


@router.delete('/channels/{channel_id}/calendar/subscriptions/{subscription_id}')
def revoke_feed(channel_id: str, subscription_id: str, data: FeedRevoke, response: Response,
                user=Depends(get_current_user), session=Depends(get_session)):
    private(response)
    require_unrestricted(session)
    subscriptions.grant(session, channel_id, user.id)
    # Reauthentication can consume a recovery-code row. Keep that write ahead
    # of workspace/channel locks, matching account-erasure lock ordering.
    ensure_reauthentication(session, user, data.password, data.code)
    subscriptions.grant(session, channel_id, user.id, lock=True)
    recheck_actor(session, user.id)
    row = session.get(CalendarSubscription, subscription_id, populate_existing=True)
    if not row or row.channel_id != channel_id or row.user_id != user.id:
        raise HTTPException(404, 'Calendar subscription not found')
    row.revoked_at = row.revoked_at or datetime.utcnow(); session.add(row); session.commit()
    return subscriptions.public(row)


@router.get('/calendar/subscriptions/{subscription_id}/{token}.ics')
def feed(subscription_id: str, token: str, request: Request, session=Depends(get_session)):
    security = {'Cache-Control': 'private, no-store', 'Pragma': 'no-cache', 'Referrer-Policy': 'no-referrer'}
    try:
        content = subscriptions.render(session, subscription_id, token)
    except HTTPException as error:
        error.headers = {**(error.headers or {}), **security}
        raise
    tag = '"' + hashlib.sha256(content).hexdigest() + '"'
    headers = {'ETag': tag, **security,
               'X-Content-Type-Options': 'nosniff', 'Content-Disposition': 'inline; filename="freecollab-calendar.ics"'}
    if hmac.compare_digest(request.headers.get('if-none-match', ''), tag):
        return Response(status_code=304, headers=headers)
    return Response(content, media_type='text/calendar; charset=utf-8', headers=headers)
