# Declarative integration library

**Integrations → Integration library** lists bundled recipes and reviewed community versions. Search, inspect source links, manifest scopes, request schemas, contract fixtures and the SHA-256 of the exact package before installing. The public read API is `GET /apps/library`; it exposes approved declarative packages, not workspace installations, credentials or pending submissions. This is an installation-local public registry, shipped with source recipes. It is not an automatically connected central FreeCollab account or hosted marketplace.

Each package has an immutable application ID/version, publisher label, license, source URL, setup instructions, endpoint example, existing app manifest, and exactly one passing transport-contract fixture per declared command. Package data is bounded to 64 KB; extra executable/script/UI fields are rejected. The server validates the existing manifest schema, argument bounds and expected transport body. No downloaded code or remote source is executed. Source links and endpoint examples are displayed; validation does not fetch them.

Contract checks verify **declared request shape**. They do not certify the publisher, audit source code, test a live third-party service or guarantee a supplied endpoint behaves honestly. Bundled recipes are labeled separately from reviewed community packages. A human instance administrator controls approval and withdrawal. Publisher labels and licenses are supplied by the contributor; the registry makes no external identity-verification claim.

## Publish and review

A current personal workspace administrator can submit a package through the panel or `POST /workspaces/{id}/apps/library/submissions`. The UI explicitly asks the publisher to check redistribution rights and remove secrets/private URLs. Only the publisher sees pending personal submissions, and an unrestricted current instance administrator can use `GET /admin/integration-library` and the digest-bound review endpoint. Approval makes package content public. App namespace ownership persists across versions and withdrawal; a different publisher cannot take it over. New versions require another review. Rate and pending-submission bounds apply.

Example signed-webhook package:

```json
{
  "package_schema": 1,
  "manifest": {
    "schema_version": 1,
    "app_id": "example.handover",
    "name": "Handover",
    "version": "1.0.0",
    "description": "Send an explicitly reviewed handover to our receiver.",
    "scopes": ["commands:invoke", "cards:write"],
    "commands": [{
      "name": "send",
      "description": "Send reviewed text",
      "protocol": "signed-webhook",
      "arguments": [{"name": "text", "label": "Text", "required": true, "max_length": 2000}]
    }]
  },
  "publisher": "Example maintainers",
  "license": "MIT",
  "source_url": "https://github.com/example/handover",
  "endpoint_example": "https://your-app.example.com/freecollab",
  "setup": "Deploy your receiver and verify the installation HMAC.",
  "tests": [{
    "command": "send",
    "arguments": {"text": "Reviewed example"},
    "expected_body": {
      "schema_version": 1,
      "id": "fixture-job",
      "type": "app.command",
      "installation_id": "fixture-installation",
      "generation": 1,
      "workspace_id": "fixture-workspace",
      "channel_id": "fixture-channel",
      "actor_id": "fixture-actor",
      "bot_id": "fixture-bot",
      "command": "send",
      "arguments": {"text": "Reviewed example"}
    }
  }]
}
```

Other supported protocols use the documented provider-specific body for the same fixed fixture context. The registry supports only transport protocols already implemented in the app platform. A community package cannot add Python/JavaScript transports or administrative account privileges. Contributions to source protocols remain ordinary reviewed source changes in the public repository.

## Install and revoke

Installation requires the exact still-available package digest, current workspace/channel administrator authority, explicitly reviewed scopes and selected native channels, and a customer-owned HTTPS destination/authorization. It calls the existing app-platform install service, preserving its native/E2EE boundary, destination protections and encrypted credentials. A new signing secret is displayed once. The UI prevents leaving until it is acknowledged. The stored installation references the release ID and digest for provenance.

Updates never mutate existing installation grants. Install/review the new version deliberately and revoke an old app through **Integrations → Apps** when appropriate. Withdrawal removes public availability and prevents new installations; existing local administrators retain control of already installed apps and can revoke their secrets/jobs. The public catalog is bounded to 200 community releases and reports incomplete results if that bound is reached.

```sh
cd backend
.venv/bin/python -m pytest tests/test_integration_library.py tests/test_app_platform.py -q
```
