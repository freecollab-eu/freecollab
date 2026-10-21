"""Personal home aggregates current sources without creating new authority."""
from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from app.api import chat_productivity, integrations_content, my_day, notifications, saved_items
from app.models.channel import ChannelMember
from app.models.chat_productivity import MessageReminder, SavedItemCompletion
from app.models.collaboration import Bookmark
from app.models.integration_content import IntegrationContent, IntegrationContentRevision
from app.models.message import Message
from app.models.notification import Notification
from app.models.project_tasks import ProjectBoard, ProjectTaskRevision, TaskCard
from app.models.secure_channel import SecureChannel
from app.models.workspace import WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider
from app.services.crypto import encrypt_message
from .test_project_tasks import board, headers, project, task, TASK  # noqa: F401


def setup(board):
    for module in (my_day, chat_productivity, integrations_content, notifications, saved_items):
        board.client.app.include_router(module.router)
    project(board); task(board)
    return board.client


def get(board, who="peer"):
    response = board.client.get('/my-day', headers=headers(board, who))
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    return response.json()


def test_current_assignment_and_private_sources_only(board):
    setup(board)
    with Session(board.engine) as session:
        session.add(Message(id='context', channel_id='a', author_id='owner', content=encrypt_message('Do not make an inbox copy', 'a'), encrypted=True))
        session.commit()
        session.add(Bookmark(user_id='peer', channel_id='a', item_id='context', kind='message'))
        session.add(MessageReminder(id='reminder', user_id='peer', channel_id='a', item_id='context', kind='message',
            due_at=datetime.utcnow()+timedelta(hours=1), session_hash='unused', claims_json='{}'))
        session.add(Notification(id='mention', user_id='peer', type='mention', title='Do not copy retained title', body='Retained private body',
            entity_type='message', entity_id='context'))
        session.add(Notification(id='other-user', user_id='owner', type='mention', title='Other user', entity_type='message', entity_id='context'))
        session.commit()
    result = get(board)
    assert [row['id'] for row in result['tasks']] == ['task-a']
    assert [row['id'] for row in result['reminders']] == ['reminder']
    assert [row['id'] for row in result['notifications']] == ['mention']
    assert [row['item_id'] for row in result['saved']] == ['context']
    assert 'Do not copy' not in str(result) and 'Retained private body' not in str(result)
    assert 'Do not make an inbox copy' not in str(result)
    assert get(board, 'owner')['tasks'] == []
    assert get(board, 'owner')['saved'] == []
    assert get(board, 'outsider')['channels'] == []


def test_cross_workspace_sources_and_dangling_seats_are_filtered(board):
    setup(board)
    with Session(board.engine) as session:
        session.add(WorkspaceMember(workspace_id='wb', user_id='peer', role='member'))
        session.add(ChannelMember(channel_id='b', user_id='peer', role='member'))
        session.commit()
    assert {row['workspace_id'] for row in get(board)['channels']} == {'wa', 'wb'}
    with Session(board.engine) as session:
        session.delete(session.get(WorkspaceMember, ('wa', 'peer'))); session.commit()
    result = get(board)
    assert [row['id'] for row in result['channels']] == ['b'] and result['tasks'] == []


def test_workspace_sign_in_never_aggregates_another_workspace(board):
    setup(board)
    with Session(board.engine) as session:
        session.add(WorkspaceMember(workspace_id='wb', user_id='owner', role='member'))
        session.add(ChannelMember(channel_id='b', user_id='owner', role='member')); session.commit()
    assert {row['workspace_id'] for row in get(board, 'owner')['channels']} == {'wa', 'wb'}
    assert {row['workspace_id'] for row in get(board, 'scoped')['channels']} == {'wa'}
    with Session(board.engine) as session:
        provider = session.get(WorkspaceOIDCProvider, 'wa'); provider.revision += 1; session.add(provider); session.commit()
    assert board.client.get('/my-day', headers=headers(board, 'scoped')).status_code == 401


def test_archived_projects_done_tasks_and_deleted_targets_disappear(board):
    setup(board)
    response = board.client.post('/channels/a/projects/project-a/tasks', headers=headers(board, 'peer'),
        json={'id':'task-a','expected_revision':1,'fields':{**TASK,'status':'done'}})
    assert response.status_code == 200, response.text
    assert get(board)['tasks'] == []
    response = board.client.post('/channels/a/projects/project-a/tasks', headers=headers(board, 'peer'),
        json={'id':'task-a','expected_revision':2,'fields':TASK})
    assert response.status_code == 200
    with Session(board.engine) as session:
        project = session.get(ProjectBoard, 'project-a'); project.archived = True; session.add(project)
        session.add(Bookmark(user_id='peer', channel_id='a', item_id='missing', kind='message'))
        session.add(Notification(user_id='peer',type='mention',title='Deleted source',entity_type='message',entity_id='missing'))
        session.commit()
    result = get(board)
    assert result['tasks'] == [] and result['saved'] == [] and result['notifications'] == []


def test_matrix_assignment_is_an_opaque_reference_not_a_server_claim(board):
    setup(board)
    with Session(board.engine) as session:
        # Channel encryption changes make old server payloads unavailable.
        session.add(SecureChannel(channel_id='a',created_by='owner'))
        session.add(TaskCard(id='secure-task', project_id='project-a', channel_id='a', created_by='owner', revision=1))
        session.add(ProjectTaskRevision(kind='task',item_id='secure-task',revision=1,created_by='owner',content_mode='matrix',matrix_event_id='$encrypted-task'))
        session.add(IntegrationContent(id='secure-event',channel_id='a',kind='calendar',created_by='owner'))
        session.flush()
        session.add(IntegrationContentRevision(item_id='secure-event',revision=1,created_by='owner',content_mode='matrix',matrix_event_id='$encrypted-event'))
        session.commit()
    result = get(board)
    assert [row['id'] for row in result['tasks']] == ['secure-task']
    assert result['tasks'][0]['matrix_event_id'] == '$encrypted-task' and 'fields' not in result['tasks'][0]
    assert result['calendar'][0]['matrix_event_id'] == '$encrypted-event' and 'fields' not in result['calendar'][0]
    assert result['incomplete'] is True
    assert 'Private next step' not in str(result)


def test_calendar_keeps_current_revision_and_not_deleted_history(board):
    client = setup(board)
    start = datetime.now(timezone.utc)+timedelta(days=1)
    fields = {'title':'A focused meeting','description':'','location':'','starts_at':start.isoformat(),
        'ends_at':(start+timedelta(hours=1)).isoformat(),'all_day':False,'timezone':'UTC','reminder_minutes':None,'external_uid':None}
    response = client.post('/channels/a/integrations/calendar',headers=headers(board),json={'id':'event','fields':fields})
    assert response.status_code == 200, response.text
    assert get(board)['calendar'][0]['fields']['title'] == 'A focused meeting'
    response = client.post('/channels/a/integrations/calendar',headers=headers(board),json={'id':'event','expected_revision':1,'deleted':True})
    assert response.status_code == 200
    assert get(board)['calendar'] == []


def test_completion_reuses_source_authority_and_removes_follow_up(board):
    client = setup(board)
    with Session(board.engine) as session:
        session.add(Message(id='saved-context',channel_id='a',author_id='owner',content='Context',encrypted=False)); session.commit()
        session.add(Bookmark(user_id='peer',channel_id='a',item_id='saved-context',kind='message')); session.commit()
    assert len(get(board)['saved']) == 1
    response = client.patch('/bookmarks/a/saved-context/completion',headers=headers(board,'peer'),json={'completed':True})
    assert response.status_code == 200
    assert get(board)['saved'] == []
    with Session(board.engine) as session:
        assert session.get(SavedItemCompletion, ('peer','a','saved-context'))
        session.delete(session.get(ChannelMember, ('a','peer'))); session.commit()
    assert client.patch('/bookmarks/a/saved-context/completion',headers=headers(board,'peer'),json={'completed':False}).status_code == 403


def test_blocked_or_unsigned_requests_cannot_open_my_day(board):
    setup(board)
    assert board.client.get('/my-day').status_code in {401,403}
    assert board.client.get('/my-day',headers=headers(board,'blocked')).status_code == 403
