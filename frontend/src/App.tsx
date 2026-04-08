import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';

type SessionType = 'main' | 'child';
type SessionStatus = 'idle' | 'streaming' | 'error';
type MessageType = 'user' | 'assistant' | 'system' | 'switch_card' | 'switch_record' | 'error';

interface Session {
  sessionId: string;
  type: SessionType;
  title: string;
  status: SessionStatus;
  createdAt: string;
  updatedAt: string;
  parentSessionId: string | null;
  childSessionId: string | null;
  linkedAgentId: string | null;
  difyConversationId: string | null;
  sourceMessageId: string | null;
  summary?: string | null;
}

interface Message {
  messageId: string;
  sessionId: string;
  type: MessageType;
  role: 'user' | 'assistant' | 'system';
  content: string;
  metadata: Record<string, unknown>;
  createdAt: string;
  status: string;
}

const apiBase = 'http://localhost:8000/api';

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    throw new Error(await response.text());
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

async function consumeSSE(
  sessionId: string,
  payload: { content: string; sourceMessageId?: string },
  onEvent: (event: string, data: unknown) => void,
): Promise<void> {
  const response = await fetch(`${apiBase}/sessions/${sessionId}/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (!response.ok || !response.body) {
    throw new Error('SSE request failed');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split('\n\n');
    buffer = chunks.pop() ?? '';

    for (const chunk of chunks) {
      const lines = chunk.split('\n');
      const eventLine = lines.find((line) => line.startsWith('event:'));
      const dataLine = lines.find((line) => line.startsWith('data:'));
      if (!eventLine || !dataLine) {
        continue;
      }

      const eventName = eventLine.replace('event:', '').trim();
      const data = JSON.parse(dataLine.replace('data:', '').trim()) as unknown;
      onEvent(eventName, data);
    }
  }
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}

export function App() {
  const [mainSessions, setMainSessions] = useState<Session[]>([]);
  const [activeSession, setActiveSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messageEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    void bootstrap();
  }, []);

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  async function bootstrap() {
    setLoading(true);
    try {
      const sessions = await api<Session[]>('/sessions');
      setMainSessions(sessions);
      if (sessions.length > 0) {
        await selectSession(sessions[0].sessionId);
      } else {
        const created = await api<Session>('/sessions', { method: 'POST', body: JSON.stringify({ title: '新会话' }) });
        setMainSessions([created]);
        await selectSession(created.sessionId, created);
      }
    } finally {
      setLoading(false);
    }
  }

  async function refreshMainSessions(preferredSessionId?: string) {
    const sessions = await api<Session[]>('/sessions');
    setMainSessions(sessions);
    const targetId = preferredSessionId ?? (activeSession?.type === 'main' ? activeSession.sessionId : activeSession?.parentSessionId);
    if (!targetId && sessions[0]) {
      setActiveSession(sessions[0]);
      return;
    }
    const matched = sessions.find((item) => item.sessionId === targetId);
    if (matched && activeSession?.type === 'main') {
      setActiveSession(matched);
    }
  }

  async function selectSession(sessionId: string, provided?: Session) {
    const session = provided ?? await api<Session>(`/sessions/${sessionId}`);
    const sessionMessages = await api<Message[]>(`/sessions/${sessionId}/messages`);
    setActiveSession(session);
    setMessages(sessionMessages);
    setError(null);
  }

  async function createSession() {
    const session = await api<Session>('/sessions', { method: 'POST', body: JSON.stringify({ title: '新会话' }) });
    await refreshMainSessions(session.sessionId);
    await selectSession(session.sessionId, session);
  }

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    if (!activeSession || !input.trim() || sending) {
      return;
    }

    const content = input.trim();
    setInput('');
    setSending(true);
    setError(null);

    try {
      await consumeSSE(activeSession.sessionId, { content }, async (eventName, data) => {
        if (eventName === 'reply_delta') {
          await reloadCurrentSession();
        }
        if (eventName === 'switch_suggestion') {
          const message = data as Message;
          setMessages((current) => [...current, message]);
        }
        if (eventName === 'error') {
          const message = data as Message;
          setMessages((current) => [...current, message]);
          setError(message.content);
        }
      });
      await reloadCurrentSession();
      await refreshMainSessions();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '发送失败');
    } finally {
      setSending(false);
    }
  }

  async function reloadCurrentSession() {
    if (!activeSession) {
      return;
    }
    const session = await api<Session>(`/sessions/${activeSession.sessionId}`);
    const sessionMessages = await api<Message[]>(`/sessions/${activeSession.sessionId}/messages`);
    setActiveSession(session);
    setMessages(sessionMessages);
  }

  async function rejectSwitch(messageId: string) {
    if (!activeSession) {
      return;
    }
    await api(`/sessions/${activeSession.sessionId}/switch-reject`, {
      method: 'POST',
      body: JSON.stringify({ messageId }),
    });
    await reloadCurrentSession();
  }

  async function confirmSwitch(message: Message) {
    if (!activeSession) {
      return;
    }
    const child = await api<Session>(`/sessions/${activeSession.sessionId}/switch-confirm`, {
      method: 'POST',
      body: JSON.stringify({ messageId: message.messageId }),
    });
    await refreshMainSessions(activeSession.sessionId);
    await selectSession(child.sessionId, child);
    const sourceUserContent = String(message.metadata.sourceUserContent ?? '');
    const sourceMessageId = String(message.metadata.sourceUserMessageId ?? '');
    if (sourceUserContent) {
      setSending(true);
      try {
        await consumeSSE(child.sessionId, { content: sourceUserContent, sourceMessageId }, async () => {
          await reloadCurrentSession();
        });
        await reloadCurrentSession();
      } finally {
        setSending(false);
      }
    }
  }

  async function enterChildSession() {
    if (!activeSession || activeSession.type !== 'main' || !activeSession.childSessionId) {
      return;
    }
    await selectSession(activeSession.childSessionId);
  }

  async function backToMainSession() {
    if (!activeSession || activeSession.type !== 'child' || !activeSession.parentSessionId) {
      return;
    }
    await selectSession(activeSession.parentSessionId);
    await refreshMainSessions(activeSession.parentSessionId);
  }

  const childSummary = useMemo(() => {
    if (!activeSession || activeSession.type !== 'main' || !activeSession.childSessionId) {
      return null;
    }
    const switchRecord = [...messages].reverse().find((message) => message.type === 'switch_record');
    return switchRecord;
  }, [activeSession, messages]);

  if (loading) {
    return <div className="app loading">加载中...</div>;
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar-header">
          <h1>AI多智能体调度系统</h1>
          <button onClick={() => void createSession()}>新建会话</button>
        </div>

        <div className="session-list">
          {mainSessions.map((session) => (
            <button
              key={session.sessionId}
              className={`session-item ${activeSession?.sessionId === session.sessionId || activeSession?.parentSessionId === session.sessionId ? 'active' : ''}`}
              onClick={() => void selectSession(session.sessionId, session)}
            >
              <strong>{session.title}</strong>
              <span>{session.childSessionId ? `已派生: ${session.linkedAgentId ?? '智能体'}` : '通用'}</span>
              <small>{formatTime(session.updatedAt)}</small>
            </button>
          ))}
        </div>
      </aside>

      <main className="chat-panel">
        {activeSession && (
          <>
            <header className="chat-header">
              <div>
                <h2>{activeSession.title}</h2>
                <p>
                  {activeSession.type === 'main'
                    ? '通用助手'
                    : `子会话 · ${activeSession.linkedAgentId ?? 'Dify 智能体'}`}
                </p>
              </div>

              <div className="header-actions">
                <span className={`status status-${activeSession.status}`}>{activeSession.status}</span>
                {activeSession.type === 'main' && activeSession.childSessionId && (
                  <button onClick={() => void enterChildSession()}>查看子会话</button>
                )}
                {activeSession.type === 'child' && (
                  <button onClick={() => void backToMainSession()}>返回主会话</button>
                )}
              </div>
            </header>

            {activeSession.type === 'main' && childSummary && (
              <section className="child-card">
                <div>
                  <strong>{String(childSummary.metadata.targetAgentName ?? '已关联子会话')}</strong>
                  <p>{childSummary.content}</p>
                </div>
                <button onClick={() => void enterChildSession()}>进入子会话</button>
              </section>
            )}

            <section className="message-list">
              {messages.map((message) => {
                if (message.type === 'switch_card') {
                  const decision = String(message.metadata.decision ?? 'pending');
                  return (
                    <div key={message.messageId} className="message system-card">
                      <div className="card-title">建议切换到 {String(message.metadata.targetAgentName ?? '')}</div>
                      <p>{message.content}</p>
                      <small>{String(message.metadata.reason ?? '')}</small>
                      <div className="card-actions">
                        <button disabled={decision !== 'pending' || sending} onClick={() => void confirmSwitch(message)}>
                          确认切换
                        </button>
                        <button disabled={decision !== 'pending' || sending} onClick={() => void rejectSwitch(message.messageId)}>
                          继续当前助手
                        </button>
                      </div>
                      <span className="card-status">状态: {decision}</span>
                    </div>
                  );
                }

                if (message.type === 'switch_record') {
                  return (
                    <div key={message.messageId} className="message system-record">
                      <p>{message.content}</p>
                      <button onClick={() => void selectSession(String(message.metadata.childSessionId))}>进入子会话</button>
                    </div>
                  );
                }

                return (
                  <div key={message.messageId} className={`message bubble ${message.role}`}>
                    <ReactMarkdown>{message.content}</ReactMarkdown>
                  </div>
                );
              })}
              <div ref={messageEndRef} />
            </section>

            <form className="composer" onSubmit={sendMessage}>
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder={activeSession.type === 'main' ? '输入你的问题...' : '继续向子会话追问...'}
                disabled={sending}
              />
              <button type="submit" disabled={sending || !input.trim()}>
                {sending ? '发送中...' : '发送'}
              </button>
            </form>

            {error && <div className="page-error">{error}</div>}
          </>
        )}
      </main>
    </div>
  );
}
