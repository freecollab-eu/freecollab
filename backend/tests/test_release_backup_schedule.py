"""Scheduled orchestration proof using existing real sealing CLI and local copy.

The cold snapshot fixture creates a valid synthetic Compose archive without
Docker. A separate local directory stands in for a transfer destination; these
tests establish no real off-host account/retention or production backup claim.
"""
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = spec_from_file_location("scheduled_backup", ROOT / "deploy/pilot/backup.py")
schedule = module_from_spec(SPEC); SPEC.loader.exec_module(schedule)


def plan_fixture(tmp_path, *, corrupt_copy=False, delay=False):
    repo = tmp_path / "repo"; (repo / "scripts").mkdir(parents=True)
    source = tmp_path / "source.env"
    source.write_text("POSTGRES_USER=fixture\nPOSTGRES_DB=fixture\nPOSTGRES_PASSWORD=private-test-only\nJWT_SECRET=private-test-only\nMESSAGE_ENCRYPTION_KEYS=private-test-only\n")
    source.chmod(0o600)
    started, restarted = tmp_path / "archive-started", tmp_path / "writers-restarted"
    cold = repo / "scripts/pilot-backup.py"
    cold.write_text('''from pathlib import Path
import hashlib,io,json,sys,tarfile,time
p=Path(sys.argv[1]);p.mkdir(mode=0o700)
Path(''' + repr(str(started)) + ''').touch()
try:
 time.sleep(''' + (".8" if delay else "0") + ''')
 (p/'database.dump').write_bytes(b'private native backup fixture')
 (p/'pilot.env').write_bytes(Path(''' + repr(str(source)) + ''').read_bytes())
 with tarfile.open(p/'files.tar','w')as archive:
  entry=tarfile.TarInfo('files/original.txt');entry.size=14;archive.addfile(entry,io.BytesIO(b'original bytes'))
 names=['database.dump','pilot.env','files.tar']
 manifest={'format':'freecollab-cold-backup-v2','images':{name:'sha256:'+'a'*64 for name in ['backend','frontend','db']},'volumes':['files'],'running_services':['db','backend','frontend'],'sha256':{name:hashlib.sha256((p/name).read_bytes()).hexdigest()for name in names}}
 (p/'manifest.json').write_text(json.dumps(manifest))
finally:Path(''' + repr(str(restarted)) + ''').touch()
''')
    for name in ("backup-seal.py", "pilot_common.py"):
        (repo / "scripts" / name).symlink_to(ROOT / "scripts" / name)
    tool = tmp_path / "transfer.py"
    tool.write_text("from pathlib import Path\nimport sys\na,b=map(Path,sys.argv[2:4])\nif sys.argv[1]=='copy':\n b.parent.mkdir(exist_ok=True);b.write_bytes(a.read_bytes()" + ("+b'tampered'" if corrupt_copy else "") + ")\nelse:raise SystemExit(0 if a.read_bytes()==b.read_bytes()else 1)\n")
    key = tmp_path / "independent.key"; key.write_bytes(secrets.token_bytes(32)); key.chmod(0o600)
    remote = tmp_path / "separate-local-destination"
    plan = {"version": 1, "repo": str(repo), "python": sys.executable, "env_file": str(source), "project": "fixture",
            "backup_root": str(tmp_path / "backups"), "key_file": str(key), "metrics_file": str(tmp_path / "backup.prom"),
            "max_backup_bytes": 1024 * 1024, "reserve_bytes": 1024**3, "command_timeout_seconds": 60,
            "upload_argv": [sys.executable, str(tool), "copy", "{bundle}", str(remote / "{bundle_name}")],
            "verify_argv": [sys.executable, str(tool), "verify", "{bundle}", str(remote / "{bundle_name}")]}
    path = tmp_path / "plan.json"; path.write_text(json.dumps(plan)); path.chmod(0o600)
    return path, plan, remote, started, restarted


def test_real_seal_and_independent_copy_verification_advance_success(tmp_path):
    path, plan, remote, _, restarted = plan_fixture(tmp_path)
    assert schedule.run(schedule.load_plan(path)) == 0
    root = Path(plan["backup_root"])
    assert restarted.exists() and json.loads((root / "last-success.json").read_text())["timestamp"] > 0
    bundle = next(root.glob("*/*.fcsealed"))
    assert bundle.read_bytes() == (remote / bundle.name).read_bytes()
    assert b"private-test-only" not in bundle.read_bytes() and b"private native backup fixture" not in bundle.read_bytes()
    opened = tmp_path / "opened"
    subprocess.run([sys.executable, str(ROOT / "scripts/backup-seal.py"), "open", str(bundle), str(opened), "--key-file", plan["key_file"]], check=True, capture_output=True)
    assert (opened / "database.dump").read_bytes() == b"private native backup fixture"
    assert (bundle.parent / "plaintext/pilot.env").exists()  # Sealing is not erasure.
    assert "freecollab_pilot_backup_last_attempt_success 1" in Path(plan["metrics_file"]).read_text()
    assert Path(plan["metrics_file"]).stat().st_mode & 0o777 == 0o644


def test_failed_byte_verifier_retains_artifacts_and_does_not_claim_success(tmp_path, capsys):
    path, plan, _, _, restarted = plan_fixture(tmp_path, corrupt_copy=True)
    assert schedule.run(schedule.load_plan(path)) == 1
    assert restarted.exists() and not (Path(plan["backup_root"]) / "last-success.json").exists()
    assert len(list(Path(plan["backup_root"]).glob("*/*.fcsealed"))) == 1
    metrics = Path(plan["metrics_file"]).read_text()
    assert "last_attempt_success 0" in metrics and "last_success_timestamp_seconds 0" in metrics and "in_progress 0" in metrics
    output = capsys.readouterr()
    assert "failed at verify" in output.err and "private-test-only" not in output.err


def test_wrapper_defers_sigterm_until_cold_helper_restarts_writers(tmp_path):
    path, plan, remote, started, restarted = plan_fixture(tmp_path, delay=True)
    process = subprocess.Popen([sys.executable, str(ROOT / "deploy/pilot/backup.py"), str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline:
            assert process.poll() is None; time.sleep(.02)
        assert started.exists()
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=10)
        assert process.returncode == 1 and restarted.exists() and not remote.exists()
        assert not (Path(plan["backup_root"]) / "last-success.json").exists()
    finally:
        if process.poll() is None: process.terminate(); process.wait(timeout=10)


def test_plan_rejects_unsafe_permissions_unknown_fields_and_shell_placeholders(tmp_path):
    path, plan, *_ = plan_fixture(tmp_path)
    path.chmod(0o644)
    with pytest.raises(ValueError): schedule.load_plan(path)
    path.chmod(0o600)
    plan["unknown"] = "rejected"; path.write_text(json.dumps(plan))
    with pytest.raises(ValueError): schedule.load_plan(path)
    del plan["unknown"]; plan["upload_argv"].append("{account_session}"); path.write_text(json.dumps(plan))
    with pytest.raises(ValueError): schedule.load_plan(path)
    plan["upload_argv"][-1] = "{bundle.__class__}"; path.write_text(json.dumps(plan))
    with pytest.raises(ValueError): schedule.load_plan(path)
    link = tmp_path / "linked-plan"; link.symlink_to(path)
    with pytest.raises(OSError): schedule.load_plan(link)
