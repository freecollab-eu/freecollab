"""Public platform verification documents, bound to operator signing identities."""
import base64
import json
import os
import re

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

router = APIRouter(tags=["native-associations"])
APPLICATION_ID = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)+$")
FINGERPRINT = re.compile(r"^(?:[A-Fa-f0-9]{2}:){31}[A-Fa-f0-9]{2}$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
ITEM = re.compile(r"^[A-Za-z0-9_$:./=-]{1,256}$")
KINDS = {"channel", "message", "task", "project", "document", "linked-document", "file", "clip", "decision", "feed", "calendar", "meeting", "stack", "workflow", "workflow-run"}


def association_documents(environment=None):
    environment = os.environ if environment is None else environment
    app_id = environment.get("NATIVE_APPLICATION_ID", "")
    apple = environment.get("NATIVE_APPLE_TEAM_ID", "")
    raw = environment.get("NATIVE_ANDROID_CERT_SHA256", "")
    if not app_id:
        return {}
    if not APPLICATION_ID.fullmatch(app_id) or len(app_id) > 200:
        raise ValueError("Invalid native application identifier")
    result = {}
    if apple:
        if not re.fullmatch(r"[A-Z0-9]{10}", apple):
            raise ValueError("Invalid Apple signing team identifier")
        result["apple-app-site-association"] = {"applinks": {"details": [{
            "appIDs": [apple + "." + app_id], "components": [{"/": "/open"}],
        }]}}
    if raw:
        fingerprints = sorted(set(value.strip().upper() for value in raw.split(",")))
        if not 1 <= len(fingerprints) <= 8 or any(not FINGERPRINT.fullmatch(value) for value in fingerprints):
            raise ValueError("Invalid Android signing certificate fingerprints")
        result["assetlinks.json"] = [{"relation": ["delegate_permission/common.handle_all_urls"], "target": {
            "namespace": "android_app", "package_name": app_id, "sha256_cert_fingerprints": fingerprints,
        }}]
    return result


def verified_source(values):
    keys = {"workspace_id", "channel_id", "kind", "item_id", "project_id"}
    if set(values) - keys or not {"workspace_id", "channel_id", "kind", "item_id"} <= set(values):
        raise ValueError("An app link must identify one resource")
    if values["kind"] not in KINDS or not IDENTIFIER.fullmatch(values["workspace_id"]) or not IDENTIFIER.fullmatch(values["channel_id"]):
        raise ValueError("Invalid app link scope")
    if not ITEM.fullmatch(values["item_id"]) or "project_id" in values and not IDENTIFIER.fullmatch(values["project_id"]):
        raise ValueError("Invalid app link target")
    if values["kind"] == "task" and not values.get("project_id"):
        raise ValueError("A task link requires its exact project")
    return dict(values)


@router.get("/.well-known/{document}")
def platform_association(document: str):
    if document not in {"assetlinks.json", "apple-app-site-association"}:
        raise HTTPException(404, "Association document not found")
    try:
        values = association_documents()
    except ValueError:
        # Never publish fabricated identities or a partial successful document.
        raise HTTPException(503, "Native signing associations are invalid") from None
    if document not in values:
        raise HTTPException(404, "This platform signing identity has not been configured")
    return JSONResponse(values[document], headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"})


@router.get("/open")
def open_resource(request: Request, response: Response):
    del response
    if len(request.url.query) > 2048 or len(request.query_params.multi_items()) != len(request.query_params):
        raise HTTPException(422, "Invalid or repeated app link fields")
    try:
        source = verified_source(dict(request.query_params))
    except ValueError:
        raise HTTPException(422, "Invalid app resource link") from None
    encoded = base64.urlsafe_b64encode(json.dumps(source, separators=(",", ":")).encode()).decode().rstrip("=")
    # Only resource IDs cross this public route. Current authentication and
    # membership must be checked by the ordinary product when opening the link.
    return RedirectResponse("/#open=" + encoded, status_code=307, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
