"""Authenticated offsite bundles fail closed before a usable backup is published."""
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys

import pytest
from nacl import bindings

from tests.test_pilot_backups import fixture_backup, helpers


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = spec_from_file_location("backup_seal_under_test", SCRIPTS / "backup-seal.py")
seal = module_from_spec(SPEC)
SPEC.loader.exec_module(seal)


@pytest.fixture
def bundle(tmp_path):
    backup = tmp_path / "cold-backup"
    manifest = fixture_backup(backup)
    # More than two frames, with zero bytes and binary content preserved exactly.
    (backup / "database.dump").write_bytes(b"private database\x00" * 150000)
    manifest["sha256"]["database.dump"] = helpers.file_hash(backup / "database.dump")
    (backup / "manifest.json").write_text(json.dumps(manifest))
    key = tmp_path / "independent-backup.key"
    seal.generate_key(key)
    encrypted = tmp_path / "cold-backup.fcsealed"
    seal.seal_backup(backup, encrypted, key)
    return backup, key, encrypted


def test_multiframe_round_trip_retains_all_artifacts_and_private_modes(bundle, tmp_path):
    backup, key, encrypted = bundle
    destination = tmp_path / "authenticated-backup"
    seal.open_backup(encrypted, destination, key)
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert stat.S_IMODE(encrypted.stat().st_mode) == 0o600
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    assert helpers.check_backup(destination) == helpers.check_backup(backup)
    for original in backup.iterdir():
        opened = destination / original.name
        assert opened.read_bytes() == original.read_bytes()
        assert stat.S_IMODE(opened.stat().st_mode) == 0o600
    assert b"private database" not in encrypted.read_bytes()
    assert b"POSTGRES_PASSWORD" not in encrypted.read_bytes()
    assert not list(tmp_path.glob(".freecollab-*"))


@pytest.mark.parametrize("attack", ["wrong-key", "magic", "header", "ciphertext", "truncated", "missing-final", "trailing", "oversize-frame"])
def test_unauthenticated_or_incomplete_bundles_never_publish_plaintext(bundle, tmp_path, attack):
    backup, key, encrypted = bundle
    content = bytearray(encrypted.read_bytes())
    header_end = len(seal.MAGIC) + seal.HEADER_BYTES
    selected_key = key
    if attack == "wrong-key":
        selected_key = tmp_path / "wrong.key"
        seal.generate_key(selected_key)
    elif attack == "magic":
        content[0] ^= 1
    elif attack == "header":
        content[len(seal.MAGIC)] ^= 1
    elif attack == "ciphertext":
        content[header_end + 7] ^= 1
    elif attack == "truncated":
        del content[-1]
    elif attack == "missing-final":
        del content[-(4 + seal.OVERHEAD_BYTES):]
    elif attack == "trailing":
        content.extend(b"extra payload")
    else:
        content = content[:header_end] + struct.pack("!I", 0xffffffff)
    candidate = tmp_path / "candidate.fcsealed"
    candidate.write_bytes(content)
    destination = tmp_path / "must-not-exist"
    with pytest.raises((ValueError, OSError)):
        seal.open_backup(candidate, destination, selected_key)
    assert not destination.exists()
    assert not list(tmp_path.glob(".freecollab-*"))
    assert helpers.check_backup(backup)


def authenticated_candidate(path, key_file, payloads):
    """A key-holder can construct frames; extraction must still enforce schema."""
    state = bindings.crypto_secretstream_xchacha20poly1305_state()
    header = bindings.crypto_secretstream_xchacha20poly1305_init_push(state, seal.read_key(key_file))
    with path.open("wb") as stream:
        stream.write(seal.MAGIC + header)
        for payload in payloads:
            seal._push(stream, state, payload)
        seal._push(stream, state, b"", final=True)


@pytest.mark.parametrize("payloads", [
    [b'M{"name":"../outside","size":0}'],
    [b'M{"name":"/absolute","size":0}'],
    [b'M{"name":"untracked-file","size":0}'],
    [b'M{"name":"manifest.json","size":0}', b'M{"name":"manifest.json","size":0}'],
    [b'M{"name":"manifest.json","size":true}'],
    [b'M{"name":"manifest.json","size":4}', b'Da'],
    [b'Dno file metadata'],
])
def test_authenticated_malformed_artifacts_cannot_escape_or_publish(tmp_path, payloads):
    key = tmp_path / "key"
    seal.generate_key(key)
    encrypted = tmp_path / "bad.fcsealed"
    authenticated_candidate(encrypted, key, payloads)
    with pytest.raises((ValueError, OSError)):
        seal.open_backup(encrypted, tmp_path / "opened", key)
    assert not (tmp_path / "opened").exists()
    assert not (tmp_path / "outside").exists()
    assert not list(tmp_path.glob(".freecollab-*"))


@pytest.mark.parametrize("malformed", ["inner-path", "inner-link", "wrong-artifact-hash"])
def test_authenticated_bundles_still_validate_inner_backup_integrity(tmp_path, malformed):
    backup = tmp_path / "backup"
    import tarfile
    fixture_backup(backup, malicious="../escape" if malformed == "inner-path" else None,
                   member_type=tarfile.SYMTYPE if malformed == "inner-link" else None)
    if malformed == "wrong-artifact-hash":
        (backup / "database.dump").write_bytes(b"changed after manifest creation")
    key = tmp_path / "key"
    seal.generate_key(key)
    encrypted = tmp_path / "bad.fcsealed"
    payloads = []
    for artifact in backup.iterdir():
        data = artifact.read_bytes()
        payloads.append(b"M" + json.dumps({"name": artifact.name, "size": len(data)}).encode())
        if data:
            payloads.append(b"D" + data)
    authenticated_candidate(encrypted, key, payloads)
    with pytest.raises(ValueError):
        seal.open_backup(encrypted, tmp_path / "opened", key)
    assert not (tmp_path / "opened").exists()
    assert not list(tmp_path.glob(".freecollab-*"))


def test_existing_outputs_and_keys_are_never_overwritten(bundle, tmp_path):
    backup, key, encrypted = bundle
    saved_key, saved_encrypted = key.read_bytes(), encrypted.read_bytes()
    with pytest.raises(FileExistsError):
        seal.generate_key(key)
    with pytest.raises(FileExistsError):
        seal.seal_backup(backup, encrypted, key)
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        seal.open_backup(encrypted, existing, key)
    assert key.read_bytes() == saved_key
    assert encrypted.read_bytes() == saved_encrypted
    assert list(existing.iterdir()) == []


def test_atomic_publish_refuses_a_directory_created_during_decryption(bundle, tmp_path, monkeypatch):
    _, key, encrypted = bundle
    destination = tmp_path / "racing-destination"
    publish = seal._publish_directory
    def racing(stage_fd, parent_fd, target_name):
        os.mkdir(target_name, dir_fd=parent_fd)
        publish(stage_fd, parent_fd, target_name)
    monkeypatch.setattr(seal, "_publish_directory", racing)
    with pytest.raises(FileExistsError):
        seal.open_backup(encrypted, destination, key)
    assert destination.is_dir() and not list(destination.iterdir())
    assert not list(tmp_path.glob(".freecollab-*"))


def replace_staging_path(stage_fd):
    """Model another UID with rename access to a nonsticky shared parent."""
    original = Path(os.readlink(f"/proc/self/fd/{stage_fd}"))
    moved = original.with_name(original.name + "-moved")
    original.rename(moved)
    original.mkdir(mode=0o700)
    return original, moved


def test_directory_publication_uses_validated_inode_after_shared_parent_swap(bundle, tmp_path, monkeypatch):
    backup, key, encrypted = bundle
    tmp_path.chmod(0o777)
    destination = tmp_path / "opened"
    publish = seal._publish_directory
    swapped = []
    def replacing(stage_fd, parent_fd, target_name):
        replacement, moved = replace_staging_path(stage_fd)
        fake = replacement / "backup"
        fake.mkdir()
        (fake / "unvalidated").write_bytes(b"must not be published")
        swapped.extend([replacement, moved])
        publish(stage_fd, parent_fd, target_name)
    monkeypatch.setattr(seal, "_publish_directory", replacing)
    seal.open_backup(encrypted, destination, key)
    assert helpers.check_backup(destination) == helpers.check_backup(backup)
    assert not (destination / "unvalidated").exists()
    assert (swapped[0] / "backup/unvalidated").read_bytes() == b"must not be published"
    assert list(swapped[1].iterdir()) == []


def test_file_publication_uses_ciphertext_inode_after_shared_parent_swap(bundle, tmp_path, monkeypatch):
    backup, key, _ = bundle
    tmp_path.chmod(0o777)
    output = tmp_path / "anchored.fcsealed"
    link = seal.os.link
    swapped = []
    def replacing(source, destination, **kwargs):
        replacement, moved = replace_staging_path(kwargs["src_dir_fd"])
        (replacement / source).write_bytes(b"unvalidated replacement")
        swapped.extend([replacement, moved])
        return link(source, destination, **kwargs)
    monkeypatch.setattr(seal.os, "link", replacing)
    seal.seal_backup(backup, output, key)
    monkeypatch.setattr(seal.os, "link", link)
    opened = tmp_path / "opened"
    seal.open_backup(output, opened, key)
    assert helpers.check_backup(opened) == helpers.check_backup(backup)
    assert (swapped[0] / "sealed").read_bytes() == b"unvalidated replacement"
    assert list(swapped[1].iterdir()) == []


@pytest.mark.parametrize("target", ["key", "source"])
def test_fifo_key_or_source_is_rejected_without_blocking(bundle, tmp_path, target):
    _, key, encrypted = bundle
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    program = """import runpy,sys
sys.path.insert(0, sys.argv[1])
module=runpy.run_path(sys.argv[1]+'/backup-seal.py')
try:
    if sys.argv[2]=='key': module['read_key'](sys.argv[3])
    else: module['open_backup'](sys.argv[3],sys.argv[4],sys.argv[5])
except (ValueError,OSError): sys.exit(0)
sys.exit(2)
"""
    completed = subprocess.run([sys.executable, "-c", program, str(SCRIPTS), target,
                                str(fifo), str(tmp_path / "opened"), str(key)],
                               capture_output=True, text=True, timeout=3)
    assert completed.returncode == 0
    assert not (tmp_path / "opened").exists()
    assert not list(tmp_path.glob(".freecollab-*"))


def test_cleanup_failure_still_closes_both_directory_descriptors(tmp_path, monkeypatch):
    captured = []
    def denied_stat(*args, **kwargs):
        raise PermissionError("Injected cleanup permission failure")
    with monkeypatch.context() as patch:
        with pytest.raises(PermissionError):
            with seal._staging(tmp_path) as descriptors:
                captured.extend(descriptors)
                patch.setattr(seal.os, "stat", denied_stat)
    for descriptor in captured:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_parent_inspection_failure_closes_its_descriptor(tmp_path, monkeypatch):
    captured = []
    original_open = os.open
    def recording_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        captured.append(descriptor)
        return descriptor
    def denied_fstat(*args, **kwargs):
        raise PermissionError("Injected parent inspection failure")
    with monkeypatch.context() as patch:
        patch.setattr(seal.os, "open", recording_open)
        patch.setattr(seal.os, "fstat", denied_fstat)
        with pytest.raises(PermissionError):
            with seal._staging(tmp_path):
                pytest.fail("Invalid parent was accepted")
    assert len(captured) == 1
    with pytest.raises(OSError):
        os.fstat(captured[0])


@pytest.mark.parametrize("invalid", ["public", "wrong-size", "symlink"])
def test_key_files_require_private_regular_exact_length(tmp_path, invalid):
    key = tmp_path / "key"
    seal.generate_key(key)
    if invalid == "public":
        key.chmod(0o644)
    elif invalid == "wrong-size":
        key.write_bytes(b"too short")
    else:
        link = tmp_path / "linked-key"
        link.symlink_to(key)
        key = link
    with pytest.raises((ValueError, OSError)):
        seal.read_key(key)


def test_real_cli_round_trip_and_failure_do_not_print_key_or_plaintext(tmp_path):
    backup = tmp_path / "source"
    fixture_backup(backup)
    key = tmp_path / "key"
    encrypted = tmp_path / "sealed"
    opened = tmp_path / "opened"
    def invoke(*arguments):
        return subprocess.run([sys.executable, str(SCRIPTS / "backup-seal.py"), *map(str, arguments)],
                              capture_output=True, text=True, timeout=15)
    for arguments in [("keygen", key), ("seal", backup, encrypted, "--key-file", key),
                      ("open", encrypted, opened, "--key-file", key)]:
        result = invoke(*arguments)
        assert result.returncode == 0, result.stderr
        assert "POSTGRES_PASSWORD" not in result.stdout + result.stderr
        assert key.read_bytes().hex() not in result.stdout + result.stderr
    result = invoke("open", encrypted, opened, "--key-file", key)
    assert result.returncode == 1 and "operation failed" in result.stderr
    assert "Traceback" not in result.stderr
    assert helpers.check_backup(opened)
