# 职业路径读取：可信匹配与公共图谱边界

## 当前问题

严格身份模式的 `GET /api/match/career-recommendation/<student_id>` 直接读取旧 `career_path.path_json`。该表曾由接受客户端 `student_id`、`recommended_dir` 和任意 `path_json` 的保存路由写入；该写路由在严格模式已停用，但历史行仍可被读取，并不证明来自当前发布岗位、确认画像或图谱。因此 owner 一致也不能使其成为职业推荐。

## 窄域合同

- 服务端 Principal 绑定路径 owner；可选 `job_portrait_id` 只用于选取该 owner 的 L3 匹配。未指定时选最新 L3 行，不能读取旧 `career_path` 表。
- 选中 L3 行须经 `read_match_report_resource` 的任务、摘要、确认画像和当前岗位发布版本校验。无匹配是 `404`；过期为 `409 stale`，不暴露旧路径、分数或岗位名。
- 精确岗位名称只能从已验证匹配内的 `job_snapshot.position_name` 取，不从可变 `job_portrait_4d`、客户端参数或旧路径表取。仅调用既有 `/api/graph/query` 的公共 `CareerNode`、`PROMOTION` 1–3 跳参数化模板。不回退到共享 `Skill`/`JobPortrait`/`Person` 或伪造映射。
- 命中的图谱边是**通用公共晋升路径参考**，并非针对学生的个性化推荐。响应不能声称已经算出 `recommended_dir`；所有输出显式标记覆盖范围。若无精确图谱路径，保留已核验的岗位上下文并标为 `partial`，提示补充公共岗位映射；图谱故障亦为 `partial`，列出不可用来源并给重试动作。
- 显式 demo 模式保留旧样例读取行为，不将其称为严格模式证明。

## 验收

先用 owner 可读取的旧 `career_path` 行重现假推荐；再验证同一行在严格模式绝不进入响应。实际 HTTP + MySQL/Neo4j 测试应覆盖：已验证 L3 + 精确公共节点/晋升边、无节点、图谱失败、撤回岗位后的 409、跨 owner 拒绝、无 L3 行、旧行未被查询、无私有图数据。只在确实命中的边上声称公共路径；不能从测试中推断岗位名称语义映射已覆盖生产数据。

复用：OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` MCP resource 读取和 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` scoped resource read 的 owner/来源验证形态已落在本项目 `match_report_resources.py`。本次不重造 Agent 状态/权限层，而复用此资源回读及本项目已有公共图固定查询模板；该模板只证明路径来自 public-only `CareerNode`，不证明对用户适配或岗位语义映射。
