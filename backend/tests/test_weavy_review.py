"""Independent 0.9 integration checks for scoped exports and retained history."""
from datetime import datetime
import json
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from sqlmodel import Session, select

from app.api import notifications
from app.models.activity_feed import (ActivityFeedChoice, ActivityFeedChoiceRevision, ActivityFeedComment,
    ActivityFeedCommentRevision, ActivityFeedPost, ActivityFeedRevision)
from app.models.channel import ChannelMember
from app.models.cloud_files import CloudFileLink
from app.models.copilot import CopilotConversation, CopilotTurn
from app.models.notification import Notification
from app.services import activity_feed, copilot, data_control as service
from app.services.crypto import encrypt_message
from tests.test_data_controls import controls, OLD, enable, headers, post, preview, queue_purge


def feed_tree(session, channel='native', identity='review-post', *, secure=False):
    author = 'peer' if channel == 'hidden' else 'owner'
    post_fields = {'title': 'PRIVATE ' + channel, 'body': 'BODY ' + channel, 'poll': None}
    post_row = ActivityFeedPost(id=identity, channel_id=channel, created_by=author, revision=1, created_at=OLD, updated_at=OLD)
    session.add(post_row); session.flush()
    post_revision = ActivityFeedRevision(post_id=identity, revision=1, created_by=author, created_at=OLD,
        content_mode='matrix' if secure else 'server', matrix_event_id='$' + identity if secure else None,
        encrypted_payload=None if secure else activity_feed.seal_fields(channel, 'post', identity, 1, post_fields))
    comment = ActivityFeedComment(id=identity + '-comment', post_id=identity, created_by=author, revision=1, created_at=OLD, updated_at=OLD)
    session.add(comment); session.flush()
    comment_revision = ActivityFeedCommentRevision(comment_id=comment.id, revision=1, created_by=author, created_at=OLD,
        content_mode='matrix' if secure else 'server', matrix_event_id='$' + comment.id if secure else None,
        encrypted_payload=None if secure else activity_feed.seal_fields(channel, 'comment', comment.id, 1, {'body': 'COMMENT ' + channel}))
    choice = ActivityFeedChoice(post_id=identity, user_id=author, kind='reaction', revision=1, updated_at=OLD)
    choice_revision = ActivityFeedChoiceRevision(post_id=identity, user_id=author, kind='reaction', revision=1, created_at=OLD,
        content_mode='matrix' if secure else 'server', matrix_event_id='$' + identity + '-reaction' if secure else None,
        encrypted_payload=None if secure else activity_feed.seal_fields(channel, 'reaction', identity + ':' + author + ':reaction', 1, {'emoji': 'heart'}))
    session.add_all([post_revision, comment_revision, choice, choice_revision]); session.commit()
    return post_row, post_revision, comment, comment_revision, choice, choice_revision


def export(session, channels=None):
    return service.export_data(session, SimpleNamespace(workspace_id='a', requested_by='owner'), {'channel_ids': channels or ['native']})[0]


def test_feed_parent_mapping_exports_native_comments_choices_and_only_secure_pointers(controls):
    with Session(controls[1]) as session:
        feed_tree(session)
        feed_tree(session, 'hidden', 'hidden-post')
        feed_tree(session, 'secure', 'secure-post', secure=True)
        data = export(session, ['native', 'secure'])
        assert len(data['activityfeedcommentrevision']) == len(data['activityfeedchoicerevision']) == 2
        assert next(row for row in data['activityfeedrevision'] if row['post_id'] == 'review-post')['fields']['title'] == 'PRIVATE native'
        assert next(row for row in data['activityfeedcommentrevision'] if row['comment_id'] == 'review-post-comment')['fields']['body'] == 'COMMENT native'
        assert next(row for row in data['activityfeedchoicerevision'] if row['post_id'] == 'review-post')['fields']['emoji'] == 'heart'
        for table in ['activityfeedrevision', 'activityfeedcommentrevision', 'activityfeedchoicerevision']:
            secure = [row for row in data[table] if row['content_mode'] == 'matrix']
            assert len(secure) == 1 and 'fields' not in secure[0] and secure[0].get('encrypted_payload') is None and secure[0]['matrix_event_id']
        assert 'hidden-post' not in json.dumps(data, default=str) and 'COMMENT hidden' not in json.dumps(data, default=str)


@pytest.mark.parametrize('kind', ['post', 'comment', 'reaction'])
def test_export_rejects_feed_ciphertext_rebound_to_another_record_context(controls, kind):
    with Session(controls[1]) as session:
        rows = feed_tree(session)
        target = rows[1 if kind == 'post' else 3 if kind == 'comment' else 5]
        value = {'title': 'secret', 'body': '', 'poll': None} if kind == 'post' else {'body': 'secret'} if kind == 'comment' else {'emoji': 'heart'}
        target.encrypted_payload = activity_feed.seal_fields('native', kind, 'different-record', 1, value)
        session.add(target); session.commit()
        with pytest.raises(HTTPException) as rejected: export(session)
        assert rejected.value.status_code == 409


def test_export_record_limit_counts_private_ai_and_members_together(controls, monkeypatch):
    with Session(controls[1]) as session:
        baseline = export(session)
        channel_rows = sum(len(value) for key, value in baseline.items() if isinstance(value, list) and key not in {'members', 'limitations', 'copilot_conversations'})
        limit = channel_rows + len(baseline['members'])
        session.add(CopilotConversation(id='own-private-ai', workspace_id='a', channel_id='native', user_id='owner', secure=False, created_at=OLD)); session.flush()
        session.add(CopilotTurn(id='own-private-turn', conversation_id='own-private-ai', request_id='review-request', request_digest='opaque', config_revision=1,
            status='complete', encrypted_sources=copilot.seal([], 'sources', 'own-private-turn'),
            encrypted_result=copilot.seal({'prompt':'private prompt','text':'private result','citations':[]}, 'result', 'own-private-turn')))
        session.commit()
        monkeypatch.setattr(service, 'MAX_ROWS', limit)
        with pytest.raises(HTTPException) as rejected: export(session)
        assert rejected.value.status_code == 413


def test_cloud_link_export_checks_same_envelope_identity_as_normal_api_reads(controls):
    with Session(controls[1]) as session:
        envelope = {'id': 'original-link', 'channel_id': 'native', 'fields': {
            'provider': 'google', 'title': 'Reviewed document', 'url': 'https://drive.google.com/file/d/private-document/view',
            'external_access_acknowledged': True}}
        session.add(CloudFileLink(id='different-link', workspace_id='a', channel_id='native', created_by='owner',
            content_mode='server', encrypted_payload=encrypt_message(json.dumps(envelope), 'native')))
        session.commit()
        with pytest.raises(HTTPException) as rejected: export(session)
        assert rejected.value.status_code == 409


def test_recent_feed_comment_or_choice_protects_entire_old_discussion_from_purge(controls):
    with Session(controls[1]) as session:
        rows = feed_tree(session)
        rows[3].created_at = datetime.utcnow()
        session.add(rows[3]); session.commit()
    enable(controls)
    assert preview(controls)['counts'].get('activityfeedpost', 0) == 0
    with Session(controls[1]) as session:
        comment_revision = session.exec(select(ActivityFeedCommentRevision)).one(); comment_revision.created_at = OLD
        choice_revision = session.exec(select(ActivityFeedChoiceRevision)).one(); choice_revision.created_at = datetime.utcnow()
        session.add_all([comment_revision, choice_revision]); session.commit()
    assert preview(controls)['counts'].get('activityfeedpost', 0) == 0
    with Session(controls[1]) as session:
        row = session.exec(select(ActivityFeedChoiceRevision)).one(); row.created_at = OLD; session.add(row); session.commit()
    dry = preview(controls)
    assert dry['counts']['activityfeedpost'] == dry['counts']['activityfeedcommentrevision'] == dry['counts']['activityfeedchoicerevision'] == 1
    queued = queue_purge(controls, dry)
    assert queued.status_code == 202
    with Session(controls[1]) as session:
        assert service.run_pending(session, job_id=queued.json()['id']) == 1
        for model in [ActivityFeedPost, ActivityFeedRevision, ActivityFeedComment, ActivityFeedCommentRevision, ActivityFeedChoice, ActivityFeedChoiceRevision]:
            assert not session.exec(select(model)).all()


def test_secure_feed_notice_has_current_channel_scope_and_disappears_when_archived(controls):
    client, engine, _, _ = controls
    client.app.include_router(notifications.router)
    with Session(engine) as session:
        feed_tree(session, 'secure', 'secure-notice-post', secure=True)
        session.add(Notification(id='secure-feed-notice', user_id='owner', type='system', title='New activity comment',
            body=None, entity_type='activity_feed_post', entity_id='secure-notice-post', action_url='#channel=other'))
        session.commit()
    response = client.get('/notifications?channel_id=secure', headers=headers(controls))
    assert response.status_code == 200 and [(row['id'], row['channel_id']) for row in response.json()] == [('secure-feed-notice', 'secure')]
    assert not client.get('/notifications?channel_id=native', headers=headers(controls)).json()
    with Session(engine) as session:
        row = session.get(ActivityFeedPost, 'secure-notice-post'); row.archived = True; session.add(row); session.commit()
    assert not client.get('/notifications?channel_id=secure', headers=headers(controls)).json()
    with Session(engine) as session:
        session.delete(session.get(ChannelMember, ('secure', 'owner'))); session.commit()
    assert client.get('/notifications?channel_id=secure', headers=headers(controls)).status_code == 403
