"""Disposable media proof API; never point this fixture at a retained database."""
import json
import os
import importlib
import pkgutil
from fastapi import FastAPI
from sqlmodel import Session
import uvicorn
from app.api import auth, channels, clips, files, meetings, secure_messaging, sfu
from app.database import engine, init_db
from app.models.user import User
from app.models.channel import Channel, ChannelMember
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceChannel
from app.services.auth import create_token
import app.models

for module in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module('app.models.' + module.name)

init_db()
with Session(engine) as session:
    names = ['host', 'peer', 'p3', 'p4', 'p5', 'p6', 'p7']
    session.add_all([User(id=name, name='Studio ' + name, email=name + '@private.example', password_hash='unused') for name in names])
    session.add(Workspace(id='team', name='Media studio')); session.add(Channel(id='shared', name='Media')); session.commit()
    session.add(WorkspaceChannel(channel_id='shared', workspace_id='team'))
    session.add_all([WorkspaceMember(workspace_id='team', user_id=name, role='owner' if name == 'host' else 'member') for name in names])
    session.add_all([ChannelMember(channel_id='shared', user_id=name, role='admin' if name == 'host' else 'member') for name in names]); session.commit()
    tokens = {name: create_token(name, session) for name in names}
    descriptor = os.open(os.environ['PROOF_CREDENTIAL_FILE'], os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w') as stream: json.dump(tokens, stream)
app = FastAPI()
for module in [auth, channels, clips, files, meetings, secure_messaging, sfu]: app.include_router(module.router)
@app.get('/ready')
def ready(): return {'ready': True}
uvicorn.run(app, host='127.0.0.1', port=int(os.environ['PROOF_API_PORT']), proxy_headers=False, access_log=False)
