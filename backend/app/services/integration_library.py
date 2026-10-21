"""Declarative packages only: validate contract fixtures, never execute plugin code."""
from datetime import datetime
import hashlib
import json
from urllib.parse import urlsplit
from fastapi import HTTPException
from pydantic import Field, field_validator
from sqlalchemy import func, update
from sqlmodel import select
from ..models.integration_library import IntegrationPublisher, IntegrationRelease, LibraryInstallation
from ..models.user import User
from . import app_platform as apps
from .request_guard import recheck_actor
from .sso_scope import require_unrestricted
from .workspaces import lock_workspace, require_workspace_admin


class ContractCase(apps.Strict):
    command: str = Field(min_length=1, max_length=32)
    arguments: dict[str, str]
    expected_body: dict


class Package(apps.Strict):
    package_schema: int = Field(default=1, ge=1, le=1)
    manifest: apps.Manifest
    publisher: str = Field(min_length=1, max_length=80)
    license: str = Field(min_length=1, max_length=80)
    source_url: str = Field(min_length=1, max_length=2048)
    setup: str = Field(min_length=1, max_length=4000)
    endpoint_example: str = Field(min_length=1, max_length=2048)
    tests: list[ContractCase] = Field(min_length=1, max_length=8)

    @field_validator("source_url", "endpoint_example")
    @classmethod
    def link(cls, value):
        parts = urlsplit(value)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.fragment or parts.query or parts.port not in {None, 443}):
            raise ValueError("Use a public HTTPS URL without credentials, query or fragment")
        return value


class Install(apps.Strict):
    package_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    endpoint: str = Field(min_length=1, max_length=2048)
    authorization: str = Field(default="", max_length=4096)
    channel_ids: list[str] = Field(min_length=1, max_length=25)
    confirm_scopes: bool


def fixture_payload(command, arguments):
    return {"schema_version": 1, "id": "fixture-job", "type": "app.command", "installation_id": "fixture-installation",
            "generation": 1, "workspace_id": "fixture-workspace", "channel_id": "fixture-channel",
            "actor_id": "fixture-actor", "bot_id": "fixture-bot", "command": command, "arguments": arguments}


def validate_package(package):
    encoded = apps.dump(package.model_dump())
    if len(encoded.encode()) > 64000:
        raise HTTPException(413, "Integration package exceeds 64 KB")
    commands = {command.name: command for command in package.manifest.commands}
    if not commands or set(commands) != {test.command for test in package.tests} or len(package.tests) != len(commands):
        raise HTTPException(422, "Include exactly one contract fixture for every declared command")
    for case in package.tests:
        command = commands[case.command]
        arguments = apps.arguments_for(command, case.arguments)
        try:
            actual = apps.provider_payload(command.protocol, fixture_payload(command.name, arguments))
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, "Contract fixture arguments are invalid") from None
        if actual != case.expected_body:
            raise HTTPException(422, "Contract fixture does not match the declared command transport")
    return encoded, hashlib.sha256(encoded.encode()).hexdigest()


def release_data(row, *, review=False):
    package = json.loads(row.package_json)
    result = {"id": row.id, "app_id": row.app_id, "version": row.version, "package_digest": row.package_digest,
              "package": package, "status": row.status, "checks": ["manifest-schema", "declared-contract-fixtures"],
              "reviewed_at": row.reviewed_at, "created_at": row.created_at,
              "verification": "Declarative contract checks; no live provider or security certification"}
    if review: result["submitted_by"] = row.publisher_id
    return result


def builtin_data():
    # Shipped, source-reviewed recipes are distinct from community moderation.
    rows = []
    for item in apps.catalog():
        package = {"package_schema": 1, "manifest": item["manifest"], "publisher": "FreeCollab",
                   "license": "MIT", "source_url": "https://github.com/freecollab-eu/freecollab",
                   "setup": item["setup"], "endpoint_example": item["endpoint_example"], "tests": []}
        digest = hashlib.sha256(apps.dump(package).encode()).hexdigest()
        rows.append({"id": "builtin:" + item["id"], "app_id": item["manifest"]["app_id"], "version": item["manifest"]["version"],
                     "package_digest": digest, "package": package, "status": "bundled", "checks": ["shipped-source-recipe"],
                     "verification": "Bundled setup recipe; verify your own provider connection"})
    return rows


def operator(session, user_id):
    recheck_actor(session, user_id); require_unrestricted(session)
    user = session.get(User, user_id, populate_existing=True)
    if not user or user.is_blocked or user.role != "admin":
        raise HTTPException(403, "Instance administrator review required")


def submit(session, workspace_id, user_id, package):
    recheck_actor(session, user_id); require_unrestricted(session)
    # Match account closure's User -> Workspace lock order. Workspace -> User
    # would deadlock concurrent closure on PostgreSQL, hidden by SQLite writers.
    session.execute(update(User).where(User.id == user_id).values(name=User.name))
    session.expire_all(); recheck_actor(session, user_id); require_unrestricted(session)
    lock_workspace(session, workspace_id); session.expire_all(); recheck_actor(session, user_id)
    require_workspace_admin(session, workspace_id, user_id)
    encoded, digest = validate_package(package)
    if package.manifest.app_id.startswith("freecollab."):
        raise HTTPException(422, "The freecollab namespace is reserved for shipped recipes")
    # A separately unique namespace serializes simultaneous first versions.
    from sqlalchemy.exc import IntegrityError
    namespace = session.get(IntegrationPublisher, package.manifest.app_id)
    if not namespace:
        try:
            with session.begin_nested():
                namespace = IntegrationPublisher(app_id=package.manifest.app_id, publisher_id=user_id)
                session.add(namespace); session.flush()
        except IntegrityError:
            namespace = session.get(IntegrationPublisher, package.manifest.app_id, populate_existing=True)
    if not namespace or namespace.publisher_id != user_id: raise HTTPException(403, "This app belongs to another publisher")
    session.execute(update(IntegrationPublisher).where(IntegrationPublisher.app_id == package.manifest.app_id)
                    .values(publisher_id=IntegrationPublisher.publisher_id))
    previous = session.exec(select(IntegrationRelease).where(IntegrationRelease.app_id == package.manifest.app_id,
        IntegrationRelease.version == package.manifest.version)).first()
    if previous:
        if previous.publisher_id == user_id and previous.package_digest == digest: return release_data(previous)
        raise HTTPException(409, "This app version already exists; publish a new version")
    pending = session.exec(select(func.count()).select_from(IntegrationRelease).where(IntegrationRelease.publisher_id == user_id,
        IntegrationRelease.status == "pending")).one()
    if pending >= 10: raise HTTPException(429, "At most ten pending community versions per publisher")
    row = IntegrationRelease(app_id=package.manifest.app_id, version=package.manifest.version,
                             publisher_id=user_id, package_json=encoded, package_digest=digest)
    session.add(row); session.flush()
    return release_data(row)


def install_release(session, workspace_id, user_id, identifier, data):
    lock_workspace(session, workspace_id); session.expire_all(); recheck_actor(session, user_id)
    require_unrestricted(session); require_workspace_admin(session, workspace_id, user_id)
    if identifier.startswith("builtin:"):
        release = next((row for row in builtin_data() if row["id"] == identifier), None)
    else:
        from sqlalchemy import update
        session.execute(update(IntegrationRelease).where(IntegrationRelease.id == identifier).values(status=IntegrationRelease.status))
        row = session.get(IntegrationRelease, identifier, populate_existing=True)
        release = release_data(row) if row and row.status == "approved" else None
    if not release: raise HTTPException(404, "Integration version is not available for installation")
    if not data.confirm_scopes or release["package_digest"] != data.package_digest:
        raise HTTPException(409, "Review this exact package digest and its scopes before installing")
    fields = apps.InstallInput(manifest=apps.Manifest.model_validate(release["package"]["manifest"]),
        channel_ids=data.channel_ids, endpoint=data.endpoint, authorization=data.authorization)
    installed, secret = apps.save_installation(session, workspace_id, user_id, fields)
    session.add(LibraryInstallation(installation_id=installed.id, release_id=identifier, package_digest=data.package_digest))
    session.commit()
    return {"installation": apps.installation_data(installed), "signing_secret": secret,
            "callback_path": "/apps/" + installed.id + "/callback", "package_digest": data.package_digest}
