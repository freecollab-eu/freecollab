"""Actual TLS Valkey coordination. Set VALKEY_SERVER_BIN for integration CI."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import time
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import SecretStr
import pytest
import redis

from app.config import settings
from app.realtime.broker import RealtimeBroker
from app.services import shared_runtime
from app.services.rate_limit import RedisAuthRateLimiter
from tests.oidc_mock import tls_fixture


@pytest.fixture
def valkey_tls(tmp_path,monkeypatch):
    binary=os.getenv('VALKEY_SERVER_BIN') or shutil.which('valkey-server')
    if not binary and Path('/tmp/valkey-8.1.3/src/valkey-server').exists():binary='/tmp/valkey-8.1.3/src/valkey-server'
    if not binary:pytest.skip('Set VALKEY_SERVER_BIN to a TLS-enabled Valkey binary for integration proof')
    cert,key=tls_fixture(tmp_path)
    with socket.socket() as binding:binding.bind(('127.0.0.1',0));port=binding.getsockname()[1]
    password=secrets.token_urlsafe(24)
    process=subprocess.Popen([binary,'--bind','127.0.0.1','--port','0','--tls-port',str(port),
        '--tls-cert-file',cert,'--tls-key-file',key,'--tls-ca-cert-file',cert,'--tls-auth-clients','no',
        '--save','','--appendonly','no','--maxmemory','64mb','--maxmemory-policy','noeviction',
        '--requirepass',password,'--dir',str(tmp_path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    original=ssl.get_default_verify_paths().cafile;trust=tmp_path/'trust.pem'
    trust.write_bytes((Path(original).read_bytes() if original else b'')+b'\n'+Path(cert).read_bytes())
    monkeypatch.setenv('SSL_CERT_FILE',str(trust))
    url=f'rediss://:{password}@localhost:{port}/0'
    monkeypatch.setattr(settings,'redis_url',SecretStr(url))
    monkeypatch.setattr(settings,'redis_namespace','proof-'+secrets.token_hex(6))
    monkeypatch.setattr(settings,'redis_tls_required',True)
    monkeypatch.setattr(settings,'redis_presence_lease_seconds',30)
    active=shared_runtime.SharedRuntime()
    try:
        for _ in range(100):
            assert process.poll() is None,'Private TLS Valkey exited before readiness'
            try:active.validate();break
            except HTTPException:time.sleep(.05)
        else:raise AssertionError('Private TLS Valkey was unavailable')
        yield SimpleNamespace(shared=active,process=process,url=url,ca=str(trust))
    finally:
        active.close()
        if process.poll() is None:
            process.terminate()
            try:process.wait(5)
            except subprocess.TimeoutExpired:process.kill();process.wait(5)


def test_real_tls_server_shared_atomic_rate_windows_and_account_failures(valkey_tls):
    a=RedisAuthRateLimiter(shared=valkey_tls.shared,login_limit=2,account_failure_limit=2,address_failure_limit=2)
    b=RedisAuthRateLimiter(shared=shared_runtime.SharedRuntime(),login_limit=2,account_failure_limit=2,address_failure_limit=2)
    a.request('login','first');b.request('login','first')
    with pytest.raises(HTTPException) as denied:b.request('login','first')
    assert denied.value.status_code==429 and int(denied.value.headers['Retry-After'])>0
    a.failed_login('second',' MEMBER@example.com ');b.failed_login('second','member@example.COM')
    with pytest.raises(HTTPException) as denied:a.check_login('third','member@example.com')
    assert denied.value.status_code==429
    b.successful_login('member@example.com');a.check_login('third','member@example.com')
    with pytest.raises(HTTPException):b.check_login('second','other@example.com')
    for key in valkey_tls.shared.client.scan_iter(valkey_tls.shared.namespace+':*'):
        assert 'MEMBER' not in key and 'first' not in key and 'example' not in key
        assert valkey_tls.shared.client.ttl(key)>0


def test_atomic_concurrent_replicas_accept_exactly_shared_limit(valkey_tls):
    limit=RedisAuthRateLimiter(shared=valkey_tls.shared,login_limit=5)
    def request(_):
        try:limit.request('login','same-shared-address');return 1
        except HTTPException as failure:assert failure.status_code==429;return 0
    with ThreadPoolExecutor(max_workers=12) as pool:assert sum(pool.map(request,range(30)))==5


def test_live_throttles_are_not_evicted_when_shared_capacity_is_full(valkey_tls):
    limit=RedisAuthRateLimiter(shared=valkey_tls.shared,max_keys=2,registration_limit=1)
    limit.request('register','first');limit.request('register','second')
    with pytest.raises(HTTPException) as capacity:limit.request('register','third')
    assert capacity.value.status_code==503
    with pytest.raises(HTTPException) as existing:limit.request('register','first')
    assert existing.value.status_code==429
    assert valkey_tls.shared.client.zcard(valkey_tls.shared.namespace+':auth-index')==2


def test_eviction_policy_and_broker_outage_fail_closed_without_local_fallback(valkey_tls):
    valkey_tls.shared.client.config_set('maxmemory-policy','allkeys-lru')
    with pytest.raises(HTTPException) as unsafe:shared_runtime.SharedRuntime().validate()
    assert unsafe.value.status_code==503
    valkey_tls.shared.client.config_set('maxmemory-policy','noeviction')
    limiter=RedisAuthRateLimiter(shared=valkey_tls.shared)
    valkey_tls.process.terminate();valkey_tls.process.wait(5)
    with pytest.raises(HTTPException) as down:limiter.request('login','first')
    assert down.value.status_code==503 and 'localhost' not in str(down.value.detail)


def test_verified_certificate_is_required_and_url_cannot_disable_tls(valkey_tls,monkeypatch):
    # An untrusted CA fails actual TLS verification.
    wrong=redis.Redis.from_url(valkey_tls.url,ssl_ca_certs='/dev/null',socket_connect_timeout=1,socket_timeout=1)
    with pytest.raises(redis.RedisError):wrong.ping()
    monkeypatch.setattr(settings,'redis_url',SecretStr('rediss://localhost:6379/0?ssl_cert_reqs=none'))
    with pytest.raises(ValueError):shared_runtime.options()
    monkeypatch.setattr(settings,'redis_url',SecretStr('redis://private.example:6379/0'))
    with pytest.raises(ValueError):shared_runtime.options()


def test_real_broker_fanout_and_repeated_worker_event_loops(valkey_tls):
    async def proof():
        received=[]
        left,right=RealtimeBroker(),RealtimeBroker()
        async def deliver(envelope):received.append(envelope)
        await left.start(deliver);await right.start(deliver)
        try:
            await left.publish('channel','room','typing',{'channel_id':'room','user_id':'member'})
            for _ in range(100):
                if received:break
                await asyncio.sleep(.02)
            assert len(received)==1 and received[0]['event']=='typing'
            publisher=RealtimeBroker()
            def worker():
                for index in range(2):asyncio.run(publisher.publish('user','member','notification',{'id':str(index)}))
            await asyncio.to_thread(worker)
            for _ in range(100):
                if len(received)>=5:break
                await asyncio.sleep(.02)
            assert len(received)==5
            assert all('token' not in json.dumps(item) and 'Bearer' not in json.dumps(item) for item in received)
            assert left.ready and right.ready
        finally:await left.stop();await right.stop()
    asyncio.run(proof())


def test_shared_presence_leases_keep_other_devices_and_recheck_authority(valkey_tls):
    async def proof():
        left,right=RealtimeBroker(),RealtimeBroker()
        async def deliver(_):pass
        await left.start(deliver);await right.start(deliver)
        try:
            first={'user_id':'member','status':'online','last_seen':'2026-10-21T10:00:00','changed_at':1,'session_hash':'one','claims':{'sub':'member','exp':9999999999}}
            second={**first,'session_hash':'two','changed_at':2}
            await left.write_presence('device-a',first);await right.write_presence('device-b',second)
            assert valkey_tls.shared.presence('member',lambda _:True)['status']=='online'
            await left.remove_presence('device-a','member')
            assert valkey_tls.shared.presence('member',lambda _:True)['status']=='online'
            assert valkey_tls.shared.presence('member',lambda row:row['session_hash']!='two')['status']=='offline'
            await right.remove_presence('device-b','member')
            assert valkey_tls.shared.presence('member',lambda _:True)['status']=='offline'
        finally:await left.stop();await right.stop()
    asyncio.run(proof())
