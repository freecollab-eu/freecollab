"""Bounded public-HTTPS transport shared by customer-authorized sync adapters.

Original hostname verification, fresh public DNS and pinned connections are reused
from the customer OIDC transport. Provider credentials and response bodies never
appear in exception text. No redirect is followed and no proxy failure falls back
to a direct socket. Tests explicitly replace individual helpers for local fixtures.
"""
from dataclasses import dataclass
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re
import ssl
import time
from urllib.parse import quote, urlunsplit

import httpcore

from . import oidc_transport as pinned

MAX_BYTES = 50 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
_OPERATION_DEADLINE = ContextVar("remote_sync_operation_deadline", default=None)


def begin_budget(seconds):
    existing = _OPERATION_DEADLINE.get()
    deadline = time.monotonic() + seconds
    return _OPERATION_DEADLINE.set(min(existing, deadline) if existing is not None else deadline)


def end_budget(token):
    _OPERATION_DEADLINE.reset(token)


class ProviderError(Exception):
    def __init__(self, code, status=None, retry_after=None):
        self.code, self.status, self.retry_after = code, status, retry_after
        super().__init__("External synchronization failed (" + str(code) + ")")


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict
    body: bytes

    def json(self):
        try:
            if len(self.body) > MAX_JSON_BYTES:
                raise ValueError()
            result = json.loads(self.body, object_pairs_hook=pinned._pairs, parse_constant=pinned._constant)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, TypeError, UnicodeError):
            raise ProviderError("invalid_response") from None


def validate_url(value):
    try:
        pinned.validate_public_url(value)
        return value
    except pinned.TransportError:
        raise ProviderError("unsafe_destination") from None


def syntax_url(value):
    try:
        pinned.normalize_url(value)
        return value
    except pinned.TransportError:
        raise ProviderError("unsafe_destination") from None


def retry_after(headers):
    try:
        raw = headers.get("retry-after", "60")
        if raw.isdigit():
            return max(1, min(int(raw), 3600))
        return max(1, min(int((parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds()), 3600))
    except (TypeError, ValueError, OverflowError):
        return 60


def http_error(status, headers=None):
    codes = {401: "reauthorization_required", 403: "permission_denied", 404: "not_found",
             409: "conflict", 410: "cursor_expired", 412: "conflict", 423: "resource_locked", 429: "rate_limited"}
    return ProviderError(codes.get(status, "provider_unavailable" if status >= 500 else "provider_rejected"),
                         status=status, retry_after=retry_after(headers or {}) if status in (429, 503) else None)


def check_response(response, allowed=(200, 201, 204, 207)):
    if response.status not in allowed:
        raise http_error(response.status, response.headers)
    return response


def request(method, url, *, headers=None, body=None, max_bytes=MAX_JSON_BYTES, timeout=30, allow_redirect_location=False, http2=False):
    """Return one bounded response; HTTP errors stay typed without body leakage.

    Non-2xx responses are returned for protocol-specific handling. Redirects are
    rejected by default. Explicit opt-in returns only a bounded Location for
    302/303, without following it or reading its body. TLS verification is mandatory.
    """
    try:
        if (method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "PROPFIND", "REPORT", "OPTIONS"}
                or type(allow_redirect_location) is not bool or type(http2) is not bool
                or type(max_bytes) is not int or not 0 <= max_bytes <= MAX_BYTES
                or type(timeout) not in {int, float} or not 0 < timeout <= 60):
            raise ValueError()
        deadline = time.monotonic() + timeout
        operation_deadline = _OPERATION_DEADLINE.get()
        if operation_deadline is not None:
            deadline = min(deadline, operation_deadline)
        pinned._remaining(deadline)
        parsed, host = pinned.normalize_url(url)
        address = pinned.public_addresses(host, timeout=pinned._remaining(deadline, 5))[0]
        port = parsed.port or 443
        authority = "[" + address + "]" if ":" in address else address
        authority += ":" + str(port) if port != 443 else ""
        destination = urlunsplit(("https", authority, quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                                  quote(parsed.query, safe="=&?/%:@!$'()*+,;~-._"), ""))
        original = "[" + host + "]" if ":" in host else host
        original += ":" + str(port) if parsed.port is not None else ""
        outgoing = {"Host": original, "Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "FreeCollab-Sync/1"}
        for key, value in (headers or {}).items():
            if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9-]+", key)
                    or not isinstance(value, str) or len(value) > 16384
                    or any(ord(c) < 32 or ord(c) == 127 for c in value)
                    or key.lower() in {"host", "connection", "content-length", "transfer-encoding", "proxy-authorization"}):
                raise ValueError()
            outgoing[key] = value
        if body is not None:
            if not isinstance(body, bytes) or len(body) > MAX_BYTES + 64 * 1024:
                raise ValueError()
            outgoing["Content-Length"] = str(len(body))
        proxy = pinned._proxy(host, port)
        context = ssl.create_default_context()
        backend = pinned._PinnedBackend(address, host, deadline, proxy_tls=bool(proxy and proxy[2]))
        options = {"ssl_context": context, "network_backend": backend, "max_connections": 1, "max_keepalive_connections": 0, "http1": not http2, "http2": http2}
        pool = (httpcore.HTTPProxy(proxy[0], proxy_auth=proxy[1], proxy_ssl_context=context if proxy[2] else None, **options)
                if proxy else httpcore.ConnectionPool(**options))
        with pool:
            with pool.stream(method, destination, headers=outgoing, content=body,
                             extensions={"timeout": {key: pinned._remaining(deadline, 5 if key == "connect" else 30)
                                                     for key in ("connect", "read", "write", "pool")}}) as response:
                status = response.status
                safe_headers = {}
                for key, value in response.headers:
                    name = key.decode("ascii").lower()
                    if name in {"etag", "content-type", "content-length", "retry-after", "content-encoding"}:
                        safe_headers[name] = value.decode("latin1")
                if 300 <= status < 400:
                    if allow_redirect_location and status in {302, 303}:
                        locations = [v.decode("latin1") for k, v in response.headers if k.lower() == b"location"]
                        if (len(locations) != 1 or not 1 <= len(locations[0]) <= 16384
                                or any(ord(c) < 32 or ord(c) == 127 for c in locations[0])):
                            raise ProviderError("invalid_response", status=status)
                        safe_headers["location"] = locations[0]
                        return Response(status, safe_headers, b"")
                    raise ProviderError("redirect_not_allowed", status=status)
                if safe_headers.get("content-encoding", "identity").lower() != "identity":
                    raise ProviderError("unsupported_encoding", status=status)
                if int(safe_headers.get("content-length", "0")) > max_bytes:
                    raise ProviderError("response_too_large", status=status)
                result = bytearray()
                for chunk in response.iter_stream():
                    pinned._remaining(deadline)
                    result.extend(chunk)
                    if len(result) > max_bytes:
                        raise ProviderError("response_too_large", status=status)
        pinned._remaining(deadline)
        return Response(status, safe_headers, bytes(result))
    except ProviderError:
        raise
    except pinned.TransportError:
        raise ProviderError("unsafe_destination") from None
    except (httpcore.TimeoutException, TimeoutError):
        raise ProviderError("provider_timeout") from None
    except Exception:
        raise ProviderError("provider_unavailable") from None
