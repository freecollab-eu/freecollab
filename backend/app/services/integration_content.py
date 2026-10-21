"""Validation for external links and calendar payloads. No remote URL fetching."""
from datetime import date, datetime, timezone
import ipaddress
import re
from urllib.parse import unquote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal


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


class DocumentFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    provider: Literal["nextcloud", "google_drive", "sharepoint"]
    url: str = Field(min_length=1, max_length=4096)
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def validate_fields(self):
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("A document needs a title")
        self.url = document_url(self.provider, self.url)
        return self


class CalendarFields(BaseModel):
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

    @model_validator(mode="after")
    def validate_fields(self):
        self.title = self.title.strip()
        if not self.title:
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
        return self
