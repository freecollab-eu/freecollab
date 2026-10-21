"""Personal provider delegations. Search never creates a second content store."""
from datetime import datetime
from uuid import uuid4
from sqlmodel import Field, SQLModel


class EnterpriseSearchConnection(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    name: str
    provider: str
    config_json: str
    encrypted_credentials: str
    revision: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)
