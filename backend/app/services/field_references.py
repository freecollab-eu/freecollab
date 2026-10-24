"""Explicit references inside encrypted work fields; never an access grant."""
import unicodedata

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_serializer, model_validator

from .message_references import TargetInput
from ..models.chat_productivity import ScheduledMessage  # noqa: F401 -- reference FK dependency


class FieldReferenceInput(TargetInput):
    # Work bodies can be longer than chat messages. Offsets remain UTF-16.
    start: int = Field(ge=0, le=50000)
    end: int = Field(ge=1, le=50000)


def normalize_field_references(texts, value):
    """Validate the actual field text, retaining only explicit nonempty maps."""
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 50 or any(key not in texts for key in value):
        raise ValueError("References must name an existing text field")
    result, count = {}, 0
    for key, items in value.items():
        if not isinstance(items, list) or len(items) > 50:
            raise ValueError("A work item supports at most fifty references")
        count += len(items)
        if count > 50:
            raise ValueError("A work item supports at most fifty references")
        text = texts[key]
        if not isinstance(text, str):
            raise ValueError("A referenced field needs text")
        offsets, cursor = {0: 0}, 0
        for index, char in enumerate(text):
            if 0xD800 <= ord(char) <= 0xDFFF:
                raise ValueError("Referenced text contains an invalid Unicode scalar")
            cursor += 2 if ord(char) > 0xFFFF else 1
            offsets[cursor] = index + 1
        references, previous = [], -1
        for item in items:
            ref = item if isinstance(item, FieldReferenceInput) else FieldReferenceInput.model_validate(item)
            if ref.start < previous or ref.end <= ref.start + 1 or ref.end - ref.start > 201 or ref.start not in offsets or ref.end not in offsets:
                raise ValueError("Reference ranges must be ordered, disjoint UTF-16 boundaries")
            start, end = offsets[ref.start], offsets[ref.end]
            label = text[start:end]
            before = text[start - 1] if start else ""
            after = text[end] if end < len(text) else ""
            if not label.startswith("@") or any(char in label for char in "\r\n\u2028\u2029"):
                raise ValueError("A reference must point to its displayed @label")
            if before and not (before.isspace() or before in "([{\"'“‘"):
                raise ValueError("A reference must start at a mention boundary")
            if after and (after == "_" or unicodedata.category(after)[0] in "LN"):
                raise ValueError("A reference must end at a mention boundary")
            references.append(ref.model_dump())
            previous = ref.end
        if references:
            result[key] = references
    return result


class ReferencedFields(BaseModel):
    """An optional sidecar in the existing content payload, not a new index."""
    field_references: dict[str, list[FieldReferenceInput]] | None = None

    def reference_texts(self):
        return {}

    @model_validator(mode="after")
    def validate_field_references(self):
        explicitly_supplied = "field_references" in self.model_fields_set
        normalized = normalize_field_references(self.reference_texts(), self.field_references)
        self.field_references = {key: [FieldReferenceInput.model_validate(ref) for ref in refs]
                                 for key, refs in normalized.items()} or None
        if not explicitly_supplied:
            self.__pydantic_fields_set__.discard("field_references")
        return self

    @model_serializer(mode="wrap")
    def omit_empty_field_references(self, handler):
        result = handler(self)
        if not result.get("field_references"):
            result.pop("field_references", None)
        return result


def preserve_referenced_text(value, names, *, checklist=False):
    """Legacy unreferenced fields still trim; reference offsets never shift."""
    if not isinstance(value, dict):
        return value
    result = dict(value)
    refs = value.get("field_references")
    refs = refs if isinstance(refs, dict) else {}
    for name in names:
        if isinstance(result.get(name), str) and not refs.get(name):
            result[name] = result[name].strip()
    if checklist and isinstance(value.get("checklist"), list):
        result["checklist"] = []
        for source in value["checklist"]:
            if not isinstance(source, dict):
                result["checklist"].append(source)
                continue
            item = dict(source)
            if isinstance(item.get("id"), str):
                item["id"] = item["id"].strip()
            if isinstance(item.get("title"), str) and not refs.get("checklist:" + str(item.get("id", ""))):
                item["title"] = item["title"].strip()
            result["checklist"].append(item)
    return result


def reference_texts_from_fields(fields):
    """Match typed field names to their actual text without inventing targets."""
    result = {key: fields[key] for key in ("title", "description", "body", "content", "text", "notes", "note", "location", "transcript")
              if isinstance(fields.get(key), str)}
    for item in fields.get("checklist", []):
        result["checklist:" + item["id"]] = item["title"]
    poll = fields.get("poll")
    if poll:
        result["poll.question"] = poll["question"]
        result.update({"poll.option:" + item["id"]: item["label"] for item in poll["options"]})
    return result


def require_field_reference_targets(session, channel_id, actor_id, fields, *, previous=None):
    """Recheck new targets; unchanged historical text does not grant access."""
    refs = fields.get("field_references")
    if not refs:
        return
    from .message_references import context, eligible_people, resolve_target
    from .workspaces import require_direct_message_writable
    session.expire_all()
    context(session, channel_id, actor_id)
    people = eligible_people(session, channel_id)
    texts = reference_texts_from_fields(fields)
    old_texts = reference_texts_from_fields(previous) if previous else {}
    old_refs = previous.get("field_references", {}) if previous else {}
    seen = set()
    for name, items in refs.items():
        # Only an exact field from this resource's latest authorized revision can
        # retain an unavailable target. Editing text or offsets checks it afresh.
        if name in old_texts and texts[name] == old_texts[name] and items == old_refs.get(name):
            continue
        for ref in items:
            key = (ref["kind"], ref["id"])
            if key in seen:
                continue
            seen.add(key)
            target = resolve_target(session, channel_id, actor_id, *key, people=people)
            if not target:
                raise HTTPException(404, "A referenced item is no longer available in this conversation")
            if ref["kind"] == "group" and not target.get("user_ids"):
                raise HTTPException(409, "This group has no current channel members")
    session.expire_all()
    context(session, channel_id, actor_id)
    require_direct_message_writable(session, channel_id, actor_id)
