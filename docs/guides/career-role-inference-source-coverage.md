# 职业方向推断的来源覆盖状态

日期：2026-10-13。适用 `POST /api/career/infer-role`，输入为抽取后的 `skills`，不是简历原文。

响应保留既有 `success` 与 `data.predicted_role`、`candidates`，并增加顶层和 `data` 内的 `status`：

- `complete`：本次需用的 Neo4j REQUIRES、MySQL `career_insights`、`skill_taxonomy` 查询均成功，且有候选。它只表示**来源查询完整**，不表示候选适合真实岗位或模型质量已验收。
- `empty`：查询成功但没有技能推断候选（或没有可用技能输入）；不能与依赖失败混为一谈。
- `partial`：至少一个来源查询失败。仍可返回其他来源的候选，但不能把降级结果当作完整评分；`data.failed_sources` 列出不可用来源，`data.next_action=retry_when_sources_available`。

`data.source_coverage` 分别列出 `neo4j_requires`、`career_insights`、`skill_taxonomy` 的 `available`、`unavailable` 或 `not_queried`。`available` 表示查询成功，**不是**该来源一定有记录。依赖失败时仍返回 HTTP 200 和 `success:true`，因为接口完成了部分推断；调用方必须检查 `status`，不能只看 `success` 或候选是否非空。

验证：`backend/tests/test_career_role_inference.py` 覆盖合法空结果、图谱故障保留 MySQL 候选、MySQL 故障不伪装空结果；显式设置 `CPA_TEST_LIVE_MYSQL=1` 后，`backend/tests/test_career_role_inference_live.py` 用实际 Flask/MySQL/Neo4j 请求和单来源故障注入复验。当前本机 Neo4j `CareerNode-[:REQUIRES]->Skill` 为 0 条，因此正常响应可能只有 MySQL 候选；这不是图谱主评分已验收。
