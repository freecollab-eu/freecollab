"""Production feed authority, stable revisions and encrypted poll/choice boundaries."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlmodel import Session, select

from app.api import activity_feed
from app.config import settings
from app.models.activity_feed import ActivityFeedChoice, ActivityFeedChoiceRevision, ActivityFeedCommentRevision, ActivityFeedFollow, ActivityFeedPost, ActivityFeedRead, ActivityFeedRevision
from app.models.channel import ChannelMember
from app.models.notification import Notification
from app.models.secure_channel import SecureChannel
from app.models.session import RevokedToken
from app.services import activity_feed as service
from app.services.auth import token_digest
from tests.test_workflows import flows, headers

BASE = '/channels/channel-a/activity-feed'


@pytest.fixture
def feed(flows, monkeypatch):
    if hasattr(settings, 'activity_feeds_enabled'): monkeypatch.setattr(settings, 'activity_feeds_enabled', True)
    flows[0].app.include_router(activity_feed.router)
    return flows


def fields(*, poll=False):
    return {'title': 'Studio announcement', 'body': '**Safe** text\n- A useful update\n<script>never execute</script>', 'poll':
        {'id': 'a' * 32, 'nonce': 'b' * 32, 'question': 'When should we meet?', 'options': [
            {'id': 'c' * 32, 'label': 'Monday'}, {'id': 'd' * 32, 'label': 'Tuesday'}], 'multiple': False, 'closes_at': None} if poll else None}


def write(feed, *, identifier='post', actor='owner', revision=0, archived=False, content=None):
    client, _, tokens = feed
    return client.post(BASE, headers=headers(tokens, actor), json={'id': identifier, 'expected_revision': revision,
        'archived': archived, 'fields': content or fields()})


def choice(feed, kind='vote', *, actor='member', revision=0, body=None):
    client, _, tokens = feed
    data = {'expected_revision': revision, 'fields': body or ({'option_ids': ['c' * 32]} if kind == 'vote' else {'emoji': 'like'})}
    if kind == 'vote': data['poll_id'] = 'a' * 32
    return client.post(BASE + '/post/' + kind, headers=headers(tokens, actor), json=data)


def test_feed_is_production_without_enabling_experimental_posts_and_encrypts_native_fields(feed):
    client, engine, tokens = feed
    assert not settings.enable_experimental_features
    created = write(feed)
    assert created.status_code == 201, created.text
    assert created.headers['cache-control'] == 'no-store'
    assert created.json()['fields'] == fields()
    with Session(engine) as session:
        row = session.exec(select(ActivityFeedRevision)).one()
        assert 'Studio announcement' not in row.encrypted_payload and row.matrix_event_id is None
    assert client.get(BASE, headers=headers(tokens, 'member')).json()['items'][0]['fields']['title'] == fields()['title']
    assert client.get(BASE, headers=headers(tokens, 'outsider')).status_code == 403
    assert client.get('/channels/channel-b/activity-feed/post', headers=headers(tokens, 'outsider')).status_code == 404


def test_native_scoped_members_can_publish_but_cannot_spoof_authors(feed):
    client, _, tokens = feed
    assert write(feed, actor='scoped-approver').status_code == 201
    response = client.post(BASE, headers=headers(tokens), json={'id': 'spoofed', 'fields': fields(), 'created_by': 'member'})
    assert response.status_code == 422
    assert write(feed, identifier='second', actor='outsider').status_code == 403


def test_post_author_admin_cas_history_and_archive_restore(feed):
    client, _, tokens = feed
    assert write(feed).status_code == 201
    assert write(feed, actor='member', revision=1).status_code == 403
    changed = {**fields(), 'title': 'Updated announcement'}
    assert write(feed, actor='other-admin', revision=1, content=changed).json()['revision'] == 2
    assert write(feed, revision=1, content={**fields(), 'title': 'Stale update'}).status_code == 409
    assert write(feed, revision=2, archived=True, content=changed).status_code == 201
    assert not client.get(BASE, headers=headers(tokens)).json()['items']
    assert client.get(BASE + '?archived=true', headers=headers(tokens)).json()['items'][0]['archived']
    assert choice(feed, 'reaction').status_code == 409
    assert write(feed, revision=3, content=changed).status_code == 201
    history = client.get(BASE + '/post/revisions', headers=headers(tokens)).json()
    assert [row['revision'] for row in history] == [4, 3, 2, 1]
    assert history[-1]['fields']['title'] == fields()['title'] and history[1]['archived']


def test_lost_ack_retry_never_duplicates_revision_comment_or_notice(feed):
    client, engine, tokens = feed
    assert write(feed).status_code == 201
    assert write(feed).json()['revision'] == 1
    payload = {'id': 'comment', 'fields': {'body': 'A private comment'}}
    for _ in range(2):
        response = client.post(BASE + '/post/comments', headers=headers(tokens, 'member'), json=payload)
        assert response.status_code == 201, response.text
    with Session(engine) as session:
        assert len(session.exec(select(ActivityFeedRevision)).all()) == 1
        assert len(session.exec(select(ActivityFeedCommentRevision)).all()) == 1
        assert len(session.exec(select(Notification)).all()) == 1


def test_comments_immutable_history_authority_and_parent_channel_fence(feed):
    client, _, tokens = feed
    write(feed)
    payload = {'id': 'comment', 'fields': {'body': 'Original comment'}}
    assert client.post(BASE + '/post/comments', headers=headers(tokens, 'member'), json=payload).status_code == 201
    payload.update(expected_revision=1, fields={'body': 'Edited comment'})
    assert client.post(BASE + '/post/comments', headers=headers(tokens, 'approver'), json=payload).status_code == 403
    assert client.post(BASE + '/post/comments', headers=headers(tokens, 'member'), json=payload).status_code == 201
    rows = client.get(BASE + '/post/comments/comment/revisions', headers=headers(tokens)).json()
    assert [row['fields']['body'] for row in rows] == ['Edited comment', 'Original comment']
    assert client.post('/channels/channel-b/activity-feed/post/comments', headers=headers(tokens, 'outsider'), json=payload).status_code == 404


def test_poll_votes_bind_actor_validate_options_and_freeze_question_and_labels(feed):
    client, engine, tokens = feed
    assert write(feed, content=fields(poll=True)).status_code == 201
    assert choice(feed).status_code == 200
    assert choice(feed).json()['revision'] == 1
    assert choice(feed, actor='approver', body={'option_ids': ['c' * 32, 'd' * 32]}).status_code == 422
    assert choice(feed, actor='approver', body={'option_ids': ['e' * 32]}).status_code == 422
    payload = {'expected_revision': 0, 'poll_id': 'a' * 32, 'fields': {'option_ids': ['c' * 32]}, 'user_id': 'owner'}
    assert client.post(BASE + '/post/vote', headers=headers(tokens, 'member'), json=payload).status_code == 422
    changed = fields(poll=True); changed['poll']['options'][0]['label'] = 'Changed meaning'
    assert write(feed, revision=1, content=changed).status_code == 409
    changed = fields(poll=True); changed['body'] = 'Text can still change'
    assert write(feed, revision=1, content=changed).status_code == 201
    assert choice(feed, revision=1, body={'option_ids': ['d' * 32]}).json()['revision'] == 2
    assert choice(feed, revision=1, body={'option_ids': []}).status_code == 409
    with Session(engine) as session:
        assert len(session.exec(select(ActivityFeedChoice)).all()) == 1
        assert len(session.exec(select(ActivityFeedChoiceRevision)).all()) == 2
    assert len(client.get(BASE + '/post/choices/vote/revisions', headers=headers(tokens, 'member')).json()) == 2


def test_closed_poll_and_archived_post_never_accept_votes(feed):
    content = fields(poll=True); content['poll']['closes_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert write(feed, content=content).status_code == 201
    assert choice(feed).status_code == 409


def test_empty_initial_vote_cannot_lock_poll_meaning_before_anyone_votes(feed):
    assert write(feed, content=fields(poll=True)).status_code == 201
    assert choice(feed, body={'option_ids': []}).status_code == 422
    changed = fields(poll=True); changed['poll']['options'][0]['label'] = 'Still editable before voting'
    assert write(feed, revision=1, content=changed).status_code == 201


def test_removed_actor_choices_are_not_counted_and_current_scoped_actor_is_allowed(feed):
    client, engine, tokens = feed
    write(feed, content=fields(poll=True))
    assert choice(feed, actor='scoped-approver').status_code == 200
    assert choice(feed, kind='reaction', actor='scoped-approver').status_code == 200
    assert len(client.get(BASE + '/post', headers=headers(tokens)).json()['votes']) == 1
    with Session(engine) as session:
        session.delete(session.get(ChannelMember, ('channel-a', 'approver'))); session.commit()
    record = client.get(BASE + '/post', headers=headers(tokens)).json()
    assert not record['votes'] and not record['reactions']
    assert choice(feed, actor='scoped-approver').status_code == 403


def test_following_is_channel_local_read_revision_is_private_and_monotonic(feed):
    client, engine, tokens = feed
    assert client.put(BASE + '/following/owner', headers=headers(tokens, 'member'), json={'following': True}).status_code == 200
    assert write(feed).status_code == 201
    assert write(feed, identifier='other-post', actor='approver').status_code == 201
    rows = client.get(BASE + '?following=true', headers=headers(tokens, 'member')).json()
    assert [row['id'] for row in rows['items']] == ['post'] and rows['following'] == ['owner']
    assert client.put(BASE + '/following/outsider', headers=headers(tokens, 'member'), json={'following': True}).status_code == 403
    assert client.put(BASE + '/post/read', headers=headers(tokens, 'member'), json={'seen_revision': 2}).status_code == 409
    assert client.put(BASE + '/post/read', headers=headers(tokens, 'member'), json={'seen_revision': 1}).status_code == 200
    assert client.get(BASE + '/post', headers=headers(tokens, 'member')).json()['post']['seen_revision'] == 1
    assert client.get(BASE + '/post', headers=headers(tokens)).json()['post']['seen_revision'] == 0
    with Session(engine) as session:
        notice = session.exec(select(Notification)).one()
        assert notice.user_id == 'member' and notice.body is None and fields()['title'] not in notice.title


def test_cursor_is_stable_with_equal_timestamps_and_cross_channel_anchor_denied(feed):
    client, engine, tokens = feed
    for identifier in ['a', 'b', 'c']: assert write(feed, identifier=identifier).status_code == 201
    with Session(engine) as session:
        for post in session.exec(select(ActivityFeedPost)).all(): post.created_at = datetime(2026, 1, 1); session.add(post)
        session.commit()
    page = client.get(BASE + '?limit=2', headers=headers(tokens)).json()
    assert [row['id'] for row in page['items']] == ['c', 'b'] and page['next_before_id'] == 'b'
    assert [row['id'] for row in client.get(BASE + '?limit=2&before_id=b', headers=headers(tokens)).json()['items']] == ['a']
    assert client.get('/channels/channel-b/activity-feed?before_id=b', headers=headers(tokens, 'outsider')).status_code == 404


def test_post_lock_token_revocation_prevents_persisting_content(feed, monkeypatch):
    client, engine, tokens = feed
    original = service.recheck_actor
    def revoked(session, actor):
        session.add(RevokedToken(token_hash=token_digest(tokens['owner']), expires_at=datetime.utcnow() + timedelta(hours=1)))
        session.flush(); original(session, actor)
    monkeypatch.setattr(service, 'recheck_actor', revoked)
    assert write(feed).status_code == 401
    with Session(engine) as session: assert not session.exec(select(ActivityFeedPost)).all()


def test_concurrent_same_actor_vote_is_one_choice_and_one_revision(feed):
    write(feed, content=fields(poll=True))
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: choice(feed), range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    with Session(feed[1]) as session:
        assert len(session.exec(select(ActivityFeedChoice)).all()) == len(session.exec(select(ActivityFeedChoiceRevision)).all()) == 1


def test_secure_events_never_accept_plaintext_and_recheck_after_remote_validation(feed, monkeypatch):
    client, engine, tokens = feed
    with Session(engine) as session:
        session.add(SecureChannel(channel_id='channel-a', created_by='owner', status='preparing')); session.commit()
    assert write(feed).status_code == 409
    validated = []
    def validate(session, channel, event_id, actor): validated.append((channel, event_id, actor))
    monkeypatch.setattr(service, 'validate_secure_event', validate)
    response = client.post(BASE, headers=headers(tokens), json={'id': 'post', 'matrix_event_id': '$private-post'})
    assert response.status_code == 201 and 'fields' not in response.json()
    assert choice(feed, kind='reaction').status_code == 409
    reaction = client.post(BASE + '/post/reaction', headers=headers(tokens, 'member'), json={'matrix_event_id': '$private-reaction'})
    assert reaction.status_code == 200 and 'fields' not in reaction.json()
    with Session(engine) as session:
        assert all(row.encrypted_payload is None for row in session.exec(select(ActivityFeedRevision)).all())
        assert all(row.encrypted_payload is None for row in session.exec(select(ActivityFeedChoiceRevision)).all())
    def revoke_during_validation(session, *_):
        session.add(RevokedToken(token_hash=token_digest(tokens['owner']), expires_at=datetime.utcnow() + timedelta(hours=1))); session.flush()
    monkeypatch.setattr(service, 'validate_secure_event', revoke_during_validation)
    assert client.post(BASE, headers=headers(tokens), json={'id': 'post', 'expected_revision': 1, 'matrix_event_id': '$new-private-post'}).status_code == 401


@pytest.mark.parametrize('content', [
    {'title': ' ', 'body': ''}, {'title': 'x', 'body': 'x' * 20001},
    {'title': 'x', 'body': '', 'html': '<script>bad</script>'},
])
def test_bounded_safe_post_schema(feed, content):
    assert write(feed, content=content).status_code == 422
