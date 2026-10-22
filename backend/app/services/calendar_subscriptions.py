"""Opt-in calendar capabilities: native schedules only; no attendee disclosure."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
from sqlalchemy import update
from sqlmodel import select

from ..config import settings
from ..models.account_security import AccountSecurity
from ..models.calendar_delivery import CalendarSubscription
from ..models.channel import Channel, ChannelMember
from ..models.integration_content import IntegrationContent
from ..models.user import User
from ..models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from .access import lock_channel_writes, require_channel_member
from .calendar_planning import expand_occurrences
from .chat_notifications import require_native_channel
from .request_guard import recheck_actor
from .sso_scope import require_unrestricted
from .workspaces import lock_workspace, require_direct_message_writable, require_workspace_admin


def grant(session, channel_id, actor_id, *, lock=False):
    mapping = session.get(WorkspaceChannel, channel_id)
    if not mapping:
        raise HTTPException(404, 'Calendar channel not found')
    workspace_id = mapping.workspace_id
    if lock:
        lock_workspace(session, workspace_id)
        lock_channel_writes(session, channel_id)
        session.expire_all()
    require_workspace_admin(session, workspace_id, actor_id)
    member = require_channel_member(session, channel_id, actor_id)
    require_direct_message_writable(session, channel_id, actor_id)
    require_native_channel(session, channel_id)
    channel = session.get(Channel, channel_id)
    if member.role != 'admin' or not channel or channel.type in {'direct', 'group_direct'}:
        raise HTTPException(403, 'A native group channel administrator must explicitly authorize public calendar sharing')
    actor = session.get(User, actor_id)
    if not actor or actor.is_blocked:
        raise HTTPException(403, 'The calendar publisher is no longer active')
    return workspace_id


def public(row):
    return {'id': row.id, 'channel_id': row.channel_id, 'include_titles': row.include_titles,
            'expires_at': row.expires_at, 'revoked_at': row.revoked_at, 'created_at': row.created_at}


def create(session, channel_id, actor_id, days, include_titles):
    require_unrestricted(session)
    workspace_id = grant(session, channel_id, actor_id, lock=True)
    recheck_actor(session, actor_id)
    state = session.get(AccountSecurity, actor_id)
    if not state:
        raise HTTPException(401, 'Reauthenticate before creating a calendar subscription')
    existing = session.exec(select(CalendarSubscription.id).where(CalendarSubscription.channel_id == channel_id,
        CalendarSubscription.revoked_at.is_(None), CalendarSubscription.expires_at > datetime.utcnow()).limit(10)).all()
    if len(existing) >= 10:
        raise HTTPException(409, 'Revoke an existing calendar subscription before creating another')
    token = secrets.token_urlsafe(32)
    row = CalendarSubscription(workspace_id=workspace_id, channel_id=channel_id, user_id=actor_id,
        auth_version=state.auth_version, token_hash=hashlib.sha256(token.encode()).hexdigest(), include_titles=include_titles,
        expires_at=datetime.utcnow() + timedelta(days=days))
    session.add(row); session.commit()
    origin = urlsplit(settings.public_app_url)
    local = settings.environment != 'production' and origin.hostname in {'localhost', '127.0.0.1', '::1'}
    if (not origin.hostname or origin.username or origin.password or origin.query or origin.fragment
            or origin.scheme != 'https' and not (local and origin.scheme == 'http')):
        row.revoked_at = datetime.utcnow(); session.add(row); session.commit()
        raise HTTPException(503, 'Configure the public HTTPS application origin before publishing a calendar')
    url = urlunsplit((origin.scheme, origin.netloc, '/api/calendar/subscriptions/' + row.id + '/' + token + '.ics', '', ''))
    return {**public(row), 'subscription_url': url}


def revoke_for_account(session, user_id):
    """Called in account closure; token digests remain only as denied tombstones."""
    session.execute(update(CalendarSubscription).where(CalendarSubscription.user_id == user_id,
        CalendarSubscription.revoked_at.is_(None)).values(revoked_at=datetime.utcnow()))


def _escape(value):
    return value.replace('\\', '\\\\').replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def _fold(line):
    lines, current, size = [], '', 0
    for character in line:
        length = len(character.encode('utf-8'))
        if size + length > 75:
            lines.append(current); current, size = ' ', 1
        current += character; size += length
    return '\r\n'.join([*lines, current])


def _basic(value, all_day=False):
    if all_day:
        return value.replace('-', '')
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def render(session, subscription_id, token, *, now=None):
    now = now or datetime.utcnow()
    row = session.get(CalendarSubscription, subscription_id)
    if (not re.fullmatch(r'[a-f0-9]{32}', subscription_id) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token)
            or not row or not hmac.compare_digest(row.token_hash, hashlib.sha256(token.encode()).hexdigest())):
        raise HTTPException(404, 'Calendar subscription unavailable')
    try:
        grant(session, row.channel_id, row.user_id, lock=True)
        row = session.get(CalendarSubscription, subscription_id, populate_existing=True)
        state = session.get(AccountSecurity, row.user_id, populate_existing=True)
        mapping = session.get(WorkspaceChannel, row.channel_id)
        if (row.revoked_at or row.expires_at <= now or not state or state.auth_version != row.auth_version
                or not mapping or mapping.workspace_id != row.workspace_id):
            raise HTTPException(404, 'Calendar subscription unavailable')
    except HTTPException:
        raise HTTPException(404, 'Calendar subscription unavailable') from None
    from ..api.integrations_content import latest_revision, serialize
    events = session.exec(select(IntegrationContent).where(IntegrationContent.channel_id == row.channel_id,
        IntegrationContent.kind == 'calendar').order_by(IntegrationContent.id).limit(1001)).all()
    if len(events) > 1000:
        raise HTTPException(409, 'Calendar subscription exceeds its event limit')
    begin = (now - timedelta(days=30)).replace(tzinfo=timezone.utc)
    end = (now + timedelta(days=365)).replace(tzinfo=timezone.utc)
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//FreeCollab//Native Subscription//EN',
             'CALSCALE:GREGORIAN', 'METHOD:PUBLISH', 'X-WR-CALNAME:FreeCollab calendar']
    count = 0
    for item in events:
        revision = latest_revision(session, item.id)
        if not revision or revision.deleted:
            continue
        fields = serialize(session, item, revision).get('fields')
        if not fields:
            raise HTTPException(409, 'Calendar subscription cannot read its native schedule')
        for occurrence in expand_occurrences(fields, begin, end):
            count += 1
            if count > 1000:
                raise HTTPException(409, 'Calendar subscription exceeds 1,000 occurrences in its window')
            identity = hmac.new(bytes.fromhex(row.token_hash), (item.id + '\0' + occurrence['original_start']).encode(), hashlib.sha256).hexdigest()
            title = occurrence['title'] if row.include_titles else 'Busy'
            all_day = occurrence['all_day']
            lines += ['BEGIN:VEVENT', 'UID:' + identity + '@freecollab',
                      'DTSTAMP:' + revision.created_at.strftime('%Y%m%dT%H%M%SZ'),
                      'DTSTART' + (';VALUE=DATE:' if all_day else ':') + _basic(occurrence['starts_at'], all_day),
                      'DTEND' + (';VALUE=DATE:' if all_day else ':') + _basic(occurrence['ends_at'], all_day),
                      'SUMMARY:' + _escape(title), 'TRANSP:OPAQUE', 'END:VEVENT']
    lines += ['END:VCALENDAR']
    return ('\r\n'.join(_fold(line) for line in lines) + '\r\n').encode('utf-8')
