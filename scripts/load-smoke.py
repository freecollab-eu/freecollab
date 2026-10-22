#!/usr/bin/env python3
"""Measure a disposable installation with real REST, files and WebSocket fanout.

Creates dedicated test accounts, a workspace and files. Run only against an
isolated installation. Customer capacity requires representative deployments and
longer trials; this bounded smoke run is not a scale or availability claim.
Use backend/.venv/bin/python; its locked environment supplies httpx/socketio.
"""
import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import random
import secrets
import time
from urllib.parse import urlsplit

import httpx
import socketio


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True, help='API origin or /api base of an isolated installation')
    parser.add_argument('--socket-url', help='WebSocket origin; defaults to API origin')
    parser.add_argument('--alternate-api', help='Second replica using the same SQL/storage/shared broker')
    parser.add_argument('--allow-remote-test-installation', action='store_true')
    parser.add_argument('--users', type=int, default=8, choices=range(2, 17), metavar='2..16')
    parser.add_argument('--sockets-per-user', type=int, default=2, choices=range(1, 5), metavar='1..4')
    parser.add_argument('--concurrency', type=int, default=20, choices=range(1, 101), metavar='1..100')
    parser.add_argument('--requests', type=int, default=400, choices=range(20, 10001), metavar='20..10000')
    parser.add_argument('--messages', type=int, default=40, choices=range(1, 101), metavar='1..100')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    for base in [args.base_url, args.alternate_api, args.socket_url]:
        if not base:
            continue
        parsed = urlsplit(base)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            parser.error('Use an explicit HTTP(S) test origin without URL credentials/query/fragment')
        try:
            local = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            local = parsed.hostname == 'localhost'
        if not local and not args.allow_remote_test_installation:
            parser.error('Remote targets require --allow-remote-test-installation and must be disposable')
    return args


def percentiles(values):
    if not values:
        return {}
    ordered = sorted(values)
    def percentile(fraction):
        return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))] * 1000, 2)
    return {'count':len(values), 'p50_ms':percentile(.50), 'p95_ms':percentile(.95), 'p99_ms':percentile(.99), 'max_ms':round(max(values)*1000, 2)}


async def exercise(args, report):
    bases = [args.base_url.rstrip('/')]
    if args.alternate_api:
        bases.append(args.alternate_api.rstrip('/'))
    parsed = urlsplit(args.base_url)
    socket_base = args.socket_url or parsed.scheme + '://' + parsed.netloc
    sockets, received, latency = [], {}, defaultdict(list)
    statuses = Counter()
    request_semaphore = asyncio.Semaphore(args.concurrency)
    password = secrets.token_urlsafe(30) + '!aA1'
    run = secrets.token_hex(8)
    users = []
    def phase(name):
        report['phase'] = name
        print('Load smoke: ' + name, flush=True)
    async with httpx.AsyncClient(timeout=45, limits=httpx.Limits(max_connections=args.concurrency + 5, max_keepalive_connections=args.concurrency + 5)) as client:
        async def api(method, path, user=None, payload=None, expected=None, replica=0, measured=None, **kwargs):
            headers = {'Authorization':'Bearer ' + user['access_token']} if user else {}
            async with request_semaphore:
                started = time.perf_counter()
                try:
                    response = await client.request(method, bases[replica % len(bases)] + path, headers=headers, json=payload, **kwargs)
                except httpx.HTTPError:
                    report['failed_operation'] = {'method':method, 'kind':measured or 'setup', 'path_type':path.split('/')[1]}
                    raise
                elapsed = time.perf_counter() - started
            if measured:
                latency[measured].append(elapsed)
                statuses[str(response.status_code)] += 1
            ok = response.status_code == expected if expected is not None else 200 <= response.status_code < 300
            if not ok:
                # Provider/HTTP bodies can include private data; retain only status.
                raise AssertionError(f'{method} {path.split("?")[0]} returned {response.status_code}')
            return response
        async def register(index):
            value = await api('POST','/auth/register',payload={'name':'Load member '+str(index),'email':'load-'+run+'-'+str(index)+'@example.com','password':password})
            return value.json()
        async def until(predicate, label, timeout=30):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if predicate():
                    return
                await asyncio.sleep(.05)
            raise AssertionError(label)
        try:
            phase('create isolated test users and workspace')
            for index in range(args.users + 1):
                users.append(await register(index))
            owner, outsider = users[0], users[-1]
            members = users[:-1]
            workspace = (await api('POST','/workspaces',owner,{'name':'Isolated load '+run})).json()
            for user in members[1:]:
                invitation = (await api('POST','/workspaces/'+workspace['id']+'/invites',owner,{'role':'member'})).json()
                await api('POST','/workspaces/invites/accept',user,{'token':invitation['token']})
            channel = (await api('POST','/channels',owner,{'name':'Isolated load '+run,'workspace_id':workspace['id'],'member_ids':[u['user_id'] for u in members[1:]]})).json()['id']
            file_body = secrets.token_bytes(64 * 1024)
            uploaded = (await api('POST','/files/channels/'+channel,owner,files={'file':('load.bin.txt',file_body,'text/plain')})).json()
            phase('connect authorized WebSocket receivers')
            for index, user in enumerate(members):
                for device in range(args.sockets_per_user):
                    name = str(index)+':'+str(device)
                    received[name] = []
                    sio = socketio.AsyncClient(reconnection=False, request_timeout=15)
                    async def capture(data, name=name):
                        if data.get('channel_id') == channel:
                            received[name].append(data['id'])
                    sio.on('message',capture)
                    await sio.connect(socket_base,auth={'token':user['access_token']},transports=['websocket'],wait_timeout=15)
                    if not (await sio.call('join',{'channel_id':channel},timeout=15)).get('ok'):
                        raise AssertionError('Authorized load member could not join')
                    sockets.append(sio)
            report['topology'] = {'api_endpoints':len(bases),'users':args.users,'websockets':len(sockets),'native_file_bytes':len(file_body),'transport':'real HTTP and WebSocket; native channels with server-side content encryption'}
            async def read(index):
                user = members[index % len(members)]
                path = ['/messages/channels/'+channel+'?limit=50','/files/channels/'+channel,'/channels/'+channel+'/members','/auth/me'][index % 4]
                await api('GET',path,user,replica=index,measured='read')
            async def send(index):
                return (await api('POST','/messages/channels/'+channel,members[index % len(members)],{'content':'Isolated load message '+run+' '+str(index)},replica=index,measured='write')).json()['id']
            async def outsider_denials():
                await api('GET','/messages/channels/'+channel,outsider,expected=403,measured='denial')
                await api('GET','/files/channels/'+channel,outsider,expected=403,measured='denial')
                await api('GET','/files/'+uploaded['id'],outsider,expected=403,measured='denial')
            started = time.perf_counter()
            work = [read(index) for index in range(args.requests)] + [send(index) for index in range(args.messages)]
            work.append(outsider_denials())
            random.Random(0).shuffle(work)
            phase('concurrent reads, writes and outsider denials')
            results = await asyncio.gather(*work)
            sent_ids = {value for value in results if isinstance(value,str)}
            phase('verify complete live fanout and committed history')
            await until(lambda: all(sent_ids.issubset(set(items)) for items in received.values()),'WebSocket fanout missed committed messages')
            elapsed = time.perf_counter() - started
            for items in received.values():
                relevant = [value for value in items if value in sent_ids]
                if len(relevant) != len(sent_ids):
                    raise AssertionError('A load receiver saw duplicate message deliveries')
            history = (await api('GET','/messages/channels/'+channel+'?limit=100',owner)).json()
            if {item['id'] for item in history} != sent_ids:
                raise AssertionError('Persisted history differs from committed writes')
            download = await api('GET','/files/'+uploaded['id'],owner,measured='file')
            if hashlib.sha256(download.content).digest() != hashlib.sha256(file_body).digest():
                raise AssertionError('Loaded file bytes changed')
            revoked_user = members[1]
            phase('verify current revocation under retained live connections')
            await api('POST','/auth/logout',revoked_user)
            after_revoke = (await api('POST','/messages/channels/'+channel,owner,{'content':'Post-revocation check '+run})).json()['id']
            retained_names = [name for name in received if not name.startswith('1:')]
            await until(lambda: all(after_revoke in received[name] for name in retained_names),'Authorized members lost post-revocation delivery')
            await asyncio.sleep(1)
            if any(after_revoke in items for name, items in received.items() if name.startswith('1:')):
                raise AssertionError('Logged-out session received a later message')
            await api('GET','/auth/me',revoked_user,expected=401)
            report.update({'passed':True,'measured_seconds':round(elapsed,3),'rest_requests_per_second':round((args.requests+args.messages+3)/elapsed,2),'fanout_deliveries_verified':len(sent_ids)*len(sockets),'status_counts':dict(statuses),'latency':{key:percentiles(value) for key,value in latency.items()},'checks':{'complete_unique_live_fanout':True,'persisted_message_set_matches':True,'exact_file_bytes':True,'outsider_denied_during_load':True,'post_logout_rest_and_live_delivery_denied':True}})
        finally:
            report['latency'] = {key:percentiles(value) for key,value in latency.items()}
            report['status_counts'] = dict(statuses)
            for sio in sockets:
                if sio.connected:
                    await sio.disconnect()
            # The installation is disposable; test resources intentionally remain
            # for the caller's inspection until its owned fixture is destroyed.


def main():
    args = arguments()
    report = {'started_at':datetime.now(timezone.utc).isoformat(),'passed':False,'limits':['Bounded isolated native-chat smoke measurement, not capacity certification','Excludes Matrix cryptography, media, external providers and wide-area networks','Operator must remove the disposable installation/test resources after inspection']}
    try:
        asyncio.run(exercise(args,report))
    except Exception as failure:
        report['failure_type'] = type(failure).__name__
        raise
    finally:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':report['passed'],'users':args.users,'websockets':report['topology']['websockets'],'rest_requests_per_second':report['rest_requests_per_second'],'report':str(args.report)}))


if __name__ == '__main__':
    main()
