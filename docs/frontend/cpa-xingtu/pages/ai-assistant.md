# AI 助手

> 物料 / 推荐形态契约见 [contracts/ai-assistant-materials.md](../contracts/ai-assistant-materials.md)。mock 回复、工具卡、成果物卡必须遵守该契约。

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/student/ai-assistant` |
| name | `student-ai-assistant` |
| roles | student |
| immersive | true |
| public | false |

## 页面职责

多轮职业咨询对话；会话栏、任务启动区与 Composer（工具卡 / Files 挂载 / Voice Turn）。

### 前端窗口复用边界（2026-10-09）

- `cpa-xingtu/src/views/student/AIAssistant.vue` 复用 `vue-element-plus-x@2.0.3`（MIT）的 `Conversations`、`Welcome`、`Prompts`、`Thinking`、`Attachments`、`XSender` 与 `Bubble`。组件负责会话列表、空态、快捷任务、思考展示、附件卡和富文本输入等**窗口交互**，业务状态仍走原 store/composable；`Bubble #content` 仍使用本项目 `MessagePartsRenderer.vue`，工具、引用、成果物和状态契约不换。库的 `--elx-*` 及 XSender 内部 `--chat-*` 均在 `src/assets/styles/theme.css` 的 `.ai-page` 映射现有语义 token，后续换主题不需逐组件重做。原创 `AgentMark.vue` 标识用于工作台、助手头像及移动端菜单，静态 SVG/PNG 位于 `public/agent-mark*`。
- 单窗口前端流程是「新任务空态 → 专家目录 → 专家详情 → 以该视角起草任务」。目录包含精选场景、专家/协作组切换、分类、搜索、排序和卡片；Composer 的 `+` 菜单可添加文件、切换处理方式、搜索并选择专家。数据在 `src/components/agent/expertCatalog.ts`，是前端任务视角，不是后端角色 ID，也不是 WorkBuddy 官方组件。
- 原有 `ask / agent / experts` 请求值保留，但主画面不再用三段技术模式占据输入区：默认 `agent` 对应「自动处理」，其他方式收在 Composer 的处理方式菜单。选中专家会把可编辑的视角说明/示例写入**可见输入框**，不覆盖已有草稿或附件，不自动发送，也不换掉当前会话；发送仍走现有链路，不请求 `/api/roles/chat`。独立专家角色接口尚未接通，不把 UI 选择冒充真实角色调用。新任务、切会话和发送完成会清理未发送的专家标记。
- mock 成果物经 `artifact_id + session_id` 进入平台已有的画像、职业分析、学习中心、简历制作、职业报告页面；目标页使用原有图表、表单与课程组件读取**同一份结构化 mock 数据**，不再用独立的摘要页或目标页另一套默认数据冒充本次结果。计划勾选、简历编辑等交互会同步回原会话产物引用；缺字段时在原功能页明确提示，不补造无关分数。mock 数据是演示素材，不代表前端组件本身是 mock。
- HTTP 模式的 `GET /api/agent/artifacts/<artifact_id>` 目前仅返回 owner-scoped 索引，不能作为完整业务正文；此模式不会把索引预览灌进模拟功能页。已有 `task_id` 接收契约的简历确认/管线任务继续走 `student-career-navigation?task_id=...`。正式业务记录跨页读取需另行接对应后端资源契约。
- 上游取证：公开 [Codex 仓库](https://github.com/openai/codex) 快照 `a06545b3`（Apache-2.0）不含 Desktop Web UI 源码；[WorkBuddy 产品页](https://cloud.tencent.com/product/workbuddy)提供客户端但没有开源窗口组件。参考其交互形态，不复制私有打包产物。可导入组件来自 [Element-Plus-X](https://github.com/element-plus-x/Element-Plus-X)（MIT）。官方 [DSH 仓库](https://github.com/deepseek-ai/deepseek-harness) 快照 `d7432673`（MIT）的 `packages/client/ui-chat`、`ui-conversation`、`ui-theme` 可作为交互与 token 分层参考；组件实际依赖 React 18、Cordis 插件和 Lexical 编辑器，不能只改 token 就移植到 Vue，故未引入其运行时。

- **http 模式**（`VITE_API_MODE=http`）：`POST /api/agent/chat` 真引擎 + session；附件走 `POST /api/agent/files` → session resources；语音走 Voice Turn；聊天只传 `attachment_ids` / `voice_turn_id`，**禁止**把全文塞进 `message`。
- **mock 模式**：本地回复 + 思考独白 + 模拟工具卡；每次创建任务覆盖 fixture 的固定 `session_id`，生成新的本地会话 ID，避免不同任务混用同一转录；消息卡与现有功能页共用本次模拟产物状态。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 发送消息（demo） | 发送 | `POST /api/agent/chat` | `mode:demo` | reply/tools | `agent/chat.demo.json` | backend-ready |
| 发送消息（真推理） | 发送 | `POST /api/agent/chat` | `mode:langgraph` + `session_id` | reply/tool_calls | — | backend-ready |
| 创建会话 | 新任务 | `POST /api/agent/session/create` | `{ student_id }` | session_id | `agent/session.create.json` | backend-ready |
| 拉历史 | （预留） | `GET /api/agent/session/<id>/history` | `limit` | messages | `agent/session.history.json` | backend-ready |
| 上传附件 | 回形针 | `POST /api/agent/files` | multipart `file` + student/session | `file_id` + excerpt | — | backend-ready |
| 挂载到会话 | 发送前 | `POST /api/agent/session/<id>/resources` | `{ resources:[{file_id}] }` | resources | — | backend-ready |
| 发送（带附件） | 发送 | `POST /api/agent/chat` | `message` + `attachment_ids` | reply/tool_calls | — | backend-ready |
| 读取成果物索引 | 成果物详情 | `GET /api/agent/artifacts/<artifact_id>` | owner scope | status/preview/resource pointer | 对话 mock 引用 | backend-ready（完整 payload 仍在业务库） |
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
| 本轮进度 | 仅当公共 `progress` 含计划步骤、待补信息或附件状态等用户可读内容时渲染；原生 `<details>` 可用鼠标/键盘展开。仅有 `path`、`allow_tools` 等路由元数据时不显示空卡。`partial/failed` 用用户语言说明未完成，不暗示存在断点续跑。 |
| 工具卡 | **零字段渲染**：只显示产品化摘要 + 状态（已完成/运行中/失败）；不显示工具函数名、参数、`task_id`（见物料契约 §4）；`pipeline_run` 任务轮询时 status=running |
| 附件 | Upload → `file_id` → session mount；Supervisor 挂载预算摘要卡；全文仅 Agent `attachment_read` |
| 语音 | Voice Turn 生命周期；浏览器 Web Speech 为 STT 适配器；支持 interim；可扩展服务端 STT |
| 快捷提问 | 一律走同一 `sendMessage` 路径（不再本地写死 reply） |
| 打字机 | mock 可做展示模拟；http 目前返回一次性 JSON，界面仅显示处理中与最终原文，不伪装真实增量流 |

## Demo / 联调

- mock：`VITE_API_MODE=mock`，快捷问 + 附件占位可用
- http：本地后端 + `LLM_API_KEY`；验证多轮 `session_id` 不变、工具卡出现
- 身份默认 `student_001`

## 本轮明确不做

- Supervisor SSE 实时 `tool_call` 事件
- 服务端 STT 厂商接入（接口与 VoiceTurn 已预留 `stt_provider` / `audio_file_id`）
- 完整前端「批准读附件」弹窗（走既有 PreToolUse）
