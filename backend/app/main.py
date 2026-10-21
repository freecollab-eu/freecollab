from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session
import socketio

from . import database
from .config import settings
from .middleware import RequestBodyLimitMiddleware, PrivateResponseCacheMiddleware
from .services.rate_limit import install_auth_rate_limits
from .services.crypto import message_keyring
from .services.workspaces import init_workspaces
from .api import auth, channels, messages, files, reactions, read_receipts, threads, mentions, presence, posts, comments, notifications, admin, preferences
from .routers import demo_ai
from .api import account, workspaces, direct_messages, saved_items, decisions, secure_messaging
from .api import oidc, workspace_oidc, webhooks, integrations_content, proofs, operations
from .api import workforce, scim, remote_sync
from .api import security_events, security_status
from .api import project_tasks, meetings, push, shared_documents, workflows, data_control
from .services.security_audit import SecurityAuditMiddleware
from .services.capability_logs import install_capability_log_filter
from .api import file_experience, activity_feed, copilot, cloud_files, external_meetings
from .api import connection_setup
from .api import pilot_feedback, pilot_readiness
from .api import embed
from .models import workspace_crypto_grant  # Register additive tables before init_db.
from .realtime.socket import sio

install_capability_log_filter()

@asynccontextmanager
async def lifespan(app):
    message_keyring()
    database.init_db()
    init_workspaces(database.engine)
    install_auth_rate_limits(app)
    yield


app = FastAPI(title="FreeCollab", version="0.10.0", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(SecurityAuditMiddleware)
app.add_middleware(PrivateResponseCacheMiddleware)

# Routes
app.include_router(auth.router)
app.include_router(channels.router)
app.include_router(messages.router)
app.include_router(files.router)
app.include_router(reactions.router)
app.include_router(read_receipts.router)
app.include_router(threads.router)
app.include_router(presence.router)
app.include_router(notifications.router)
app.include_router(admin.router)
app.include_router(preferences.router)
app.include_router(account.router)
app.include_router(workspaces.router)
app.include_router(direct_messages.router)
app.include_router(saved_items.router)
app.include_router(decisions.router)
app.include_router(secure_messaging.router)
app.include_router(mentions.router)
app.include_router(oidc.router)
app.include_router(workspace_oidc.router)
app.include_router(workforce.router)
app.include_router(scim.router)
app.include_router(scim.admin_router)
app.include_router(remote_sync.router)
app.include_router(remote_sync.oauth_router)
app.include_router(webhooks.router)
app.include_router(integrations_content.router)
app.include_router(proofs.router)
app.include_router(operations.router)
app.include_router(security_events.router)
app.include_router(security_status.router)
app.include_router(project_tasks.router)
app.include_router(meetings.router)
app.include_router(push.router)
app.include_router(shared_documents.router)
app.include_router(workflows.router)
app.include_router(data_control.router)
app.include_router(data_control.account_router)
app.include_router(file_experience.router)
app.include_router(activity_feed.router)
app.include_router(copilot.router)
app.include_router(cloud_files.router)
app.include_router(cloud_files.oauth_router)
app.include_router(external_meetings.router)
app.include_router(external_meetings.oauth_router)
app.include_router(connection_setup.router)
app.include_router(pilot_feedback.router)
app.include_router(pilot_readiness.router)
app.include_router(embed.router)
if settings.enable_experimental_features:
    app.include_router(posts.router)
    app.include_router(comments.router)
if settings.enable_demo_ai:
    app.include_router(demo_ai.router)
    # Preserve the original direct-backend demo URL for opted-in installations.
    app.include_router(demo_ai.router, prefix="/api", include_in_schema=False)

# Socket.io
socket_app = socketio.ASGIApp(sio, app)


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.10.0"}


@app.get("/ready")
def ready():
    try:
        with Session(database.engine) as session:
            session.exec(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ready"}


@app.get("/capabilities")
def capabilities():
    return {
        "version": "0.10.0",
        "experimental_features": settings.enable_experimental_features,
        "demo_ai": settings.enable_demo_ai,
        "max_file_size": files.MAX_FILE_SIZE,
        "file_types": sorted(files.ALLOWED_TYPES),
        "invitations": True,
        "workspaces": True,
        "direct_messages": True,
        "project_tasks": True,
        "languages": ["en", "sv"],
        "language_preference": True,
        "activity_feeds": settings.activity_feeds_enabled,
        "file_experience": True,
        "office_saveback": True,
        "component_sdk": True,
        "embedded_workspaces": True,
        "embedded_customer_sso": True,
        "embedded_encrypted_client": True,
        "cloud_files": settings.cloud_files_enabled,
        "external_meetings": settings.external_meetings_enabled,
        "copilot": settings.copilot_enabled,
        "meetings": True,
        "shared_documents": True,
        "mobile_offline": True,
        "workflows": settings.workflows_enabled,
        "data_controls": settings.data_controls_enabled,
        "push_notifications": settings.push_enabled,
        "account_security": True,
        "secure_messaging": settings.matrix_enabled,
        "message_encryption": "hybrid" if settings.matrix_enabled else "server-side",
        "secure_channels": settings.matrix_enabled,
        "secure_channel_encryption": "matrix-client-e2ee" if settings.matrix_enabled else None,
        "legacy_message_encryption": "server-side",
        "file_encryption": "secure-channels-only" if settings.matrix_enabled else False,
        "integrations": {
            "connection_setup": True,
            "workspace_feedback": True,
            "deployment_readiness": True,
            "oidc": settings.oidc_enabled,
            "workspace_sso": True,
            "workforce_onboarding": True,
            "workspace_scim": True,
            "workspace_sync": settings.sync_enabled,
            "webhooks": settings.webhooks_enabled,
            "document_links": True,
            "calendar_ics": True,
            "otrust": settings.otrust_enabled,
            "operator_diagnostics": True,
        },
    }
