# 技能自评

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/exams` |
| name | `exams` |
| roles | 需登录 |
| immersive | false |
| public | false |

## 页面职责

HTTP 模式通过真实账号完成技能自述并保存：选择方向、逐项选择、服务端核验与回读、退出重登后回看最近一次完整记录。它不是考试、岗位标准、能力认证或匹配结果，不自动改变简历、画像、报告或课程进度。

页面沿用五档本人自述并增加“暂时无法判断”。每题必须明确选择；未知不是零分，不当作能力不足。HTTP 页面不显示本地热度、要求分、综合掌握度、达标、同届排名或投递建议。显式 Mock 模式继续原演示分支，其结果不是真实保存或业务验收。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 可选方向 | 进入页 | `GET /api/career/assessment/questionnaire` | — | 方向、题包版本 | HTTP 不用 fixture | real HTTP |
| 读取题目 | 选择方向 | 同上，`?direction_id=data` | 方向引用 | 问题、选项、版本、方向 | HTTP 不用 fixture | real HTTP |
| 提交本人回答 | 提交并保存 | `POST /api/career/assessment` | 见下 | 已保存记录与完整快照 | HTTP 不用 fixture | real HTTP |
| 最近一次 | 进入页、保存后、重登 | `GET /api/career/assessment/<本人ID>` | — | 最近记录；无记录 404 | HTTP 不用 fixture | real HTTP |

### 当前 HTTP 提交形状

```json
{
  "assessment_kind": "self_reflection",
  "submission_id": "本次提交的随机标识",
  "direction_id": "data",
  "questionnaire_version": "来自服务端题包的版本",
  "answers": { "python": "concept", "sql": "unsure" }
}
```

上例只展示字段形状；真实提交必须覆盖所选题包全部问题。owner 从 Principal 获取，不由 body 指定。题目名称、选项文案、分数或岗位要求不由前端提交；服务端核验引用并保存当时快照。

## 题包与持久化边界

- `backend/config/self_reflection_questionnaire.json` 迁移原页面 5 个方向、15 个细分赛道的 218 个技能主题与原五档自述，新增未知选项；删除旧 `required` 等评分字段。它是产品自述提纲，不是经过验证的测量量表或招聘依据。
- 服务端按题包规范内容生成版本。答题期间版本变化时拒绝保存，页面保留未提交回答，只有学生确认后才清空并读取新题目重新核对；不静默迁移旧选择。
- 迁移 `0015_self_reflection` 在原自评表新增可空的 `reflection_snapshot`、`submission_id` 与 owner＋提交标识唯一约束；不改写旧行，不在请求中自动建列。先按既有迁移手册升级目标环境，隔离回放不代表共享开发库已升级。
- 相同标识与内容重试读回原记录；内容不同冲突。新自评新增一行，旧行不覆盖；幂等重放不因题包更新而重算或晋升为最新。保存结果和最新读取都核对权威 MySQL，最新按 `created_at DESC,id DESC`，不使用旧 Redis 投影替代最近记录。
- 历史回读使用保存的方向、问题、选项与回答，不访问当前题包来补旧记录。没有完整快照的旧行只展示可确认的待补项，明确无法恢复完整问答。

## 页面状态与兼容

- 新页面实现为 `HttpSelfReflection.vue`，由原 `ExamsView.vue` 的 HTTP 分支挂载；不改变其他 Mock 页面或创建通用考试框架。
- 先读取本人最近记录；无记录与读取失败分开。提交期间禁用重复操作，只有保存并回读核验后才进入结果。
- 响应不明只重读；仍无法确认时保留回答，提供重新读取或主动重试同一次提交。另一页面有更新记录时明确提示当前展示最新记录，不把不同记录误认成本次提交。
- 未提交草稿只在本页内存，路由离开和浏览器卸载会提醒；取消离开可继续。账号切换清掉本人记录/回答，迟到请求不能恢复旧内容。
- 原 `gap_only/skill_levels` API 保持兼容；`pipeline/ext/exams` 是另一条旧业务路径，不是本页当前提交入口，本轮不扩展它。旧文档中“优先该接口”的建议已撤销。

## 回放与未覆盖

从[用户旅程指南第九条](../../../guides/backend-user-journey-acceptance.md#第九条旅程学生完成技能自评离开后回来查看自己的回答)进入独立脚本与日期化报告。真实登录、HTTP、隔离 MySQL/Redis，无预置已完成自评，无模型/OCR/worker；未知与无经历分别保存。

不提供历史列表、未提交草稿持久化、分数趋势、课程自动推荐、职业推荐或认证。题目合理性、量表质量及全站 Mock 演示不是本轮通过结论。
