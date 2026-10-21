"""Channel presence is limited to active members and authorized callers."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import presence
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.user import User
from app.services.auth import create_token


@pytest.fixture
def presence_context(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for user_id in ['member', 'colleague', 'outsider', 'blocked']:
            session.add(User(id=user_id, name=user_id, email=f'{user_id}@example.com', password_hash='unused', is_blocked=user_id == 'blocked'))
        session.add(Channel(id='private', name='Private'))
        for user_id in ['member', 'colleague', 'blocked']:
            session.add(ChannelMember(channel_id='private', user_id=user_id))
        session.commit()
    app = FastAPI()
    app.include_router(presence.router)
    def sessions():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    monkeypatch.setattr(presence, 'get_online_users', lambda: ['member', 'colleague', 'outsider', 'blocked', 'deleted'])
    with TestClient(app) as client:
        yield client
    engine.dispose()


def headers(user_id):
    return {'Authorization': 'Bearer ' + create_token(user_id)}


def test_channel_presence_requires_authentication(presence_context):
    assert presence_context.get('/presence/channel/private').status_code in [401, 403]


def test_channel_presence_rejects_nonmember(presence_context):
    response = presence_context.get('/presence/channel/private', headers=headers('outsider'))
    assert response.status_code == 403
    assert 'online_users' not in response.json()


def test_channel_presence_filters_active_members(presence_context):
    response = presence_context.get('/presence/channel/private', headers=headers('member'))
    assert response.status_code == 200
    assert response.json() == {'channel_id': 'private', 'online_users': ['member', 'colleague'], 'count': 2}


def test_channel_presence_missing_channel(presence_context):
    assert presence_context.get('/presence/channel/missing', headers=headers('member')).status_code == 404
