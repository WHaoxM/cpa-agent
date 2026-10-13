# Agent HTTP 验收主轴：独立验收复核

日期：2026-10-10
状态：首批 socket HTTP 红灯已由应用头修复后重跑通过；其替身边界和架构覆盖缺口仍明确保留

## 结论

最终验收以少量真实 socket HTTP 用户旅程为主轴；`Flask.test_client()`、mock Supervisor/模型/Redis 与进程内线程测试继续保留作快回归和故障定位。新增 HTTP harness 已覆盖跨轮 JSON→SSE、合成 TXT 上传与归属/资源挂载；另一组 harness 进一步用真实 Redis 和两个 Python Worker 测了工具超时副作用、并发 session、丢失 Redis 时 fail-closed。它们是独立的 HTTP + 替身验收，不是生产部署或真实模型质量证明。SSE keep-alive 红灯有已验证的最小运行代码修复，细节见末尾更新。

## 依据

- 已读 `AGENTS.md`、`CONTEXT.md`、`docs/README.md` 与 `README.md` 的运行环境段落。运行/测试应使用 `D:\Miniconda\miniconda3\envs\mkg\python.exe`；正式 session 跨 Worker 保证依赖 Redis lease，Strict auth 默认启用，MySQL/Neo4j/Redis 的仓库默认端口分别为 `13306/17687/16379`。
- 目视检查 `D:/Downloads/Agent_0_1791642812318.pdf` 的关键页：第 3 页要求感知、意图、DM、planning、工具及反思分层；第 7 页要求上下文按来源/阶段整理，并把 UI 事件转为状态；第 10 页强调最小权限、默认拒绝与沙箱隔离；第 12 页要求工具 schema、错误码、幂等、降级、熔断；第 14 页明确 Agent 做下一步决策，Harness 管循环、超时、重试、边界与日志。与其对应的验收重点是用户可观察状态及权威副作用，不是单独验证 Python 分支。
- 当前架构审计基线：`docs/reports/2026-10-10-general-agent-guide-architecture-audit.md`。其中指出 SSE 当前先送 `: connected`，Supervisor 完整执行完后才回放事件；它不是实时 token/工具进度流。工具失败之前有 ToolMessage 类型处理缺口，当前实现有待端到端核验。

## 当前测试的传输/替身分级

| 测试/工具 | 所走边界 | 可证明 | 不能证明 |
| --- | --- | --- | --- |
| `backend/tests/test_scoped_tool_failure_contract.py`（本次独立运行） | 前 3 类直接调用 gated `StructuredTool` / `scoped_invoke`；HTTP 断言用 Flask `test_client()`；mock 模型、Supervisor、审计与 artifact committer | ToolNode→`ToolMessage`→结构化错误/有限重试；JSON/SSE egress 字段及脱敏逻辑 | socket、真实 provider、真实工具副作用、真实 DB/Redis、真实 SSE 分块时序 |
| `backend/tests/test_session_turn_coordination.py`（本次独立运行） | 两个 Coordinator + `threading.Lock` 假 Redis；HTTP 案例用 `test_client()`；session 存储 fake；Supervisor mock | 单进程对象间的串行逻辑、权威刷新分支与 fail-closed 状态映射 | 两个 Flask 进程、Redis 实际 Lua lock/续租/发布、Redis 故障与真实持久状态 |
| `test_agent_stream_parity.py`、`test_turn_outcome_status.py`、`test_agent_media.py` | 主要为 `test_client()`；多数 API 测试替换 session/provider/prepare 等边界 | 快速回归 transport projection、附件 admission 与模型入口参数 | WSGI/TCP 中间件、代理缓冲、真实服务状态、用户网络观测 |
| 其余 `backend/tests` 中 `create_app().test_client()` | Flask WSGI 测试客户端，不创建 TCP socket | 路由与应用内部集成（程度视其 DB/provider 替身而定） | 不得标为真实 HTTP |
| `backend/tests/fixtures/platform_acceptance_probe.py ask --live` | `urllib.request.urlopen` 发给 `http://127.0.0.1:5001`，真实 socket HTTP；默认不 live | 可由本机实际进程检查两轮同 session 连续性；专用输出做 hash 脱敏 | 不运行在 pytest 默认回归中；只覆盖文本 Ask，不覆盖附件/SSE/工具副作用/双 Worker；本轮未执行 live |
| `backend/tests/test_agent_http_journeys.py` | 父 pytest 用同一个 `requests.Session` 走 loopback TCP；每条启动一个真实 Python/Werkzeug 子进程；真实 app routes/session coordinator/session manager/TXT解析/file store/resources | 2 条用户旅程：JSON→SSE 连续 session；TXT upload→ready→显式挂载→history/resources 回读；负向附件 JSON/SSE 拒绝及消息未写；owner 隔离 | Supervisor 是 deterministic substitute；Redis 是 fake `SnapshotRedis`；MySQL/媒体 schema 写被替代；单 worker，无真实 Redis lease/真实 LLM/ToolNode/OCR |
| `backend/tests/test_agent_http_failure_acceptance.py` + `fixtures/http_failure_acceptance_server.py` | urllib 真实 socket；两个单独 Python/Werkzeug 进程；Redis DB 15 为真实 Redis；Redis 会话状态/锁/side-effect ledger 都从 authority 回读 | 两条 journey：真实 gated tool wrapper 写一次再 timeout，HTTP outcome/history 为 failed；两个进程跨 cache 同 session 串行，顺序及第二轮 history 从真实 Redis 确认；让一个 Worker Redis 连接转为拒绝后得到 503 且 Redis 状态不变 | Deterministic Supervisor + 测试 `pipeline_run` 替身，审计与 MySQL 投影禁用；未走真实 provider/完整 LangGraph tool-calling loop、Celery、SSE 工具失败流、生产身份 provider；Redis 使用固定 DB 15，虽 key 唯一并逐项清理，仍须确保该 DB 专用于测试 |
| `backend/tests/fixtures/real_pdf_agent_chain_probe.py` | 文件自述“real Flask API test client; real model/DB/PDF” | 可作为真实 LLM/DB/PDF 的进程内应用集成线索 | `test_client()` 仍不是 socket HTTP，且昂贵/数据特定，不应等价主验收 |

通过下列命令，我独立运行了两个目标文件，结果 `16 passed`（约 8.44 秒）：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q backend/tests/test_scoped_tool_failure_contract.py backend/tests/test_session_turn_coordination.py
```

这 16 项全部属于上表中所述的进程内/替身验证。测试产生的 warning（默认 SECRET_KEY、pynvml、TRANSFORMERS_CACHE）不影响通过结论，但 SECRET_KEY 警告说明验收进程应显式注入随机临时值。

## 建议的 5 条 HTTP 验收矩阵与当前状态

所有行都使用真实 socket 客户端（`urllib`/`httpx` 均可）访问实际 WSGI 进程；不要以 `Flask.test_client()` 替代。稳定断言应落在 session ID、事件类型/顺序、明确字段、持久状态和副作用计数，不断言自由文本的“回答质量”。把模型决策收敛为测试 provider（兼容项目当前使用的 OpenAI 协议），允许真实应用中的 Prompt/工具路由与状态机执行；每行结果标记 provider 为 deterministic fixture，避免误写成真实模型质量验收。

| # | HTTP 用户旅程 | 最小稳定断言 / 权威面 | 主要依赖与隔离 |
| --- | --- | --- | --- |
| 1 | **跨轮上下文** | **已测**：同一 session JSON 第一轮/SSE 第二轮，history 4 条按序；第二轮测试 Supervisor 输入有上轮历史与 settled context；authoritative adapter snapshot 为 2 轮；异 owner 回读拒绝。 | **边界**：一条旅程用 fake Redis/一 worker；跨进程真实 Redis 刷新由矩阵 5 覆盖，但不是真实 LLM 事实理解。 |
| 2 | **附件顺序和归属** | **已测**：HTTP 上传合成 TXT→ready→JSON 引用挂载→SSE 重复引用；读取 resources 与真实磁盘挂载/文件 metadata/session history 交叉确认；缺失附件 JSON 404/SSE error 且无 STM/模型调用。 | **未测**：PDF/DOCX/OCR、voice、生产 file service/MySQL、跨 owner 在真实 socket journey（已有 test_client 覆盖部分拒绝分支）。 |
| 3 | **JSON/SSE 契约与时序** | **已测**：HTTP 行程规范化事件类型、event id、turn id、terminal outcome、`[DONE]` 和 JSON 对照；真实 socket 下复用同一个 requests Session 后继续 GET，针对原 SSE keep-alive 红灯验证。 | **未测**：SSE 首 token/工具事件实时推送、代理缓冲生产配置；当前 SSE 业务事件仍是完整回合后 replay。工具失败 SSE 目前只在 `test_client` 覆盖。 |
| 4 | **工具失败、重试与真实副作用** | **部分已测**：真实 socket JSON `/chat` 触发 gated test `pipeline_run` 写一次到 Redis 后 timeout；权威副作用 hash count=1 且 owner/session 被 gate 重写，HTTP outcome failed、history 有失败 assistant、无私有异常细节。组件测试也覆盖只读一次重试、拒绝不执行。 | **未测**：真实 Supervisor/provider 经 LangGraph ToolNode 发起调用；SSE 工具失败；真实外部工具/事务的模糊提交恢复。 |
| 5 | **两 Worker 同 session 并发与结算** | **已测**：独立 PID A/B + 真实 Redis DB 15；先分别 warm 两 worker cache，A 进入后 B 被挡至完整 turn settle，事件顺序 start/end，第二 worker history 含完整第一轮；Redis 断连时 503 且权威会话与工具事件未变。 | **未测**：多主机/Redis failover/长任务 lease 续租和断连竞态；对应 test_client fake Redis 仅为快速回归。 |

上述 4 条 HTTP journey 独立复跑为 `4 passed in 30.01s`。规划 worktree 合并后新增 `backend/tests/test_plan_handoff_http.py`，我随后独立运行 `1 passed in 8.81s`；再把全部三组真实 HTTP 测试一起复跑为 `5 passed in 31.49s`。文件/多轮旅程使用临时磁盘 `tmp_path` + finally 关停单子进程；工具/并发两进程测试 finally 终止并 wait workers，删除唯一 session/session-list entry/lease 及唯一 effect/event keys，不 flush DB 15。规划 HTTP fixture 则以临时 state dir 和子进程 finally 清理。需给真实 Redis DB 15 专用性留明确环境约束。

## 最小执行、数据隔离与清理约束

1. 独立 compose project / ephemeral stack：只启动路径实际需要的 Redis + MySQL（附件解析可用小文本 fixture；非 OCR 场景不启动 Neo4j/parse-sidecar）。若验收采用现有默认 MySQL schema，则使用独立容器/schema，不能复用开发实例；service ports 用随机映射而不是覆盖共享 `13306/16379`。
2. 每次 run 生成 `run_id`，独立 Redis DB 或 `CPA_TEST_PREFIX`、专属 schema/owner/session ID、独立 `UPLOAD_FOLDER`/工作区目录、测试 provider 端口。当前代码是否支持 session/upload namespace 需要先核实；没有支持时通过临时容器/schema/path 做隔离，不要只靠事后清 Redis 全库。
3. 身份用最小范围的专用测试 Principal；不打开通用 demo/弱默认密钥。测试数据人工合成且脱敏，不读用户真实文件或本机凭据。测试 provider 静态返回可预测工具调用，并在每一回合记录 request-id/消息 hash，日志不得保存完整简历内容或 access token。
4. 用 pytest fixture/context manager 启停 worker/provider/依赖，pytest `finally` 清理临时文件、专用 session key、测试行和容器；清理只针对本次 `run_id`。失败时保留脱敏摘要与 run ID 供重放，提供显式 `--keep-artifacts` 才留数据。清理失败要让测试失败，不静默忽略。
5. CI/默认快速回归继续跑替身测试；少量 HTTP E2E 独立 marker（例如 `http_acceptance`）运行，真实 OCR/LLM质量另设昂贵/人工验收，不把网络服务暂不可用误归为测试通过。

## 缺口与审阅裁决

- `Flask.test_client` 工具失败/session 协调用例仍有价值，不能代替新的真实 HTTP。新的 socket harness 补上真实边界但显式使用了替身，报告中不把它写成全链路 Agent/LangGraph/真实服务验收。
- 两个架构 worktree 已定位并只读核查：planning/workflow = `%USERPROFILE%\.codex\worktrees\dec7\cpa-agent`，commit `782fdb5d8f0c764051e1fa5289b87c91b4d265bc`（`fix(agent): preserve task brief across all plan steps`），改 `plan.py`、新增 `test_plan_handoff_context.py` 和 `docs/guides/plan-handoff-context.md`；RAG/evidence = `%USERPROFILE%\.codex\worktrees\41e1\cpa-agent`，commit `d8a6e98c2e1de20688a5c17a98efc4ed81ab45ea`（`fix(agent): fail closed on private vector collections`），改 `semantic_search.py` 与新增 `test_semantic_search_scope.py`。提交都来自独立 detached worktree。规划切片随后已由主代理合入当前工作树（`plan.py` 与 `test_plan_handoff_context.py` 可见）；其原组件测试在 detached worktree 独立运行 `18 passed`。目前新增的 `test_plan_handoff_http.py` 已由我独立 socket 运行 `1 passed`，实际覆盖 HTTP→Supervisor/ModeRuntime→Experts plan→scoped LangGraph→server-side plan verifier：测试 brief 到达三个后续模型步骤，负向伪造黑板证据被 gate 停止。明确替身仍包括 Lead/Step model、pipeline writer、MySQL、Redis；成功产物是 fixture DomainRows，不是真实业务行/真实模型质量。RAG worktree 当前未整体并入，因主树公共归属门禁更严格；其组件测试 `3 passed`，只用 fake embedding/Milvus。另有当前主树 `test_agent_rag_http_acceptance.py` socket journey：验证 owner-scoped `career://me/resume` source reference 经资源目录与证据检索分页，首轮 coverage 10/12 且 `next_action=continue_evidence`，续读取得剩余 2 项并清 cursor；history/resources 权威回读一致，跨 owner 请求 404 且无额外模型调用/会话写入。该 fixture 仍替换 EvidenceRepository/MySQL、session Redis adapter 与 LLM；不是对 worktree `semantic_search.py`、真实 Milvus 查询、真实 RAG DB 或公开 collection 命中的验收。这里不修改 `plan.py` 或 `semantic_search.py`。
- PDF 架构要点逐层映射：

| PDF 模块/关注 | 已有 socket HTTP | 仅组件/test_client/进程内 | 当前未覆盖或边界 |
| --- | --- | --- | --- |
| 接入与感知（文本/文件） | synthetic TXT 上传、ready、挂载和资源回读 | 附件/语音准备与归属有多组 test_client | PDF/DOCX/OCR、voice、真实 sidecar |
| 意图识别 | 无；HTTP harness 以固定 ask/agent 路径进 deterministic Supervisor | routing/mode tests 多为模型/服务替身 | 真实 provider intent+slots/confidence 与拒绝分支 |
| 对话管理 DM/状态 | JSON/SSE 共享 session 旅程；真实 Redis 双进程顺序/历史 | 假 Redis snapshot 旅程，session tests fake Redis + 同进程线程 | Redis failover/TTL expiry、多主机与实时 UI event replay |
| 任务规划 Planning | `test_plan_handoff_http.py` 真实 TCP 覆盖 Experts brief 到每一步及伪造证明被 gate 停止 | `test_plan_handoff_context.py` 在组件层测多个模板及 ModeRuntime handoff | 真实模型、真实 pipeline/DB 业务成果回读尚未覆盖；对话历史不要靠黑板假设传递 |
| 流程引擎 Flow Engine | 双 Worker HTTP 测试在运行路由内验证了 session admission/settlement，不验证业务 workflow | plan/template/scoped invoke 组件测试 | 实际长业务流程/挂起恢复/幂等结算 |
| 知识库/RAG | `test_agent_rag_http_acceptance.py` 真实 TCP 覆盖 owner-scoped `career://me/resume` 证据分页、partial continuation、同 owner history 回读及跨 owner 404；证据来源元数据只在授权投影中出现 | `test_semantic_search_scope.py`（RAG worktree）使用 fake embedding/Milvus；当前 HTTP fixture 也替换 EvidenceRepository/MySQL、Redis 与 LLM | 未覆盖真实 Milvus/vector semantic search 与当前主树公开 collection；worktree RAG patch 未合入。真实 provider、真实 MySQL/Redis evidence persistence、引用外部资料闭环未验证 |
| 记忆系统 | Redis 会话历史被下一 worker真实读到；另一旅程仅 fake Redis snapshot | memory/context assembler 和 Ask tests 多为替身/test_client | 长期记忆/RAG owner scope、summary/compaction、跨 session 取证 |
| 工具调用 | 真实 HTTP JSON 失败回合真实执行 gate wrapper + Redis test mutation，证明一次 side effect 与失败输出 | scoped ToolNode+ToolMessage 和权限/重试大多使用 mock model/tool/test_client | 真 provider 的完整模型→LangGraph ToolNode→真实业务工具→SSE/history/authoritative DB 闭环 |
| 执行与反思/校验 | HTTP outcome/history 在工具失败时为 failed；协调故障返回 503 不写 assistant 成功状态 | outcome/error egress projection 有 test_client 与 pure unit | Plan B、重试后人工兜底、真实业务产物验收与体验质量 |
| Harness 安全边界/审计 | loopback DevAuth、owner/session/file 路由拒绝；两个 process Redis-required fail-closed | Principal/gated-tool scope 单测及 test_client | 正式认证适配器、多租户上线部署、审计 persistence (HTTP harness 禁用了 audit/MySQL) |

## 2026-10-10 SSE keep-alive 根因与复验

- 首轮真实 HTTP 证据：两条 journey 均已完整读到 SSE `[DONE]`，但在同一个 `requests.Session` 下一条 history/resources GET 等待响应超时；请求没有抵达 Flask。首次输出约 `2 failed in 38.1s`，read timeout 为 15 秒。
- harness 代理进行了唯一变量诊断：只移除 Flask 的 SSE response `Connection: keep-alive` 响应头（测试 app `after_request`），两个原旅程由 `2 failed` 转为 `2 passed`；临时 harness 诊断开关随后已移除。我的独立复核也先在默认头下复现两条超时，再只在运行代码删除 demo/Supervisor SSE response 中显式的 `Connection: keep-alive`，原始 journey tests 在没有客户端新建连接/禁复用/响应头篡改情况下 `2 passed in 10.29s`。
- 之后连同真实 Redis/双 Worker/side-effect 旅程按同一命令复跑：`4 passed in 30.01s`。未改 harness、requests Session 或 test sequence。最小运行代码变更在 `backend/app/api/agent.py` 两个 SSE Response header 中删掉显式 `Connection`，让 Werkzeug/WSGI server 决定连接边界；没有声称这必然解释所有代理/生产服务器行为。
- 最终运行代码**已真实验证**：SSE 完成后同一复用连接可继续 GET history/resources；JSON/SSE 同 session、附件旅程、错误分支与工具/跨进程 scenario 保持绿灯。生产反向代理/HTTP2 下的 SSE 行为仍未单独验收。

## 2026-10-11 RAG HTTP / egress 独立复核

- 独立运行 `test_agent_rag_http_acceptance.py` 及 citation、TurnOutcome、Ask evidence continuation/retrieval 邻接集（加上本次恶意 sentinel 回归）：`46 passed, 1 warning`。随后独立运行全部真实 HTTP 文件 `test_agent_http_journeys.py`, `test_agent_http_failure_acceptance.py`, `test_agent_http_idempotency_acceptance.py`, `test_agent_rag_http_acceptance.py`, `test_plan_handoff_http.py`：`12 passed, 1 warning`。命令均使用 mkg Python，`-B -m pytest -q`。
- RAG journey 走真实 loopback TCP 子进程及当前 Flask route；同一个 HTTP session 执行 owner-scoped 证据首轮/continuation，再从实际 history/resources route 回读；第一轮 `10/12` + continuation action，下一轮从 offset 10 取得余下两项并清除 cursor。不同 owner 的 `career://me/resume` 被拒绝为 404，authority history 与模型调用计数不增加。固定测试 LLM、EvidenceRepository/MySQL、session Redis adapter、快照/存储层是替身，所以这验证的是 HTTP 路由和应用投影，不是生产 RAG/真实向量 DB/provider 的完整链。
- egress review 找到过一个真实投影缺口：字段名虽在 allowlist 中，但 `evidence_pack.status` / `coverage.reason` 原样复制任意字符串。对 `project_turn` 注入 `PRIVATE_PROMPT_SENTINEL` / `PRIVATE_EVIDENCE_SENTINEL`，确认旧投影会输出 sentinel。当前 `/backend/app/services/agent_runtime/egress_pipeline.py` 对 status 和 reason 改用 exact enum allowlist：未知 status 降为 `unavailable`、未知 reason 删除；coverage bool 与非负 exact-int 字段也做类型校验，并对 history projection 用相同规则。`test_agent_citation_contract.py::test_evidence_egress_drops_unknown_status_reason_and_private_pack_content` 验证未知状态/原因、malformed metadata、内部 evidence items 和 prompt sentinel 不进入 turn/history；citation contract 邻接回归通过。
- 没有发现与 citation 或 TurnOutcome 的契约冲突：TurnOutcome 表示本轮回答是否结束（`completion_scope=turn`），evidence coverage 独立表示本轮只覆盖 10/12 且可继续；用户仍能看到清晰 partial/continue 状态，不会把一轮完成误报成证据全集完成。公共 egress 输出经过字段投影的 source ref/section/version/status/coverage，不含原始 evidence items 或 prompt；RAG 的 owner/resource gate 在来源进入检索前执行。服务端任意可写的其他内部属性并非本次检查的目标，故不声称对全部运行态字段做了全面信息流审计。
- 矩阵更新：真实 HTTP RAG 证据分页/续读/所有者隔离/历史权威回读已测（依赖替身注明如上）；真实 Milvus/公开 collection 命中、生产 EvidenceRepository、真实 LLM citation 质量与真实 provider 全链仍未测。规划 workflow 当前主树已并入且其 HTTP Experts handoff 独立通过，使用 Lead/Step model、writer、MySQL、Redis fixture；RAG `d8a6e98` worktree 仍未合并，3 个 fake embedding/Milvus 组件测试不能替代上述 socket 验收。

## 2026-10-11 幂等公开 HTTP 状态复核

- `docs/guides/agent-turn-idempotency.md` 已列出公开状态语义：同 key 的 stale `reserved/running` 和 `unknown` 返回 HTTP 409 `IDEMPOTENCY_OUTCOME_UNKNOWN`；completed response 超期后清正文并保留 `expired` tombstone，之后返回 HTTP 409 `IDEMPOTENCY_RESULT_EXPIRED`，不会重执行。未知不等同普通业务失败；已确定的 failed `TurnOutcome` 仍能以 ledger `completed` 状态安全重放。
- `test_agent_http_idempotency_acceptance.py` 的 `test_crash_after_effect_becomes_unknown_and_never_reexecutes` 与 `test_assistant_settlement_failure_marks_unknown_without_second_effect` 真实 HTTP 断言 409/unknown code、Redis 副作用计数不增加，并从 MySQL ledger/history 验证权威状态；`test_expired_response_becomes_permanent_tombstone_without_reexecution` 真实 HTTP 断言 409/expired code，且 MySQL tombstone 清空 response body、effect count 保持 1。其 HTTP harness 使用真实 MySQL ledger 与 Redis DB 15，确定性模型/工具 fixture，测试 finally 按唯一 session/key 清理；不验证自动清理调度或真实 provider。
- 本轮独立完整套件为 `889 passed, 28 skipped`，无 setup error；幂等状态/HTTP contract 测试已纳入该全量结果。PDF 映射中“幂等结算未覆盖”仅指业务 workflow/长流程幂等，不表示显式 `Idempotency-Key` request ledger 未验证；此处补充分界，避免混淆。
