# 职途导航

> 2026-10-10 运行面说明：下方旧的 mock/轻量接口表保留作历史设计参考，**不是**当前 `VITE_API_MODE=http` 的职途导航执行链。真实模式的权威链路与验收见本节；不能将 mock 动画、岗位规模或七维星图当成真实成果。

纯演示模式（`npm run dev:mock` / Pages）保留上传框、粘贴框和五个偏好方向的解析前界面。它使用固定画像样例，不会解析刚上传或粘贴的个人简历；`pipeline/parse.text.json` 只提供演示流程所需的有效结构字段。页面需明确标注“演示样例”，不能把这一流程当作 HTTP 真实确认链路的验收。

## 当前 HTTP 模式：权威数据流

1. 文件或粘贴文字进入同一条 L0：页面把粘贴文字作为 UTF-8 `.txt` 文件，调用 `POST /api/profile/student/upload?async=1`。服务端先提交 owner-scoped 任务与派发意图，立即返回 `task_id`，Celery 再运行文档、结构、实体、证据四个实际分工。方向预选目前只是页面选择，**不进入解析、确认或评分**，界面必须说明这一点。
2. `needs_input` 候选由用户逐页核对。`observed_pages` 只是提取到文字证据的页数，**不是** OCR 逐字准确率；`missing_pages` 不为空时不能默认全文完整。
3. 用户明确点击后，`POST /api/profile/student/confirm` 依据服务端 Principal、L0 任务与确认快照写入 `student_profile`。此时只表示简历事实已确认，不代表画像完成。
4. `POST /api/profile/student/pipeline` 请求 L2。`data.status=completed` 且画像业务记录回读通过才可称“已保存画像”。合法 `partial` 是 HTTP 200 的业务结果，不是解析异常；页面展示已有候选维度和缺证项，未知分数仍为 `null`，不借 mock 七维星图填空。
5. 上传成功后把服务端返回的 L0 `task_id` 写入当前路由 query。深链 `/app/student/career-navigation?task_id=<L0 ID>` 刷新时，待处理任务从 `GET /api/pipeline/status/<task_id>` 与 owner-scoped `GET /api/pipeline/trace/<task_id>` 回放检查点和多 Agent 交接；候选由 `GET /api/profile/student/candidate/<task_id>` 重放，并关联**同一 L0 来源**的最新 `portrait_task`。旧 L0 被后续确认替代返回 409，不拿其他简历画像拼接。百分比为四个已提交检查点的完成比例，不是耗时预测；`GET /api/pipeline/events/<task_id>` 仍只是业务状态转换记录。

当前真实四维候选可能已有 D2/D4 值而 D1/D3 缺证；前者仍属 `partial` 候选，不可标作已发布分数。学校层次、专业类别和 D3 主观能力须有可引用且经过确认的事实。`next_action=confirm_semantic_facts` 目前只是机读缺口代码，**没有可执行的事实确认写入 API**；`action_available=false`、`resume_state=not_available` 明示此限制。AI 助手可讨论差距，但不能代替用户完成确认。

### 页面展示与验收边界

- 同一套页面：解析前保留原演示版导入左栏、中心结果预览与黑色仪表盘；真实解析时中心原位显示四个后端检查点、右侧原位显示 DocumentAgent → StructureAgent → EntityAgent → EvidenceAgent 的执行与交接，不切入独立的临时预览页。候选产生后左栏逐页核对，L2 返回候选或已保存画像后切到原 `TalentPortrait` 舞台；右侧仍复用同一仪表盘轨迹。
- `TalentPortrait` 的真实任务输入是薄适配层：身份/院校、页数、简历技能与经历、四维分数及证据均来自同一 L0/L2 来源。真实四维不强套演示七维；`null` 维度画灰色空心花与虚线条并标“待核对”，不转成 0；`partial` 时不显示综合分或发布完成态。
- 画像中的项目、竞赛和技能按服务端 `candidate_profile` 的结构化字段分别展示，不把对象序列化成 `desc/role/is_team/award` 键值串。首屏按简历顺序呈现 2 项项目、4 项竞赛和 10 个技能词，其余通过可展开区域完整查看；这不是重要性排序，也不截断底层内容。项目名称、角色、团队属性和描述分别成层；竞赛名称与已提供的级别分开呈现，重复的级别/奖项不重复显示。
- 首屏先解释“从文件取得什么、已确认什么、尚不能判断什么”；任务 ID、原始状态码和事件时间戳放在可展开技术记录，不作为主内容。候选字段保留逐页原文供核对，窄视口下画像仍是独立全屏视图，不能只看到一条红色错误。
- HTTP 模式不宣称“近万岗位/全行业/全国覆盖”；可信岗位目录、匹配与报告未通过证据链时不显示本地模拟推荐。重新上传是修正原简历的入口，不会自动补齐未在原文出现的主观能力事实。
- 后端定向回归 `backend/tests/test_resume_trace_async_mysql.py` 与 `backend/tests/test_resume_golden_path.py`；真实 `5174` 代理需回读已确认 L0 和同源 L2，`partial` 总分保持 `null`。前端跑 `npm run build`。API 200 和构建通过不等于窄屏视觉验收。

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
| 方向推断 | 有技能后 | `POST /api/career/infer-role` | `{ skills }` | predicted_role / candidates / status / source_coverage | — | field-mismatch：后端已标记 partial/empty，现页面尚未呈现降级状态 |
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
- **推断来源状态**：后端现在区分 `complete`、`empty`、`partial`，并列出不可用的图谱/MySQL 来源；当前页面只读取候选和置信度，没有把 `partial` 告知用户，也可能继续使用洞察兜底。不能把 HTTP 模式的候选显示等同于完整来源评分；适配前保持上述 `field-mismatch` 标记。
- **禁止**：简历原文进 `explore`；用正则自造 profile 替代 NS-LE；`extract/skills` 关 LLM；FE 哈希伪造 GPA
- `VITE_API_MODE=mock` 时仍走 fixture + 本地画像动画
- 技能图谱/桑基装饰仍用本地 `getCareerInsightsMock` 补全可视化，方向与分数以 API 为准
