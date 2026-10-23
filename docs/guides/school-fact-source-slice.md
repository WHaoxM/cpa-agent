# 院校事实源：211 历史名单的只读候选切片

**状态：已接入被选中的 `school-context` 子步骤；真实 MySQL 合成 owner 与 D1 行前后快照、一次隔离 HTTP→Celery→真实模型回放均有证据。但该次模型答复越出了来源边界；随后增加的结构化事实 finalizer 保护目前只有无模型回归，尚未复跑真实模型。**本文件不能作为学生工作台已上线声明。

## 来源与边界

- 原始来源：[教育部“211工程”学校名单](https://www.moe.gov.cn/srcsite/A22/s7065/200512/t20051223_82762.html)，页面标注生成日期 `2005-12-23`。2026-10-17 从该页面的名单表读取 28 行、每行 4 校，得到 112 个互异名称；与教育部对 211 工程 112 所的[术语说明](https://www.moe.gov.cn/jyb_xwfb/xw_zt/moe_357/s7865/s7987/s7995/201404/t20140428_167741.html)一致。抓取 HTML 的 SHA-256 为 `3c92af7f7a132a290fb8f5673ffe922f4b109f9aa0f941200ce1b1a7bdd3e3f6`。
- 固定数据放在 `backend/config/agent_skills/school-context/references/moe-211-2005.json`，与解读方法 `SKILL.md` 分开。纯查找 `backend/app/services/agent_runtime/school_facts.py::lookup_historical_211` 只对名单原名精确匹配；未命中或改名/校区/别名歧义统一为 `unknown`，**不输出“非 211”**。
- 这是历史建设工程名单的正向事实来源，不等于当前学校身份目录、985/双一流名单，也不授予 D1 评分权限。`国防科技大学` 等可能与名单旧名称相关的输入仍为未知，直到有单独审阅的别名映射；不做相似名推断。
- `read_owner_school_context(mysql, student_id=...)` 只接受服务端可信的当前学生 ID，复用 `require_confirmed_profile` 回读 L0 已确认资料及其版本；其他 owner、未确认资料或读取失败不提供学生院校标签。`scoped_invoke` 仅在校验后的 QA 步骤选中 `school-context@1.0.2` 时读取该结构化上下文，不加载 112 个校名进主 Agent 提示。步骤 metadata 带 Skill 内容哈希、名单数据哈希、资料版本，并产生一条真实的只读来源读取过程卡。`matched_official_name` 仅指**简历自报校名精确命中名单**，不证明本人学籍；仍未把任意用户文本提升为已确认简历，也未写入 `school_tier_review`、`sub_school`、`dimension_score` 或画像状态。
- 当前名单仅能给出历史 211 **正向精确命中**。`985`、双一流标为 `not_evaluated`；未命中/别名歧义标为 `unknown`，不得推断“非 211”或“不是双一流”。没有可信资料/名单来源时标记部分状态及下一步，不伪称已核验。
- `project_verified_school_facts(context)` 将 owner profile 与名单来源投影成有界结构化事实（精确状态、source title/publisher/date/URL 与哈希、profile 版本、不评估标签、无评分效果）；不包含自由模型候选或来源未提供的文号。持久 `career-coach/career-planning` finalizer 只收到步骤状态和此投影，不收到 QA 自由文本、工具原始数据或 Skill 文本；受控规则要求院校断言只能依据 `verified_facts`。回复后还有可判定的文号/年份/来源标题/其他标签/招聘结果检查，越界时记录稳定 reason 并使用确定性安全答复。无事实时跳过最终模型调用并说明无法判断。Ask 与旧 Experts 未启用此院校专用限制。

## 核验

本机用 `mkg` 环境将 JSON 的 112 个名称按顺序与教育部在线表格逐项比对：`exact_order_match=True`、`html_hash_match=True`。当前定向无模型回归覆盖只读 owner 核验、非 owner/未知学校降级、仅选中 Skill 时读取并注入事实、来源和资料哈希/版本回传，以及 finalizer 的文号/来源标题/招聘边界检测与安全 fallback；截至 2026-10-19 为 **28 passed、1 skipped**。

独立 live probe 设置 `CPA_TEST_LIVE_MYSQL=1` 后在随机 `cpa_school_fact_<12 hex>` schema 上迁移到当前 Alembic head，插入一份合成已确认简历和一条非空 `student_portrait_4d` D1 行。选中 Skill 的真实 DB 只读链返回 `matched_official_name`、资料版本与名单哈希；调用前后画像 `d1_score`/`portrait_json`、`school_tier_review` 行数及 `student_profile` 原文完全相同；其他 owner 得不到院校事实。该测试 **1 passed（21.59s）**，结束后关闭连接并删除且仅删除随机测试 schema。模型用 fake LLM，因此这是数据库/Skill 输入验证，不是实际模型引用、HTTP 页面或网站后续更新验证。

2026-10-19 的真实模型隔离样本见[职业规划能力包回放报告](../reports/2026-10-19-career-capability-live-replay.md)：HTTP/计划/Skill 来源哈希/完成状态/D1 不变通过，但最终文案编造了来源没有的文号并推断招聘信任度，因此事实边界只判部分通过。补上的结构化 facts 与 finalizer fallback 仅经无模型测试，**不得声称它已经在实时模型链路验证**；为避免重复模型请求，本轮没有重跑。重新验证须另获确认，并核对最终来源标题、不得有文号/招聘推断，及完整 owner 行与 D1 快照。该次测试用随机 MySQL schema 与隔离 Redis DB/Celery worker，并已删除 schema、清空 Redis、退出 worker；留下 pytest 临时证据，不代表真实用户确认 API 或浏览器链路。
