"""Pydantic models shared across the AuraBook pipeline."""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Role(str, Enum):
    """Supported OpenAI-style message roles."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ResponseGradient(str, Enum):
    """Intervention tiers used by AuraBook."""

    PASS_THROUGH = "pass_through"
    NOTES = "notes"
    GUIDE = "guide"
    WALKTHROUGH = "walkthrough"
    FULL = "full"


class FunctionCall(BaseModel):
    """Function-call payload for tool calls."""

    name: str
    arguments: str


class ToolCall(BaseModel):
    """OpenAI-compatible tool call representation."""

    id: str = Field(default_factory=lambda: f"call_{uuid.uuid4().hex[:24]}")
    type: Literal["function"] = "function"
    function: FunctionCall


class ContentPart(BaseModel):
    """Structured message content part."""

    type: str = "text"
    text: str | None = None

    model_config = ConfigDict(extra="allow")


class Message(BaseModel):
    """Single chat message."""

    role: Role
    content: str | list[ContentPart] | None = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None

    model_config = ConfigDict(extra="allow")

    def text_content(self) -> str:
        """Return the text-only representation of the message."""
        if isinstance(self.content, str):
            return self.content
        if not self.content:
            return ""
        return "\n".join(part.text for part in self.content if part.text)


class ChatCompletionRequest(BaseModel):
    """Universal inbound request model used by the proxy pipeline."""

    id: str = Field(default_factory=lambda: f"req_{uuid.uuid4().hex[:12]}")
    messages: list[Message]
    model: str = "gpt-4o-mini"
    temperature: float = 0.7
    max_tokens: int | None = None
    stream: bool = False
    stop: str | list[str] | None = None

    target_adapter: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)

    model_config = ConfigDict(extra="allow")

    @field_validator("messages")
    @classmethod
    def validate_messages(cls, value: list[Message]) -> list[Message]:
        """Reject requests that do not contain at least one message."""
        if not value:
            raise ValueError("messages must not be empty")
        return value


class Choice(BaseModel):
    """Single response choice."""

    index: int = 0
    message: Message
    finish_reason: str | None = "stop"


class Usage(BaseModel):
    """Token usage summary."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    """Universal outbound response model used by the proxy pipeline."""

    id: str = Field(default_factory=lambda: f"chatcmpl_{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = ""
    choices: list[Choice] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")

    @property
    def text(self) -> str:
        """Return the text content of the first assistant choice."""
        if not self.choices:
            return ""
        return self.choices[0].message.text_content()

    @property
    def has_tool_calls(self) -> bool:
        """Return whether the first choice includes tool calls."""
        if not self.choices:
            return False
        tool_calls = self.choices[0].message.tool_calls
        return bool(tool_calls)


class TrajectoryTurn(BaseModel):
    """Single stored prompt/response pair used for trajectory capture."""

    prompt: str
    response: str
    model: str | None = None
    thought: str | None = None
    tried: str | None = None
    outcome: str | None = None
    commands: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    token_count: int | None = None
    duration_ms: int | None = None
    timestamp: float = Field(default_factory=time.time)


class TrajectoryUploadPayload(BaseModel):
    """Payload sent to the cloud memory service when a success is detected."""

    client_id: str
    source: str = "local_client"
    cdc_hash: str | None = None
    original_goal: str | None = None
    original_prompt: str
    broken_code: str | None = None
    error_log: str
    ai_reflection: str | None = None
    final_fix: str
    history: list[TrajectoryTurn]
    metadata: dict[str, Any] = Field(default_factory=dict)


class CommunityPrefetchMatch(BaseModel):
    """Exact-match context returned from the Cloud Brain."""

    trajectory_id: str
    cdc_hash: str
    note_id: str | None = None
    original_goal: str | None = None
    final_fix: str
    note_text: str | None = None
    source_model: str | None = None
    ai_reflection: str | None = None

    model_config = ConfigDict(extra="ignore")


class CommunityPrefetchResponse(BaseModel):
    """Prefetch lookup response returned from the Cloud Brain."""

    found: bool
    match: CommunityPrefetchMatch | None = None

    model_config = ConfigDict(extra="ignore")


class CommunitySearchResult(BaseModel):
    """Semantic memory match returned from the Cloud Brain."""

    note_id: str
    trajectory_id: str
    note_text: str
    source_model: str
    similarity: float
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    original_prompt: str | None = None
    final_fix: str | None = None
    ai_reflection: str | None = None
    version: int | None = None
    helpful_count: int | None = None
    unhelpful_count: int | None = None
    suppress_count: int | None = None

    model_config = ConfigDict(extra="ignore")


class CommunitySearchResponse(BaseModel):
    """Semantic search response from the Cloud Brain."""

    matches: list[CommunitySearchResult] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class FeedbackKind(str, Enum):
    """Supported user feedback labels for interventions."""

    HELPFUL = "helpful"
    WRONG = "wrong"
    TOO_AGGRESSIVE = "too_aggressive"


class CommunityFeedbackRequest(BaseModel):
    """Feedback payload sent from the local client to the Cloud Brain."""

    note_id: str | None = None
    trajectory_id: str | None = None
    client_id: str
    feedback: FeedbackKind
    repository: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CommunityFeedbackResponse(BaseModel):
    """Feedback acknowledgement returned from the Cloud Brain."""

    accepted: bool = True
    note_id: str | None = None
    trajectory_id: str | None = None
    feedback: FeedbackKind
