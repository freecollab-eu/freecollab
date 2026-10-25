"""Native provider protocols over one bounded, pinned and proxy-aware transport."""
import ipaddress
import json
import os
import re
import ssl
import time
from typing import Literal
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.parse import unquote
from urllib.request import getproxies, proxy_bypass_environment

import httpcore
from fastapi import HTTPException
from pydantic import Field, field_validator, model_validator

from . import copilot, oidc_transport as transport


class Provider(copilot.Strict):
    name: str = Field(min_length=1, max_length=80)
    protocol: Literal["openai", "xai", "anthropic", "azure", "gemini", "ollama"] = "openai"
    endpoint: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
    instructions: str = Field(default="", max_length=2000)
    api_key: str = Field(default="", max_length=2048)
    api_version: str = Field(default="2024-10-21", pattern=r"^[A-Za-z0-9.-]{1,50}$")
    key_source: Literal["workspace", "personal"] = "workspace"
    max_output_tokens: int = Field(default=2048, ge=256, le=8192)
    input_cost_per_million_micros: int = Field(default=0, ge=0, le=100000000000)
    output_cost_per_million_micros: int = Field(default=0, ge=0, le=100000000000)

    @field_validator("api_key")
    @classmethod
    def header_key(cls, value):
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Invalid model credential header")
        return value

    @model_validator(mode="after")
    def coherent(self):
        _, _, local = endpoint(self.endpoint, self.protocol)
        if self.key_source == "workspace" and self.protocol != "ollama" and not local and not 8 <= len(self.api_key) <= 2048:
            raise ValueError("Configure a workspace key or select personal credentials")
        return self


def private_origins(variable="FREECOLLAB_AI_PRIVATE_ORIGINS"):
    """Only operator configuration can grant local/private model destinations."""
    try:
        value = json.loads(os.getenv(variable, "[]"))
        if not isinstance(value, list) or len(value) > 16 or any(not isinstance(item, str) for item in value):
            return set()
        return set(value)
    except (ValueError, TypeError):
        return set()


def forbidden_address(value):
    addresses = [value]
    if value.version == 6:
        if value.ipv4_mapped:
            addresses.append(value.ipv4_mapped)
        if value.sixtofour:
            addresses.append(value.sixtofour)
        if value.teredo:
            addresses.extend(value.teredo)
        if value in ipaddress.ip_network("64:ff9b::/96") or value in ipaddress.ip_network("64:ff9b:1::/48"):
            addresses.append(ipaddress.ip_address(int(value) & 0xffffffff))
    metadata = {"100.100.100.200", "168.63.129.16", "192.0.0.192", "fd00:ec2::254"}
    return any(address.is_link_local or address.is_multicast or address.is_unspecified or str(address) in metadata for address in addresses)


def endpoint(value, protocol):
    try:
        if ("\\" in value or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value)):
            raise ValueError()
        parsed = urlsplit(value)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        if (not host or parsed.username is not None or parsed.password is not None or parsed.fragment
                or parsed.query or protocol != "registered_read" and (not parsed.path or parsed.path == "/") or "%" in host or host.endswith(".")):
            raise ValueError()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        authority = ("[" + host + "]" if ":" in host else host) + ((":" + str(port)) if parsed.port is not None else "")
        origin = urlunsplit((parsed.scheme, authority, "", "", ""))
        local = (protocol in {"openai", "xai", "anthropic", "azure", "gemini", "ollama"} and origin in private_origins()
            or protocol == "registered_read" and origin in private_origins("FREECOLLAB_AI_TOOL_PRIVATE_ORIGINS"))
        if not local:
            parsed, host = transport.normalize_url(value)
        elif parsed.scheme not in {"http", "https"} or host == "metadata.google.internal":
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if forbidden_address(address) or parsed.scheme == "http" and address.is_global:
                raise ValueError()
        return parsed, host, local
    except Exception:
        raise ValueError("Use public HTTPS443, or an exact operator-allowed model/read origin") from None


def proxy_for(scheme, host, port):
    if scheme == "https":
        return transport._proxy(host, port)
    proxies = getproxies()
    authority = ("[" + host + "]" if ":" in host else host) + ":" + str(port)
    if proxy_bypass_environment(authority, proxies):
        return None
    value = proxies.get("http") or proxies.get("all")
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("The inherited HTTP proxy is unavailable")
    name = ("[" + parsed.hostname + "]" if ":" in parsed.hostname else parsed.hostname) + ((":" + str(parsed.port)) if parsed.port else "")
    credentials = (unquote(parsed.username).encode(), unquote(parsed.password or "").encode()) if parsed.username is not None else None
    return urlunsplit((parsed.scheme, name, "", "", "")), credentials, parsed.scheme == "https"


def request(provider, payload, *, headers=None, method="POST", query=None):
    """No redirect, implicit failover, socket retry or bypass of inherited proxy."""
    deadline = time.monotonic() + 30
    parsed, host, local = endpoint(provider.endpoint, provider.protocol)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = transport._resolve(host, port, 5) if local else transport.public_addresses(host, timeout=5)
    for address in addresses:
        value = ipaddress.ip_address(address)
        if forbidden_address(value) or parsed.scheme == "http" and value.is_global or not local and not transport.public_ip(address):
            raise ValueError("Forbidden model address")
    address = addresses[0]
    authority = ("[" + address + "]" if ":" in address else address)
    if port != (443 if parsed.scheme == "https" else 80):
        authority += ":" + str(port)
    if method not in {"GET", "POST"} or method == "GET" and payload is not None:
        raise ValueError("Only registered reads or native completion requests are supported")
    query_string = urlencode({"api-version": provider.api_version}) if provider.protocol == "azure" else urlencode(query or {})
    pinned = urlunsplit((parsed.scheme, authority, quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~"), query_string, ""))
    proxy = proxy_for(parsed.scheme, host, port)
    context = ssl.create_default_context()
    backend = transport._PinnedBackend(address, host, deadline, proxy_tls=bool(proxy and proxy[2]))
    options = {"ssl_context": context, "network_backend": backend, "max_connections": 1, "max_keepalive_connections": 0}
    pool = httpcore.HTTPProxy(proxy[0], proxy_auth=proxy[1], proxy_ssl_context=context if proxy[2] else None, **options) if proxy else httpcore.ConnectionPool(**options)
    target = ("[" + host + "]" if ":" in host else host) + ((":" + str(port)) if parsed.port is not None else "")
    outgoing = {"Host": target, "Content-Type": "application/json", "Accept": "application/json", "Accept-Encoding": "identity", **(headers or {})}
    body = copilot.dump(payload).encode() if payload is not None else None
    if body is not None and len(body) > 256 * 1024:
        raise ValueError("Provider request too large")
    with pool:
        with pool.stream(method, pinned, headers=outgoing, content=body, extensions={"timeout": {key: transport._remaining(deadline, 5 if key == "connect" else 20) for key in ("connect", "read", "write", "pool")}}) as response:
            if response.status != 200:
                raise ValueError("Provider rejected request")
            if not any(key.lower() == b"content-type" and b"application/json" in value.lower() for key, value in response.headers):
                raise ValueError("Expected JSON model response")
            data = bytearray()
            for chunk in response.iter_stream():
                data.extend(chunk)
                if len(data) > copilot.MAX_RESPONSE:
                    raise ValueError("Provider response too large")
            result = json.loads(bytes(data).decode("utf-8"), object_pairs_hook=transport._pairs, parse_constant=transport._constant)
            if not isinstance(result, (dict, list) if provider.protocol == "registered_read" else dict) or isinstance(result, dict) and result.get("error"):
                raise ValueError("Invalid model response")
            return result


def usage(input_tokens=None, output_tokens=None):
    reported = type(input_tokens) is int and type(output_tokens) is int and 0 <= input_tokens <= 1000000 and 0 <= output_tokens <= 1000000
    return {"input_tokens": input_tokens if reported else 0, "output_tokens": output_tokens if reported else 0, "usage_reported": reported}


def complete(provider, messages, *, max_tokens=2048, tools=None):
    """Translate native request/response shapes; never select a different model."""
    try:
        limit = min(max_tokens, provider.max_output_tokens)
        if not 1 <= limit <= 8192:
            raise ValueError()
        tools = tools or []
        system = "\n".join(item["content"] for item in messages if item["role"] == "system")
        history = [item for item in messages if item["role"] != "system"]
        protocol = provider.protocol
        if protocol == "anthropic":
            payload = {"model": provider.model, "system": system, "messages": history, "max_tokens": limit}
            if tools:
                payload["tools"] = [{"name": item["function"]["name"], "description": item["function"].get("description", ""), "input_schema": item["function"]["parameters"]} for item in tools]
            packet = request(provider, payload, headers={"anthropic-version": "2023-06-01", **({"x-api-key": provider.api_key} if provider.api_key else {})})
            if packet.get("stop_reason") not in {"end_turn", "tool_use"}:
                raise ValueError("Truncated Anthropic response")
            text = "".join(item.get("text", "") for item in packet.get("content", []) if item.get("type") == "text")
            calls = [{"id": item.get("id", ""), "name": item.get("name", ""), "arguments": copilot.dump(item.get("input", {}))} for item in packet.get("content", []) if item.get("type") == "tool_use"]
            counts = packet.get("usage", {})
            measured = usage(counts.get("input_tokens"), counts.get("output_tokens"))
        elif protocol == "gemini":
            payload = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "model" if item["role"] == "assistant" else "user", "parts": [{"text": item["content"]}]} for item in history], "generationConfig": {"maxOutputTokens": limit}}
            if tools:
                payload["tools"] = [{"functionDeclarations": [item["function"] for item in tools]}]
            packet = request(provider, payload, headers={"x-goog-api-key": provider.api_key} if provider.api_key else {})
            candidates = packet.get("candidates", [])
            if len(candidates) != 1 or candidates[0].get("finishReason") != "STOP":
                raise ValueError("Truncated Gemini response")
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(item.get("text", "") for item in parts)
            calls = [{"id": "gemini-" + str(index), "name": item["functionCall"].get("name", ""), "arguments": copilot.dump(item["functionCall"].get("args", {}))} for index, item in enumerate(parts) if "functionCall" in item]
            counts = packet.get("usageMetadata", {})
            measured = usage(counts.get("promptTokenCount"), counts.get("candidatesTokenCount"))
        elif protocol == "ollama":
            payload = {"model": provider.model, "messages": messages, "stream": False, "options": {"num_predict": limit}}
            if tools:
                payload["tools"] = tools
            packet = request(provider, payload, headers={"Authorization": "Bearer " + provider.api_key} if provider.api_key else {})
            if packet.get("done") is not True or packet.get("done_reason") not in {None, "stop"}:
                raise ValueError("Incomplete Ollama response")
            message = packet.get("message", {})
            text = message.get("content", "")
            calls = [{"id": "ollama-" + str(index), "name": item.get("function", {}).get("name", ""), "arguments": copilot.dump(item.get("function", {}).get("arguments", {}))} for index, item in enumerate(message.get("tool_calls", []))]
            measured = usage(packet.get("prompt_eval_count"), packet.get("eval_count"))
        else:
            payload = {"model": provider.model, "messages": messages, "stream": False, "max_tokens": limit, "n": 1}
            if tools:
                payload.update(tools=tools, tool_choice="auto")
            header = ({"api-key": provider.api_key} if protocol == "azure" else {"Authorization": "Bearer " + provider.api_key}) if provider.api_key else {}
            packet = request(provider, payload, headers=header)
            choices = packet.get("choices", [])
            if len(choices) != 1 or choices[0].get("finish_reason") not in {"stop", "tool_calls"}:
                raise ValueError("Incomplete model response")
            message = choices[0].get("message", {})
            text = message.get("content") or ""
            calls = [{"id": item.get("id", ""), "name": item.get("function", {}).get("name", ""), "arguments": item.get("function", {}).get("arguments", "{}")} for item in message.get("tool_calls", [])]
            counts = packet.get("usage", {})
            measured = usage(counts.get("prompt_tokens"), counts.get("completion_tokens"))
        if not isinstance(text, str) or len(text) > copilot.MAX_OUTPUT or len(calls) > 2 or not text.strip() and not calls:
            raise ValueError("Invalid bounded model output")
        if any(not isinstance(item["arguments"], str) or len(item["arguments"]) > 8000 or len(item["name"]) > 80 for item in calls):
            raise ValueError("Invalid tool output")
        return {"text": text, "tool_calls": calls, "usage": measured}
    except Exception:
        raise HTTPException(502, "The selected AI provider failed or returned an invalid bounded response") from None
