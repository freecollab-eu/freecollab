"""Validate recovery manifests before any Docker volume or DB is mutated."""
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
import json
from pathlib import Path
import runpy
import sys
import tarfile
from types import ModuleType, SimpleNamespace

import pytest

SPEC = spec_from_file_location("pilot_backup_helpers", Path(__file__).resolve().parents[2] / "scripts" / "pilot_common.py")
helpers = module_from_spec(SPEC)
SPEC.loader.exec_module(helpers)


def fixture_backup(directory, *, version=2, volumes=("files", "mail", "matrix"), malicious=None, member_type=None):
    directory.mkdir()
    (directory / "database.dump").write_bytes(b"synthetic postgres dump")
    (directory / "pilot.env").write_text("POSTGRES_USER=fixture\nPOSTGRES_DB=fixture\nPOSTGRES_PASSWORD=fixture\nJWT_SECRET=fixture\nMESSAGE_ENCRYPTION_KEYS=fixture\n")
    if version == 1:
        volumes = ("files",)
    for volume in volumes:
        with tarfile.open(directory / (volume + ".tar"), "w") as archive:
            root = tarfile.TarInfo(volume)
            root.type = tarfile.DIRTYPE
            root.mode = 0o700
            archive.addfile(root)
            item = tarfile.TarInfo(malicious if malicious and volume == volumes[-1] else volume + "/private-file")
            item.uid = item.gid = 991 if volume == "matrix" else 10001
            item.mode = 0o600
            if member_type:
                item.type = member_type
                item.linkname = "/etc/passwd"
                archive.addfile(item)
            else:
                item.size = len(b"private bytes")
                archive.addfile(item, BytesIO(b"private bytes"))
    images = {service: "sha256:" + "a" * 64 for service in ("backend", "frontend", "db")}
    if "matrix" in volumes:
        images["matrix"] = "sha256:" + "b" * 64
    names = ("database.dump", "pilot.env", *(volume + ".tar" for volume in volumes))
    manifest = {"format": "freecollab-cold-backup-v" + str(version), "images": images,
                "sha256": {name: helpers.file_hash(directory / name) for name in names}}
    if version == 2:
        manifest.update(volumes=list(volumes), running_services=list(images))
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return manifest


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_and_complete_matrix_backup_formats_are_readable(tmp_path, version):
    path = tmp_path / "backup"
    manifest = fixture_backup(path, version=version)
    assert helpers.check_backup(path) == manifest


@pytest.mark.parametrize("path", ["../outside", "/absolute", "matrix/../../outside", "other/file"])
def test_every_volume_archive_rejects_path_escape(tmp_path, path):
    backup = tmp_path / "backup"
    fixture_backup(backup, malicious=path)
    with pytest.raises(ValueError, match="unsafe path"):
        helpers.check_backup(backup)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE])
def test_volume_archives_reject_links_and_special_files(tmp_path, kind):
    backup = tmp_path / "backup"
    fixture_backup(backup, member_type=kind)
    with pytest.raises(ValueError, match="link or special"):
        helpers.check_backup(backup)


@pytest.mark.parametrize("filename", ["database.dump", "pilot.env", "files.tar", "mail.tar", "matrix.tar"])
def test_all_durable_artifacts_have_integrity_checks(tmp_path, filename):
    backup = tmp_path / "backup"
    fixture_backup(backup)
    with (backup / filename).open("ab") as stream:
        stream.write(b"corruption")
    with pytest.raises(ValueError, match="integrity"):
        helpers.check_backup(backup)


def test_matrix_volume_cannot_be_restored_without_pinned_server_image(tmp_path):
    backup = tmp_path / "backup"
    manifest = fixture_backup(backup)
    del manifest["images"]["matrix"]
    (backup / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="exact server image"):
        helpers.check_backup(backup)


def test_matrix_server_image_cannot_be_restored_without_its_data(tmp_path):
    backup = tmp_path / "backup"
    manifest = fixture_backup(backup, volumes=("files", "mail"))
    manifest["images"]["matrix"] = "sha256:" + "c" * 64
    (backup / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="durable volume"):
        helpers.check_backup(backup)


def test_manifest_cannot_select_arbitrary_services_or_archive_names(tmp_path):
    backup = tmp_path / "backup"
    manifest = fixture_backup(backup)
    manifest["sha256"]["../secret"] = "0" * 64
    (backup / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unexpected files"):
        helpers.check_backup(backup)
    manifest["sha256"].pop("../secret")
    manifest["running_services"].append("untrusted-service")
    (backup / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="running-service"):
        helpers.check_backup(backup)


@pytest.mark.parametrize("volume", ["files", "mail", "matrix", "postgres"])
def test_restore_refuses_manually_named_existing_optional_volumes(monkeypatch, volume):
    def docker(*arguments, **_):
        match = f"name=^fresh_{volume}$"
        return SimpleNamespace(stdout="existing" if match in arguments else "")
    monkeypatch.setattr(helpers, "docker", docker)
    with pytest.raises(ValueError, match="existing target volume"):
        helpers.assert_fresh_project("fresh")


@pytest.mark.parametrize("resume_mail,resume_integrations,was_running", [
    (False, False, True), (True, False, True), (False, True, True),
    (True, True, True), (True, True, False),
])
def test_restore_quarantines_outbound_workers_until_explicit_opt_in(
        tmp_path, monkeypatch, capsys, resume_mail, resume_integrations, was_running):
    """Execute the real CLI orchestration without touching Docker or SMTP."""
    backup = tmp_path / "backup"
    manifest = fixture_backup(backup)
    for name in ("mail-worker", "integration-worker"):
        manifest["images"][name] = "sha256:" + "d" * 64
        if was_running:
            manifest["running_services"].append(name)
    calls = []
    private_config = {}
    module = ModuleType("pilot_common")
    module.RESTORE_VOLUME_SCRIPT = "fixture-only archive extraction"
    module.assert_fresh_project = lambda project: None
    module.check_backup = lambda path: manifest
    module.project_name = lambda value: value
    module.read_env = lambda path: {
        "POSTGRES_USER": "fixture", "POSTGRES_DB": "fixture",
        "ACCOUNT_SECURITY_KEY": "retained-private-key", "MAIL_DELIVERY_MODE": "smtp",
        "ENVIRONMENT": "production", "SMTP_HOST": "customer-mail.example",
    }
    module.write_env = lambda path, values: private_config.update(values)
    module.compose = lambda env, project, *arguments, **kwargs: calls.append(arguments)
    module.docker = lambda *arguments, **kwargs: None
    monkeypatch.setitem(sys.modules, "pilot_common", module)
    command = ["pilot-restore.py", str(backup), "--project", "fresh-review",
               "--env-file", str(tmp_path / "restored.env")]
    if resume_mail:
        command.append("--resume-mail")
    if resume_integrations:
        command.append("--resume-integrations")
    monkeypatch.setattr(sys, "argv", command)
    runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/pilot-restore.py"), run_name="__main__")
    startup = next(call for call in calls if "--no-build" in call)
    assert ("mail-worker" in startup) == (was_running and resume_mail)
    assert ("integration-worker" in startup) == (was_running and resume_integrations)
    assert "matrix" in startup
    assert private_config["ACCOUNT_SECURITY_KEY"] == "retained-private-key"
    assert private_config["MAIL_DELIVERY_MODE"] == "smtp"
    output = capsys.readouterr().out
    assert ("Mail worker remains stopped" in output) == (was_running and not resume_mail)
    assert ("Outbound integration worker remains stopped" in output) == (was_running and not resume_integrations)


@pytest.mark.parametrize("host_override", [False, True])
def test_s3_backup_refuses_before_output_docker_or_writer_stop(tmp_path, monkeypatch, capsys, host_override):
    config = tmp_path / "source.env"
    config.write_text("POSTGRES_USER=fixture\nPOSTGRES_DB=fixture\nPOSTGRES_PASSWORD=private-fixture-password\nJWT_SECRET=private-fixture-signing\nMESSAGE_ENCRYPTION_KEYS=private-fixture-key\n"
                      + ("" if host_override else "STORAGE_BACKEND=s3\n"))
    output = tmp_path / "must-not-exist"
    module = ModuleType("pilot_common")
    module.__dict__.update({name: value for name, value in vars(helpers).items() if not name.startswith("__")})
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args); raise AssertionError("S3 refusal must happen before Docker or Compose")
    module.compose = module.docker = forbidden
    monkeypatch.setitem(sys.modules, "pilot_common", module)
    if host_override: monkeypatch.setenv("STORAGE_BACKEND", "s3")
    else: monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.setattr(sys, "argv", ["pilot-backup.py", str(output), "--env-file", str(config)])
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/pilot-backup.py"), run_name="__main__")
    assert error.value.code == 1 and not output.exists() and calls == []
    message = capsys.readouterr().err
    assert "S3 cold backup is not supported" in message and "private-fixture" not in message


@pytest.mark.parametrize("version,host_override", [(1, False), (2, False), (2, True)])
def test_s3_restore_refuses_old_archives_before_docker_or_target_mutations(tmp_path, monkeypatch, capsys, version, host_override):
    backup = tmp_path / "backup"
    manifest = fixture_backup(backup, version=version)
    if not host_override:
        with (backup / "pilot.env").open("a") as output: output.write("STORAGE_BACKEND=s3\n")
        manifest["sha256"]["pilot.env"] = helpers.file_hash(backup / "pilot.env")
        (backup / "manifest.json").write_text(json.dumps(manifest))
    target = tmp_path / "must-not-exist.env"
    calls = []
    module = ModuleType("pilot_common")
    module.__dict__.update({name: value for name, value in vars(helpers).items() if not name.startswith("__")})
    def forbidden(*args, **kwargs):
        calls.append(args); raise AssertionError("S3 refusal must happen before any target operation")
    module.assert_fresh_project = module.compose = module.docker = module.write_env = forbidden
    monkeypatch.setitem(sys.modules, "pilot_common", module)
    if host_override: monkeypatch.setenv("STORAGE_BACKEND", "s3")
    else: monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.setattr(sys, "argv", ["pilot-restore.py", str(backup), "--project", "fresh-s3-review", "--env-file", str(target)])
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/pilot-restore.py"), run_name="__main__")
    assert error.value.code == 1 and not target.exists() and calls == []
    assert "Automatic S3 cold restore is not supported" in capsys.readouterr().err


def test_ai_manifest_preserves_legacy_format_and_requires_the_backend_image(tmp_path):
    backup = tmp_path / "backup"
    manifest = fixture_backup(backup)
    manifest["images"]["ai-runtime-worker"] = manifest["images"]["backend"]
    manifest["running_services"].append("ai-runtime-worker")
    (backup / "manifest.json").write_text(json.dumps(manifest))
    assert helpers.check_backup(backup) == manifest
    manifest["images"]["ai-runtime-worker"] = "sha256:" + "c" * 64
    (backup / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="backed-up backend image"):
        helpers.check_backup(backup)


@pytest.mark.parametrize("resume_ai,present,was_running", [
    (False, True, True), (True, True, True), (True, True, False),
    (False, True, False), (True, False, False), (False, False, False),
])
def test_real_restore_orchestration_quarantines_ai_and_pins_its_backend_image(
        tmp_path, monkeypatch, capsys, resume_ai, present, was_running):
    backup = tmp_path / "backup"
    manifest = fixture_backup(backup)
    with (backup / "pilot.env").open("a") as stream:
        stream.write("COPILOT_ENABLED=true\n")
    manifest["sha256"]["pilot.env"] = helpers.file_hash(backup / "pilot.env")
    if present:
        manifest["images"]["ai-runtime-worker"] = manifest["images"]["backend"]
    if was_running:
        manifest["running_services"].append("ai-runtime-worker")
    (backup / "manifest.json").write_text(json.dumps(manifest))
    calls, image_overrides, private_config = [], [], {}
    module = ModuleType("pilot_common")
    module.__dict__.update({key: value for key, value in vars(helpers).items() if not key.startswith("__")})
    module.assert_fresh_project = lambda _: None
    module.write_env = lambda path, values: private_config.update(values)
    module.docker = lambda *args, **kwargs: None
    def compose(env, project, *args, **kwargs):
        calls.append((args, kwargs))
        image_overrides.append(json.loads(Path(kwargs["extra_file"]).read_text()))
    module.compose = compose
    monkeypatch.setitem(sys.modules, "pilot_common", module)
    command = ["pilot-restore.py", str(backup), "--project", "owned-ai-restore", "--env-file", str(tmp_path / "restored.env")]
    if resume_ai:
        command.append("--resume-ai")
    monkeypatch.setattr(sys, "argv", command)
    runpy.run_path(str(helpers.ROOT / "scripts/pilot-restore.py"), run_name="__main__")
    startup, options = next(call for call in calls if "--no-build" in call[0])
    assert ("ai-runtime-worker" in startup) == (resume_ai and was_running)
    assert "mail-worker" not in startup and "integration-worker" not in startup
    assert ("integrations" in options["profiles"]) == present
    assert private_config["COPILOT_ENABLED"] == "true"  # Quarantine execution, retain configuration.
    if present:
        assert all(item["services"]["ai-runtime-worker"]["image"] == item["services"]["backend"]["image"] for item in image_overrides)
    else:
        assert all("ai-runtime-worker" not in item["services"] for item in image_overrides)
    output = capsys.readouterr().out
    assert ("AI worker remains stopped" in output) == (was_running and not resume_ai)
    assert ("AI worker was not running" in output) == (resume_ai and not was_running)


def test_restore_rejects_mismatched_ai_image_before_any_target_mutation(tmp_path, monkeypatch):
    backup = tmp_path / "backup"
    manifest = fixture_backup(backup)
    manifest["images"]["ai-runtime-worker"] = "sha256:" + "c" * 64
    (backup / "manifest.json").write_text(json.dumps(manifest))
    calls = []
    module = ModuleType("pilot_common")
    module.__dict__.update({key: value for key, value in vars(helpers).items() if not key.startswith("__")})
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Invalid AI image must fail before target mutation")
    module.assert_fresh_project = module.compose = module.docker = module.write_env = forbidden
    monkeypatch.setitem(sys.modules, "pilot_common", module)
    monkeypatch.setattr(sys, "argv", ["pilot-restore.py", str(backup), "--project", "owned-ai-restore", "--env-file", str(tmp_path / "never.env"), "--resume-ai"])
    with pytest.raises(ValueError, match="backed-up backend image"):
        runpy.run_path(str(helpers.ROOT / "scripts/pilot-restore.py"), run_name="__main__")
    assert calls == [] and not (tmp_path / "never.env").exists()


def cold_backup_scenario(tmp_path, monkeypatch, *, was_running, fail_dump=False, wrong_ai_image=False):
    source = tmp_path / "source.env"
    source.write_text("POSTGRES_USER=fixture\nPOSTGRES_DB=fixture\nPOSTGRES_PASSWORD=fixture\nJWT_SECRET=fixture\nMESSAGE_ENCRYPTION_KEYS=fixture\n")
    source.chmod(0o600)
    output = tmp_path / "cold"
    images = {name: "sha256:" + "a" * 64 for name in ("backend", "frontend", "db", "ai-runtime-worker", "mail-worker")}
    if wrong_ai_image:
        images["ai-runtime-worker"] = "sha256:" + "c" * 64
    running = ["db", "backend", "frontend"] + (["ai-runtime-worker"] if was_running else [])
    calls = []
    module = ModuleType("pilot_common")
    module.__dict__.update({key: value for key, value in vars(helpers).items() if not key.startswith("__")})
    def compose(env, project, *args, **kwargs):
        calls.append(("compose", args))
        if args[0] == "ps":
            if "--status" in args:
                return SimpleNamespace(stdout="\n".join(running))
            return SimpleNamespace(stdout="owned-" + args[-1] if args[-1] in images else "")
        if args[0] in {"stop", "start"}:
            return SimpleNamespace(stdout="")
        if args[0] == "exec":
            if fail_dump:
                raise RuntimeError("Owned dump fixture failure")
            kwargs["stdout"].write(b"owned synthetic database dump")
            return SimpleNamespace(stdout="")
        raise AssertionError("Unexpected Compose fixture command")
    def docker(*args, **kwargs):
        calls.append(("docker", args))
        if args[0] == "inspect":
            return SimpleNamespace(stdout=images[args[-1].removeprefix("owned-")])
        if args[:2] == ("volume", "ls"):
            return SimpleNamespace(stdout="owned-files" if args[-1].endswith("_files$") else "")
        if args[0] == "run":
            with tarfile.open(fileobj=kwargs["stdout"], mode="w") as archive:
                info = tarfile.TarInfo("files/owned.txt"); info.size = 5
                archive.addfile(info, BytesIO(b"owned"))
            return SimpleNamespace(stdout="")
        raise AssertionError("Unexpected Docker fixture command")
    module.compose, module.docker = compose, docker
    monkeypatch.setitem(sys.modules, "pilot_common", module)
    monkeypatch.setattr(sys, "argv", ["pilot-backup.py", str(output), "--env-file", str(source), "--project", "owned-ai-backup"])
    return output, calls, lambda: runpy.run_path(str(helpers.ROOT / "scripts/pilot-backup.py"), run_name="__main__")


@pytest.mark.parametrize("was_running", [False, True])
@pytest.mark.parametrize("fail_dump", [False, True])
def test_cold_backup_fences_and_restarts_only_previously_running_ai_even_after_failure(tmp_path, monkeypatch, was_running, fail_dump):
    output, calls, run = cold_backup_scenario(tmp_path, monkeypatch, was_running=was_running, fail_dump=fail_dump)
    if fail_dump:
        with pytest.raises(RuntimeError, match="Owned dump"):
            run()
    else:
        run()
        manifest = helpers.check_backup(output)
        assert manifest["images"]["ai-runtime-worker"] == manifest["images"]["backend"]
        assert ("ai-runtime-worker" in manifest["running_services"]) == was_running
    stops = [args for kind, args in calls if kind == "compose" and args[0] == "stop"]
    starts = [args for kind, args in calls if kind == "compose" and args[0] == "start"]
    assert len(stops) == len(starts) == 1
    assert ("ai-runtime-worker" in stops[0]) == was_running
    assert ("ai-runtime-worker" in starts[0]) == was_running
    assert "db" not in stops[0] and "db" not in starts[0]
    assert "mail-worker" not in stops[0] and "mail-worker" not in starts[0]
    positions = {name: next(index for index, (kind, args) in enumerate(calls) if kind == "compose" and args[0] == name) for name in ("stop", "exec", "start")}
    assert positions["stop"] < positions["exec"] < positions["start"]


def test_cold_backup_refuses_ai_image_mismatch_before_stopping_any_writer(tmp_path, monkeypatch, capsys):
    output, calls, run = cold_backup_scenario(tmp_path, monkeypatch, was_running=True, wrong_ai_image=True)
    with pytest.raises(SystemExit) as error:
        run()
    assert error.value.code == 1
    assert not any(kind == "compose" and args[0] in {"stop", "start", "exec"} for kind, args in calls)
    assert not (output / "manifest.json").exists()
    assert "No writers were stopped" in capsys.readouterr().err
