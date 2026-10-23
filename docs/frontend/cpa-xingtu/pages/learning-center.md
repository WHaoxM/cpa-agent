# 技能提升（学习中心）

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/student/learning` |
| name | `student-learning` |
| roles | student |
| immersive | false |
| public | false |

### 别名 / redirect

| path | 说明 |
|------|------|
| `/app/courses` | redirect → `/app/student/learning` |

## 页面职责

HTTP 模式：浏览服务端课程，在同页查看章节与材料，主动记录本人章节已学状态，重登继续。课程目录公开读取；页面登录及本人记录仍由真实账号会话保护。显式 Mock 模式保留演示课程、推荐图与本地记录，不作为真实验收。

带 `artifact_id` 时优先展示原行动计划面板及其加载/错误状态；不论资料是否有效，都不会自动回退课程。通过“退出计划，浏览全部课程”才进入普通课程区。行动计划的步骤进度、练习成果与课程章节记录是不同业务记录。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 课程列表 | 进入页、标题搜索、分类、分页 | `GET /api/learning/courses?keyword=SQL&category=数据分析&limit=6&offset=0` | 参数可选 | `{ data: { courses, count, total, categories, limit, offset } }` | HTTP 不用 fixture | real HTTP |
| 课程详情 | 点进课程 | `GET /api/learning/courses/<course_id>` | — | course 行与 chapters | HTTP 不用 fixture | real HTTP |
| 读进度 | 进入目录、详情、重新读取 | `GET /api/learning/learning/progress/<登录学生ID>` | — | 本人 progress[] | HTTP 不用 fixture | real HTTP |
| 写进度 | 标记本章已学／取消已学 | `POST /api/learning/learning/progress` | 见下 | 写入后核验记录；页面另 GET 读回 | HTTP 不用 fixture | real HTTP |
| 打开材料 | 安全链接 | 登记的绝对 HTTP(S) URL，新标签页 | — | 外部只读材料 | 无 | 不写进度／时长 |

### 写进度 Request

```json
{
  "course_id": "course_001",
  "chapter_id": "1",
  "progress": 100,
  "completed": true
}
```

取消用 `progress=0, completed=false`。owner 只从 Principal 取得；`chapter_id` 是服务端实际章节引用，写前核验课程归属。旧百分比记录只读显示，不擅自转成已学或自动写回。

实际入口为 `HttpCourseLibrary.vue` 与 `api/learning.ts` 的独立 HTTP DTO；不经过会合并 Mock 数据、补默认评分和讲师的 demo store。标题搜索是参数化的字面子串匹配（`%`、`_` 不充当通配符）。`count` 仍是当页条数，`total` 才是筛选后总数，`categories` 来自真实目录。

## 呈现与恢复约定

- HTTP 复用原分类导航、封面卡与双图表结构，不再以纯文本双栏卡替换整页。树图使用 `category_options[{value,label,course_count}]` 与 `catalog_summary{total,scope:all_courses}`，只表示完整目录的分类课程数量；与筛选后的 `total`、当页 `count` 分开。点击或键盘激活树图分类节点只筛选分类，不伪造课程引用。
- 服务端维护八类旧编码名称，正常文本分类原样保留；未知编码及已发现的损坏分类归入“分类待整理”，原值不改写。缺分类独立为“分类未提供”。缺少完整目录元数据时显示可重试错误，不按当页课程估算或画零分布。
- 正常库五条旧资料还存在名称等字段的存储乱码；页面对已观察到的损坏字符明确标为“课程名称待整理／资料文字待核对”，不在前端转码或根据 ID 猜内容。逐条原值与待审差异在修复报告的最终回放中保留，正常库修改需另外确认。
- 弦图区域明确待就绪，不取 Mock 岗位共现；随机种子图片及缺失／加载失败封面使用装饰性占位。卡片只承诺“查看课程详情”，`chapter_count=0` 时明确尚无章节；详情不显示 `0/0` 或标记操作。

- `/app/student/learning?course_id=…` 只负责选择；刷新仍从服务端读取详情和本人记录，定位首个未标记章节。菜单回访卡片标识已有本人记录。
- 缺失讲师、说明、章节简介明确显示未提供；不附加评分、人数、认证或推荐依据。无链接仍可本人标记；危险协议或带凭据链接不提供打开操作。
- 目录加载、空结果、读取错误分别呈现。进度读取失败时状态未知、禁用标记，不展示成零进度；重试后读回。
- 保存期间禁止重复操作；写响应不明时仅重读，不自动重发。账号变化、卸载和过期请求代数阻止迟到响应写入新页面。重新读取时清掉上一轮成功提示。
- 在已打开的章节保存时，若服务端明确返回课程／章节不存在，立即清掉缓存详情并给出重读入口；不是等刷新才发现失效。
- “已标记 X/Y 章”只计服务端已完成且 100% 的章节记录，不声称掌握能力。打开材料无任何学习写入。

## 验收入口

见[用户旅程指南：课程学习](../../../guides/backend-user-journey-acceptance.md#第八条旅程学生选择课程记录章节进度离开后回来继续)。独立探针 `backend/tests/manual/course_learning_browser_probe.py --live` 使用真实 Vue、登录 Cookie/CSRF、隔离完整迁移 MySQL 与空专用 Redis，无预置进度、模型或 worker。结果以该指南链接的日期化报告为准。

### 已核对公开资料的补充

`backend/config/curated_learning_resources.json` 保存两份官方文档的版本、来源、核对日期与六个章节链接，摘要为自行整理；课程不声称有讲师、视频、时长或能力认证。`backend/scripts/import_curated_learning.py --database <目标库>` 默认只读预览；显式 `--apply --confirm-database <相同目标库>` 才在单事务应用。课程 ID 与章节 sequence 是固定身份，不因标题更新换 ID；重复导入保留章节及进度，不删除额外章节，身份冲突或重复序号会回滚。

正常库应用须先审阅逐项差异、另做备份并取得确认；不要为了补全目录覆盖原有课程、章节或进度。可用 `--presentation` 扩展原课程探针验证隔离导入、数量图、缺图／无章节与回访，不把其清理逻辑用于正常库。

2026-10-22 用户另行确认后，正常库 `cpa_agent` 已通过原事务导入器追加 **8 份官方资料、24 个章节**：除上面两份外，新增 Flask、FastAPI、Pro Git、pytest、SQLAlchemy 和 MDN HTTP。本次使用本地 `output/public-data-20261022/learning-candidates.json` 扩展候选清单与薄操作包装，**仓库默认清单仍是原两份**，不能把新克隆默认导入量写成八份。原 151 门课程与 22 个章节逐行未变，学习记录计数仍为 11；没有接 Neo4j 或修改任何个人资料。真实 `5174` 代理的搜索、详情、24 个链接均已回查；完整登录页面路径因账号尚未确认而未验收。命令、备份和两次 TLS 失败后的精确重查见[本次补记](../../../reports/2026-10-22-course-learning-return-replay.md#正常公共资料追加2026-10-22)。SQL 默认时长零和难度 beginner 不是来源事实，不能据此录制时长或官方难度主张。

### 从首页继续同一课程

首页原学习进展区现可列出最多三门有本人记录、仍有未标记章节的课程，直达既有 `?course_id=…`。本页继续从服务端重读详情与本人进度，定位首个未标记章节；首页摘要或链接不提供课程内容、不代替授权、不写进度。课程不可读取时保留错误与返回目录入口，不展示缓存详情冒充当前资料。

正常5174已用既有合成账号完成官方Python资料的搜索、章节、真实外部材料打开、首页续学、退出重登及第二章标记／取消；没有预置进度，也未修改本人真实账号。先前“本人账号尚未确认、完整录屏未验收”的限制仍适用于**用户本人账号**，不应被合成账号回放覆盖。聚合和设计合同归[首页owner](home-center.md#首页接着学2026-10-22)，实际证据归[本轮回放](../../../reports/2026-10-22-home-learning-continuation-replay.md)；原课程入口和故障／权限回归继续保留。

## 未纳入

课程发布审核、播放器、自动跟踪、笔记／测验、推荐质量、时长统计、能力认证未接入本轮普通 HTTP 课程区。Mock 页面继续按原演示行为运行，不因本页接入而宣称全站真实。
