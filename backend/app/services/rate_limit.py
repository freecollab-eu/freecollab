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


def install_auth_rate_limits(app):
    """Initialize state at startup; safe to call on each application lifespan."""
    app.state.auth_rate_limiter = AuthRateLimiter()


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
