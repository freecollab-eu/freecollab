"""Shared operator helpers. Never print a resolved Compose config or secrets."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
BASE_SERVICES = {"frontend", "backend", "db"}
OPTIONAL_SERVICES = {"mail-worker", "matrix", "integration-worker", "ai-runtime-worker", "sfu-worker"}
VOLUME_NAMES = {"files", "mail", "matrix"}

# The snapshot container has no network and mounts one volume read-only. Root
# is necessary to read both application uid 10001 and private Synapse uid 991.
ARCHIVE_VOLUME_SCRIPT = """import sys, tarfile
archive = tarfile.open(fileobj=sys.stdout.buffer, mode='w|')
archive.add('/snapshot', arcname=sys.argv[1])
archive.close()
"""
RESTORE_VOLUME_SCRIPT = """import sys, tarfile
from pathlib import PurePosixPath
root = sys.argv[1]
def checked(member, destination):
    name = PurePosixPath(member.name)
    if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] != root or not (member.isfile() or member.isdir()):
        raise ValueError('Unsafe volume archive member')
    safe = tarfile.data_filter(member, destination)
    if safe is not None:
        # Restore numeric owners for Synapse/app private files, retaining the
        # path/link checks while removing executable privilege bits.
        safe.uid, safe.gid = member.uid, member.gid
        safe.uname = safe.gname = None
        safe.mode = member.mode & 0o777
    return safe
archive = tarfile.open(fileobj=sys.stdin.buffer, mode='r|')
archive.extractall('/data', filter=checked, numeric_owner=True)
"""
MATRIX_BACKUP_CHECK_SCRIPT = """import sys, yaml
from pathlib import Path, PurePosixPath
config = yaml.safe_load(Path('/snapshot/homeserver.yaml').read_text())
database = config.get('database', {})
if database.get('name') != 'sqlite3':
    raise SystemExit('Local Matrix backup requires its SQLite database inside the durable volume')
paths = [database.get('args', {}).get('database', ''), config.get('signing_key_path', ''), config.get('media_store_path', '/data/media_store')]
for value in paths:
    path = PurePosixPath(value)
    if not path.is_absolute() or '..' in path.parts or len(path.parts) < 3 or path.parts[1] != 'data':
        raise SystemExit('Matrix database, signing key and media must be inside its durable /data volume')
"""


def project_name(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", value):
        raise ValueError("Project must be 1–41 lowercase letters, digits, underscores or hyphens")
    return value


def read_env(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        result[key] = value
    for required in ("POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PASSWORD", "JWT_SECRET", "MESSAGE_ENCRYPTION_KEYS"):
        if not result.get(required):
            raise ValueError(f"Configuration is missing {required}")
    return result


def write_env(path, values):
    # Values generated here never contain quotes/newlines or dollar expansion.
    if any(not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or any(character in value for character in "'\r\n") for key, value in values.items()):
        raise ValueError("Configuration keys/values cannot contain quote or newline characters")
    content = "".join(f"{key}='{value}'\n" for key, value in values.items())
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)


def docker(*args, **kwargs):
    environment = os.environ.copy()
    for name in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        environment.pop(name, None)
    return subprocess.run(["docker", "--host=unix:///var/run/docker.sock", *args], check=True, env=environment, **kwargs)


def compose(env_file, project, *args, extra_file=None, profiles=(), **kwargs):
    command = ["compose", "--project-name", project_name(project), "--env-file", str(Path(env_file).resolve()), "-f", str(ROOT / "docker-compose.yml")]
    for profile in profiles:
        command += ["--profile", profile]
    if extra_file:
        command += ["-f", str(extra_file)]
    return docker(*command, *args, **kwargs)


def assert_fresh_project(project):
    project_name(project)
    for kind, args in (
        ("container", ("ps", "-aq")),
        ("network", ("network", "ls", "-q")),
        ("volume", ("volume", "ls", "-q")),
    ):
        found = docker(*args, "--filter", f"label=com.docker.compose.project={project}", capture_output=True, text=True).stdout.strip()
        if found:
            raise ValueError(f"Refusing to use existing project {kind} state")
    # Also protect manually created volumes whose names collide with Compose.
    for suffix in ("postgres", *sorted(VOLUME_NAMES)):
        found = docker("volume", "ls", "-q", "--filter", f"name=^{project}_{suffix}$", capture_output=True, text=True).stdout.strip()
        if found:
            raise ValueError("Refusing to use an existing target volume")


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_backup(directory):
    # Keep an operator-provided directory-fd path anchored. Resolving a
    # /proc/self/fd directory back to its pathname would reintroduce rename races.
    directory = Path(directory)
    manifest_file = directory / "manifest.json"
    if manifest_file.is_symlink() or not manifest_file.is_file() or manifest_file.stat().st_size > 1024 * 1024:
        raise ValueError("Backup manifest is invalid")
    manifest = json.loads(manifest_file.read_text())
    version = manifest.get("format")
    if version not in ("freecollab-cold-backup-v1", "freecollab-cold-backup-v2"):
        raise ValueError("Unsupported backup format")
    volumes = ["files"] if version.endswith("v1") else manifest.get("volumes")
    if not isinstance(volumes, list) or any(not isinstance(name, str) for name in volumes) or len(set(volumes)) != len(volumes) or "files" not in volumes or not set(volumes) <= VOLUME_NAMES:
        raise ValueError("Backup volume manifest is invalid")
    expected_files = {"database.dump", "pilot.env", *(name + ".tar" for name in volumes)}
    if set(manifest.get("sha256", {})) != expected_files:
        raise ValueError("Backup manifest has unexpected files")
    images = manifest.get("images", {})
    if not isinstance(images, dict) or not BASE_SERVICES <= set(images) or not set(images) <= BASE_SERVICES | OPTIONAL_SERVICES:
        raise ValueError("Backup is missing exact service image identifiers")
    if version.endswith("v1") and set(images) != BASE_SERVICES:
        raise ValueError("Legacy backup has unexpected service images")
    if "matrix" in volumes and "matrix" not in images:
        raise ValueError("Matrix backup is missing its exact server image")
    if any(not isinstance(identifier, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", identifier) for identifier in images.values()):
        raise ValueError("Backup image identifier is invalid")
    if "ai-runtime-worker" in images and images["ai-runtime-worker"] != images["backend"]:
        raise ValueError("AI runtime worker must use the backed-up backend image")
    if version.endswith("v2"):
        running = manifest.get("running_services")
        if not isinstance(running, list) or any(not isinstance(name, str) for name in running) or len(set(running)) != len(running) or not set(running) <= set(images) or "db" not in running:
            raise ValueError("Backup running-service manifest is invalid")
        if "matrix" in images and "matrix" not in volumes:
            raise ValueError("Matrix backup is missing its durable volume")
    for name, expected in manifest["sha256"].items():
        path = directory / name
        if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected) or path.is_symlink() or not path.is_file() or file_hash(path) != expected:
            raise ValueError(f"Backup integrity check failed: {name}")
    for volume in volumes:
        with tarfile.open(directory / (volume + ".tar"), "r:") as archive:
            for member in archive:
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts or not name.parts or name.parts[0] != volume:
                    raise ValueError("Volume archive contains an unsafe path")
                if not (member.isfile() or member.isdir()):
                    raise ValueError("Volume archive contains a link or special file")
    read_env(directory / "pilot.env")
    return manifest
