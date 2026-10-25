#!/usr/bin/env python3
"""Exercise built Compose images and a cold restore in disposable projects.

Requires Docker/Compose, Python 3.11+ and the built freecollab-*:pilot images.
Creates only localhost listeners and temporary synthetic data. Run after build:
    python scripts/test-pilot.py --port 18090
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from pilot_common import ROOT, assert_fresh_project, compose


def request(base, method, path, *, token=None, payload=None, body=None, content_type=None):
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    with urlopen(Request(base + "/api" + path, data=body, headers=headers, method=method), timeout=30) as response:
        data = response.read()
        return json.loads(data) if "application/json" in response.headers.get("Content-Type", "") else data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18090)
    args = parser.parse_args()
    if not 1024 <= args.port < 65535:
        parser.error("Choose a base port between 1024 and 65534")
    project = "fc-restore-test-" + uuid4().hex[:10]
    target = project + "-restored"
    created_source = created_target = False
    with tempfile.TemporaryDirectory(prefix="freecollab-ops-test-") as directory:
        directory = Path(directory)
        source_env, target_env = directory / "source.env", directory / "target.env"
        backup = directory / "backup"
        base, restored = f"http://127.0.0.1:{args.port}", f"http://127.0.0.1:{args.port + 1}"
        def helper(name, *arguments, **kwargs):
            return subprocess.run([sys.executable, str(ROOT / "scripts" / name), *map(str, arguments)], check=True, **kwargs)
        try:
            helper("pilot-init.py", "--output", source_env, "--port", args.port)
            assert_fresh_project(project)
            created_source = True
            compose(source_env, project, "up", "-d", "--no-build", "--wait", "--wait-timeout", "120")
            assert request(base, "GET", "/ready")["status"] == "ready"
            email = f"restore-{uuid4().hex}@example.com"
            credentials = {"email": email, "password": "Restore-test-password1!"}
            user = request(base, "POST", "/auth/register", payload={"name": "Restore test", **credentials})
            token = user["access_token"]
            channel = request(base, "POST", "/channels", token=token, payload={"name": "Durable test", "member_ids": []})["id"]
            request(base, "POST", f"/messages/channels/{channel}", token=token, payload={"content": "Encrypted content after restore"})
            content = b"original file bytes survive cold restore"
            boundary = "fixture" + uuid4().hex
            multipart = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="fixture.txt"\r\nContent-Type: text/plain\r\n\r\n'.encode() + content + f"\r\n--{boundary}--\r\n".encode())
            file_id = request(base, "POST", f"/files/channels/{channel}", token=token, body=multipart, content_type="multipart/form-data; boundary=" + boundary)["id"]
            helper("pilot-backup.py", backup, "--env-file", source_env, "--project", project)
            manifest = json.loads((backup / "manifest.json").read_text())
            assert manifest["images"]["ai-runtime-worker"] == manifest["images"]["backend"]
            assert "ai-runtime-worker" in manifest["running_services"]
            source_running = compose(source_env, project, "ps", "--status", "running", "--services", profiles=["*"], capture_output=True, text=True).stdout.split()
            assert set(source_running) & set(manifest["images"]) == set(manifest["running_services"])
            created_target = True
            helper("pilot-restore.py", backup, "--env-file", target_env, "--project", target, "--port", args.port + 1)
            target_running = compose(target_env, target, "ps", "--status", "running", "--services", profiles=["*"], capture_output=True, text=True).stdout.split()
            assert "ai-runtime-worker" not in target_running
            restored_token = request(restored, "POST", "/auth/login", payload=credentials)["access_token"]
            history = request(restored, "GET", f"/messages/channels/{channel}", token=restored_token)
            assert history[0]["content"] == "Encrypted content after restore"
            assert request(restored, "GET", f"/files/{file_id}", token=restored_token) == content
            assert request(base, "GET", f"/messages/channels/{channel}", token=token)[0]["content"] == history[0]["content"]
            # The target project's state must never be silently overwritten.
            duplicate = subprocess.run([sys.executable, str(ROOT / "scripts/pilot-restore.py"), str(backup), "--env-file", str(directory / "another.env"), "--project", target, "--port", str(args.port + 1)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            assert duplicate.returncode != 0
            corrupt = directory / "corrupt"
            shutil.copytree(backup, corrupt)
            with (corrupt / "files.tar").open("ab") as stream:
                stream.write(b"corrupted")
            damaged = subprocess.run([sys.executable, str(ROOT / "scripts/pilot-restore.py"), str(corrupt), "--env-file", str(directory / "corrupt.env"), "--project", target + "-bad", "--port", str(args.port + 1)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            assert damaged.returncode != 0
            print("PASS: PostgreSQL/Nginx startup, encrypted history + file restore, source preservation, overwrite and corruption guards")
        finally:
            if created_target and target_env.exists():
                compose(target_env, target, "down", "--volumes", "--remove-orphans", stdout=subprocess.DEVNULL)
            if created_source and source_env.exists():
                compose(source_env, project, "down", "--volumes", "--remove-orphans", stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
