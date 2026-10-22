"""Reviewed calendar effects, current-source queue authority and private receipts."""
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import re
import secrets
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..models.calendar_delivery import CalendarProviderJob, CalendarProviderPublication
from ..models.integration_content import IntegrationContent
from ..models.remote_sync import RemoteSyncConnection, RemoteSyncItem
from . import remote_sync, remote_providers, remote_transport
from .account_security import _secret_key, seal_private, unseal_private
from .calendar_subscriptions import grant
from .chat_productivity import authority, bind_authority
from .request_guard import recheck_actor
from .sso_scope import require_unrestricted, clear_scope


ACTIONS = {'publish', 'cancel', 'respond', 'refresh', 'reconcile'}


def seal(value, purpose, identity):
    return seal_private(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')), purpose, identity)


def unseal(value, purpose, identity):
    return json.loads(unseal_private(value, purpose, identity)) if value else {}


def digest(value):
    return hmac.new(_secret_key(), json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode(), hashlib.sha256).hexdigest()


def attendees(values):
    if not isinstance(values, list) or len(values) > 50:
        raise HTTPException(422, 'Invite at most 50 external attendees')
    result, seen = [], set()
    for value in values:
        if not isinstance(value, dict) or set(value) - {'email', 'name', 'optional'}:
            raise HTTPException(422, 'Use explicit attendee email addresses, names and optional flags')
        address, name = value.get('email'), value.get('name', '')
        if (not isinstance(address, str) or len(address) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", address)
                or not isinstance(name, str) or len(name) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in name)
                or type(value.get('optional', False)) is not bool):
            raise HTTPException(422, 'Use a valid external attendee email address and display name')
        address = address.lower()
        if address in seen:
            raise HTTPException(422, 'Each external attendee must appear only once')
        seen.add(address)
        result.append({'email': address, 'name': name.strip(), 'optional': value.get('optional', False)})
    return sorted(result, key=lambda row: row['email'])


def source(session, channel_id, actor_id, connection_id, revision, *, lock=False):
    require_unrestricted(session)
    workspace_id = grant(session, channel_id, actor_id, lock=lock)
    recheck_actor(session, actor_id)
    row = session.get(RemoteSyncConnection, connection_id, populate_existing=True)
    if (not remote_sync.enabled() or not row or row.channel_id != channel_id or row.workspace_id != workspace_id
            or row.kind != 'calendar' or row.provider not in {'google', 'microsoft'} or row.grant_user_id != actor_id
            or row.revision != revision or not row.enabled or row.tested_revision != row.revision or not row.encrypted_credentials):
        raise HTTPException(409, 'Choose your own enabled, tested Google or Microsoft calendar connection and review its current revision')
    remote_sync.require_native_grant(session, row)
    return row


def event(session, channel_id, item_id, revision):
    from ..api.integrations_content import latest_revision, serialize
    item = session.get(IntegrationContent, item_id)
    latest = latest_revision(session, item_id) if item else None
    if not item or item.kind != 'calendar' or item.channel_id != channel_id or not latest or latest.deleted:
        raise HTTPException(404, 'Current native calendar event not found')
    if latest.revision != revision:
        raise HTTPException(409, 'The calendar event changed; review the latest version')
    payload = serialize(session, item, latest)
    if latest.content_mode != 'server' or not payload.get('fields'):
        raise HTTPException(409, 'Encrypted schedules stay on the trusted device and cannot enter provider delivery')
    return payload['fields']


def publication_data(row):
    return {'id': row.id, 'item_id': row.item_id, 'connection_id': row.connection_id,
            'connection_revision': row.connection_revision, 'revision': row.revision, 'state': row.state,
            'has_remote_event': bool(row.remote_id), 'updated_at': row.updated_at,
            'provider_event': unseal(row.encrypted_result, 'calendar-provider-result', row.id) if row.encrypted_result else None}


def job_data(row):
    return {'id': row.id, 'item_id': row.item_id, 'publication_id': row.publication_id, 'action': row.action,
            'status': row.status, 'failure_code': row.failure_code, 'created_at': row.created_at, 'finished_at': row.finished_at}


def publication(session, item_id, connection_id):
    return session.exec(select(CalendarProviderPublication).where(CalendarProviderPublication.item_id == item_id,
        CalendarProviderPublication.connection_id == connection_id)).first()


def preview(session, actor_id, channel_id, item_id, value):
    connection = source(session, channel_id, actor_id, value['connection_id'], value['connection_revision'])
    fields = event(session, channel_id, item_id, value['event_revision'])
    action = value['action']
    if action not in ACTIONS:
        raise HTTPException(422, 'Unsupported calendar provider action')
    row = publication(session, item_id, connection.id)
    expected = value.get('publication_revision', 0)
    if (row.revision if row else 0) != expected or row and row.user_id != actor_id:
        raise HTTPException(409, 'The provider publication changed; reload it before reviewing an action')
    invitees = attendees(value.get('attendees', [])) if action == 'publish' else []
    if action in {'cancel', 'respond'}:
        cached = unseal(row.encrypted_result, 'calendar-provider-result', row.id) if row and row.encrypted_result else {}
        if not cached.get('fields') or cached.get('deleted') or cached.get('etag') != row.remote_etag:
            raise HTTPException(409, 'Read provider status before reviewing its cancellation or personal response')
        # Cancellation/RSVP operate on the provider copy. Display its last
        # fenced schedule and roster, rather than imply that an empty request
        # attendee list or divergent local fields describe the external effect.
        fields, invitees = cached['fields'], cached.get('attendees', [])
    if action in {'cancel', 'respond', 'refresh'} and not (row and row.remote_id):
        mapping = session.exec(select(RemoteSyncItem).where(RemoteSyncItem.connection_id == connection.id,
            RemoteSyncItem.local_id == item_id)).first()
        if not mapping or not mapping.remote_id:
            raise HTTPException(409, 'Publish or synchronize this event before requesting its provider action')
    if action == 'respond' and value.get('response') not in {'accepted', 'tentative', 'declined'}:
        raise HTTPException(422, 'Choose accepted, tentative or declined for your provider account only')
    if row and row.state == 'uncertain' and action not in {'reconcile', 'refresh'} and not value.get('acknowledge_uncertain'):
        raise HTTPException(409, 'Check the provider outcome and explicitly acknowledge the uncertain previous action before retrying')
    reviewed = {'connection_id': connection.id, 'connection_revision': connection.revision,
        'event_revision': value['event_revision'], 'publication_revision': expected, 'action': action,
        'response': value.get('response') if action == 'respond' else None, 'attendees': invitees,
        'fields': fields, 'provider': connection.provider, 'connection_name': connection.name,
        'acknowledge_uncertain': bool(value.get('acknowledge_uncertain'))}
    return {**reviewed, 'review_digest': digest(reviewed), 'external_invitations': action in {'publish', 'cancel', 'respond'}}


def queue(session, actor_id, channel_id, item_id, value):
    source(session, channel_id, actor_id, value['connection_id'], value['connection_revision'], lock=True)
    request_hash = digest({key: value[key] for key in sorted(value) if key != 'request_id'})
    existing = session.exec(select(CalendarProviderJob).where(CalendarProviderJob.user_id == actor_id,
        CalendarProviderJob.client_id == value['request_id'])).first()
    if existing:
        if existing.item_id != item_id or existing.channel_id != channel_id or existing.request_hash != request_hash:
            raise HTTPException(409, 'This request identifier already belongs to a different reviewed action')
        return job_data(existing)
    reviewed = preview(session, actor_id, channel_id, item_id, value)
    if not value.get('provider_disclosure') or reviewed['action'] in {'publish', 'cancel', 'respond'} and not value.get('send_invitations'):
        raise HTTPException(422, 'Explicit approval of this provider disclosure and its invitation/response effects is required')
    if not hmac.compare_digest(value.get('review_digest', ''), reviewed['review_digest']):
        raise HTTPException(409, 'The reviewed calendar action changed; preview it again')
    pending = session.exec(select(CalendarProviderJob.id).where(CalendarProviderJob.channel_id == channel_id,
        CalendarProviderJob.item_id == item_id, CalendarProviderJob.status.in_(['queued', 'running'])).limit(1)).first()
    if pending:
        raise HTTPException(409, 'Wait for the current calendar action before submitting another')
    row = publication(session, item_id, reviewed['connection_id'])
    if not row:
        connection = session.get(RemoteSyncConnection, reviewed['connection_id'])
        row = CalendarProviderPublication(workspace_id=connection.workspace_id, channel_id=channel_id,
            item_id=item_id, connection_id=connection.id, user_id=actor_id, connection_revision=connection.revision)
        session.add(row); session.flush()
    if row.connection_revision != reviewed['connection_revision']:
        raise HTTPException(409, 'The original provider grant changed; inspect its existing event before continuing')
    mapping = session.exec(select(RemoteSyncItem).where(RemoteSyncItem.connection_id == row.connection_id,
        RemoteSyncItem.local_id == item_id)).first()
    if not mapping:
        mapping = RemoteSyncItem(id=row.id, connection_id=row.connection_id, local_id=item_id,
            pending_operation='calendar-review')
        session.add(mapping); session.flush()
    if not row.remote_id and mapping.remote_id:
        row.remote_id, row.remote_etag = mapping.remote_id, mapping.base_remote_etag
    reviewed['remote_key'] = 'fc-' + mapping.id
    reviewed['remote_id'], reviewed['remote_etag'] = row.remote_id, row.remote_etag
    session_hash, claims = authority(session, datetime.utcnow() + timedelta(seconds=30))
    job = CalendarProviderJob(client_id=value['request_id'], request_hash=request_hash, user_id=actor_id,
        channel_id=channel_id, item_id=item_id, publication_id=row.id, action=reviewed['action'],
        event_revision=reviewed['event_revision'], publication_revision=row.revision, connection_revision=row.connection_revision,
        session_hash=session_hash, claims_json=claims)
    job.encrypted_payload = seal(reviewed, 'calendar-provider-job', job.id)
    session.add(job); session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, 'A concurrent calendar action was already submitted; reload its receipt') from None
    return job_data(job)


def _current(session, job_id, claim):
    job = session.get(CalendarProviderJob, job_id, populate_existing=True)
    if not job or job.status != 'running' or job.claim_token != claim:
        raise HTTPException(409, 'The calendar action lease changed')
    bind_authority(session, job)
    row = session.get(CalendarProviderPublication, job.publication_id, populate_existing=True)
    if not row or row.user_id != job.user_id or row.revision != job.publication_revision:
        raise HTTPException(409, 'The provider publication changed after review')
    connection = source(session, job.channel_id, job.user_id, row.connection_id, job.connection_revision, lock=True)
    event(session, job.channel_id, job.item_id, job.event_revision)
    return job, row, connection


def _call(session, job, claim, function, *args, effect_state=None, **kwargs):
    from .provider_calendar import authority_guard
    job, row, connection = _current(session, job.id, claim)
    secret = remote_sync.credentials(connection)
    config = remote_sync._provider_config(connection, secret)
    from .external_meeting_providers import check_scope
    check_scope('google_meet' if connection.provider == 'google' else 'microsoft_teams', secret)
    budget = remote_transport.begin_budget(60)
    def accepted():
        if effect_state is not None:
            effect_state['accepted'] = True
    try:
        try:
            with authority_guard(lambda: _current(session, job.id, claim), accepted):
                return function(connection.provider, config, secret, *args, **kwargs)
        except remote_transport.ProviderError as error:
            if error.status != 401 or not secret.get('refresh_token'):
                raise
            if effect_state is not None and effect_state['accepted']:
                raise remote_transport.ProviderError('partial_effect') from None
            _current(session, job.id, claim)
            token = remote_providers.refresh_tokens(connection.provider, config, secret['refresh_token'])
            updated = {**secret, **token}
            check_scope('google_meet' if connection.provider == 'google' else 'microsoft_teams', updated)
            job, row, connection = _current(session, job.id, claim)
            remote_sync.save_credentials(connection, updated); session.add(connection); session.commit()
            job, row, connection = _current(session, job.id, claim)
            with authority_guard(lambda: _current(session, job.id, claim), accepted):
                return function(connection.provider, remote_sync._provider_config(connection, updated), updated, *args, **kwargs)
    finally:
        remote_transport.end_budget(budget)


def _finish(session, job_id, claim, *, status, code=None, result=None):
    job = session.get(CalendarProviderJob, job_id, populate_existing=True)
    if not job or job.status != 'running' or job.claim_token != claim:
        return
    row = session.get(CalendarProviderPublication, job.publication_id, populate_existing=True)
    if result is not None and row:
        row.remote_id = result.get('id', row.remote_id)
        row.remote_etag = result.get('etag', row.remote_etag)
        row.encrypted_result = seal(result, 'calendar-provider-result', row.id)
        row.state = 'cancelled' if result.get('deleted') else 'ready'
        row.revision += 1; row.updated_at = datetime.utcnow(); session.add(row)
    elif row and status == 'uncertain':
        row.state = 'uncertain'; row.updated_at = datetime.utcnow(); session.add(row)
    job.status, job.failure_code, job.finished_at = status, code, datetime.utcnow()
    job.claim_token = None; job.lease_expires_at = None
    job.encrypted_payload = None; job.claims_json = '{}'
    session.add(job); session.commit()


def run_pending(session, limit=5):
    from . import provider_calendar as provider
    now = datetime.utcnow()
    # Expired in-flight effects are uncertain, never silently dispatched again.
    expired = session.exec(select(CalendarProviderJob).where(CalendarProviderJob.status == 'running',
        CalendarProviderJob.lease_expires_at < now).limit(20)).all()
    for item in expired:
        _finish(session, item.id, item.claim_token, status='uncertain', code='dispatch_outcome_unknown')
    pending = session.exec(select(CalendarProviderJob.id).where(CalendarProviderJob.status == 'queued')
        .order_by(CalendarProviderJob.created_at).limit(min(max(limit, 1), 10))).all()
    processed = 0
    for job_id in pending:
        claim = secrets.token_hex(16)
        claimed = session.execute(update(CalendarProviderJob).where(CalendarProviderJob.id == job_id,
            CalendarProviderJob.status == 'queued').values(status='running', claim_token=claim,
                lease_expires_at=now + timedelta(minutes=5)))
        session.commit()
        if claimed.rowcount != 1:
            continue
        mutation = False
        effects = {'accepted': False}
        try:
            job, row, connection = _current(session, job_id, claim)
            payload = unseal(job.encrypted_payload, 'calendar-provider-job', job.id)
            if payload.get('event_revision') != job.event_revision or payload.get('connection_revision') != connection.revision:
                raise HTTPException(409, 'The stored calendar review context does not match')
            action = job.action
            remote_id = row.remote_id
            if action in {'refresh', 'reconcile'}:
                result = _call(session, job, claim, provider.calendar_read, remote_id) if remote_id else _call(
                    session, job, claim, provider.calendar_reconcile, payload['remote_key'])
                _current(session, job_id, claim)
                if result is None:
                    _finish(session, job_id, claim, status='failed', code='provider_event_not_found')
                    continue
            else:
                # Re-read before any update/action. Cached ETags never establish
                # permission to rewrite attendees or reply to another account.
                if remote_id:
                    fresh = _call(session, job, claim, provider.calendar_read, remote_id)
                    if payload.get('remote_etag') != fresh.get('etag'):
                        raise remote_transport.ProviderError('conflict', status=412)
                session.commit()  # Durable running marker precedes possible effect.
                job, row, connection = _current(session, job_id, claim)
                mutation = True
                if action == 'publish':
                    result = _call(session, job, claim, provider.calendar_publish, payload['fields'],
                        remote_id=remote_id, expected_etag=payload.get('remote_etag'),
                        remote_key=payload['remote_key'], attendees=payload['attendees'], effect_state=effects)
                elif action == 'cancel':
                    result = _call(session, job, claim, provider.calendar_cancel, remote_id,
                        expected_etag=payload.get('remote_etag'), effect_state=effects)
                    result = result or {'id': remote_id, 'etag': payload.get('remote_etag'), 'deleted': True}
                elif action == 'respond':
                    result = _call(session, job, claim, provider.calendar_respond, remote_id, payload['response'],
                        expected_etag=payload.get('remote_etag'), effect_state=effects)
                else:
                    raise HTTPException(422, 'Unsupported calendar action')
                _current(session, job_id, claim)
            # Link the same local resource to the existing sync mapping. Native
            # sync treats attendee events as read-only and never sends invites.
            mapping = session.exec(select(RemoteSyncItem).where(RemoteSyncItem.connection_id == connection.id,
                RemoteSyncItem.local_id == job.item_id)).first()
            if mapping and result and result.get('id'):
                mapping.remote_id = result['id']; mapping.read_only = bool(result.get('attendees'))
                remote_sync._complete_item(session, mapping, remote_sync.local_item(session, connection, job.item_id), result)
            _finish(session, job_id, claim, status='succeeded', result=result)
            processed += 1
        except HTTPException:
            session.rollback(); _finish(session, job_id, claim, status='uncertain' if mutation else 'cancelled',
                code='authority_or_review_changed')
        except remote_transport.ProviderError as error:
            session.rollback()
            before_effect = error.code in {'combined_series_edit_requires_separate_review',
                'cancelled_occurrence_requires_new_series', 'organizer_required', 'delegated_attendee_required'}
            uncertain = mutation and (effects['accepted'] or not before_effect and (error.status is None or error.status >= 500))
            safe_code = error.code if error.code in {'combined_series_edit_requires_separate_review',
                'cancelled_occurrence_requires_new_series', 'unsupported_recurrence', 'unsupported_timezone',
                'unsupported_instance', 'unsupported_attendees', 'organizer_required',
                'delegated_attendee_required', 'precondition_unavailable'} else 'provider_action_failed'
            _finish(session, job_id, claim, status='uncertain' if uncertain else 'failed',
                code='provider_conflict' if error.status in {409, 412} else 'provider_outcome_unknown' if uncertain else safe_code)
        except Exception:
            session.rollback(); _finish(session, job_id, claim, status='uncertain' if mutation else 'failed', code='calendar_action_stopped')
        finally:
            clear_scope(session)
            from .request_guard import clear_request
            clear_request(session)
    return processed


def erase_for_account(session, user_id):
    """Scrub addresses/payloads in the account-closure transaction; no provider I/O."""
    from .calendar_subscriptions import revoke_for_account
    revoke_for_account(session, user_id)
    session.execute(update(CalendarProviderPublication).where(CalendarProviderPublication.user_id == user_id)
        .values(encrypted_result=None, state='revoked'))
    session.execute(update(CalendarProviderJob).where(CalendarProviderJob.user_id == user_id)
        .values(encrypted_payload=None, claims_json='{}', status='cancelled', failure_code='account_closed',
                claim_token=None, lease_expires_at=None, finished_at=datetime.utcnow()))
