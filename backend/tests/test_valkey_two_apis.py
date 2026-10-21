"""Independent real two-process API/WebSocket proof over TLS Valkey.

No hosted services or application traffic are used. A TLS-enabled Valkey binary
is required (VALKEY_SERVER_BIN); otherwise the integration test is skipped.
"""
import asyncio
import base64
from datetime import datetime
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time

import httpx
import socketio

from tests.test_valkey_runtime import valkey_tls

BACKEND = Path(__file__).resolve().parents[1]
PYTHON = os.getenv("BACKEND_PYTHON") or (str(BACKEND/".venv/bin/python") if (BACKEND/".venv/bin/python").exists() else sys.executable)
PASSWORD = "Private-two-api-proof!2026"


def free_port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def stop(processes):
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(5)


def test_two_actual_api_processes_authorize_every_remote_delivery_and_share_limits(valkey_tls, tmp_path):
    ports = [free_port(), free_port()]
    urls = ["http://127.0.0.1:"+str(port) for port in ports]
    env = os.environ.copy()
    env.update(ENVIRONMENT="test", DATABASE_URL="sqlite:///"+str(tmp_path/"apis.db"), STORAGE_PATH=str(tmp_path/"files"),
        JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
        ACCOUNT_SECURITY_KEY_FILE=str(tmp_path/"account.key"), MESSAGE_ENCRYPTION_KEYS=json.dumps({"proof": base64.b64encode(secrets.token_bytes(32)).decode()}),
        MESSAGE_ENCRYPTION_ACTIVE_KEY_ID="proof", MESSAGE_ENCRYPTION_KEY_FILE=str(tmp_path/"message.keys"), MATRIX_ENABLED="false",
        MAIL_DELIVERY_MODE="disabled", OIDC_ENABLED="false", WEBHOOKS_ENABLED="false", OTRUST_ENABLED="false", SYNC_ENABLED="false",
        COPILOT_ENABLED="false", CLOUD_FILES_ENABLED="false", EXTERNAL_MEETINGS_ENABLED="false", PUSH_ENABLED="false",
        PUBLIC_APP_URL=urls[0], CORS_ORIGINS=json.dumps(urls), REDIS_URL=valkey_tls.url,
        REDIS_NAMESPACE=valkey_tls.shared.namespace, REDIS_TLS_REQUIRED="true", REDIS_PRESENCE_LEASE_SECONDS="30",
        SSL_CERT_FILE=valkey_tls.ca, PYTHONPATH=str(BACKEND))
    env["NO_PROXY"] = ",".join(filter(None, [env.get("NO_PROXY", ""), "127.0.0.1", "localhost", "::1"]))
    launcher = tmp_path/"api.py"
    launcher.write_text('''import sys, uvicorn
from fastapi import Depends
from sqlmodel import select
from app.main import app, socket_app
from app.database import get_session
from app.services.auth import get_current_user, create_token, token_digest
from app.services.workspaces import require_workspace_admin, require_workspace_member
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
@app.post('/__proof/scoped')
def scope(data:dict,user=Depends(get_current_user),session=Depends(get_session)):
    require_workspace_admin(session,data['workspace_id'],user.id)
    require_workspace_member(session,data['workspace_id'],data['user_id'])
    provider=WorkspaceOIDCProvider(workspace_id=data['workspace_id'],name='Private proof provider',issuer_url='https://issuer.fixture.example',client_id='private-fixture',enabled=True,created_by=user.id)
    session.add(provider);session.commit()
    token=create_token(data['user_id'],session,auth_methods=['sso'],workspace_scope={'workspace_id':data['workspace_id'],'provider_revision':1})
    session.add(WorkspaceOIDCSession(token_hash=token_digest(token),workspace_id=data['workspace_id'],provider_revision=1));session.commit()
    return {'access_token':token}
@app.post('/__proof/login-limit')
def limit(data:dict):
    app.state.auth_rate_limiter.login_limit=data['limit']
    return {'configured':True}
uvicorn.run(socket_app,host='127.0.0.1',port=int(sys.argv[1]),proxy_headers=False,access_log=False,log_level='warning')
''')
    processes, streams = [], []
    try:
        for index, port in enumerate(ports):
            stream = (tmp_path/("api-"+str(index)+".log")).open("w"); streams.append(stream)
            process = subprocess.Popen([PYTHON, str(launcher), str(port)], cwd=BACKEND, env=env, stdout=stream, stderr=stream)
            processes.append(process)
            with httpx.Client(trust_env=False, timeout=3) as client:
                until = time.monotonic()+45
                while time.monotonic() < until:
                    assert process.poll() is None, "A private API process exited before readiness"
                    try:
                        if client.get(urls[index]+"/ready").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise AssertionError("A private API process did not become ready")
        checks = asyncio.run(proof(urls, env, tmp_path, valkey_tls))
        report = Path(os.getenv("VALKEY_TWO_API_REPORT", str(BACKEND.parent.parent/".freecollab/proof-results/valkey-two-apis.json")))
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({"ok": True, "checks": checks, "api_processes": 2, "transport": "real_tls_valkey"}, indent=2)+"\n")
    finally:
        stop(processes)
        for stream in streams:
            stream.close()


async def proof(urls, env, private, valkey_tls):
    sockets, events, tokens, checks = [], {}, [], []
    client = httpx.AsyncClient(trust_env=False, timeout=15)
    async def api(replica, method, path, token=None, data=None, expected=200):
        response = await client.request(method, urls[replica]+path, headers={"Authorization": "Bearer "+token} if token else {}, json=data)
        assert response.status_code == expected, "Unexpected private API status for "+method+" "+path+": "+str(response.status_code)
        return response.json() if response.content else None
    async def wait(condition, label, timeout=8):
        until = time.monotonic()+timeout
        while time.monotonic() < until:
            if condition():
                return
            await asyncio.sleep(.05)
        raise AssertionError(label)
    async def connect(name, replica, token, channel):
        sio = socketio.AsyncClient(reconnection=False, request_timeout=5)
        events[name] = []
        for event in ("message", "typing", "notification", "presence", "worker.proof"):
            async def capture(data, event=event):
                events[name].append((event, data))
            sio.on(event, capture)
        await sio.connect(urls[replica], auth={"token": token}, transports=["websocket"], wait_timeout=8)
        assert (await sio.call("join", {"channel_id": channel}, timeout=5)).get("ok"), "Current member could not join on remote replica"
        sockets.append(sio)
        return sio
    def received(name, event, identifier=None, marker=None):
        return [data for kind, data in events[name] if kind == event and (identifier is None or data.get("id") == identifier or data.get("entity_id") == identifier)
                and (marker is None or data.get("content") == marker)]
    async def send(marker, mentions=None):
        return await api(0, "POST", "/messages/channels/"+channel, owner["access_token"], {"content": marker, "mentions": mentions or []})
    pubsub = valkey_tls.shared.client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(valkey_tls.shared.namespace+":events")
    try:
        users = []
        for name in ("Owner", "Member", "Scoped"):
            email = name.lower()+"-"+secrets.token_hex(5)+"@example.com"
            user = await api(0, "POST", "/auth/register", data={"name": name, "email": email, "password": PASSWORD})
            user["email"] = email; users.append(user); tokens.append(user["access_token"])
        owner, member, scoped = users
        workspace = await api(0, "POST", "/workspaces", owner["access_token"], {"name": "Private two API team"}, expected=201)
        workspace_id = workspace["id"]
        for user in (member, scoped):
            invite = await api(0, "POST", "/workspaces/"+workspace_id+"/invites", owner["access_token"], {"role": "member"}, expected=201)
            await api(1, "POST", "/workspaces/invites/accept", user["access_token"], {"token": invite["token"]})
        room = await api(0, "POST", "/channels", owner["access_token"], {"name": "Shared replica proof", "workspace_id": workspace_id, "member_ids": [member["user_id"], scoped["user_id"]]})
        channel = room["id"]
        other_workspace = await api(0, "POST", "/workspaces", owner["access_token"], {"name": "Other scoped team"}, expected=201)
        invite = await api(0, "POST", "/workspaces/"+other_workspace["id"]+"/invites", owner["access_token"], {"role": "member"}, expected=201)
        await api(1, "POST", "/workspaces/invites/accept", scoped["access_token"], {"token": invite["token"]})
        other = await api(0, "POST", "/channels", owner["access_token"], {"name": "Other workspace", "workspace_id": other_workspace["id"], "member_ids": [scoped["user_id"]]})
        scoped_token = (await api(0, "POST", "/__proof/scoped", owner["access_token"], {"workspace_id": workspace_id, "user_id": scoped["user_id"]}))["access_token"]
        tokens.append(scoped_token)
        second = await api(1, "POST", "/auth/login", data={"email": member["email"], "password": PASSWORD})
        tokens.append(second["access_token"])
        a = await connect("owner", 0, owner["access_token"], channel)
        b = await connect("member", 1, member["access_token"], channel)
        second_socket = await connect("second-device", 0, second["access_token"], channel)
        restricted = await connect("scoped", 1, scoped_token, channel)
        first = await send("cross-replica-message", [member["user_id"]])
        await wait(lambda: received("member", "message", first["id"]), "Remote replica did not deliver a real API message")
        await wait(lambda: received("member", "notification", first["id"]), "Remote replica did not deliver the referenced mention notification")
        reply = await api(1, "POST", "/messages/channels/"+channel, member["access_token"], {"content": "reverse-replica-message"})
        await wait(lambda: received("owner", "message", reply["id"]), "Reverse replica message was not delivered")
        assert (await a.call("typing", {"channel_id": channel}, timeout=5)).get("ok")
        await wait(lambda: any(kind == "typing" and data.get("user_id") == owner["user_id"] for kind, data in events["member"]), "Typing did not cross replicas")
        checks.append("actual_api_messages_mentions_and_typing_cross_replicas_both_directions")
        assert (await api(0, "GET", "/presence/users/"+member["user_id"], owner["access_token"]))["status"] == "online"
        assert (await b.call("set_status", {"status": "busy"}, timeout=5)).get("ok")
        await second_socket.disconnect()
        assert (await api(0, "GET", "/presence/users/"+member["user_id"], owner["access_token"]))["status"] == "busy"
        presence = await api(0, "GET", "/presence/channel/"+channel, owner["access_token"])
        assert scoped["user_id"] in presence["online_users"] and owner["user_id"] in presence["online_users"]
        checks.append("shared_presence_explicit_status_and_one_device_disconnect_preserve_other_lease")
        denied = await restricted.call("join", {"channel_id": other["id"]}, timeout=5)
        assert denied.get("error"), "Workspace scoped socket joined another workspace"
        foreign = await api(0, "POST", "/messages/channels/"+other["id"], owner["access_token"], {"content": "must-not-cross-workspace-scope", "mentions": [scoped["user_id"]]})
        await asyncio.sleep(.5)
        assert not received("scoped", "notification", foreign["id"]) and not received("scoped", "message", foreign["id"])
        checks.append("workspace_scoped_private_rooms_reject_other_workspace_notifications")
        worker = private/"worker.py"
        worker.write_text('''import asyncio,os
from app.main import app
from app.realtime.socket import broadcast_to_channel
for index in range(2):
    asyncio.run(broadcast_to_channel(os.environ['PROOF_CHANNEL'],'worker.proof',{'channel_id':os.environ['PROOF_CHANNEL'],'iteration':index}))
''')
        result = await asyncio.to_thread(subprocess.run, [PYTHON, str(worker)], cwd=BACKEND, env={**env, "PROOF_CHANNEL": channel}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        assert result.returncode == 0, "Standalone worker asyncio.run publisher failed"
        await wait(lambda: len(received("member", "worker.proof")) == 2 and len(received("owner", "worker.proof")) == 2, "Standalone repeated worker loops did not fan out to both API processes")
        checks.append("separate_worker_repeated_asyncio_run_publishes_to_both_actual_apis")
        with sqlite3.connect(private/"apis.db") as db:
            db.execute("UPDATE workspaceoidcprovider SET revision=revision+1 WHERE workspace_id=?", (workspace_id,)); db.commit()
        revoked_scope = await send("revoked-provider-must-not-deliver", [scoped["user_id"]])
        await wait(lambda: received("member", "message", revoked_scope["id"]), "Control recipient did not receive provider-revision test event")
        await asyncio.sleep(.5)
        assert not received("scoped", "message", revoked_scope["id"]) and not received("scoped", "notification", revoked_scope["id"])
        assert (await restricted.call("typing", {"channel_id": channel}, timeout=5)).get("error")
        await api(1, "GET", "/auth/me", scoped_token, expected=401)
        checks.append("remote_delivery_rechecks_current_workspace_provider_revision")
        await api(1, "POST", "/auth/logout", member["access_token"])
        logged_out = await send("logged-out-socket-must-not-deliver", [member["user_id"]])
        await wait(lambda: received("owner", "message", logged_out["id"]), "Control recipient did not receive logout test event")
        await asyncio.sleep(.5)
        assert not received("member", "message", logged_out["id"]) and not received("member", "notification", logged_out["id"])
        checks.append("remote_delivery_rechecks_current_native_jwt_session_revocation")
        current_member = await connect("current-member", 1, second["access_token"], channel)
        await api(0, "DELETE", "/channels/"+channel+"/members/"+member["user_id"], owner["access_token"])
        removed = await send("removed-channel-member-must-not-deliver")
        await wait(lambda: received("owner", "message", removed["id"]), "Control recipient did not receive membership test event")
        await asyncio.sleep(.5)
        assert not received("current-member", "message", removed["id"])
        assert (await current_member.call("typing", {"channel_id": channel}, timeout=5)).get("error")
        await api(1, "GET", "/auth/me", second["access_token"])
        checks.append("remote_delivery_rechecks_channel_membership_without_revoking_other_account_access")
        envelopes = []
        for _ in range(100):
            message = await asyncio.to_thread(pubsub.get_message, timeout=.02)
            if message and isinstance(message.get("data"), str):
                envelopes.append(message["data"])
        assert envelopes, "Proof did not observe actual broker envelopes"
        serialized = "\n".join(envelopes)
        assert not any(token in serialized for token in tokens) and "Bearer " not in serialized
        for key in valkey_tls.shared.client.scan_iter(valkey_tls.shared.namespace+":presence-session:*"):
            value = valkey_tls.shared.client.get(key)
            assert value and not any(token in value for token in tokens)
            assert "session_hash" in value and '"claims"' in value
        checks.append("actual_broker_envelopes_and_presence_leases_contain_no_bearer_jwt")
        for replica in range(2):
            await api(replica, "POST", "/__proof/login-limit", data={"limit": 2})
        keys = list(valkey_tls.shared.client.scan_iter(valkey_tls.shared.namespace+":auth*"))
        if keys:
            valkey_tls.shared.client.delete(*keys)
        for replica in (0, 1):
            await api(replica, "POST", "/auth/login", data={"email": "nobody@example.com", "password": "not-a-valid-password"}, expected=401)
        for replica in (0, 1):
            await api(replica, "POST", "/auth/login", data={"email": "nobody@example.com", "password": "not-a-valid-password"}, expected=429)
        checks.append("actual_auth_requests_share_one_atomic_login_window_across_api_processes")
        valkey_tls.process.terminate(); valkey_tls.process.wait(5)
        for replica in (0, 1):
            await api(replica, "POST", "/auth/login", data={"email": "nobody@example.com", "password": "not-a-valid-password"}, expected=503)
            await api(replica, "GET", "/ready", expected=503)
            await api(replica, "GET", "/presence/users/"+owner["user_id"], owner["access_token"], expected=503)
        checks.append("real_broker_outage_fails_closed_for_shared_auth_presence_and_readiness")
        return checks
    finally:
        pubsub.close()
        for sio in reversed(sockets):
            if sio.connected:
                try:
                    await sio.disconnect()
                except Exception:
                    pass
        await client.aclose()
