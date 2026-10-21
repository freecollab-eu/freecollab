#!/usr/bin/env python3
"""Schedule existing Compose cold-backup/seal CLIs; no new archive or crypto format.

Uploads only the sealed bundle through private operator-configured argv. Retains
plaintext locally; never prunes a backup, source volume or remote object.
"""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
from string import Formatter
import subprocess
import sys
import time

FIELDS = {"version", "repo", "python", "env_file", "project", "backup_root", "key_file", "metrics_file", "max_backup_bytes", "reserve_bytes", "command_timeout_seconds", "upload_argv", "verify_argv"}
PATHS = {"repo", "python", "env_file", "backup_root", "key_file", "metrics_file"}


def load_plan(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "r") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError()
        content = stream.read(65537)
        if len(content) > 65536:
            raise ValueError()
        plan = json.loads(content)
    if not isinstance(plan, dict) or set(plan) != FIELDS or type(plan["version"]) is not int or plan["version"] != 1:
        raise ValueError()
    for name in PATHS:
        if not isinstance(plan[name], str) or not Path(plan[name]).is_absolute() or "\x00" in plan[name]:
            raise ValueError()
    if not isinstance(plan["project"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", plan["project"]):
        raise ValueError()
    for name, lower, upper in (("max_backup_bytes", 1048576, 2**60), ("reserve_bytes", 1073741824, 2**60), ("command_timeout_seconds", 60, 86400)):
        if type(plan[name]) is not int or not lower <= plan[name] <= upper:
            raise ValueError()
    substitutions = dict.fromkeys(("bundle", "bundle_name", "file_list", "sealed_dir"), "fixture")
    for name in ("upload_argv", "verify_argv"):
        argv = plan[name]
        if not isinstance(argv, list) or len(argv) < 2 or not all(isinstance(part, str) and part and "\x00" not in part for part in argv):
            raise ValueError()
        if not Path(argv[0]).is_absolute() or any("REPLACE_" in part for part in argv):
            raise ValueError()
        for part in argv:
            for _, field, spec, conversion in Formatter().parse(part):
                if field is not None and (field not in substitutions or spec or conversion):
                    raise ValueError()
            part.format_map(substitutions)
    return plan


def metrics(path, success, attempt, *, last_success, in_progress):
    path = Path(path)
    values = {"last_success_timestamp_seconds": last_success, "last_attempt_timestamp_seconds": attempt,
              "last_attempt_success": int(success), "in_progress": int(in_progress)}
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(6))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.fchmod(descriptor, 0o644)  # Counters only; readable by node_exporter.
        with os.fdopen(descriptor, "w") as stream:
            stream.write("".join(f"freecollab_pilot_backup_{key} {value}\n" for key, value in values.items()))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def run(plan):
    root = Path(plan["backup_root"])
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError()
    descriptor = os.open(root / "run.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _locked_run(plan, root)


def _locked_run(plan, root):
    stopping = False

    def stop_requested(*_):
        nonlocal stopping
        stopping = True  # Let the existing cold helper restart its source writers.

    attempt = int(time.time())
    state = root / "last-success.json"
    last_success = 0
    if state.exists():
        stored = json.loads(state.read_text())
        if type(stored.get("timestamp")) is not int or not 0 <= stored["timestamp"] <= attempt:
            raise ValueError()
        last_success = stored["timestamp"]
    previous = {number: signal.signal(number, stop_requested) for number in (signal.SIGTERM, signal.SIGINT)}
    stage = "space-check"
    success = False
    try:
        if shutil.disk_usage(root).free < 2 * plan["max_backup_bytes"] + plan["reserve_bytes"]:
            raise ValueError()
        metrics(plan["metrics_file"], False, attempt, last_success=last_success, in_progress=True)
        identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
        work = root / identifier
        work.mkdir(mode=0o700)
        plain, bundle = work / "plaintext", work / (identifier + ".fcsealed")
        file_list = work / "offhost-files.txt"
        file_list.write_text(bundle.name + "\n")
        substitutions = {"bundle": str(bundle), "bundle_name": bundle.name, "file_list": str(file_list), "sealed_dir": str(work)}

        def execute(argv, timeout=None):
            if stopping:
                raise InterruptedError()
            subprocess.run(argv, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)

        stage = "cold-backup"
        # No forced deadline here: killing this helper could bypass its finally
        # block and leave writers stopped. The service unit also does not kill it.
        execute([plan["python"], str(Path(plan["repo"]) / "scripts/pilot-backup.py"), str(plain), "--env-file", plan["env_file"], "--project", plan["project"]])
        if sum(path.stat().st_size for path in plain.iterdir()) > plan["max_backup_bytes"]:
            raise ValueError()
        if shutil.disk_usage(root).free < plan["max_backup_bytes"] + plan["reserve_bytes"]:
            raise ValueError()
        stage = "authenticated-seal"
        execute([plan["python"], str(Path(plan["repo"]) / "scripts/backup-seal.py"), "seal", str(plain), str(bundle), "--key-file", plan["key_file"]], plan["command_timeout_seconds"])
        for name in ("upload_argv", "verify_argv"):
            stage = name.removesuffix("_argv")
            execute([part.format_map(substitutions) for part in plan[name]], plan["command_timeout_seconds"])
        if stopping:
            raise InterruptedError()
        last_success = int(time.time())
        temporary = work / "success.json"
        temporary.write_text(json.dumps({"timestamp": last_success}))
        temporary.replace(state)
        success = True
        print("FreeCollab backup: cold archive, authenticated seal and configured off-host verification completed.")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print("FreeCollab backup failed at " + stage + "; source data and local artifacts were retained.", file=sys.stderr)
        return 1
    finally:
        try:
            metrics(plan["metrics_file"], success, attempt, last_success=last_success, in_progress=False)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)


def main():
    os.umask(0o077)
    try:
        if len(sys.argv) != 2:
            raise ValueError()
        return run(load_plan(sys.argv[1]))
    except (OSError, ValueError, KeyError, TypeError):
        print("FreeCollab backup: invalid private plan, lock, metrics directory or storage permissions.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
