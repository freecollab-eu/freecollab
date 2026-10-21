"""0.9 modules retain the existing account, export and erasure boundaries."""
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
import pytest
from sqlmodel import Session, select

from app.api import preferences, secure_messaging
from app.models.activity_feed import ActivityFeedPost, ActivityFeedRevision
from app.models.cloud_files import CloudFileImport, CloudFileLink
from app.models.copilot import CopilotConfig, CopilotConversation, CopilotTurn
from app.models.data_control import DataControlJob
from app.models.file import File
from app.models.file_experience import FileComment
from app.models.message import Message
from app.models.notification import Notification
from app.models.preference import UserLanguage
from app.models.channel import ChannelMember
from app.services import activity_feed, copilot, data_control as service
from app.services.crypto import encrypt_message
from tests.test_product_release_integration import release
from tests.test_data_controls import controls, FILE_ID, OLD, PASSWORD, headers, enable, preview, post, queue_purge


@pytest.mark.parametrize('kind', ['feed', 'copilot', 'cloud'])
def test_new_native_history_prevents_relabeling_channel_as_e2ee(release, kind):
    with Session(release.engine) as session:
        if kind == 'feed': session.add(ActivityFeedPost(id='native-post', channel_id='channel', created_by='owner', archived=True))
        elif kind == 'copilot': session.add(CopilotConversation(id='native-ai', workspace_id='workspace', channel_id='channel', user_id='owner', secure=False))
        else: session.add(CloudFileLink(id='native-link', workspace_id='workspace', channel_id='channel', created_by='owner', content_mode='server', encrypted_payload='retained'))
        session.commit()
    result = release.client.post('/secure/channels/channel/enable', headers=release.headers())
    assert result.status_code == 409
    with Session(release.engine) as session: assert not secure_messaging.has_native_product_history(session, 'empty')


def test_language_is_available_in_customer_scoped_sso_session(release):
    release.client.app.include_router(preferences.router)
    response = release.client.put('/preferences/language', headers=release.headers('scoped'), json={'locale': 'sv'})
    assert response.status_code == 200
    assert release.client.get('/preferences/language', headers=release.headers()).json() == {'locale': 'sv'}
    assert release.client.get('/preferences/language', headers=release.headers('peer')).json() == {'locale': 'auto'}


def test_sdk_notification_filter_has_proven_channel_scope_and_private_cursors(release):
    with Session(release.engine) as session:
        session.add_all([Message(id=key, channel_id=channel, author_id='owner', content='opaque') for key, channel in [('m1', 'channel'), ('m2', 'empty')]])
        session.flush()
        session.add_all([Notification(id='n1', user_id='owner', type='mention', title='Channel one', entity_type='message', entity_id='m1'),
                         Notification(id='n2', user_id='owner', type='mention', title='Channel two', entity_type='message', entity_id='m2'),
                         Notification(id='global', user_id='owner', type='system', title='Account-wide private record')])
        session.commit()
    response = release.client.get('/notifications?channel_id=channel', headers=release.headers())
    assert response.status_code == 200 and [(row['id'], row['channel_id']) for row in response.json()] == [('n1', 'channel')]
    assert release.client.get('/notifications?channel_id=channel&before=n2', headers=release.headers()).status_code == 404
    assert release.client.get('/notifications?channel_id=channel', headers=release.headers('outsider')).status_code == 403
    with Session(release.engine) as session:
        session.delete(session.get(ChannelMember, ('channel', 'owner'))); session.commit()
    assert release.client.get('/notifications?channel_id=channel', headers=release.headers()).status_code == 403


def test_export_verifies_feed_context_and_excludes_other_members_private_ai(controls):
    _, engine, _, _ = controls
    with Session(engine) as session:
        session.add(ActivityFeedPost(id='post', channel_id='native', created_by='owner', revision=1))
        session.flush()
        session.add(ActivityFeedRevision(post_id='post', revision=1, created_by='owner', content_mode='server',
            encrypted_payload=activity_feed.seal_fields('native', 'post', 'post', 1, {'title':'PUBLIC FEED TITLE', 'body':'Shared body', 'poll':None})))
        session.add(CopilotConfig(workspace_id='a', updated_by='owner', encrypted_config=copilot.seal({'api_key':'NEVER EXPORT PROVIDER KEY'}, 'config', 'a')))
        for who in ('owner', 'member'):
            session.add(CopilotConversation(id='ai-'+who, workspace_id='a', channel_id='native', user_id=who, secure=False))
            session.flush()
            identifier = 'turn-'+who
            session.add(CopilotTurn(id=identifier, conversation_id='ai-'+who, request_id=identifier, request_digest='digest', config_revision=1, status='complete',
                encrypted_sources=copilot.seal([], 'sources', identifier), encrypted_result=copilot.seal({'prompt':'PRIVATE '+who,'text':'ANSWER '+who,'citations':[]}, 'result', identifier)))
        session.add(UserLanguage(user_id='owner', locale='sv')); session.commit()
        exported, _, _ = service.export_data(session, SimpleNamespace(workspace_id='a', requested_by='owner'), {'channel_ids':['native']})
        serialized = json.dumps(exported, default=str)
        assert exported['activityfeedrevision'][0]['fields']['title'] == 'PUBLIC FEED TITLE'
        assert 'PRIVATE owner' in serialized and 'PRIVATE member' not in serialized and 'NEVER EXPORT PROVIDER KEY' not in serialized
        assert exported['personal_preferences']['language'] == 'sv'


def test_new_file_comment_protects_old_file_and_import_erasure_preserves_retry_tombstone(controls):
    _, engine, _, _ = controls
    with Session(engine) as session:
        session.add(FileComment(id='comment', channel_id='native', document_id=FILE_ID, file_id=FILE_ID, created_by='owner', encrypted_text='opaque'))
        session.add(CloudFileImport(user_id='owner', request_id='picker-retry', channel_id='native', fingerprint='digest', file_id=FILE_ID, created_at=OLD))
        session.commit()
    enable(controls)
    dry = preview(controls)
    assert dry['counts'].get('file', 0) == 0
    with Session(engine) as session:
        comment = session.get(FileComment, 'comment'); comment.created_at = OLD; session.add(comment); session.commit()
    dry = preview(controls)
    assert dry['counts']['file'] == 1 and dry['counts']['filecomment'] == 1
    queued = queue_purge(controls, dry); assert queued.status_code == 202
    with Session(engine) as session:
        assert service.run_pending(session, job_id=queued.json()['id']) == 1
        assert session.get(File, FILE_ID) is None and session.get(FileComment, 'comment') is None
        assert session.get(CloudFileImport, ('owner', 'picker-retry')).file_id is None


def test_identity_closure_removes_personal_language_and_private_ai_history(controls):
    client, engine, _, _ = controls
    with Session(engine) as session:
        session.add(UserLanguage(user_id='member', locale='sv'))
        session.add(CopilotConversation(id='private-ai', workspace_id='a', channel_id='native', user_id='member', secure=False)); session.flush()
        session.add(CopilotTurn(id='private-turn', conversation_id='private-ai', request_id='request', request_digest='digest', config_revision=1,
            encrypted_sources=copilot.seal([], 'sources', 'private-turn'))); session.commit()
    dry = client.get('/account/data-closure', headers=headers(controls, 'member')).json()
    response = client.post('/account/data-closure', headers=headers(controls, 'member'), json={'password':PASSWORD,
        'preview_fingerprint':dry['fingerprint'], 'confirmation':'ERASE MY ACCOUNT', 'acknowledge_retained_shared_authorship_and_backups':True})
    assert response.status_code == 200, response.text
    with Session(engine) as session:
        assert session.get(UserLanguage, 'member') is None
        assert session.get(CopilotConversation, 'private-ai') is None and session.get(CopilotTurn, 'private-turn') is None
