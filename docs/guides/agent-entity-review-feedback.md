# 人工实体审阅 → 主 Agent 只读反馈

主 Agent 通过既有 `career_data_read` 读取 `career://me/resume`，
`section=entity_links`。此适配器复用 `read_entity_review` 权威服务，不建第二套状态机。

## 返回边界

- `version`：当前已确认简历任务版本。
- `review.revision` / `state_revision`：最新审阅 journal revision；没有审阅为0。
- `review.source_confirmation_task_id/source_sha256`：必须与本次简历提议一致。
- 每条 `decision/candidate/rationale`：原模型建议。
- 每条 `review/review_state/review_revision`：用户审阅；proposed、accepted、rejected、deferred。
- `accepted_relation`：仅接受时透传审阅服务保存的 equivalent/related，不能将related当等价。
- `graph_publication_status`：透传权威投影状态；接受提议本身不证明图谱已发布。

用户 reject/defer 后下一次 Agent 读取立即可见，不改已确认skills，也不写公共图。
调用方 owner 只来自服务器Principal，模型 student_id 参数不授予读取他人资料权限。

## 安全降级与分页

历史简历没有实体提议：not_available、空列表，不捏造审阅。
journal读取失败：review unavailable、revision=null，不得误报为“用户尚未审阅”。
profile与review确认版本在两次读取之间改变：拒绝混合结果，要求重读。

分页携带 source version 和 state_revision。EvidencePack沿用原版本门禁，增加
可选state_revision比较：跨页或继续读取时revision变化，返回显式partial/unavailable，
拒绝追加新版本页面。已经读取的旧页可作为有版本的部分证据，但不能声称当前完整快照。
直接read的消费者必须检查每页review.revision；工具说明与返回continuation明确此规则。

## 复用来源与验证

沿用既有业务资源/MCP风格list-read边界（opencode `388406238b`、Codex `a06545b`），
扩展项目现有EvidencePack版本校验；没有复制review状态转移或新增通用运行时。

`backend/tests/test_agent_entity_review_feedback.py` 使用真实MySQL隔离临时用户：
页面POST审阅 → 主Agent工具分页读 → 再POST拒绝/暂缓 → revision实时更新；
覆盖无Principal、伪造owner、related接受不升级等价、历史/故障降级、跨页版本冲突。
测试清理临时审阅/简历/任务；不修改student_001。
