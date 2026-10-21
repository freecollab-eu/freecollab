"""Public HTTPS transport for customer-configured OIDC providers.

Every endpoint, including discovery-advertised token/JWKS URLs, is independently
validated and freshly resolved. Connections target a validated IP while TLS and
Host retain the original name, also through the inherited outbound proxy. There
is no redirect following, private-network option or direct retry after a proxy
failure. Tests can replace helpers explicitly without weakening runtime policy.
"""
import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import ipaddress
import json
import re
import socket
import ssl
from threading import BoundedSemaphore
import time
from urllib.parse import quote, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import getproxies, proxy_bypass_environment

import httpcore

MAX_RESPONSE_BYTES = 256 * 1024
MAX_URL_BYTES = 4096
_DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="customer-oidc-dns")
_DNS_SLOTS = BoundedSemaphore(4)
_TRANSITION_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "64:ff9b::/96", "64:ff9b:1::/48", "2001::/32", "2002::/16",
))


class TransportError(Exception):
    """Safe to expose as a generic provider-connectivity failure."""


def public_ip(address):
    value = ipaddress.ip_address(address)
    if not value.is_global or value.is_multicast or value.is_unspecified:
        return False
    if value.version == 6:
        if value.ipv4_mapped and not public_ip(value.ipv4_mapped):
            return False
        if any(value in network for network in _TRANSITION_NETWORKS):
            return False
    return True


def normalize_url(value, *, issuer=False):
    """Return parsed URL and ASCII host without altering an issuer identifier."""
    try:
        if (not isinstance(value, str) or len(value.encode("utf-8")) > MAX_URL_BYTES
                or "\\" in value or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value)):
            raise ValueError()
        parsed = urlsplit(value)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        if (parsed.scheme != "https" or not host or len(host) > 253 or parsed.port not in (None, 443)
                or parsed.username is not None or parsed.password is not None or parsed.fragment
                or (issuer and parsed.query) or "%" in host or host.endswith(".")
                or host == "metadata.google.internal" or host == "localhost"
                or host.endswith((".localhost", ".local", ".internal", ".home.arpa"))):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
                raise ValueError()
        else:
            if not public_ip(address):
                raise ValueError()
        return parsed, host
    except (ValueError, TypeError, UnicodeError):
        raise TransportError("Organization sign-in requires a public HTTPS endpoint on port 443") from None


def _resolve(host, port, timeout):
    """Bound even DNS for the trusted inherited proxy; never queue endlessly."""
    deadline = time.monotonic() + max(0, min(float(timeout), 5))
    if not _DNS_SLOTS.acquire(timeout=max(0, deadline - time.monotonic())):
        raise TransportError("Organization sign-in provider could not be resolved")
    try:
        lookup = _DNS_POOL.submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    except Exception:
        _DNS_SLOTS.release()
        raise TransportError("Organization sign-in provider could not be resolved") from None
    lookup.add_done_callback(lambda _: _DNS_SLOTS.release())
    try:
        addresses = sorted({answer[4][0] for answer in lookup.result(timeout=max(0, deadline - time.monotonic()))})
        if not addresses:
            raise ValueError()
        return addresses
    except (OSError, ValueError, TypeError, FutureTimeout):
        lookup.cancel()
        raise TransportError("Organization sign-in provider could not be resolved") from None


def public_addresses(host, timeout=5):
    """Reject every private answer, including mixed public/private DNS sets."""
    addresses = _resolve(host, 443, timeout)
    if any(not public_ip(address) for address in addresses):
        raise TransportError("Organization sign-in provider must resolve only to public addresses")
    return addresses


def validate_public_url(value, *, issuer=False):
    _, host = normalize_url(value, issuer=issuer)
    public_addresses(host)
    return value


def _remaining(deadline, maximum=None):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise httpcore.ReadTimeout("Organization sign-in transport deadline reached")
    return min(remaining, maximum) if maximum is not None else remaining


class _TLSNameStream(httpcore.NetworkStream):
    def __init__(self, stream, ip, hostname, deadline, proxy_tls=False):
        self.stream, self.ip, self.hostname = stream, ip, hostname
        self.deadline, self.proxy_tls = deadline, proxy_tls

    def read(self, max_bytes, timeout=None):
        return self.stream.read(max_bytes, _remaining(self.deadline, timeout))

    def write(self, buffer, timeout=None):
        return self.stream.write(buffer, _remaining(self.deadline, timeout))

    def close(self):
        self.stream.close()

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        # For HTTPS proxies the first handshake belongs to the proxy itself.
        # Even a literal proxy IP equal to the target IP keeps its own name.
        name = server_hostname if self.proxy_tls else self.hostname if server_hostname == self.ip else server_hostname
        secured = self.stream.start_tls(ssl_context, name, _remaining(self.deadline, timeout))
        return _TLSNameStream(secured, self.ip, self.hostname, self.deadline, False)


class _PinnedBackend(httpcore.NetworkBackend):
    def __init__(self, ip, hostname, deadline, *, proxy_tls=False):
        self.ip, self.hostname, self.deadline = ip, hostname, deadline
        self.proxy_tls, self.backend = proxy_tls, httpcore.SyncBackend()

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        # The destination is already numeric. An inherited proxy may instead
        # have a DNS hostname: resolve it under the same absolute deadline, then
        # connect numerically so socket.create_connection cannot do unbounded
        # name resolution. The proxy is trusted configuration and may be local.
        try:
            ipaddress.ip_address(host)
        except ValueError:
            host = _resolve(host, port, _remaining(self.deadline, timeout))[0]
        stream = self.backend.connect_tcp(host, port, _remaining(self.deadline, timeout), local_address, socket_options)
        return _TLSNameStream(stream, self.ip, self.hostname, self.deadline, self.proxy_tls)

    def connect_unix_socket(self, *args, **kwargs):
        raise httpcore.ConnectError("Unix sockets are not organization sign-in endpoints")

    def sleep(self, seconds):
        self.backend.sleep(min(seconds, _remaining(self.deadline)))


def _proxy(host, port):
    proxies = getproxies()
    authority = ("[" + host + "]" if ":" in host else host) + ":" + str(port)
    if proxy_bypass_environment(authority, proxies):
        return None
    value = proxies.get("https") or proxies.get("all")
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise TransportError("Organization sign-in outbound proxy is unavailable")
    parsed.port
    name = "[" + parsed.hostname + "]" if ":" in parsed.hostname else parsed.hostname
    name += ":" + str(parsed.port) if parsed.port else ""
    url = urlunsplit((parsed.scheme, name, "", "", ""))
    auth = (unquote(parsed.username).encode(), unquote(parsed.password or "").encode()) if parsed.username is not None else None
    return url, auth, parsed.scheme == "https"


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError("Ambiguous JSON object")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("Non-finite JSON value")


def request_json(method, url, *, data=None, auth=None, timeout=12, max_bytes=MAX_RESPONSE_BYTES):
    """Fetch one bounded JSON object. No provider details escape in errors."""
    try:
        if (type(timeout) not in {int, float} or not 0 < timeout <= 30
                or type(max_bytes) is not int or not 1 <= max_bytes <= MAX_RESPONSE_BYTES):
            raise ValueError()
        deadline = time.monotonic() + timeout
        parsed, host = normalize_url(url)
        address = public_addresses(host, timeout=_remaining(deadline, 5))[0]
        port = parsed.port or 443
        authority = "[" + address + "]" if ":" in address else address
        # Non-443 values can only be supplied by an explicit test replacement of
        # normalize_url. The production URL policy above always requires 443.
        authority += ":" + str(port) if port != 443 else ""
        pinned = urlunsplit(("https", authority, quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                             quote(parsed.query, safe="=&?/%:@!$'()*+,;~-._"), ""))
        target_host = "[" + host + "]" if ":" in host else host
        target_host += ":" + str(port) if parsed.port is not None else ""
        headers = {"Host": target_host, "Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "FreeCollab-OIDC/1"}
        if method not in {"GET", "POST"} or (method == "GET" and (data is not None or auth is not None)):
            raise ValueError()
        content = None
        if method == "POST":
            if not isinstance(data, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in data.items()):
                raise ValueError()
            content = urlencode(data).encode("ascii")
            if len(content) > 32 * 1024:
                raise ValueError()
            headers.update({"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(content))})
            if auth is not None:
                if not isinstance(auth, tuple) or len(auth) != 2 or not all(isinstance(value, str) for value in auth):
                    raise ValueError()
                headers["Authorization"] = "Basic " + base64.b64encode((auth[0] + ":" + auth[1]).encode("utf-8")).decode("ascii")
        proxy = _proxy(host, port)
        context = ssl.create_default_context()
        backend = _PinnedBackend(address, host, deadline, proxy_tls=bool(proxy and proxy[2]))
        options = {"ssl_context": context, "network_backend": backend, "max_connections": 1, "max_keepalive_connections": 0}
        pool = (httpcore.HTTPProxy(proxy[0], proxy_auth=proxy[1], proxy_ssl_context=context if proxy[2] else None, **options)
                if proxy else httpcore.ConnectionPool(**options))
        with pool:
            with pool.stream(method, pinned, headers=headers, content=content,
                             extensions={"timeout": {key: _remaining(deadline, 5 if key == "connect" else 12) for key in ("connect", "read", "write", "pool")}}) as response:
                if response.status != 200:
                    raise ValueError()
                for key, value in response.headers:
                    if key.lower() == b"content-length" and int(value) > max_bytes:
                        raise ValueError()
                body = bytearray()
                for chunk in response.iter_stream():
                    _remaining(deadline)
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise ValueError()
        _remaining(deadline)
        result = json.loads(body, object_pairs_hook=_pairs, parse_constant=_constant)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except Exception:
        raise TransportError("Organization sign-in provider request failed") from None
