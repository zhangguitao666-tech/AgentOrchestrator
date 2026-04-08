from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


SessionType = Literal["main", "child"]
SessionStatus = Literal["idle", "streaming", "error"]
MessageType = Literal[
    "user", "assistant", "system", "switch_card", "switch_record", "error"
]
MessageRole = Literal["user", "assistant", "system"]


class Session(BaseModel):
    sessionId: str
    type: SessionType
    title: str
    status: SessionStatus = "idle"
    createdAt: str
    updatedAt: str
    parentSessionId: str | None = None
    childSessionId: str | None = None
    linkedAgentId: str | None = None
    difyConversationId: str | None = None
    sourceMessageId: str | None = None
    summary: str | None = None


class Message(BaseModel):
    messageId: str
    sessionId: str
    type: MessageType
    role: MessageRole
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    createdAt: str
    status: str = "done"


class GeneralModelConfig(BaseModel):
    baseUrl: str = ""
    apiKey: str = ""
    model: str = ""
    systemPrompt: str = "你是主会话调度助手。"


class DifyAgentConfig(BaseModel):
    agentId: str
    sessionId: str
    name: str
    description: str
    difyAppId: str
    routingPrompt: str
    apiKey: str = ""
    baseUrl: str = ""
    workflowType: str = "chatflow"


class AgentConfig(BaseModel):
    generalModel: GeneralModelConfig = Field(default_factory=GeneralModelConfig)
    difyAgents: list[DifyAgentConfig] = Field(default_factory=list)


class RoutingDecision(BaseModel):
    assistantReply: str
    shouldSwitch: bool
    targetAgentId: str | None = None
    reason: str | None = None
    cardPrompt: str | None = None


class CreateSessionRequest(BaseModel):
    title: str = "新会话"


class StreamRequest(BaseModel):
    content: str = Field(min_length=1)
    sourceMessageId: str | None = None
    includeSummary: bool = False


class SwitchConfirmRequest(BaseModel):
    messageId: str


class SwitchRejectRequest(BaseModel):
    messageId: str


class RetryResponse(BaseModel):
    ok: bool


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
