"""Redacted, SELECT-only pilot configuration diagnostics.

Reuse the operational checks rather than interpreting container files, opening
backups or testing providers. A recent heartbeat means polling, not delivery.
"""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import text
from sqlmodel import select

from ..config import settings
from ..models.user import User
from .maildiagnostics import mail_configuration, worker_status
from .operational_security import https_origin, security_status
from .request_guard import recheck_actor
from .sso_scope import require_unrestricted
from .workforce_access import require_personal_account

SUMMARY = "Local configuration only. This does not verify public TLS, DNS, provider delivery, off-host backup or restore success."
FEATURES = (
    ("webhooks", "webhooks_enabled", True), ("otrust", "otrust_enabled", True),
    ("sync", "sync_enabled", True), ("workflows", "workflows_enabled", True),
    ("data_controls", "data_controls_enabled", True), ("push", "push_enabled", True),
    ("cloud_files", "cloud_files_enabled", False), ("external_meetings", "external_meetings_enabled", False),
    ("copilot", "copilot_enabled", False), ("activity_feeds", "activity_feeds_enabled", False),
    ("matrix", "matrix_enabled", False), ("organization_sso", "oidc_enabled", False),
)


def authorize_operator(session, actor_id):
    """Fresh role checks, including changes during diagnostic reads."""
    recheck_actor(session, actor_id)
    require_unrestricted(session)
    require_personal_account(session, actor_id)
    role = session.exec(select(User.role).where(User.id == actor_id)).first()
    if role != "admin":
        raise HTTPException(403, "Installation operator access required")


def _worker(session, now, name, enabled):
    row = worker_status(session, now=now, name=name)
    seen = row["last_seen_at"]
    # All existing worker timestamps are naive UTC. Normalize a future
    # timezone-aware backend without ever trusting a future heartbeat as fresh.
    if seen is not None and seen.tzinfo is not None:
        seen = seen.astimezone(timezone.utc).replace(tzinfo=None)
    delta = (now - seen).total_seconds() if seen is not None else None
    state = "ready" if not enabled else "unknown" if delta is None else "attention" if delta < -30 or delta > 180 else "ready"
    return {"enabled": enabled, "status": state, "last_seen_at": seen,
            "age_seconds": max(0, int(delta)) if delta is not None else None}


def _check(identifier, status, label, detail, action):
    return {"id": identifier, "status": status, "label": label, "detail": detail, "action": action}


def snapshot(session, actor_id, *, now=None):
    authorize_operator(session, actor_id)
    now = now or datetime.utcnow()
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    session.exec(text("SELECT 1"))
    dialect = session.get_bind().dialect.name
    database = {"engine": dialect if dialect in {"postgresql", "sqlite"} else "other", "reachable": True}
    security = security_status(session, now=now)
    controls = {row["id"]: row["status"] for row in security["checks"]}
    mail = mail_configuration()
    features = [{"id": name, "enabled": bool(getattr(settings, key, False)), "worker_required": worker}
                for name, key, worker in FEATURES]
    mail_ready = mail["mode"] == "smtp" and mail["configured"]
    workers = {"mail": _worker(session, now, "mail", bool(mail["configured"])),
               "integrations": _worker(session, now, "integrations", any(row["enabled"] and row["worker_required"] for row in features))}
    metrics = {"configured": bool(settings.operations_metrics_token and settings.operations_metrics_token.get_secret_value())}
    origin = https_origin(settings.public_app_url, allow_root_slash=True)
    checks = [
        _check("production_environment", "ready" if settings.environment == "production" else "attention", "Production mode",
            "Production safeguards are active." if settings.environment == "production" else "This installation is running in development or test mode.",
            "Set ENVIRONMENT=production with independent retained keys before admitting users."),
        _check("public_https_origin", "ready" if origin else "unconfigured", "Public HTTPS origin",
            "The configured public origin has valid public HTTPS syntax. TLS and DNS have not been tested." if origin else "A valid public HTTPS origin is not configured; local development addresses do not establish public readiness.",
            "Configure PUBLIC_APP_URL, then verify public DNS and certificate validity from outside the installation."),
        _check("cors_https_origins", "ready" if controls["cors_https_origins"] == "pass" else "attention", "Allowed browser origins",
            "Explicit canonical HTTPS browser origins include the public application origin." if controls["cors_https_origins"] == "pass" else "Browser origins do not match the public HTTPS application origin exactly.",
            "Set explicit CORS_ORIGINS for the public origin; avoid wildcard origins."),
        _check("proxy_boundary", "unknown", "Trusted proxy boundary",
            "The application cannot verify the active reverse proxy network or Uvicorn forwarding trust policy.",
            "Keep the API private, overwrite forwarding headers at the edge and trust only the intended proxy network."),
        _check("host_boundary", "unknown", "Allowed public hosts",
            "The application has no authoritative runtime evidence of the edge host allowlist.",
            "Configure the public host at the edge and reject unknown Host headers before proxying to the private API."),
        _check("database", "attention" if settings.environment == "production" and dialect != "postgresql" else "ready", "Database",
            "This request reached the configured database. This does not verify backups, durable volumes or failover.",
            "Verify durable database storage and a restore into an isolated new database. PostgreSQL is recommended for production."),
        _check("mail", "ready" if mail_ready else "attention" if mail["mode"] == "smtp" else "unconfigured", "SMTP configuration",
            "SMTP is locally configured; no test message has been sent by this check." if mail_ready else "Production SMTP is not ready. A development file sink does not deliver email to users.",
            "Configure the customer SMTP sender and TLS, then explicitly send and confirm receipt of an operator delivery test."),
        _check("mail_worker", workers["mail"]["status"], "Mail worker",
            "A recent heartbeat proves polling only, not recipient delivery." if workers["mail"]["enabled"] else "Mail delivery is disabled or incomplete, so no mail worker is currently required.",
            "Run python -m app.services.mail and inspect explicit test delivery plus queue failures."),
        _check("integration_worker", workers["integrations"]["status"], "Integration worker",
            "Enabled background features require a recent integration-worker heartbeat; on-demand provider actions are separate." if workers["integrations"]["enabled"] else "No enabled feature currently requires the integration worker.",
            "Run python -m app.services.integration_worker and monitor queue failures before enabling customer background grants."),
        _check("server_keys", "ready" if controls["independent_server_keys"] == "pass" else "attention", "Independent server keys",
            "Configured server keys satisfy the existing independence checks; no key value is included." if controls["independent_server_keys"] == "pass" else "Independent retained production signing, account and content keys need attention.",
            "Provision independent production keys and retain them separately with the recovery procedure; do not rotate existing keys casually."),
        _check("operator_mfa", "ready" if controls["operator_mfa"] == "pass" else "attention", "Operator MFA",
            "Every active personal installation operator has MFA configured." if controls["operator_mfa"] == "pass" else "At least one active installation operator has no MFA configured.",
            "Enable operator MFA and retain recovery codes through the normal account security flow."),
        _check("storage", "ready" if controls["storage_capacity"] == "pass" else controls["storage_capacity"], "Upload storage headroom",
            "The existing local upload-volume check meets its free-space threshold." if controls["storage_capacity"] == "pass" else "Upload storage is low or its free-space counters could not be inspected.",
            "Monitor the upload volume, database volume and infrastructure capacity separately."),
        _check("metrics", "ready" if metrics["configured"] else "unconfigured", "Monitoring credential",
            "A metrics credential is configured; collector reachability and alert delivery have not been tested." if metrics["configured"] else "A dedicated metrics credential is not configured.",
            "Configure OPERATIONS_METRICS_TOKEN and explicitly verify the authenticated collector plus alert routing."),
        _check("backup_evidence", "unknown", "Off-host backup and restore evidence",
            "No trusted off-host backup or restore verification is recorded here. Local file existence cannot establish either result.",
            "Create and seal a consistent backup, verify transfer off-host and complete a restore drill in an isolated new project. Record dated evidence in the operator runbook."),
    ]
    overall = "attention" if any(row["status"] in {"attention", "unconfigured"} for row in checks) else "unknown" if any(row["status"] == "unknown" for row in checks) else "ready"
    # Scalar auth/role reads again after all diagnostics: a cached User or scope
    # must not retain operator authority if access changed during this request.
    session.expire_all()
    authorize_operator(session, actor_id)
    return {"checked_at": now, "environment": settings.environment, "scope": "local_configuration", "status": overall,
            "summary": SUMMARY, "public_origin": origin, "checks": checks, "database": database,
            "mail": {key: mail[key] for key in ("mode", "configured", "tls", "issues")}, "workers": workers,
            "features": features, "metrics": metrics,
            "backup": {"status": "unknown", "evidence_source": "not_recorded", "last_backup_at": None,
                       "last_offsite_verified_at": None, "last_restore_verified_at": None, "freshness": "unknown"}}
