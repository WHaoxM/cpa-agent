# 职业方向推断的来源覆盖状态

更新：2026-10-14。适用 `POST /api/career/infer-role`，输入为抽取后的 `skills`，不是简历原文。

响应保留既有 `success` 与 `data.predicted_role`、`candidates`，并增加顶层和 `data` 内的 `status`：

- `complete`：本次需用的 Neo4j REQUIRES、MySQL `career_insights`、`skill_taxonomy` 查询均成功、有候选，且本次评分/排序过程没有使用未经审核的 `career_insights.core_skills` 或 `skill_taxonomy.related_roles` 冷启动目录（包括结果因 `top_k` 截断而未展示的候选）。它只表示**本接口使用的来源查询完整**，不表示候选适合真实岗位或模型质量已验收。
- `empty`：查询成功但没有技能推断候选（或没有可用技能输入）；不能与依赖失败混为一谈。
- `partial`：至少一个来源查询失败，**或**入选候选使用了尚无逐字段来源审核的 `career_insights.core_skills` 冷启动目录。仍可返回技能匹配候选，但不能把候选或排名当作完整可信结论。`data.partial_reasons` 区分 `dependency_unavailable` 与 `unverified_seed_catalogue`；依赖失败时 `failed_sources` 列出不可用来源，`next_action=retry_when_sources_available`；仅种子目录未核验时 `next_action=await_verified_market_source`。

`data.source_coverage` 分别列出 `neo4j_requires`、`career_insights`、`skill_taxonomy` 的 `available`、`unavailable` 或 `not_queried`。`available` 表示查询成功，**不是**该来源一定有记录，也不是该来源的市场断言已审核。`data.market_data_status=unverified_seed`（至少一个冷启动结构来源可读）、`unavailable`（来源均不可用）或 `not_queried`（无输入），配合 `unavailable_fields` 明确保留岗位需求等级、入门难度及技能市场热度的缺证边界；候选的兼容字段 `demand_level` 固定为 `null`。查询 `career_insights` 只取角色、类别与核心技能；查询 `skill_taxonomy` 不读 `heat_score`，关联职业只做固定中性权重的结构投票。需求等级、入门难度、技能市场热度既不读入评分，也不回传。部分结果仍返回 HTTP 200 和 `success:true`，调用方必须检查 `status` 与 `partial_reasons`，不能只看 `success` 或候选是否非空。

验证：`backend/tests/test_career_role_inference.py` 覆盖合法空结果、图谱故障保留 MySQL 候选、MySQL 故障不伪装空结果、仅改变需求等级或入门难度不改变评分、无证据字段不泄露。显式设置 `CPA_TEST_LIVE_MYSQL=1` 后，`backend/tests/test_career_role_inference_live.py` 用实际 Flask/MySQL/Neo4j 请求及单来源故障注入复验；其中独立 TCP socket 注册正式 career blueprint、读取真 MySQL 种子和 Neo4j，避免 app factory 的 schema ensure。2026-10-14 本机该测试为 2 passed。当前本机 Neo4j `CareerNode-[:REQUIRES]->Skill` 为 0 条，因此正常响应可能只有 MySQL 候选；这不是图谱主评分已验收。正式 `:5001` 进程要重启加载新代码后才会改变行为。

Agent 基础能力参照：本机 `opencode` 快照 commit `388406238bd5ca15564a762840a2362c3a45bd9c`，`packages/opencode/src/session/prompt.ts` 的 MCP resource 读取分支对失败和超限输出显式标记，不把缺失内容伪装为已读取。这里仅借鉴**失败/缺证显式标记**的边界；OpenCode 没有本项目的职业目录、技能评分或市场来源结构，不能复用为岗位评分轮子。此次沿用项目现有 `source_coverage`/`partial` 合同，未复制或重写通用 Agent 状态机。
