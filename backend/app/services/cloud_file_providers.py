"""Read-only cloud picker adapters. Fixed endpoints, bounded lists and revisions.

Provider credentials never leave their API host. Box's one documented content
redirect is inspected explicitly and fetched without Authorization; the common
transport still refuses every redirect by default.
"""
from functools import wraps
import json
import mimetypes
import re
from urllib.parse import quote, urlencode, urlsplit

from . import remote_providers as existing
from . import remote_transport as transport

ProviderError = transport.ProviderError
ROOTS = {"google": existing.GOOGLE_DRIVE, "microsoft": existing.GRAPH,
         "dropbox": "https://api.dropboxapi.com/2", "box": "https://api.box.com/2.0"}
MAX_PAGE = 100


def value(raw, maximum=1024, empty=False):
    if not isinstance(raw, str) or len(raw) > maximum or (not raw and not empty) or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise ProviderError("invalid_response")
    return raw


def ident(raw):
    return quote(value(raw), safe="")


def _headers(credentials):
    return {"Authorization": "Bearer " + value(credentials.get("access_token"), 16000)}


def _json(method, url, credentials, body=None):
    headers = _headers(credentials)
    if body is not None: headers["Content-Type"] = "application/json"
    return transport.check_response(transport.request(method, url, headers=headers,
        body=None if body is None else json.dumps(body, separators=(",", ":")).encode())).json()


def validate_config(provider, config):
    if provider not in {"dropbox", "box"} or not isinstance(config, dict) or set(config) - {"client_id", "folder_id"}:
        raise ProviderError("invalid_configuration")
    client_id = value(config.get("client_id"), 1024)
    if any(c.isspace() for c in client_id): raise ProviderError("invalid_configuration")
    root = value(config.get("folder_id"), 1024, empty=provider == "dropbox")
    if provider == "box" and not re.fullmatch(r"[0-9]{1,40}", root): raise ProviderError("invalid_configuration")
    if provider == "dropbox" and root and (not root.startswith("/") or "//" in root or any(p in {".", ".."} for p in root.split("/")[1:])):
        raise ProviderError("invalid_configuration")
    return {"client_id": client_id, "folder_id": root.rstrip("/") if provider == "dropbox" else root}


def authorization_url(provider, config, redirect_uri, state, challenge):
    validate_config(provider, {key: config[key] for key in ("client_id", "folder_id")})
    base = "https://www.dropbox.com/oauth2/authorize" if provider == "dropbox" else "https://account.box.com/api/oauth2/authorize"
    query = {"response_type": "code", "client_id": config["client_id"], "redirect_uri": redirect_uri,
             "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    if provider == "dropbox":
        query.update(token_access_type="offline", scope="files.metadata.read files.content.read")
    return base + "?" + urlencode(query)


def _tokens(provider, config, fields):
    endpoint = "https://api.dropboxapi.com/oauth2/token" if provider == "dropbox" else "https://api.box.com/oauth2/token"
    fields.update(client_id=config["client_id"])
    if config.get("client_secret"): fields["client_secret"] = config["client_secret"]
    response = transport.request("POST", endpoint, headers={"Content-Type": "application/x-www-form-urlencoded"}, body=urlencode(fields).encode())
    if response.status == 400: raise ProviderError("reauthorization_required", status=401)
    data = transport.check_response(response).json()
    token = value(data.get("access_token"), 16000)
    if str(data.get("token_type", "")).lower() != "bearer": raise ProviderError("invalid_response")
    seconds = data.get("expires_in", 14400)
    if type(seconds) is not int or not 1 <= seconds <= 86400: raise ProviderError("invalid_response")
    result = {"access_token": token, "token_type": "Bearer", "expires_in": seconds}
    if data.get("refresh_token"): result["refresh_token"] = value(data["refresh_token"], 16000)
    if data.get("scope"): result["scope"] = value(data["scope"], 4000)
    return result


def exchange_code(provider, config, code, verifier, redirect_uri):
    return _tokens(provider, config, {"grant_type": "authorization_code", "code": code, "code_verifier": verifier, "redirect_uri": redirect_uri})


def refresh_tokens(provider, config, refresh_token):
    return _tokens(provider, config, {"grant_type": "refresh_token", "refresh_token": refresh_token})


def _google_folder(config, credentials, folder):
    root, current, visited = config["folder_id"], folder, set()
    for _ in range(16):
        if current in visited: break
        visited.add(current)
        row = _json("GET", ROOTS["google"] + "/files/" + ident(current) + "?fields=id,name,mimeType,parents,trashed", credentials)
        if row.get("trashed") or row.get("mimeType") != "application/vnd.google-apps.folder" or row.get("id") != current: break
        if current == root: return
        parents = row.get("parents", [])
        if not isinstance(parents, list) or len(parents) != 1: break
        current = value(parents[0])
    raise ProviderError("outside_collection", status=403)


def _graph_folder(config, credentials, folder):
    current, visited = folder, set()
    for _ in range(16):
        if current in visited: break
        visited.add(current)
        row = _json("GET", ROOTS["microsoft"] + "/drives/" + ident(config["drive_id"]) + "/items/" + ident(current), credentials)
        if row.get("deleted") or not isinstance(row.get("folder"), dict) or row.get("id") != current: break
        if current == config["folder_id"]: return
        parent = row.get("parentReference", {})
        if parent.get("driveId", config["drive_id"]) != config["drive_id"]: break
        current = value(parent.get("id"))
    raise ProviderError("outside_collection", status=403)


def _dropbox_path(config, path):
    path = value(path, 1024, empty=True)
    root = config["folder_id"].lower()
    if path and (not path.startswith("/") or "//" in path or any(part in {".", ".."} for part in path.split("/")[1:])):
        raise ProviderError("outside_collection", status=403)
    if root and path.lower() != root and not path.lower().startswith(root + "/"):
        raise ProviderError("outside_collection", status=403)
    return path


def _box_metadata(config, credentials, item_id, folder=False):
    kind = "folders" if folder else "files"
    row = _json("GET", ROOTS["box"] + "/" + kind + "/" + ident(item_id) + "?fields=id,type,name,size,etag,parent,path_collection,item_status", credentials)
    if row.get("id") != item_id or row.get("type") != ("folder" if folder else "file") or row.get("item_status", "active") != "active":
        raise ProviderError("not_found", status=404)
    ancestry = row.get("path_collection", {}).get("entries", [])
    if not isinstance(ancestry, list) or len(ancestry) > 100: raise ProviderError("invalid_response")
    if item_id != config["folder_id"] and config["folder_id"] not in [entry.get("id") for entry in ancestry]:
        raise ProviderError("outside_collection", status=403)
    return row


def _entry(provider, row, parent):
    folder = (row.get("mimeType") == "application/vnd.google-apps.folder" if provider == "google" else
              isinstance(row.get("folder"), dict) if provider == "microsoft" else row.get(".tag") == "folder" if provider == "dropbox" else row.get("type") == "folder")
    item_id = value(row.get("id"))
    name = value(row.get("name"), 512)
    if folder:
        return {"id": row.get("path_lower", item_id) if provider == "dropbox" else item_id, "name": name, "folder": True, "parent_id": parent}
    if provider in {"google", "microsoft"}:
        parsed = existing._file_item(row, provider)
        if not parsed: return None
        etag, mime, size = parsed["etag"], parsed["mime_type"], parsed.get("size")
        name = parsed["name"]
    else:
        etag = value(row.get("rev") if provider == "dropbox" else row.get("etag"))
        if provider == "box" and not re.fullmatch(r"[0-9]{1,64}", etag): raise ProviderError("precondition_unavailable")
        mime, size = mimetypes.guess_type(name)[0] or "application/octet-stream", row.get("size")
    if type(size) is not int and size is not None: size = int(size)
    if size is not None and not 0 <= size <= transport.MAX_BYTES: raise ProviderError("response_too_large")
    return {"id": item_id, "name": name, "folder": False, "parent_id": parent, "etag": etag, "mime_type": mime, "size": size}


def browse(provider, config, credentials, folder, cursor=None):
    folder = config["folder_id"] if folder is None else value(folder, empty=provider == "dropbox")
    if provider == "google":
        _google_folder(config, credentials, folder)
        query = {"q": "'" + folder.replace("\\", "\\\\").replace("'", "\\'") + "' in parents and trashed=false", "pageSize": MAX_PAGE,
                 "fields": "nextPageToken,files(id,name,mimeType,size,version,parents,trashed)", "orderBy": "folder,name"}
        if cursor: query["pageToken"] = value(cursor, 4000)
        result = _json("GET", ROOTS[provider] + "/files?" + urlencode(query), credentials)
        rows, next_token = result.get("files"), result.get("nextPageToken")
    elif provider == "microsoft":
        _graph_folder(config, credentials, folder)
        base = ROOTS[provider] + "/drives/" + ident(config["drive_id"]) + "/items/" + ident(folder) + "/children"
        target = base + "?$top=100" if not cursor else value(cursor, 16000)
        parsed, original = urlsplit(target), urlsplit(base)
        if ((parsed.scheme, parsed.netloc, parsed.path) != (original.scheme, original.netloc, original.path)
                or parsed.fragment or parsed.username or parsed.password): raise ProviderError("unsafe_pagination")
        result = _json("GET", target, credentials)
        rows, next_token = result.get("value"), result.get("@odata.nextLink")
    elif provider == "dropbox":
        _dropbox_path(config, folder)
        endpoint, body = ("/files/list_folder/continue", {"cursor": value(cursor, 4000)}) if cursor else (
            "/files/list_folder", {"path": folder, "recursive": False, "include_deleted": False, "limit": MAX_PAGE})
        result = _json("POST", ROOTS[provider] + endpoint, credentials, body)
        rows, next_token = result.get("entries"), result.get("cursor") if result.get("has_more") else None
        # Listing by path does not grant a moved item outside the selected root.
        for row in rows or []:
            path = _dropbox_path(config, row.get("path_lower"))
            if path.rsplit("/", 1)[0] != folder.lower(): raise ProviderError("outside_collection", status=403)
    elif provider == "box":
        _box_metadata(config, credentials, folder, folder=True)
        offset = int(cursor or "0")
        if not 0 <= offset <= 10000: raise ProviderError("invalid_response")
        result = _json("GET", ROOTS[provider] + "/folders/" + ident(folder) + "/items?" + urlencode(
            {"limit": MAX_PAGE, "offset": offset, "fields": "id,type,name,size,etag,parent"}), credentials)
        rows = result.get("entries")
        next_token = str(offset + len(rows or [])) if offset + len(rows or []) < int(result.get("total_count", 0)) else None
    else: raise ProviderError("unsupported_provider")
    if not isinstance(rows, list) or len(rows) > MAX_PAGE: raise ProviderError("invalid_response")
    if next_token is not None: value(next_token, 16000)
    items = [item for row in rows if (item := _entry(provider, row, folder)) is not None]
    return {"folder_id": folder, "items": items, "cursor": next_token}


def metadata(provider, config, credentials, item_id, parent):
    if provider in {"google", "microsoft"}:
        (_google_folder if provider == "google" else _graph_folder)(config, credentials, parent)
        scoped = {**config, "folder_id": parent}
        _, row, _ = (existing._google_file if provider == "google" else existing._graph_file)(scoped, credentials, item_id)
        return {**row, "parent_id": parent, "folder": False}
    if provider == "dropbox":
        _dropbox_path(config, parent)
        row = _json("POST", ROOTS[provider] + "/files/get_metadata", credentials, {"path": item_id, "include_deleted": False})
        path = _dropbox_path(config, row.get("path_lower"))
        if row.get(".tag") != "file" or path.rsplit("/", 1)[0] != parent.lower() or row.get("id") != item_id:
            raise ProviderError("outside_collection", status=403)
    else:
        row = _box_metadata(config, credentials, item_id)
        if row.get("parent", {}).get("id") != parent: raise ProviderError("outside_collection", status=403)
    return _entry(provider, row, parent)


def read_file(provider, config, credentials, item_id, parent, expected_etag):
    before = metadata(provider, config, credentials, item_id, parent)
    if before["etag"] != expected_etag: raise ProviderError("conflict", status=412)
    if provider in {"google", "microsoft"}:
        data = existing.read_item(provider, "files", {**config, "folder_id": parent}, credentials, {"id": item_id})
        content, mime = data["bytes"], data["mime_type"]
        if data["etag"] != expected_etag: raise ProviderError("conflict", status=412)
    elif provider == "dropbox":
        response = transport.check_response(transport.request("POST", "https://content.dropboxapi.com/2/files/download",
            headers={**_headers(credentials), "Dropbox-API-Arg": json.dumps({"path": item_id, "rev": expected_etag})}, max_bytes=transport.MAX_BYTES))
        content, mime = response.body, before["mime_type"]
    else:
        response = transport.request("GET", ROOTS[provider] + "/files/" + ident(item_id) + "/content",
            headers={**_headers(credentials), "If-Match": '"' + expected_etag + '"'}, max_bytes=transport.MAX_BYTES,
            allow_redirect_location=True)
        if response.status in {302, 303}:
            location = value(response.headers.get("location"), 16000)
            parsed = urlsplit(location)
            if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment or parsed.port not in {None, 443}
                    or not parsed.hostname or not parsed.hostname.endswith(".boxcloud.com")):
                raise ProviderError("unsafe_destination")
            response = transport.request("GET", location, headers={"Accept": "application/octet-stream"}, max_bytes=transport.MAX_BYTES)
        response = transport.check_response(response, allowed=(200,))
        content, mime = response.body, before["mime_type"]
    after = metadata(provider, config, credentials, item_id, parent)
    if after["etag"] != expected_etag or after["name"] != before["name"] or after["mime_type"] != before["mime_type"]:
        raise ProviderError("conflict", status=412)
    return {**after, "content": content, "mime_type": mime}


def _bounded(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        token = transport.begin_budget(45)
        try: return fn(*args, **kwargs)
        except ProviderError: raise
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError): raise ProviderError("invalid_response") from None
        finally: transport.end_budget(token)
    return call

for _name in ("validate_config", "authorization_url", "exchange_code", "refresh_tokens", "browse", "read_file"):
    globals()[_name] = _bounded(globals()[_name])
