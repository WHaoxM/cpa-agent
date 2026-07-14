---
status: accepted
---

# 配置与 Workspace 分层

CPA-Agent 后端将业务参数、Prompt、运营数据从 Python 硬编码中拆出，采用 **repo 权威默认 + workspace 白名单覆盖** 的双层模型，并用固定 **配置栈** 在启动时合并。术语见根目录 [CONTEXT.md](../../CONTEXT.md)。

## 决定了什么

1. **业务规则**：`config/defaults/rules/` 为权威默认；workspace 仅可覆盖**可调参数**（权重、阈值、评分表数值），**规则语义**（公式、R-08 硬约束逻辑）由代码实现且不可被 workspace 改写。
2. **Prompt**：repo 存 `base.md`（基座 + 契约）与默认 `tone.md`；workspace 仅可替换 `tone.md`。通过 `config/prompts/registry.yaml` 索引所有 `prompt_id`，禁止靠目录猜测。
3. **Workspace 职责（已校准）**：当前运行面承诺收缩为 rules 可调参数与 prompt tone 覆盖。seeds 与 branding 可保留 loader 和示例文件，但在接入真实冷启动、知识库导入或报告生成链路前，只属于暂缓能力；主体词典、DDL、冻结 schema 留 repo；用户简历与会话等**运行时数据**永不进入 workspace。
4. **Repo 目录树**：
   ```
   backend/config/
     defaults/{rules,dictionaries,integrations}/
     prompts/registry.yaml + {prompt_id}/base.md|tone.md
     schemas/                    # 冻结 JSON 契约，只读
   workspace/{env}/              # 开发默认；见 WORKSPACE_PATH
     workspace.yaml
     overrides/rules.yaml
     overrides/prompts/{prompt_id}/tone.md
     seeds/ | branding/          # 暂缓能力：loader/样例存在，运行面未接线
   ```
   **Workspace 路径解析**：未设置 `WORKSPACE_PATH` 时，使用 repo 根 `workspace/{WORKSPACE_ID}/`（默认 `WORKSPACE_ID=default`）；生产可通过 `WORKSPACE_PATH` 指向部署机外部目录（挂载卷），不再拼接 `WORKSPACE_ID`。
5. **配置栈（严格四层，后层不得越权）**：
   `schemas`（只读）→ `defaults` → `workspace`（白名单）→ `.env`（仅密钥与连接串、`WORKSPACE_ID` 等基础设施）。
   - rules：workspace 对 defaults **deep merge**，仅限注册表白名单键
   - prompts：`base` 不合并；`tone` 整文件替换（workspace 有则用 workspace）
   - seeds / branding：保留 loader 能力；未接入运行面前，不计入配置栈的上线能力
   - workspace 出现未授权键：**启动报错**（拒绝启动）

## 否决了什么

| 选项 | 场景 | 为何否决 |
|------|------|----------|
| 业务规则全进 workspace | 规则选项 B | 同一套代码难以追溯默认行为；竞赛/发版不可复现 |
| 业务规则全锁 repo | 规则选项 A | 机构调参必须改代码发版 |
| Prompt 全进 workspace | Prompt 选项 B | 抽取 JSON 契约易被改坏，与 Schema v2.0 冲突 |
| 仅目录约定、无 registry | Prompt 导航 B | 新人无法定位「改哪一层」 |
| workspace 只管 rules+tone | Workspace 选项 A | 换演示岗位包仍要改 SQL 发版 |
| 种子/词典全迁 workspace | Workspace 选项 C | 主体词表难 diff、难回归 |
| env 覆盖业务 YAML | 配置栈选项 C | 与 `.env` 密钥混用，来源不可追踪 |
| workspace 未知键仅 warn | 配置栈选项 B | 易退化成第三套硬编码（仅迁移期可短期启用） |

## 刻意保留在代码中的内容

- `logic_rules.py`、`privacy_rules.py` 等**约束矩阵与安全正则** — 算法型逻辑，不做 YAML 化
- **规则语义实现** — matcher / portrait 中的公式与门禁流程
- **API 密钥与连接串** — 仅 `.env`，不进 YAML

## 实现顺序

**阶段一（完成 2026-06-18）**：规则与阈值 — `config/defaults/rules/`、`ConfigLoader`（修复空 override bug + YAML 段落包裹）、改造 `portrait_generator` / `matcher` / `entity_normalizer`；Prompt registry 初步落地 (`config/prompts/` + `PromptLoader` + `react_agent` 消费)。

**阶段二（完成 2026-06-18，已校准）**：dictionaries / integrations / schemas 迁移完成；workspace seeds+branding 完成 loader 与示例，但未接入真实业务运行面：
  - `config/defaults/dictionaries/` — skill_lexicon/entity_patterns/pii_patterns 词表 → YAML；`DictionaryLoader`；正则保留代码（ADR-0001: 算法/安全逻辑不做 YAML 化）
  - `config/defaults/integrations/` — LLM presets / embedding / agent 配置 → YAML；`IntegrationLoader`；密钥留 .env
  - `config/schemas/` — extraction schemas → JSON；`SchemaLoader`；冻结契约 `_meta.frozen=true`
  - `workspace/{id}/seeds/` — 追加运营数据 loader（skills/jobs）；暂不作为冷启动/知识库导入主线
  - `workspace/{id}/branding/` — 报告品牌 loader（logo/style/accent_color）；暂不作为报告生成主线
  - `config/__init__.py` 统一导出所有 loader

**阶段三（完成 2026-06-18）**：Prompt registry 扩展 — 散落代码中的硬编码 prompt 全部迁移到 `config/prompts/`：
  - 3A: entity_extractor（5 resume + 4 jd，9 prompt）
  - 3B: tool.llm_reason.system + entity_normalizer（award + generic，3 prompt）
  - 3C: text_cyber 三段（intent / generate / rewrite，3 prompt）
  - 3D: l4_report_generator 摘要（1 prompt）
  - 合计 17 个 prompt（含阶段一 `agent.chat.system`）全部注册；6 个消费方模块改用 `load_prompt()`：resume_prompts / jd_prompts / llm_reason / entity_normalizer / text_cyber / l4_report_generator
  - 关键技术：JSON 模板中的花括号用 `{{...}}` 转义，规避与 `str.format()` 变量插值冲突

**阶段四（完成 2026-06-18）**：Prompt 收尾：
  - 4A: 补完 3C 漏改 — text_cyber.py:299 cypher.intent.user、:378 cypher.rewrite.user 改为 load_prompt（修正 3C 阶段的 f-string 残留）
  - 4B: tool.graph_query.system — graph_query 工具的 system prompt（含动态 schema 注入）
  - 4C: agent.langgraph.system — LangGraph 备用 agent system prompt（含用户阶段感知）
  - 4D: entity.skill_extractor.user — LLM 技能抽取 prompt
  - 合计新增 3 个 prompt（tool.graph_query.system、agent.langgraph.system、entity.skill_extractor.user），总 prompt 数 17 → 20
  - 4 个消费方模块改用 `load_prompt()`：text_cyber（3 处全改完）、graph_query、langgraph_agent、llm_skill_extractor
  - 设计决策：`agent.langgraph.system` 与 `agent.chat.system` 平行建 ID，避免与 ReAct 工具调用工作流耦合

**阶段五（完成 2026-06-18）**：scripts/ 冷启动脚本 prompt 收尾：
  - 5A: 迁移 `scripts/generate_vectors.py` 两个 prompt + `scripts/seed_skill_vectors.py` 一个 prompt
  - 新增 3 个 prompt：`script.vector.skill_enrich.user`、`script.vector.jd_chunk.user`、`script.vector.skill_seed_enrich.user`
  - 总 prompt 数 20 → 23
  - 命名规则：scripts 下 prompt 以 `script.` 前缀 + 子域 + 用途 + `user` 结尾，与 `entity.*` / `tool.*` / `cypher.*` / `agent.*` 风格一致
  - 2 个脚本改用 `load_prompt()`：scripts/generate_vectors、scripts/seed_skill_vectors

## 后果

- 阶段一至阶段二的配置 loader 已完成。`config/` 目录结构完整落地：`defaults/rules/`、`defaults/dictionaries/`、`defaults/integrations/`、`prompts/`、`schemas/`；`workspace/` 当前运行面只承诺 rules 与 prompt tone，seeds / branding 暂不计入上线能力。
- 已实现轻量 `ConfigLoader` 与 `PromptRegistry`（PyYAML + 启动校验），并逐模块替换硬编码常量。
- `WORKSPACE_ID`（默认 `default`）与可选 `WORKSPACE_PATH` 成为部署配置：前者选中 repo 内 `workspace/{id}/`；后者覆盖为外部绝对路径（生产挂载）。
- 新 Prompt 或可调参数必须先登记白名单/registry，否则 workspace 无法覆盖。
