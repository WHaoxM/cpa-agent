# 未跟踪 Agent 模块分流核查（2026-10-17）

**目的：**厘清工作树中的候选文件与当前主链的关系，避免把“文件存在”误当作已运行能力，或把未审实验静默装入发布候选。本文是静态盘点，不是代码验收、删除许可或新架构决策。

## 范围与可复核事实

- `git ls-files --others --exclude-standard -- backend/app/services/agent_runtime backend/app/utils` 列出 **22 个未跟踪 Python 文件**：`agent_runtime/` 下 5 个，`utils/` 下 17 个；`mkg` Python 3.10 的 `ast.parse` 对全部 22 个文件成功。语法可解析不等于可运行。
- 已跟踪的 `backend/app/__init__.py` 注册 `/api/agent`；`backend/app/api/agent.py` 的 `/chat` 和 `/chat/stream` **默认 Supervisor 路径**进入 `get_supervisor_service()`，包入口 `agent_runtime/__init__.py` 明确导入 `.supervisor`（显式旧引擎另走 legacy 分支）。在已跟踪的 `backend/app`、`backend/tests` 中未找到这 22 个候选模块的精确导入或其入口函数调用；候选内部有彼此导入。没有执行真实 HTTP 或模型回合，因此结论是**当前接线证据缺失**，不是动态加载绝对不可能。
- `.opencode/MIGRATION_PLAN.md`（本机 2026-08-10）仍把这些通用能力列为“待执行”，而多份同名源码已存在；计划状态与文件状态不一致。它不是 [`AGENTS.md`](../../AGENTS.md) 认可的 Agent 基础能力改造验收，也不能覆盖[上游复用记录](../architecture/agent-upstream-adaptation.md)。
- `backend/Dockerfile` 当前使用 `COPY . .`，`backend/.dockerignore` 不排除这些候选 Python 文件。因此**从脏工作树本地构建**会把它们带进镜像，即使默认路径不调用；这不证明它们会执行，也不能让该镜像成为已冻结发布物。本机未提交的生产镜像提案已有干净 commit 门禁。

## 候选分组与处理意见

| 分组 | 文件 | 现场判断 | 处理边界 |
|---|---|---|---|
| 平行会话/工具内核（5） | `agent_runtime/agent_loop_integration.py`、`session_compaction.py`、`session_input.py`、`session_runner.py`、`tool_registry.py` | 内部能组成一条候选链，但当前 HTTP 主入口不指向它。`SessionInput` 用进程内字典和 `threading.Lock` 保存输入；不能代替现行 Redis `SessionCoordinator`、持久幂等账本或跨 worker 恢复。 | 保持未接线；只有先证明现行链路的具体缺口、上游适配边界和 owner/状态/预算/工具结果的真实回放，才讨论引入。 |
| 通用工具轮子（13） | `utils/compaction.py`、`concurrency.py`、`context_source.py`、`errors.py`、`event_sourcing.py`、`file_utils.py`、`mcp_adapter.py`、`permissions.py`、`plugin_system.py`、`session_queue.py`、`skill_system.py`、`state_management.py`、`tool_schema.py` | 与现有 `agent_runtime/compaction.py`、`permissions.py`、`session_coordinator.py`、`tool_policy.py`、`context_assembler.py` 等职责部分重叠；未发现逐项运行验收或已跟踪调用点。 | 不批量并入；逐个以具体缺陷和复用证据判断“现有实现/成熟库/薄适配/不用”，不要整套平移编码 Agent 内核。 |
| 简历/实体实验（4） | `utils/agent_loop_v2.py`、`normalization_agent.py`、`resume_agent_loop.py`、`skill_normalizer.py` | 构成另一条简历解析/归一化设想；`skill_normalizer.py` 与 `normalization_agent.py` 会在模型提出新映射后调用 `_save_mappings`，直接写 `backend/config/defaults/dictionaries/*.yaml`，且失败被吞掉。与仓库默认配置发布边界、人审提议不自动提升为真值的现行契约冲突。 | **不得直接接入当前业务链**。先明确证据与人工确认合同；运行时不得静默改仓库权威 YAML。保留文件供溯源，不自动删除。 |

## 下一步判定顺序

1. **先隔离发布身份**：生产镜像只能从干净、固定的提交构建；不要为掩盖这组候选而放宽构建/验收要求。
2. **逐项决定是否还需要**：以当前 Supervisor、L0、owner 资源、预算和工具协议的实际失败为入口，而不是以 `.opencode/MIGRATION_PLAN.md` 的完整清单为待办。
3. **需要接入时才开发**：按 [`AGENTS.md`](../../AGENTS.md) 记录上游 commit/模块/许可证与适配边界，先做一条真实请求到权威结果的回归；中途截断、失败和继续读取不能伪装成完成。
4. **不需要时再归档**：先确认文件归属和是否被其他本机工作使用，再保留原件与来源记录；本轮不移动、不删除这 22 个模块。

**本轮验证范围：**只读代码/文档、已跟踪引用搜索、22 文件静态语法解析和 Docker 构建上下文检查；未启动 Flask、worker、模型或数据库，不声称端到端不可达或功能质量已验收。
