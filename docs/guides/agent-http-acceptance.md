# Agent 真实 HTTP 边界验收

面向维护者；用于复现用户可见边界故障，再下钻进程内定位。本文覆盖最小的两条确定性旅程，不是完整模型质量或生产依赖验收。

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

只使用合成 TXT。PDF/DOCX、OCR Sidecar、Celery、外部知识服务和浏览器不属于这两条旅程。不要据此改写那些能力的验收状态。

## 两条用户旅程

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
```

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

此次仅增加验收适配器，没有修改 Agent 状态机/解析/预算核心，也没有引入新测试框架：pytest、requests、Werkzeug 均为现有环境依赖。

- 项目现有 `test_agent_stream_parity.py`、`test_session_turn_coordination.py`、`test_agent_media.py` 提供事件、会话、文件契约；直接复用 `_SharedSessionRedis`，不另造通用 Redis 模拟库。
- `opencode` commit `388406238bd5ca15564a762840a2362c3a45bd9c`，`packages/sdk/js/test/session-history.test.ts`：历史接口的稳定字段断言。该测试是 SDK 契约，不被误称为 TCP 测试。
- 外部 `_agent_reference/codex` commit `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`，`codex-rs/core/src/responses_retry_tests.rs`：观察并断言错误/重试/历史状态而非模型措辞。Rust 内部 session fixture 不适配 Flask，未复制实现，只沿用有界、确定性状态断言思路。

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
