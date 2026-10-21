"""Private browser-proof launcher; not a production application."""
import json
import os
from pathlib import Path
from fastapi import FastAPI
from sqlmodel import Session
from app.api import auth, external_meetings, integrations_content, preferences
from app.database import engine, init_db
from app.models.user import User
from app.models.channel import Channel, ChannelMember
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceChannel
from app.services.auth import create_token, hash_password
from tests.external_meeting_fixture import ProviderFixture
import uvicorn

def run():
    private = Path(os.environ['PROOF_DIRECTORY'])
    fixture = ProviderFixture(private / 'provider').start()
    context = fixture.transport(); context.__enter__()
    init_db()
    with Session(engine) as session:
        session.add(User(id='owner', name='Private fixture owner', email='fixture-owner@example.invalid', password_hash=hash_password('private-ui-password')))
        session.add(Workspace(id='team', name='Fixture team'))
        session.add(Channel(id='room', name='Fixture channel')); session.commit()
        session.add_all([WorkspaceMember(workspace_id='team', user_id='owner', role='owner'), WorkspaceChannel(channel_id='room', workspace_id='team'), ChannelMember(channel_id='room', user_id='owner', role='admin')]); session.commit()
        token = create_token('owner', session)
        fd = os.open(private / 'access.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as stream: json.dump({'token': token}, stream)
    app = FastAPI()
    for router in [auth.router, external_meetings.router, external_meetings.oauth_router, integrations_content.router, preferences.router]:
        app.include_router(router)
    @app.get('/ready')
    def ready(): return {'ready': True}
    @app.post('/fixture/issue-code')
    def issue_code(body: dict): return {'code': fixture.issue_code(body['provider'], body['challenge'], body['client_id'])}
    @app.post('/fixture/uncertain-zoom')
    def uncertain(): fixture.uncertain_once.add('zoom'); return {'ready': True}
    @app.get('/fixture/report')
    def report(): return {'posts': fixture.posts, 'deletes': fixture.deletes, 'refreshes': fixture.refreshes, 'requests': len(fixture.requests)}
    try:
        uvicorn.run(app, host='127.0.0.1', port=int(os.environ['PROOF_API_PORT']), proxy_headers=False, access_log=False)
    finally:
        context.__exit__(None, None, None); fixture.close()

if __name__ == '__main__': run()
