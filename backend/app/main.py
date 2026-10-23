from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi import HTTPException
from redis.exceptions import RedisError
from contextlib import asynccontextmanager
from contextlib import suppress
import asyncio
import logging
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
from .api import account, workspaces, direct_messages, saved_items, decisions, secure_messaging
from .api import oidc, workspace_oidc, webhooks, integrations_content, proofs, operations
from .api import workforce, scim, remote_sync
from .api import security_events, security_status
from .api import project_tasks, meetings, push, shared_documents, workflows, data_control
from .api import channel_metadata, document_organization
from .services.security_audit import SecurityAuditMiddleware
from .services.capability_logs import install_capability_log_filter
from .api import file_experience, activity_feed, copilot, cloud_files, external_meetings
from .api import connection_setup
from .api import pilot_feedback, pilot_readiness
from .api import embed
from .api import chat_productivity, connect, app_platform, enterprise_search, clips, sfu, enterprise_governance
from .api import native_clients, native_push
from .api import slack_import, integration_library
from .api import connect_federation
from .api import calendar_planning
from .api import calendar_delivery
from .api import my_day
from .api import personal_oauth
from .api import avatars
from .api import conversation_work
from .services.native_calls import configured as native_call_configuration
from .models import workspace_crypto_grant  # Register additive tables before init_db.
from .realtime.socket import sio, start_realtime_broker, stop_realtime_broker, realtime_broker_ready

install_capability_log_filter()

@asynccontextmanager
async def lifespan(app):
    if settings.demo_instance:
        from .services.public_demo import require_demo_instance
        require_demo_instance()
    message_keyring()
    database.init_db()
    init_workspaces(database.engine)
    install_auth_rate_limits(app)
    await start_realtime_broker()
    from .services.productivity_relay import run as relay_productivity
    relay = asyncio.create_task(relay_productivity(), name="freecollab-worker-effects")
    demo_cleanup = None
    if settings.demo_instance:
        from .services.public_demo import cleanup_expired_visits
        def collect_expired_examples():
            with Session(database.engine) as session:
                cleanup_expired_visits(session)
        async def maintain_examples():
            while True:
                await asyncio.sleep(30)
                try:
                    await asyncio.to_thread(collect_expired_examples)
                except Exception:
                    logging.getLogger(__name__).warning("Expired example workspaces could not be collected")
        demo_cleanup = asyncio.create_task(maintain_examples(), name="freecollab-example-cleanup")
    try:
        yield
    finally:
        if demo_cleanup:
            demo_cleanup.cancel()
            with suppress(asyncio.CancelledError):
                await demo_cleanup
        relay.cancel()
        with suppress(asyncio.CancelledError):
            await relay
        await stop_realtime_broker()


app = FastAPI(title="FreeCollab", version="0.13.0", lifespan=lifespan)

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
if settings.demo_instance:
    from .api import public_demo
    from .services.public_demo_boundary import PublicDemoBoundaryMiddleware
    app.add_middleware(PublicDemoBoundaryMiddleware)
    app.include_router(public_demo.router)

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
app.include_router(avatars.router)
app.include_router(conversation_work.router)
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
app.include_router(channel_metadata.router)
app.include_router(document_organization.router)
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
app.include_router(chat_productivity.router)
app.include_router(connect.router)
app.include_router(app_platform.router)
app.include_router(enterprise_search.router)
app.include_router(clips.router)
app.include_router(sfu.router)
app.include_router(enterprise_governance.router)
app.include_router(native_clients.router)
app.include_router(slack_import.router)
app.include_router(integration_library.router)
app.include_router(connect_federation.router)
app.include_router(native_push.router)
app.include_router(calendar_planning.router)
app.include_router(calendar_delivery.router)
app.include_router(my_day.router)
app.include_router(personal_oauth.router)
if settings.enable_experimental_features:
    app.include_router(posts.router)
    app.include_router(comments.router)

# Socket.io
socket_app = socketio.ASGIApp(sio, app)
if settings.demo_instance:
    from .services.public_demo_boundary import PublicDemoSocketBoundaryMiddleware
    socket_app = PublicDemoSocketBoundaryMiddleware(socket_app)


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.13.0"}


@app.get("/ready")
def ready():
    try:
        with Session(database.engine) as session:
            session.exec(text("SELECT 1"))
        from .services.shared_runtime import runtime
        shared = runtime()
        if shared is not None:
            if not realtime_broker_ready():
                return JSONResponse({"status": "unavailable"}, status_code=503)
            shared.client.ping()
    except (SQLAlchemyError, RedisError, HTTPException, OSError):
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ready"}


@app.get("/capabilities")
def capabilities():
    return {
        "version": "0.13.0",
        "experimental_features": settings.enable_experimental_features,
        "demo_instance": settings.demo_instance,
        "demo_ai": False,
        "max_file_size": files.MAX_FILE_SIZE,
        "file_types": sorted(files.ALLOWED_TYPES),
        "invitations": True,
        "workspaces": True,
        "my_day": True,
        "slack_import": "resumable-native-or-trusted-device-e2ee-archive",
        "integration_library": "instance-reviewed-declarative-versions",
        "personal_provider_oauth": ["google", "microsoft"],
        "calendar_recurrence": True,
        "calendar_invitations": "internal-members-and-reviewed-external-provider-delivery",
        "calendar_provider_delivery": settings.sync_enabled,
        "calendar_subscriptions": "explicit-revocable-native-channel-capability",
        "native_push": native_push.service.configuration(),
        "encrypted_offline_library": "opt-in-same-sign-in-device-storage",
        "direct_messages": True,
        "group_conversations": True,
        "scheduled_messages": True,
        "encrypted_scheduling": "trusted-device-online",
        "personal_message_reminders": True,
        "apps_platform": True,
        "enterprise_search": settings.enterprise_search_enabled,
        "clips": True,
        "local_clip_transcription": True,
        "shared_channels": "same-installation-matrix" if settings.matrix_enabled else False,
        "cross_installation_shared_channels": bool(settings.matrix_enabled and settings.connect_federation_enabled and settings.connect_federation_peers),
        "meeting_sfu": settings.meetings_sfu_provider,
        "enterprise_governance": True,
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
        "channel_metadata": {"native": True, "matrix": settings.matrix_enabled, "revisioned": True},
        "shared_document_organization": {"native": True, "matrix": False, "message_references": True, "derived_comments": True},
        "thread_history": "paged-with-roots-only-timeline",
        "custom_presence": "active-device-leases",
        "mobile_offline": True,
        "workflows": settings.workflows_enabled,
        "data_controls": settings.data_controls_enabled,
        "push_notifications": settings.push_enabled,
        "native_push_notifications": settings.native_push_enabled,
        "native_background_calls": native_call_configuration(),
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
