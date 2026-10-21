"""Customer-authorized Google, Microsoft, WebDAV and CalDAV synchronization.

Every operation remains confined to a selected collection. Credentials are passed
only by the owning worker and never embedded in stored item metadata. Full catalog
snapshots have complete=True; incremental responses have complete=False and carry
explicit tombstones. A failed/truncated snapshot never advances a cursor.
"""
import hashlib
from functools import wraps
import json
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlsplit

from . import remote_transport as transport
from .remote_transport import ProviderError
from .integration_content import CalendarFields

GOOGLE_DRIVE = "https://www.googleapis.com/drive/v3"
GOOGLE_UPLOAD = "https://www.googleapis.com/upload/drive/v3"
GOOGLE_CALENDAR = "https://www.googleapis.com/calendar/v3"
GRAPH = "https://graph.microsoft.com/v1.0"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
MAX_ITEMS = 1000
MAX_PAGES = 20


def _id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 1024 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ProviderError("invalid_configuration")
    return quote(value, safe="")


def _oauth_config(provider, config):
    if provider not in {"google", "microsoft"}:
        raise ProviderError("unsupported_provider")
    client = config.get("client_id")
    if not isinstance(client, str) or not client.strip() or len(client) > 1024 or any(ord(c) < 33 for c in client):
        raise ProviderError("invalid_configuration")
    secret = config.get("client_secret")
    if secret is not None and (not isinstance(secret, str) or len(secret) > 4096 or any(ord(c) < 32 for c in secret)):
        raise ProviderError("invalid_configuration")
    if provider == "microsoft" and not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", config.get("tenant_id", "")):
        raise ProviderError("tenant_id_required")


def validate_config(provider, kind, config):
    if kind not in {"files", "calendar"} or not isinstance(config, dict):
        raise ProviderError("invalid_configuration")
    result = dict(config, kind=kind)
    if provider in {"nextcloud", "caldav"}:
        if provider == "caldav" and kind != "calendar":
            raise ProviderError("unsupported_provider")
        from .remote_dav import validate_config as validate_dav
        return dict(validate_dav(kind, result), kind=kind)
    _oauth_config(provider, result)
    if kind == "files":
        _id(result.get("folder_id"))
        if provider == "microsoft": _id(result.get("drive_id"))
    else:
        _id(result.get("calendar_id"))
    return result


def authorization_url(provider, config, redirect_uri, state, challenge):
    _oauth_config(provider, config)
    if not isinstance(state, str) or not isinstance(challenge, str) or len(state) < 20 or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", challenge):
        raise ProviderError("invalid_authorization")
    kind = config.get("kind")
    if kind not in {"files", "calendar"}: raise ProviderError("invalid_configuration")
    if provider == "google":
        url = "https://accounts.google.com/o/oauth2/v2/auth"
        scope = "https://www.googleapis.com/auth/drive" if kind == "files" else "https://www.googleapis.com/auth/calendar.events"
        extra = {"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"}
    else:
        url = "https://login.microsoftonline.com/" + config["tenant_id"] + "/oauth2/v2.0/authorize"
        scope = "offline_access " + ("Files.ReadWrite.All" if kind == "files" else "Calendars.ReadWrite")
        extra = {"response_mode": "query"}
    return url + "?" + urlencode({"client_id": config["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
                                    "scope": scope, "state": state, "code_challenge": challenge, "code_challenge_method": "S256", **extra})


def _token_request(provider, config, fields):
    _oauth_config(provider, config)
    url = GOOGLE_TOKEN if provider == "google" else "https://login.microsoftonline.com/" + config["tenant_id"] + "/oauth2/v2.0/token"
    data = {"client_id": config["client_id"], **fields}
    if config.get("client_secret"): data["client_secret"] = config["client_secret"]
    response = transport.request("POST", url, headers={"Content-Type": "application/x-www-form-urlencoded"}, body=urlencode(data).encode())
    if response.status == 400:
        try: reason = response.json().get("error")
        except ProviderError: reason = None
        if reason in {"invalid_grant", "invalid_client", "unauthorized_client"}:
            raise ProviderError("reauthorization_required", status=400)
    transport.check_response(response, (200,))
    result = response.json()
    if (not isinstance(result.get("access_token"), str) or not result["access_token"]
            or result.get("token_type", "Bearer").lower() != "bearer"
            or type(result.get("expires_in", 3600)) is not int or not 1 <= result.get("expires_in", 3600) <= 86400):
        raise ProviderError("invalid_token_response")
    return {key: result[key] for key in ("access_token", "refresh_token", "expires_in", "scope", "token_type") if key in result}


def exchange_code(provider, config, redirect_uri, code, verifier):
    if not isinstance(code, str) or not code or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
        raise ProviderError("invalid_authorization")
    return _token_request(provider, config, {"grant_type": "authorization_code", "redirect_uri": redirect_uri, "code": code, "code_verifier": verifier})


def refresh_tokens(provider, config, refresh_token):
    if not isinstance(refresh_token, str) or not refresh_token: raise ProviderError("reauthorization_required")
    return _token_request(provider, config, {"grant_type": "refresh_token", "refresh_token": refresh_token})


def _headers(credentials):
    token = credentials.get("access_token")
    if not isinstance(token, str) or not token or len(token) > 16000 or any(ord(c) < 33 or ord(c) == 127 for c in token):
        raise ProviderError("reauthorization_required")
    return {"Authorization": "Bearer " + token}


def _json(method, url, credentials, *, body=None, headers=None, allowed=(200, 201)):
    outgoing = {**_headers(credentials), **(headers or {})}
    if body is not None:
        outgoing["Content-Type"] = "application/json"
        body = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
    response = transport.request(method, url, headers=outgoing, body=body)
    transport.check_response(response, allowed)
    return response.json(), response.headers


def _same_endpoint(value, base):
    """Never send a bearer token to a nextLink or deltaLink outside this API."""
    target, source = urlsplit(value), urlsplit(base)
    if (target.scheme, target.netloc) != (source.scheme, source.netloc) or not target.path.startswith(source.path.rstrip("/") + "/") or target.fragment or target.username or target.password:
        raise ProviderError("unsafe_pagination")
    return value


def _cursor(value):
    if value is not None and (not isinstance(value, str) or not value or len(value) > 8192 or any(ord(c) < 33 for c in value)):
        raise ProviderError("invalid_cursor")
    return value


def _calendar_fields(raw, provider):
    if raw.get("recurrence") or raw.get("recurringEventId") or raw.get("type") in {"seriesMaster", "occurrence", "exception"}:
        raise ProviderError("unsupported_recurrence")
    if provider == "google":
        start, end = raw.get("start", {}), raw.get("end", {})
        all_day = bool(start.get("date"))
        start_value, end_value = (start.get("date"), end.get("date")) if all_day else (start.get("dateTime"), end.get("dateTime"))
        reminders = raw.get("reminders", {})
        custom = reminders.get("overrides", []) if not reminders.get("useDefault") else []
        if len(custom) > 1 or any(row.get("method") != "popup" for row in custom):
            raise ProviderError("unsupported_reminder")
        fields = {"title": raw.get("summary") or "Untitled event", "description": raw.get("description", ""), "location": raw.get("location", ""),
                  "starts_at": start_value, "ends_at": end_value, "all_day": all_day, "timezone": start.get("timeZone", "UTC"),
                  "reminder_minutes": custom[0].get("minutes") if custom else None, "external_uid": raw.get("iCalUID")}
    else:
        all_day = bool(raw.get("isAllDay"))
        def graph_time(row):
            value = row.get("dateTime", "")
            if all_day: return value[:10]
            if row.get("timeZone") != "UTC": raise ProviderError("unsupported_timezone")
            return value + "Z" if not re.search(r"(?:Z|[+-]\d\d:\d\d)$", value) else value
        fields = {"title": raw.get("subject") or "Untitled event", "description": raw.get("body", {}).get("content", ""),
                  "location": raw.get("location", {}).get("displayName", ""), "starts_at": graph_time(raw.get("start", {})),
                  "ends_at": graph_time(raw.get("end", {})), "all_day": all_day, "timezone": "UTC",
                  "reminder_minutes": raw.get("reminderMinutesBeforeStart") if raw.get("isReminderOn") else None,
                  "external_uid": raw.get("iCalUId")}
        if raw.get("body", {}).get("contentType", "text").lower() != "text":
            # Prefer returns text with the calendar requests; never render or
            # silently rewrite an HTML body through the plaintext event editor.
            raise ProviderError("unsupported_calendar_body")
    try:
        return CalendarFields(**fields).model_dump()
    except (ValueError, TypeError):
        raise ProviderError("unsupported_resource") from None


def _calendar_item(row, provider):
    identity = row.get("id")
    _id(identity)
    deleted = row.get("status") == "cancelled" or "@removed" in row
    result = {"id": identity, "etag": row.get("etag") if provider == "google" else row.get("@odata.etag"), "name": row.get("summary", row.get("subject", "Calendar event")),
              "mime_type": "text/calendar", "deleted": deleted, "read_only": False}
    if deleted: return result
    result["fields"] = _calendar_fields(row, provider)
    result["remote_key"] = row.get("extendedProperties", {}).get("private", {}).get("freecollabSyncKey") if provider == "google" else row.get("transactionId")
    if provider == "microsoft" and row.get("attendees"): result["read_only"] = True
    if not _valid_etag(result["etag"]): raise ProviderError("precondition_unavailable")
    return result


def _file_item(row, provider):
    identity = row.get("id"); _id(identity)
    if provider == "google":
        mime = row.get("mimeType", "application/octet-stream")
        if mime == "application/vnd.google-apps.folder": return None
        export = {"application/vnd.google-apps.document": ("application/pdf", ".pdf"),
                  "application/vnd.google-apps.spreadsheet": ("application/pdf", ".pdf"),
                  "application/vnd.google-apps.presentation": ("application/pdf", ".pdf")}.get(mime)
        if mime.startswith("application/vnd.google-apps.") and export is None:
            raise ProviderError("unsupported_resource")
        version = str(row.get("version", ""))
        if not row.get("etag") and not re.fullmatch(r"[0-9]{1,30}", version):
            raise ProviderError("precondition_unavailable")
        if row.get("etag") and not _valid_etag(row["etag"]):
            raise ProviderError("precondition_unavailable")
        return {"id": identity, "etag": row.get("etag") or "version:" + version, "name": row.get("name", "Document") + (export[1] if export else ""),
                "mime_type": export[0] if export else mime, "size": int(row.get("size", 0)), "deleted": bool(row.get("trashed")),
                "read_only": bool(export), "remote_key": row.get("appProperties", {}).get("freecollabSyncKey"), "export": bool(export)}
    if "folder" in row: return None
    if "deleted" not in row and "@removed" not in row and not _valid_etag(row.get("eTag") or row.get("@odata.etag")):
        raise ProviderError("precondition_unavailable")
    key = re.match(r"^(fc-[a-zA-Z0-9_-]+?)--", row.get("name", ""))
    return {"id": identity, "etag": row.get("eTag") or row.get("@odata.etag"), "name": row.get("name", "Document")[len(key[0]):] if key else row.get("name", "Document"),
            "mime_type": row.get("file", {}).get("mimeType", "application/octet-stream"), "size": int(row.get("size", 0)),
            "deleted": "deleted" in row or "@removed" in row, "read_only": False, "remote_key": key[1] if key else None}


def _google_file(config, credentials, identity):
    raw, headers = _json("GET", GOOGLE_DRIVE + "/files/" + _id(identity) + "?" + urlencode({"fields": "id,name,mimeType,size,version,parents,trashed,appProperties"}), credentials)
    if config["folder_id"] not in raw.get("parents", []) or raw.get("trashed"):
        raise ProviderError("outside_collection")
    item = _file_item(raw, "google")
    if item is None: raise ProviderError("unsupported_resource")
    return raw, item, headers.get("etag")


def _graph_file_base(config):
    return GRAPH + "/drives/" + _id(config["drive_id"])


def _graph_file(config, credentials, identity):
    raw, headers = _json("GET", _graph_file_base(config) + "/items/" + _id(identity), credentials)
    if raw.get("parentReference", {}).get("id") != config["folder_id"]:
        raise ProviderError("outside_collection")
    item = _file_item(raw, "microsoft")
    if item is None: raise ProviderError("unsupported_resource")
    return raw, item, headers.get("etag") or item["etag"]


def _dav_credentials(credentials):
    return {**credentials, "password": credentials.get("password") or credentials.get("app_password")}


def snapshot(provider, kind, config, credentials, cursor=None):
    config = validate_config(provider, kind, config); _cursor(cursor)
    if provider in {"nextcloud", "caldav"}:
        from .remote_dav import snapshot as dav_snapshot
        return dav_snapshot(kind, config, _dav_credentials(credentials), cursor)
    incremental = cursor is not None
    values, next_cursor, pages = [], None, 0
    if provider == "google" and kind == "files":
        escaped = config["folder_id"].replace("\\", "\\\\").replace("'", "\\'")
        base = GOOGLE_DRIVE + "/files"
        query = {"q": "'" + escaped + "' in parents and trashed = false", "pageSize": "100", "fields": "nextPageToken,files(id,name,mimeType,size,version,parents,appProperties)"}
        url = base + "?" + urlencode(query); incremental = False
    elif provider == "google":
        base = GOOGLE_CALENDAR + "/calendars/" + _id(config["calendar_id"]) + "/events"
        query = {"maxResults": "100", "showDeleted": "true", "singleEvents": "false"}
        if cursor: query["syncToken"] = cursor
        url = base + "?" + urlencode(query)
    elif kind == "files":
        base = _graph_file_base(config) + "/items/" + _id(config["folder_id"]) + "/delta"
        url = _same_endpoint(cursor, GRAPH) if cursor else base
    else:
        base = GRAPH + "/me/calendars/" + _id(config["calendar_id"]) + "/events"
        url = base + "?$top=100"; incremental = False
    seen_pages = set()
    while url:
        if url in seen_pages or pages >= MAX_PAGES: raise ProviderError("snapshot_too_large")
        seen_pages.add(url); pages += 1
        raw, _ = _json("GET", url, credentials, headers={"Prefer": 'outlook.timezone="UTC", outlook.body-content-type="text"'} if provider == "microsoft" and kind == "calendar" else None)
        rows = raw.get("files" if provider == "google" and kind == "files" else "items" if provider == "google" else "value", [])
        if not isinstance(rows, list): raise ProviderError("invalid_response")
        for row in rows:
            if not isinstance(row, dict): raise ProviderError("invalid_response")
            if provider == "microsoft" and kind == "files" and "deleted" not in row and row.get("parentReference", {}).get("id") != config["folder_id"]:
                if row.get("id") != config["folder_id"] and "folder" not in row:
                    _id(row.get("id")); values.append({"id": row["id"], "deleted": True, "etag": None})
                continue
            item = _file_item(row, provider) if kind == "files" else _calendar_item(row, provider)
            if item is not None: values.append(item)
            if len(values) > MAX_ITEMS: raise ProviderError("snapshot_too_large")
        if provider == "google":
            next_cursor = raw.get("nextSyncToken") if kind == "calendar" else None
            page = raw.get("nextPageToken")
            url = base + "?" + urlencode({**query, "pageToken": page}) if page else None
        else:
            url = _same_endpoint(raw["@odata.nextLink"], GRAPH) if raw.get("@odata.nextLink") else None
            if raw.get("@odata.deltaLink"): next_cursor = _same_endpoint(raw["@odata.deltaLink"], GRAPH)
    if len({item["id"] for item in values}) != len(values):
        # Repeated ids in a delta are legal; the last state wins, but a complete
        # catalog cannot contain ambiguous duplicate identities.
        if not incremental: raise ProviderError("invalid_response")
        values = list({item["id"]: item for item in values}.values())
    return {"items": values, "cursor": next_cursor, "complete": not incremental}


def read_item(provider, kind, config, credentials, item):
    config = validate_config(provider, kind, config)
    if provider in {"nextcloud", "caldav"}:
        from .remote_dav import read_item as dav_read
        return dav_read(kind, config, _dav_credentials(credentials), item)
    if kind == "calendar":
        url = (GOOGLE_CALENDAR + "/calendars/" + _id(config["calendar_id"]) + "/events/" + _id(item["id"]) if provider == "google"
               else GRAPH + "/me/calendars/" + _id(config["calendar_id"]) + "/events/" + _id(item["id"]))
        raw, _ = _json("GET", url, credentials, headers={"Prefer": 'outlook.timezone="UTC", outlook.body-content-type="text"'} if provider == "microsoft" else None)
        mapped = _calendar_item(raw, provider)
        return {"fields": mapped["fields"], "etag": mapped["etag"]}
    if provider == "google":
        raw, mapped, actual_etag = _google_file(config, credentials, item["id"])
        suffix = "/export?" + urlencode({"mimeType": mapped["mime_type"]}) if mapped["export"] else "?alt=media"
        headers = _headers(credentials)
        if _valid_etag(actual_etag) and not actual_etag.startswith("W/"):
            headers["If-Match"] = actual_etag
        response = transport.request("GET", GOOGLE_DRIVE + "/files/" + _id(item["id"]) + suffix,
                                     headers=headers, max_bytes=transport.MAX_BYTES)
    else:
        raw, mapped, _ = _graph_file(config, credentials, item["id"])
        url = raw.get("@microsoft.graph.downloadUrl")
        host = urlsplit(url or "").hostname or ""
        if not (host.endswith((".sharepoint.com", ".sharepoint.de", ".1drv.com", ".onedrive.com", ".onedrive.live.com"))):
            raise ProviderError("unsafe_download")
        # The trusted Graph metadata contains a short-lived preauthorized URL.
        # It is freshly pinned/validated and receives no bearer authorization.
        response = transport.request("GET", url, max_bytes=transport.MAX_BYTES)
    transport.check_response(response, (200,))
    # Metadata and content are separate provider reads. Never label bytes with
    # a revision read before a concurrent edit, move or permission change. The
    # authenticated API rechecks both the selected parent and canonical revision;
    # Graph's preauthorized content URL still receives no bearer credential.
    _, current, _ = (_google_file(config, credentials, item["id"]) if provider == "google"
                     else _graph_file(config, credentials, item["id"]))
    if current["etag"] != mapped["etag"] or current["mime_type"] != mapped["mime_type"]:
        raise ProviderError("conflict", status=412)
    return {"bytes": response.body, "etag": mapped["etag"], "mime_type": mapped["mime_type"]}


def _calendar_write(resource, provider):
    try: fields = CalendarFields(**{key: resource[key] for key in CalendarFields.model_fields if key in resource}).model_dump()
    except (ValueError, TypeError): raise ProviderError("invalid_resource") from None
    if fields.get("recurrence") or fields.get("exceptions"):
        raise ProviderError("unsupported_recurrence")
    if provider == "google":
        start = {"date": fields["starts_at"]} if fields["all_day"] else {"dateTime": fields["starts_at"], "timeZone": fields["timezone"]}
        end = {"date": fields["ends_at"]} if fields["all_day"] else {"dateTime": fields["ends_at"], "timeZone": fields["timezone"]}
        return {"summary": fields["title"], "description": fields["description"], "location": fields["location"], "start": start, "end": end,
                "reminders": {"useDefault": False, "overrides": [] if fields["reminder_minutes"] is None else [{"method": "popup", "minutes": fields["reminder_minutes"]}]}}
    def value(value):
        if fields["all_day"]: return value + "T00:00:00"
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None).isoformat()
    return {"subject": fields["title"], "body": {"contentType": "text", "content": fields["description"]}, "location": {"displayName": fields["location"]},
            "start": {"dateTime": value(fields["starts_at"]), "timeZone": "UTC"}, "end": {"dateTime": value(fields["ends_at"]), "timeZone": "UTC"},
            "isAllDay": fields["all_day"], "isReminderOn": fields["reminder_minutes"] is not None,
            **({"reminderMinutesBeforeStart": fields["reminder_minutes"]} if fields["reminder_minutes"] is not None else {})}


def _valid_etag(value):
    return isinstance(value, str) and len(value) <= 1024 and re.fullmatch(r'(?:W/)?"[\x21\x23-\x7e\x80-\xff]*"', value) is not None


def _precondition(expected_etag):
    if not _valid_etag(expected_etag):
        raise ProviderError("precondition_required")
    return {"If-Match": expected_etag}


def _remote_key(resource):
    value = resource.get("remote_key")
    if not isinstance(value, str) or not re.fullmatch(r"fc-[A-Za-z0-9_-]{1,100}", value):
        raise ProviderError("idempotency_key_required")
    return value


def write_item(provider, kind, config, credentials, resource, remote_id=None, expected_etag=None):
    config = validate_config(provider, kind, config)
    if kind == "calendar" and (resource.get("recurrence") or resource.get("exceptions")):
        raise ProviderError("unsupported_recurrence")
    if provider in {"nextcloud", "caldav"}:
        from .remote_dav import write_item as dav_write
        return dav_write(kind, config, _dav_credentials(credentials), resource, remote_id, expected_etag)
    if kind == "calendar":
        body = _calendar_write(resource, provider)
        key = _remote_key(resource) if remote_id is None else None
        base = GOOGLE_CALENDAR + "/calendars/" + _id(config["calendar_id"]) + "/events" if provider == "google" else GRAPH + "/me/calendars/" + _id(config["calendar_id"]) + "/events"
        if provider == "google":
            if remote_id is None:
                body.update(id=hashlib.sha256(key.encode()).hexdigest(), extendedProperties={"private": {"freecollabSyncKey": key}})
            suffix = "?sendUpdates=none"
        else:
            if remote_id is None: body["transactionId"] = key
            else:
                current, _ = _json("GET", base + "/" + _id(remote_id), credentials, headers={"Prefer": 'outlook.timezone="UTC", outlook.body-content-type="text"'})
                if current.get("attendees"): raise ProviderError("read_only_resource")
            suffix = ""
        raw, _ = _json("PATCH" if remote_id else "POST", base + ("/" + _id(remote_id) if remote_id else "") + suffix,
                       credentials, body=body, headers=_precondition(expected_etag) if remote_id else {"If-None-Match": "*"})
        return _calendar_item(raw, provider)
    content = resource.get("bytes")
    if not isinstance(content, bytes) or len(content) > transport.MAX_BYTES: raise ProviderError("invalid_resource")
    name, mime = resource.get("name"), resource.get("mime_type", "application/octet-stream")
    if (not isinstance(name, str) or not name.strip() or len(name) > 255 or any(ord(c) < 32 or c in '/\\' for c in name)
            or not isinstance(mime, str) or not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", mime)):
        raise ProviderError("invalid_resource")
    if provider == "google":
        if remote_id:
            current, mapped, actual_etag = _google_file(config, credentials, remote_id)
            if mapped["read_only"]: raise ProviderError("read_only_resource")
            if mapped["etag"] != expected_etag: raise ProviderError("conflict", status=412)
            headers = _precondition(actual_etag)
            metadata = {"name": name}
        else:
            key = _remote_key(resource)
            found = snapshot(provider, kind, config, credentials)["items"]
            existing = [row for row in found if row.get("remote_key") == key]
            if len(existing) > 1: raise ProviderError("ambiguous_create")
            if existing: return existing[0]
            headers = {"If-None-Match": "*"}
            metadata = {"name": name, "parents": [config["folder_id"]], "appProperties": {"freecollabSyncKey": key}}
        boundary = "freecollab-" + secrets.token_hex(16)
        body = ("--" + boundary + "\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n" + json.dumps(metadata) + "\r\n--" + boundary + "\r\nContent-Type: " + mime + "\r\n\r\n").encode() + content + ("\r\n--" + boundary + "--\r\n").encode()
        response = transport.request("PATCH" if remote_id else "POST", GOOGLE_UPLOAD + "/files" + ("/" + _id(remote_id) if remote_id else "") + "?uploadType=multipart&fields=id,name,mimeType,size,version,appProperties",
                                     headers={**_headers(credentials), **headers, "Content-Type": "multipart/related; boundary=" + boundary}, body=body)
        transport.check_response(response, (200, 201))
        return _file_item(response.json(), provider)
    if remote_id:
        _, current, _ = _graph_file(config, credentials, remote_id)
        if current["etag"] != expected_etag: raise ProviderError("conflict", status=412)
        url = _graph_file_base(config) + "/items/" + _id(remote_id) + "/content"
        headers = _precondition(expected_etag)
    else:
        key = _remote_key(resource)
        name = key + "--" + name
        url = _graph_file_base(config) + "/items/" + _id(config["folder_id"]) + ":/" + quote(name, safe="") + ":/content?@microsoft.graph.conflictBehavior=fail"
        headers = {"If-None-Match": "*"}
    response = transport.request("PUT", url, headers={**_headers(credentials), **headers, "Content-Type": mime}, body=content)
    transport.check_response(response, (200, 201))
    return _file_item(response.json(), provider)


def delete_item(provider, kind, config, credentials, remote_id, expected_etag=None):
    config = validate_config(provider, kind, config)
    if provider in {"nextcloud", "caldav"}:
        from .remote_dav import delete_item as dav_delete
        return dav_delete(kind, config, _dav_credentials(credentials), remote_id, expected_etag)
    headers = {} if provider == "google" and kind == "files" else _precondition(expected_etag)
    if kind == "files":
        if provider == "google":
            _, item, etag = _google_file(config, credentials, remote_id)
            if item["read_only"]: raise ProviderError("read_only_resource")
            if item["etag"] != expected_etag: raise ProviderError("conflict", status=412)
            headers = _precondition(etag)
            url = GOOGLE_DRIVE + "/files/" + _id(remote_id)
            # Drive DELETE permanently erases a file. A synchronized channel
            # deletion must use the provider's reversible trash operation.
            response = transport.request("PATCH", url,
                headers={**_headers(credentials), **headers, "Content-Type": "application/json"},
                body=b'{"trashed":true}', max_bytes=transport.MAX_JSON_BYTES)
            transport.check_response(response, (200,))
            return
        else:
            _graph_file(config, credentials, remote_id)
            url = _graph_file_base(config) + "/items/" + _id(remote_id)
    else:
        url = GOOGLE_CALENDAR + "/calendars/" + _id(config["calendar_id"]) + "/events/" + _id(remote_id) + "?sendUpdates=none" if provider == "google" else GRAPH + "/me/calendars/" + _id(config["calendar_id"]) + "/events/" + _id(remote_id)
        if provider == "microsoft":
            current, _ = _json("GET", url, credentials, headers={"Prefer": 'outlook.timezone="UTC", outlook.body-content-type="text"'})
            if current.get("attendees"): raise ProviderError("read_only_resource")
    response = transport.request("DELETE", url, headers={**_headers(credentials), **headers}, max_bytes=transport.MAX_JSON_BYTES)
    transport.check_response(response, (200, 204))


def _safe_operation(operation):
    @wraps(operation)
    def safe(*args, **kwargs):
        budget = transport.begin_budget(45)
        try:
            return operation(*args, **kwargs)
        except ProviderError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError):
            # Customer/provider-controlled fields must not be echoed by a worker
            # or become an arbitrary exception string in a status response.
            raise ProviderError("invalid_response") from None
        finally:
            transport.end_budget(budget)
    return safe


for _operation in ("validate_config", "authorization_url", "exchange_code", "refresh_tokens", "snapshot", "read_item", "write_item", "delete_item"):
    globals()[_operation] = _safe_operation(globals()[_operation])
