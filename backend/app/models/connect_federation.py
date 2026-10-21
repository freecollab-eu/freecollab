"""Installation trust and opaque remote roster metadata; never foreign users."""
from datetime import datetime
from sqlmodel import Field, SQLModel


class ConnectFederationIdentity(SQLModel, table=True):
    id: str = Field(default="installation", primary_key=True)
    encrypted_seed: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ConnectFederationNonce(SQLModel, table=True):
    origin: str = Field(primary_key=True)
    nonce: str = Field(primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class FederatedConnection(SQLModel, table=True):
    id: str = Field(primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", unique=True, index=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    peer_origin: str
    peer_workspace_id: str
    role: str  # host or guest; each party retains its own channel and members.
    authority_user_id: str = Field(foreign_key="user.id")
    status: str = Field(default="pending", index=True)
    invitation_hash: str = Field(default="", index=True)
    invitation_expires_at: datetime
    peer_operator: str = ""
    peer_roster_hash: str = ""
    roster_checked_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    revoked_at: datetime | None = None


class FederatedGrant(SQLModel, table=True):
    connection_id: str = Field(primary_key=True, foreign_key="federatedconnection.id")
    user_id: str = Field(primary_key=True, foreign_key="user.id")
    created_by: str = Field(foreign_key="user.id")
    revoked_at: datetime | None = None


class FederatedPeerMember(SQLModel, table=True):
    connection_id: str = Field(primary_key=True, foreign_key="federatedconnection.id")
    matrix_user_id: str = Field(primary_key=True)
    name: str
    revoked_devices_json: str = "[]"
    # Cache only: fresh signed remote authorization is required before a send.
    active: bool = True
