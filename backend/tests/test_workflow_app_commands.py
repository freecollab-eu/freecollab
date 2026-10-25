"""Workflow actions wait for real installed-app delivery and cancel safely."""
from datetime import datetime,timedelta
from sqlmodel import Session, select
from app.api import app_platform as routes
from app.models.app_platform import AppInstallation, AppJob, WorkflowAppReceipt
from app.models.workflow import WorkflowRun
from app.services import app_platform as apps
from app.services import workflow
from tests.test_workflows import flows, headers, create, definition, start, read, process, PASSWORD, BASE


def installed(flows,monkeypatch):
    client,engine,tokens=flows;client.app.include_router(routes.router)
    monkeypatch.setattr(apps.webhooks,'public_addresses',lambda host:['8.8.8.8'])
    manifest=apps.catalog()[0]['manifest']
    result=client.post('/workspaces/a/apps',headers=headers(tokens),json={'manifest':manifest,'channel_ids':['channel-a'],'endpoint':'https://apps.example.com/hook'})
    assert result.status_code==201,result.text
    return result.json()['installation']


def step(installation):
    return {'kind':'app_command','installation_id':installation['id'],'command':'invoke','expected_generation':installation['generation'],
        'arguments':{'text':'Only explicitly configured workflow text'},'disclose_to_app':True}


def test_workflow_queues_exactly_once_waits_for_delivery_then_completes(flows,monkeypatch):
    app=installed(flows,monkeypatch)
    made=create(flows,definition(steps=[step(app)]));assert made.status_code==201,made.text
    run_id=start(flows,made.json());process(flows[1])
    state=read(flows,run_id);assert state['status']=='waiting_external' and state['external_actions'][0]['status']=='pending'
    process(flows[1],now=datetime.utcnow()+timedelta(seconds=6))
    with Session(flows[1]) as session:
        assert len(session.exec(select(AppJob)).all())==1
        assert apps.run_pending(session,send=lambda *args:(204,None))==1
    process(flows[1],now=datetime.utcnow()+timedelta(seconds=12))
    state=read(flows,run_id);assert state['status']=='completed' and state['external_actions'][0]['status']=='delivered'
    with Session(flows[1]) as session:assert len(session.exec(select(WorkflowAppReceipt)).all())==1


def test_stopping_a_workflow_cancels_its_undelivered_outbox_atomically(flows,monkeypatch):
    app=installed(flows,monkeypatch);made=create(flows,definition(steps=[step(app)])).json()
    run_id=start(flows,made);process(flows[1])
    result=flows[0].post(BASE+'/runs/'+run_id+'/cancel',headers=headers(flows[2]),json={'password':PASSWORD})
    assert result.status_code==200,result.text
    with Session(flows[1]) as session:
        job=session.exec(select(AppJob)).one();assert job.status=='cancelled' and not job.encrypted_payload
        calls=[];assert apps.run_pending(session,send=lambda *args:calls.append(args))==0 and not calls


def test_changed_app_generation_and_missing_disclosure_cannot_authorize_future_actions(flows,monkeypatch):
    app=installed(flows,monkeypatch)
    bad=step(app);bad['disclose_to_app']=False
    assert create(flows,definition(steps=[bad])).status_code==422
    bad=step(app);bad['expected_generation']=2
    assert create(flows,definition(steps=[bad])).status_code==409
    made=create(flows,definition(steps=[step(app)])).json();run_id=start(flows,made)
    with Session(flows[1]) as session:
        row=session.get(AppInstallation,app['id']);row.generation+=1;session.add(row);session.commit()
    process(flows[1]);assert read(flows,run_id)['status']=='cancelled'
    with Session(flows[1]) as session:assert session.exec(select(AppJob)).all()==[]


def test_uncertain_provider_write_is_visible_and_never_advances_to_a_false_success(flows,monkeypatch):
    app=installed(flows,monkeypatch)
    made=create(flows,definition(steps=[step(app)])).json();run_id=start(flows,made);process(flows[1])
    with Session(flows[1]) as session:
        job=session.exec(select(AppJob)).one();job.status='uncertain';job.failure='Inspect receiver';session.add(job);session.commit()
    process(flows[1],now=datetime.utcnow()+timedelta(seconds=6))
    state=read(flows,run_id);assert state['status']=='failed' and state['failure']=='app_delivery_uncertain'
    assert state['external_actions'][0]['status']=='uncertain'
