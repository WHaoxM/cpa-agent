# AI 助手

> 物料 / 推荐形态契约见 [contracts/ai-assistant-materials.md](../contracts/ai-assistant-materials.md)。mock 回复、工具卡、成果物卡必须遵守该契约。

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/student/ai-assistant` |
| name | `student-ai-assistant` |
| roles | student |
| immersive | false |
| public | false |

## 页面职责

多轮职业咨询对话；Claude/Qoder 式 Composer（工具卡 / Files 挂载 / Voice Turn）。

- **http 模式**（`VITE_API_MODE=http`）：`POST /api/agent/chat` 真引擎 + session；附件走 `POST /api/agent/files` → session resources；语音走 Voice Turn；聊天只传 `attachment_ids` / `voice_turn_id`，**禁止**把全文塞进 `message`。
- **mock 模式**：本地回复 + 思考独白 + 模拟工具卡；UI 与 http 一致。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 发送消息（demo） | 发送 | `POST /api/agent/chat` | `mode:demo` | reply/tools | `agent/chat.demo.json` | backend-ready |
| 发送消息（真推理） | 发送 | `POST /api/agent/chat` | `mode:langgraph` + `session_id` | reply/tool_calls | — | backend-ready |
| 创建会话 | 新对话 | `POST /api/agent/session/create` | `{ student_id }` | session_id | `agent/session.create.json` | backend-ready |
| 拉历史 | （预留） | `GET /api/agent/session/<id>/history` | `limit` | messages | `agent/session.history.json` | backend-ready |
| 上传附件 | 回形针 | `POST /api/agent/files` | multipart `file` + student/session | `file_id` + excerpt | — | backend-ready |
| 挂载到会话 | 发送前 | `POST /api/agent/session/<id>/resources` | `{ resources:[{file_id}] }` | resources | — | backend-ready |
| 发送（带附件） | 发送 | `POST /api/agent/chat` | `message` + `attachment_ids` | reply/tool_calls | — | backend-ready |
| 任务状态 | 工具卡自动轮询 | `GET /api/pipeline/status/<task_id>` | — | status | — | backend-ready |
| 语音回合开始 | 麦克风 | `POST /api/agent/voice/turns` | session/student/lang | `turn_id` | — | backend-ready |
| 语音回合结束 | 识别完成 | `PATCH/POST .../voice/turns/<id>` | transcript + is_final | ready turn | — | backend-ready |
| 浏览器 STT | 麦克风 | Web Speech（适配器） | — | interim/final → VoiceTurn | — | local adapter |
| 职业建议列表 | 侧栏建议 | `POST /api/agent/career-suggestions` | 见下 | string[] | — | backend-ready |

### Request / Response 展开

#### 发送消息（真推理）

**Request**

```http
POST /api/agent/chat
Content-Type: application/json
```

```json
{
  "message": "哪些岗位需要 Python？",
  "student_id": "student_001",
  "session_id": "sess_001",
  "mode": "langgraph"
}
```

**Response（节选）**

```json
{
  "success": true,
  "data": {
    "reply": "…",
    "tool_calls": [
      {
        "tool_name": "graph_query",
        "parameters": { "question": "Python 相关岗位" },
        "result_preview": "…"
      }
    ],
    "session_id": "sess_001",
    "elapsed_seconds": 2.4,
    "engine": "supervisor",
    "active_subagent": "qa_subagent"
  }
}
```

**Client：** `api.agent.chat` + `mapAgentToolCalls`（`tool_name` → `name`，`result_preview` → `summary`）

#### 创建会话

**Fixture:** `cpa-xingtu/public/fixtures/agent/session.create.json`  
**Client：** `api.agent.createSession(studentId)`；新对话时重置并重建 session。

## 前端行为说明

| 行为 | 说明 |
|------|------|
| Session | 模块级 `agentSessionId`；每次 http 发送携带；「新对话」调用 `createSession` |
| 思考区 | mock：Claude 式独白流；http：处理中计时 + 完成后「思考了 Ns」（subagent/耗时） |
| 工具卡 | **零字段渲染**：只显示产品化摘要 + 状态（已完成/运行中/失败）；不显示工具函数名、参数、`task_id`（见物料契约 §4）；`pipeline_run` 任务轮询时 status=running |
| 附件 | Upload → `file_id` → session mount；Supervisor 挂载预算摘要卡；全文仅 Agent `attachment_read` |
| 语音 | Voice Turn 生命周期；浏览器 Web Speech 为 STT 适配器；支持 interim；可扩展服务端 STT |
| 快捷提问 | 一律走同一 `sendMessage` 路径（不再本地写死 reply） |
| 打字机 | 前端对真实 `reply` 做短流式展示 |

## Demo / 联调

- mock：`VITE_API_MODE=mock`，快捷问 + 附件占位可用
- http：本地后端 + `LLM_API_KEY`；验证多轮 `session_id` 不变、工具卡出现
- 身份默认 `student_001`

## 本轮明确不做

- Supervisor SSE 实时 `tool_call` 事件
- 服务端 STT 厂商接入（接口与 VoiceTurn 已预留 `stt_provider` / `audio_file_id`）
- 完整前端「批准读附件」弹窗（走既有 PreToolUse）
