# AI 助手物料契约（Materials & Recommendations）

Status: active  
Last updated: 2026-08-06  
Scope: 定义用户使用 AI 助手时产生的**物料**（工具证据 / 成果物 / 回复结构）与**推荐**形态；mock 与 http 共享同一 UI 契约。

> 本契约替代旧通用"前端/Vue 学习建议"演示内容。它只约束**演示物料形态**，不约束后端真引擎内容质量（由 prompt 决定）。

## 1. 设计原则

1. **产品域 = 职业规划**：简历 → 画像 → 匹配 → 计划 → 报告，不是通用学习答疑。
2. **回复解剖 5 层**：结论 → 证据 → 差距/动作 → 成果物 →（按需）升级提示，遵守 Honest Career Stance（先抓决定项、证据优先、每轮 2–4 条高杠杆）。
3. **零字段渲染（最重要）**：用户通道里**不得出现任何机器/技术字段**——工具函数名、`args`/`parameters`、`task_id`、`artifact_id`、`kind` 底层值、`action` id、`*_json` 一律不渲染。一切展示都必须是产品语义（自然语言摘要、中文标签、可点按钮）。
4. **每条业务回复尽量带 `artifact_ref` 卡**：成果可再次打开（对齐后端 `ARTIFACT_KINDS`）。
5. **工具名用后端真实 13 个工具**，不虚构（仅存在于 mock 数据内部，UI 不显示）。

## 2. 回复解剖（5 层）

| 层 | 内容 | UI 载体 |
|----|------|---------|
| 结论 | 一句话，先抓决定项（院校/岗位门槛、稀缺度、可验证项目深度） | `text` |
| 证据 | 检索 / 查询 / 画像 / 匹配工具的执行摘要 | `tool_call` card（name + summary） |
| 差距与动作 | 2–4 条，短/中期各一层，可执行 | `text`（markdown list） |
| 成果物 | 可再次打开的画像 / 匹配 / 计划 / 简历等 | `artifact_ref` card |
| 升级提示 | 任务超出当前模式预算时，不自动偷升 | `upgrade_hint`（仅按需） |

## 3. 按用户意图的物料映射

| 意图（快捷问） | 触发工具（真实名） | 回复要点 | 成果物 kind / action |
|---|---|---|---|
| 方向：帮我分析适合的职业方向 | `semantic_search`, `skill_match` | 3–5 个候选方向 + 覆盖率依据 | `match` / `open_match` |
| 画像：查看我的能力画像 | `portrait_generate` | 四维画像 + 决定性短板 | `portrait` / `view_portrait` |
| 计划：制定本周学习计划 | `learning_path` | 分周动作 + 课程 + 验收节点 | `action_plan` / `continue_plan` |
| 自评：做一次技能自评 | `skill_match` | 维度得分 + 档位 + 一句建议 | 无（text + 工具卡） |
| 导航：各模块功能介绍 | —（无工具） | 模块功能引导 | 无（notice 口吻 text） |
| 匹配：我和目标岗位差距多大 | `skill_match`, `db_query` | 匹配分 + 技能覆盖率 + 缺失 + 硬约束 | `match` / `open_match` |
| 建议：现阶段重点学什么 | `skill_match`, `llm_reason` | 2–4 条高杠杆建议（先抓决定项） | 无 |
| 简历：简历还需要补充什么 | `pipeline_run`, `llm_reason` | 结构解读 + 2–4 条改进项 | `file` / `open_resume` |
| 课程：有哪些课程适合我 | `learning_path`, `db_query` | 课程清单 + 先后顺序 | `action_plan` / `continue_plan` |

## 4. 工具卡 ui_view 契约（零字段）

- **渲染**：`summary`（一句产品化摘要，如「计算你与目标岗位的技能覆盖率」）+ `status`（已完成 / 运行中 / 失败）。
- **禁止渲染**：工具函数名（`skill_match` 等）、`args` / `parameters` / `task_id` / `result_preview` / 任何内部字段。
- 状态仅 `done | running | error`（`running` 仅出现在 pipeline 任务轮询期）。

## 5. 成果物卡契约

`ArtifactRef` 字段：`artifact_id`、`kind`、`status`、`title`、`summary`、`actions`。

**零字段渲染规则**：UI 只渲染 `title`（中文标题）、`summary`（产品化摘要）、`kind` 的中文标签（见下表）、`status`（已完成等）、`actions` 的 `label`（按钮文字）。**不渲染** `artifact_id`、`kind` 底层值、`actions.id` 等技术字段。

kind + action → 目标页映射（与 `ArtifactCard.vue` 对齐）：

| kind | action | 目标页 |
|------|--------|--------|
| `portrait` | `view_portrait` | `/app/student/career-portrait` |
| `match` | `open_match` | `/app/student/career-analysis` |
| `action_plan` | `continue_plan` | `/app/student/learning` |
| `file` | `open_resume` | `/app/student/resume-builder` |
| `career_report` | `open_report` | `/app/student/career-report` |

## 6. Mock 演示人设（统一且自洽）

> 计算机专业大三、目标后端（Java / Spring Boot / MySQL / Redis 为主）、意向投递中厂后端岗。

所有快捷问的回复基于这一致人设展开：画像 → 匹配 → 计划 → 建议可互相串联（如画像说缺"分布式/消息队列"，匹配与计划必须呼应同一缺口）。

## 7. Mock 实现约束（实现方必须遵守）

1. `src/mock/data.ts` 中 `mockAIResponses` / `mockThinkingTemplates` 重写为职业规划内容，人设统一。
2. `mockToolCallsForCategory` 改用真实工具名 + 一句话 `summary`，**无 `args`**。
3. 新增 `mockArtifactsForCategory(category)`，返回该意图的 `ArtifactRef[]`（无成果物的意图返回 `[]`）。
4. `getAIResponse` 返回 `content / thinking / category / toolCalls / artifacts`；`getAIResponseFromApi` mock 分支透传 `artifacts`。
5. `AIAssistant.vue` mock 分支把 `artifacts` 挂到消息（`MessagePartsRenderer` 会从 `toolCalls + artifacts + content` 自动组装 parts）。
6. `ToolCallCard.vue` 零字段渲染：只显示 `summary` + `status`，**不显示工具函数名**、`task_id`、参数。
7. `ArtifactCard.vue` 增加 `open_resume` → `/app/student/resume-builder` 分支；UI 只渲染产品语义字段。
8. 工具卡渲染顺序：`tool_call` → `artifact_ref` → `text`（与 `MessagePartsRenderer` 一致）。
9. **回复正文只能用 `renderMarkdown` 支持的语法**：标题 / 无序有序列表 / 加粗 / 引用 / 行内代码 / 代码围栏；**不要用 markdown 表格**（当前渲染器不支持，会原样显示为管道符）。

## 8. 不在本轮范围

- 真引擎内容质量（由后端 prompt 决定）。
- 每个意图的端到端真链路数据（mock 仅一条演示主线）。
- 新增交互模式或后端工具。
