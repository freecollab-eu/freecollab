#!/usr/bin/env python3
"""One Railway API plus existing queue workers on the same durable volume.

No additional scheduler or queue implementation. Any child exit stops the whole
service, allowing Railway's normal restart policy to recover a coherent runtime.
"""
import ipaddress
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

UID = GID = 10001


def prepare_storage(root=Path("/data")):
    """Initialize only known directory inodes; never walk existing user data."""
    if os.geteuid() != 0:
        return
    for path in (root, root / "files", root / "mail", root / "security"):
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, UID):
                raise ValueError("Review existing volume ownership before migration")
            if info.st_uid == 0:
                os.fchown(descriptor, UID, GID)
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)
    os.setgroups([])
    os.setgid(GID)
    os.setuid(UID)


def stop_children(children, timeout=30):
    for child in children:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + timeout
    while any(child.poll() is None for child in children) and time.monotonic() < deadline:
        time.sleep(.05)
    for child in children:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        child.wait()


def supervise(api_command, worker_commands, *, ready_url, startup_timeout=90, stop_timeout=30):
    children = []
    stopping = False

    def stop_requested(*_):
        nonlocal stopping
        stopping = True

    previous = {number: signal.signal(number, stop_requested) for number in (signal.SIGTERM, signal.SIGINT)}
    opener = build_opener(ProxyHandler({}))  # Only a fixed loopback readiness URL.
    try:
        children.append(subprocess.Popen(api_command, start_new_session=True))
        deadline = time.monotonic() + startup_timeout
        ready = False
        while not stopping and time.monotonic() < deadline:
            if children[0].poll() is not None:
                raise RuntimeError("API exited before readiness")
            try:
                with opener.open(ready_url, timeout=.5) as response:
                    ready = response.status == 200 and response.read(128).strip().replace(b" ", b"") == b'{"status":"ready"}'
            except (URLError, OSError):
                pass
            if ready:
                break
            time.sleep(.1)
        if stopping:
            return 0
        if not ready:
            raise RuntimeError("API readiness deadline exceeded")
        for command in worker_commands:
            children.append(subprocess.Popen(command, start_new_session=True))
        print(f"FreeCollab Railway: API ready; {len(worker_commands)} queue workers started.", flush=True)
        while not stopping:
            if any(child.poll() is not None for child in children):
                raise RuntimeError("A runtime child exited")
            time.sleep(.2)
        return 0
    except (OSError, RuntimeError):
        print("FreeCollab Railway: runtime unavailable; stopping all children for service restart.", file=sys.stderr, flush=True)
        return 1
    finally:
        stop_children(children, stop_timeout)
        for number, handler in previous.items():
            signal.signal(number, handler)


def main():
    os.umask(0o077)
    try:
        port = int(os.getenv("PORT", "8000"))
        if not 1024 <= port <= 65535:
            raise ValueError()
        # Trust only explicit private proxy addresses/networks, never all peers.
        trusted = os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1,::1")
        for entry in trusted.split(","):
            network = ipaddress.ip_network(entry.strip(), strict=False)
            if network.prefixlen == 0:
                raise ValueError()
        prepare_storage()
    except (OSError, ValueError):
        print("FreeCollab Railway: invalid port/proxy trust or volume ownership; review private service settings.", file=sys.stderr)
        return 1
    api = [sys.executable, "-m", "uvicorn", "app.main:socket_app", "--host", "::", "--port", str(port),
           "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", trusted, "--no-access-log"]
    workers = [[sys.executable, "-m", "app.services.integration_worker"],
               [sys.executable, "-m", "app.services.mail"],
               [sys.executable, "-m", "app.services.ai_runtime_worker"]]
    # Railway private networking uses IPv6. Uvicorn's single-process asyncio
    # listener on :: is IPv6-only, so its own readiness probe must use ::1 too.
    return supervise(api, workers, ready_url=f"http://[::1]:{port}/ready")


if __name__ == "__main__":
    raise SystemExit(main())
