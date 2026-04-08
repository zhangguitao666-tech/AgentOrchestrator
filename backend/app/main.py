import asyncio
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .models import (
    CreateSessionRequest,
    Message,
    StreamRequest,
    SwitchConfirmRequest,
    SwitchRejectRequest,
    now_iso,
)
from .services import (
    ConfigService,
    DifyService,
    MessageService,
    RoutingService,
    SessionService,
    SummaryService,
    build_id,
    sse_event,
)
from .storage import JsonStorage


app = FastAPI(title="AI多智能体调度系统 MVP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_storage() -> JsonStorage:
    return JsonStorage(Path(__file__).resolve().parents[1] / "data")


def get_session_service(storage: JsonStorage = Depends(get_storage)) -> SessionService:
    return SessionService(storage)


def get_message_service(storage: JsonStorage = Depends(get_storage)) -> MessageService:
    return MessageService(storage)


def get_config_service(storage: JsonStorage = Depends(get_storage)) -> ConfigService:
    return ConfigService(storage)


def get_routing_service(
    config_service: ConfigService = Depends(get_config_service),
) -> RoutingService:
    return RoutingService(config_service)


def get_summary_service() -> SummaryService:
    return SummaryService()


def get_dify_service() -> DifyService:
    return DifyService()


@app.get("/api/sessions")
async def list_sessions(session_service: SessionService = Depends(get_session_service)):
    return session_service.list_sessions()


@app.post("/api/sessions")
async def create_session(
    body: CreateSessionRequest,
    session_service: SessionService = Depends(get_session_service),
):
    return session_service.create_main_session(body.title)


@app.get("/api/sessions/{session_id}")
async def get_session(
    session_id: str, session_service: SessionService = Depends(get_session_service)
):
    try:
        return session_service.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@app.get("/api/sessions/{session_id}/messages")
async def get_messages(
    session_id: str, message_service: MessageService = Depends(get_message_service)
):
    return message_service.list_messages(session_id)


@app.get("/api/sessions/{session_id}/child")
async def get_child_session(
    session_id: str, session_service: SessionService = Depends(get_session_service)
):
    session = session_service.get_session(session_id)
    if not session.childSessionId:
        return None
    return session_service.get_session(session.childSessionId)


@app.post("/api/sessions/{session_id}/switch-confirm")
async def switch_confirm(
    session_id: str,
    body: SwitchConfirmRequest,
    session_service: SessionService = Depends(get_session_service),
    message_service: MessageService = Depends(get_message_service),
    summary_service: SummaryService = Depends(get_summary_service),
    config_service: ConfigService = Depends(get_config_service),
):
    session = session_service.get_session(session_id)
    if session.type != "main":
        raise HTTPException(status_code=400, detail="Only main session can switch")
    if session.childSessionId:
        return session_service.get_session(session.childSessionId)

    message = message_service.get_message(body.messageId)
    target_agent_id = message.metadata.get("targetAgentId")
    agent = config_service.get_agent(target_agent_id)
    if not agent:
        raise HTTPException(status_code=400, detail="Target agent not found")

    summary = summary_service.build_summary(
        message_service.list_messages(session.sessionId)
    )
    child = session_service.create_child_session(
        session, agent.agentId, message.messageId, summary, agent.name
    )

    message.metadata["decision"] = "confirmed"
    message.status = "done"
    message_service.update_message(message)

    record = Message(
        messageId=build_id("msg"),
        sessionId=session.sessionId,
        type="switch_record",
        role="system",
        content=f"已派生子会话：{child.title}",
        metadata={
            "childSessionId": child.sessionId,
            "targetAgentId": agent.agentId,
            "targetAgentName": agent.name,
        },
        createdAt=now_iso(),
        status="done",
    )
    message_service.append_message(record)
    return child


@app.post("/api/sessions/{session_id}/switch-reject")
async def switch_reject(
    session_id: str,
    body: SwitchRejectRequest,
    message_service: MessageService = Depends(get_message_service),
):
    _ = session_id
    message = message_service.get_message(body.messageId)
    message.metadata["decision"] = "rejected"
    message.status = "done"
    return message_service.update_message(message)


@app.post("/api/sessions/{session_id}/retry/{message_id}")
async def retry_message(
    session_id: str,
    message_id: str,
    session_service: SessionService = Depends(get_session_service),
    message_service: MessageService = Depends(get_message_service),
    config_service: ConfigService = Depends(get_config_service),
    dify_service: DifyService = Depends(get_dify_service),
):
    session = session_service.get_session(session_id)
    message = message_service.get_message(message_id)
    agent = config_service.get_agent(session.linkedAgentId or "")
    if not agent:
        raise HTTPException(status_code=400, detail="Agent not found")

    reply, conversation_id = await dify_service.retry(
        session, message.content, agent.name
    )
    assistant_message = Message(
        messageId=build_id("msg"),
        sessionId=session.sessionId,
        type="assistant",
        role="assistant",
        content=reply,
        metadata={},
        createdAt=now_iso(),
        status="done",
    )
    message_service.append_message(assistant_message)
    session.difyConversationId = conversation_id
    session.status = "idle"
    session.updatedAt = now_iso()
    session_service.update_session(session)
    return {"ok": True}


@app.post("/api/sessions/{session_id}/stream")
async def stream_message(
    session_id: str,
    body: StreamRequest,
    session_service: SessionService = Depends(get_session_service),
    message_service: MessageService = Depends(get_message_service),
    routing_service: RoutingService = Depends(get_routing_service),
    config_service: ConfigService = Depends(get_config_service),
    dify_service: DifyService = Depends(get_dify_service),
):
    session = session_service.get_session(session_id)
    user_message = Message(
        messageId=build_id("msg"),
        sessionId=session.sessionId,
        type="user",
        role="user",
        content=body.content,
        metadata={},
        createdAt=now_iso(),
        status="done",
    )
    message_service.append_message(user_message)

    if session.title == "新会话" and session.type == "main":
        session.title = body.content[:20]
    session.status = "streaming"
    session.updatedAt = now_iso()
    session_service.update_session(session)

    async def event_generator():
        yield sse_event("status", {"status": "streaming"})
        await asyncio.sleep(0.05)

        if session.type == "main":
            decision = await routing_service.route(
                body.content, has_child=bool(session.childSessionId)
            )
            assistant_message = Message(
                messageId=build_id("msg"),
                sessionId=session.sessionId,
                type="assistant",
                role="assistant",
                content=decision.assistantReply,
                metadata={},
                createdAt=now_iso(),
                status="done",
            )
            message_service.append_message(assistant_message)

            yield sse_event("reply_delta", {"message": assistant_message.content})
            yield sse_event("reply_done", {"messageId": assistant_message.messageId})

            if decision.shouldSwitch and decision.targetAgentId:
                agent = config_service.get_agent(decision.targetAgentId)
                if agent:
                    card = Message(
                        messageId=build_id("msg"),
                        sessionId=session.sessionId,
                        type="switch_card",
                        role="system",
                        content=decision.cardPrompt or f"建议切换到 {agent.name}。",
                        metadata={
                            "targetAgentId": agent.agentId,
                            "targetAgentName": agent.name,
                            "reason": decision.reason,
                            "decision": "pending",
                            "sourceUserMessageId": user_message.messageId,
                            "sourceUserContent": user_message.content,
                        },
                        createdAt=now_iso(),
                        status="pending",
                    )
                    message_service.append_message(card)
                    yield sse_event("switch_suggestion", card.model_dump())
        else:
            agent = config_service.get_agent(session.linkedAgentId or "")
            if not agent:
                error_message = Message(
                    messageId=build_id("msg"),
                    sessionId=session.sessionId,
                    type="error",
                    role="system",
                    content="未找到子会话绑定的智能体配置。",
                    metadata={"sourceMessageId": user_message.messageId},
                    createdAt=now_iso(),
                    status="error",
                )
                message_service.append_message(error_message)
                session.status = "error"
                session.updatedAt = now_iso()
                session_service.update_session(session)
                yield sse_event("error", error_message.model_dump())
                return

            reply, conversation_id = await dify_service.reply(
                session, body.content, agent.name
            )
            assistant_message = Message(
                messageId=build_id("msg"),
                sessionId=session.sessionId,
                type="assistant",
                role="assistant",
                content=reply,
                metadata={
                    "sourceMessageId": body.sourceMessageId or user_message.messageId
                },
                createdAt=now_iso(),
                status="done",
            )
            message_service.append_message(assistant_message)
            session.difyConversationId = conversation_id
            yield sse_event("reply_delta", {"message": assistant_message.content})
            yield sse_event("reply_done", {"messageId": assistant_message.messageId})

        session.status = "idle"
        session.updatedAt = now_iso()
        session_service.update_session(session)
        yield sse_event("status", {"status": "idle"})

    return StreamingResponse(event_generator(), media_type="text/event-stream")
