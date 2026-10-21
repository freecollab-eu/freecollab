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
