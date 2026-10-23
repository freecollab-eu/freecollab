"""Bounded, consented exports and preview-bound native-content erasure.

Workspace ownership alone never opens an unassigned private channel. Deletion
uses an explicit model allowlist and refuses unhandled live foreign references.
The server cannot export or erase client-held Matrix plaintext or old backups.
"""
import base64
from datetime import datetime, timedelta
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import zipfile

from fastapi import HTTPException
from nacl import bindings
from sqlalchemy import and_, case, delete, func, or_, select as sqlselect, update
from sqlmodel import SQLModel, select

from ..config import settings
from ..models.account_security import AccountSecurity
from ..models.channel import Channel, ChannelMember
from ..models.data_control import (AccountIdentityErasure, DataControlJob, DataControlPreview,
                                  DataFileErasure, WorkspaceRetentionPolicy)
from ..models.file import File
from ..models.secure_channel import SecureChannel
from ..models.session import ActiveSession, RevokedToken
from ..models.user import User
from ..models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from .account_security import _secret_key, seal_private, unseal_private
from .access import lock_channel_writes
from .crypto import decrypt_message
from .sso_scope import require_unrestricted
from .workforce_access import is_managed
from .workspaces import lock_workspace, require_workspace_member

MAX_ROWS = 10000
MAX_CHANNELS = 20
MAX_BYTES = 128 * 1024 * 1024
CHUNK = 1024 * 1024
MAGIC = b"FCEXPORT1\n"
IDENTIFIER = re.compile(r"[0-9a-f]{32}\Z")


def policy_for(session, workspace_id):
    return session.get(WorkspaceRetentionPolicy, workspace_id) or WorkspaceRetentionPolicy(workspace_id=workspace_id)


def require_owner(session, workspace_id, user_id):
    if not settings.data_controls_enabled:
        raise HTTPException(404, "Workspace data controls are disabled")
    require_unrestricted(session)
    if is_managed(session, user_id):
        raise HTTPException(403, "Use a personal owner account for data controls")
    member = require_workspace_member(session, workspace_id, user_id)
    actor = session.get(User, user_id, populate_existing=True)
    role = session.exec(select(WorkspaceMember.role).where(WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == user_id)).first()
    if role != "owner" or not actor or actor.is_blocked:
        raise HTTPException(403, "Current workspace owner access required")
    return actor


def selected_channels(session, workspace_id, actor_id, ids, *, purge=False):
    if not ids or len(ids) > MAX_CHANNELS or len(set(ids)) != len(ids):
        raise HTTPException(400, "Select between one and twenty distinct channels")
    result = []
    for identifier in sorted(ids):
        mapping = session.get(WorkspaceChannel, identifier)
        channel = session.get(Channel, identifier)
        member = session.get(ChannelMember, (identifier, actor_id), populate_existing=True)
        if not mapping or mapping.workspace_id != workspace_id or not channel or not member:
            raise HTTPException(403, "Every selected channel requires current membership in this workspace")
        if purge and member.role != "admin":
            raise HTTPException(403, "Content erasure requires administrator membership in every selected channel")
        if purge and session.get(SecureChannel, identifier):
            raise HTTPException(409, "Encrypted Matrix history requires its own device/provider retention controls")
        result.append(channel)
    return result


def _table(name):
    return SQLModel.metadata.tables.get(name)


def _records(session, name, condition=None):
    table = _table(name)
    if table is None:
        return []
    query = sqlselect(table)
    if condition is not None:
        query = query.where(condition)
    rows, size = [], 0
    for result in session.execute(query.limit(MAX_ROWS + 1).execution_options(yield_per=100)).mappings():
        row = dict(result); size += len(_json(row).encode())
        if len(rows) >= MAX_ROWS or size > 8 * 1024 * 1024:
            raise HTTPException(413, "This selection exceeds the bounded data-control batch; select fewer channels")
        rows.append(row)
    return rows


def _json(value):
    def encode(item):
        if isinstance(item, datetime):
            return item.isoformat() + "Z"
        if isinstance(item, bytes):
            return {"base64": base64.b64encode(item).decode()}
        raise TypeError("Unsupported archive value")
    return json.dumps(value, default=encode, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _ids(rows, key="id"):
    return [row[key] for row in rows]


def _children(session, name, column, values):
    table = _table(name)
    return _records(session, name, table.c[column].in_(values)) if table is not None and values else []


def _pk(table, row):
    return {column.name: row[column.name] for column in table.primary_key.columns}


def _old(row, cutoff):
    dates = [row.get(key) for key in ("created_at", "updated_at", "edited_at") if row.get(key) is not None]
    return bool(dates) and all(value < cutoff for value in dates)


def _native(rows):
    return all(row.get("content_mode", "server") == "server" for row in rows)


def _channel_records(session, name, channel_ids):
    table = _table(name)
    return _records(session, name, table.c.channel_id.in_(channel_ids)) if table is not None and "channel_id" in table.c else []


def _active_automation(session, channels):
    for name in ("remotesyncconnection", "workflowdefinition"):
        table = _table(name)
        if table is not None and _records(session, name, and_(table.c.channel_id.in_(channels), table.c.enabled == True)):
            raise HTTPException(409, "Pause synchronization and workflows in the selected channels before permanent erasure")
    table = _table("workflowrun")
    if table is not None and _records(session, table.name, and_(table.c.channel_id.in_(channels), table.c.status.not_in(["completed", "rejected", "cancelled", "timed_out", "failed"]))):
        raise HTTPException(409, "Finish or cancel pending workflow approvals before erasure")
    table = _table("appinstallation")
    if table is not None and any(set(json.loads(row["channel_ids_json"])) & set(channels) for row in
            _records(session, table.name, and_(table.c.enabled == True, table.c.revoked_at.is_(None)))):
        raise HTTPException(409, "Revoke apps granted to selected channels before permanent erasure")
    for name, condition in (("channelrecapschedule", "enabled"), ("appjob", "pending")):
        table = _table(name)
        if table is not None:
            active = table.c.enabled == True if condition == "enabled" else table.c.status.in_(["pending", "processing"])
            if _records(session, table.name, and_(table.c.channel_id.in_(channels), active)):
                raise HTTPException(409, "Stop recurring recaps and cancel pending app requests before permanent erasure")


def build_plan(session, workspace_id, actor_id, channel_ids, cutoff):
    selected_channels(session, workspace_id, actor_id, channel_ids, purge=True)
    _active_automation(session, channel_ids)
    plan = {}
    def add(name, rows):
        if not rows:
            return
        table = _table(name)
        current = {_json(_pk(table, row)): row for row in plan.get(name, [])}
        for row in rows:
            if "channel_id" in row and row["channel_id"] not in channel_ids:
                raise HTTPException(409, "A retained cross-channel dependency prevents erasure")
            current[_json(_pk(table, row))] = row
        plan[name] = list(current.values())
    # A thread is eligible only when its entire descendant tree is old.
    messages = _channel_records(session, "message", channel_ids)
    by_id = {row["id"]: row for row in messages}
    eligible = {row["id"] for row in messages if _old(row, cutoff)}
    changed = True
    while changed:
        changed = False
        for row in messages:
            if row.get("parent_id") in eligible and row["id"] not in eligible:
                eligible.remove(row["parent_id"]); changed = True
    add("message", [by_id[key] for key in eligible])
    files = _channel_records(session, "file", channel_ids)
    docs = _channel_records(session, "filedocument", channel_ids)
    versions = _children(session, "fileversion", "document_id", _ids(docs))
    grouped = {}
    for row in versions:
        grouped.setdefault(row["document_id"], set()).add(row["file_id"])
    file_by_id = {row["id"]: row for row in files}
    file_ids = {row["id"] for row in files if _old(row, cutoff)}
    for document in docs:
        group = grouped.get(document["id"], {document["current_file_id"]})
        if not group <= file_ids or not _old(document, cutoff):
            file_ids -= group
        else:
            add("filedocument", [document])
    add("file", [file_by_id[key] for key in file_ids])
    for parent, revision, fk in (("decision", "decisionrevision", "decision_id"),
                                 ("integrationcontent", "integrationcontentrevision", "item_id")):
        for row in _channel_records(session, parent, channel_ids):
            revisions = _children(session, revision, fk, [row["id"]])
            if _old(row, cutoff) and all(_old(item, cutoff) for item in revisions) and _native(revisions):
                add(parent, [row]); add(revision, revisions)
    # New product records have explicit allowlisted ownership and payload types.
    tasks = _channel_records(session, "taskcard", channel_ids)
    boards = _channel_records(session, "projectboard", channel_ids)
    revision_table = _table("projecttaskrevision")
    for kind, rows in (("task", tasks), ("project", boards)):
        for row in rows:
            revs = _records(session, "projecttaskrevision", and_(revision_table.c.kind == kind, revision_table.c.item_id == row["id"])) if revision_table is not None else []
            if _old(row, cutoff) and all(_old(item, cutoff) for item in revs) and _native(revs):
                if kind == "project" and any(child["project_id"] == row["id"] and child["id"] not in _ids(plan.get("taskcard", [])) for child in tasks):
                    continue
                add("taskcard" if kind == "task" else "projectboard", [row]); add("projecttaskrevision", revs)
    for doc in _channel_records(session, "shareddocument", channel_ids):
        children = {name: _children(session, name, "document_id", [doc["id"]]) for name in (
            "shareddocumentupdate", "shareddocumentsnapshot", "shareddocumentcomment", "shareddocumentplacement")}
        relations = _table("shareddocumentrelation")
        children["shareddocumentrelation"] = _records(session, relations.name, or_(
            relations.c.first_document_id == doc["id"], relations.c.second_document_id == doc["id"])) if relations is not None else []
        flattened = [row for rows in children.values() for row in rows]
        if _old(doc, cutoff) and all(_old(item, cutoff) for item in flattened) and _native(flattened):
            add("shareddocument", [doc])
            for name, rows in children.items(): add(name, rows)
    # An organization change is part of a document's retained history. Old
    # empty stacks can be erased only after every placement is also eligible.
    references = _channel_records(session, "shareddocumentstackreference", channel_ids)
    add("shareddocumentstackreference", [row for row in references if _old(row, cutoff)])
    # A new saved source keeps the source message and its parent chain reachable.
    protected_messages = set()
    for reference in references:
        if _old(reference, cutoff): continue
        identifier = reference["message_id"]
        while identifier in by_id and identifier not in protected_messages:
            protected_messages.add(identifier)
            identifier = by_id[identifier].get("parent_id")
    plan["message"] = [row for row in plan.get("message", []) if row["id"] not in protected_messages]
    removed_references = set(_ids(plan.get("shareddocumentstackreference", [])))
    removed_placements = {row["document_id"] for row in plan.get("shareddocumentplacement", [])}
    placements = _channel_records(session, "shareddocumentplacement", channel_ids)
    for stack in _channel_records(session, "shareddocumentstack", channel_ids):
        if (_old(stack, cutoff)
                and not any(row["stack_id"] == stack["id"] and row["document_id"] not in removed_placements for row in placements)
                and not any(row["stack_id"] == stack["id"] and row["id"] not in removed_references for row in references)):
            add("shareddocumentstack", [stack])
    # Current channel identity is not a historical content item. Preserve its
    # monotonic revision while the Channel exists (avoids stale-editor ABA).
    for run in _channel_records(session, "workflowrun", channel_ids):
        if run.get("status") in {"completed", "rejected", "cancelled", "timed_out", "failed"} and run.get("finished_at") and run["finished_at"] < cutoff:
            add("workflowrun", [run])
    for meeting in _channel_records(session, "meeting", channel_ids):
        end = meeting.get("ended_at") or meeting["expires_at"]
        children = {name: _children(session, name, "meeting_id", [meeting["id"]]) for name in (
            "meetingparticipant", "meetinginvitation", "meetingsignal")}
        if end < cutoff and _old(meeting, cutoff) and all(_old(row, cutoff) and all(
            row[key] < cutoff for key in ("last_seen_at", "expires_at", "used_at") if row.get(key)) for rows in children.values() for row in rows):
            add("meeting", [meeting])
            for name, rows in children.items(): add(name, rows)
            authority = _table("meetingparticipantauthority")
            if authority is not None:
                add(authority.name, _records(session, authority.name, and_(authority.c.transport == "mesh", authority.c.meeting_id == meeting["id"])))
    # Feed revisions and actor choices are shared channel content. Erase a post
    # only when its complete retained discussion is older than the cutoff.
    for post in _channel_records(session, "activityfeedpost", channel_ids):
        children = {name: _children(session, name, "post_id", [post["id"]]) for name in (
            "activityfeedrevision", "activityfeedcomment", "activityfeedchoice", "activityfeedchoicerevision", "activityfeedread")}
        children["activityfeedcommentrevision"] = _children(session, "activityfeedcommentrevision", "comment_id", _ids(children["activityfeedcomment"]))
        all_rows = [row for rows in children.values() for row in rows]
        if _old(post, cutoff) and all(_old(row, cutoff) for row in all_rows) and _native(all_rows):
            add("activityfeedpost", [post])
            for name, rows in children.items(): add(name, rows)
    for link in _channel_records(session, "cloudfilelink", channel_ids):
        if _old(link, cutoff) and link["content_mode"] == "server": add("cloudfilelink", [link])
    clips = _channel_records(session, "channelclip", channel_ids)
    for clip in clips:
        if _old(clip, cutoff) and clip["content_mode"] == "server":
            add("channelclip", [clip])
    for room in _channel_records(session, "sfuroom", channel_ids):
        participants = _children(session, "sfuparticipant", "meeting_id", [room["id"]])
        end = room.get("ended_at") or room["expires_at"]
        if end < cutoff and _old(room, cutoff) and all(
                row["expires_at"] < cutoff and row["last_seen_at"] < cutoff for row in participants):
            add("sfuroom", [room]); add("sfuparticipant", participants)
            authority = _table("meetingparticipantauthority")
            if authority is not None:
                add(authority.name, _records(session, authority.name, and_(authority.c.transport == "sfu", authority.c.meeting_id == room["id"])))
    for name in ("appcard", "appaidraft", "appjob"):
        for row in _channel_records(session, name, channel_ids):
            if _old(row, cutoff) and (name != "appjob" or row["status"] not in {"pending", "processing"}):
                add(name, [row])
    # App callback deduplication tombstones have no content and remain, so a
    # retained installation cannot recreate a purged card by replaying an event.
    for conversation in _channel_records(session, "copilotconversation", channel_ids):
        turns = _children(session, "copilotturn", "conversation_id", [conversation["id"]])
        approvals = _children(session, "copilottoolapproval", "turn_id", _ids(turns))
        if (not conversation["secure"] and _old(conversation, cutoff)
                and all(row["status"] != "processing" and _old(row, cutoff) and (not row["completed_at"] or row["completed_at"] < cutoff) for row in turns)
                and all(_old(row, cutoff) for row in approvals)):
            add("copilotconversation", [conversation]); add("copilotturn", turns); add("copilottoolapproval", approvals)
    for meeting in _channel_records(session, "externalmeeting", channel_ids):
        if not meeting["secure"] and meeting["state"] not in {"pending", "creating", "cancelling"} and _old(meeting, cutoff):
            add("externalmeeting", [meeting])
        elif meeting.get("calendar_item_id"):
            # A retained meeting keeps its local calendar reference. No purge
            # operation contacts or deletes an event at an external provider.
            identifier = meeting["calendar_item_id"]
            plan["integrationcontent"] = [row for row in plan.get("integrationcontent", []) if row["id"] != identifier]
            plan["integrationcontentrevision"] = [row for row in plan.get("integrationcontentrevision", []) if row["item_id"] != identifier]
    # Structured links from retained task history protect their targets.
    removed_clips = set(_ids(plan.get("channelclip", [])))
    protected_files = {row["file_id"] for row in clips if row["id"] not in removed_clips and row.get("file_id")}
    protected_decisions = set()
    if revision_table is not None:
        purged_revisions = set(_ids(plan.get("projecttaskrevision", [])))
        item_ids = _ids(tasks) + _ids(boards)
        for rev in _records(session, "projecttaskrevision", revision_table.c.item_id.in_(item_ids)) if item_ids else []:
            if rev["id"] not in purged_revisions and rev.get("content_mode") == "server" and rev.get("encrypted_payload"):
                # Find the owning authorized channel before decryption.
                item = next((row for row in tasks + boards if row["id"] == rev["item_id"]), None)
                fields = json.loads(decrypt_message(rev["encrypted_payload"], item["channel_id"])).get("fields", {})
                protected_files.update(fields.get("file_ids", [])); protected_decisions.update(fields.get("decision_ids", []))
    if protected_files:
        protected_document_ids = {row["document_id"] for row in versions if row["file_id"] in protected_files}
        protected_files.update(row["file_id"] for row in versions if row["document_id"] in protected_document_ids)
        plan["file"] = [row for row in plan.get("file", []) if row["id"] not in protected_files]
        plan["filedocument"] = [row for row in plan.get("filedocument", []) if row["id"] not in protected_document_ids and row["current_file_id"] not in protected_files]
    if protected_decisions:
        plan["decision"] = [row for row in plan.get("decision", []) if row["id"] not in protected_decisions]
        plan["decisionrevision"] = [row for row in plan.get("decisionrevision", []) if row["decision_id"] not in protected_decisions]
    # A newer comment protects every version of its document. Office edit
    # capabilities are removed with the selected document; they are not content.
    comments = _channel_records(session, "filecomment", channel_ids)
    protected_docs = {row["document_id"] for row in comments if not _old(row, cutoff)}
    protected_comment_files = {row["file_id"] for row in comments if row["document_id"] in protected_docs}
    protected_comment_files.update(row["file_id"] for row in versions if row["document_id"] in protected_docs)
    plan["file"] = [row for row in plan.get("file", []) if row["id"] not in protected_comment_files]
    plan["filedocument"] = [row for row in plan.get("filedocument", []) if row["id"] not in protected_docs]
    removed_files, removed_docs = set(_ids(plan.get("file", []))), set(_ids(plan.get("filedocument", [])))
    add("filecomment", [row for row in comments if row["file_id"] in removed_files or row["document_id"] in removed_docs])
    office = [row for row in _channel_records(session, "officeeditsession", channel_ids) if row["document_id"] in removed_docs or row["last_file_id"] in removed_files]
    add("officeeditsession", office)
    locks = _table("officeeditlock")
    if locks is not None and (office or removed_docs):
        add("officeeditlock", _records(session, locks.name, or_(locks.c.document_id.in_(removed_docs), locks.c.session_id.in_(_ids(office)))))
    # Retained decision history may cite a source message; keep that source.
    removed_decision_revs = set(_ids(plan.get("decisionrevision", [])))
    decisions = _channel_records(session, "decision", channel_ids)
    for decision in decisions:
        for revision in _children(session, "decisionrevision", "decision_id", [decision["id"]]):
            if revision["id"] not in removed_decision_revs and revision.get("content_mode") == "server":
                source = json.loads(decrypt_message(revision["encrypted_payload"], decision["channel_id"])).get("source_message_id")
                if source:
                    plan["message"] = [row for row in plan.get("message", []) if row["id"] != source]
    # Local erasure must not turn into an unintended remote provider deletion.
    # Paused sync mappings are removed; provider copies have separate retention.
    connections = _channel_records(session, "remotesyncconnection", channel_ids)
    mapping = _table("remotesyncitem")
    local_ids = _ids(plan.get("filedocument", [])) + _ids(plan.get("integrationcontent", []))
    if mapping is not None and connections and local_ids:
        add("remotesyncitem", _records(session, mapping.name, and_(mapping.c.connection_id.in_(_ids(connections)), mapping.c.local_id.in_(local_ids))))
    # Delete only known dependents; unknown future FK models fail closed below.
    allowed_dependents = {"reaction", "readreceipt", "messagechange", "threadsubscription", "fileversion", "fileuploadreceipt",
        "remotesyncfilestate", "decisionrevision", "integrationcontentrevision", "calendarattendee", "shareddocumentupdate", "shareddocumentsnapshot",
        "shareddocumentcomment", "shareddocumentplacement", "shareddocumentrelation", "shareddocumentstackreference", "workflowvote", "workflowactivity", "workflownotice", "pushdelivery", "nativepushdelivery", "remotesyncconflict", "slackimportmessage", "slackimportdetail",
        "calendarproviderpublication", "calendarproviderjob", "nativecallsession", "nativecallconsent"}
    expanded = True
    while expanded:
        before = sum(map(len, plan.values()))
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in allowed_dependents: continue
            for fk in table.foreign_keys:
                target = fk.column.table
                if target.name not in plan: continue
                values = [row[fk.column.name] for row in plan[target.name]]
                add(table.name, _records(session, table.name, fk.parent.in_(values)) if values else [])
        expanded = before != sum(map(len, plan.values()))
    removed_ids = {row["id"] for rows in plan.values() for row in rows if "id" in row}
    table = _table("remotesyncactivity")
    if table is not None and plan.get("remotesyncitem"):
        add(table.name, _records(session, table.name, table.c.item_id.in_(_ids(plan["remotesyncitem"]))))
    for name in ("channelpin", "bookmark"):
        table = _table(name)
        if table is not None and removed_ids:
            add(name, _records(session, name, and_(table.c.channel_id.in_(channel_ids), table.c.item_id.in_(removed_ids))))
    table = _table("notification")
    if table is not None and removed_ids:
        add("notification", _records(session, "notification", and_(table.c.entity_id.in_(removed_ids), table.c.entity_type.in_(["message", "file", "decision", "task", "shared_document", "workflow_run", "activity_feed_post", "calendar_invitation"]))))
    table = _table("notificationdelivery")
    if table is not None and plan.get("notification"):
        add("notificationdelivery", _records(session, table.name, table.c.notification_id.in_(_ids(plan["notification"]))))
    proof = _table("versionproof")
    if proof is not None and removed_ids:
        add("versionproof", _records(session, proof.name, and_(proof.c.channel_id.in_(channel_ids), proof.c.target_id.in_(removed_ids))))
    if sum(map(len, plan.values())) > MAX_ROWS:
        raise HTTPException(413, "This preview exceeds the bounded erasure batch")
    if sum(len(_json(row).encode()) for rows in plan.values() for row in rows) > 16 * 1024 * 1024:
        raise HTTPException(413, "This preview exceeds the bounded erasure batch")
    # A newly introduced model must never be silently swept by a generic cascade.
    for table in SQLModel.metadata.sorted_tables:
        planned = {_json(_pk(table, row)) for row in plan.get(table.name, [])}
        for fk in table.foreign_keys:
            if fk.column.table.name not in plan: continue
            if table.name == "offlinemessagereceipt" and fk.parent.name == "message_id" and fk.column.table.name == "message":
                # Preserve retry identities so an offline reconnect cannot
                # recreate content that an owner has permanently erased.
                continue
            if table.name == "cloudfileimport" and fk.parent.name == "file_id" and fk.column.table.name == "file":
                continue  # Preserve import idempotency as an erased tombstone.
            values = [row[fk.column.name] for row in plan[fk.column.table.name]]
            if not values: continue
            for row in _records(session, table.name, fk.parent.in_(values)):
                if _json(_pk(table, row)) not in planned:
                    raise HTTPException(409, "Retained dependent records prevent this erasure; narrow the selection or remove the dependent item first")
    return {name: rows for name, rows in sorted(plan.items()) if rows}


def plan_digest(plan):
    normalized = {name: sorted(rows, key=_json) for name, rows in plan.items()}
    return hashlib.sha256(_json(normalized).encode()).hexdigest()


def plan_counts(plan):
    return {name: len(rows) for name, rows in plan.items() if rows}


def _job_actor(session, job, channels, *, purge=False):
    session.execute(update(User).where(User.id == job.requested_by).values(name=User.name))
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == job.requested_by).values(auth_version=AccountSecurity.auth_version))
    lock_workspace(session, job.workspace_id)
    for channel_id in sorted(channels): lock_channel_writes(session, channel_id)
    session.expire_all()
    user = require_owner(session, job.workspace_id, job.requested_by)
    state = session.get(AccountSecurity, job.requested_by)
    tracked = session.exec(select(ActiveSession).where(ActiveSession.token_hash == job.token_hash)).first()
    if (not state or state.auth_version != job.auth_version or not tracked or tracked.user_id != job.requested_by or tracked.revoked_at
            or tracked.expires_at <= datetime.utcnow() or session.get(RevokedToken, job.token_hash)):
        raise HTTPException(401, "The requesting owner session is no longer valid")
    selected_channels(session, job.workspace_id, user.id, channels, purge=purge)
    return user


def _archive_root():
    root = Path(settings.storage_path).absolute() / ".data-control"
    if root.is_symlink(): raise HTTPException(409, "Private export storage is unavailable")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return root


def _artifact_path(identifier):
    if not IDENTIFIER.fullmatch(identifier): raise HTTPException(400, "Invalid job identifier")
    return _archive_root() / (identifier + ".fcdata")


def _artifact_key(identifier):
    return hmac.new(_secret_key(), ("freecollab-data-export:" + identifier).encode(), hashlib.sha256).digest()


def seal_archive(source, destination, identifier):
    state = bindings.crypto_secretstream_xchacha20poly1305_state()
    header = bindings.crypto_secretstream_xchacha20poly1305_init_push(state, _artifact_key(identifier))
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    source.seek(0)
    with os.fdopen(fd, "wb") as output:
        output.write(MAGIC + header)
        while True:
            piece = source.read(CHUNK)
            final = not piece
            encrypted = bindings.crypto_secretstream_xchacha20poly1305_push(state, piece, tag=(bindings.crypto_secretstream_xchacha20poly1305_TAG_FINAL if final else bindings.crypto_secretstream_xchacha20poly1305_TAG_MESSAGE))
            output.write(len(encrypted).to_bytes(4, "big")); output.write(encrypted)
            if final: break
        output.flush(); os.fsync(output.fileno())


def open_archive(identifier):
    source = _artifact_path(identifier)
    temporary = tempfile.NamedTemporaryFile(prefix="download-", suffix=".zip", dir=_archive_root(), delete=False)
    path = Path(temporary.name)
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(fd).st_mode): os.close(fd); raise ValueError("Invalid artifact")
        with os.fdopen(fd, "rb") as stream, temporary as output:
            if stream.read(len(MAGIC)) != MAGIC: raise ValueError("Invalid artifact")
            header = stream.read(bindings.crypto_secretstream_xchacha20poly1305_HEADERBYTES)
            state = bindings.crypto_secretstream_xchacha20poly1305_state()
            bindings.crypto_secretstream_xchacha20poly1305_init_pull(state, header, _artifact_key(identifier))
            total = 0
            while True:
                length = stream.read(4)
                if len(length) != 4: raise ValueError("Truncated artifact")
                size = int.from_bytes(length, "big")
                if not bindings.crypto_secretstream_xchacha20poly1305_ABYTES <= size <= CHUNK + bindings.crypto_secretstream_xchacha20poly1305_ABYTES:
                    raise ValueError("Invalid artifact frame")
                body = stream.read(size)
                if len(body) != size: raise ValueError("Truncated artifact")
                plain, tag = bindings.crypto_secretstream_xchacha20poly1305_pull(state, body)
                total += len(plain)
                if total > MAX_BYTES + 4 * 1024 * 1024: raise ValueError("Oversized artifact")
                output.write(plain)
                if tag == bindings.crypto_secretstream_xchacha20poly1305_TAG_FINAL:
                    if stream.read(1): raise ValueError("Trailing artifact data")
                    break
                if tag != bindings.crypto_secretstream_xchacha20poly1305_TAG_MESSAGE: raise ValueError("Unsupported artifact tag")
        return path
    except Exception:
        temporary.close(); path.unlink(missing_ok=True)
        raise HTTPException(409, "The private export artifact could not be authenticated") from None


EXPORT_FIELDS = {
    "channelclip": "id channel_id created_by created_at content_mode matrix_event_id file_id encrypted_payload",
    "sfuroom": "id channel_id host_user_id created_at expires_at ended_at max_participants",
    "appcard": "id workspace_id channel_id bot_id bot_name created_at",
    "activityfeedpost": "id channel_id created_by created_at updated_at revision archived poll_json",
    "activityfeedrevision": "id post_id revision created_by created_at archived content_mode matrix_event_id encrypted_payload",
    "activityfeedcomment": "id post_id created_by created_at updated_at revision archived",
    "activityfeedcommentrevision": "id comment_id revision created_by created_at archived content_mode matrix_event_id encrypted_payload",
    "activityfeedchoice": "post_id user_id kind revision poll_id updated_at",
    "activityfeedchoicerevision": "id post_id user_id kind revision poll_id created_at content_mode matrix_event_id encrypted_payload",
    "filecomment": "id channel_id document_id file_id created_by created_at",
    "cloudfilelink": "id workspace_id channel_id created_by content_mode matrix_event_id created_at encrypted_payload",
    "externalmeeting": "id channel_id created_by state revision secure calendar_item_id created_at updated_at",
    "channel": "id name type created_at",
    "message": "id channel_id author_id content encrypted parent_id thread_count mentions edited_at deleted created_at",
    "slackimportmessage": "message_id import_id source_channel source_timestamp",
    "slackimportdetail": "message_id encrypted_details",
    "file": "id channel_id uploader_id filename content_type size created_at",
    "filedocument": "id channel_id owner_id current_file_id version_count created_at",
    "fileversion": "file_id document_id number",
    "decision": "id channel_id created_by created_at",
    "decisionrevision": "id decision_id revision created_by created_at content_mode matrix_event_id encrypted_payload",
    "integrationcontent": "id channel_id kind created_by created_at",
    "integrationcontentrevision": "id item_id revision created_by created_at content_mode matrix_event_id encrypted_payload deleted",
    "calendarattendee": "item_id user_id invited_by invited_at response responded_at revision",
    "calendarproviderpublication": "id workspace_id channel_id item_id connection_id user_id connection_revision revision state updated_at",
    "calendarproviderjob": "id user_id channel_id item_id publication_id action event_revision publication_revision connection_revision status created_at finished_at failure_code",
    "calendarsubscription": "id workspace_id channel_id user_id include_titles expires_at revoked_at created_at",
    "projectboard": "id channel_id created_by created_at updated_at revision archived",
    "taskcard": "id project_id channel_id created_by created_at updated_at revision archived",
    "projecttaskrevision": "id kind item_id revision created_by created_at content_mode matrix_event_id encrypted_payload archived",
    "shareddocument": "id channel_id created_by created_at updated_at archived version update_count stored_bytes summary_event_id summary_update_id summary_by summary_encrypted_payload",
    "shareddocumentupdate": "document_id update_id sequence created_by created_at content_mode matrix_event_id encrypted_payload byte_size",
    "shareddocumentsnapshot": "id document_id covers_sequence created_by created_at content_mode matrix_event_id encrypted_payload",
    "shareddocumentcomment": "id document_id created_by created_at content_mode matrix_event_id encrypted_payload resolved version",
    "shareddocumentstack": "id channel_id created_by created_at updated_at version generation encrypted_payload",
    "shareddocumentplacement": "document_id channel_id stack_id version updated_by updated_at",
    "shareddocumentrelation": "id channel_id first_document_id second_document_id created_by created_at",
    "shareddocumentstackreference": "id channel_id stack_id message_id created_by created_at",
    "channelmetadata": "channel_id version content_mode matrix_event_id encrypted_payload created_by updated_by created_at updated_at",
    "workflowdefinition": "id workspace_id channel_id created_by granted_by current_revision enabled created_at updated_at",
    "workflowrevision": "workflow_id revision encrypted_definition created_by created_at",
    "workflowrun": "id workflow_id channel_id workflow_revision event_id event_kind resource_id actor_id status step_index due_at deadline_at attempts created_at updated_at finished_at",
    "workflowvote": "run_id step_index user_id choice matrix_event_id created_at",
    "workflowactivity": "id run_id kind actor_id step_index created_at",
    "meeting": "id channel_id host_user_id created_at expires_at ended_at",
}



def export_pilot_feedback(session, workspace_id, user_id):
    """Only the requester's optional personal reports enter an owner archive."""
    from ..models.pilot_feedback import PilotFeedback
    from .pilot_feedback import own_record
    rows = session.exec(select(PilotFeedback).where(PilotFeedback.workspace_id == workspace_id,
        PilotFeedback.user_id == user_id).order_by(PilotFeedback.task).limit(5)).all()
    return [{"task": row.task, "revision": row.revision, "deleted": True} if row.deleted else
        {**own_record(row), "deleted": False} for row in rows]


def erase_pilot_feedback_for_account(session, user_id):
    """Erase private reports and replay tombstones in the identity transaction.

    Closing the account revokes every old session, so replay protection no longer
    requires retaining these personal workspace/user links. No notes are opened.
    """
    table = _table("pilotfeedback")
    if table is not None:
        session.execute(delete(table).where(table.c.user_id == user_id))


def export_data(session, job, request):
    channels = selected_channels(session, job.workspace_id, job.requested_by, request["channel_ids"])
    channel_ids = [row.id for row in channels]
    secure_ids = {row.channel_id for row in session.exec(select(SecureChannel).where(SecureChannel.channel_id.in_(channel_ids))).all()}
    records = {"channel": [{"id": row.id, "name": None if row.id in secure_ids else row.name,
                "type": row.type, "created_at": row.created_at, "content_mode": "matrix" if row.id in secure_ids else "server"} for row in channels]}
    parents = {}
    for name in ("message", "file", "filedocument", "decision", "integrationcontent", "projectboard", "taskcard", "shareddocument", "shareddocumentstack", "shareddocumentplacement", "shareddocumentrelation", "shareddocumentstackreference", "channelmetadata", "workflowdefinition", "workflowrun", "meeting", "activityfeedpost", "filecomment", "cloudfilelink", "externalmeeting", "appcard", "channelclip", "sfuroom", "calendarproviderpublication", "calendarproviderjob", "calendarsubscription"):
        rows = _channel_records(session, name, channel_ids)
        if name in {"message", "file", "filedocument", "appcard"}:
            # Matrix messages/media are not represented by native file bytes.
            rows = [row for row in rows if row["channel_id"] not in secure_ids]
        records[name] = rows
        parents[name] = {row["id"]: row["channel_id"] for row in rows if "id" in row}
    for name, parent, field in (("slackimportmessage", "message", "message_id"), ("slackimportdetail", "message", "message_id"),
        ("fileversion", "filedocument", "document_id"), ("decisionrevision", "decision", "decision_id"),
        ("integrationcontentrevision", "integrationcontent", "item_id"), ("calendarattendee", "integrationcontent", "item_id"), ("shareddocumentupdate", "shareddocument", "document_id"),
        ("shareddocumentsnapshot", "shareddocument", "document_id"), ("shareddocumentcomment", "shareddocument", "document_id"),
        ("workflowrevision", "workflowdefinition", "workflow_id"), ("workflowvote", "workflowrun", "run_id"), ("workflowactivity", "workflowrun", "run_id"),
        ("activityfeedrevision", "activityfeedpost", "post_id"), ("activityfeedcomment", "activityfeedpost", "post_id"),
        ("activityfeedchoice", "activityfeedpost", "post_id"), ("activityfeedchoicerevision", "activityfeedpost", "post_id")):
        records[name] = _children(session, name, field, list(parents.get(parent, {})))
        if name == "activityfeedcomment":
            parents[name] = {row["id"]: parents["activityfeedpost"][row["post_id"]] for row in records[name]}
    records["activityfeedcommentrevision"] = _children(session, "activityfeedcommentrevision", "comment_id", list(parents.get("activityfeedcomment", {})))
    table = _table("projecttaskrevision")
    task_channels = {**parents.get("projectboard", {}), **parents.get("taskcard", {})}
    records["projecttaskrevision"] = _records(session, table.name, table.c.item_id.in_(list(task_channels))) if table is not None and task_channels else []
    if sum(map(len, records.values())) > MAX_ROWS:
        raise HTTPException(413, "Export is too large; select fewer channels")
    total_bytes = 0
    exported = {}
    for name, rows in records.items():
        clean = []
        for row in rows:
            fields = EXPORT_FIELDS[name].split()
            value = {key: row[key] for key in fields if key in row}
            channel = row.get("channel_id")
            for parent, field in (("message", "message_id"), ("decision", "decision_id"), ("integrationcontent", "item_id"), ("shareddocument", "document_id"), ("workflowdefinition", "workflow_id"), ("workflowrun", "run_id"), ("activityfeedpost", "post_id"), ("activityfeedcomment", "comment_id")):
                if channel is None and field in row: channel = parents.get(parent, {}).get(row[field])
            if name == "projecttaskrevision": channel = task_channels.get(row["item_id"])
            if "encrypted_payload" in fields and row.get("encrypted_payload"):
                value.pop("encrypted_payload", None)
                if channel not in secure_ids and row.get("content_mode", "server") == "server":
                    if name == "cloudfilelink":
                        from ..models.cloud_files import CloudFileLink
                        from .cloud_files import serialize_link
                        value["fields"] = serialize_link(CloudFileLink.model_validate(row))["fields"]
                    elif name in {"activityfeedrevision", "activityfeedcommentrevision", "activityfeedchoicerevision"}:
                        from .activity_feed import open_fields
                        kind = "post" if name == "activityfeedrevision" else "comment" if name == "activityfeedcommentrevision" else row["kind"]
                        identifier = row["post_id"] if kind == "post" else row["comment_id"] if kind == "comment" else row["post_id"] + ":" + row["user_id"] + ":" + kind
                        value["fields"] = open_fields(session, channel, kind, identifier, row["revision"], row["encrypted_payload"])
                    elif name == "channelmetadata":
                        from ..models.channel_metadata import ChannelMetadata
                        from .channel_metadata import open_payload
                        value["fields"] = open_payload(session, ChannelMetadata.model_validate(row))
                    elif name == "shareddocumentstack":
                        from .shared_documents import open_payload
                        value["fields"] = open_payload(session, channel, "stack", row["id"], row["generation"], row["encrypted_payload"])
                    elif name.startswith("shareddocument"):
                        from .shared_documents import open_payload
                        kind = {"shareddocumentupdate": "update", "shareddocumentsnapshot": "snapshot", "shareddocumentcomment": "comment"}[name]
                        value["fields"] = open_payload(session, channel, kind, row["document_id"], row.get("update_id", row.get("id")), row["encrypted_payload"])
                    elif name == "channelclip":
                        from .shared_documents import open_payload
                        value["fields"] = open_payload(session, channel, "clip", row["id"], row["id"], row["encrypted_payload"])
                    else:
                        value["fields"] = json.loads(decrypt_message(row["encrypted_payload"], channel))
            if name == "channelmetadata": value.pop("encrypted_payload", None)
            if name == "shareddocument":
                value.pop("summary_encrypted_payload", None)
                if channel not in secure_ids and row.get("summary_encrypted_payload"):
                    from .shared_documents import open_payload
                    value["summary"] = open_payload(session, channel, "summary", row["id"], row["id"], row["summary_encrypted_payload"])
            if name == "workflowrevision":
                # Safe metadata only unless the workflow module supplies its
                # explicit native-definition serializer; no ciphertext/keys.
                value.pop("encrypted_definition", None)
                try:
                    from .workflow import definition_fields
                    from ..models.workflow import WorkflowDefinition
                    membership = session.get(ChannelMember, (channel, job.requested_by))
                    if membership and membership.role == "admin":
                        definition = session.get(WorkflowDefinition, row["workflow_id"])
                        value["definition"] = definition_fields(session, definition, row["revision"]).model_dump(mode="json")
                except ImportError:
                    pass
            if name == "message":
                value["content"] = decrypt_message(row["content"], channel) if row.get("encrypted") else row["content"]
            if name == "slackimportdetail":
                value.pop("encrypted_details", None)
                if channel not in secure_ids and row.get("encrypted_details"):
                    value["details"] = json.loads(decrypt_message(row["encrypted_details"], channel))
            if name == "appcard":
                value["card"] = json.loads(unseal_private(row["encrypted_card"], "app-card", row["id"]))
            if name == "filecomment" and channel not in secure_ids:
                comment = json.loads(decrypt_message(row["encrypted_text"], channel))
                if (comment.get("id"), comment.get("document_id"), comment.get("file_id")) != (row["id"], row["document_id"], row["file_id"]):
                    raise HTTPException(409, "Stored file comment context changed")
                value["text"] = comment["text"]
            if name == "externalmeeting" and not row["secure"] and channel not in secure_ids:
                if row.get("encrypted_request"): value["fields"] = json.loads(decrypt_message(row["encrypted_request"], channel))
                if row.get("encrypted_result"):
                    from .external_meetings import unpack
                    value["provider_event"] = unpack(row["encrypted_result"], "external-meeting-result", row["id"])
            if name == "meeting":
                participant = _table("meetingparticipant")
                value["participant_count"] = session.execute(sqlselect(func.count()).select_from(participant).where(
                    participant.c.meeting_id == row["id"])).scalar_one() if participant is not None else 0
            if name == "sfuroom":
                participant = _table("sfuparticipant")
                value["participant_count"] = session.execute(sqlselect(func.count()).select_from(participant).where(
                    participant.c.meeting_id == row["id"])).scalar_one() if participant is not None else 0
            total_bytes += len(_json(value).encode())
            if total_bytes > MAX_BYTES: raise HTTPException(413, "Export exceeds the bounded archive size")
            clean.append(value)
        exported[name] = clean
    # Copilot conversations are private even inside a shared channel. An owner
    # export can include only the requester's history, with fresh source checks.
    from ..models.copilot import CopilotConversation
    from .copilot import history as copilot_history
    own_conversations = session.exec(select(CopilotConversation).where(CopilotConversation.user_id == job.requested_by,
        CopilotConversation.channel_id.in_(channel_ids))).all()
    exported["copilot_conversations"] = [{"id": row.id, "channel_id": row.channel_id, "secure": row.secure,
        "created_at": row.created_at, "turns": [] if row.secure or row.channel_id in secure_ids else copilot_history(session, row, job.requested_by)} for row in own_conversations]
    from .app_platform import export_private_drafts
    exported["private_app_drafts"] = export_private_drafts(session, job.workspace_id,
        [identifier for identifier in channel_ids if identifier not in secure_ids], job.requested_by)
    if sum(len(row["turns"]) + 1 for row in exported["copilot_conversations"]) + len(exported["private_app_drafts"]) + sum(map(len, records.values())) > MAX_ROWS:
        raise HTTPException(413, "Export is too large; select fewer channels")
    from ..models.preference import UserLanguage
    language = session.get(UserLanguage, job.requested_by)
    exported["personal_preferences"] = {"language": language.locale if language else "auto"}
    from ..models.enterprise_search import EnterpriseSearchConnection
    exported["personal_search_connections"] = [{"id": row.id, "name": row.name, "provider": row.provider,
        "workspace_id": row.workspace_id, "created_at": row.created_at.isoformat(), "revision": row.revision}
        for row in session.exec(select(EnterpriseSearchConnection).where(
            EnterpriseSearchConnection.workspace_id == job.workspace_id,
            EnterpriseSearchConnection.user_id == job.requested_by)).all()]
    exported["pilot_feedback"] = export_pilot_feedback(session, job.workspace_id, job.requested_by)
    # No password/hash/key/session/IdP or remote-provider credential table enters
    # this explicit profile projection.
    memberships = _records(session, "workspacemember", WorkspaceMember.__table__.c.workspace_id == job.workspace_id)
    private_ai_rows = sum(1 + len(row["turns"]) for row in exported["copilot_conversations"]) + len(exported["private_app_drafts"])
    if sum(map(len, records.values())) + private_ai_rows + len(exported["pilot_feedback"]) + len(exported["personal_search_connections"]) + len(memberships) + 1 > MAX_ROWS:
        raise HTTPException(413, "Export is too large; select fewer channels")
    users = {row.id: row for row in session.exec(select(User.id, User.name, User.email).where(User.id.in_([member["user_id"] for member in memberships]))).all()}
    exported["members"] = [{"user_id": row["user_id"], "role": row["role"], "name": users[row["user_id"]].name,
                             "email": users[row["user_id"]].email} for row in memberships if row["user_id"] in users]
    exported["format"] = "freecollab-workspace-export-v1"
    exported["workspace_id"] = job.workspace_id
    workspace = session.get(Workspace, job.workspace_id)
    exported["workspace"] = {"id": workspace.id, "name": workspace.name}
    exported["created_at"] = datetime.utcnow()
    if request.get("governance_case_id"):
        from ..models.enterprise_governance import GovernanceCase
        case = session.get(GovernanceCase, request["governance_case_id"], populate_existing=True)
        if not case or case.workspace_id != job.workspace_id or case.status != "active":
            raise HTTPException(409, "Evidence preservation changed before collection")
        exported["preservation_case"] = {"id": case.id, "revision": case.revision,
            "channel_ids": json.loads(case.channel_ids_json), "custodian_ids": json.loads(case.custodian_ids_json),
            "collection_scope": "authorized_native_channel_context", "matrix_plaintext_included": False}
    exported["limitations"] = ["Only channels currently assigned to the requesting owner are included.",
        "Matrix records contain opaque references; this archive contains no client-held message keys or decrypted Matrix history.",
        "Provider copies, other members' device caches and old backups are separate retention domains.",
        "Workspace feedback includes only the requester's voluntary reports and private notes."]
    total_bytes = len(_json(exported).encode())
    if total_bytes > MAX_BYTES: raise HTTPException(413, "Export exceeds the bounded archive size")
    return exported, records.get("file", []), total_bytes


def create_archive(session, job, request):
    data, files, total = export_data(session, job, request)
    for file in files:
        total += file["size"]
        if total > MAX_BYTES: raise HTTPException(413, "Export exceeds the bounded archive size")
    # TemporaryFile is anonymous/unlinked on the supported POSIX server. A
    # killed process cannot leave a named plaintext build archive behind.
    with tempfile.TemporaryFile(mode="w+b", dir=_archive_root()) as plain:
        with zipfile.ZipFile(plain, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=False) as archive:
            archive.writestr("export.json", _json(data))
            for file in files:
                if not IDENTIFIER.fullmatch(file["id"]): raise HTTPException(409, "Invalid stored file identity")
                try:
                    from .storage import storage_service, MAX_BLOB_BYTES
                    content = storage_service.read_file(file["id"], max_bytes=MAX_BLOB_BYTES, expected_size=file["size"])
                    if content is None:
                        raise ValueError("Missing stored file")
                    archive.writestr("files/" + file["id"], content)
                except (OSError, ValueError):
                    raise HTTPException(409, "A stored file is missing or changed; no incomplete archive was published") from None
        artifact = _artifact_path(job.id)
        artifact.unlink(missing_ok=True)  # Only this job's private stale attempt.
        seal_archive(plain, artifact, job.id)
        plain.seek(0, os.SEEK_END); job.artifact_size = plain.tell()
        plain.seek(0); job.artifact_sha256 = hashlib.file_digest(plain, "sha256").hexdigest()
        job.counts_json = _json({key: len(value) for key, value in data.items() if isinstance(value, list) and key != "limitations"})


def apply_plan(session, job, plan):
    from .enterprise_governance import assert_purge_allowed
    request = json.loads(unseal_private(job.encrypted_request, "data-job", job.id))
    assert_purge_allowed(session, job.workspace_id, request["channel_ids"])
    # Explicit PK plans + reverse dependency order preserve live FK integrity.
    files = _ids(plan.get("file", []))
    imports = _table("cloudfileimport")
    if imports is not None and files:
        session.execute(update(imports).where(imports.c.file_id.in_(files)).values(file_id=None))
    for identifier in files:
        if not IDENTIFIER.fullmatch(identifier): raise HTTPException(409, "Invalid stored file identity")
        session.add(DataFileErasure(job_id=job.id, file_id=identifier))
    message_table = _table("message")
    if message_table is not None and plan.get("message"):
        receipt = _table("offlinemessagereceipt")
        if receipt is not None:
            session.execute(update(receipt).where(receipt.c.message_id.in_(_ids(plan["message"]))).values(message_id=None))
        # Break only selected self references before deleting their tree.
        session.execute(update(message_table).where(message_table.c.id.in_(_ids(plan["message"]))).values(parent_id=None))
    authority = _table("meetingparticipantauthority")
    if authority is not None:
        for row in plan.get(authority.name, []):
            key = _pk(authority, row)
            session.execute(delete(authority).where(and_(*(authority.c[name] == value for name, value in key.items()))))
    for table in reversed(SQLModel.metadata.sorted_tables):
        if table.name == "meetingparticipantauthority": continue
        for row in plan.get(table.name, []):
            key = _pk(table, row)
            session.execute(delete(table).where(and_(*(table.c[name] == value for name, value in key.items()))))
    if message_table is not None:
        for parent in {row.get("parent_id") for row in plan.get("message", []) if row.get("parent_id")}:
            remaining = session.execute(sqlselect(message_table.c.id).where(message_table.c.parent_id == parent)).all()
            session.execute(update(message_table).where(message_table.c.id == parent).values(thread_count=len(remaining)))
    for connection in _channel_records(session, "remotesyncconnection", json.loads(unseal_private(job.encrypted_request, "data-job", job.id))["channel_ids"]):
        table = _table("remotesyncconnection")
        session.execute(update(table).where(table.c.id == connection["id"]).values(encrypted_cursor=None))
    job.counts_json = _json(plan_counts(plan))


def erase_file_bytes(session, job):
    pending = session.exec(select(DataFileErasure).where(DataFileErasure.job_id == job.id, DataFileErasure.erased_at.is_(None))).all()
    for row in pending:
        if session.get(File, row.file_id): raise HTTPException(409, "A retained file prevents physical erasure")
        if not IDENTIFIER.fullmatch(row.file_id): raise HTTPException(409, "Invalid stored file identity")
        from .storage import storage_service
        storage_service.delete_file_sync(row.file_id)
        row.erased_at = datetime.utcnow(); session.add(row)


def process_job(session, job):
    token = job.lease_token
    locked = session.execute(update(DataControlJob).where(DataControlJob.id == job.id, DataControlJob.lease_token == token,
        DataControlJob.status.in_(["processing", "erasing_files"])).values(status=DataControlJob.status))
    if locked.rowcount != 1: raise HTTPException(409, "Data-control job lease changed")
    session.refresh(job)
    if job.status == "erasing_files":
        # The authorized metadata erasure has already committed. Completing its
        # immutable garbage queue must not depend on a later browser logout.
        erase_file_bytes(session, job)
        job.status = "completed"; job.finished_at = datetime.utcnow(); job.lease_expires_at = None; job.lease_token = None
        session.add(job); session.commit(); return
    request = json.loads(unseal_private(job.encrypted_request, "data-job", job.id))
    _job_actor(session, job, request["channel_ids"], purge=job.kind == "purge")
    if job.expires_at <= datetime.utcnow(): raise HTTPException(410, "The data-control authorization expired")
    if job.kind == "export":
        if request.get("governance_case_id"):
            from ..models.enterprise_governance import GovernanceCase, GovernanceCaseExport
            case = session.get(GovernanceCase, request["governance_case_id"], populate_existing=True)
            bound = session.get(GovernanceCaseExport, job.id, populate_existing=True)
            if (not case or case.workspace_id != job.workspace_id or case.status != "active"
                    or not bound or bound.case_id != case.id or bound.case_revision != case.revision):
                raise HTTPException(409, "Evidence preservation changed before collection")
        create_archive(session, job, request)
        _job_actor(session, job, request["channel_ids"])
        job.status = "ready"; job.finished_at = datetime.utcnow(); job.lease_token = None; job.lease_expires_at = None
        job.expires_at = datetime.utcnow() + timedelta(minutes=30)
        session.add(job); session.commit()
    else:
        if job.status != "erasing_files":
            policy = policy_for(session, job.workspace_id)
            if not policy.enabled or policy.hold or policy.revision != request["policy_revision"]:
                raise HTTPException(409, "Retention policy changed or is on hold")
            cutoff = datetime.fromisoformat(request["cutoff"])
            plan = build_plan(session, job.workspace_id, job.requested_by, request["channel_ids"], cutoff)
            if plan_digest(plan) != request["fingerprint"]: raise HTTPException(409, "Content changed after preview; create a fresh preview")
            apply_plan(session, job, plan)
            job.status = "erasing_files"; session.add(job); session.commit()
        erase_file_bytes(session, job)
        job.status = "completed"; job.finished_at = datetime.utcnow(); job.lease_expires_at = None
        session.add(job); session.commit()


def run_pending(session, limit=1, *, job_id=None):
    now = datetime.utcnow()
    # Defense in depth for a process terminated during a plaintext download.
    for path in list(_archive_root().glob("download-*.zip"))[:20]:
        try:
            if path.lstat().st_mtime < now.timestamp() - 1800: path.unlink(missing_ok=True)
        except OSError:
            pass
    # Expired downloadable copies are never retained indefinitely.
    expired = session.exec(select(DataControlJob).where(DataControlJob.kind == "export", DataControlJob.expires_at <= now, DataControlJob.status.in_(["ready", "failed"])).limit(20)).all()
    for row in expired:
        _artifact_path(row.id).unlink(missing_ok=True)
        row.status = "expired"; session.add(row)
    session.commit()
    due = or_(DataControlJob.status == "queued", and_(DataControlJob.status.in_(["processing", "erasing_files"]), or_(DataControlJob.lease_expires_at.is_(None), DataControlJob.lease_expires_at < now)))
    query = select(DataControlJob.id).where(due)
    if job_id: query = query.where(DataControlJob.id == job_id)
    identifiers = session.exec(query.order_by(DataControlJob.created_at).limit(min(max(limit, 1), 2))).all()
    processed = 0
    for identifier in identifiers:
        claimed = session.execute(update(DataControlJob).where(DataControlJob.id == identifier, due).values(
            status=case((DataControlJob.status == "erasing_files", "erasing_files"), else_="processing"),
            lease_token=secrets.token_hex(16), lease_expires_at=now + timedelta(minutes=5), attempts=DataControlJob.attempts + 1))
        session.commit()
        if claimed.rowcount != 1: continue
        job = session.get(DataControlJob, identifier, populate_existing=True)
        claim_token = job.lease_token
        try:
            process_job(session, job); processed += 1
        except Exception:
            session.rollback()
            job = session.get(DataControlJob, identifier, populate_existing=True)
            if job is None or job.lease_token != claim_token:
                # A stale worker cannot delete another worker's archive or
                # overwrite a successfully reclaimed job after its rollback.
                continue
            if job.kind == "export": _artifact_path(job.id).unlink(missing_ok=True)
            # Never return raw exceptions, file paths, payloads or credentials.
            job.failure = "Data-control action stopped; review current access, policy and the selected content before retrying."
            if job.status == "erasing_files" and job.attempts < 3:
                job.lease_expires_at = datetime.utcnow() + timedelta(minutes=1)
            else:
                job.status = "failed"; job.finished_at = datetime.utcnow(); job.lease_expires_at = None
            session.add(job); session.commit()
    return processed
