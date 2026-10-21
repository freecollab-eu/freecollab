"""Calendar recurrence, invitation authority and bounded shared-time privacy."""
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from app.api import calendar_planning, integrations_content, notifications
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.calendar_planning import CalendarAttendee
from app.models.integration_content import IntegrationContentRevision
from app.models.notification import Notification
from app.models.secure_channel import SecureChannel
from app.models.user import User
from app.services.auth import create_token
from app.services.calendar_planning import expand_occurrences
from app.services.integration_content import CalendarFields

EVENT = {"title": "Team standup", "description": "Private discussion", "location": "Studio", "starts_at": "2026-10-23T07:00:00Z", "ends_at": "2026-10-23T07:30:00Z", "timezone": "Europe/Stockholm", "all_day": False, "reminder_minutes": None, "external_uid": None}
HEADERS = lambda user="owner": {"Authorization": "Bearer " + create_token(user)}


def expand(fields, start="2026-01-01T00:00:00+00:00", end="2028-01-01T00:00:00+00:00"):
    return expand_occurrences(fields, datetime.fromisoformat(start), datetime.fromisoformat(end))


def test_daily_series_preserves_wall_clock_across_dst_and_exact_first_overlap():
    fields = CalendarFields.model_validate({**EVENT, "recurrence": {"frequency": "daily", "count": 4}})
    assert [row["starts_at"] for row in expand(fields)] == ["2026-10-23T07:00:00Z", "2026-10-24T07:00:00Z", "2026-10-25T08:00:00Z", "2026-10-26T08:00:00Z"]
    overlap = {**EVENT, "starts_at": "2026-10-25T01:30:00Z", "ends_at": "2026-10-25T02:00:00Z", "recurrence": {"frequency": "daily", "count": 2}}
    assert expand(overlap)[0]["starts_at"] == "2026-10-25T01:30:00Z"


def test_monthly_skips_absent_dates_and_daily_skips_nonexistent_wall_times():
    fields = {**EVENT, "starts_at": "2026-01-31", "ends_at": "2026-02-01", "all_day": True, "recurrence": {"frequency": "monthly", "count": 3}}
    assert [row["starts_at"] for row in expand(fields)] == ["2026-01-31", "2026-03-31", "2026-05-31"]
    fields = {**EVENT, "starts_at": "2026-03-28T01:30:00Z", "ends_at": "2026-03-28T02:00:00Z", "recurrence": {"frequency": "daily", "count": 3}}
    assert [row["starts_at"] for row in expand(fields)] == ["2026-03-28T01:30:00Z", "2026-03-30T00:30:00Z", "2026-03-31T00:30:00Z"]


def test_exceptions_cancel_and_move_into_window_without_rewriting_identity():
    fields = {**EVENT, "recurrence": {"frequency": "weekly", "count": 3}, "exceptions": [
        {"original_start": "2026-10-30T08:00:00Z", "cancelled": True},
        {"original_start": "2026-11-06T08:00:00Z", "starts_at": "2026-10-27T12:00:00Z", "ends_at": "2026-10-27T13:00:00Z", "title": "Moved"}]}
    rows = expand(fields, "2026-10-27T00:00:00+00:00", "2026-10-28T00:00:00+00:00")
    assert len(rows) == 1 and rows[0]["title"] == "Moved" and rows[0]["original_start"] == "2026-11-06T08:00:00Z"


@pytest.mark.parametrize("change", [
    {"recurrence": {"frequency": "yearly", "count": 3}}, {"recurrence": {"frequency": "daily", "count": 367}},
    {"recurrence": {"frequency": "daily", "interval": 0}},
    {"exceptions": [{"original_start": EVENT["starts_at"], "cancelled": True}]},
    {"recurrence": {"frequency": "daily", "count": 2}, "exceptions": [{"original_start": "2026-11-03T07:00:00Z", "cancelled": True}]},
    {"recurrence": {"frequency": "daily", "count": 2}, "exceptions": [{"original_start": EVENT["starts_at"], "cancelled": True, "title": "Hidden"}]},
    {"recurrence": {"frequency": "daily", "count": 2}, "exceptions": [{"original_start": EVENT["starts_at"], "starts_at": "2026-10-23T12:00:00Z"}]},
])
def test_invalid_recurrence_and_exceptions_are_rejected(change):
    with pytest.raises(ValueError): CalendarFields.model_validate({**EVENT, **change})


@pytest.fixture
def calendar():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([User(id=name, name=name, email=name + "@example.com", password_hash="unused") for name in ("owner", "member", "peer", "outside")])
        session.add_all([Channel(id="room", name="Room"), Channel(id="private", name="Private")]); session.commit()
        session.add_all([ChannelMember(channel_id="room", user_id=name, role="admin" if name == "owner" else "member") for name in ("owner", "member", "peer")])
        session.add(ChannelMember(channel_id="private", user_id="outside", role="admin")); session.commit()
    app = FastAPI(); app.include_router(calendar_planning.router); app.include_router(integrations_content.router); app.include_router(notifications.router)
    def sessions():
        with Session(engine) as session: yield session
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client: yield SimpleNamespace(client=client, engine=engine)
    engine.dispose()


def create(calendar, id="event", user="owner", fields=None):
    result = calendar.client.post('/channels/room/integrations/calendar', json={"id": id, "fields": fields or EVENT}, headers=HEADERS(user))
    assert result.status_code == 200, result.text
    return result.json()


def test_only_organizer_or_admin_invites_only_current_members_and_revisions(calendar):
    create(calendar, user="member")
    path = '/channels/room/calendar/event/attendees'
    assert calendar.client.put(path, json={"expected_revision": 1, "user_ids": ["peer"]}, headers=HEADERS("peer")).status_code == 403
    assert calendar.client.put(path, json={"expected_revision": 1, "user_ids": ["outside"]}, headers=HEADERS("member")).status_code == 422
    assert calendar.client.put(path, json={"expected_revision": 2, "user_ids": ["peer"]}, headers=HEADERS("member")).status_code == 409
    result = calendar.client.put(path, json={"expected_revision": 1, "user_ids": ["peer"]}, headers=HEADERS())
    assert result.status_code == 200 and result.json()["attendees"][0]["response"] == "needs-action"
    assert calendar.client.get(path, headers=HEADERS("outside")).status_code == 403
    with Session(calendar.engine) as session:
        invitation = session.exec(select(Notification)).one()
        assert invitation.title == 'Calendar invitation updated' and EVENT["title"] not in invitation.body


def test_rsvp_is_personal_current_revision_and_event_updates_reset_it_atomically(calendar):
    create(calendar)
    path = '/channels/room/calendar/event'
    calendar.client.put(path + '/attendees', json={"expected_revision": 1, "user_ids": ["member"]}, headers=HEADERS())
    assert calendar.client.put(path + '/response', json={"expected_revision": 1, "response": "accepted"}, headers=HEADERS("peer")).status_code == 403
    accepted = calendar.client.put(path + '/response', json={"expected_revision": 1, "response": "accepted"}, headers=HEADERS("member"))
    assert accepted.json()["attendees"][0]["response"] == "accepted"
    edited = calendar.client.post('/channels/room/integrations/calendar', json={"id": "event", "expected_revision": 1, "fields": {**EVENT, "title": "Changed"}}, headers=HEADERS())
    assert edited.status_code == 200, edited.text
    response = calendar.client.get(path + '/attendees', headers=HEADERS("member")).json()
    assert response["event_revision"] == 2 and response["attendees"][0]["response"] == "needs-action"
    assert calendar.client.put(path + '/response', json={"expected_revision": 1, "response": "accepted"}, headers=HEADERS("member")).status_code == 409
    with Session(calendar.engine) as session:
        session.delete(session.get(ChannelMember, ("room", "member"))); session.commit()
    assert calendar.client.put(path + '/response', json={"expected_revision": 2, "response": "accepted"}, headers=HEADERS("member")).status_code == 403
    assert calendar.client.get(path + '/attendees', headers=HEADERS()).json()["attendees"] == []
    assert calendar.client.get('/notifications', headers=HEADERS("member")).json() == []


def test_deletion_removes_invitations_and_hides_old_notifications(calendar):
    create(calendar)
    calendar.client.put('/channels/room/calendar/event/attendees', json={"expected_revision": 1, "user_ids": ["member"]}, headers=HEADERS())
    assert calendar.client.get('/notifications', headers=HEADERS("member")).json()
    assert calendar.client.post('/channels/room/integrations/calendar', json={"id": "event", "expected_revision": 1, "deleted": True}, headers=HEADERS()).status_code == 200
    assert calendar.client.get('/channels/room/calendar/event/attendees', headers=HEADERS()).status_code == 404
    assert calendar.client.get('/notifications', headers=HEADERS("member")).json() == []
    with Session(calendar.engine) as session: assert session.exec(select(CalendarAttendee)).all() == []


def test_availability_is_bounded_channel_only_and_recurrence_aware(calendar):
    create(calendar, fields={**EVENT, "recurrence": {"frequency": "daily", "count": 4}})
    payload = {"starts_at": "2026-10-25T07:30:00Z", "ends_at": "2026-10-25T09:00:00Z", "user_ids": ["owner"], "duration_minutes": 30}
    response = calendar.client.post('/channels/room/calendar/availability', json=payload, headers=HEADERS("member"))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["private_calendars_included"] is False and result["scope"] == 'selected-channel-only'
    assert result["busy"] == [{"starts_at": "2026-10-25T08:00:00Z", "ends_at": "2026-10-25T08:30:00Z"}]
    assert result["slots"] == [{"starts_at": "2026-10-25T07:30:00Z", "ends_at": "2026-10-25T08:00:00Z"}, {"starts_at": "2026-10-25T08:30:00Z", "ends_at": "2026-10-25T09:00:00Z"}]
    assert EVENT["title"] not in response.text and EVENT["description"] not in response.text
    assert calendar.client.post('/channels/room/calendar/availability', json={**payload, "user_ids": ["outside"]}, headers=HEADERS()).status_code == 422
    assert calendar.client.post('/channels/room/calendar/availability', json={**payload, "ends_at": "2027-01-01T00:00:00Z"}, headers=HEADERS()).status_code == 422
    assert calendar.client.post('/channels/private/calendar/availability', json=payload, headers=HEADERS()).status_code == 403


def test_secure_availability_never_reads_native_plaintext_and_invites_accept_opaque_reference(calendar, monkeypatch):
    with Session(calendar.engine) as session:
        session.add(SecureChannel(channel_id="room", created_by="owner", status="preparing")); session.commit()
    monkeypatch.setattr(integrations_content, 'validate_secure_event', lambda *args: None)
    response = calendar.client.post('/channels/room/integrations/calendar', json={"id": "event", "matrix_event_id": "$opaque"}, headers=HEADERS())
    assert response.status_code == 200 and "fields" not in response.json()
    assert calendar.client.put('/channels/room/calendar/event/attendees', json={"expected_revision": 1, "user_ids": ["member"]}, headers=HEADERS()).status_code == 200
    assert calendar.client.get('/notifications', headers=HEADERS("member")).json()
    payload = {"starts_at": "2026-10-25T07:30:00Z", "ends_at": "2026-10-25T09:00:00Z", "user_ids": ["owner"]}
    assert calendar.client.post('/channels/room/calendar/availability', json=payload, headers=HEADERS()).status_code == 409
    with Session(calendar.engine) as session:
        assert session.exec(select(IntegrationContentRevision)).one().encrypted_payload is None


def test_cloud_sync_cannot_silently_flatten_a_local_series():
    from app.services import remote_providers, remote_dav
    from app.services.remote_transport import ProviderError
    fields = {**EVENT, "recurrence": {"frequency": "daily", "count": 3}}
    for provider in ("google", "microsoft"):
        with pytest.raises(ProviderError) as error:
            remote_providers._calendar_write(fields, provider)
        assert error.value.code == "unsupported_recurrence"
    with pytest.raises(ProviderError) as error:
        remote_dav.serialize_calendar(fields)
    assert error.value.code == "unsupported_recurrence"


def test_native_retention_plan_includes_invitation_dependents(calendar):
    from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
    from app.services.data_control import build_plan
    from datetime import timedelta
    create(calendar)
    calendar.client.put('/channels/room/calendar/event/attendees', json={"expected_revision": 1, "user_ids": ["member"]}, headers=HEADERS())
    with Session(calendar.engine) as session:
        session.add(Workspace(id="wa", name="Workspace")); session.commit()
        session.add_all([WorkspaceChannel(channel_id="room", workspace_id="wa"), WorkspaceMember(workspace_id="wa", user_id="owner", role="owner"), WorkspaceMember(workspace_id="wa", user_id="member", role="member")]); session.commit()
        plan = build_plan(session, "wa", "owner", ["room"], datetime.utcnow() + timedelta(days=1))
        assert plan["calendarattendee"][0]["item_id"] == "event"
        assert plan["notification"][0]["entity_type"] == "calendar_invitation"
