"""Live delegated search. Providers, not channel membership, authorize each hit.

Fixed Google/Graph endpoints and the shared SSRF-safe HTTPS transport are used.
Custom connectors must implement the explicit subject/ACL protocol documented in
docs/enterprise-search.md. No provider content is copied into an index.
"""
import json
from urllib.parse import quote, urlencode, urlsplit

from . import cloud_file_providers as files, remote_transport as transport

ProviderError = transport.ProviderError


def clean(raw, maximum=1024, *, empty=False):
    return files.value(raw, maximum, empty=empty)


def config(provider, raw):
    if not isinstance(raw, dict): raise ProviderError("invalid_configuration")
    keys = {"google": {"folder_id"}, "microsoft": {"folder_id", "drive_id"},
            "custom": {"search_url", "resolve_url", "external_subject"}}
    if provider not in keys or set(raw) != keys[provider]: raise ProviderError("invalid_configuration")
    result = {key: clean(value, 2048) for key, value in raw.items()}
    if provider == "custom":
        for key in ("search_url", "resolve_url"): transport.syntax_url(result[key])
        left, right = urlsplit(result["search_url"]), urlsplit(result["resolve_url"])
        if left.netloc != right.netloc or left.query or right.query or left.fragment or right.fragment:
            raise ProviderError("invalid_configuration")
    return result


def request(method, url, token, body=None):
    headers = {"Authorization": "Bearer " + clean(token, 16000)}
    if body is not None: headers["Content-Type"] = "application/json"
    return transport.check_response(transport.request(method, url, headers=headers,
        body=None if body is None else json.dumps(body, separators=(",", ":")).encode(), timeout=12)).json()


def web_url(provider, value):
    transport.syntax_url(clean(value, 8192))
    host = urlsplit(value).hostname or ""
    if provider == "google" and host not in {"docs.google.com", "drive.google.com"}:
        raise ProviderError("invalid_response")
    if provider == "microsoft" and not (host == "onedrive.live.com" or host.endswith((".sharepoint.com", ".sharepoint.us", ".sharepoint.de", ".sharepoint.cn"))):
        raise ProviderError("invalid_response")
    return value


def entry(provider, row, settings):
    if not isinstance(row, dict): raise ProviderError("invalid_response")
    if provider == "custom":
        acl = row.get("acl")
        if not isinstance(acl, dict) or acl.get("subject") != settings["external_subject"] or acl.get("allowed") is not True:
            return None
        identifier, title, url = row.get("id"), row.get("title"), row.get("url")
        snippet = clean(row.get("snippet", ""), 2000, empty=True)
    else:
        if row.get("trashed") or row.get("deleted") or ("folder" in row) or row.get("mimeType") == "application/vnd.google-apps.folder": return None
        identifier, title = row.get("id"), row.get("name")
        url = row.get("webViewLink") if provider == "google" else row.get("webUrl")
        snippet = ""  # Provider hit titles are real; no invented content excerpts.
    if not url: return None
    return {"id": clean(identifier), "title": clean(title, 512), "url": web_url(provider, url),
        "text": snippet, "provider": provider, "acl_checked": True}


def search(provider, settings, token, query, limit):
    if provider == "google":
        escape = lambda value: value.replace("\\", "\\\\").replace("'", "\\'")
        criteria = "trashed = false and '" + escape(settings["folder_id"]) + "' in parents and fullText contains '" + escape(query) + "'"
        url = files.ROOTS[provider] + "/files?" + urlencode({"q": criteria, "pageSize": limit,
            "fields": "files(id,name,mimeType,webViewLink,parents,trashed),nextPageToken",
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
        data = request("GET", url, token)
        rows, more = data.get("files"), bool(data.get("nextPageToken"))
    elif provider == "microsoft":
        url = files.ROOTS[provider] + "/drives/" + quote(settings["drive_id"], safe="") + "/items/" + quote(settings["folder_id"], safe="")
        url += "/search(q='" + quote(query.replace("'", "''"), safe="") + "')?" + urlencode({"$top": limit,
            "$select": "id,name,webUrl,parentReference,file,folder,deleted"})
        data = request("GET", url, token)
        rows, more = data.get("value"), bool(data.get("@odata.nextLink"))
    else:
        data = request("POST", settings["search_url"], token, {"query": query, "limit": limit, "subject": settings["external_subject"]})
        rows, more = data.get("items"), data.get("has_more", False)
        if type(more) is not bool: raise ProviderError("invalid_response")
    if not isinstance(rows, list) or len(rows) > 100: raise ProviderError("invalid_response")
    items = []
    for row in rows[:limit]:
        item = entry(provider, row, settings)
        if item:
            # Re-read each resource with the same personal delegation. Search
            # result caches can outlive permission changes at the provider.
            try: item = resolve(provider, settings, token, item["id"])
            except ProviderError as error:
                if error.status in {401, 403, 404} or error.code == "outside_collection": continue
                raise
            if item: items.append(item)
    return {"items": items, "complete": not more and len(rows) <= limit}


def resolve(provider, settings, token, identifier):
    if provider == "google":
        row = request("GET", files.ROOTS[provider] + "/files/" + quote(identifier, safe="") + "?fields=id,name,mimeType,webViewLink,parents,trashed&supportsAllDrives=true", token)
        if settings["folder_id"] not in row.get("parents", []): raise ProviderError("outside_collection", status=403)
    elif provider == "microsoft":
        root = files.ROOTS[provider] + "/drives/" + quote(settings["drive_id"], safe="") + "/items/"
        row = request("GET", root + quote(identifier, safe=""), token)
        parent = row.get("parentReference", {})
        if parent.get("driveId") != settings["drive_id"]: raise ProviderError("outside_collection", status=403)
        # All ancestors must currently resolve under the same delegated token.
        current, seen = parent.get("id"), set()
        for _ in range(16):
            if current == settings["folder_id"]: break
            if not current or current in seen: raise ProviderError("outside_collection", status=403)
            seen.add(current)
            ancestor = request("GET", root + quote(clean(current), safe=""), token)
            current = ancestor.get("parentReference", {}).get("id")
        else: raise ProviderError("outside_collection", status=403)
    else:
        row = request("POST", settings["resolve_url"], token, {"id": identifier, "subject": settings["external_subject"]})
    if row.get("id") != identifier: raise ProviderError("invalid_response")
    result = entry(provider, row, settings)
    if result is None: raise ProviderError("permission_denied", status=403)
    return result
