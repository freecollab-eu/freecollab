"""Private broker fanout that delegates every delivery to local authorization.

This deliberately does not use AsyncRedisManager's global Socket.IO rooms:
another process cannot authorize sessions held only in this process's memory.
"""
import asyncio
from contextlib import suppress
import json
import secrets
import time

import redis

from ..services.shared_runtime import runtime, unavailable


PRESENCE_WRITE = """
local t=redis.call('TIME'); local now=tonumber(t[1])+tonumber(t[2])/1000000
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now)
if not redis.call('ZSCORE',KEYS[1],ARGV[1]) and redis.call('ZCARD',KEYS[1])>=64 then return 0 end
redis.call('SET',KEYS[2],ARGV[2],'EX',ARGV[3])
redis.call('ZADD',KEYS[1],now+tonumber(ARGV[3]),ARGV[1])
redis.call('EXPIRE',KEYS[1],ARGV[3])
return 1
"""


class RealtimeBroker:
    def __init__(self):
        self.runtime = runtime()
        self.process_id = secrets.token_hex(16)
        self.client = None
        self.pubsub = None
        self.listener = None
        self.ready = False

    async def publish(self, target, identifier, event, data, *, skip=None):
        # Sync pool is thread-safe across worker asyncio.run invocations. It is
        # never attached to a short-lived event loop, and raw JWTs aren't sent.
        await asyncio.to_thread(self.runtime.publish, {"v":1,"origin":self.process_id,"target":target,
            "id":identifier,"event":event,"data":data,"skip":skip})

    async def start(self, deliver):
        await asyncio.to_thread(self.runtime.validate,force=True)
        self.client = self.runtime.async_client()
        self.pubsub = self.client.pubsub(ignore_subscribe_messages=False)
        await self.pubsub.subscribe(self.runtime.namespace + ":events")
        deadline=time.monotonic()+3
        subscribed=False
        while time.monotonic()<deadline:
            confirmation=await self.pubsub.get_message(ignore_subscribe_messages=False,timeout=.5)
            if confirmation and confirmation.get("type")=="subscribe":subscribed=True;break
        if not subscribed:
            await self.pubsub.aclose();await self.client.aclose()
            raise unavailable()
        self.ready = True
        async def listen():
            last_ping=time.monotonic()
            while True:
                try:
                    message = await self.pubsub.get_message(ignore_subscribe_messages=True,timeout=1)
                    if not message:
                        if time.monotonic()-last_ping>=5:
                            await self.pubsub.ping();last_ping=time.monotonic()
                        continue
                    if message.get("type")=="pong":self.ready=True;continue
                    data = message.get("data")
                    if not isinstance(data,str) or len(data.encode()) > 1024*1024: continue
                    envelope = json.loads(data)
                    if (not isinstance(envelope,dict) or envelope.get("v")!=1 or envelope.get("origin")==self.process_id
                            or envelope.get("target") not in {"channel","user","presence"}
                            or not isinstance(envelope.get("event"),str) or len(envelope["event"])>80
                            or not isinstance(envelope.get("data"),dict) or not isinstance(envelope.get("id"),str)
                            or not 1<=len(envelope["id"])<=128):
                        continue
                    self.ready = True
                    await deliver(envelope)
                except asyncio.CancelledError: raise
                except (redis.RedisError, ValueError, TypeError, KeyError):
                    self.ready = False
                    await asyncio.sleep(.5)
                    try:await self.pubsub.ping()
                    except redis.RedisError:pass
        self.listener = asyncio.create_task(listen(), name="freecollab-authorized-broker")

    async def write_presence(self, sid, row):
        identifier = self.process_id + ":" + sid
        try:
            accepted = await self.client.eval(PRESENCE_WRITE,2,self.runtime.key("presence-user",row["user_id"]),
                self.runtime.key("presence-session",identifier),identifier,json.dumps(row,separators=(",",":")),self.runtime.lease)
            if not accepted: raise unavailable()
        except (redis.RedisError,OSError): raise unavailable() from None

    async def remove_presence(self, sid, user_id):
        identifier = self.process_id + ":" + sid
        try:
            async with self.client.pipeline(transaction=True) as pipe:
                pipe.delete(self.runtime.key("presence-session",identifier))
                pipe.zrem(self.runtime.key("presence-user",user_id),identifier)
                await pipe.execute()
        except (redis.RedisError,OSError): raise unavailable() from None

    async def stop(self):
        self.ready=False
        if self.listener:
            self.listener.cancel()
            with suppress(asyncio.CancelledError): await self.listener
        if self.pubsub: await self.pubsub.aclose()
        if self.client: await self.client.aclose()


_broker = None


def broker():
    global _broker
    active = runtime()
    if active is None: return None
    if _broker is None or _broker.runtime is not active: _broker = RealtimeBroker()
    return _broker
