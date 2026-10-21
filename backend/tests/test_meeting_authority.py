"""Meeting capabilities preserve verified native/embed restrictions independently."""
import pytest
from sqlmodel import Session
from app.api import native_clients, meetings
from app.models.meetings import MeetingParticipantAuthority
from app.models.native_clients import NativeClientSession
from app.models.workspace_oidc import WorkspaceOIDCProvider
from app.models.embed import EmbedSession
from app.models.sfu import SFUParticipant
from app.services.auth import decode_token_claims, revoke_token, token_digest
from app.services import meetings as service
from tests.test_sfu import livekit, create
from tests.test_meetings import team
from tests.test_native_clients import connect, exchange, bearer
from tests.test_embed_identity import api, child


def native_child(d, source):
    d.client.app.include_router(native_clients.router)
    grant = connect(d.client, source); assert grant.status_code == 200, grant.text
    response = exchange(d.client, grant.json()['code']); assert response.status_code == 200, response.text
    token = response.json()['access_token']
    assert d.client.get('/auth/me', headers=bearer(token)).status_code == 200
    return token


@pytest.mark.parametrize('transport', ['mesh', 'sfu'])
@pytest.mark.parametrize('mutation', ['parent_logout', 'native_sidecar', 'authority_snapshot', 'sso_revision'])
def test_real_native_child_join_stays_active_then_fails_closed_for_source_change(livekit, transport, mutation):
    d = livekit; source = d.scoped if mutation == 'sso_revision' else d.tokens['owner']
    token = native_child(d, source)
    if transport == 'mesh':
        response = d.client.post('/channels/shared/meetings', json={}, headers=bearer(token)); assert response.status_code == 201, response.text
        access = response.json(); identifier = access['meeting']['id']; pid = access['participant']['id']
        def current(): return d.client.get(f'/meeting-access/{identifier}/poll', headers={'X-Meeting-Token': access['meeting_token']})
    else:
        identifier = create(d).json()['id']
        response = d.client.post(f'/large-meetings/{identifier}/join', json={}, headers=bearer(token)); assert response.status_code == 200, response.text
        access = response.json(); pid = access['participant_id']
        def current(): return d.client.post(f'/large-meetings/{identifier}/participants/{pid}/heartbeat', json={}, headers=bearer(token))
    assert current().status_code == 200
    with Session(d.engine) as session:
        snapshot = session.get(MeetingParticipantAuthority, (transport, pid)); assert snapshot
        participant = session.get(service.MeetingParticipant if transport == 'mesh' else SFUParticipant, pid)
        retained = service.participant_claims(session, participant)
        signed = decode_token_claims(token)
        assert all(retained.get(key) == signed.get(key) for key in service.AUTHORITY_CLAIMS)
        assert retained['native_client_id'] and token not in snapshot.encrypted_claims and 'native_client_id' not in snapshot.encrypted_claims
        if mutation == 'parent_logout': revoke_token(source, session)
        elif mutation == 'native_sidecar': session.delete(session.get(NativeClientSession, token_digest(token)))
        elif mutation == 'authority_snapshot': session.delete(snapshot)
        else:
            provider = session.get(WorkspaceOIDCProvider, 'a'); provider.revision += 1; session.add(provider)
        session.commit()
        if transport == 'sfu':
            from app.services.sfu import reconcile
            assert reconcile(session)['removed'] == 1
    assert current().status_code in {401, 403, 410}
    if mutation == 'authority_snapshot':
        # An ordinary REST session can remain valid while its call capability
        # is fenced by an independently missing meeting authority snapshot.
        assert d.client.get('/auth/me', headers=bearer(token)).status_code == 200


def test_embed_child_meeting_retains_marker_and_sidecar_loss_cannot_upgrade_it(api):
    api.client.app.include_router(meetings.router)
    token = child(api)
    response = api.client.post('/channels/shared/meetings', json={}, headers=bearer(token)); assert response.status_code == 201, response.text
    access = response.json(); identifier = access['meeting']['id']; pid = access['participant']['id']
    poll = lambda: api.client.get(f'/meeting-access/{identifier}/poll', headers={'X-Meeting-Token': access['meeting_token']})
    assert poll().status_code == 200
    with Session(api.engine) as session:
        participant = session.get(service.MeetingParticipant, pid)
        assert service.participant_claims(session, participant)['embed_application_id'] == 'application'
        session.delete(session.get(EmbedSession, token_digest(token))); session.commit()
    assert poll().status_code == 410


def test_legacy_call_without_authority_ends_and_channel_can_start_fresh(team):
    client, engine, tokens, _ = team
    auth = bearer(tokens['owner'])
    response = client.post('/channels/shared/meetings', json={}, headers=auth); assert response.status_code == 201
    access = response.json(); old = access['meeting']['id']; pid = access['participant']['id']
    with Session(engine) as session:
        session.delete(session.get(MeetingParticipantAuthority, ('mesh', pid))); session.commit()
    assert client.get(f'/meeting-access/{old}/poll', headers={'X-Meeting-Token': access['meeting_token']}).status_code == 410
    fresh = client.post('/channels/shared/meetings', json={}, headers=auth); assert fresh.status_code == 201, fresh.text
    assert fresh.json()['meeting']['id'] != old
    with Session(engine) as session: assert session.get(service.Meeting, old).ended_at is not None
