"""Bindings to Matrix rooms; no content keys or decrypted room metadata."""
from datetime import datetime

from sqlmodel import Field, SQLModel


class SecureChannel(SQLModel, table=True):
    channel_id: str = Field(primary_key=True, foreign_key="channel.id")
    matrix_room_id: str | None = Field(default=None, unique=True, index=True)
    created_by: str = Field(foreign_key="user.id")
    status: str = Field(default="preparing")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class MatrixAccount(SQLModel, table=True):
    user_id: str = Field(primary_key=True, foreign_key="user.id")
    matrix_user_id: str = Field(unique=True, index=True)
    encrypted_password: str
    provisioned: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class MatrixSession(SQLModel, table=True):
    matrix_token_hash: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    auth_token_hash: str = Field(index=True)
    auth_expires_at: datetime
    auth_version: int = 0
    device_id: str
    encrypted_access_token: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class MatrixServiceAccount(SQLModel, table=True):
    id: str = Field(default="operator", primary_key=True)
    matrix_user_id: str
    encrypted_password: str
    provisioned: bool = False


class MatrixDeviceRevocation(SQLModel, table=True):
    """Permanent account-scoped tombstones; device identifiers are not global."""
    user_id: str = Field(primary_key=True, foreign_key="user.id")
    device_id: str = Field(primary_key=True)
    revoked_at: datetime = Field(default_factory=datetime.utcnow)
