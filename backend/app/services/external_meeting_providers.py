"""Customer OAuth and meeting protocols over the existing pinned HTTPS transport.

Endpoints are fixed. Redirects/DNS/private destinations/body bounds are enforced
by remote_transport. Neither provider bodies nor credentials enter exceptions.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from . import remote_providers as calendar
from . import remote_transport as transport
from .remote_transport import ProviderError

PROVIDERS = {"google_meet": "google", "microsoft_teams": "microsoft", "zoom": "zoom"}
ZOOM_API = "https://api.zoom.us/v2"
ZOOM_SCOPES = {"meeting:write:meeting", "meeting:read:meeting", "meeting:read:list_meetings", "meeting:delete:meeting"}


def config(provider, value):
    if provider not in PROVIDERS or not isinstance(value, dict):
        raise ProviderError("invalid_configuration")
    allowed = {"client_id", "tenant_id", "calendar_id"}
    if set(value) - allowed:
        raise ProviderError("invalid_configuration")
    if provider != "zoom":
        result = calendar.validate_config(PROVIDERS[provider], "calendar", value)
        return {key: result[key] for key in allowed if key in result}
    client_id = value.get("client_id")
    if not isinstance(client_id, str) or not 1 <= len(client_id) <= 1024 or re.search(r"[\s\x00-\x1f\x7f]", client_id):
        raise ProviderError("invalid_configuration")
    if set(value) != {"client_id"}:
        raise ProviderError("invalid_configuration")
    return dict(value)


def oauth_config(provider, public, secret):
    return {**config(provider, public), "kind": "calendar", **({"client_secret": secret["client_secret"]} if secret.get("client_secret") else {})}


def authorization_url(provider, public, secret, redirect_uri, state, challenge):
    settings = oauth_config(provider, public, secret)
    if provider != "zoom":
        return calendar.authorization_url(PROVIDERS[provider], settings, redirect_uri, state, challenge)
    if not settings.get("client_secret"):
        raise ProviderError("invalid_configuration")
    return "https://zoom.us/oauth/authorize?" + urlencode({"response_type": "code", "client_id": settings["client_id"],
        "redirect_uri": redirect_uri, "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})


def _zoom_token(public, secret, fields):
    settings = oauth_config("zoom", public, secret)
    client_secret = settings.get("client_secret")
    if not isinstance(client_secret, str) or not client_secret or len(client_secret) > 4096 or re.search(r"[\x00-\x1f\x7f]", client_secret):
        raise ProviderError("invalid_configuration")
    basic = base64.b64encode((settings["client_id"] + ":" + client_secret).encode()).decode()
    response = transport.request("POST", "https://zoom.us/oauth/token", headers={"Authorization": "Basic " + basic,
        "Content-Type": "application/x-www-form-urlencoded"}, body=urlencode(fields).encode())
    transport.check_response(response, (200,))
    result = response.json()
    if (not isinstance(result.get("access_token"), str) or not 1 <= len(result["access_token"]) <= 16000
            or re.search(r"[\s\x00-\x1f\x7f]", result["access_token"])
            or result.get("token_type", "Bearer").lower() != "bearer"
            or type(result.get("expires_in")) is not int or not 1 <= result["expires_in"] <= 86400):
        raise ProviderError("invalid_token_response")
    return {key: result[key] for key in ("access_token", "refresh_token", "expires_in", "scope", "token_type") if key in result}


def exchange_code(provider, public, secret, redirect_uri, code, verifier):
    if provider != "zoom":
        token = calendar.exchange_code(PROVIDERS[provider], oauth_config(provider, public, secret), redirect_uri, code, verifier)
    else:
        token = _zoom_token(public, secret, {"grant_type": "authorization_code", "redirect_uri": redirect_uri, "code": code, "code_verifier": verifier})
    check_scope(provider, token)
    return token


def refresh(provider, public, secret):
    refresh_token = secret.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise ProviderError("reauthorization_required")
    if provider != "zoom":
        token = calendar.refresh_tokens(PROVIDERS[provider], oauth_config(provider, public, secret), refresh_token)
    else:
        token = _zoom_token(public, secret, {"grant_type": "refresh_token", "refresh_token": refresh_token})
    merged = {**secret, **token}
    check_scope(provider, merged)
    return merged


def check_scope(provider, credentials):
    scopes = credentials.get("scope")
    if not isinstance(scopes, str):
        raise ProviderError("scope_consent_required")
    available = set(scopes.split())
    if provider == "google_meet":
        allowed = bool(available & {"https://www.googleapis.com/auth/calendar", "https://www.googleapis.com/auth/calendar.events"})
    elif provider == "microsoft_teams":
        allowed = bool({scope.lower().removeprefix("https://graph.microsoft.com/") for scope in available} & {"calendars.readwrite"})
    else:
        allowed = ZOOM_SCOPES <= available or {"meeting:write", "meeting:read"} <= available
    if not allowed:
        raise ProviderError("scope_consent_required")


def join_url(provider, value):
    if not isinstance(value, str) or not 1 <= len(value) <= 4096 or re.search(r"[\x00-\x20\x7f\\]", value):
        raise ProviderError("invalid_meeting_link")
    try:
        parsed = urlsplit(value); port = parsed.port
    except ValueError:
        raise ProviderError("invalid_meeting_link") from None
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None or port not in (None, 443) or parsed.fragment:
        raise ProviderError("invalid_meeting_link")
    queries = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if provider == "google_meet":
        valid = host == "meet.google.com" and re.fullmatch(r"/[a-z]{3}-[a-z]{4}-[a-z]{3}", parsed.path) and not queries
    elif provider == "microsoft_teams":
        valid = host in {"teams.microsoft.com", "teams.live.com"} and parsed.path.startswith("/l/meetup-join/") and set(queries) <= {"context", "tenantId"}
    else:
        valid = (host == "zoom.us" or host.endswith(".zoom.us")) and re.fullmatch(r"/j/[0-9]{6,20}", parsed.path) and set(queries) <= {"pwd", "omn"}
    if not valid:
        raise ProviderError("invalid_meeting_link")
    return value


def _json(method, url, secret, body=None, *, allowed=(200, 201), headers=None):
    outgoing = {**calendar._headers(secret), **(headers or {})}
    if body is not None:
        outgoing["Content-Type"] = "application/json"
    response = transport.request(method, url, headers=outgoing,
        body=json.dumps(body, separators=(",", ":"), allow_nan=False).encode() if body is not None else None, timeout=25)
    transport.check_response(response, allowed)
    return (response.json() if response.body else {}), response.headers


def event_base(provider, public):
    calendar_id = quote(public.get("calendar_id", ""), safe="")
    if provider == "google_meet":
        return calendar.GOOGLE_CALENDAR + "/calendars/" + calendar_id + "/events"
    if provider == "microsoft_teams":
        return calendar.GRAPH + "/me/calendars/" + calendar_id + "/events"
    return ZOOM_API + "/users/me/meetings"


def test(provider, public, secret):
    config(provider, public); check_scope(provider, secret)
    if provider == "google_meet":
        # calendar.events permits this read; calendars.get requires broader
        # metadata scopes. Availability of Meet is confirmed by explicit create.
        _json("GET", event_base(provider, public) + "?maxResults=1", secret)
    elif provider == "microsoft_teams":
        raw, _ = _json("GET", calendar.GRAPH + "/me/calendars/" + quote(public["calendar_id"], safe="") + "?$select=id,allowedOnlineMeetingProviders,canEdit", secret)
        if raw.get("canEdit") is False or "teamsForBusiness" not in raw.get("allowedOnlineMeetingProviders", []):
            raise ProviderError("meeting_provider_unavailable")
    else:
        _json("GET", ZOOM_API + "/users/me/meetings?page_size=1&type=scheduled", secret)


def _normal(provider, raw, headers=None):
    identity = str(raw.get("id", ""))
    if not 1 <= len(identity) <= 1024 or re.search(r"[\x00-\x20\x7f]", identity):
        raise ProviderError("invalid_response")
    if provider == "google_meet":
        status = raw.get("conferenceData", {}).get("createRequest", {}).get("status", {}).get("statusCode")
        if status == "failure":
            raise ProviderError("meeting_provider_unavailable")
        url = raw.get("hangoutLink") or next((e.get("uri") for e in raw.get("conferenceData", {}).get("entryPoints", []) if e.get("entryPointType") == "video"), None)
        result = {"id": identity, "etag": raw.get("etag") or (headers or {}).get("etag"), "join_url": join_url(provider, url) if url else None,
            "title": raw.get("summary", ""), "start": raw.get("start", {}).get("dateTime"), "end": raw.get("end", {}).get("dateTime"), "deleted": raw.get("status") == "cancelled"}
    elif provider == "microsoft_teams":
        url = raw.get("onlineMeeting", {}).get("joinUrl") if raw.get("onlineMeeting") else None
        def instant(row):
            value = row.get("dateTime", "")
            if row.get("timeZone") != "UTC": raise ProviderError("unsupported_timezone")
            return value if re.search(r"(?:Z|[+-]\d\d:\d\d)$", value) else value + "Z"
        result = {"id": identity, "etag": raw.get("@odata.etag") or (headers or {}).get("etag"), "join_url": join_url(provider, url) if url else None,
            "title": raw.get("subject", ""), "start": instant(raw.get("start", {})), "end": instant(raw.get("end", {})), "deleted": bool(raw.get("isCancelled"))}
    else:
        start = raw.get("start_time")
        duration = raw.get("duration")
        try:
            if type(duration) is not int or not 1 <= duration <= 1440: raise ValueError()
            end = (datetime.fromisoformat(start.replace("Z", "+00:00")) + timedelta(minutes=duration)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except (ValueError, TypeError, AttributeError):
            raise ProviderError("invalid_response") from None
        result = {"id": identity, "etag": None, "join_url": join_url(provider, raw.get("join_url")) if raw.get("join_url") else None,
            "title": raw.get("topic", ""), "start": start, "end": end, "deleted": False}
    if not result["deleted"]:
        try:
            for key in ("start", "end"):
                instant = datetime.fromisoformat(result[key].replace("Z", "+00:00"))
                if instant.tzinfo is None: raise ValueError()
                result[key] = instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except (ValueError, AttributeError, TypeError):
            raise ProviderError("invalid_response") from None
    if not isinstance(result["title"], str) or len(result["title"]) > 200:
        raise ProviderError("invalid_response")
    return result


def fingerprint(result):
    return hashlib.sha256(json.dumps({key: result.get(key) for key in ("id", "title", "start", "end", "join_url", "deleted")}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def create(provider, public, secret, identity, fields):
    check_scope(provider, secret)
    base = event_base(provider, public)
    marker = "FreeCollab reference: " + identity
    if provider == "google_meet":
        body = {"id": "fc" + identity, "summary": fields["title"], "start": {"dateTime": fields["starts_at"], "timeZone": fields["timezone"]},
            "end": {"dateTime": fields["ends_at"], "timeZone": fields["timezone"]}, "description": marker,
            "conferenceData": {"createRequest": {"requestId": identity, "conferenceSolutionKey": {"type": "hangoutsMeet"}}}}
        try: raw, headers = _json("POST", base + "?conferenceDataVersion=1&sendUpdates=none", secret, body)
        except ProviderError as error:
            if error.status != 409: raise
            return read(provider, public, secret, "fc" + identity)
    elif provider == "microsoft_teams":
        body = {"transactionId": identity, "subject": fields["title"], "body": {"contentType": "text", "content": marker},
            "start": {"dateTime": fields["starts_at"].removesuffix("Z"), "timeZone": "UTC"}, "end": {"dateTime": fields["ends_at"].removesuffix("Z"), "timeZone": "UTC"},
            "isOnlineMeeting": True, "onlineMeetingProvider": "teamsForBusiness"}
        raw, headers = _json("POST", base, secret, body, headers={"Prefer": 'outlook.timezone="UTC"'})
    else:
        start = datetime.fromisoformat(fields["starts_at"].replace("Z", "+00:00")); end = datetime.fromisoformat(fields["ends_at"].replace("Z", "+00:00"))
        minutes = int((end - start).total_seconds() / 60)
        if (end - start).total_seconds() != minutes * 60 or not 1 <= minutes <= 1440:
            raise ProviderError("unsupported_duration")
        raw, headers = _json("POST", base, secret, {"topic": fields["title"], "type": 2, "start_time": fields["starts_at"], "duration": minutes,
            "timezone": "UTC", "agenda": marker, "settings": {"waiting_room": True, "join_before_host": False, "auto_recording": "none"}})
    return _normal(provider, raw, headers)


def read(provider, public, secret, remote_id):
    suffix = quote(str(remote_id), safe="")
    base = ZOOM_API + "/meetings" if provider == "zoom" else event_base(provider, public)
    raw, headers = _json("GET", base + "/" + suffix, secret, headers={"Prefer": 'outlook.timezone="UTC"'} if provider == "microsoft_teams" else None)
    return _normal(provider, raw, headers)


def reconcile(provider, public, secret, identity):
    """Recover an uncertain write by reads only; never blindly recreate Zoom."""
    if provider == "google_meet":
        return read(provider, public, secret, "fc" + identity)
    marker = "FreeCollab reference: " + identity
    base = event_base(provider, public)
    url = base + ("?page_size=100&type=scheduled" if provider == "zoom" else "?$top=100&$select=id,transactionId,subject,onlineMeeting,start,end,isCancelled,body")
    for _ in range(3):
        raw, _ = _json("GET", url, secret, headers={"Prefer": 'outlook.timezone="UTC", outlook.body-content-type="text"'} if provider == "microsoft_teams" else None)
        values = raw.get("meetings" if provider == "zoom" else "value", [])
        if not isinstance(values, list) or len(values) > 100: raise ProviderError("invalid_response")
        matches = [row for row in values if (row.get("agenda") == marker if provider == "zoom" else row.get("transactionId") == identity)]
        if len(matches) > 1: raise ProviderError("duplicate_remote_meetings")
        if matches:
            return read(provider, public, secret, matches[0]["id"])
        if provider == "zoom":
            token = raw.get("next_page_token")
            if not token: break
            if not isinstance(token, str) or len(token) > 2048: raise ProviderError("invalid_response")
            url = base + "?" + urlencode({"page_size": 100, "type": "scheduled", "next_page_token": token})
        else:
            value = raw.get("@odata.nextLink")
            if not value: break
            url = calendar._same_endpoint(value, calendar.GRAPH)
            if urlsplit(url).path != urlsplit(base).path:
                raise ProviderError("unsafe_pagination")
    raise ProviderError("uncertain_create_not_found")


def cancel(provider, public, secret, remote_id, etag):
    if provider in {"google_meet", "microsoft_teams"} and not calendar._valid_etag(etag):
        raise ProviderError("precondition_unavailable")
    base = ZOOM_API + "/meetings" if provider == "zoom" else event_base(provider, public)
    url = base + "/" + quote(str(remote_id), safe="") + ("?sendUpdates=none" if provider == "google_meet" else "")
    _json("DELETE", url, secret, allowed=(200, 204), headers={"If-Match": etag} if etag else None)
