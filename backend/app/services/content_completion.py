"""Transaction-neutral content privacy helpers for API/export/erasure owners."""
import json
from fastapi import HTTPException
from sqlmodel import select
from ..models.content_completion import ContentCollectionItem, PersonalCalendarEvent
from .account_security import unseal_private
from .integration_content import CalendarFields
from .request_guard import recheck_actor
from .workspaces import require_workspace_member
from .access import require_channel_member


def personal_event_record(row):
    try:
        payload = json.loads(unseal_private(row.encrypted_payload, 'personal-calendar:' + row.workspace_id + ':' + row.id, row.user_id))
        fields = CalendarFields.model_validate(payload['fields'])
    except Exception:
        raise HTTPException(409, 'The private calendar event could not be read') from None
    return {'id': row.id, 'workspace_id': row.workspace_id, 'revision': row.revision,
        'fields': fields.model_dump(), 'context_channel_id': payload.get('context_channel_id'),
        'created_at': row.created_at, 'updated_at': row.updated_at}


def export_personal_calendar(session, workspace_id, actor_id):
    recheck_actor(session, actor_id); require_workspace_member(session, workspace_id, actor_id)
    rows = session.exec(select(PersonalCalendarEvent).where(PersonalCalendarEvent.workspace_id == workspace_id,
        PersonalCalendarEvent.user_id == actor_id).order_by(PersonalCalendarEvent.id)).all()
    result = [personal_event_record(row) for row in rows]
    recheck_actor(session, actor_id); require_workspace_member(session, workspace_id, actor_id)
    return result


def erase_personal_calendar(session, actor_id):
    """The authorized erasure caller owns the transaction and actor decision."""
    rows = session.exec(select(PersonalCalendarEvent).where(PersonalCalendarEvent.user_id == actor_id)).all()
    for row in rows:
        session.delete(row)
    return len(rows)


def export_collection_pointers(session, channel_ids, actor_id):
    channels = list(dict.fromkeys(channel_ids))
    for channel in channels:
        require_channel_member(session, channel, actor_id)
    rows = session.exec(select(ContentCollectionItem).where(ContentCollectionItem.channel_id.in_(channels))
        .order_by(ContentCollectionItem.channel_id, ContentCollectionItem.id)).all() if channels else []
    from ..api.content_completion import Item, item_record
    from .matrix_bridge import is_secure_channel
    result = []
    for row in rows:
        # Native pointers resolve through the same canonical per-kind validator
        # as interactive collections. Encrypted collections use opaque Matrix
        # records, never this native content table.
        if is_secure_channel(session, row.channel_id):
            continue
        try:
            item_record(session, row.channel_id, actor_id, Item(kind=row.kind, id=row.resource_id, parent_id=row.parent_id))
        except HTTPException as error:
            if error.status_code not in (403, 404, 409, 413, 422):
                raise
            continue
        result.append(row.model_dump())
    recheck_actor(session, actor_id)
    for channel in channels:
        require_channel_member(session, channel, actor_id)
    return result
