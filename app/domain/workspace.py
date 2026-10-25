"""User-facing workspace contracts, independent of immutable execution snapshots."""
from typing import Literal, Annotated
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from app.domain.project import ProjectInput
from app.domain.research import RunBudget


class WorkspaceProjectInput(ProjectInput):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    expected_settings_revision: int = Field(default=1, ge=1)
    memory_enabled: bool = True


class ConversationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=120)


class ConversationEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None


class MessageInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: str = Field(min_length=1, max_length=4000)
    request_id: UUID = Field(default_factory=uuid4)
    reply_to_message_id: UUID | None = None
    mode: Literal['auto', 'quick', 'overview', 'focused'] = 'auto'
    source_version_ids: list[UUID] = Field(default_factory=list, max_length=12)
    allow_network: bool = False
    memory_enabled: bool = True
    incremental: bool = False
    budget: RunBudget | None = None
