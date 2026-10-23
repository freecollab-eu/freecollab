"""Sample content stored through the product's normal native models and codecs.

Only the separate disposable demo installation calls this seeder. It does not
commit, issue credentials, connect providers or maintain an alternative UI/API.
"""
import base64
from datetime import datetime, timedelta, timezone
import json
import struct
from uuid import uuid4
import zlib

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
from ..models.notification import Notification
from ..models.project_tasks import ProjectBoard, TaskCard
from ..models.reaction import Reaction
from ..models.read_receipt import ReadReceipt
from ..models.shared_documents import SharedDocument, SharedDocumentUpdate, SharedDocumentComment, SharedDocumentSnapshot
from ..models.workspace import WorkspaceChannel, WorkspaceMember
from ..models.workflow import WorkflowDefinition, WorkflowRevision
from . import activity_feed, project_tasks, shared_documents
from .channel_metadata import seal_payload
from .chat_notifications import record_message_change
from .crypto import encrypt_message
from .integration_content import CalendarFields
from .storage import StorageService


# Generated once with the repository's pinned Yjs package. Runtime seeding needs
# no Node process; normal clients decode, edit and append to these real updates.
PAGES = (
    ("Launch brief", "AQKkn0kABAEFdGl0bGUMTGF1bmNoIGJyaWVmBAEEYm9keaUDIyBBIHF1aWV0ZXIgd2F5IHRvIHdvcmsKCkJyaW5nIGNvbnZlcnNhdGlvbiwgZGVjaXNpb25zIGFuZCBtYXRlcmlhbCBpbnRvIG9uZSBzaGFyZWQgcGxhY2UuCgojIyBXaGF0IHdlIGFyZSBtYWtpbmcKQSBzdW1tZXIgbGF1bmNoIHRoYXQgZmVlbHMgY2xlYXIsIHVzZWZ1bCBhbmQgaHVtYW4uIFN0YXJ0IHdpdGggdGhlIHdvcmsgcGVvcGxlIGFscmVhZHkgc2hhcmUuCgojIyBGaXJzdCByZWxlYXNlCi0gT25lIGNvbnNpZGVyZWQgc3RvcnkKLSBBIHVzZWZ1bCBleGFtcGxlIHdvcmtzcGFjZQotIEEgY2FsbSB3ZWxjb21lIGZvciB0aGUgdGVhbQoKIyMgTmV4dApNaXJhIHdpbGwgc2hhcGUgdGhlIHZpc3VhbCBzdG9yeS4gRWxpYXMgd2lsbCB0ZXN0IHRoZSBmaXJzdC1ydW4gZmxvdy4gQWxleCB3aWxsIGdhdGhlciB0aGUgZmluYWwgbm90ZXMuCgA="),
    ("Design decisions", "AQKw1GEABAEFdGl0bGUQRGVzaWduIGRlY2lzaW9ucwQBBGJvZHn0AiMgR2l2ZSB0aGUgd29yayByb29tIHRvIGJyZWF0aGUKClVzZSBhIHdhcm0gY2FudmFzLCByZWFkYWJsZSB0eXBlIGFuZCBhIHNtYWxsIG51bWJlciBvZiBkZWxpYmVyYXRlIGNvbG9ycy4KCiMjIERlY2lzaW9ucwotIEtlZXAgdGhlIGNvbnZlcnNhdGlvbiB2aXNpYmxlIHdoZW4gb3BlbmluZyBzaGFyZWQgd29yay4KLSBMZXQgZXZlcnlvbmUgY2hvb3NlIHRoZWlyIG93biBjb2xvcnMgYW5kIHR5cGUuCi0gTWFrZSBjaGFuZ2VzIGV4cGxpY2l0IGFuZCBlYXN5IHRvIHVuZG8uCgojIyBPcGVuIHF1ZXN0aW9uCkhvdyBtaWdodCB0aGUgZmlyc3QgdmlzaXQgZmVlbCBtb3JlIGxpa2Ugam9pbmluZyBhIHRhYmxlIHRoYW4gY29uZmlndXJpbmcgc29mdHdhcmU/CgA="),
    ("Reading list", "AQKkn0kABAEFdGl0bGUMUmVhZGluZyBsaXN0BAEEYm9kec4BIyBUaGluZ3Mgd29ydGggcmV0dXJuaW5nIHRvCgojIyBUaGlzIHdlZWsKLSBUaGUgbGF1bmNoIGJyaWVmCi0gTm90ZXMgZnJvbSB0aGUgZmlyc3QtcnVuIHJldmlldwotIFRoZSBjb2xvciBzdHVkeSBzaGFyZWQgaW4gU3R1ZGlvCgpBZGQgYSB0aG91Z2h0IGluIHRoZSBjb21tZW50cywgb3IgY29ubmVjdCB0aGlzIHBhZ2UgdG8gYSByZWxhdGVkIGRvY3VtZW50LgoA"),
)


def _id():
    return uuid4().hex


def _color_study():
    """A small, safe PNG attachment; no fetched image or rendering dependency."""
    width, height = 640, 400
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            color = (238, 237, 228)
            if 34 <= x < 272 and 34 <= y < 366:
                color = (77, 94, 70)
            elif 294 <= x < 606 and 34 <= y < 208:
                color = (200, 155, 126)
            elif 294 <= x < 438 and 230 <= y < 366:
                color = (219, 206, 171)
            elif 460 <= x < 606 and 230 <= y < 366:
                color = (110, 131, 136)
            if (x - 152) ** 2 + (y - 178) ** 2 < 68 ** 2:
                color = (224, 226, 209)
            rows.extend(color)
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def _seed_files(session, channel_id, owner_id, now, storage, file_ids):
    items = (
        ("field-notes.md", "text/markdown", b"# Notes from the table\n\nA good first visit should feel like joining the work already happening.\n\n- Keep the conversation in view.\n- Put the useful next step nearby.\n- Give every change a clear way back.\n"),
        ("launch-checklist.csv", "text/csv", b"Work,Owner,Status\nLaunch story,Mira,In progress\nFirst-run review,Elias,To do\nShared brief,Alex,Ready\n"),
        ("color-study.png", "image/png", _color_study()),
    )
    for index, (filename, content_type, content) in enumerate(items):
        file_id, document_id = _id(), _id()
        location = storage.save_file_sync(file_id, content)
        file_ids.append(file_id)
        created = now - timedelta(minutes=8 - index)
        session.add(File(id=file_id, channel_id=channel_id, uploader_id=owner_id, filename=filename,
                         content_type=content_type, size=len(content), storage_path=location,
                         encrypted=False, created_at=created))
        session.flush()
        session.add(FileDocument(id=document_id, channel_id=channel_id, owner_id=owner_id,
                                 current_file_id=file_id, version_count=1, created_at=created))
        session.flush()
        session.add(FileVersion(file_id=file_id, document_id=document_id, number=1))


def _seed_pages(session, channel_id, visitor, mira, source_id, now):
    documents = []
    for index, (title, encoded) in enumerate(PAGES):
        document_id, update_id = _id(), _id()
        fields = {"update_base64": shared_documents.canonical_update(encoded), "title": title}
        created = now - timedelta(minutes=28 - index)
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
        encrypted_payload=shared_documents.seal(channel_id, "stack", stack_id, generation, {"title": "Launch"})))
    session.flush()
    for document in documents[:2]:
        session.add(SharedDocumentPlacement(document_id=document.id, channel_id=channel_id,
            stack_id=stack_id, updated_by=visitor, updated_at=now - timedelta(minutes=16)))
    left, right = sorted(document.id for document in documents[:2])
    session.add(SharedDocumentRelation(id=_id(), channel_id=channel_id, first_document_id=left,
        second_document_id=right, created_by=visitor, created_at=now - timedelta(minutes=15)))
    fields = {"text": "The Design decisions page belongs beside this brief. I would keep the first story small and useful."}
    session.add(SharedDocumentComment(id=comment_id, document_id=documents[0].id, created_by=mira,
        created_at=now - timedelta(minutes=12), content_mode="server", payload_hash=shared_documents.fingerprint(fields, None),
        encrypted_payload=shared_documents.seal(channel_id, "comment", documents[0].id, comment_id, fields)))
    session.add(SharedDocumentStackReference(id=_id(), channel_id=channel_id, stack_id=stack_id,
        message_id=source_id, created_by=visitor, created_at=now - timedelta(minutes=10)))
    return {"document_ids": [document.id for document in documents], "stack_id": stack_id, "comment_id": comment_id}


def _seed_work(session, channel_id, visitor, mira, elias, source_id, now, file_ids):
    from ..api.decisions import DecisionContent, new_revision as decision_revision
    decision = Decision(id=_id(), channel_id=channel_id, created_by=visitor, created_at=now - timedelta(minutes=17))
    session.add(decision)
    session.flush()
    session.add(decision_revision(decision, DecisionContent(title="Keep the first story focused",
        content="Start with one clear launch story and an example workspace. Gather feedback before adding another layer.",
        status="accepted", responsible_user_id=visitor, decision_date=now.date(), source_message_id=source_id), 1, visitor))
    project = ProjectBoard(id=_id(), channel_id=channel_id, created_by=visitor,
        created_at=now - timedelta(minutes=25), updated_at=now - timedelta(minutes=20))
    session.add(project)
    session.flush()
    fields = project_tasks.ProjectFields(title="Summer launch", description="A calm, useful introduction to the way we work together.", color="sage").model_dump(mode="json")
    write = project_tasks.SnapshotWrite(id=project.id, fields=fields)
    revision = project_tasks.new_revision("project", project, write, fields, visitor)
    project.revision = 1
    session.add(project)
    session.add(revision)
    task_ids = []
    tasks = (
        ("Shape the launch story", "Choose the three things someone should understand on their first visit.", "doing", mira, "high"),
        ("Walk through the first visit", "Try the welcome flow and note anything that asks for too much explanation.", "todo", elias, "normal"),
        ("Bring the brief together", "Keep the shared brief, design decisions and checklist in one useful place.", "done", visitor, "normal"),
    )
    session.flush()
    for index, (title, description, status, assignee, priority) in enumerate(tasks):
        task = TaskCard(id=_id(), project_id=project.id, channel_id=channel_id, created_by=visitor,
            created_at=now - timedelta(minutes=23 - index), updated_at=now - timedelta(minutes=9 - index))
        session.add(task)
        session.flush()
        fields = project_tasks.TaskFields(title=title, description=description, status=status,
            priority=priority, assignee_user_id=assignee, due_date=now.date() + timedelta(days=3 + index),
            checklist=[project_tasks.ChecklistItem(id=_id(), title="Bring notes to the launch review", done=status == "done")],
            file_ids=[file_ids[1]] if index == 2 else [], decision_ids=[decision.id] if index == 0 else []).model_dump(mode="json")
        write = project_tasks.SnapshotWrite(id=task.id, fields=fields)
        revision = project_tasks.new_revision("task", task, write, fields, visitor)
        task.revision = 1
        session.add(task)
        session.add(revision)
        task_ids.append(task.id)
    return {"project_id": project.id, "task_ids": task_ids, "decision_id": decision.id}


def _seed_feed_calendar(session, channel_id, visitor, mira, now):
    post = ActivityFeedPost(id=_id(), channel_id=channel_id, created_by=mira,
        created_at=now - timedelta(hours=1), updated_at=now - timedelta(hours=1), revision=1)
    session.add(post)
    session.flush()
    fields = activity_feed.PostFields(title="A small launch, made together", body="The brief is ready to shape. Add a thought, pick up a task, or put a useful reference in the Launch stack.").model_dump(mode="json")
    session.add(ActivityFeedRevision(post_id=post.id, revision=1, created_by=mira,
        created_at=post.created_at, content_mode="server", encrypted_payload=activity_feed.seal_fields(channel_id, "post", post.id, 1, fields)))
    comment = ActivityFeedComment(id=_id(), post_id=post.id, created_by=visitor, revision=1,
        created_at=now - timedelta(minutes=35), updated_at=now - timedelta(minutes=35))
    session.add(comment)
    session.flush()
    fields = activity_feed.CommentFields(body="The pieces are in place. Let’s make the first visit feel easy.").model_dump(mode="json")
    session.add(ActivityFeedCommentRevision(comment_id=comment.id, revision=1, created_by=visitor,
        created_at=comment.created_at, content_mode="server",
        encrypted_payload=activity_feed.seal_fields(channel_id, "comment", comment.id, 1, fields)))
    start = (now + timedelta(days=1)).replace(hour=13, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    fields = CalendarFields(title="Launch review", description="Bring the story, first-run notes and one useful question.",
        location="The studio table", starts_at=start.isoformat(), ends_at=(start + timedelta(minutes=30)).isoformat(),
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
                      task_title="Follow up after the launch review"),
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
    visitor, mira, elias, noa = participant_ids
    channels = []
    for index, (name, description) in enumerate((
        ("Studio", "Conversation, material and small next steps for our summer launch."),
        ("Design notes", "A place for the details that make the whole feel natural."),
        ("Around the table", "Good questions, quiet discoveries and the rest of the day."),
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
    messages = []
    conversation = (
        (mira, "What if our launch felt as calm as the product?"),
        (elias, "One clear story. A useful place to begin. Room for people to find their own way."),
        (visitor, "I have brought the Launch brief and Design decisions together. The Launch stack should keep the useful pieces close."),
        (noa, "The color study is in Files. I like the warm canvas with a little forest green."),
        (mira, "Let’s keep the first story focused. We can bring the next layer in after the review."),
        (elias, "I’ll walk through the first visit. Leave any small questions in the brief; they are usually the useful ones."),
    )
    for index, (author, text) in enumerate(conversation):
        message = Message(id=_id(), channel_id=studio.id, author_id=author,
            content=encrypt_message(text, studio.id), encrypted=True, created_at=now - timedelta(minutes=44 - 5 * index))
        session.add(message)
        messages.append(message)
    session.flush()
    messages[0].thread_count = 2
    session.add(messages[0])
    for index, (author, text) in enumerate(((visitor, "Yes. Make the next useful step visible, then let the work do the explaining."),
                                          (mira, "That is the thread I want the visual story to follow."))):
        message = Message(id=_id(), channel_id=studio.id, author_id=author, parent_id=messages[0].id,
            content=encrypt_message(text, studio.id), encrypted=True, created_at=now - timedelta(minutes=42 - index))
        session.add(message)
        messages.append(message)
    for channel, author, text in ((design, mira, "A little less interface. A little more room for the work."),
                                  (design, visitor, "Keep the type generous and the actions close to what they change."),
                                  (table, noa, "A good question for the afternoon: what could we make easier to understand?"),
                                  (table, elias, "The first welcome. It should feel like there is already a place for you at the table.")):
        message = Message(id=_id(), channel_id=channel.id, author_id=author,
            content=encrypt_message(text, channel.id), encrypted=True, created_at=now - timedelta(minutes=30))
        session.add(message)
        messages.append(message)
    session.flush()
    for message in messages:
        record_message_change(session, message, "created")
    session.add(Reaction(id=_id(), message_id=messages[0].id, user_id=visitor, emoji="🌿"))
    session.add(Reaction(id=_id(), message_id=messages[4].id, user_id=mira, emoji="👍"))
    session.add(Reaction(id=_id(), message_id=messages[4].id, user_id=elias, emoji="👍"))
    session.add(ChannelPin(channel_id=studio.id, item_id=messages[0].id, created_by=visitor))
    session.add(Bookmark(user_id=visitor, channel_id=studio.id, item_id=messages[4].id))
    session.add(Notification(user_id=visitor, type="reply", actor_id=mira,
        entity_type="message", entity_id=messages[7].id, title="Mira replied to your conversation",
        body="Open FreeCollab to view this activity.",
        action_url="#channel=" + studio.id + "&message=" + messages[7].id,
        created_at=now - timedelta(minutes=41)))
    for person in (mira, elias, noa):
        session.add(ReadReceipt(channel_id=studio.id, user_id=person, last_read_message_id=messages[5].id,
                                read_at=now - timedelta(minutes=1)))
    storage, file_ids = StorageService(settings.storage_path), []
    try:
        _seed_files(session, studio.id, visitor, now, storage, file_ids)
        examples = {"message_id": messages[0].id, "own_message_id": messages[2].id, "reply_id": messages[6].id,
                    "channel_ids": [channel.id for channel in channels]}
        examples.update(_seed_pages(session, studio.id, visitor, mira, messages[0].id, now))
        examples.update(_seed_work(session, studio.id, visitor, mira, elias, messages[4].id, now, file_ids))
        examples["workflow_id"] = _seed_workflow(session, workspace_id, studio.id, visitor, examples["project_id"], now)
        examples.update(_seed_feed_calendar(session, studio.id, visitor, mira, now))
        session.flush()
        return {"channel_id": studio.id, "example_ids": examples, "file_ids": file_ids}
    except BaseException:
        for file_id in file_ids:
            storage.delete_file_sync(file_id)
        raise
