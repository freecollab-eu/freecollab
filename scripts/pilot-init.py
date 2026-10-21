#!/usr/bin/env python3
"""Generate private local-pilot configuration without printing credentials."""
import argparse
import base64
import json
import secrets
from pathlib import Path

from pilot_common import ROOT, write_env

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=ROOT / "pilot.env")
parser.add_argument("--port", type=int, default=8080)
parser.add_argument("--secure", action="store_true", help="Enable client E2EE; start Compose with --profile secure")
args = parser.parse_args()
if not 1024 <= args.port <= 65535:
    parser.error("Choose a local port between 1024 and 65535")
values = {
    "ENVIRONMENT": "development",
    "PILOT_PORT": str(args.port),
    "POSTGRES_USER": "freecollab",
    "POSTGRES_DB": "freecollab",
    "POSTGRES_PASSWORD": secrets.token_hex(32),
    "JWT_SECRET": secrets.token_hex(32),
    "ACCOUNT_SECURITY_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
    "PUBLIC_APP_URL": f"http://127.0.0.1:{args.port}",
    "MAIL_DELIVERY_MODE": "file",
    "MAIL_FILE_PATH": "/data/mail",
    "MATRIX_ENABLED": "true" if args.secure else "false",
    "MATRIX_SERVER_NAME": "freecollab.local",
    "MATRIX_REGISTRATION_SHARED_SECRET": secrets.token_hex(48),
    "MESSAGE_ENCRYPTION_KEYS": json.dumps({"pilot-1": base64.b64encode(secrets.token_bytes(32)).decode()}, separators=(",", ":")),
    "MESSAGE_ENCRYPTION_ACTIVE_KEY_ID": "pilot-1",
    "CORS_ORIGINS": json.dumps([f"http://localhost:{args.port}", f"http://127.0.0.1:{args.port}"], separators=(",", ":")),
}
try:
    write_env(args.output, values)
except FileExistsError:
    parser.exit(1, "Refusing to overwrite existing configuration\n")
print(f"Created private local-pilot configuration: {args.output}")
if args.secure:
    print("Start Compose with --profile secure. Keep the Matrix server name and volume stable.")
print("Keep it with your backups. Local HTTP must not be exposed as production.")
