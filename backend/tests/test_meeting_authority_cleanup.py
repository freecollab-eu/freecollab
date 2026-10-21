"""Private claim snapshots erase with their owner/call and never enter exports."""
from datetime import datetime, timedelta
from sqlmodel import Session
from app.models.meetings import Meeting, MeetingParticipant, MeetingParticipantAuthority
from app.models.sfu import SFURoom, SFUParticipant
from app.services import data_control as service
from tests.test_data_controls import controls, enable, preview, queue_purge, _close_feedback_account

OLD = datetime.utcnow() - timedelta(days=30)


def authority(transport, participant, meeting, user='owner'):
    return MeetingParticipantAuthority(transport=transport, participant_id=participant, meeting_id=meeting,
        user_id=user, token_hash='PRIVATE AUTH DIGEST', encrypted_claims='PRIVATE CLAIM SNAPSHOT', created_at=OLD)


def test_retention_erases_transport_bound_authorities_without_sweeping_other_channel(controls):
    _, engine, _, _ = controls
    with Session(engine) as session:
        session.add(Meeting(id='same-id', channel_id='native', host_user_id='owner', created_at=OLD, expires_at=OLD, ended_at=OLD))
        session.add_all([SFURoom(id='same-id', channel_id='hidden', host_user_id='peer', created_at=OLD, expires_at=OLD, ended_at=OLD, max_participants=100, provisioned=True), SFURoom(id='erase-sfu', channel_id='native', host_user_id='owner', created_at=OLD, expires_at=OLD, ended_at=OLD, max_participants=100, provisioned=True)])
        session.flush()
        session.add(MeetingParticipant(id='mesh-device', meeting_id='same-id', user_id='owner', name='Owner', credential_hash='private-hash', created_at=OLD, last_seen_at=OLD, expires_at=OLD, state='left'))
        for identifier, room in [('hidden-device', 'same-id'), ('sfu-device', 'erase-sfu')]:
            session.add(SFUParticipant(id=identifier, meeting_id=room, user_id='owner', name='Owner', auth_token_hash='private-hash', auth_version=0, expires_at=OLD, last_seen_at=OLD, state='left', disconnected=True, authorization_checked_at=OLD))
        session.add_all([authority('mesh', 'mesh-device', 'same-id'), authority('sfu', 'hidden-device', 'same-id'), authority('sfu', 'sfu-device', 'erase-sfu')]); session.commit()
    enable(controls)
    dry = preview(controls)
    assert dry['counts']['meetingparticipantauthority'] == 2
    queued = queue_purge(controls, dry); assert queued.status_code == 202, queued.text
    with Session(engine) as session:
        assert service.run_pending(session) == 1
        assert session.get(MeetingParticipantAuthority, ('mesh', 'mesh-device')) is None
        assert session.get(MeetingParticipantAuthority, ('sfu', 'sfu-device')) is None
        assert session.get(MeetingParticipantAuthority, ('sfu', 'hidden-device'))


def test_account_closure_erases_only_own_private_authorities_without_decrypting_them(controls, monkeypatch):
    with Session(controls[1]) as session:
        session.add_all([authority('mesh', 'own-mesh', 'retained-call'), authority('sfu', 'own-sfu', 'retained-room'), authority('mesh', 'peer-mesh', 'retained-call', user='peer')]); session.commit()
    monkeypatch.setattr('app.services.matrix_bridge.remove_secure_member', lambda *_: None)
    dry, response = _close_feedback_account(controls, controls[2]['owner'])
    assert dry['can_erase_identity'] and response.status_code == 200, response.text
    with Session(controls[1]) as session:
        assert session.get(MeetingParticipantAuthority, ('mesh', 'own-mesh')) is None
        assert session.get(MeetingParticipantAuthority, ('sfu', 'own-sfu')) is None
        assert session.get(MeetingParticipantAuthority, ('mesh', 'peer-mesh')).encrypted_claims == 'PRIVATE CLAIM SNAPSHOT'
