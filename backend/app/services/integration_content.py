"""Validation for external links and calendar payloads. No remote URL fetching."""
from datetime import date, datetime, timezone
import ipaddress
import re
from urllib.parse import unquote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator
from typing import Literal
from .field_references import ReferencedFields, preserve_referenced_text


def document_url(provider: str, value: str) -> str:
    """Validate provider identity before displaying an external navigation link.

    Queries and fragments are deliberately excluded: OAuth credentials and
    account tokens must never be smuggled into stored document URLs. Standard
    share links work without tracking query parameters; private access still
    requires authentication at the destination.
    """
    if len(value) > 4096 or any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise ValueError("Use a valid HTTPS document URL")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        raise ValueError("Use a valid HTTPS document URL") from None
    if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
            or port not in (None, 443) or not host or parsed.query or parsed.fragment
            or "\\" in value or "%" in host or host.endswith(".")):
        raise ValueError("Use an HTTPS document URL without credentials, query parameters or a fragment")
    try:
        host = host.encode("idna").decode("ascii").lower()
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Document URLs must use a provider hostname")
    if "." not in host or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
        raise ValueError("Document URLs must use a provider hostname")
    path = unquote(parsed.path)
    if any(ord(char) < 32 or ord(char) == 127 for char in path) or "\\" in path:
        raise ValueError("Invalid document path")
    if provider == "google_drive":
        valid = ((host == "drive.google.com" and re.match(r"^/(?:file/d/|drive/(?:u/\d+/)?folders/)[A-Za-z0-9_-]+", path))
                 or (host == "docs.google.com" and re.match(r"^/(?:document|spreadsheets|presentation|forms|drawings)/d/[A-Za-z0-9_-]+", path)))
    elif provider == "sharepoint":
        valid = (host.endswith(".sharepoint.com") or host.endswith(".sharepoint.de")) and path not in ("", "/")
    elif provider == "nextcloud":
        valid = bool(re.match(r"^/(?:index\.php/)?(?:s/[A-Za-z0-9_-]+|f/\d+|apps/files(?:/|$))", path))
    else:
        valid = False
    if not valid:
        raise ValueError("The URL does not match the selected document provider")
    return urlunsplit(("https", host, parsed.path, "", ""))


class DocumentFields(ReferencedFields):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    provider: Literal["nextcloud", "google_drive", "sharepoint"]
    url: str = Field(min_length=1, max_length=4096)
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="before")
    @classmethod
    def normalize_text(cls, value):
        return preserve_referenced_text(value, ("title",))

    def reference_texts(self):
        return {"title": self.title, "notes": self.notes}

    @model_validator(mode="after")
    def validate_fields(self):
        if not self.title.strip():
            raise ValueError("A document needs a title")
        self.url = document_url(self.provider, self.url)
        return self


class CalendarFields(ReferencedFields):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    location: str = Field(default="", max_length=1000)
    starts_at: str = Field(min_length=1, max_length=64)
    ends_at: str = Field(min_length=1, max_length=64)
    all_day: bool = False
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    reminder_minutes: int | None = Field(default=None, ge=0, le=10080)
    external_uid: str | None = Field(default=None, max_length=255)
    recurrence: "CalendarRecurrence | None" = None
    exceptions: list["CalendarException"] = Field(default_factory=list, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def normalize_text(cls, value):
        return preserve_referenced_text(value, ("title",))

    def reference_texts(self):
        return {"title": self.title, "description": self.description, "location": self.location}

    @model_serializer(mode="wrap")
    def preserve_single_event_shape(self, handler):
        # Existing sync fingerprints must not change merely on upgrade. Keep
        # the released standalone payload shape when no new feature is used.
        result = handler(self)
        if not self.field_references:
            result.pop("field_references", None)
        if self.recurrence is None:
            result.pop("recurrence", None)
        if not self.exceptions:
            result.pop("exceptions", None)
        return result

    @model_validator(mode="after")
    def validate_fields(self):
        if not self.title.strip():
            raise ValueError("An event needs a title")
        if self.external_uid is not None and (not self.external_uid or any(ord(c) < 33 or ord(c) == 127 for c in self.external_uid)):
            raise ValueError("Invalid calendar UID")
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("Use a valid IANA time zone") from None
        try:
            if self.all_day:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.starts_at) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.ends_at):
                    raise ValueError()
                start, end = date.fromisoformat(self.starts_at), date.fromisoformat(self.ends_at)
            else:
                start, end = (datetime.fromisoformat(value.replace("Z", "+00:00")) for value in (self.starts_at, self.ends_at))
                if start.tzinfo is None or end.tzinfo is None:
                    raise ValueError()
                start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
                self.starts_at, self.ends_at = start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")
            if end <= start:
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            raise ValueError("Use valid dates with an end after the start; timed events need a UTC offset") from None
        # The same bounded expansion validates exception identities. Encrypted
        # channels run its equivalent on the trusted device, never this API.
        if self.recurrence:
            from .calendar_planning import _dates
            list(_dates(self))  # Reject series that overflow before persisting.
        if self.exceptions:
            from .calendar_planning import validate_exceptions
            validate_exceptions(self)
        return self


class CalendarRecurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frequency: Literal["daily", "weekly", "monthly"]
    interval: int = Field(default=1, ge=1, le=30)
    count: int = Field(default=10, ge=1, le=366)


class CalendarException(BaseModel):
    model_config = ConfigDict(extra="forbid")
    original_start: str = Field(min_length=1, max_length=64)
    cancelled: bool = False
    starts_at: str | None = Field(default=None, max_length=64)
    ends_at: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=1000)
    description: str | None = Field(default=None, max_length=10000)

    @model_validator(mode="after")
    def paired_dates(self):
        if (self.starts_at is None) != (self.ends_at is None):
            raise ValueError("An occurrence override needs both start and end")
        if self.cancelled and any(value is not None for value in (self.starts_at, self.ends_at, self.title, self.location, self.description)):
            raise ValueError("A cancelled occurrence must not contain replacement details")
        return self


CalendarFields.model_rebuild()
