import json
import re
import uuid
from typing import Iterable

from .models import AgentConfig, Message, RoutingDecision, Session, now_iso
from .storage import JsonStorage


def build_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class ConfigService:
    def __init__(self, storage: JsonStorage) -> None:
        self.storage = storage

    def get_agents(self) -> AgentConfig:
        raw = self.storage.read_json("agents.json", DEFAULT_AGENTS)
        return AgentConfig.model_validate(raw)

    def get_agent(self, agent_id: str):
        agents = self.get_agents()
        return next(
            (agent for agent in agents.difyAgents if agent.agentId == agent_id), None
        )


class SessionService:
    def __init__(self, storage: JsonStorage) -> None:
        self.storage = storage

    def list_sessions(self) -> list[Session]:
        items = self.storage.read_json("sessions.json", [])
        sessions = [Session.model_validate(item) for item in items]
        return sorted(
            (s for s in sessions if s.type == "main"),
            key=lambda item: item.createdAt,
            reverse=True,
        )

    def list_all_sessions(self) -> list[Session]:
        items = self.storage.read_json("sessions.json", [])
        return [Session.model_validate(item) for item in items]

    def get_session(self, session_id: str) -> Session:
        sessions = self.list_all_sessions()
        for session in sessions:
            if session.sessionId == session_id:
                return session
        raise KeyError(session_id)

    def save_sessions(self, sessions: Iterable[Session]) -> None:
        self.storage.write_json(
            "sessions.json", [session.model_dump() for session in sessions]
        )

    def create_main_session(self, title: str = "新会话") -> Session:
        now = now_iso()
        session = Session(
            sessionId=build_id("main"),
            type="main",
            title=title,
            createdAt=now,
            updatedAt=now,
        )
        sessions = self.list_all_sessions()
        sessions.append(session)
        self.save_sessions(sessions)
        return session

    def update_session(self, updated: Session) -> Session:
        sessions = self.list_all_sessions()
        merged: list[Session] = []
        for session in sessions:
            merged.append(
                updated if session.sessionId == updated.sessionId else session
            )
        self.save_sessions(merged)
        return updated

    def create_child_session(
        self,
        main_session: Session,
        agent_id: str,
        source_message_id: str,
        summary: str,
        agent_name: str,
    ) -> Session:
        now = now_iso()
        child = Session(
            sessionId=build_id("child"),
            type="child",
            title=f"{main_session.title} / {agent_name}",
            createdAt=now,
            updatedAt=now,
            parentSessionId=main_session.sessionId,
            linkedAgentId=agent_id,
            sourceMessageId=source_message_id,
            summary=summary,
        )
        sessions = self.list_all_sessions()
        updated_sessions: list[Session] = []
        for session in sessions:
            if session.sessionId == main_session.sessionId:
                session.childSessionId = child.sessionId
                session.linkedAgentId = agent_id
                session.updatedAt = now
            updated_sessions.append(session)
        updated_sessions.append(child)
        self.save_sessions(updated_sessions)
        return child


class MessageService:
    def __init__(self, storage: JsonStorage) -> None:
        self.storage = storage

    def list_messages(self, session_id: str) -> list[Message]:
        items = self.storage.read_json("messages.json", [])
        messages = [Message.model_validate(item) for item in items]
        return [message for message in messages if message.sessionId == session_id]

    def list_all_messages(self) -> list[Message]:
        items = self.storage.read_json("messages.json", [])
        return [Message.model_validate(item) for item in items]

    def save_messages(self, messages: Iterable[Message]) -> None:
        self.storage.write_json(
            "messages.json", [message.model_dump() for message in messages]
        )

    def append_message(self, message: Message) -> Message:
        messages = self.list_all_messages()
        messages.append(message)
        self.save_messages(messages)
        return message

    def get_message(self, message_id: str) -> Message:
        for message in self.list_all_messages():
            if message.messageId == message_id:
                return message
        raise KeyError(message_id)

    def update_message(self, updated: Message) -> Message:
        messages = self.list_all_messages()
        merged: list[Message] = []
        for message in messages:
            merged.append(
                updated if message.messageId == updated.messageId else message
            )
        self.save_messages(merged)
        return updated


class SummaryService:
    def build_summary(self, messages: list[Message]) -> str:
        conversation = [
            message for message in messages if message.type in {"user", "assistant"}
        ][-6:]
        if not conversation:
            return "无历史摘要。"

        lines = [f"{message.role}: {message.content}" for message in conversation]
        return "最近对话摘要:\n" + "\n".join(lines)


class RoutingService:
    def __init__(self, config_service: ConfigService) -> None:
        self.config_service = config_service

    async def route(self, user_message: str, has_child: bool) -> RoutingDecision:
        if has_child:
            return RoutingDecision(
                assistantReply="当前主会话已经绑定了一个专业子会话，如需继续专业处理，可直接进入已有子会话。",
                shouldSwitch=False,
            )

        agents = self.config_service.get_agents().difyAgents
        lower_text = user_message.lower()
        for agent in agents:
            keywords = [
                segment.strip()
                for segment in re.split(r"[、,，\s]+", agent.routingPrompt)
                if segment.strip()
            ]
            if any(keyword.lower() in lower_text for keyword in keywords):
                return RoutingDecision(
                    assistantReply=f"这是一个偏专业的问题，我先给你一个初步判断：{agent.name}会更适合继续处理。",
                    shouldSwitch=True,
                    targetAgentId=agent.agentId,
                    reason=f"问题命中了 {agent.name} 的处理范围。",
                    cardPrompt=f"建议切换到 {agent.name} 继续处理。",
                )

        return RoutingDecision(
            assistantReply=f"我先基于当前信息给出初步回答：{user_message}",
            shouldSwitch=False,
        )


class DifyService:
    async def reply(
        self, session: Session, user_message: str, agent_name: str
    ) -> tuple[str, str]:
        conversation_id = session.difyConversationId or build_id("dify")
        answer = f"[{agent_name}] 已接管该子会话。\n\n结合主会话摘要，我对你的问题“{user_message}”的最简处理建议如下：\n1. 先拆解目标。\n2. 明确关键风险或约束。\n3. 逐项给出执行建议。"
        return answer, conversation_id

    async def retry(
        self, session: Session, user_message: str, agent_name: str
    ) -> tuple[str, str]:
        return await self.reply(session, user_message, agent_name)


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


DEFAULT_AGENTS = {
    "generalModel": {
        "baseUrl": "",
        "apiKey": "",
        "model": "mock-general-model",
        "systemPrompt": "你是主会话调度助手。先给出简短回答，再判断是否建议切换到专业智能体。",
    },
    "difyAgents": [
        {
            "agentId": "contract-review",
            "sessionId": "agent_contract_review",
            "name": "合同审核助手",
            "description": "处理合同审查、风险分析类任务",
            "difyAppId": "demo-contract-review",
            "routingPrompt": "合同、风险、法务、条款、采购",
            "apiKey": "",
            "baseUrl": "",
            "workflowType": "chatflow",
        },
        {
            "agentId": "sql-expert",
            "sessionId": "agent_sql_expert",
            "name": "SQL 助手",
            "description": "处理 SQL、数据库查询与优化类任务",
            "difyAppId": "demo-sql-expert",
            "routingPrompt": "sql、数据库、查询、索引、慢查询",
            "apiKey": "",
            "baseUrl": "",
            "workflowType": "chatflow",
        },
    ],
}
