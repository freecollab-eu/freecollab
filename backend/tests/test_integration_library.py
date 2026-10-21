"""Immutable declarative community versions, moderation and exact local grants."""
import copy
import json

import pytest
from sqlmodel import Session, select
from app.api import integration_library as routes
from app.models.app_platform import AppInstallation
from app.models.integration_library import IntegrationRelease, LibraryInstallation
from app.models.user import User
from app.services import integration_library as service
from tests.test_webhooks import team

BASE = '/workspaces/team/apps/library'


@pytest.fixture
def library(team):
    team[0].app.include_router(routes.router)
    with Session(team[1]) as session:
        user = session.get(User, 'admin'); user.role = 'admin'; session.add(user); session.commit()
    return team


def package(version='1.0.0'):
    arguments = {'text': 'A reviewed public fixture'}
    return {'package_schema': 1, 'manifest': {'schema_version': 1, 'app_id': 'community.example', 'name': 'Community example',
            'version': version, 'description': 'A bounded customer-owned signed webhook.',
            'scopes': ['commands:invoke', 'cards:write'], 'commands': [{'name': 'send', 'description': 'Send explicit text',
                'protocol': 'signed-webhook', 'arguments': [{'name': 'text', 'label': 'Text', 'required': True, 'max_length': 2000}]}]},
            'publisher': 'Example maintainers', 'license': 'MIT', 'source_url': 'https://github.com/example/integration',
            'endpoint_example': 'https://apps.example.com/hook', 'setup': 'Run your own endpoint; verify the signing secret.',
            'tests': [{'command': 'send', 'arguments': arguments, 'expected_body': service.fixture_payload('send', arguments)}]}


def submit(library, data=None, actor='owner'):
    response = library[0].post(BASE + '/submissions', headers=library[2][actor], json=data or package())
    assert response.status_code == 201, response.text
    return response.json()


def review(library, row, approve=True, actor='admin', **changes):
    data = {'package_digest': row['package_digest'], 'approve': approve}; data.update(changes)
    return library[0].post('/admin/integration-library/' + row['id'] + '/review', headers=library[2][actor], json=data)


def install(library, row, **changes):
    data = {'package_digest': row['package_digest'], 'endpoint': 'https://apps.example.com/hook', 'authorization': '',
            'channel_ids': ['room'], 'confirm_scopes': True}; data.update(changes)
    return library[0].post(BASE + '/' + row['id'] + '/install', headers=library[2]['owner'], json=data)


def test_public_catalog_contains_only_approved_immutable_versions_and_checks(library):
    client, engine, headers = library
    before = client.get('/apps/library').json()['releases']
    assert len(before) >= 5 and all(row['status'] == 'bundled' for row in before)
    row = submit(library)
    assert row['status'] == 'pending' and len(row['package_digest']) == 64
    assert row['id'] not in {item['id'] for item in client.get('/apps/library').json()['releases']}
    assert install(library, row).status_code == 404
    assert review(library, row, actor='owner').status_code == 403
    assert review(library, row, package_digest='0'*64).status_code == 409
    assert review(library, row).status_code == 200
    public = client.get('/apps/library?q=community').json()['releases']
    assert len(public) == 1 and public[0]['id'] == row['id']
    assert public[0]['checks'] == ['manifest-schema', 'declared-contract-fixtures']
    assert 'publisher_id' not in json.dumps(public) and 'submitted_by' not in json.dumps(public)
    assert submit(library)['id'] == row['id']
    altered = package(); altered['setup'] = 'Changed immutable version'
    assert client.post(BASE + '/submissions', headers=headers['owner'], json=altered).status_code == 409
    next_version = submit(library, package('1.0.1'))
    assert next_version['id'] != row['id'] and next_version['status'] == 'pending'
    assert client.get('/apps/library?q=missing').json()['releases'] == []


def test_install_binds_digest_scopes_current_grants_and_withdrawal(library):
    row = submit(library); assert review(library, row).status_code == 200
    assert install(library, row, package_digest='0'*64).status_code == 409
    assert install(library, row, confirm_scopes=False).status_code == 409
    assert install(library, row, channel_ids=['hidden']).status_code == 403
    assert install(library, row, channel_ids=['secure']).status_code == 409
    response = install(library, row)
    assert response.status_code == 201, response.text
    made = response.json(); assert len(made['signing_secret']) > 30
    with Session(library[1]) as session:
        origin = session.get(LibraryInstallation, made['installation']['id'])
        installed = session.get(AppInstallation, made['installation']['id'])
        assert origin.package_digest == row['package_digest'] and origin.release_id == row['id']
        assert json.loads(installed.channel_ids_json) == ['room'] and installed.granted_by == 'owner'
        assert made['signing_secret'] not in installed.encrypted_secret
    assert review(library, row, approve=False).status_code == 200
    assert install(library, row).status_code == 404
    assert row['id'] not in {item['id'] for item in library[0].get('/apps/library').json()['releases']}
    with Session(library[1]) as session:
        assert session.get(AppInstallation, made['installation']['id']).enabled is True


@pytest.mark.parametrize('mutation', [
    lambda row: row['tests'][0]['expected_body'].update(command='wrong'),
    lambda row: row.update(tests=[]),
    lambda row: row['tests'][0]['arguments'].update(arbitrary='no'),
    lambda row: row['manifest'].update(app_id='freecollab.impersonation'),
    lambda row: row.update(source_url='javascript:alert(1)'),
    lambda row: row.update(endpoint_example='https://secret:token@private.example.com/hook'),
    lambda row: row.update(executable='<script>'),
])
def test_untrusted_package_fixtures_links_reserved_namespace_and_executable_fields_rejected(library, mutation):
    data = package(); mutation(data)
    response = library[0].post(BASE + '/submissions', headers=library[2]['owner'], json=data)
    assert response.status_code == 422, response.text


def test_community_namespace_and_pending_reviews_cannot_be_taken_by_other_publisher(library):
    row = submit(library)
    response = library[0].post(BASE + '/submissions', headers=library[2]['admin'], json=package('2.0.0'))
    assert response.status_code == 403, response.text
    assert library[0].get('/admin/integration-library', headers=library[2]['member']).status_code == 403
    assert library[0].get(BASE + '/submissions', headers=library[2]['member']).json()['releases'] == []
    assert library[0].post(BASE + '/submissions', headers=library[2]['member'], json=package()).status_code == 403


def test_bundled_recipe_installs_through_same_exact_digest_flow(library):
    catalog = library[0].get('/apps/library').json()['releases']
    row = next(item for item in catalog if item['id'] == 'builtin:signed-webhook')
    response = install(library, row)
    assert response.status_code == 201, response.text
    assert response.json()['package_digest'] == row['package_digest']


def test_simultaneous_publisher_versions_are_idempotent_across_workspaces(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from sqlmodel import SQLModel, create_engine
    from app.config import settings
    from app.models.workspace import Workspace, WorkspaceMember
    engine = create_engine('sqlite:///' + str(tmp_path/'community.db'), connect_args={'check_same_thread': False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id='publisher',name='Publisher',email='publisher@example.com',password_hash='unused'))
        session.add_all([Workspace(id='w1',name='One'),Workspace(id='w2',name='Two')]); session.flush()
        session.add_all([WorkspaceMember(workspace_id=wid,user_id='publisher',role='owner') for wid in ['w1','w2']]); session.commit()
    def submit_one(wid):
        with Session(engine) as session:
            result=service.submit(session,wid,'publisher',service.Package.model_validate(package()))
            session.commit(); return result['id']
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids=list(pool.map(submit_one,['w1','w2']))
        assert ids[0]==ids[1]
        with Session(engine) as session:
            assert len(session.exec(select(IntegrationRelease)).all())==1
    finally: engine.dispose()


def test_pending_quota_does_not_prevent_retry_of_an_existing_version(library):
    first=submit(library)
    for index in range(1,10): submit(library,package('1.0.'+str(index)))
    assert submit(library)['id']==first['id']
    response=library[0].post(BASE+'/submissions',headers=library[2]['owner'],json=package('2.0.0'))
    assert response.status_code==429,response.text


def test_publisher_lock_order_matches_account_closure_user_before_workspace(library):
    from sqlalchemy import event
    updates=[]
    def record(connection,cursor,statement,parameters,context,executemany):
        text=statement.upper().replace('"','')
        if text.startswith('UPDATE USER ') or text.startswith('UPDATE WORKSPACE '): updates.append(text.split()[1])
    event.listen(library[1],'before_cursor_execute',record)
    try:
        submit(library)
        assert updates.index('USER')<updates.index('WORKSPACE')
    finally: event.remove(library[1],'before_cursor_execute',record)
