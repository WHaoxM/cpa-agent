# 附件解析完整性：当前运行链与验证

本指南描述已接入的 `upload → ingest → document_access → Ask/Agent` 路径，不把结构化简历字段抽取与文档全文访问混为一谈。

## 运行链

1. `backend/app/services/agent_runtime/media/file_store.py` 持久化原文件，随后异步 ingest；`full_text`、`excerpt`、`chunk`、`block` 是不同产物。
2. `backend/app/services/agent_runtime/media/ingest.py` 对 PDF 可先取轻量文本，但只要允许重型解析，就**无条件**让 L0/Sidecar 再核验，不根据“教育经历/技能”等标题、全文长度或联系信息猜是否完整；L0 失败而保留轻量文本时明确 `source_coverage=unverified`，文档访问不得显示 `complete=true`。DOCX 通过 Mammoth 1.12.0（BSD-2-Clause）的 `extract_raw_text` 读取正文、表格与文本框顺序，再用 python-docx 读取页眉/页脚；发现嵌入媒体或复杂形状时也标为 `unverified`，不把图片中的文字伪称已读。
3. `backend/app/services/agent_runtime/media/document_access.py` 在提问时提供全文或带 `offset` 的窗口。`complete=true` 仅在源产物未截断、版面块未缺失、且本次窗口确实覆盖全部已提取文本时成立。它不是 OCR 逐字准确率的保证。
4. `backend/app/services/agent_runtime/mode_runtime.py` 的 Ask/Agent 分支将本轮所有已挂载附件纳入共享预算并交给主模型；超预算时要求 `attachment_read` 分页/定位，而不是把首页片段伪装成全文。旧产物重解析只排后台任务，Ask 不同步运行 OCR。
5. `/api/agent/chat` 与 `/api/agent/chat/stream` 的 Supervisor 路径共用一次 `prepare_user_turn` 输入规范化：纯附件/语音回合、`attachment_ids`、`content_parts`、`voice_turn_id`、服务端资源引用校验和 STM metadata 采用同一边界。文件 ID 和语音回合先校验当前 Principal；语音还要求属于当前 session，创建 Voice Turn 时必须提供有效 `session_id`。跨 owner/缺失引用在写 STM 前拒绝，不能把私有文件名/状态写入模型卡；只有当前 owner 的附件尚未就绪等可恢复失败进入 `mount_errors`/`partial`。Ask 重建附件目录卡时继续传入当前 owner。进程内同 session 的 prepare→STM→Supervisor→assistant 落库置于同一锁；生产 compose 另启用 Redis 跨 worker 租约，拿到租约后先从 Redis 刷新会话缓存。`redis_required` 不可用时 fail closed；开发 `redis_preferred/local` 不声称跨 worker 保证。SSE 先发 `: connected` 注释，执行后在锁外回放事件，慢客户端不持锁。连接建立后的媒体拒绝通过脱敏 SSE `error` 事件返回，而非 HTTP 4xx。旧引擎对多模态输入明确返回 400；SSE 仍非实时 token/工具进度流。

当前借鉴 OpenCode/Codex 的**显式截断、按偏移继续读取、模型窗口预留输出额、工具结果标注覆盖范围**等模式；没有复制其编码代理拓扑。上游参考源码分别位于 `opencode/` 与工作区外的 `_agent_reference/codex/`，均不是运行时依赖；模块、快照及许可证见 `docs/architecture/agent-upstream-adaptation.md`。

## 已修复的静默丢失

- PDF 轻量解析不再只读前 40 页。
- `full_text` 不再在 80,000 字处裁切；旧产物若标记 `truncated`，不得宣称完整，提问前会尝试无损回补。
- L0 版面块不再只保存前 40 个，块正文不再裁切到 500/2,000 字。旧记录若 `block_count > len(blocks)`，按结构不完整处理。
- 回补采用“先解析、再比较、最后替换”：新 OCR 不可用或结果更短时保留旧产物，避免把可读简历覆盖成空文本。请求线程只排队，`upgrade_status` 展示后台状态；过期状态可显式重试。
- DOCX 不再将所有表格统一移到正文末尾；Mammoth 保留文本框正文顺序，页眉/页脚也进入读取范围。空白 DOCX 不再报告解析成功。
- PDF 轻量解析的“看到了技能但未看到教育”“字符少于阈值”等内容关键词捷径已移除；任意标题组合都不能跳过 L0。旧纯 pypdf/pdfplumber 产物保守视为来源未验证，允许显式修复。
- Ask/Agent 不再静默丢弃第 5 份及之后的附件；每份附件有独立完整性标记，一轮共享 `48_000` 字符近似预算。
- 职途导航的 L0 PyMuPDF 文本层解析不再把多页文本合并后重新分块、将所有块误标为第 1 页；现在直接保留每页 PyMuPDF 1.24.14 的 `Page.get_text("text")` 提取块的零基页码，再由候选 API 转为用户可见页码。这是复用现有解析库的页码来源，不另造 PDF 分页器；OpenCode/Codex 快照只用于 Agent 上下文契约比对，本缺陷没有从其编码工具移植解析逻辑。2026-10-10 用一份用户提供的 3 页 PDF 复现：修复前全文约 4070 字、26 个块却只报告第 1 页；修复后 28 个块覆盖第 1、2、3 页，块 ID 在整份文档内唯一。通过 `5174` 前端代理重传后，权威候选 API 与事件 API 返回 `page_count=3`、`observed_pages=[1,2,3]`、`missing_pages=[]`、`needs_input`，没有自动确认画像。文件正文和个人信息不写入测试或报告。合成三页/空白中页回归见 `backend/tests/test_pdf_page_provenance.py`；空白页仍会显示为缺失，不伪造内容。

## 本机 OCR 边界

按 `docs/adr/0002-pattern-upgrade-retrieve-parse.md`，`CPA_L0_PDF_ENGINE=auto` 只走 Sidecar → PyMuPDF；未配置 `CPA_L0_PARSE_SIDECAR_URL` 时，扫描 PDF 的 PyMuPDF 文本层可能为零。已存 OCR 文本不会因此被自动覆盖，但新扫描件不能凭空完成 OCR。`docker-compose.parse-sidecar.yml` 的 `parse-sidecar-contract` profile 仅是会返回 503 的契约 stub，**不提供 OCR，也不承担任务队列**。本机可用 `mkg` 环境和已有本地 MinerU 模型启动真实服务：

```powershell
# 独立终端：从仓库根目录启动真实 OCR Sidecar
$env:MODELSCOPE_CACHE='D:/AGeneral Workspace/cpa-agent/models/mineru'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B backend/scripts/parse_sidecar_mineru.py

# 后端进程环境：保持 ADR 的 auto 阶梯
$env:CPA_L0_PDF_ENGINE='auto'
$env:CPA_L0_PARSE_SIDECAR_URL='http://127.0.0.1:9380'
```

Sidecar `/v1/health` 仅在本地模型/依赖就绪时宣称 `ocr_pdf=true`；占位文本、PyMuPDF 文本层回退不可冒充 OCR。不要把 PDF 文本层成功率当作扫描件成功率。

2026-10-09 Docker 恢复后复测扫描版 A：真实 Sidecar health 为 `ocr_pdf=true,durable_tasks=false`，自动阶梯返回 `engine=sidecar`、68 个块、内部页索引 `[0,1]`、3335 字；候选 API 对页索引统一加 1 后供用户查看。初次复测曾因 Flask debug reloader 在其他 Agent 修改 `pipeline.py` 时重启 Sidecar，连接中断后 PyMuPDF 回退拿到空文本；现已禁用 Sidecar reloader，且 L0 对 Sidecar/MinerU/PyMuPDF 的空文本或空块显式返回 `success=false`，不能再把扫描件的空回退记为解析完成。`backend/tests/test_pdf_empty_is_not_success.py` 为此加负测。这是覆盖和失败状态验证，不是逐字 CER 验收。

上传 `POST /api/agent/files` 返回文件状态；已登录的文件所有者可用 `GET /api/agent/files/<file_id>` 查看 `status`、`job_id/job_status`、`upgrade_status`、`upgrade_job_id/upgrade_job_status`、`upgrade_error`、`event_cursor` 和 `ocr_capability`。`GET /api/agent/files/<file_id>/events?after=<seq>&limit=<1..200>` 从同一权威记录重放严格大于 cursor 的媒体状态事件。PDF/DOCX ingest 与 repair 由 Celery 的 `cpa.media` 专用队列投递，Redis AOF 保存未确认消息，worker 使用 late ack；Flask 或 worker 重启后未确认任务可重投，业务状态仍以文件目录/MySQL 的 `agent_file` 为准。修好 OCR 服务后，对失败或不完整产物调用 `POST /api/agent/files/<file_id>/retry` 只排后台重试，不同步等待 OCR；再次 GET 直到 `status=ready` 且需要回补时 `upgrade_status=ready`。启动与验证见 `durable-media-jobs.md`。这不覆盖通用 `fork_jobs`，也不代表 MinerU Sidecar 本身有持久任务状态。

本机两页扫描简历的旧记录报告 68 个块，仅保存 40 个且全为第 1 页。显式 MinerU 重解析后可保存 68 个块，页码覆盖第 1、2 页；真实 Sidecar 的 Flask 接口对该样本返回 HTTP 200、68 个带页码的块。在临时隔离存储中，`document_catalog.source_complete` 与 Ask 文档包从 `false` 变为 `true`，原上传记录未改写。逐页证据见 `docs/reports/2026-10-09-resume-page-extraction-audit.md`。该验证只说明**提取链覆盖**，不等于 OCR 内容已与原 PDF 逐字校对。

## 回归验证

在仓库根目录、`mkg` 环境运行：

```powershell
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q backend/tests/test_agent_media.py backend/tests/test_agent_media_async.py backend/tests/test_document_access.py backend/tests/test_agent_document_budget.py backend/tests/test_model_context_budget.py
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q backend/tests/test_pdf_page_provenance.py backend/tests/test_pdf_empty_is_not_success.py
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q backend/tests/test_turn_outcome_status.py backend/tests/test_agent_stream_parity.py backend/tests/test_agent_citation_contract.py
```

重点断言：第 41 页、第 41 个版面块、80,000 字之后的尾部、旧产物异步回补失败时不覆盖、DOCX 文本框/页眉及表格顺序、`complete=true` 的真值。Sidecar/异步状态另见 `backend/tests/test_agent_media_async.py`，多附件主模型链路见 `backend/tests/test_agent_document_budget.py`。

JSON/SSE 输入一致性回归使用 Flask 路由与模拟 Supervisor：同一纯媒体请求在两入口都只规范化一次、写入同一类 STM metadata、把相同 `prepared_turn` 交给 Supervisor；合法 owner 的挂载失败时两者公开的 `partial` outcome 相同。另以真实 `prepare_user_turn`、双 owner VoiceTurnService、Ask 实际上下文组装和同 session 并发执行检查 owner、session、STM、附件卡及回合序列；SSE 首字节先于 Supervisor 执行。跨 worker 回合准入另由 `test_session_turn_coordination.py` 以两个独立 Coordinator + 共享 Redis 后端、及 worker 缓存刷新回归验证；生产真实 Redis 仍应随部署演练。它不是实际 OCR、完整 ModeRuntime/真实模型或实时工具进度的验收。

历史联合回归结果只代表当时的输入/输出契约。媒体队列另增加了磁盘状态恢复、late-ack 配置、确定性 task id 与重复投递 no-op 测试；真实 Redis/worker 进程中断验收步骤见 `durable-media-jobs.md`，不能由单元测试替代。

## 尚未覆盖

- DOCX 复杂嵌套形状、批注、修订内容与图片中的文字仍可能漏读；现有文本框验证为合成样本，需要真实 DOCX 简历渲染比对。
- OCR 识别质量/阅读顺序尚无逐页人工标注的金标准；`complete=true` 只针对已提取文本及可检测的结构缺口。
- 多附件分配额仍是字符近似，不是 provider 精确 token。Ask 直连前已按最终消息和候选模型预检；LLMClient 在 failover 时重算并可回传实际 `usage`，但非 OpenAI tokenizer 仍是保守字节估算，Agent/Experts 的 LangGraph 最终消息预算还未全面验收。
- 模型真实回答质量、引用与 AIGC 推荐流程尚未在本指南的回归测试中证明；职业规划四类问题目前只做输入/输出契约 mock，详见 `docs/reports/2026-10-09-agent-document-context-evaluation.md`。
