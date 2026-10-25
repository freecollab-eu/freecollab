"""Composer catalogue and typed reviewed invocations reuse the real app outbox."""
import pytest
from sqlmodel import Session, select
from app.models.app_platform import AppInstallation, AppJob
from tests.test_webhooks import team
from tests.test_app_platform import platform, install

CATALOG = '/workspaces/team/channels/room/apps/commands'


def typed(platform):
    manifest = {'app_id':'customer.typed','name':'Typed customer app','version':'1.0.0','description':'Real typed schema',
        'scopes':['commands:invoke'],'commands':[{'name':'ship','description':'Queue reviewed release', 'arguments':[
            {'name':'version','label':'Version','max_length':30,'required':True,'type':'text'},
            {'name':'count','label':'Count','max_length':30,'required':True,'type':'number'},
            {'name':'notify','label':'Notify','max_length':5,'required':True,'type':'boolean'},
            {'name':'region','label':'Region','max_length':20,'required':True,'type':'choice','choices':['eu','us']},
        ]}]}
    return install(platform,manifest=manifest)


def invoke(platform,made,**patch):
    data={'request_id':'composer_request_1234','expected_generation':made['installation']['generation'],
        'command':'ship','arguments':{'version':'1.2.3','count':'4','notify':'false','region':'eu'},'confirm':True,'disclose_to_app':True}
    data.update(patch)
    return platform[0].post(CATALOG+'/'+made['installation']['id'],headers=platform[2]['member'],json=data)


def test_catalogue_uses_exact_granted_installation_generation_and_declared_arguments(platform):
    made=typed(platform)
    result=platform[0].get(CATALOG,headers=platform[2]['member']);assert result.status_code==200,result.text
    command=result.json()['commands'][0]
    assert command['key']==made['installation']['id']+':ship' and command['generation']==1
    assert command['origin']=='https://apps.example.com' and command['arguments'][3]['choices']==['eu','us']
    assert 'encrypted' not in result.text and made['signing_secret'] not in result.text
    assert platform[0].get('/workspaces/team/channels/secure/apps/commands',headers=platform[2]['owner']).status_code==409
    assert platform[0].get(CATALOG,headers=platform[2]['outsider']).status_code==403


def test_composer_invocation_requires_generation_disclosure_schema_and_stable_id(platform):
    made=typed(platform)
    assert invoke(platform,made,expected_generation=2).status_code==409
    assert invoke(platform,made,disclose_to_app=False).status_code==422
    for key,value in [('count','Infinity'),('notify','yes'),('region','everywhere')]:
        values={'version':'1.2.3','count':'4','notify':'false','region':'eu',key:value}
        assert invoke(platform,made,arguments=values).status_code==422
    result=invoke(platform,made);assert result.status_code==202,result.text
    assert invoke(platform,made).json()['id']==result.json()['id']
    assert invoke(platform,made,arguments={'version':'different','count':'4','notify':'false','region':'eu'}).status_code==409
    with Session(platform[1]) as session:
        rows=session.exec(select(AppJob)).all();assert len(rows)==1
        assert '1.2.3' not in rows[0].encrypted_payload


def test_revocation_removes_catalogue_and_blocks_old_selected_command(platform):
    made=typed(platform)
    response=platform[0].delete('/workspaces/team/apps/'+made['installation']['id'],headers=platform[2]['owner'])
    assert response.status_code==200
    assert platform[0].get(CATALOG,headers=platform[2]['member']).json()['commands']==[]
    assert invoke(platform,made).status_code in (403,409)
    with Session(platform[1]) as session:assert session.exec(select(AppJob)).all()==[]


def test_existing_invocation_endpoint_also_rechecks_optional_selected_generation(platform):
    made=typed(platform)
    response=platform[0].post('/workspaces/team/apps/'+made['installation']['id']+'/commands',headers=platform[2]['member'],json={
        'request_id':'legacy_review_123','channel_id':'room','command':'ship','arguments':{},'confirm':True,'disclose_to_app':True,'expected_generation':2})
    assert response.status_code==409


def test_legacy_write_without_reviewed_generation_fails_closed_before_queue(platform):
    made=typed(platform)
    response=platform[0].post('/workspaces/team/apps/'+made['installation']['id']+'/commands',headers=platform[2]['member'],json={
        'request_id':'missing_review_123','channel_id':'room','command':'ship','arguments':{'version':'1','count':'1','notify':'true','region':'eu'},'confirm':True,'disclose_to_app':True})
    assert response.status_code==422
    with Session(platform[1]) as session:assert session.exec(select(AppJob)).all()==[]


def test_legacy_app_ai_drafts_cannot_bypass_common_budget_or_erase_unknown_usage(platform,monkeypatch):
    from app.models.ai_runtime import AIBudget,AIUsage
    from app.models.app_platform import AppAIDraft
    from app.services import app_platform as service
    from tests.test_app_platform import generate
    from fastapi import HTTPException
    calls=[]
    with Session(platform[1]) as session:session.add(AIBudget(workspace_id='team',max_tokens=1));session.commit()
    monkeypatch.setattr(service.copilot,'completion',lambda *_:calls.append('called'))
    assert generate(platform).status_code==409 and not calls
    with Session(platform[1]) as session:
        budget=session.get(AIBudget,'team');budget.max_tokens=200000;session.add(budget);session.commit()
    def uncertain(*_):calls.append('sent');raise HTTPException(502,'Synthetic provider timeout')
    monkeypatch.setattr(service.copilot,'completion',uncertain)
    assert generate(platform).status_code==502 and calls==['sent']
    with Session(platform[1]) as session:
        ledger=session.exec(select(AIUsage)).one()
        assert ledger.status=='unknown' and ledger.charged_tokens==ledger.reserved_tokens>0
        assert ledger.execution_id.startswith('app-draft:')
        assert session.exec(select(AppAIDraft)).all()==[]


def test_reviewed_literal_indentation_newlines_and_trailing_spaces_survive_real_outbox(platform):
    import json
    from app.services.account_security import unseal_private
    made=typed(platform);literal='  line one\nline two  \n'
    response=invoke(platform,made,arguments={'version':literal,'count':'4','notify':'false','region':'eu'})
    assert response.status_code==202,response.text
    with Session(platform[1]) as session:
        job=session.exec(select(AppJob)).one()
        payload=json.loads(unseal_private(job.encrypted_payload,'app-job',job.id))
        assert payload['arguments']['version']==literal
    # Whitespace is part of the reviewed identity, not a silently equivalent request.
    assert invoke(platform,made,arguments={'version':literal.strip(),'count':'4','notify':'false','region':'eu'}).status_code==409
