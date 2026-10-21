"""Optional private Valkey runtime; defaults remain process-local.

Keys contain hashes of account/address/session identifiers, never bearer tokens.
Raw JWTs stay in their owning API process. TLS and noeviction are checked before
shared coordination is used; outages fail closed instead of weakening limits.
"""
from hashlib import sha256
import json
import re
from threading import Lock
from urllib.parse import urlsplit

from fastapi import HTTPException
import redis
import redis.asyncio as async_redis

from ..config import settings


def configured():
    return bool(getattr(settings, "redis_url", None))


def unavailable():
    return HTTPException(503, "Shared coordination is unavailable. Please retry shortly.", headers={"Retry-After":"5"})


def digest(value):
    return sha256(value.encode()).hexdigest()


def options():
    secret = getattr(settings, "redis_url", None)
    value = secret.get_secret_value() if hasattr(secret, "get_secret_value") else secret
    namespace = getattr(settings, "redis_namespace", "freecollab")
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError()
        if getattr(settings, "redis_tls_required", True) and parsed.scheme != "rediss" and parsed.hostname not in {"localhost","127.0.0.1","::1"}:
            raise ValueError()
        if not re.fullmatch(r"/[0-9]{1,2}|", parsed.path) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,64}", namespace):
            raise ValueError()
        lease = getattr(settings, "redis_presence_lease_seconds", 75)
        if type(lease) is not int or not 30 <= lease <= 300: raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("Invalid shared runtime URL, TLS policy, namespace or lease configuration") from None
    return value, namespace, lease


class SharedRuntime:
    def __init__(self):
        value, self.namespace, self.lease = options()
        self.client = redis.Redis.from_url(value, decode_responses=True, socket_connect_timeout=2,
            socket_timeout=2, retry_on_timeout=False, max_connections=32)
        self.url = value
        self._validated = False
        self._lock = Lock()

    def key(self, kind, value):
        return self.namespace + ":" + kind + ":" + digest(value)

    def validate(self, *, force=False):
        if force or not self._validated:
            with self._lock:
                if force or not self._validated:
                    try:
                        self.client.ping()
                        policy = self.client.config_get("maxmemory-policy")
                        if policy.get("maxmemory-policy") != "noeviction": raise ValueError()
                    except (redis.RedisError, ValueError, OSError): raise unavailable() from None
                    self._validated = True

    def async_client(self):
        return async_redis.Redis.from_url(self.url, decode_responses=True, socket_connect_timeout=2,
            socket_timeout=2, retry_on_timeout=False, max_connections=32)

    def publish(self, envelope):
        self.validate()
        encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode()) > 1024 * 1024: raise HTTPException(413, "Realtime event exceeds shared delivery limit")
        try: self.client.publish(self.namespace + ":events", encoded)
        except (redis.RedisError, OSError): raise unavailable() from None

    def presence(self, user_id, validator):
        """Bounded current digest/claims leases; recheck database authority."""
        self.validate()
        key = self.key("presence-user", user_id)
        try:
            now = self.client.time(); timestamp = now[0] + now[1] / 1e6
            self.client.zremrangebyscore(key, "-inf", timestamp)
            members = self.client.zrange(key, 0, 63)
            rows = self.client.mget([self.key("presence-session", value) for value in members]) if members else []
            valid, stale = [], []
            for identifier, raw in zip(members, rows):
                try:
                    row = json.loads(raw) if raw else None
                    if (not row or row["user_id"] != user_id or row["status"] not in {"online","away","busy","offline"}
                            or not validator(row)): stale.append(identifier)
                    else: valid.append(row)
                except (KeyError, ValueError, TypeError): stale.append(identifier)
            if stale: self.client.zrem(key, *stale)
        except (redis.RedisError, OSError): raise unavailable() from None
        if not valid: return {"status":"offline", "last_seen":None}
        # Most recently explicit status change wins across devices. A periodic
        # lease refresh preserves that timestamp rather than overriding status.
        latest = max(valid, key=lambda row: row["changed_at"])
        return {"status":latest["status"], "last_seen":latest["last_seen"]}

    def close(self):
        self.client.close()


_runtime = None
_identity = None


def runtime():
    global _runtime, _identity
    if not configured(): return None
    identity = options()[:2]
    if _runtime is None or _identity != identity:
        if _runtime is not None: _runtime.close()
        _runtime, _identity = SharedRuntime(), identity
    return _runtime
