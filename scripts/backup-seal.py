#!/usr/bin/env python3
"""Seal validated cold backups using an independent private SecretStream key.

The plaintext source backup is retained. Opening publishes a new backup directory
only after the complete stream and every existing backup integrity check pass.
"""
import argparse
from contextlib import contextmanager
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import struct

from nacl import bindings
from nacl.exceptions import CryptoError

from pilot_common import check_backup, file_hash


MAGIC = b"FREECOLLAB-BACKUP-SECRETSTREAM-1\n"
CHUNK_BYTES = 1024 * 1024
ALLOWED_FILES = {"manifest.json", "database.dump", "pilot.env", "files.tar", "mail.tar", "matrix.tar"}
KEY_BYTES = bindings.crypto_secretstream_xchacha20poly1305_KEYBYTES
HEADER_BYTES = bindings.crypto_secretstream_xchacha20poly1305_HEADERBYTES
OVERHEAD_BYTES = bindings.crypto_secretstream_xchacha20poly1305_ABYTES
FINAL = bindings.crypto_secretstream_xchacha20poly1305_TAG_FINAL


def _regular_file(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK)
    stream = os.fdopen(descriptor, "rb")
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        stream.close()
        raise ValueError("Expected a regular file")
    return stream


def read_key(path):
    with _regular_file(path) as stream:
        info = os.fstat(stream.fileno())
        if info.st_mode & 0o077:
            raise ValueError("Key file must be private (mode 0600 or stricter)")
        key = stream.read(KEY_BYTES + 1)
    if len(key) != KEY_BYTES:
        raise ValueError("Key file must contain exactly 32 binary bytes")
    return key


def generate_key(path):
    path = Path(path)
    with _staging(path.parent) as (parent_fd, stage_fd):
        descriptor = os.open("key", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=stage_fd)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(secrets.token_bytes(KEY_BYTES))
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(stage_fd)
        os.link("key", path.name, src_dir_fd=stage_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
        os.fsync(parent_fd)


def _artifact_names(directory, manifest):
    names = set(manifest["sha256"]) | {"manifest.json"}
    if not names <= ALLOWED_FILES or {path.name for path in directory.iterdir()} != names:
        raise ValueError("Backup contains unexpected artifacts")
    return ["manifest.json", *sorted(names - {"manifest.json"})]


def _push(stream, state, payload, *, final=False):
    ciphertext = bindings.crypto_secretstream_xchacha20poly1305_push(
        state, payload, ad=MAGIC, tag=FINAL if final else 0)
    stream.write(struct.pack("!I", len(ciphertext)))
    stream.write(ciphertext)


@contextmanager
def _staging(parent):
    """Anchor staging and publication even if a shared parent renames entries."""
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    stage_fd = None
    stage_owned = False
    try:
        parent_info = os.fstat(parent_fd)
        # Only unlink a staging pathname when another untrusted UID cannot swap
        # it between the inode check and rmdir. Shared parents can retain an
        # empty directory.
        reclaim_name = not (parent_info.st_mode & 0o022) or bool(
            parent_info.st_mode & stat.S_ISVTX and parent_info.st_uid in {0, os.geteuid()})
        name = ".freecollab-backup-" + secrets.token_hex(16)
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        stage_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        info = os.fstat(stage_fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("Backup staging directory was replaced")
        stage_owned = True
        yield parent_fd, stage_fd
    finally:
        try:
            if stage_fd is not None:
                try:
                    if stage_owned:
                        # Clean the original inode, never an attacker replacement.
                        for entry in os.scandir(stage_fd):
                            if entry.is_dir(follow_symlinks=False):
                                shutil.rmtree(entry.name, dir_fd=stage_fd)
                            else:
                                os.unlink(entry.name, dir_fd=stage_fd)
                        try:
                            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                        except FileNotFoundError:
                            current = None
                        if reclaim_name and current and (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino):
                            os.rmdir(name, dir_fd=parent_fd)
                finally:
                    os.close(stage_fd)
        finally:
            os.close(parent_fd)


def seal_backup(directory, output, key_file):
    directory, output = Path(directory), Path(output)
    if os.path.lexists(output):
        raise FileExistsError("Refusing to overwrite sealed backup")
    key = read_key(key_file)
    manifest = check_backup(directory)
    names = _artifact_names(directory, manifest)
    hashes = {**manifest["sha256"], "manifest.json": file_hash(directory / "manifest.json")}
    # A private sibling permits an atomic no-overwrite hard-link publication.
    with _staging(output.parent) as (parent_fd, stage_fd):
        descriptor = os.open("sealed", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=stage_fd)
        with os.fdopen(descriptor, "wb") as stream:
            state = bindings.crypto_secretstream_xchacha20poly1305_state()
            header = bindings.crypto_secretstream_xchacha20poly1305_init_push(state, key)
            stream.write(MAGIC + header)
            for name in names:
                with _regular_file(directory / name) as source:
                    size = os.fstat(source.fileno()).st_size
                    _push(stream, state, b"M" + json.dumps({"name": name, "size": size}, separators=(",", ":")).encode())
                    remaining = size
                    digest = hashlib.sha256()
                    while remaining:
                        content = source.read(min(CHUNK_BYTES, remaining))
                        if not content:
                            raise ValueError("Backup artifact changed during sealing")
                        _push(stream, state, b"D" + content)
                        digest.update(content)
                        remaining -= len(content)
                    if source.read(1):
                        raise ValueError("Backup artifact changed during sealing")
                    if digest.hexdigest() != hashes[name]:
                        raise ValueError("Backup artifact changed during sealing")
            _push(stream, state, b"", final=True)
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck the snapshot; a changed live source must not be published.
        if check_backup(directory) != manifest:
            raise ValueError("Backup manifest changed during sealing")
        os.fsync(stage_fd)
        os.link("sealed", output.name, src_dir_fd=stage_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
        os.fsync(parent_fd)


def _read_exact(stream, count):
    content = bytearray()
    while len(content) < count:
        chunk = stream.read(count - len(content))
        if not chunk:
            raise ValueError("Sealed backup is truncated")
        content.extend(chunk)
    return bytes(content)


def _publish_directory(stage_fd, parent_fd, destination_name):
    """Linux atomic RENAME_NOREPLACE: never replace even an empty directory."""
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if rename is None:
        raise OSError(errno.ENOTSUP, "Atomic directory publication requires Linux renameat2")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(stage_fd, b"backup", parent_fd, os.fsencode(destination_name), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination_name))


def open_backup(source, destination, key_file):
    source, destination = Path(source), Path(destination)
    if os.path.lexists(destination):
        raise FileExistsError("Refusing to overwrite an existing backup directory")
    key = read_key(key_file)
    with _staging(destination.parent) as (parent_fd, stage_fd):
        os.mkdir("backup", mode=0o700, dir_fd=stage_fd)
        backup_fd = os.open("backup", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=stage_fd)
        unpacked = Path("/proc/self/fd") / str(backup_fd)
        target, remaining, seen = None, 0, set()
        try:
            with _regular_file(source) as stream:
                if _read_exact(stream, len(MAGIC)) != MAGIC:
                    raise ValueError("Unsupported sealed backup format")
                header = _read_exact(stream, HEADER_BYTES)
                state = bindings.crypto_secretstream_xchacha20poly1305_state()
                bindings.crypto_secretstream_xchacha20poly1305_init_pull(state, header, key)
                while True:
                    size = struct.unpack("!I", _read_exact(stream, 4))[0]
                    if not OVERHEAD_BYTES <= size <= CHUNK_BYTES + 1 + OVERHEAD_BYTES:
                        raise ValueError("Invalid sealed backup frame size")
                    ciphertext = _read_exact(stream, size)
                    try:
                        payload, tag = bindings.crypto_secretstream_xchacha20poly1305_pull(state, ciphertext, ad=MAGIC)
                    except CryptoError:
                        raise ValueError("Sealed backup authentication failed") from None
                    if tag == FINAL:
                        if payload or target is not None or stream.read(1):
                            raise ValueError("Sealed backup is incomplete or has trailing data")
                        break
                    if tag != 0:
                        raise ValueError("Unsupported sealed backup frame tag")
                    if payload.startswith(b"M"):
                        if target is not None or len(payload) > 1024:
                            raise ValueError("Invalid sealed backup artifact metadata")
                        try:
                            metadata = json.loads(payload[1:])
                        except (ValueError, UnicodeDecodeError):
                            raise ValueError("Invalid sealed backup artifact metadata") from None
                        if not isinstance(metadata, dict) or set(metadata) != {"name", "size"}:
                            raise ValueError("Invalid sealed backup artifact metadata")
                        name, remaining = metadata["name"], metadata["size"]
                        if not isinstance(name, str) or name not in ALLOWED_FILES or name in seen:
                            raise ValueError("Unsafe or duplicate sealed backup artifact")
                        if type(remaining) is not int or remaining < 0:
                            raise ValueError("Invalid sealed backup artifact size")
                        seen.add(name)
                        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=backup_fd)
                        target = os.fdopen(descriptor, "wb")
                        if remaining == 0:
                            target.close()
                            target = None
                    elif payload.startswith(b"D"):
                        content = payload[1:]
                        if target is None or not content or len(content) > remaining:
                            raise ValueError("Invalid sealed backup artifact content")
                        target.write(content)
                        remaining -= len(content)
                        if remaining == 0:
                            target.flush()
                            os.fsync(target.fileno())
                            target.close()
                            target = None
                    else:
                        raise ValueError("Invalid sealed backup artifact frame")
            manifest = check_backup(unpacked)
            if seen != set(_artifact_names(unpacked, manifest)):
                raise ValueError("Sealed backup has unexpected artifacts")
            os.fsync(backup_fd)
            os.fsync(stage_fd)
            _publish_directory(stage_fd, parent_fd, destination.name)
            os.fsync(parent_fd)
        finally:
            if target is not None:
                target.close()
            os.close(backup_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="operation", required=True)
    keygen = subcommands.add_parser("keygen", help="Generate a new independent private binary key file")
    keygen.add_argument("key_file", type=Path)
    for command in ("seal", "open"):
        action = subcommands.add_parser(command, help="Seal a cold backup" if command == "seal" else "Authenticate and open into a new directory")
        action.add_argument("source", type=Path)
        action.add_argument("destination", type=Path)
        action.add_argument("--key-file", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.operation == "keygen":
            generate_key(arguments.key_file)
        elif arguments.operation == "seal":
            seal_backup(arguments.source, arguments.destination, arguments.key_file)
        else:
            open_backup(arguments.source, arguments.destination, arguments.key_file)
    except (OSError, ValueError, CryptoError, KeyError, TypeError):
        # No raw crypto, filesystem or parsed configuration values in diagnostics.
        parser.exit(1, "Backup operation failed; check the key, artifact integrity, permissions and unused destination.\n")
    print("Backup operation complete. Retain the independent key separately; plaintext sources were preserved.")


if __name__ == "__main__":
    main()
