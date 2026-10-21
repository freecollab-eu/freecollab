#!/usr/bin/env python3
"""Restore a cold backup into a new Compose project, refusing existing state."""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile

from pilot_common import RESTORE_VOLUME_SCRIPT, assert_fresh_project, check_backup, compose, docker, project_name, read_env, write_env

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("backup", type=Path)
parser.add_argument("--project", required=True, help="New project, never an existing installation")
parser.add_argument("--env-file", type=Path, required=True, help="New private configuration path")
parser.add_argument("--port", type=int, default=8081)
parser.add_argument("--resume-integrations", action="store_true", help="Resume the restored outbound worker after reviewing destinations and deduplication")
parser.add_argument("--resume-mail", action="store_true", help="Resume restored mail delivery after reviewing retained messages and SMTP destinations")
parser.add_argument("--resume-sfu", action="store_true", help="Resume meeting authorization after reviewing external LiveKit rooms and restored participant leases")
args = parser.parse_args()
project_name(args.project)
if not 1024 <= args.port <= 65535:
    parser.error("Choose a local port between 1024 and 65535")
if args.env_file.exists():
    parser.exit(1, "Refusing to overwrite existing configuration\n")
manifest = check_backup(args.backup)
settings = read_env(args.backup / "pilot.env")
if settings.get("STORAGE_BACKEND", "local").strip().lower() == "s3" or os.environ.get("STORAGE_BACKEND", "").strip().lower() == "s3":
    parser.exit(1, "Automatic S3 cold restore is not supported by this local-volume helper. No target configuration or volumes were created. Restore and verify a consistent external object-storage snapshot with the database and key volumes using the manual process in docs/OBJECT_STORAGE.md.\n")
assert_fresh_project(args.project)
for identifier in manifest["images"].values():
    if not isinstance(identifier, str) or not identifier.startswith("sha256:"):
        parser.exit(1, "Backup image identifier is invalid\n")
    docker("image", "inspect", identifier, stdout=subprocess.DEVNULL)
if "ACCOUNT_SECURITY_KEY" not in settings:
    # Legacy v1 installations had no encrypted authenticator/outbox material.
    # Do not derive this new independent key from signing or content secrets.
    settings["ACCOUNT_SECURITY_KEY"] = base64.b64encode(secrets.token_bytes(32)).decode()
settings.setdefault("MAIL_DELIVERY_MODE", "disabled")
settings["PILOT_PORT"] = str(args.port)
if settings.get("ENVIRONMENT") != "production":
    settings["CORS_ORIGINS"] = json.dumps([f"http://localhost:{args.port}", f"http://127.0.0.1:{args.port}"], separators=(",", ":"))
    settings["PUBLIC_APP_URL"] = f"http://127.0.0.1:{args.port}"
write_env(args.env_file, settings)
profiles = (["secure"] if "matrix" in manifest["images"] else []) + (["mail"] if "mail-worker" in manifest["images"] else []) + (["integrations"] if "integration-worker" in manifest["images"] else []) + (["large-meetings"] if "sfu-worker" in manifest["images"] else [])
volumes = manifest.get("volumes", ["files"])
with tempfile.TemporaryDirectory(prefix="freecollab-restore-") as directory:
    overrides = Path(directory) / "images.json"
    overrides.write_text(json.dumps({"services": {name: {"image": image, "pull_policy": "never"} for name, image in manifest["images"].items()}}))
    for volume in volumes:
        docker("volume", "create", "--label", f"com.docker.compose.project={args.project}", "--label", f"com.docker.compose.volume={volume}", f"{args.project}_{volume}", stdout=subprocess.DEVNULL)
    compose(args.env_file, args.project, "up", "-d", "--wait", "--wait-timeout", "120", "db", extra_file=overrides, profiles=profiles)
    with (args.backup / "database.dump").open("rb") as stream:
        compose(args.env_file, args.project, "exec", "-T", "db", "pg_restore", "--no-owner", "--no-privileges", "--exit-on-error", "-U", settings["POSTGRES_USER"], "-d", settings["POSTGRES_DB"], stdin=stream, extra_file=overrides, profiles=profiles)
    for volume in volumes:
        with (args.backup / (volume + ".tar")).open("rb") as stream:
            docker("run", "--rm", "-i", "--network", "none", "--user", "0:0", "--mount", f"type=volume,src={args.project}_{volume},dst=/data/{volume}", "--entrypoint", "python", manifest["images"]["backend"], "-c", RESTORE_VOLUME_SCRIPT, volume, stdin=stream)
    services = ["db", "backend", "frontend"] + [name for name in ("matrix",) if name in manifest.get("running_services", [])]
    if args.resume_mail and "mail-worker" in manifest.get("running_services", []):
        services.append("mail-worker")
    if args.resume_integrations and "integration-worker" in manifest.get("running_services", []):
        services.append("integration-worker")
    if args.resume_sfu and "sfu-worker" in manifest.get("running_services", []):
        services.append("sfu-worker")
    compose(args.env_file, args.project, "up", "-d", "--no-build", "--wait", "--wait-timeout", "120", *services, extra_file=overrides, profiles=profiles)
print(f"Restored private pilot project {args.project} on local port {args.port}")
print("Run the smoke checks before routing user traffic. Keep the restored key configuration.")
if "integration-worker" in manifest.get("running_services", []) and not args.resume_integrations:
    print("Outbound integration worker remains stopped. Review restored destinations before starting it.")
if "mail-worker" in manifest.get("running_services", []) and not args.resume_mail:
    print("Mail worker remains stopped. Review retained messages and SMTP destinations before starting it.")
if "sfu-worker" in manifest.get("running_services", []) and not args.resume_sfu:
    print("Meeting authorization worker remains stopped. Review restored leases and external LiveKit rooms before starting it.")
