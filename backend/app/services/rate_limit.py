"""Bounded, process-local auth throttling for the supported single-worker pilot.

Addresses come from request.client, never from caller-provided forwarding headers.
An operator using a reverse proxy must configure that proxy and Uvicorn's trusted
forwarder allowlist. Multiple workers/replicas require a shared limiter first.
"""
from collections import OrderedDict, deque
from hashlib import sha256
from math import ceil
from threading import Lock
from time import monotonic
import secrets

import redis

from fastapi import HTTPException, Request


class AuthRateLimiter:
    def __init__(self, *, clock=monotonic, max_keys=10000,
                 registration_limit=30, login_limit=60,
                 account_failure_limit=10, address_failure_limit=30):
        self.clock = clock
        self.max_keys = max_keys
        self.registration_limit = registration_limit
        self.login_limit = login_limit
        self.account_failure_limit = account_failure_limit
        self.address_failure_limit = address_failure_limit
        self.events = OrderedDict()
        self.lock = Lock()

    @staticmethod
    def _denied(retry):
        raise HTTPException(429, "Too many authentication attempts. Please retry later.",
                            headers={"Retry-After": str(max(1, ceil(retry)))})

    def _prune(self, now):
        # Every retained event has at most the failure window's five-minute TTL.
        for key, queue in list(self.events.items()):
            if not queue or queue[-1] <= now - 300:
                self.events.pop(key)

    def _events(self, key, now, window, create=False):
        queue = self.events.get(key)
        if queue is None:
            if not create:
                return ()
            self._prune(now)
            if len(self.events) >= self.max_keys:
                self._denied(60)
            queue = deque()
            self.events[key] = queue
        while queue and queue[0] <= now - window:
            queue.popleft()
        if queue:
            self.events.move_to_end(key)
        elif not create:
            self.events.pop(key, None)
            return ()
        return queue

    def request(self, action, address):
        limit = self.registration_limit if action == "register" else self.login_limit
        now = self.clock()
        with self.lock:
            queue = self._events((action, address), now, 60, create=True)
            if len(queue) >= limit:
                self._denied(queue[0] + 60 - now)
            queue.append(now)
            self.events.move_to_end((action, address))

    @staticmethod
    def _account(email):
        return sha256(email.casefold().strip().encode()).hexdigest()

    def check_login(self, address, email):
        now = self.clock()
        with self.lock:
            for key, limit in ((('failed-address', address), self.address_failure_limit),
                               (('failed-account', self._account(email)), self.account_failure_limit)):
                queue = self._events(key, now, 300)
                if len(queue) >= limit:
                    self._denied(queue[0] + 300 - now)

    def failed_login(self, address, email):
        now = self.clock()
        with self.lock:
            for key, limit in ((('failed-address', address), self.address_failure_limit),
                               (('failed-account', self._account(email)), self.account_failure_limit)):
                queue = self._events(key, now, 300, create=True)
                if len(queue) < limit:
                    queue.append(now)
                self.events.move_to_end(key)

    def successful_login(self, email):
        with self.lock:
            self.events.pop(('failed-account', self._account(email)), None)


SHARED_WINDOW = """
local t=redis.call('TIME');local now=tonumber(t[1])*1000+math.floor(tonumber(t[2])/1000)
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now)
local create=0
for i=2,#KEYS do
 local offset=3+(i-2)*3
 local window=tonumber(ARGV[offset]);local limit=tonumber(ARGV[offset+1]);local mode=tonumber(ARGV[offset+2])
 redis.call('ZREMRANGEBYSCORE',KEYS[i],'-inf',now-window)
 local count=redis.call('ZCARD',KEYS[i])
 if mode~=2 and count>=limit then
  local first=redis.call('ZRANGE',KEYS[i],0,0,'WITHSCORES')
  return {0,math.max(1,math.ceil((tonumber(first[2])+window-now)/1000))}
 end
 if mode>0 and not redis.call('ZSCORE',KEYS[1],KEYS[i]) then create=create+1 end
end
if redis.call('ZCARD',KEYS[1])+create>tonumber(ARGV[1]) then return {-1,5} end
for i=2,#KEYS do
 local offset=3+(i-2)*3
 local window=tonumber(ARGV[offset]);local limit=tonumber(ARGV[offset+1]);local mode=tonumber(ARGV[offset+2])
 if mode>0 then
  if redis.call('ZCARD',KEYS[i])<limit then redis.call('ZADD',KEYS[i],now,ARGV[2]..':'..i) end
  redis.call('PEXPIRE',KEYS[i],window+1000)
  redis.call('ZADD',KEYS[1],now+window+1000,KEYS[i])
 end
end
redis.call('EXPIRE',KEYS[1],600)
return {1,0}
"""


class RedisAuthRateLimiter(AuthRateLimiter):
    """Atomic server-time sliding windows shared by all configured API workers."""
    def __init__(self, *, shared=None, **kwargs):
        super().__init__(**kwargs)
        from .shared_runtime import runtime
        self.shared = shared or runtime()

    def _run(self, buckets):
        from .shared_runtime import unavailable
        self.shared.validate(force=True)
        keys = [self.shared.namespace + ':auth-index']
        arguments = [self.max_keys, secrets.token_hex(16)]
        for label, identifier, limit, seconds, mode in buckets:
            keys.append(self.shared.key('auth', label + '\0' + identifier))
            arguments.extend((seconds*1000,limit,mode))
        try:
            allowed,retry=self.shared.client.eval(SHARED_WINDOW,len(keys),*keys,*arguments)
        except (redis.RedisError,OSError,ValueError): raise unavailable() from None
        if allowed<0: raise unavailable()
        if not allowed:self._denied(retry)

    def request(self, action, address):
        limit=self.registration_limit if action=='register' else self.login_limit
        self._run([(action,address,limit,60,1)])

    def check_login(self,address,email):
        self._run([('failed-address',address,self.address_failure_limit,300,0),
            ('failed-account',self._account(email),self.account_failure_limit,300,0)])

    def failed_login(self,address,email):
        self._run([('failed-address',address,self.address_failure_limit,300,2),
            ('failed-account',self._account(email),self.account_failure_limit,300,2)])

    def successful_login(self,email):
        from .shared_runtime import unavailable
        self.shared.validate(force=True)
        key=self.shared.key('auth','failed-account\0'+self._account(email))
        try:
            with self.shared.client.pipeline(transaction=True) as pipe:
                pipe.delete(key);pipe.zrem(self.shared.namespace+':auth-index',key);pipe.execute()
        except (redis.RedisError,OSError):raise unavailable() from None


def install_auth_rate_limits(app):
    """Initialize state at startup; safe to call on each application lifespan."""
    from .shared_runtime import configured
    app.state.auth_rate_limiter = RedisAuthRateLimiter() if configured() else AuthRateLimiter()


def limiter_for(request: Request):
    limiter = getattr(request.app.state, "auth_rate_limiter", None)
    if limiter is None:
        install_auth_rate_limits(request.app)
        limiter = request.app.state.auth_rate_limiter
    return limiter


def request_address(request: Request):
    return request.client.host if request.client else "unknown"


def auth_rate_limit(request: Request):
    action = request.url.path.rstrip("/").rsplit("/", 1)[-1]
    limiter_for(request).request(action, request_address(request))
