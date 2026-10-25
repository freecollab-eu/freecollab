"""Registered JSON web search and HTTP reads, with separate disclosure.

Models cannot supply URLs or run these tools implicitly. A user reviews the
actual registered recipient, query and current revision before each request.
Results remain ephemeral until the user explicitly selects text for AI.
"""
import hashlib
import re
from types import SimpleNamespace
from typing import Literal
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import Field, model_validator

from ..models.ai_runtime import AIReadTool
from . import ai_providers, copilot


class Tool(copilot.Strict):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["web_search", "http_read"]
    endpoint: str = Field(min_length=1, max_length=2048)
    method: Literal["GET", "POST"] = "GET"
    api_key: str = Field(default="", max_length=2048)
    auth_header: Literal["bearer", "x-api-key"] = "bearer"
    query_field: str = Field(default="q", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    result_path: list[str] = Field(default_factory=lambda: ["results"], max_length=5)
    title_field: str = Field(default="title", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    url_field: str = Field(default="url", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    text_field: str = Field(default="content", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    max_results: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def registered_read(self):
        parsed, _, _ = ai_providers.endpoint(self.endpoint, "registered_read")
        if parsed.query or self.kind == "http_read" and self.method != "GET":
            raise ValueError("Use an exact public HTTPS read/search endpoint")
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key) for key in self.result_path):
            raise ValueError("Use bounded JSON field paths")
        if any(ord(char) < 32 or ord(char) == 127 for char in self.api_key):
            raise ValueError("Invalid credential header")
        return self


class ReadInput(copilot.Strict):
    channel_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1, strict=True)
    query: str = Field(min_length=1, max_length=2000)
    disclose_to_tool: bool
    disclose_decrypted_content: bool = False


def fields(row):
    return Tool.model_validate(copilot.opened(row.encrypted_config, "read-tool", row.workspace_id + ":" + row.tool_id))


def public(row):
    try:
        tool = fields(row)
    except ValueError:
        raw = copilot.opened(row.encrypted_config, "read-tool", row.workspace_id + ":" + row.tool_id)
        return {"id": row.tool_id, **{key: value for key, value in raw.items() if key != "api_key"}, "revision": row.revision,
            "enabled": False, "configured": False, "configuration_error": "destination_policy_changed"}
    return {"id": row.tool_id, "revision": row.revision, "enabled": row.enabled, "configured": True, **tool.model_dump(exclude={"api_key"})}


def selected_field(value, path):
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise HTTPException(502, "The registered search response does not match its reviewed JSON schema")
        value = value[key]
    return value


def read(session, workspace, actor, identifier, data):
    if not copilot.enabled():
        raise HTTPException(409, "External AI tools are disabled by this installation")
    secure = copilot.grant(session, workspace, data.channel_id, actor, lock=True)
    row = session.get(AIReadTool, (workspace, identifier), populate_existing=True)
    if not row or not row.enabled or row.revision != data.expected_revision:
        raise HTTPException(409, "Registered tool changed; review the actual recipient again")
    if not data.disclose_to_tool or secure and not data.disclose_decrypted_content:
        raise HTTPException(422, "Explicitly approve disclosure to this separate search/tool recipient")
    if not secure:
        from .ai_runtime import native_policy
        native_policy(session, data.channel_id, actor, data.query)
    try:
        tool = fields(row)
    except ValueError:
        raise HTTPException(409, "The registered destination is no longer permitted by installation policy") from None
    fingerprint = hashlib.sha256(row.encrypted_config.encode()).hexdigest()
    headers = {}
    if tool.api_key:
        headers["x-api-key" if tool.auth_header == "x-api-key" else "Authorization"] = tool.api_key if tool.auth_header == "x-api-key" else "Bearer " + tool.api_key
    provider = SimpleNamespace(endpoint=tool.endpoint, protocol="registered_read")
    payload = {tool.query_field: data.query} if tool.method == "POST" else None
    session.commit()
    try:
        packet = ai_providers.request(provider, payload, headers=headers, method=tool.method, query={tool.query_field: data.query} if tool.method == "GET" else None)
    except Exception:
        raise HTTPException(502, "The registered search/read endpoint failed; no alternate recipient was used") from None
    actual = session.get(AIReadTool, (workspace, identifier), populate_existing=True)
    if (not actual or not actual.enabled or actual.revision != data.expected_revision or hashlib.sha256(actual.encrypted_config.encode()).hexdigest() != fingerprint
            or copilot.grant(session, workspace, data.channel_id, actor, lock=True) != secure):
        raise HTTPException(409, "Tool, membership or channel changed during the request")
    value = selected_field(packet, tool.result_path)
    results = []; truncated = False
    def clean(value, limit):
        nonlocal truncated
        text = str(value)
        redacted = text.replace(tool.api_key, "[credential omitted]") if tool.api_key else text
        truncated = truncated or len(redacted) > limit
        return redacted[:limit]
    if tool.kind == "web_search":
        if not isinstance(value, list):
            raise HTTPException(502, "The registered search endpoint must return a JSON results list")
        truncated = len(value) > tool.max_results
        for item in value[:tool.max_results]:
            if not isinstance(item, dict):
                raise HTTPException(502, "The search provider returned an invalid result")
            title, url, text = item.get(tool.title_field), item.get(tool.url_field), item.get(tool.text_field)
            if not all(isinstance(field, str) for field in (title, url, text)) or tool.api_key and tool.api_key in url:
                raise HTTPException(502, "The search result did not match its registered fields")
            try:
                parsed, _ = copilot.transport.normalize_url(url)
                if parsed.query and any(word in parsed.query.lower() for word in ("api_key=", "access_token=", "secret=")):
                    raise ValueError()
            except Exception:
                raise HTTPException(502, "The search provider returned an unsafe citation URL") from None
            results.append({"title": clean(title, 200), "url": url, "text": clean(text, 2000)})
        text = "\n\n".join("[W" + str(index + 1) + "] " + item["title"] + "\n" + item["url"] + "\n" + item["text"] for index, item in enumerate(results))
        truncated = truncated or len(text) > 12000; text = text[:12000]
    else:
        text = clean(copilot.dump(value), 12000)
    if not secure:
        native_policy(session, data.channel_id, actor, data.query + "\n" + text)
    # No prompt, query, result or decrypted source enters persistence/history.
    copilot.audit(session, workspace, actor, "registered_read_tool", len(results)); session.commit()
    return {"tool_id": identifier, "tool_revision": row.revision, "recipient": tool.endpoint, "query": data.query,
        "results": results, "text": text, "bounded": True, "truncated": truncated, "ephemeral": True}
