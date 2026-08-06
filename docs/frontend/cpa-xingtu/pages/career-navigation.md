# 职途导航

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/student/career-navigation` |
| name | `student-career-navigation` |
| roles | student |
| immersive | true |
| public | false |

## 页面职责

引导上传/粘贴简历 → 轻量解析 →（可选）触发管线任务并轮询状态 → 进入画像/匹配后续页。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 粘贴文本解析 | 提交简历文本 | `POST /api/pipeline/ext/parse/text` | 见下 | 见下 | `pipeline/parse.text.json` | backend-ready |
| 上传文件抽文本 | 选择文件 | `POST /api/pipeline/ext/parse/upload/resume` | multipart | blocks/text | `pipeline/parse.upload.json` | backend-ready |
| 技能抽取 | 解析后 | `POST /api/pipeline/ext/extract/skills` | `{ text, use_llm }` | skills[] | — | backend-ready |
| 方向推断 | 有技能后 | `POST /api/career/infer-role` | `{ skills }` | predicted_role / candidates | — | backend-ready |
| 兴趣探索 | 用户短关键词（非简历） | `GET /api/career/explore?q=` | 短兴趣词 ≤64 | fuzzy/semantic | — | backend-ready |
| 触发管线 | 「开始分析」 | `POST /api/pipeline/trigger` | 见下 | task_id | — | heavy-excluded |
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

#### 触发管线（heavy）

**Request**

```json
{
  "pipeline": "L0_parse_resume",
  "student_id": "student_001"
}
```

**Response（示意）**

```json
{
  "success": true,
  "data": {
    "task_id": "task_demo_001",
    "status": "pending",
    "progress": 0
  }
}
```

**BE 状态:** heavy-excluded — Pages/冒烟用 fixture 模拟进度，勿真跑。

#### 轮询状态

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

## 后续实现备注

- **已接线（http 模式）**：`parse/upload` 或粘贴文本 → **`/nsle/resume`（LLM 实体）** → `extract/skills?use_llm` → `career/infer-role` → `portrait/student`（带 `student_profile`）
- **禁止**：简历原文进 `explore`；用正则自造 profile 替代 NS-LE；`extract/skills` 关 LLM；FE 哈希伪造 GPA
- `VITE_API_MODE=mock` 时仍走 fixture + 本地画像动画
- 技能图谱/桑基装饰仍用本地 `getCareerInsightsMock` 补全可视化，方向与分数以 API 为准
