"""Current-member meeting invitations and explicitly shared native availability."""
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select

from ..database import get_session
from ..models.calendar_planning import CalendarAttendee
from ..models.integration_content import IntegrationContent
from ..models.user import User
from ..services.access import lock_channel_writes, require_channel_member
from ..services.auth import get_current_user
from ..services.calendar_planning import expand_occurrences, instant, iso
from ..services.chat_notifications import require_native_channel
from ..services.workspaces import require_direct_message_writable
from .integrations_content import latest_revision, serialize

router = APIRouter(tags=["calendar planning"])


class InviteWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    user_ids: list[str] = Field(max_length=50)


class ResponseWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    response: Literal["accepted", "tentative", "declined"]


class AvailabilityRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    starts_at: datetime
    ends_at: datetime
    user_ids: list[str] = Field(min_length=1, max_length=25)
    duration_minutes: int = Field(default=30, ge=15, le=480)


def event(session, channel_id, item_id, user_id, *, write=False):
    if write:
        lock_channel_writes(session, channel_id)
    member = require_channel_member(session, channel_id, user_id)
    require_direct_message_writable(session, channel_id, user_id)
    item = session.get(IntegrationContent, item_id)
    revision = latest_revision(session, item_id) if item else None
    if not item or item.channel_id != channel_id or item.kind != "calendar" or not revision or revision.deleted:
        raise HTTPException(404, "Calendar event not found")
    return item, revision, member


def eligible(session, channel_id, user_id):
    person = session.get(User, user_id)
    if not person or person.is_blocked:
        raise HTTPException(422, "Invite only active current channel members")
    try:
        require_channel_member(session, channel_id, user_id)
        require_direct_message_writable(session, channel_id, user_id)
    except HTTPException:
        raise HTTPException(422, "Invite only active current channel members") from None
    return person


def roster(session, item, revision):
    rows = session.exec(select(CalendarAttendee).where(CalendarAttendee.item_id == item.id)).all()
    result = []
    for row in rows:
        try:
            person = eligible(session, item.channel_id, row.user_id)
        except HTTPException:
            continue
        result.append({"user_id": row.user_id, "name": person.name, "response": row.response if row.revision == revision.revision else "needs-action",
                       "responded_at": row.responded_at if row.revision == revision.revision else None})
    return {"event_revision": revision.revision, "organizer_id": item.created_by, "attendees": result}


def notify_invitation(session, channel_id, item_id, user_id, actor_id):
    from ..models.notification import Notification, NotificationPreference
    preferences = session.exec(select(NotificationPreference).where(NotificationPreference.user_id == user_id)).first()
    if preferences and (not preferences.in_app or not preferences.system):
        return
    if user_id != actor_id:
        session.add(Notification(user_id=user_id, type="system", actor_id=actor_id, entity_type="calendar_invitation", entity_id=item_id,
                                 title="Calendar invitation updated", body="Open the channel calendar to review and respond.",
                                 action_url="#channel=" + channel_id))


def calendar_revision_changed(session, item, revision):
    """Commit invitation invalidation with the event, not as a separate effect."""
    rows = session.exec(select(CalendarAttendee).where(CalendarAttendee.item_id == item.id)).all()
    for row in rows:
        if revision.deleted:
            session.delete(row)
            continue
        row.response, row.responded_at, row.revision = "needs-action", None, revision.revision
        session.add(row)
        try:
            eligible(session, item.channel_id, row.user_id)
        except HTTPException:
            continue
        notify_invitation(session, item.channel_id, item.id, row.user_id, revision.created_by)


@router.get("/channels/{channel_id}/calendar/{item_id}/attendees")
def get_attendees(channel_id: str, item_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    item, revision, _ = event(session, channel_id, item_id, user.id)
    return roster(session, item, revision)


@router.put("/channels/{channel_id}/calendar/{item_id}/attendees")
def set_attendees(channel_id: str, item_id: str, data: InviteWrite, user=Depends(get_current_user), session=Depends(get_session)):
    item, revision, member = event(session, channel_id, item_id, user.id, write=True)
    if item.created_by != user.id and member.role != "admin":
        raise HTTPException(403, "Only the event organizer or channel administrator can invite attendees")
    if revision.revision != data.expected_revision:
        raise HTTPException(409, "This event changed; reload its current version")
    ids = set(data.user_ids)
    for user_id in ids:
        eligible(session, channel_id, user_id)
    rows = session.exec(select(CalendarAttendee).where(CalendarAttendee.item_id == item_id)).all()
    existing = {row.user_id: row for row in rows}
    for row in rows:
        if row.user_id not in ids:
            session.delete(row)
    for user_id in sorted(ids):
        if user_id not in existing:
            session.add(CalendarAttendee(item_id=item_id, user_id=user_id, invited_by=user.id, revision=revision.revision))
            notify_invitation(session, channel_id, item_id, user_id, user.id)
    session.commit()
    return roster(session, item, revision)


@router.put("/channels/{channel_id}/calendar/{item_id}/response")
def respond(channel_id: str, item_id: str, data: ResponseWrite, user=Depends(get_current_user), session=Depends(get_session)):
    item, revision, _ = event(session, channel_id, item_id, user.id, write=True)
    if revision.revision != data.expected_revision:
        raise HTTPException(409, "This event changed; review its current version before responding")
    row = session.get(CalendarAttendee, (item_id, user.id))
    if not row:
        raise HTTPException(403, "This account is not invited to this event")
    row.response, row.responded_at, row.revision = data.response, datetime.utcnow(), revision.revision
    session.add(row)
    session.commit()
    return roster(session, item, revision)


@router.post("/channels/{channel_id}/calendar/availability")
def availability(channel_id: str, data: AvailabilityRead, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    require_direct_message_writable(session, channel_id, user.id)
    require_native_channel(session, channel_id)
    if data.starts_at.tzinfo is None or data.ends_at.tzinfo is None or not timedelta() < data.ends_at - data.starts_at <= timedelta(days=31):
        raise HTTPException(422, "Choose an availability window of at most 31 days with explicit UTC offsets")
    wanted = set(data.user_ids)
    for person_id in wanted:
        eligible(session, channel_id, person_id)
    items = session.exec(select(IntegrationContent).where(IntegrationContent.channel_id == channel_id, IntegrationContent.kind == "calendar").limit(1001)).all()
    if len(items) > 1000:
        raise HTTPException(409, "This calendar exceeds the availability scan limit")
    busy = []
    for item in items:
        revision = latest_revision(session, item.id)
        if not revision or revision.deleted:
            continue
        attendees = session.exec(select(CalendarAttendee).where(CalendarAttendee.item_id == item.id)).all()
        involved = {item.created_by} | {row.user_id for row in attendees if row.response != "declined" or row.revision != revision.revision}
        if not wanted.intersection(involved):
            continue
        fields = serialize(session, item, revision)["fields"]
        try:
            occurrences = expand_occurrences(fields, data.starts_at, data.ends_at)
        except ValueError:
            raise HTTPException(409, "This calendar contains an invalid recurrence") from None
        from zoneinfo import ZoneInfo
        from datetime import date, time
        for occurrence in occurrences:
            if occurrence["all_day"]:
                zone = ZoneInfo(occurrence["timezone"])
                begins = datetime.combine(date.fromisoformat(occurrence["starts_at"]), time(), zone)
                ends = datetime.combine(date.fromisoformat(occurrence["ends_at"]), time(), zone)
            else:
                begins, ends = instant(occurrence["starts_at"]), instant(occurrence["ends_at"])
            busy.append((max(begins, data.starts_at), min(ends, data.ends_at)))
    merged = []
    for begins, ends in sorted(busy):
        if merged and begins <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(ends, merged[-1][1]))
        else:
            merged.append((begins, ends))
    # Candidate starts use quarter-hour UTC boundaries; no implicit working-hour
    # assumptions or person-specific private calendar details are disclosed.
    cursor = data.starts_at.astimezone(timezone.utc)
    cursor = cursor.replace(second=0, microsecond=0) + timedelta(minutes=(-cursor.minute) % 15)
    if cursor < data.starts_at:
        cursor += timedelta(minutes=15)
    length = timedelta(minutes=data.duration_minutes)
    slots = []
    while cursor + length <= data.ends_at and len(slots) < 100:
        if not any(cursor < ends and cursor + length > begins for begins, ends in merged):
            slots.append({"starts_at": iso(cursor), "ends_at": iso(cursor + length)})
        cursor += timedelta(minutes=15)
    return {"scope": "selected-channel-only", "private_calendars_included": False,
            "busy": [{"starts_at": iso(begins), "ends_at": iso(ends)} for begins, ends in merged], "slots": slots}
