# Agent 通用能力：上游复用与薄适配记录

## 硬规则与边界

先遵守仓库根目录 `AGENTS.md` 的“Agent 基础能力开源优先”规则。通用数据流、文件读取、上下文窗口、工具结果与异步状态先比对成熟实现，再决定复用库、协议或状态机。职业规划的证据判断、问题澄清与推荐策略才是本项目需要保留的业务层；不得把编码 Agent 的整套执行拓扑直接搬进 Flask 后端。

此表是本轮**可核对的参考记录**，不是宣称 OpenCode 或 Codex 已成为运行时依赖。上游代码目前均未直接复制到项目源文件。
表中的本机参考快照不随 CPA-Agent 仓库分发；在其他环境可按记录的上游 commit 获取对应源码。

MinerU 3.0.7 的异步 `/tasks` 与 AGPLv3 声明见[该版本发布页](https://pypi.org/project/mineru/3.0.7/)；当前最新版许可证不同，不能拿最新仓库首页反推本机旧版本的许可。

| 上游快照 / 许可证 | 参考模块 | 本项目适配与验收 |
| --- | --- | --- |
| OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c`，MIT；本地 `opencode/` | `packages/opencode/src/tool/read.ts`：`offset`、截断标记；`src/tool/truncate.ts` | `backend/app/services/agent_runtime/media/document_access.py` 的 `read_window`、`complete`/`has_more` 与 `attachment_read`，旧文本不再被头部窗口冒充全文。测试第 41 页、80,000 字以后及多附件分页。 |
| 同一 OpenCode 快照 | `packages/opencode/src/session/overflow.ts`、`session/compaction.ts`：按模型窗口、输出保留额与实际 usage 判断溢出 | `backend/app/services/agent_runtime/budget.py` 的按 provider/model 能力判定，`llm_client.py` 的每次 failover 重算及 usage 回传。仅文字请求有保守估算；不能称为与上游完全同等的自动压缩。 |
| Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`，Apache-2.0；本地 `D:/AGeneral Workspace/_agent_reference/codex/` | `codex-rs/core/src/tools/handlers/get_context_remaining.rs`、`codex-rs/core/src/compact.rs` | 借鉴“剩余窗口是显式状态、截断必须标明不完整”；不移植 Rust 会话内核。当前附件报告暴露模型窗口判定，未知模型不伪称全文已内联。 |
| Mammoth 1.12.0，BSD-2-Clause；本机 `mkg` 环境 | DOCX `extract_raw_text`，含 `w:txbxContent`/`v:textbox` 内容 | `backend/app/utils/docx_text.py` 读取正文/表格/文本框，再用 `python-docx` 补页眉页脚；合成文本框回归测试。复杂形状、图片字与真实 DOCX 样本仍待校对。 |
| MinerU 3.0.7（本机安装包声明 AGPL-3.0；不从网络自动下载模型） | 现成 PDF OCR/版面 `content_list.json`；该版本上游另提供 `/tasks` 异步接口 | `backend/scripts/parse_sidecar_mineru.py` 暂作薄 HTTP 适配；保留页码块、显式健康能力与隔离临时文件。两页扫描简历实测 68 个文本块；这不等于 OCR 字符准确率。部署前应按实际采用的 MinerU 版本核对许可证和模型许可。 |

## 为什么不整套嵌入上游

OpenCode 核心为 TypeScript，Codex 核心为 Rust；本项目当前权威服务是 Python/Flask/LangGraph，直接嵌入二者完整会话执行内核会引入跨进程协议、权限模型及部署复杂度。本轮选择复用已验证的 DOCX/PDF 解析库，同时对照上游的**可测契约**改造现有 Python 边界。若后续要采用其完整运行时，需先以独立进程协议和实际端到端测试证明收益，不能只因为名字成熟而平移代码。

## 当前尚未达到上游能力的部分

- 摘要/Reflection 的 `fork_jobs` 仍是进程内后台线程。媒体 ingest/repair 已单独迁到本机已有 Celery 5.3.6 + Redis：`cpa.media` 队列使用 late ack、worker-lost redelivery、单预取和确定性 task id，磁盘/MySQL `agent_file` 仍是权威状态。安装态 MinerU 3.0.7 的 `AsyncTaskManager` 在服务进程内保存 `/tasks` 状态，服务重启后不可作为恢复来源，因此只把 MinerU 当解析引擎，不把其任务表当持久队列。见 `docs/guides/durable-media-jobs.md`。
- `48_000` 字符只是附件分配预算，不等于最终请求的精确 token。模型能力已按候选模型检查，但非 OpenAI tokenizer 目前是保守 UTF-8 字节估计，真实 provider `usage` 才是权威值。
- 未知模型窗口、非文本多模态消息、复杂 DOCX 结构不能被误标为“已完整送入模型”。
- 提示词链路有 mock 契约测试，不代表真实主模型职业规划判断质量已经通过人工评测。
