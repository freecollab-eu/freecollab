"""Privacy integration for current collaboration modules.

All mutations join the caller's reviewed transaction. Credential and private
content cleanup does not contact providers. Configured semantic mirror deletion
is the one explicit retention I/O: failure leaves the purge incomplete.
"""
from datetime import datetime, timedelta
from fastapi import HTTPException
from sqlmodel import select

from ..models.app_platform import AppAIDraft, AppForegroundReceipt, AppJob
from ..models.ai_runtime import AIRun, AIRunSource
from ..models.content_completion import ChannelSemanticIndex, ContentCollectionItem
from ..models.encrypted_work import EncryptedWorkAuthority, EncryptedWorkNotice, EncryptedWorkPointer
from ..models.media_capture import MeetingCapture, MeetingCaptureAIUse, MeetingCaptureReceipt


SOURCE_TABLES = {
    "message": ("message",), "document": ("shareddocument",), "shared_document": ("shareddocument",),
    "file": ("file", "filedocument"), "decision": ("decision",), "clip": ("channelclip",),
    "task": ("taskcard",), "project": ("projectboard",), "feed": ("activityfeedpost",),
    "calendar": ("integrationcontent",), "linked-document": ("integrationcontent",),
    "workflow": ("workflowdefinition",),
}
DERIVED_CROSS_CHANNEL_TABLES = {"airun", "airunsource"}
RETENTION_DEPENDENTS = {"workflowappreceipt", "slackbridgejob", "slackbridgemessage", "contentcollectionitem",
    "airunstep", "airunsource", "airunapproval", "meetingcaptureconsent", "meetingcapturereceipt", "meetingcaptureaiuse"}


def assert_purge_idle(session, channel_ids):
    from . import ai_runtime, slack_livebridge
    if ai_runtime.pending_in_channels(session, channel_ids):
        raise HTTPException(409, "Finish or cancel AI work using these channels, including cross-channel sources, before erasure")
    if slack_livebridge.active_bridge_work(session, channel_ids):
        raise HTTPException(409, "Pause live Slack bridges and finish claimed deliveries before erasure")
    if session.exec(select(MeetingCapture.id).where(MeetingCapture.channel_id.in_(channel_ids), MeetingCapture.state != "stopped").limit(1)).first():
        raise HTTPException(409, "Stop active recording and caption captures before erasure")
    for row in session.exec(select(AppForegroundReceipt).where(AppForegroundReceipt.channel_id.in_(channel_ids), AppForegroundReceipt.status == "processing")).all():
        if row.created_at > datetime.utcnow() - timedelta(seconds=90):
            raise HTTPException(409, "Wait for the active foreground app disclosure to finish before erasure")
        row.status, row.failure, row.finished_at = "uncertain", "Delivery outcome is unknown; inspect the recipient before another action", datetime.utcnow()
        session.add(row)


def _source_removed(kind, identifier, removed):
    return identifier in removed.get(kind, set())


def extend_plan(session, plan, channel_ids, cutoff, add):
    """Add bounded dependency rows after the final native source selection."""
    from . import data_control as controls, slack_livebridge
    removed = {kind: {row.get("id") for name in names for row in plan.get(name, [])} for kind, names in SOURCE_TABLES.items()}
    # Collection pointers never protect an explicitly purged source, while a
    # retained source protects its current containing collection from deletion.
    items = [ContentCollectionItem.model_validate(row) for row in controls._channel_records(session, "contentcollectionitem", channel_ids)]
    retained_stacks = {row.stack_id for row in items if not _source_removed(row.kind, row.resource_id, removed)}
    plan["shareddocumentstack"] = [row for row in plan.get("shareddocumentstack", []) if row["id"] not in retained_stacks]
    removed_stacks = set(controls._ids(plan.get("shareddocumentstack", [])))
    add("contentcollectionitem", [row.model_dump() for row in items if row.stack_id in removed_stacks or _source_removed(row.kind, row.resource_id, removed)])
    # Cache/vector removal is derived from source erasure, independent of the
    # cache's newer creation time. It does not remove newer source documents.
    content_names = set(sum((list(names) for names in SOURCE_TABLES.values()), []))
    affected = {row.get("channel_id") for name in content_names for row in plan.get(name, []) if row.get("channel_id")}
    add("channelsemanticindex", [row.model_dump() for row in session.exec(select(ChannelSemanticIndex)
        .where(ChannelSemanticIndex.channel_id.in_(affected))).all()] if affected else [])
    # A source purge invalidates copied private AI context even in another
    # target conversation. The preview exposes counts, never those private rows.
    source_ids = {row.run_id for row in session.exec(select(AIRunSource).where(AIRunSource.channel_id.in_(channel_ids))).all()
        if _source_removed(row.kind, row.source_id, removed)}
    runs = session.exec(select(AIRun).where(AIRun.id.in_(source_ids) | AIRun.channel_id.in_(channel_ids))).all()
    selected = [row for row in runs if row.id in source_ids or row.created_at < cutoff and row.updated_at < cutoff
        and row.status not in {"queued", "running", "awaiting_review"}]
    from .enterprise_governance import assert_purge_allowed
    for workspace, channel in sorted({(row.workspace_id, row.channel_id) for row in selected}):
        assert_purge_allowed(session, workspace, [channel])
    add("airun", [row.model_dump() for row in selected])
    add("appforegroundreceipt", [row.model_dump() for row in session.exec(select(AppForegroundReceipt).where(
        AppForegroundReceipt.channel_id.in_(channel_ids), AppForegroundReceipt.status != "processing",
        AppForegroundReceipt.created_at < cutoff)).all()])
    # Stopped capture authority outlives every publication/AI authorization
    # window. Active capture exclusion is checked before the plan is built.
    meetings = set(controls._ids(plan.get("meeting", [])) + controls._ids(plan.get("sfuroom", [])))
    for capture in session.exec(select(MeetingCapture).where(MeetingCapture.channel_id.in_(channel_ids))).all():
        receipts = session.exec(select(MeetingCaptureReceipt).where(MeetingCaptureReceipt.capture_id == capture.id)).all()
        eligible = capture.state == "stopped" and capture.updated_at < cutoff and (not capture.ai_expires_at or capture.ai_expires_at < cutoff) and all(row.expires_at < cutoff for row in receipts)
        if eligible:
            add("meetingcapture", [capture.model_dump()])
        elif capture.meeting_id in meetings:
            for name in ("meeting", "sfuroom"):
                plan[name] = [row for row in plan.get(name, []) if row["id"] != capture.meeting_id]
            # Participant authority remains with an unexpired capture's room.
            for name in ("meetingparticipant", "meetinginvitation", "meetingsignal", "sfuparticipant", "meetingparticipantauthority"):
                plan[name] = [row for row in plan.get(name, []) if row.get("meeting_id") != capture.meeting_id]
    clips = set(controls._ids(plan.get("channelclip", [])))
    if clips:
        add("meetingcaptureaiuse", [row.model_dump() for row in session.exec(select(MeetingCaptureAIUse).where(MeetingCaptureAIUse.clip_id.in_(clips))).all()])
    for name, value in slack_livebridge.retention_dependencies(session, channel_ids, cutoff, controls._ids(plan.get("message", []))).items():
        add(name, [row.model_dump() for row in value["rows"]])


def tombstone_erased_clips(session, clip_ids):
    """Preserve consumed-publication identity while making replay permanent."""
    if not clip_ids: return 0
    rows = session.exec(select(MeetingCaptureReceipt).where(MeetingCaptureReceipt.clip_id.in_(clip_ids))).all()
    for row in rows:
        row.erased_at = row.erased_at or datetime.utcnow(); session.add(row)
    return len(rows)


def apply_pre_delete(session, plan):
    from . import ai_runtime, semantic_opensearch, slack_livebridge
    source_tables = set(sum((list(names) for names in SOURCE_TABLES.values()), []))
    affected = {row.get("channel_id") for name in source_tables for row in plan.get(name, []) if row.get("channel_id")}
    for channel in sorted(affected):
        semantic_opensearch.purge_channel(session, channel)
    runs = [row["id"] for row in plan.get("airun", [])]
    from .enterprise_governance import assert_purge_allowed
    for workspace, channel in sorted({(row["workspace_id"], row["channel_id"]) for row in plan.get("airun", [])}):
        assert_purge_allowed(session, workspace, [channel])
    if runs: ai_runtime.cleanup_run_ids(session, runs)
    ai_runtime.anonymize_usage(session, execution_ids=["app-draft:" + row["id"] for row in plan.get("appaidraft", [])]
        + ["turn:" + row["id"] for row in plan.get("copilotturn", [])])
    slack_livebridge.tombstone_erased_messages(session, [row["id"] for row in plan.get("message", [])])
    tombstone_erased_clips(session, [row["id"] for row in plan.get("channelclip", [])])


def assert_account_cleanup_allowed(session, actor_id):
    """Fence preservation before Matrix revocation or private file removal."""
    from .enterprise_governance import assert_deletion_allowed
    channels = session.exec(select(AIRun.channel_id).where(AIRun.user_id == actor_id)).all()
    channels += session.exec(select(AppAIDraft.channel_id).where(AppAIDraft.owner_id == actor_id)).all()
    channels += session.exec(select(AppJob.channel_id).where(AppJob.actor_id == actor_id, AppJob.encrypted_payload != "")).all()
    for channel in sorted(set(channels)):
        assert_deletion_allowed(session, channel, author_id=actor_id)


def close_account_resources(session, actor_id):
    from . import ai_runtime, content_completion, encrypted_work, media_capture, slack_livebridge
    assert_account_cleanup_allowed(session, actor_id)
    ai_runtime.cleanup_actor(session, actor_id)
    content_completion.erase_personal_calendar(session, actor_id)
    media_capture.close_account_resources(session, actor_id)
    encrypted_work.erase_user_progress(session, actor_id)
    slack_livebridge.close_account_resources(session, actor_id)


def cleanup_empty_workspace(session, workspace_id):
    from . import ai_runtime
    ai_runtime.cleanup_workspace(session, workspace_id)


def export_additions(session, workspace_id, channel_ids, actor_id):
    from . import ai_runtime, content_completion, data_control as controls, media_capture, slack_livebridge
    # Bound cardinality before opening the requester's sealed private calendar.
    personal = controls._table("personalcalendarevent")
    controls._records(session, personal.name, (personal.c.workspace_id == workspace_id) & (personal.c.user_id == actor_id))
    result = {
        "private_ai_runtime": ai_runtime.export_actor(session, actor_id, channel_ids, workspace_id=workspace_id),
        "personal_calendar": content_completion.export_personal_calendar(session, workspace_id, actor_id),
        "collection_pointers": content_completion.export_collection_pointers(session, channel_ids, actor_id),
        "meeting_capture_metadata": media_capture.export_capture_metadata(session, channel_ids, actor_id),
        "slack_bridges": slack_livebridge.export_bridge_metadata(session, workspace_id, channel_ids, actor_id),
        "foreground_app_receipts": [{key: getattr(row, key) for key in ("id", "installation_id", "channel_id", "actor_id", "generation", "command", "status", "http_status", "created_at", "finished_at")}
            for row in session.exec(select(AppForegroundReceipt).where(AppForegroundReceipt.channel_id.in_(channel_ids))).all()],
        "encrypted_work_pointers": [row.model_dump() for row in session.exec(select(EncryptedWorkPointer).where(EncryptedWorkPointer.channel_id.in_(channel_ids))).all()],
        "encrypted_work_authority": [row.model_dump() for row in session.exec(select(EncryptedWorkAuthority).where(EncryptedWorkAuthority.channel_id.in_(channel_ids))).all()],
        "own_encrypted_work_progress": [row.model_dump() for row in session.exec(select(EncryptedWorkNotice).where(EncryptedWorkNotice.channel_id.in_(channel_ids), EncryptedWorkNotice.user_id == actor_id)).all()],
    }
    return result


def record_count(value):
    """Count nested exported collections so feature dictionaries cannot bypass caps."""
    if isinstance(value, list): return len(value) + sum(record_count(item) for item in value)
    if isinstance(value, dict): return sum(record_count(item) for item in value.values())
    return 0
