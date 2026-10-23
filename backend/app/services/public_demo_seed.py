"""Sample content stored through the product's normal native models and codecs.

Only the separate disposable demo installation calls this seeder. It does not
commit, issue credentials, connect providers or maintain an alternative UI/API.
"""
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

from ..config import settings
from ..models.activity_feed import ActivityFeedPost, ActivityFeedRevision, ActivityFeedComment, ActivityFeedCommentRevision
from ..models.channel import Channel, ChannelMember
from ..models.channel_metadata import ChannelMetadata
from ..models.collaboration import Bookmark, ChannelPin, Decision
from ..models.document_organization import SharedDocumentStack, SharedDocumentPlacement, SharedDocumentRelation, SharedDocumentStackReference
from ..models.file import File
from ..models.file_version import FileDocument, FileVersion
from ..models.integration_content import IntegrationContent, IntegrationContentRevision
from ..models.message import Message
from ..models.message_references import MentionGroup, MentionGroupMember
from ..models.notification import Notification
from ..models.project_tasks import ProjectBoard, TaskCard
from ..models.reaction import Reaction
from ..models.read_receipt import ReadReceipt
from ..models.shared_documents import SharedDocument, SharedDocumentUpdate, SharedDocumentComment, SharedDocumentSnapshot
from ..models.user import User
from ..models.workspace import WorkspaceChannel, WorkspaceMember
from ..models.workflow import WorkflowDefinition, WorkflowRevision
from . import activity_feed, project_tasks, shared_documents
from .avatars import set_avatar_bytes
from .channel_metadata import seal_payload
from .chat_notifications import record_message_change
from .crypto import encrypt_message
from .integration_content import CalendarFields
from .storage import StorageService


# Generated once with the repository's pinned Yjs package. Runtime seeding needs
# no Node process; normal clients decode, edit and append to these real updates.
PAGES = (
    ("Project brief", "AQK4/pkIAAQBBXRpdGxlDVByb2plY3QgYnJpZWYEAQRib2R5iQIjIyBBIHF1aWV0ZXIgY291cnR5YXJkCgpDcmVhdGUgYSB3ZWxjb21pbmcgc2hhcmVkIHNwYWNlIGZvciB0aGUgbmVpZ2hib3VyaG9vZC4KCiMjIyBPdXIgZGlyZWN0aW9uCi0gV2FybSwgZHVyYWJsZSBtYXRlcmlhbHMKLSBSb29tIHRvIGdhdGhlciBhbmQgcm9vbSB0byBwYXVzZQotIEEgc2ltcGxlIGZpcnN0IHZlcnNpb24sIHNoYXBlZCBieSBmZWVkYmFjawoKIyMjIE9wZW4gcXVlc3Rpb24KV2hpY2ggbWF0ZXJpYWxzIHdvcmsgYmVzdCBmb3IgdGhpcyBzcGFjZT8KAA=="),
    ("Studio notes", "AQK5/pkIAAQBBXRpdGxlDFN0dWRpbyBub3RlcwQBBGJvZHnvASMjIEEgZmV3IHRoaW5ncyB3ZSBhZ3JlZWQKCi0gU3RhcnQgc21hbGwgYW5kIGtlZXAgZGVjaXNpb25zIHZpc2libGUuCi0gQ29sbGVjdCBxdWVzdGlvbnMgYmVmb3JlIHRoZSBuZXh0IHdhbGt0aHJvdWdoLgotIEdpdmUgZXZlcnkgdGFzayBhIGNsZWFyIG5leHQgc3RlcC4KCiMjIyBOZXh0IGNvbnZlcnNhdGlvbgpCcmluZyBvbmUgaWRlYSB0aGF0IG1ha2VzIHRoZSBjb3VydHlhcmQgZmVlbCBtb3JlIHdlbGNvbWluZy4KAA=="),
    ("Materials & sketches", "AQK6/pkIAAQBBXRpdGxlFE1hdGVyaWFscyAmIHNrZXRjaGVzBAEEYm9kecsBIyMgTWF0ZXJpYWxzIHRvIGJyaW5nIHRvZ2V0aGVyCgotIEEgd2FybSB0aW1iZXIgc2FtcGxlIGZvciB0aGUgbG9uZyB0YWJsZQotIEJyaWNrIGFuZCBzdG9uZSBjb2xvdXJzIGZyb20gdGhlIGNvdXJ0eWFyZAotIEEgc2tldGNoIG9mIHRoZSBvcGVuIGRvb3J3YXkKCkxlYXZlIHJvb20gZm9yIHRoZSB0ZWFtIHRvIGFkZCBhbm90aGVyIHBvc3NpYmlsaXR5LgoA"),
)


def _id():
    return uuid4().hex


ASSET_DIRECTORY = Path(__file__).resolve().parents[1] / "assets" / "public-demo"


def _seed_files(session, channel_id, owner_id, photographer_id, now, storage, file_ids):
    items = (
        ("courtyard-notes.md", "text/markdown", b"# A quieter courtyard\n\nA welcoming shared space for the neighbourhood.\n\n- Keep the doorway open and let the light reach in.\n- Use warm, durable materials.\n- Bring timber, brick and stone samples to the walkthrough.\n"),
        ("materials-checklist.csv", "text/csv", b"Material,Owner,Status\nTimber sample,Nora,To bring\nBrick and stone colours,Noor,To review\nDoorway sketch,Elias,Ready\n"),
        ("courtyard.png", "image/png", (ASSET_DIRECTORY / "courtyard.png").read_bytes()),
    )
    for index, (filename, content_type, content) in enumerate(items):
        file_id, document_id = _id(), _id()
        location = storage.save_file_sync(file_id, content)
        file_ids.append(file_id)
        created = now - timedelta(minutes=8 - index)
        session.add(File(id=file_id, channel_id=channel_id, uploader_id=photographer_id if index == 2 else owner_id, filename=filename,
                         content_type=content_type, size=len(content), storage_path=location,
                         encrypted=False, created_at=created))
        session.flush()
        session.add(FileDocument(id=document_id, channel_id=channel_id, owner_id=owner_id,
                                 current_file_id=file_id, version_count=1, created_at=created))
        session.flush()
        session.add(FileVersion(file_id=file_id, document_id=document_id, number=1))


def _seed_pages(session, channel_id, visitor, maja, source_id, now):
    documents = []
    for index, (title, encoded) in enumerate(PAGES):
        document_id, update_id = _id(), _id()
        fields = {"update_base64": shared_documents.canonical_update(encoded), "title": title}
        # The brief is the most recently updated page, so the ordinary shared
        # material preview opens it without any sample-name special case.
        created = now - timedelta(minutes=28 + index)
        document = SharedDocument(id=document_id, channel_id=channel_id, created_by=visitor,
            created_at=created, updated_at=created, update_count=1, stored_bytes=len(base64.b64decode(encoded)),
            summary_by=visitor, summary_update_id=update_id,
            summary_encrypted_payload=shared_documents.seal(channel_id, "summary", document_id, document_id, {"title": title}))
        session.add(document)
        session.flush()
        session.add(SharedDocumentUpdate(document_id=document_id, update_id=update_id, sequence=1,
            created_by=visitor, created_at=created, content_mode="server", payload_hash=shared_documents.fingerprint(fields, None),
            encrypted_payload=shared_documents.seal(channel_id, "update", document_id, update_id, fields),
            byte_size=len(base64.b64decode(encoded))))
        snapshot_id = _id()
        session.add(SharedDocumentSnapshot(id=snapshot_id, document_id=document_id, covers_sequence=1,
            created_by=visitor, created_at=created, content_mode="server",
            encrypted_payload=shared_documents.seal(channel_id, "snapshot", document_id, snapshot_id,
                {"name": "First draft", "update_base64": encoded})))
        documents.append(document)
    stack_id, generation, comment_id = _id(), _id(), _id()
    session.add(SharedDocumentStack(id=stack_id, channel_id=channel_id, created_by=visitor,
        created_at=now - timedelta(minutes=18), updated_at=now - timedelta(minutes=18), generation=generation,
        encrypted_payload=shared_documents.seal(channel_id, "stack", stack_id, generation, {"title": "Reference material"})))
    session.flush()
    for document in documents[:2]:
        session.add(SharedDocumentPlacement(document_id=document.id, channel_id=channel_id,
            stack_id=stack_id, updated_by=visitor, updated_at=now - timedelta(minutes=16)))
    left, right = sorted(document.id for document in documents[:2])
    session.add(SharedDocumentRelation(id=_id(), channel_id=channel_id, first_document_id=left,
        second_document_id=right, created_by=visitor, created_at=now - timedelta(minutes=15)))
    fields = {"text": "The Studio notes belong beside this brief. Let’s bring the material samples and one open question to the walkthrough."}
    session.add(SharedDocumentComment(id=comment_id, document_id=documents[0].id, created_by=maja,
        created_at=now - timedelta(minutes=12), content_mode="server", payload_hash=shared_documents.fingerprint(fields, None),
        encrypted_payload=shared_documents.seal(channel_id, "comment", documents[0].id, comment_id, fields)))
    session.add(SharedDocumentStackReference(id=_id(), channel_id=channel_id, stack_id=stack_id,
        message_id=source_id, created_by=visitor, created_at=now - timedelta(minutes=10)))
    return {"document_ids": [document.id for document in documents], "stack_id": stack_id, "comment_id": comment_id}


def _seed_work(session, channel_id, visitor, maja, noor, source_id, now, file_ids):
    from ..api.decisions import DecisionContent, new_revision as decision_revision
    decision = Decision(id=_id(), channel_id=channel_id, created_by=visitor, created_at=now - timedelta(minutes=17))
    session.add(decision)
    session.flush()
    session.add(decision_revision(decision, DecisionContent(title="Keep the courtyard open",
        content="Use warm, durable materials and keep the courtyard open.",
        status="accepted", responsible_user_id=visitor, decision_date=now.date(), source_message_id=source_id), 1, visitor))
    project = ProjectBoard(id=_id(), channel_id=channel_id, created_by=visitor,
        created_at=now - timedelta(minutes=25), updated_at=now - timedelta(minutes=20))
    session.add(project)
    session.flush()
    fields = project_tasks.ProjectFields(title="Courtyard project", description="A welcoming shared space, shaped together through sketches, materials and a team walkthrough.", color="sage").model_dump(mode="json")
    write = project_tasks.SnapshotWrite(id=project.id, fields=fields)
    revision = project_tasks.new_revision("project", project, write, fields, visitor)
    project.revision = 1
    session.add(project)
    session.add(revision)
    task_ids = []
    tasks = (
        ("Review the courtyard materials", "Compare warm timber, brick and stone samples for the shared space.", "doing", maja, "high"),
        ("Plan a short team walkthrough", "Bring the team together in the courtyard and collect the remaining questions.", "todo", noor, "normal"),
        ("Add open questions to the brief", "Keep the shared brief, studio notes and material checklist together.", "done", visitor, "normal"),
    )
    session.flush()
    for index, (title, description, status, assignee, priority) in enumerate(tasks):
        task = TaskCard(id=_id(), project_id=project.id, channel_id=channel_id, created_by=visitor,
            created_at=now - timedelta(minutes=23 - index), updated_at=now - timedelta(minutes=9 - index))
        session.add(task)
        session.flush()
        fields = project_tasks.TaskFields(title=title, description=description, status=status,
            priority=priority, assignee_user_id=assignee, due_date=now.date() + timedelta(days=3 + index),
            checklist=[project_tasks.ChecklistItem(id=_id(), title="Bring one question to the walkthrough", done=status == "done")],
            file_ids=[file_ids[1]] if index == 2 else [], decision_ids=[decision.id] if index == 0 else []).model_dump(mode="json")
        write = project_tasks.SnapshotWrite(id=task.id, fields=fields)
        revision = project_tasks.new_revision("task", task, write, fields, visitor)
        task.revision = 1
        session.add(task)
        session.add(revision)
        task_ids.append(task.id)
    return {"project_id": project.id, "task_ids": task_ids, "decision_id": decision.id}


def _seed_feed_calendar(session, channel_id, visitor, maja, now):
    post = ActivityFeedPost(id=_id(), channel_id=channel_id, created_by=maja,
        created_at=now - timedelta(hours=1), updated_at=now - timedelta(hours=1), revision=1)
    session.add(post)
    session.flush()
    fields = activity_feed.PostFields(title="A quieter courtyard, together", body="The new sketch is ready. Add a question to the Project brief, compare the material samples, and join us for a short walkthrough tomorrow.").model_dump(mode="json")
    session.add(ActivityFeedRevision(post_id=post.id, revision=1, created_by=maja,
        created_at=post.created_at, content_mode="server", encrypted_payload=activity_feed.seal_fields(channel_id, "post", post.id, 1, fields)))
    comment = ActivityFeedComment(id=_id(), post_id=post.id, created_by=visitor, revision=1,
        created_at=now - timedelta(minutes=35), updated_at=now - timedelta(minutes=35))
    session.add(comment)
    session.flush()
    fields = activity_feed.CommentFields(body="I’ll bring the timber samples. Let’s see how they feel beside the brick in the afternoon light.").model_dump(mode="json")
    session.add(ActivityFeedCommentRevision(comment_id=comment.id, revision=1, created_by=visitor,
        created_at=comment.created_at, content_mode="server",
        encrypted_payload=activity_feed.seal_fields(channel_id, "comment", comment.id, 1, fields)))
    start = (now + timedelta(days=1)).replace(hour=13, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    fields = CalendarFields(title="Courtyard walkthrough", description="Bring the material samples and one question. We’ll look at the open doorway, the long table and the afternoon light.",
        location="The courtyard", starts_at=start.isoformat(), ends_at=(start + timedelta(minutes=30)).isoformat(),
        timezone="UTC").model_dump(mode="json")
    event = IntegrationContent(id=_id(), channel_id=channel_id, kind="calendar", created_by=visitor,
        created_at=now - timedelta(minutes=22))
    session.add(event)
    session.flush()
    payload = {"id": event.id, "kind": "calendar", "revision": 1, "fields": fields, "deleted": False}
    session.add(IntegrationContentRevision(item_id=event.id, revision=1, created_by=visitor,
        created_at=event.created_at, content_mode="server",
        encrypted_payload=encrypt_message(json.dumps(payload, ensure_ascii=False), channel_id)))
    return {"feed_post_id": post.id, "calendar_event_id": event.id}


def _seed_workflow(session, workspace_id, channel_id, visitor, project_id, now):
    from . import workflow
    fields = workflow.DefinitionFields(name="From review to next step", trigger="manual", steps=[
        workflow.Step(kind="approval", recipients=[visitor], quorum=1, timeout_minutes=60,
                      reminder_minutes=15, max_reminders=1),
        workflow.Step(kind="create_task", project_id=project_id,
                      task_title="Bring material samples to the walkthrough"),
    ])
    workflow.validate_fields(session, workspace_id, channel_id, fields)
    definition = WorkflowDefinition(id=_id(), workspace_id=workspace_id, channel_id=channel_id,
        created_by=visitor, granted_by=visitor, current_revision=1, trigger="manual", enabled=True,
        created_at=now - timedelta(minutes=14), updated_at=now - timedelta(minutes=14))
    session.add(definition)
    session.flush()
    payload = {**fields.model_dump(mode="json"), "workflow_id": definition.id, "revision": 1}
    session.add(WorkflowRevision(workflow_id=definition.id, revision=1, created_by=visitor,
        created_at=definition.created_at,
        encrypted_definition=encrypt_message(json.dumps(payload, separators=(",", ":")), "workflow:" + channel_id)))
    return definition.id


def seed_demo_workspace(session, *, visit_id: str, user_id: str, workspace_id: str,
                        participant_ids: list[str], now: datetime) -> dict:
    """Seed one visitor's private workspace; caller owns the transaction."""
    if not settings.demo_instance or len(participant_ids) != 4 or participant_ids[0] != user_id:
        raise ValueError("Sample content requires its own demo visit")
    if any(session.get(WorkspaceMember, (workspace_id, person)) is None for person in participant_ids):
        raise ValueError("Sample collaborators must belong to this visit's workspace")
    visitor, maja, noor, elias = participant_ids
    # These collaborators have their own real user rows in this visit. Names
    # and portraits are ordinary product data, never a client-side identity map.
    for identifier, name in zip(participant_ids, ("Nora", "Maja", "Noor", "Elias")):
        person = session.get(User, identifier)
        if person is None:
            raise ValueError("Sample collaborators must exist before their content")
        person.name = name
        session.add(person)
    channels = []
    for index, (name, description) in enumerate((
        ("Courtyard", "Ideas, materials and a quieter courtyard."),
        ("Design", "Sketches, warm materials and room to think."),
        ("Planning", "A shared plan and the next small step."),
    )):
        channel = Channel(id=_id(), name=name, type="group", created_at=now - timedelta(days=3, minutes=index))
        session.add(channel)
        session.flush()
        session.add(WorkspaceChannel(channel_id=channel.id, workspace_id=workspace_id))
        for person in participant_ids:
            session.add(ChannelMember(channel_id=channel.id, user_id=person, role="admin" if person == visitor else "member",
                joined_at=now - timedelta(days=3)))
        session.add(ChannelMetadata(channel_id=channel.id, created_by=visitor, updated_by=visitor,
            created_at=channel.created_at, updated_at=channel.created_at,
            encrypted_payload=seal_payload(channel.id, 1, {"name": name, "description": description})))
        channels.append(channel)
    session.flush()
    studio, design, table = channels
    mention_group = MentionGroup(channel_id=studio.id, name='Design team', name_key='design team', created_by=visitor,
        created_at=now - timedelta(days=2), updated_at=now - timedelta(days=2))
    session.add(mention_group); session.flush()
    for identifier in (maja, noor, elias):
        session.add(MentionGroupMember(group_id=mention_group.id, user_id=identifier))
    messages = []
    conversation = (
        (maja, "Shall we open up to the courtyard?"),
        (noor, "Yes. Then the light reaches all the way in."),
        (visitor, "I’ll bring the material samples tomorrow."),
        (elias, "I’ve shared the new sketch here. The long table can sit by the open doors."),
        (maja, "Use warm, durable materials and keep the courtyard open."),
        (noor, "The Project brief and Studio notes are together in Reference material. I’ve left the doorway question open for our walkthrough."),
    )
    for index, (author, text) in enumerate(conversation):
        message = Message(id=_id(), channel_id=studio.id, author_id=author,
            content=encrypt_message(text, studio.id), encrypted=True, created_at=now - timedelta(minutes=44 - 5 * index))
        session.add(message)
        messages.append(message)
    session.flush()
    messages[0].thread_count = 2
    session.add(messages[0])
    for index, (author, text) in enumerate(((visitor, "The long table could sit beside the doors, with a little room left to pause."),
                                          (maja, "Yes. Let’s try the timber sample there before we decide."))):
        message = Message(id=_id(), channel_id=studio.id, author_id=author, parent_id=messages[0].id,
            content=encrypt_message(text, studio.id), encrypted=True, created_at=now - timedelta(minutes=42 - index))
        session.add(message)
        messages.append(message)
    for channel, author, text in ((design, elias, "Warm materials, generous space and a little less noise."),
                                  (design, maja, "The timber sample brings out the warm colour of the brick."),
                                  (table, noor, "One shared plan, with a clear next step for everyone."),
                                  (table, visitor, "I’ll bring the samples. Shall we meet in the courtyard at one?")):
        message = Message(id=_id(), channel_id=channel.id, author_id=author,
            content=encrypt_message(text, channel.id), encrypted=True, created_at=now - timedelta(minutes=30))
        session.add(message)
        messages.append(message)
    session.flush()
    for message in messages:
        record_message_change(session, message, "created")
    session.add(Reaction(id=_id(), message_id=messages[0].id, user_id=visitor, emoji="🌿"))
    session.add(Reaction(id=_id(), message_id=messages[4].id, user_id=maja, emoji="👍"))
    session.add(Reaction(id=_id(), message_id=messages[4].id, user_id=noor, emoji="👍"))
    session.add(ChannelPin(channel_id=studio.id, item_id=messages[0].id, created_by=visitor))
    session.add(Bookmark(user_id=visitor, channel_id=studio.id, item_id=messages[4].id))
    session.add(Notification(user_id=visitor, type="reply", actor_id=maja,
        entity_type="message", entity_id=messages[7].id, title="Maja replied to your conversation",
        body="Open FreeCollab to view this activity.",
        action_url="#channel=" + studio.id + "&message=" + messages[7].id,
        created_at=now - timedelta(minutes=41)))
    for person in (maja, noor, elias):
        session.add(ReadReceipt(channel_id=studio.id, user_id=person, last_read_message_id=messages[5].id,
                                read_at=now - timedelta(minutes=1)))
    storage, file_ids = StorageService(settings.storage_path), []
    try:
        _seed_files(session, studio.id, visitor, elias, now, storage, file_ids)
        examples = {"message_id": messages[0].id, "own_message_id": messages[2].id, "reply_id": messages[6].id,
                    "channel_ids": [channel.id for channel in channels], 'mention_group_id': mention_group.id}
        examples.update(_seed_pages(session, studio.id, visitor, maja, messages[0].id, now))
        examples.update(_seed_work(session, studio.id, visitor, maja, noor, messages[4].id, now, file_ids))
        examples["workflow_id"] = _seed_workflow(session, workspace_id, studio.id, visitor, examples["project_id"], now)
        examples.update(_seed_feed_calendar(session, studio.id, visitor, maja, now))
        for identifier, portrait in zip(participant_ids, ("nora", "maja", "noor", "elias")):
            avatar = set_avatar_bytes(session, identifier, (ASSET_DIRECTORY / (portrait + ".png")).read_bytes())
            file_ids.append(avatar.blob_id)
        session.flush()
        from .conversation_work import attach_reference
        reference_ids = []
        for source_id, kind, resource_id in (
            (messages[0].id, "document", examples["document_ids"][0]),
            (messages[2].id, "task", examples["task_ids"][0]),
            (messages[4].id, "decision", examples["decision_id"]),
        ):
            reference, _, _ = attach_reference(session, studio.id, visitor, source_id, kind, resource_id)
            reference_ids.append(reference.id)
        examples["conversation_work_reference_ids"] = reference_ids
        return {"channel_id": studio.id, "example_ids": examples, "file_ids": file_ids}
    except BaseException:
        for file_id in file_ids:
            storage.delete_file_sync(file_id)
        raise
