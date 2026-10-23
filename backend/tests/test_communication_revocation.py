"""Native chat mutations recheck the original actor after a writer lock."""
from datetime import datetime

import pytest
from sqlmodel import Session, select

from app.api import messages, reactions, threads
from app.models.account_security import AccountSecurity
from app.models.message import Message
from app.models.reaction import Reaction
from app.models.session import ActiveSession
from app.models.user import User
from app.services.auth import token_digest
from .test_message_security import chat


@pytest.mark.parametrize('operation', ['send', 'edit', 'delete', 'reply', 'react', 'unreact'])
@pytest.mark.parametrize('change', ['logout', 'blocked', 'security_version'])
def test_native_mutations_recheck_actor_after_lock_before_any_write(chat, monkeypatch, operation, change):
    client, engine, users, headers, channel = chat
    original = client.post('/messages/channels/' + channel, headers=headers[0], json={'content': 'Original message'}).json()
    identifier, actor = original['id'], users[0]['user_id']
    if operation == 'unreact':
        assert client.post('/reactions/messages/' + identifier, headers=headers[0], json={'emoji': '👍'}).status_code == 200
    module = messages if operation in {'send', 'edit', 'delete'} else threads if operation == 'reply' else reactions
    normal_lock = module.lock_channel_writes
    def changed_after_authorization(session, channel_id):
        normal_lock(session, channel_id)
        # Deterministically place the authority change after the initial auth
        # dependency and at the lock boundary, before the mutation can continue.
        if change == 'logout':
            row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(users[0]['access_token']))).one()
            row.revoked_at = datetime.utcnow()
        elif change == 'blocked':
            row = session.get(User, actor); row.is_blocked = True
        else:
            row = session.get(AccountSecurity, actor); row.auth_version += 1
        session.add(row); session.flush()
    monkeypatch.setattr(module, 'lock_channel_writes', changed_after_authorization)
    if operation == 'send': response = client.post('/messages/channels/' + channel, headers=headers[0], json={'content': 'Must not send'})
    elif operation == 'edit': response = client.put('/messages/' + identifier, headers=headers[0], json={'content': 'Must not edit'})
    elif operation == 'delete': response = client.delete('/messages/' + identifier, headers=headers[0])
    elif operation == 'reply': response = client.post('/threads/messages/' + identifier + '/reply', headers=headers[0], json={'content': 'Must not reply'})
    elif operation == 'react': response = client.post('/reactions/messages/' + identifier, headers=headers[0], json={'emoji': '👍'})
    else: response = client.delete('/reactions/messages/' + identifier + '/👍', headers=headers[0])
    assert response.status_code == 401, response.text
    with Session(engine) as session:
        message = session.get(Message, identifier)
        assert not message.deleted and message.edited_at is None and message.thread_count == 0
        assert len(session.exec(select(Message)).all()) == 1
        assert len(session.exec(select(Reaction)).all()) == (1 if operation == 'unreact' else 0)
