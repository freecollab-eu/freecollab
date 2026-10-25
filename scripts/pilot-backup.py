#!/usr/bin/env python3
"""Cold backup of a local Compose pilot. Stops writers briefly; retains keys."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess

from pilot_common import ARCHIVE_VOLUME_SCRIPT, BASE_SERVICES, MATRIX_BACKUP_CHECK_SCRIPT, OPTIONAL_SERVICES, ROOT, VOLUME_NAMES, check_backup, compose, docker, file_hash, project_name, read_env

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("output", type=Path, help="New private backup directory")
parser.add_argument("--env-file", type=Path, default=ROOT / "pilot.env")
parser.add_argument("--project", default="freecollab")
args = parser.parse_args()
project_name(args.project)
settings = read_env(args.env_file)
if settings.get("STORAGE_BACKEND", "local").strip().lower() == "s3" or os.environ.get("STORAGE_BACKEND", "").strip().lower() == "s3":
    parser.exit(1, "S3 cold backup is not supported by this local-volume helper. No writers were stopped and no backup was created. Create and verify a consistent database/key-volume plus external object-storage snapshot using your provider's backup process; see docs/OBJECT_STORAGE.md.\n")
if settings.get("MATRIX_DATABASE_HOST"):
    parser.exit(1, "This backup supports the local Matrix SQLite pilot; snapshot an external Matrix database separately\n")
args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
running = compose(args.env_file, args.project, "ps", "--status", "running", "--services", profiles=["*"], capture_output=True, text=True).stdout.split()
if "db" not in running:
    parser.exit(1, "Database must be running before a backup\n")
writers = [name for name in ("frontend", "backend", "mail-worker", "integration-worker", "ai-runtime-worker", "sfu-worker", "matrix") if name in running]
images = {}
for service in sorted(BASE_SERVICES | OPTIONAL_SERVICES):
    container_id = compose(args.env_file, args.project, "ps", "-aq", service, profiles=["*"], capture_output=True, text=True).stdout.strip()
    if not container_id:
        if service in BASE_SERVICES:
            parser.exit(1, f"Missing service container: {service}\n")
        continue
    images[service] = docker("inspect", "--format", "{{.Image}}", container_id, capture_output=True, text=True).stdout.strip()
if "ai-runtime-worker" in images and images["ai-runtime-worker"] != images["backend"]:
    parser.exit(1, "AI runtime worker must use the same backend image before taking a cold backup. No writers were stopped.\n")
volumes = []
for volume in sorted(VOLUME_NAMES):
    existing = docker("volume", "ls", "-q", "--filter", f"name=^{args.project}_{volume}$", capture_output=True, text=True).stdout.strip()
    if existing:
        volumes.append(volume)
if "files" not in volumes:
    parser.exit(1, "File volume is missing\n")
if ("matrix" in volumes) != ("matrix" in images):
    parser.exit(1, "Matrix snapshot requires its durable volume and exact service container image\n")
try:
    if writers:
        compose(args.env_file, args.project, "stop", "--timeout", "30", *writers, profiles=["*"], stdout=subprocess.DEVNULL)
    if "matrix" in volumes:
        # This offline check needs only pinned PyYAML, already in the smaller
        # backend image. Keep the exact Synapse image in the restore manifest.
        docker("run", "--rm", "--network", "none", "--user", "0:0", "--mount", f"type=volume,src={args.project}_matrix,dst=/snapshot,readonly", "--entrypoint", "python", images["backend"], "-c", MATRIX_BACKUP_CHECK_SCRIPT)
    for name in ("database.dump", *(volume + ".tar" for volume in volumes)):
        descriptor = os.open(args.output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            if name == "database.dump":
                compose(args.env_file, args.project, "exec", "-T", "db", "pg_dump", "--format=custom", "--no-owner", "--no-privileges", "-U", settings["POSTGRES_USER"], settings["POSTGRES_DB"], stdout=stream)
            else:
                volume = name.removesuffix(".tar")
                docker("run", "--rm", "--network", "none", "--user", "0:0", "--mount", f"type=volume,src={args.project}_{volume},dst=/snapshot,readonly", "--entrypoint", "python", images["backend"], "-c", ARCHIVE_VOLUME_SCRIPT, volume, stdout=stream)
    shutil.copyfile(args.env_file, args.output / "pilot.env")
    os.chmod(args.output / "pilot.env", 0o600)
    manifest = {
        "format": "freecollab-cold-backup-v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_project": args.project,
        "images": images,
        "volumes": volumes,
        "running_services": sorted(set(running) & set(images)),
        "sha256": {name: file_hash(args.output / name) for name in ("database.dump", "pilot.env", *(volume + ".tar" for volume in volumes))},
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    os.chmod(args.output / "manifest.json", 0o600)
    check_backup(args.output)
finally:
    if writers:
        compose(args.env_file, args.project, "start", *writers, profiles=["*"], stdout=subprocess.DEVNULL)
print(f"Backup complete: {args.output}. Contains private keys; protect it as credentials.")
