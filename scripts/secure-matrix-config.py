#!/usr/bin/env python3
"""Initialize/update private Synapse configuration inside its official container.

Run with its /data volume mounted. Secrets are read from environment or *_FILE;
no secrets are printed. The application backend must be the only client gateway.
"""
import os
import json
import re
from pathlib import Path
import subprocess
import sys
import yaml


def secret(name):
    filename = os.environ.get(name + "_FILE")
    value = Path(filename).read_text().strip() if filename else os.environ.get(name, "")
    if not value:
        raise SystemExit(name + " or " + name + "_FILE is required")
    return value

path = Path(sys.argv[1] if len(sys.argv) > 1 else "/data/homeserver.yaml")
server_name = os.environ.get("MATRIX_SERVER_NAME", os.environ.get("SYNAPSE_SERVER_NAME", ""))
if not server_name or any(ch in server_name for ch in "/ \r\n"):
    raise SystemExit("A stable MATRIX_SERVER_NAME is required")
registration_secret = secret("MATRIX_REGISTRATION_SHARED_SECRET")
try:
    federation_domains = json.loads(os.environ.get("MATRIX_FEDERATION_DOMAINS", "[]"))
    if (not isinstance(federation_domains, list) or len(federation_domains) > 20
            or any(not isinstance(domain, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,251}[a-z0-9]", domain)
                   for domain in federation_domains)):
        raise ValueError()
except (ValueError, TypeError):
    raise SystemExit("MATRIX_FEDERATION_DOMAINS must be a JSON array of explicit DNS server names") from None
if not path.exists():
    env = {**os.environ, "SYNAPSE_SERVER_NAME": server_name, "SYNAPSE_REPORT_STATS": "no"}
    subprocess.run(["/start.py", "generate"], env=env, check=True, stdout=subprocess.DEVNULL)
config = yaml.safe_load(path.read_text())
if config.get("server_name") != server_name:
    raise SystemExit("Refusing to change the identity of an existing homeserver")
config.update({
    "registration_shared_secret": registration_secret,
    "enable_registration": False,
    "enable_registration_without_verification": False,
    "report_stats": False,
    "trusted_key_servers": [],
    "federation_domain_whitelist": federation_domains,
    "allow_public_rooms_without_auth": False,
    "allow_public_rooms_over_federation": False,
    "enable_room_list_search": False,
    "max_upload_size": "50M",
    # Public native auth is rate limited separately; all private Matrix login
    # requests originate from the authenticated gateway address.
    "rc_login": {"address": {"per_second": 10, "burst_count": 1000}},
    "url_preview_enabled": False,
    "listeners": [{"port": 8008, "tls": False, "type": "http", "x_forwarded": False,
                   "resources": [{"names": ["client", "federation"] if federation_domains else ["client"], "compress": False}]}],
})
# Synapse's human-message default (0.2/s, burst10) also throttles encrypted
# document updates, meeting signaling and captions. Keep a bounded per-user
# collaboration allowance, while preserving an operator's explicit policy.
config.setdefault("rc_message", {"per_second": 10, "burst_count": 100})
if os.environ.get("MATRIX_DATABASE_HOST"):
    config["database"] = {"name": "psycopg2", "args": {
        "host": os.environ["MATRIX_DATABASE_HOST"], "port": int(os.environ.get("MATRIX_DATABASE_PORT", "5432")),
        "database": os.environ.get("MATRIX_DATABASE_NAME", "synapse"),
        "user": os.environ.get("MATRIX_DATABASE_USER", "synapse"),
        "password": secret("MATRIX_DATABASE_PASSWORD"), "cp_min": 5, "cp_max": 10,
    }}
path.write_text(yaml.safe_dump(config))
path.chmod(0o600)
print("Private Matrix configuration initialized; registration is disabled and federation uses only the explicit operator allowlist.")
