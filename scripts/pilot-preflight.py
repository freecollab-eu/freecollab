#!/usr/bin/env python3
"""Bounded, read-only HTTPS checks for an operator's FreeCollab installation.

Never logs response bodies or credentials. Optional metrics access accepts only
a private token file; this command has no mail, OAuth, retry or mutation action.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import ssl
import stat
import subprocess
import sys
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPSHandler, HTTPRedirectHandler, Request

MAX_RESPONSE = 256 * 1024
MAX_TOKEN = 4096


class CheckError(Exception):
    """Contains only a fixed safe diagnostic, never remote content."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise CheckError("redirect_refused")


def public_origin(value):
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        if (parsed.scheme != "https" or not host or parsed.port not in (None, 443)
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or any(character.isspace() or ord(character) < 32 for character in value)
                or "\\" in value or "%" in host or host.endswith(".")):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            labels = host.split(".")
            if len(host) > 253 or len(labels) < 2 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
                raise ValueError()
            if host.endswith((".localhost", ".local", ".internal", ".home.arpa")):
                raise ValueError()
        else:
            if not address.is_global or address.is_multicast:
                raise ValueError()
        authority = "[" + host + "]" if ":" in host else host
        return "https://" + authority, host
    except (ValueError, TypeError, UnicodeError):
        raise CheckError("public_https_origin_required") from None


def resolve_public(host, timeout):
    # A separate short-lived resolver bounds even an unresponsive OS DNS lookup.
    # Keep inherited proxy/CA settings for the subsequent verified HTTPS client.
    code = "import json,socket,sys;print(json.dumps(sorted({r[4][0] for r in socket.getaddrinfo(sys.argv[1],443,type=socket.SOCK_STREAM)})[:32]))"
    try:
        result = subprocess.run([sys.executable, "-I", "-c", code, host], capture_output=True, timeout=min(timeout, 5), check=True)
        addresses = json.loads(result.stdout)
        if not isinstance(addresses, list) or not addresses or any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast for address in addresses):
            raise ValueError()
    except (OSError, ValueError, subprocess.SubprocessError):
        raise CheckError("public_dns_check_failed") from None


def private_token(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
                raise ValueError()
            token = stream.read(MAX_TOKEN + 1)
        token = token.removesuffix(b"\n")
        if not 32 <= len(token) <= MAX_TOKEN or not re.fullmatch(rb"[\x21-\x7e]+", token):
            raise ValueError()
        return token.decode("ascii")
    except (OSError, ValueError, UnicodeError):
        raise CheckError("private_metrics_token_file_required") from None


def _request(opener, origin, path, timeout, *, token=None, json_body=True):
    headers = {"Accept": "application/json" if json_body else "text/plain", "User-Agent": "FreeCollab-preflight/1"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with opener.open(Request(origin + path, headers=headers, method="GET"), timeout=timeout) as response:
            if response.status != 200:
                raise CheckError("unexpected_http_status")
            payload = response.read(MAX_RESPONSE + 1)
            if len(payload) > MAX_RESPONSE:
                raise CheckError("response_too_large")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if json_body:
                if content_type != "application/json":
                    raise CheckError("json_response_required")
                value = json.loads(payload)
                if not isinstance(value, dict):
                    raise CheckError("json_object_required")
                return value, response.headers
            if content_type != "text/plain":
                raise CheckError("metrics_text_required")
            return payload.decode("utf-8"), response.headers
    except CheckError:
        raise
    except HTTPError as error:
        # Never include remote reason/body/URL or request headers in output.
        raise CheckError("http_" + str(error.code)) from None
    except (ssl.SSLError, URLError, OSError, ValueError, UnicodeError):
        raise CheckError("verified_https_request_failed") from None


def request(opener, origin, path, timeout, **kwargs):
    # urllib's socket timeout alone permits indefinitely trickled response bytes.
    # A daemon worker adds an absolute deadline; failure ends the CLI immediately
    # without waiting for a stalled resolver/proxy/body reader to unwind.
    result = queue.Queue(maxsize=1)

    def read():
        try:
            result.put((_request(opener, origin, path, timeout, **kwargs), None))
        except CheckError as error:
            result.put((None, error))
        except Exception:
            result.put((None, CheckError("verified_https_request_failed")))

    threading.Thread(target=read, daemon=True).start()
    try:
        value, error = result.get(timeout=timeout)
    except queue.Empty:
        raise CheckError("https_deadline_exceeded") from None
    if error:
        raise error
    return value


def dotted_capability(capabilities, name):
    value = capabilities
    for segment in name.split("."):
        if not isinstance(value, dict) or segment not in value:
            return False
        value = value[segment]
    return value is True


def check_site(origin, *, timeout=10, expect_version=None, required=(), token=None, context=None, resolver=resolve_public, opener=None):
    origin, host = public_origin(origin)
    resolver(host, timeout)
    context = context or ssl.create_default_context()
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise CheckError("certificate_and_hostname_verification_required")
    opener = opener or build_opener(NoRedirects(), HTTPSHandler(context=context))
    checks = {}
    version = None
    for name, path, status in (("health", "/api/health", "ok"), ("database_readiness", "/api/ready", "ready")):
        value, _ = request(opener, origin, path, timeout)
        if value.get("status") != status:
            raise CheckError(name + "_not_ready")
        if name == "health":
            version = value.get("version")
            if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
                raise CheckError("invalid_version_response")
        checks[name] = True
    capabilities, _ = request(opener, origin, "/api/capabilities", timeout)
    if capabilities.get("version") != version or expect_version and version != expect_version:
        raise CheckError("version_mismatch")
    for name in required:
        if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)?", name) or not dotted_capability(capabilities, name):
            raise CheckError("required_capability_unavailable")
    checks["capabilities_and_version"] = True
    if token:
        metrics, headers = request(opener, origin, "/api/operations/metrics", timeout, token=token, json_body=False)
        expected = {"mail_configured", "mail_worker_healthy", "integrations_enabled", "integrations_worker_healthy"}
        found = {match[1] for line in metrics.splitlines() if (match := re.fullmatch(r"freecollab_(mail_configured|mail_worker_healthy|integrations_enabled|integrations_worker_healthy) [01]", line))}
        if found != expected or "no-store" not in headers.get("Cache-Control", "").lower():
            raise CheckError("private_metrics_contract_failed")
        checks["authenticated_read_only_metrics"] = True
    return {"ok": True, "origin": origin, "version": version, "checks": checks,
            "tls": "Client certificate-chain and hostname verification enabled",
            "limits": "No provider, mail, OAuth, content-write or delivery verification"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="Your application's exact public HTTPS origin")
    parser.add_argument("--timeout", type=float, default=10, help="Each request: 1–15 seconds; DNS at most five seconds")
    parser.add_argument("--expect-version")
    parser.add_argument("--require-capability", action="append", default=[], help="Boolean key, optionally integrations.key; repeat as needed")
    parser.add_argument("--metrics-token-file", type=Path, help="Owned mode-0600 regular file; scoped metrics credential only")
    parser.add_argument("--ca-file", type=Path, help="Optional trusted CA bundle; verification remains enabled")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 15:
        parser.error("timeout must be between 1 and 15 seconds")
    try:
        token = private_token(args.metrics_token_file) if args.metrics_token_file else None
        context = ssl.create_default_context(cafile=str(args.ca_file) if args.ca_file else None)
        result = check_site(args.origin, timeout=args.timeout, expect_version=args.expect_version,
                            required=args.require_capability, token=token, context=context)
    except CheckError as error:
        result = {"ok": False, "error": str(error)}  # Fixed helper codes only.
    except (OSError, ValueError):
        # Deliberately no raw OS exception, response, URL query or credential.
        result = {"ok": False, "error": "HTTPS preflight failed; check origin, DNS/TLS, readiness, expected capabilities and private metrics credential."}
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
