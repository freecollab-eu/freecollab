"""Real signed Events API, native encrypted storage and durable Slack outbox."""
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import time
import pytest
from sqlmodel import Session, select

from app.api import slack_livebridge as routes
from app.models.channel import ChannelMember
from app.models.message import Message
from app.models.collaboration import MessageChange
from app.models.slack_livebridge import SlackBridge, SlackBridgeJob, SlackBridgeMessage
from app.models.user import User
from app.services import slack_livebridge as service
from app.services.auth import hash_password
from app.services.crypto import decrypt_message
from tests.test_webhooks import team

PASSWORD = "Synthetic-bridge-password!2026"
BASE = "/workspaces/team/slack-bridges"


@pytest.fixture
def bridge_team(team):
    team[0].app.include_router(routes.router)
    with Session(team[1]) as session:
        owner = session.get(User, "owner"); owner.password_hash = hash_password(PASSWORD); session.add(owner); session.commit()
    return team


def create(team, **changes):
    data = {"channel_id": "room", "slack_team_id": "T1234", "slack_channel_id": "C1234", "slack_bot_user_id": "U1234",
        "bot_token": "xoxb-synthetic-customer-token-12345", "signing_secret": "synthetic-slack-signing-secret",
        "inbound": True, "outbound": True, "enabled": True, "disclose_future_messages": True, "password": PASSWORD}
    data.update(changes)
    return team[0].post(BASE, headers=team[2]["owner"], json=data)


def event(team, bridge, *, event_id="Ev1234", stamp=None, signing="synthetic-slack-signing-secret", team_id="T1234", type="event_callback", **changes):
    data = {"type": type, "team_id": team_id, "event_id": event_id,
        "event": {"type": "message", "channel": "C1234", "user": "UREMOTE", "text": "A real inbound message", "ts": "1700000000.000001", "event_ts": "1700000000.000001", **changes}}
    if type == "url_verification": data["challenge"] = "synthetic-challenge"
    body = service.app_platform.dump(data).encode(); stamp = str(stamp or int(time.time()))
    signature = "v0=" + hmac.new(signing.encode(), b"v0:" + stamp.encode() + b":" + body, hashlib.sha256).hexdigest()
    return team[0].post("/slack/bridges/" + bridge["id"] + "/events", content=body,
        headers={"X-Slack-Request-Timestamp": stamp, "X-Slack-Signature": signature})


def run(team, send=None):
    with Session(team[1]) as session: return service.run_pending(session, send=send)


def test_mapping_is_explicit_native_admin_reauthenticated_and_keys_remain_encrypted(bridge_team):
    team = bridge_team
    assert create(team, disclose_future_messages=False).status_code == 422
    assert create(team, password="wrong").status_code in (400, 401, 403)
    assert create(team, channel_id="secure").status_code == 409
    made = create(team); assert made.status_code == 201, made.text
    bridge = made.json()["bridge"]
    listing = team[0].get("/workspaces/team/channels/room/slack-bridges", headers=team[2]["member"])
    assert listing.status_code == 200 and listing.json()["can_manage"] is False
    assert "xoxb-" not in listing.text and "signing_secret" not in listing.text and "encrypted_config" not in listing.text
    with Session(team[1]) as session:
        row = session.get(SlackBridge, bridge["id"])
        assert "xoxb-" not in row.encrypted_config and row.encrypted_config.startswith("fcas1:")
    assert create(team).status_code == 409


def test_signature_replay_bounds_workspace_mapping_and_actual_native_delivery(bridge_team):
    team = bridge_team; bridge = create(team).json()["bridge"]
    assert event(team, bridge, type="url_verification").json() == {"challenge": "synthetic-challenge"}
    assert event(team, bridge, signing="wrong-signing-secret").status_code == 401
    assert event(team, bridge, stamp=int(time.time()) - 301).status_code == 401
    assert event(team, bridge, team_id="TOTHER").status_code == 403
    accepted = event(team, bridge); assert accepted.status_code == 200, accepted.text
    assert event(team, bridge).json()["duplicate"] is True
    assert event(team, bridge, text="Changed content under same event ID").status_code == 409
    assert run(team) == 1
    with Session(team[1]) as session:
        link = session.get(SlackBridgeMessage, (bridge["id"], "1700000000.000001"))
        message = session.get(Message, link.message_id)
        assert message.author_id == "owner" and message.encrypted
        assert "A real inbound message" not in message.content
        assert decrypt_message(message.content, "room") == "Slack · UREMOTE\nA real inbound message"
        assert session.exec(select(MessageChange).where(MessageChange.message_id == message.id)).one().kind == "created"
    called = []
    assert run(team, send=lambda *args: called.append(args)) == 0
    assert called == []  # The imported message cannot echo back into Slack.


def test_thread_mapping_edits_deletes_and_out_of_order_tombstones(bridge_team):
    team = bridge_team; bridge = create(team).json()["bridge"]
    event(team, bridge); run(team)
    assert event(team, bridge, event_id="EvReply", ts="1700000000.000002", event_ts="1700000000.000002", thread_ts="1700000000.000001").status_code == 200
    run(team)
    with Session(team[1]) as session:
        parent = session.get(SlackBridgeMessage, (bridge["id"], "1700000000.000001"))
        child = session.get(SlackBridgeMessage, (bridge["id"], "1700000000.000002"))
        assert session.get(Message, child.message_id).parent_id == parent.message_id
        assert session.get(Message, parent.message_id).thread_count == 1
    assert event(team, bridge, event_id="EvEdit", subtype="message_changed", event_ts="1700000001.000001",
        message={"ts":"1700000000.000001", "user":"UREMOTE", "text":"Edited in Slack"}).status_code == 200
    run(team)
    assert event(team, bridge, event_id="EvDelete", subtype="message_deleted", deleted_ts="1700000000.000001", event_ts="1700000002.000001").status_code == 200
    run(team)
    assert event(team, bridge, event_id="EvOld", event_ts="1700000000.000001").status_code == 200
    run(team)
    with Session(team[1]) as session:
        parent = session.get(SlackBridgeMessage, (bridge["id"], "1700000000.000001"))
        assert session.get(Message, parent.message_id).deleted is True
    assert event(team, bridge, event_id="EvUnseenDelete", subtype="message_deleted", deleted_ts="1700000003.000001", event_ts="1700000004.000001").status_code == 200
    run(team)
    event(team, bridge, event_id="EvLateCreate", ts="1700000003.000001", event_ts="1700000003.000001")
    run(team)
    with Session(team[1]) as session:
        link = session.get(SlackBridgeMessage, (bridge["id"], "1700000003.000001"))
        assert session.get(Message, link.message_id).deleted is True


def test_future_only_outbound_native_send_update_delete_and_stable_client_identifier(bridge_team, monkeypatch):
    from app.api import messages
    async def no_network(*_args, **_kwargs): pass
    monkeypatch.setattr(messages, "broadcast_to_channel", no_network); monkeypatch.setattr(messages, "process_message_mentions", no_network)
    team = bridge_team
    old = team[0].post("/messages/channels/room", headers=team[2]["member"], json={"content":"Not historical backfill"}); assert old.status_code == 200
    bridge = create(team).json()["bridge"]
    sent = team[0].post("/messages/channels/room", headers=team[2]["member"], json={"content":"Future shared message"}); assert sent.status_code == 200, sent.text
    calls = []
    def sender(method, payload, token):
        calls.append((method, payload)); assert token.startswith("xoxb-")
        return {"ok": True, "channel":"C1234", "ts":"1700000050.000001"}
    assert run(team, sender) == 1
    assert calls[0][0] == "chat.postMessage" and calls[0][1]["text"] == "member: Future shared message"
    assert calls[0][1]["unfurl_links"] is False and len(calls[0][1]["client_msg_id"]) == 36
    assert "Not historical" not in repr(calls)
    with Session(team[1]) as session:
        message = session.get(Message, sent.json()["id"]); message.content = service.encrypt_message("Updated native content", "room"); session.add(message)
        service.record_message_change(session, message, "edited"); session.commit()
    assert run(team, sender) == 1 and calls[-1][0] == "chat.update"
    with Session(team[1]) as session:
        message = session.get(Message, sent.json()["id"]); message.deleted = True; session.add(message)
        service.record_message_change(session, message, "deleted"); session.commit()
    assert run(team, sender) == 1 and calls[-1][0] == "chat.delete"
    assert run(team, sender) == 0 and len(calls) == 3


def test_uncertain_posts_never_retry_automatically_and_pause_cancels_pending_work(bridge_team):
    team = bridge_team; bridge = create(team).json()["bridge"]
    with Session(team[1]) as session:
        message = Message(id="native-uncertain", channel_id="room", author_id="member", content="Reviewed future post", encrypted=False)
        session.add(message); session.flush(); service.record_message_change(session, message, "created"); session.commit()
    attempts = []
    def fail(*args): attempts.append(args); raise TimeoutError()
    assert run(team, fail) == 0 and len(attempts) == 1
    assert run(team, fail) == 0 and len(attempts) == 1
    jobs = team[0].get(BASE+'/'+bridge['id']+'/jobs', headers=team[2]['owner']).json()['jobs']
    assert jobs[0]['status'] == 'uncertain'
    endpoint=BASE+'/'+bridge['id']+'/jobs/'+jobs[0]['id']+'/retry'
    assert team[0].post(endpoint, headers=team[2]['owner'], json={'confirm_slack_has_not_processed':False}).status_code == 409
    assert team[0].post(endpoint, headers=team[2]['owner'], json={'confirm_slack_has_not_processed':True}).status_code == 200
    paused = team[0].post(BASE+'/'+bridge['id']+'/pause', headers=team[2]['owner']); assert paused.status_code == 200
    assert run(team, fail) == 0 and len(attempts) == 1
    assert event(team, bridge).status_code == 401


def test_removed_bridge_administrator_cannot_receive_or_disclose_later_messages(bridge_team):
    team = bridge_team; bridge = create(team).json()["bridge"]
    event(team, bridge)
    with Session(team[1]) as session:
        member=session.get(ChannelMember, ('room','owner'));member.role='member';session.add(member);session.commit()
    assert event(team, bridge, event_id='EvNew').status_code == 401
    calls=[];assert run(team, lambda *args:calls.append(args)) == 0 and not calls
    with Session(team[1]) as session:
        assert session.exec(select(Message).where(Message.channel_id=='room')).all() == []
        assert session.exec(select(SlackBridgeJob)).one().status == 'cancelled'


def test_callback_bounds_and_bot_echo_are_rejected_without_delivery(bridge_team):
    team = bridge_team; bridge = create(team).json()["bridge"]
    assert event(team, bridge, subtype='bot_message', bot_id='B1234').json()['ignored']
    assert event(team, bridge, user='U1234').json()['ignored']
    assert event(team, bridge, text='x'*20001).status_code == 422
    response=team[0].post('/slack/bridges/'+bridge['id']+'/events',content=b'x'*65537)
    assert response.status_code == 413
    with Session(team[1]) as session: assert session.exec(select(SlackBridgeJob)).all() == []


def test_multiple_mappings_never_relay_an_imported_slack_message_into_a_loop(bridge_team):
    team=bridge_team;bridge=create(team).json()['bridge']
    second=create(team,slack_team_id='T5678',slack_channel_id='C5678',slack_bot_user_id='U5678')
    assert second.status_code==201,second.text
    event(team,bridge);assert run(team)==1
    called=[];assert run(team,lambda *args:called.append(args))==0 and not called


def test_revoke_can_be_reconnected_once_with_fresh_credentials_and_generation(bridge_team):
    team=bridge_team;bridge=create(team).json()['bridge']
    assert team[0].delete(BASE+'/'+bridge['id'],headers=team[2]['owner']).status_code==200
    restored=create(team);assert restored.status_code==201,restored.text
    assert restored.json()['bridge']['id']==bridge['id'] and restored.json()['bridge']['generation']>bridge['generation']
    assert event(team,restored.json()['bridge']).status_code==200


def test_private_account_cleanup_joins_transaction_and_retention_exports_no_keys_or_text(bridge_team):
    team=bridge_team;bridge=create(team).json()['bridge'];event(team,bridge);run(team)
    with Session(team[1]) as session:
        exported=service.export_bridge_metadata(session,'team',['room'],'member')
        assert service.export_bridge_metadata(session,'team',['secure'],'owner')==[]
        assert len(exported)==1 and 'encrypted' not in repr(exported) and 'xoxb-' not in repr(exported)
        assert 'A real inbound message' not in repr(exported)
        with pytest.raises(Exception) as failure:service.retention_dependencies(session,['room'],datetime.utcnow(),[])
        assert failure.value.status_code==409
        service.close_account_resources(session,'owner')
        assert session.get(SlackBridge,bridge['id']).encrypted_config==''
        session.rollback()  # Helper must not commit the account-erasure transaction.
    with Session(team[1]) as session:assert session.get(SlackBridge,bridge['id']).enabled
    team[0].post(BASE+'/'+bridge['id']+'/pause',headers=team[2]['owner'])
    with Session(team[1]) as session:
        link=session.exec(select(SlackBridgeMessage)).one()
        message_id=link.message_id
        dependencies=service.retention_dependencies(session,['room'],datetime.utcnow()+timedelta(days=1),[link.message_id])
        assert dependencies['slackbridgemessage']['rows'][0].message_id==link.message_id
        assert len(dependencies['slackbridgejob']['rows'])==1
        service.close_account_resources(session,'owner');session.commit()
    with Session(team[1]) as session:
        assert session.get(SlackBridge,bridge['id']).revoked_at
        assert not session.exec(select(SlackBridgeJob)).one().encrypted_payload
        assert session.get(Message,message_id) is not None  # Shared authored content is not silently deleted.


def test_pause_purge_resume_signed_replay_and_new_edit_cannot_resurrect_erased_message(bridge_team):
    team=bridge_team;bridge=create(team).json()['bridge'];assert event(team,bridge).status_code==200;run(team)
    assert team[0].post(BASE+'/'+bridge['id']+'/pause',headers=team[2]['owner']).status_code==200
    with Session(team[1]) as session:
        link=session.exec(select(SlackBridgeMessage)).one();message_id=link.message_id
        assert service.tombstone_erased_messages(session,[message_id])==1
        for change in session.exec(select(MessageChange).where(MessageChange.message_id==message_id)).all():session.delete(change)
        session.delete(session.get(Message,message_id));session.commit()
    paused=team[0].get('/workspaces/team/channels/room/slack-bridges',headers=team[2]['owner']).json()['bridges'][0]
    resumed=team[0].put(BASE+'/'+bridge['id'],headers=team[2]['owner'],json={'channel_id':'room','slack_team_id':'T1234',
        'slack_channel_id':'C1234','slack_bot_user_id':'U1234','inbound':True,'outbound':True,'enabled':True,
        'disclose_future_messages':True,'password':PASSWORD,'expected_generation':paused['generation']})
    assert resumed.status_code==200,resumed.text
    current=resumed.json()['bridge'];assert current['generation']>bridge['generation']
    assert event(team,current).json().get('ignored') is True
    assert event(team,current,event_id='EvFreshAfterErase',subtype='message_changed',message={
        'user':'UREMOTE','text':'ERASED CONTENT MUST NOT RETURN','ts':'1700000000.000001'},event_ts='1700000010.000001').json().get('ignored') is True
    assert run(team)==0
    with Session(team[1]) as session:
        link=session.exec(select(SlackBridgeMessage)).one()
        assert link.message_id is None and link.erased_at is not None
        assert not session.exec(select(Message).where(Message.channel_id=='room')).all()
        assert all(not job.encrypted_payload for job in session.exec(select(SlackBridgeJob)).all())
