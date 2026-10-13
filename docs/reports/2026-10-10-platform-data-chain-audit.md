# 平台数据链路审计：已接通、已修复与仍中断的环节

Date: 2026-10-10
Scope: 本机 `mkg` 后端、Docker MySQL/Neo4j/Redis、`student_001` 已确认简历、`5174` backend-dev 前端。此文是当次运行证据，不替代业务契约或未来验收。

## 验收方法

每个功能按 **用户动作 → Principal → API/任务 → 权威写入 → 同版本读取 → 页面/Agent** 核对。API 200、页面能打开、模型生成文案或本地 mock 都不能单独证明业务数据链完成。`student_001` 是本机开发身份；正式部署仍需认证适配器提供 Principal。

| 链路 | 本机权威状态与本轮证据 | 本轮修复 | 尚未打通 |
| --- | --- | --- | --- |
| 简历 → 画像 | L0 当前确认版本 `task_56edb2e94056` 可从 `career://me/resume` 读取；最新 L2 画像为 `partial`，不能发布总分。 | 沿用已有逐页证据/确认门禁；不把 L2 候选改写成完成画像。 | 画像缺事实依据仍需用户核对；不以旧 `student_portrait_4d` 行代替当前版本。 |
| 匹配 → 报告 | 本机可信岗位发布、匹配、报告均为 0 条；L3/L4 无权威成果可供当前用户展示。 | 旧详情与报告导出复用 `match_report_resources` 的任务、归属、摘要和当前来源版本校验；临时真库正例 200 partial、跨 owner 拒绝、撤回来源后 409 且不返回旧分/正文。 | 可信岗位目录未发布；旧列表与 match explain 读面尚未全数接同一门禁。 |
| 个人图谱 | `student_001` 的 `PersonalSkill` 前 10 项可按源 `resume-confirmation:task_56edb2e94056` 分页读，另一身份不可见。 | 保持 `graph_query(personal_skills)`，未另建图谱会话或复制成 MySQL 用户库。 | 岗位关联及公共图谱完整覆盖未验收。 |
| 学习进度 | 临时真实课程/章节写入进度后，`career://me/learning-progress` 回读同一 owner 的课程、章节和进度；另一个 owner 为空。 | 写前校验 `0–100` 数值及 `course JOIN chapter` 配对；不存在章节 404 且不落孤儿行。 | 当前真实库的课程、章节和进度存量均为 0，不能说已有推荐内容。 |
| 收藏与目标方向 | 本机 `student_001` 的 saved-jobs、target-roles API 均真实返回空数组。 | HTTP 前端空结果现在覆盖演示收藏；读取失败也不回填 mock。 | 页面点击链路未通过浏览器自动化验收；岗位目录和用户收藏仍为空。 |
| 成长分析 AIGC | 修复前，真实 HTTP 在没有可核验报告时仍调用模型，读取浏览器 `snapshots` 并引用旧画像的 0.0 值。 | 现只读 `career://me/portrait` 与 `career://me/reports[/id]` 权威投影；前端不再发送 `userId`/`snapshots`，后端也不信任它们。无已发布同版本画像/报告返回 409 partial，不调用模型。`growthScore` 在成功路径由已发布画像的确定性总分覆盖模型输出。 | 当前 L2 partial 且报告 0，故本机成长分析应显示资料未就绪；AIGC 建议事实性及职业推荐来源尚未评测。 |
| 职业建议 AIGC | 修复前，`target_role` 表为空仍可由请求体 `targetRoles` 触发模型，真实 HTTP 返回了 3 条建议；`career_insights` 冷启动数据无岗位来源字段。 | 现只接受当前 Principal 保存的目标方向，并用共享 EvidencePack 读取已确认简历技能；无保存方向返回 409 needs_input，模型不运行。回复标 `draft_advice_only` 与证据覆盖，既不读冷启动薪资，也不冒充实时岗位推荐。 | 当前目标方向为 0，故没有可诚实生成的个性化建议；前 10 项技能以外的证据仍需分页，建议事实性待评测。 |
| 页面 → Agent | 职途导航提交不透明 `task_id`，服务端按 Principal 核验并仅在任务与当前资源版本一致时挂载；Ask、Agent 已实测读取当前确认简历。 | 本轮未再复制任务 JSON 到会话。 | 浏览器自动化连接失败，不能宣称完整点击/视觉旅程已验收。 |

## 本轮回归与重放

- 后端使用 `D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests -q`，从仓库根目录执行；合并后结果为 **748 passed、21 skipped**。`21 skipped` 不代表所有可选真库/浏览器用例已验收。
- 前端 `cpa-xingtu` 的 `npm run type-check` 与 `npm run build-only` 通过；构建有大 chunk 和 `lottie-web` eval 警告，不是本轮数据链路完成证明。
- 开发后端 `5001` 使用 `CPA_DEV_AUTH=1`；真实本机请求须带 `X-Dev-Student-Id: student_001`。无头的 401 是预期身份门禁。
- `POST /api/ai/growth-analysis` 在当前无权威报告/正式画像的状态下应返回 `409`、`status=partial`、`data=null`、`next_action=complete_verified_portrait_and_report`；不得输出 `growthScore=0` 当作测评结果。
- `POST /api/agent/career-suggestions` 即使请求体自报 `targetRoles`，当前 `target_role` 仍为空时应返回 `409`、`status=needs_input`、`next_action=save_target_role`；不调用模型编造个人建议。
- `GET /api/saved-jobs?user_id=student_001` 与 `GET /api/target-roles?user_id=student_001` 带开发身份头返回 `data: []`；前端 HTTP 模式不再显示 starter mock 收藏。
- 匹配/报告的来源撤回与跨身份读侧测试使用临时真库数据，测试清理后本机岗位发布、匹配、报告仍为 0。

## 下一批优先级

1. 把旧列表、match explain 与报告集合页也接同一权威来源版本门禁；否则详情安全而列表仍可能展示旧分。
2. 建立可发布、可撤回、带来源和质量审核的岗位目录，再做 L3/L4 真正的持久化金路径；不能为了跑通页面伪造岗位。
3. 给课程/项目/网站推荐建立同样的来源、更新时间、owner 和反馈合同；目前“空库 + 模型推荐”不能作为可信推荐。
4. 在可用浏览器会话中逐页点选，核对 5174 的 API 请求、错误态与最终组件；本轮只证明真实 API 和构建/类型检查，未证明点击体验。
