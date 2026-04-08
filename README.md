# AgentOrchestrator

AI 多智能体调度系统最简 MVP。

## 技术栈

- 前端：`React + Vite + TypeScript`
- 后端：`FastAPI`
- 存储：本地 `JSON`
- 流式：`SSE`

## 目录结构

- `frontend/`：聊天界面
- `backend/`：FastAPI API 与本地 JSON 存储
- `backend/data/`：`agents.json`、`sessions.json`、`messages.json`

## 启动方式

### 1. 启动后端

```bash
python -m venv backend/.venv
backend/.venv/Scripts/pip install -r backend/requirements.txt
backend/.venv/Scripts/python -m uvicorn app.main:app --reload --app-dir backend
```

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

## MVP 范围

- 主会话聊天
- 结构化切换建议卡片
- 确认切换创建子会话
- 主子会话往返
- 本地 JSON 持久化
- 统一 SSE 事件

当前版本默认使用本地 mock 路由与 mock Dify 回复，便于直接演示；后续可替换为真实 OpenAI 兼容接口与 Dify API。
