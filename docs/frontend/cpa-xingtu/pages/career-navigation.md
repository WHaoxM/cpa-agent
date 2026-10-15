# 职途导航

**正式页面选择：**以 `cpa-xingtu` 的此路由为唯一联调与交付页面，吸收 `vue-kecheng` 演示里的导入左栏、方向预选、阶段式结果和沉浸画像交互；不维护两条并行真实业务链。旧版 `vue-kecheng` 的模拟进度、岗位规模与评分文案不作为数据契约。该选择已获用户确认。

> 2026-10-10 运行面说明：下方旧的 mock/轻量接口表保留作历史设计参考，**不是**当前 `VITE_API_MODE=http` 的职途导航执行链。真实模式的权威链路与验收见本节；不能将 mock 动画、岗位规模或七维星图当成真实成果。

纯演示模式（`npm run dev:mock` / Pages）保留上传框、粘贴框和五个偏好方向的解析前界面。它使用固定画像样例，不会解析刚上传或粘贴的个人简历；`pipeline/parse.text.json` 只提供演示流程所需的有效结构字段。页面需明确标注“演示样例”，不能把这一流程当作 HTTP 真实确认链路的验收。

## 当前 HTTP 模式：权威数据流

1. 文件或粘贴文字进入同一条 L0：页面把粘贴文字作为 UTF-8 `.txt` 文件，调用 `POST /api/profile/student/upload?async=1`。服务端先提交 owner-scoped 任务与派发意图，立即返回 `task_id`，Celery 再运行文档、结构、实体、证据四个实际分工。方向预选**不进入解析、确认或评分**；它只在简历确认后作为单独的兴趣偏好参与职业方向建议。用户确认了“参与建议，不参与评分”。
2. `needs_input` 候选由用户逐页核对。`observed_pages` 只是提取到文字证据的页数，**不是** OCR 逐字准确率。`missing_pages` 不为空时，页面默认禁止确认；用户可勾选明确知悉缺页后仅确认**已读取内容**，继续部分诊断与候选画像，完整评分须补传清晰文件。后端 L2 对此类确认永不写入已完成画像，即使可见维度暂时有分数。
3. 用户明确点击后，`POST /api/profile/student/confirm` 依据服务端 Principal、L0 任务与确认快照写入 `student_profile`，并在同一数据库事务留下 L2 画像与简历诊断两个持久任务。此时只表示简历事实已确认、分析已排队，不代表画像或报告完成；页面可离开。
4. 正式页面轮询 `GET /api/profile/student/workbench` 的 `analysis_run`、`portrait`、`resume_diagnostic`，只读取得两份独立成果，不在确认后自动发同步 L2/报告 POST。旧 `POST /api/profile/student/pipeline` 保留为用户显式重新生成入口。画像只在权威业务记录回读通过时称已保存；合法 `partial` 是可读的未知候选，不是解析异常，缺证分数为 `null`，不借 mock 七维星图填空。
5. 上传成功后把服务端返回的 L0 `task_id` 写入当前路由 query。深链 `/app/student/career-navigation?task_id=<L0 ID>` 刷新时，待处理任务从 `GET /api/pipeline/status/<task_id>` 与 owner-scoped `GET /api/pipeline/trace/<task_id>` 回放检查点和多 Agent 交接；候选由 `GET /api/profile/student/candidate/<task_id>` 重放，并关联**同一 L0 来源**的最新 `portrait_task`。旧 L0 被后续确认替代返回 409，不拿其他简历画像拼接。百分比为四个已提交检查点的完成比例，不是耗时预测；`GET /api/pipeline/events/<task_id>` 仍只是业务状态转换记录。
6. 当前 L0 已确认且提取出技能后，页面调用 `POST /api/profile/student/confirmed-resume/roles`，只提交确认任务引用与可选 `preferred_role`；后端从当前账号的已确认简历读取技能，不接受浏览器自报技能作为“简历技能”。偏好通过页面 query 保留，刷新仍可调整；它不写入 `student_profile`、L2 画像或人岗评分。方向卡只展示岗位名称、简历技能线索与“含你的偏好”标签，不把接口原始分数/置信度误称匹配分；当来源为未核验目录或部分依赖不可用时明确说明，缺技能时不靠偏好单独制造适合岗位。用户另行填写的技术技能先经 owner/当前 L0 绑定保存在 `_skill_exploration`，随后由 `POST /api/profile/student/skill-exploration/roles` 从服务端快照单独探索；页面分列“已确认简历技能”和“用户自述技能”，后者不参与画像或匹配评分。通用 `/api/career/infer-role` 仍供其他探索场景使用，不是本页已确认简历的权威来源。
7. 新生成的 L2 结果同时留下独立 `snapshot_id`；`GET /api/profile/student/portrait-snapshots` 与 `/<snapshot_id>` 提供当前账号的历史版本对照。页面用只读折叠区展示当前/旧版、当时的简历与自述版本、已知/未知维度，不将旧版拿来填当前画像或岗位分。无快照的旧 L2 仍可按现有工作台读面展示，但不伪造一个历史成果编号。

当前真实四维候选可能已有 D2 值而 D1/D3 缺证；D4 仅在简历中有竞赛、科研、成绩或带队等可引用依据时给出候选分，完全没有这类依据则为 `null`，标记 `growth_evidence`，不能把未知显示成 0。已有值仍属 `partial` 候选，不可标作已发布分数。D3 四项可由学生在同一页面填写自我判断和 12–500 字的具体经历，经 `POST /api/profile/student/self-report` 按当前 L0 任务与修订号保存；后端自动为新修订排队 L2，只作为标记清楚的**用户自述**候选参与 D3，不冒充简历引句。更新自述使旧 L2 画像失效，刷新深链不展示旧分数。学校档位与专业类别依然需各自可信来源，缺页及成长经历遗漏仍须补传；`confirm_semantic_facts` 用于非自述缺口时仍非可执行写入 API。AI 助手不能代替用户完成确认。

### 页面展示与验收边界

- 同一套页面：解析前保留原演示版导入左栏、中心结果预览与仪表盘；真实解析时中心原位显示四个后端检查点及服务端摘要，**不再展示竞争力、岗位匹配和 AI 评语的演示空骨架**。候选产生后逐页核对；L2 返回候选或已保存画像后切到原 `TalentPortrait` 舞台。解析中只给学生看四步进度，不展开逐事件执行日志和历史尝试；已有结果时不再常驻执行记录侧栏，也不呈现内部 Agent 类名、阶段代码、原始 JSON 和任务 ID。排障所需原始轨迹仍可由受鉴权后端接口读取。
- `TalentPortrait` 的真实任务输入是薄适配层：身份/院校、页数、简历技能与经历、四维分数及证据均来自同一 L0/L2 来源。真实四维不强套演示七维；`null` 维度画灰色空心花与虚线条并标“待核对”，不转成 0；`partial` 时不显示综合分或发布完成态。
- 画像的 D3 若采用自述，分项明确标“含用户自述”，展开项显示用户填写的经历；简历诊断报告继续只用原文和确认快照。四项职业素养自述可作为 D3 候选；另行填写的技术技能自述**只用于职业方向探索**，不补 D2、不改变综合分、不进入个人图谱或简历报告。两类自述均不构成可篡改简历或学校档位的入口。
- 若旧 L2 记录把没有任何成长依据的 D4 算作 0，当前候选读取隐藏该旧画像但保留已确认简历，并提供重新生成入口；新的 L2 会将无依据 D4 标为待核对。不能在重新生成之前继续展示或向 Agent 引用旧评分。
- 画像中的项目、竞赛和技能按服务端 `candidate_profile` 的结构化字段分别展示，不把对象序列化成 `desc/role/is_team/award` 键值串。项目经历按原顺序完整呈现，不插入“查看其余”卡；竞赛同名记录在**显示层**合成一组，以文字区分提取出的级别/奖项和同名条数，注明需核对是否为不同届次，不把多个级别并列画成已核实的获奖徽章，也不擅自改写简历或评分输入。技术词按原词保留大小写，精确同词重复只显示一次并标记提及次数；作为独立的按需核对清单默认收起，正文不再铺出词墙。它们可能是工具、方法或重复提及，不能直接宣称熟练程度或能力分数。
- L2 的 D2 评分输入也对去除两端空白后的**完全相同技术词**只计一次，`skill_count`、`skill_list` 和技能深度不因重复提及抬高；简历原文及已确认 `student_profile.skills` 仍保留原条目。`React` 与 `ReAct` 等拼写不同的词不自动合并；这只是保守的重复计数纠正，不是技能归一化或熟练度核验。覆盖目标岗位要求的既有计算规则不变。
- 首屏先解释“从文件取得什么、已确认什么、尚不能判断什么”；学生页面不再显示“技术任务记录”、原始状态码、内部任务 ID 或事件 JSON，排障仍可使用 owner-scoped API 与服务端日志。候选字段按姓名/教育/技能/经历顺序展示，项目、竞赛与带引句的语义事实使用可读字段而非内部对象键串；仍保留逐页原文供核对。窄视口下核对与补充操作和画像连续滚动可达，不能只看到一条红色错误。
- L0 任务虽已入库和派发，但可读轨迹连续 20 秒仍为第 0 轮、仅有“文件已接收”时，左栏说明“后台暂未接手”、任务已保存、可刷新继续且无需重复上传；页面继续轮询，不把等待伪装成解析成功或擅自判定故障原因。隔离队列暂停 worker 后用真实 PDF、HTTP、MySQL 与浏览器复现此状态，恢复 worker 后同一任务完成；截图与步骤见 `../../../guides/career-navigation-state-replay.md`。
- L2 候选出现后，桌面主内容为**一条居中的连续滚动阅读轴**：能力画像、下一步、自述与方向参考上下自然衔接，不再分成固定高度、各自滚动的上下两屏；完成后的执行记录侧栏不再出现。切换简历报告不自动隐藏补充入口。移动端继续使用外层连续滚动；解析中的“过程”按钮保留，结果页不再出现该入口。两种视口均要能到达完整补充表单。
- 方向建议卡排在缺证说明和职业素养补充之后，不压过更急需的事实核对；桌面、390px 移动截图及当前 owner 绑定的 `confirmed-resume/roles` 浏览器请求见 `output/career-nav-browser-check/`。隔离浏览器用已确认 L0 夹具验证：从 URL 恢复“前端开发”，改选“后端开发”再次请求后端；当前 L2 task ID 未变、`student_profile` 未增加 `target_role`。无简历技能时，又验证从同一页面保存 Vue、TypeScript 自述并得到独立的“前端开发/全栈开发”探索线索；刷新可恢复自述，L2 技能分仍为 `null`，真实简历报告 `completed`。截图 `desktop-skill-exploration.png`、`mobile-skill-exploration.png`。另以虚构文字型 PDF 和**无文字层的虚构扫描 PDF**分别验证真实 L0→浏览器核对→用户确认→L2/报告；扫描版截图见同目录 `l0-scanned-pdf-*`，仅证明该受控扫描件而非所有 OCR 版式。可信岗位市场数据仍不在此验收范围内。
- HTTP 模式不宣称“近万岗位/全行业/全国覆盖”；可信岗位目录、匹配与报告未通过证据链时不显示本地模拟推荐。重新上传是修正原简历的入口，不会自动补齐未在原文出现的主观能力事实。
- 后端定向回归 `backend/tests/test_resume_trace_async_mysql.py` 与 `backend/tests/test_resume_golden_path.py`；真实 `5174` 代理需回读已确认 L0 和同源 L2，`partial` 总分保持 `null`。前端跑 `npm run build`。API 200 和构建通过不等于窄屏视觉验收。
- P1 新快照经真实文字 PDF、正式 Cookie、Flask 5001、Vite 5174、MySQL/Redis/worker/Beat 与浏览器再次验收：画像 `ready_with_unknowns` 产生稳定快照，展开“已保存的画像版本”并查看当前版本，桌面截图 `output/career-nav-browser-check/l0-text-pdf-history.png`；390px 无横向溢出。另以受控两次 L2 和新简历确认的 HTTP/MySQL 测试验证旧版保持只读且不参与当前评分。旧有画像不自动补造快照，完整产品版本迁移仍未结束。
- 桌面呈现修正后再次经真实文字 PDF→登录→浏览器确认→后台 L2/报告核验：1440px 主区只有一条滚动轴，画像结束后“继续探索与补充”自然接续；结果页不出现执行记录侧栏，切换报告不会自动隐藏自述入口。截图 `output/career-nav-browser-check/l0-text-pdf-confirmed.png`、`l0-text-pdf-next-steps.png`、`l0-text-pdf-report.png`。另用与原文证据块一致的**受控密集候选夹具**检查 35 条技术词、4 项项目、8 条竞赛的排版：同名竞赛呈 5 个名称，重复技术词呈 34 个不同词；桌面默认收起技术词核对清单，展开后完整可读，390px 无横向溢出；截图 `desktop-dense-stories.png`、`desktop-dense-skills-collapsed.png`、`mobile-dense-skills-expanded.png`。密集夹具只证明呈现，不冒充模型对这类真实简历的抽取准确率。

服务端派发、轨迹和复用边界见 `../../../guides/resume-agent-team-trace.md`；门禁及重放契约见 `../../../guides/career-navigation-state-replay.md`；PDF 页码证据修复见 `../../../guides/attachment-completeness.md`。

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/student/career-navigation` |
| name | `student-career-navigation` |
| roles | student |
| immersive | true |
| public | false |

## 历史 mock 页面职责（非当前 HTTP 真实链路）

引导上传/粘贴简历 → 轻量解析 →（已确认简历时）通过 `/api/profile/student/pipeline` 同步执行 L2 → 进入画像/匹配后续页。旧 `/api/pipeline/trigger` 并非当前页面执行链，strict 模式无执行器。

## 历史 mock / 轻量 API 操作表（非当前 HTTP 执行链）

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 粘贴文本解析 | 提交简历文本 | `POST /api/pipeline/ext/parse/text` | 见下 | 见下 | `pipeline/parse.text.json` | backend-ready |
| 上传文件抽文本 | 选择文件 | `POST /api/pipeline/ext/parse/upload/resume` | multipart | blocks/text | `pipeline/parse.upload.json` | backend-ready |
| 技能抽取 | 解析后 | `POST /api/pipeline/ext/extract/skills` | `{ text, use_llm }` | skills[] | — | backend-ready |
| 方向推断 | 已确认简历有技能后 | `POST /api/profile/student/confirmed-resume/roles` | `{ task_id, preferred_role? }` | 当前确认版本 / source_kind / roles 内的 candidates、status、source_coverage | — | 从 owner 绑定的简历读取技能；降级、偏好与未核验目录单独标注，不显示匹配分 |
| 兴趣探索 | 用户短关键词（非简历） | `GET /api/career/explore?q=` | 短兴趣词 ≤64 | fuzzy/semantic | — | backend-ready |
| 历史管线触发 | 非当前页面调用 | `POST /api/pipeline/trigger` | 见下 | strict: 503，无 task_id | — | backend-gap：未接执行器，不生成 pending 任务 |
| 轮询任务 | 进度条 | `GET /api/pipeline/status/<task_id>` | — | status/progress | `pipeline/status.sample.json` | backend-ready |
| 列管线能力 | 调试/说明 | `GET /api/pipeline/list` | — | L0–L4 描述 | `pipeline/list.json` | backend-ready |
| 真简历入库 | 走 L0 上传 | `POST /api/profile/student/upload` | multipart | task_id | — | heavy-excluded |

### Request / Response 展开

#### 粘贴文本解析

**Request**

```http
POST /api/pipeline/ext/parse/text
Content-Type: application/json
```

```json
{
  "text": "钟梓珉\n本科 计算机\n技能：Vue、TypeScript、Python\n项目：职导星图前端…",
  "student_id": "student_001",
  "source": "career-navigation"
}
```

**Response**

```json
{
  "success": true,
  "data": {
    "name": "钟梓珉",
    "contact": {},
    "education": [{ "school": "示例大学", "major": "计算机科学与技术" }],
    "skills": ["Vue", "TypeScript", "Python"],
    "raw_text": "钟梓珉\n本科 计算机\n…"
  }
}
```

**Fixture:** `cpa-xingtu/public/fixtures/pipeline/parse.text.json`  
**Client 建议:** `api.pipeline.parseText({ text, studentId })`

#### 历史触发管线（当前不可用）

**Request**

```json
{
  "pipeline": "L0_parse_resume",
  "student_id": "student_001"
}
```

**Response（strict 运行面）**

```json
{
  "success": false,
  "code": "pipeline_dispatch_unavailable",
  "error": "该管线入口尚未接入执行器，未创建任务",
  "retryable": false
}
```

**HTTP 503；BE 状态:** backend-gap。请求体 `student_id` 不作为身份来源；demo `/trigger` 只有 `partial` 预览，不能模仿真实任务完成。不要用下面的状态 fixture 冒充本入口返回的结果。

#### 轮询已有任务状态（历史 fixture 示例，不由上述入口创建）

**Request**

```http
GET /api/pipeline/status/task_demo_001
```

**Response**

```json
{
  "success": true,
  "data": {
    "task_id": "task_demo_001",
    "status": "completed",
    "progress": 100,
    "result": { "student_id": "student_001" },
    "error": null
  }
}
```

**Fixture:** `cpa-xingtu/public/fixtures/pipeline/status.sample.json`  
**Client 建议:** `api.pipeline.getStatus(taskId)`

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| 步骤向导索引 | 前端 |
| 解析前预览编辑 | 本地 state |
| 跳转画像/报告 | `router.push` |

## Demo 说明

- `student_001`；mock 模式用 parse.text + status.sample 串联动画
- 现有 seed：`src/mock/resumeSeed.ts`

## 历史实现备注（以顶部当前 HTTP 链路为准）

- **已接线（http 模式）**：`parse/upload` 或粘贴文本 → **`/nsle/resume`（LLM 实体）** → `extract/skills?use_llm` → `career/infer-role` → `portrait/student`（带 `student_profile`）
- **推断来源状态**：后端区分 `complete`、`empty`、`partial` 并列出不可用来源。当前主页面已展示 `partial`/未核验目录警示；此历史轻量链仅供旧设计回溯，不能把候选方向等同于真实市场或岗位匹配评分。
- **禁止**：简历原文进 `explore`；用正则自造 profile 替代 NS-LE；`extract/skills` 关 LLM；FE 哈希伪造 GPA
- `VITE_API_MODE=mock` 时仍走 fixture + 本地画像动画
- 技能图谱/桑基装饰仍用本地 `getCareerInsightsMock` 补全可视化，方向与分数以 API 为准
