# Agent 真实 HTTP 边界验收

面向维护者；用于复现用户可见边界故障，再下钻进程内定位。本文覆盖三条确定性 HTTP 旅程，不是完整模型质量或生产依赖验收。

## 验收边界

`backend/tests/test_agent_http_journeys.py` 每条旅程启动独立 Python 后端进程。子进程使用真实 `create_app()` 和 Werkzeug 监听 `127.0.0.1` 的操作系统分配端口；父进程以 `requests.Session` 发 HTTP。**没有 Flask `test_client`，也不靠每请求新连接绕过 SSE 后的连接问题。**

真实执行：

- strict 身份解析与显式开启的 loopback DevAuth；缺身份 401、异 owner 403。
- `/session/create`、`/chat`、`/chat/stream`、history、文件上传/查询及 resources 路由。
- `_run_recorded_supervisor_turn`、`_run_supervisor` 的 history/context 组装、SessionManager、单进程 SessionCoordinator、公开 egress。
- TXT 字节保存、实际文本解析、附件 owner 校验、磁盘元数据和挂载目录。

明确替身：

| 依赖 | 替身 | 不证明什么 |
|---|---|---|
| `get_supervisor_service().chat` | 根据 history 计数的确定性回复，记录调用输入并返回下一轮黑板 | 真实 Supervisor/ModeRuntime、LLM 的上下文理解、路由、工具执行、token 预算与回答质量 |
| Redis | 复用 `test_session_turn_coordination._SharedSessionRedis`，每次会话写入同时留测试证据 | 实际 Redis 持久性、断线恢复、跨 Worker 租约 |
| MySQL | 空查询/不写库的适配器；关闭自动 schema 写入 | SQL schema、MySQL 会话投影、实际事务与持久性 |

文件与挂载的磁盘状态是真实资源状态；`*.state.json` 是**替身会话存储在写入点的观测副本**，不是生产 Redis 的证明。它与 HTTP history、下一轮收到的 history/context 交叉核验，避免只断言一份伪造回答。

只使用合成 TXT。PDF/DOCX、OCR Sidecar、Celery、外部知识服务、真实模型和浏览器不属于这些旅程。不要据此改写那些能力的验收状态。

## 三条用户旅程

### 1. 同一 session 连续对话

1. 无身份创建 session → 401。
2. owner 创建 session → JSON 第一轮 → SSE 第二轮。
3. 同一 `requests.Session` 随后 GET history，确认 4 条 user/assistant 顺序与回复重组结果。
4. 下一轮 Supervisor 替身实际收到上轮 user/assistant 及已结算 context；会话存储中的计数为 2。
5. 异 owner 读取/对话 → 403，存储和 Supervisor 调用次数不变。

### 2. 上传、引用与 JSON/SSE 语义

1. 创建 session → multipart 上传 TXT → GET 文件 ready。
2. 上传本身没有 user/assistant 消息、没有模型调用，也没有自动挂载。
3. JSON chat 显式引用 `file_id` → owner-bound 准备/挂载 → GET resources；与真实磁盘挂载记录、文件 owner/status、会话消息附件引用一致。
4. SSE chat 再次引用 → 挂载幂等；event id 连续、turn id 每轮独立、`turn_start → reply_delta → turn_done → [DONE]`，最终 outcome 与 JSON 相同。
5. 仅将 `attachment_ids` 改为不存在的文件：JSON 404；SSE 在已建立的 200 流中给 `RESOURCE_NOT_FOUND`、`[DONE]`，不产生 `turn_done`，不写消息、不调用 Supervisor。

### 3. 下一轮引用本会话已挂载的附件

1. owner 在 session A 上传并挂载合成 TXT；第一轮消息记录该轮的 `attachment_ids`。
2. 同一 owner 在同一 session A 继续问“刚才上传的简历”，不重传附件 ID；第二轮消息的当前轮附件列表为空，但 Session Resources 仍有该附件。
3. Ask 只为这条明确引用启用 `attachment_read`；文件工具再次校验 owner 与 session。只挂在其他 session 或其他 owner 名下的资源不可从本旅程读取。
4. 若用户切换到 session B，完整历史不会从 session A 自动带入；`use_memory` 是单独的可选记忆挂载，不等同于搜索全部旧聊天。

该旅程验证 HTTP 挂载/历史状态和资源边界；HTTP fixture 对 Supervisor 使用确定性替身，另有 ModeRuntime 单测验证只读工具 allowlist。它不调用真实模型，也不证明模型一定会选择工具或正确总结简历。

SSE comment `: connected` 只证明连接已建立，不是增量模型进度；当前测试不声称首 token 延迟或流式模型生成已验收。

## 一键回放

在仓库根目录 PowerShell 执行，使用 README 指定的 `mkg`，不需要先启动 Docker 或配置真实模型：

```powershell
Set-Location 'D:\AGeneral Workspace\cpa-agent'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest backend/tests/test_agent_http_journeys.py -q --tb=short
```

单独回放失败旅程：

```powershell
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest backend/tests/test_agent_http_journeys.py::test_http_multi_turn_preserves_session_history_and_context -q --tb=short
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest backend/tests/test_agent_http_journeys.py::test_http_upload_then_reference_orders_state_and_json_sse_semantics -q --tb=short
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest backend/tests/test_agent_http_journeys.py::test_http_followup_keeps_prior_session_attachment_addressable_without_resending -q --tb=short
```

只回放本会话附件旅程并输出不含文件名、ID、消息正文或文件正文的统计 trace：

```powershell
$traceRoot = Join-Path $env:TEMP ('cpa-agent-attachment-trace-' + [guid]::NewGuid().ToString('N'))
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest `
  backend/tests/test_agent_http_journeys.py::test_http_followup_keeps_prior_session_attachment_addressable_without_resending `
  -q --tb=short --basetemp $traceRoot
if ($LASTEXITCODE -ne 0) { throw 'Attachment continuity journey failed' }
$trace = Get-ChildItem -LiteralPath $traceRoot -Filter 'safe-trace.json' -Recurse | Select-Object -First 1
if (-not $trace) { throw 'safe-trace.json was not produced' }
Get-Content -Encoding UTF8 $trace.FullName
```

Trace 仅含 runtime/scope、历史消息计数、当前轮附件计数、session 挂载计数和异 owner HTTP 状态。fixture 还会保存其他合成请求/状态证据；不要把它们误认为真实用户数据或生产 Redis/MySQL。此 fixture 的 MySQL 替身不创建 Agent run，因此 `TaskContextStore.read` 被显式固定为空列表，避免把 SQLAlchemy 查询误打到只支持轻量查询的替身上。

每条旅程使用 pytest `tmp_path` 隔离存储和日志，自动设置测试身份边界；`finally` 中关闭客户端、终止并等待**该子进程**，失败也不会留下后端。pytest 临时目录保留以便审阅，由 pytest 的临时目录保留机制管理，不触碰工作区已有上传、数据库与日志。

## 证据与排障

失败报告中的 `http_backend` fixture 路径就是本次证据目录，通常位于 `%TEMP%\pytest-of-<user>\pytest-<n>\test_http_<journey>0`：

- `ready.json`：实际 PID、TCP 地址、替身清单。
- `requests.jsonl`：请求方法、路径、合成 owner、JSON/form、上传文件名/大小/MIME、响应 headers/body 或网络异常。上传正文见测试中的合成 fixture。
- `server.log`、`logs/`：启动与请求日志。
- `supervisor-calls.jsonl`：实际进入替身时收到的 history、context、prepared_turn。
- `<session_id>.state.json`：会话存储写入观测。
- `uploads/agent_files/`、`uploads/session_mounts/`：真实解析和挂载产物。

先找第一条偏离预期的 HTTP 请求及资源状态，再用现有窄测试（`test_agent_stream_parity.py`、`test_session_turn_coordination.py`、`test_agent_media.py`）定位。修复后必须重跑**原 HTTP 旅程**，不能仅以进程内绿灯替代。

## 源码导航：从症状下钻

```text
HTTP /api/agent/chat 或 /chat/stream
  → app/api/agent.py：身份、session owner、当前轮消息/附件记录、JSON/SSE 包装
  → _run_recorded_supervisor_turn → _run_supervisor
  → services/agent_runtime/supervisor.py → ModeRuntime.chat
  → 同 session history + context_assembler 预算 + Ask/Agent 分流
  → Ask：显式证据选择/附件范围 → scoped_invoke 与只读工具
  → attachment_read → document_access → 提取文本/coverage/分页结果
  → turn_contract：权威 outcome 和可回放事件
```

| 要查的问题 | 从这里开始 | 重点边界 |
|---|---|---|
| 历史消息没回来 | `app/api/agent.py` 的 history 路由；`services/session_manager.py::get_history_for_agent` | 必须是同一 owner、同一 `session_id`。完整聊天历史不跨 session 搜索。 |
| 附件卡片显示但回答没读正文 | `media/session_attachment_context.py` → `tool_policy.py` / `scoped_invoke.py` → `tools/attachment_read.py` → `media/document_access.py` | 当前轮附件沿现有 mount 流程；旧轮附件只在本轮明确提到文件/简历时、从同 session owner mount 列表开放只读工具。检查 ready 状态、工具结果、coverage/next offset，不能由卡片或附件数量推断已读。 |
| 工具没出现或被拒 | `agent_runtime/tool_policy.py`、`permissions.py`、`scoped_invoke.py`，再查工具自己的 owner/session 校验 | Ask 的只读工具由服务端逐项 allowlist；客户端或模型不能扩大权限。 |
| 任务显示运行中、完成但没更新 | 普通回合看 `agent_runtime/turn_contract.py` 与 JSON/SSE 事件；持久 Agent run 看 `api/agent_runs.py` → `agent_run_store.py` / `agent_run_dispatch.py` → `agent_run_jobs.py` → `agent_run_executor.py` | run/plan/step/dispatch 的 MySQL 状态是业务权威；Celery 负责执行/派发，LangGraph checkpointer 是执行阶段恢复器，不取代业务状态表。 |
| 回答像截断或漏掉附件尾部 | `agent_runtime/context_assembler.py`、`media/document_access.py`、`tools/attachment_read.py` | 分别查上下文预算、文档 coverage 与分页 offset；partial 必须标记，不把窗口当全文。 |
| provider 请求内容或解析失败 | `app/utils/llm_client.py::get_llm_client` 和 `LLMClient` | 不打印完整 prompt、密钥或私人正文；用脱敏来源计数、工具名、结果状态、预算和事件序号定位。 |

`context_assembler` 负责不同来源的预算拼装，不负责授权；权限边界在工具 allowlist、owner/session 绑定和各工具入口。上传接入及解析生命周期见《附件解析完整性：当前运行链与验证》；持久 Agent run 状态可从 `agent_run_store.py`、`agent_run_dispatch.py`、`agent_run_jobs.py` 和 `agent_run_executor.py` 开始追踪。

需要手工操作时，在前台启动独立测试 host（不是正式部署入口）：

```powershell
$state = Join-Path $env:TEMP ('cpa-http-manual-' + [guid]::NewGuid().ToString('N'))
Write-Host $state
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B backend/tests/fixtures/http_acceptance_server.py --state-dir $state --port 5087
```

另一终端可先执行健康和会话请求：

```powershell
curl.exe --noproxy '*' http://127.0.0.1:5087/health
curl.exe --noproxy '*' -X POST -H 'X-Dev-Student-Id: http_acceptance_owner' http://127.0.0.1:5087/api/agent/session/create
```

完整请求顺序以测试代码为可执行回放源。手动 host 用 `Ctrl+C` 停止，保留打印的状态目录供审阅；下次使用新目录，不能复用旧 `ready.json` 当作启动成功。若 5087 被占用，选另一空闲端口或省略 `--port` 并读取新目录中的 `ready.json`。

## 复用来源与适配边界

本次在 Ask 的 `ModeRuntime` 分支增加了“显式提到附件时，才为同一 owner/session 的已挂载文件开放只读工具”这一薄适配；没有改 Agent 持久状态机、解析器或预算核心，也没有引入新测试框架：pytest、requests、Werkzeug 均为现有环境依赖。

- 项目现有 `test_agent_stream_parity.py`、`test_session_turn_coordination.py`、`test_agent_media.py` 提供事件、会话、文件契约；直接复用 `_SharedSessionRedis`，不另造通用 Redis 模拟库。
- `opencode` commit `388406238bd5ca15564a762840a2362c3a45bd9c`，`packages/sdk/js/test/session-history.test.ts`：历史接口的稳定字段断言。该测试是 SDK 契约，不被误称为 TCP 测试。
- 外部 `_agent_reference/codex` commit `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`，`codex-rs/core/src/responses_retry_tests.rs`：观察并断言错误/重试/历史状态而非模型措辞。Rust 内部 session fixture 不适配 Flask，未复制实现，只沿用有界、确定性状态断言思路。
- 附件/上下文边界核对了 OpenCode snapshot `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/session/message-v2.ts` 与 `session/compaction.ts`，以及 Codex snapshot `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的 `app-server/src/request_processors/thread_attachments.rs`、`thread_input.rs`、`core/src/context/compaction_summary.rs`。只适配“附件属于明确 session/thread、历史上下文需要显式有界”的原则；没有复制上游状态机、实现或许可证材料，也没有加入跨会话搜索。更完整的仓库、版本和许可对照见 `docs/architecture/agent-upstream-adaptation.md`。

## 首次实测发现（2026-10-10）

新增前，进程内 stream parity + session coordination 基线为 **12 passed**。新增两条真实 HTTP 旅程后均在 **SSE `[DONE]` 之后的 GET 超时**，而 SSE 内容本身完整；同一连接的下一次请求未抵达 Flask。

单变量实验只删除应用层 `Connection: keep-alive`，原旅程变为 **2 passed**。这是 HTTP 连接行为，`test_client` 看不到。运行代码修复后，诊断开关已移除，在没有任何 harness 响应头改写、仍复用同一客户端的条件下原旅程回放为 **2 passed（9.67s）**。不要通过禁止连接复用或每次新建客户端把红灯藏掉。

补充稳定错误码断言后，与原有 stream parity、session coordination 一起回归：**14 passed（18.73s）**。两条 HTTP 后端 PID 均在 fixture 结束后退出；只保留隔离临时证据目录。

## RAG / EvidencePack HTTP 旅程

`backend/tests/test_agent_rag_http_acceptance.py` 另起真实 Flask TCP 子进程，按用户请求顺序验收已确认简历证据的读取、服务端预算、继续分页、公开来源和 history readback。它不是“直接调用 EvidencePack builder”的进程内测试：HTTP 进入真实 Agent route、SessionManager、resource catalog / owner checks、Ask `ModeRuntime`、evidence selector、EvidencePack budget/paging 和 JSON/SSE egress。

本旅程使用合成数据和确定性替身，边界如下：

| 部分 | 真实 / 替身 | 证据范围 |
|---|---|---|
| Flask/TCP 与 Agent API | 真实独立子进程、OS 分配 loopback 端口、requests HTTP | 路由、HTTP 状态码、JSON/SSE、history 响应 |
| 资源、权限、证据预算、Ask 和公开 egress | 实际运行代码 | `career://me/resume` owner 绑定、拒绝未分区公共 URI、10/12 partial、下一页 2/12、源 URI/section/version/status |
| MySQL 资源数据 | 合成只读 repository adapter | 不证明 MySQL driver、SQL schema、事务或真实 DB 中的数据投影 |
| Session Redis | 复用 `test_session_turn_coordination._SharedSessionRedis`，并在写入点输出隔离快照 | 能跨 HTTP 请求验证 cursor 和 history 持久状态；不证明 Redis 服务、故障恢复或跨 Worker 互斥 |
| LLM | 本地确定性 selector / answer fixture | 不证明真实模型的选择、语义质量或 token 行为；会记录实际收到的消息及 EvidencePack |

完整旅程：owner 经 HTTP 创建 session → 引用已确认简历技能 → Answer 模型收到最多 10 个条目的服务端 EvidencePack → 会话保存 owner/session/version/offset cursor → 同 session 用 SSE “继续”并读回剩余 2 项 → HTTP history 保留两轮来源/覆盖进度。测试另外确认不支持的 `career://public/skills` 在写状态或调用模型前返回 400，且另一 owner 引用 `career://me/resume` 时返回 404、无状态变化、无模型调用。JSON 回答中稳定断言覆盖度和来源，不对模型自然语言做逐字验收。

### 回放

```powershell
Set-Location 'D:\AGeneral Workspace\cpa-agent'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest backend/tests/test_agent_rag_http_acceptance.py -q --tb=short
```

旅程证据位于 pytest `tmp_path`：`ready.json` 列出 TCP PID 和真实/替身边界，`llm-calls.jsonl` 是每次实际模型适配器调用收到的消息及解析的 EvidencePack，`<session_id>.state.json` 是 Redis adapter 写入时的状态观测副本。测试结束只终止该子进程；无真实模型调用，不触及共享数据库或 Redis。

首次回放暴露一个真实 API 一致性缺陷：首轮 HTTP chat 返回公开 `orchestration.progress.evidence`，但同一会话的 history 丢失该进度，因为 history 再次投影时只读 `supervisor_view`，而已保存的是经过 egress 的公开 `progress`。`egress_pipeline._public_orchestration()` 现在对已保存的公开进度执行独立 allowlist 清理并回放来源字段；HTTP 验收断言覆盖修复前后行为。窄回归（RAG HTTP、EvidencePack surface/retrieval、citation contract、turn outcome、既有 HTTP journeys）最近一次为 **45 passed**。
