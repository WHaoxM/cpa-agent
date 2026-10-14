# 个人能力画像

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/student/career-portrait` |
| name | `student-career-portrait` |
| roles | student |
| immersive | true |
| public | false |

## 页面职责

展示学生四维（及子维）画像，可对照岗位画像；支持流式「解读」动效（本地或 agent）。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 加载学生 4D | 进入页面 | `GET /api/profile/student/student_001/4d` | 可选 `target_job_id` | 见下 | `profile/student.4d.student_001.json` | backend-ready |
| 加载岗位画像 | 选择对照岗位 | `GET /api/profile/job/job_001` | — | 见下 | `profile/job.job_001.json` | field-mismatch：仅已发布快照，旧四维 fixture 字段不保证 |
| 岗位列表 | 下拉选择 | `GET /api/profile/job/list` | `limit` | 列表 | `profile/job.list.json` | field-mismatch：仅已发布摘要，真实目录当前可能为空 |
| 流式人设解读 | 「生成解读」 | `POST /api/agent/chat` | `mode:demo` | reply | `agent/chat.demo.json` | backend-ready |
| 真 LLM 流式 | 同左 | `POST /api/agent/chat/stream` | — | SSE | — | heavy-excluded |

### Request / Response 展开

#### 加载学生 4D

**Request**

```http
GET /api/profile/student/student_001/4d?target_job_id=job_001
```

**Response**

```json
{
  "success": true,
  "data": {
    "student_id": "student_001",
    "completeness": 95.0,
    "dimensions": {
      "D1": { "name": "基础要求", "score": 85.0, "description": "学历、学校、专业等基础条件" },
      "D2": { "name": "职业技能", "score": 80.0, "description": "技术技能匹配程度" },
      "D3": { "name": "项目经历", "score": 78.0, "description": "项目与实践深度" },
      "D4": { "name": "综合素质", "score": 82.0, "description": "软技能与潜力" }
    },
    "sub_dimensions": {
      "skill": { "score": 80.0 },
      "cert": { "score": 70.0 }
    },
    "total_score": 81.0,
    "skill_coverage": 0.72,
    "skill_count": 18
  }
}
```

**Fixture:** `cpa-xingtu/public/fixtures/profile/student.4d.student_001.json`  
**Client 建议:** `api.profile.getStudent4d(studentId, { targetJobId })`

#### 加载岗位画像

**Request**

```http
GET /api/profile/job/job_001
```

**Response（节选）**

```json
{
  "success": true,
  "data": {
    "portrait_id": "job_001",
    "title": "前端开发工程师",
    "d1_weight": 0.25,
    "d2_weight": 0.35,
    "d3_weight": 0.2,
    "d4_weight": 0.2,
    "portrait_json": {}
  }
}
```

**Fixture:** `cpa-xingtu/public/fixtures/profile/job.job_001.json`  
**Client 建议:** `api.profile.getJobPortrait(portraitId)`

**HTTP 模式注意：**上面的 `title`、`d1_weight` 等是旧 mock fixture，不是现行后端公开发布合同。真实接口只接受当前有效发布的岗位 ID，返回来源审核后的快照；未发布或已撤回的岗位为 404。若页面仍需四维权重对照，需要先建立经审核的权重来源与明确的适配契约，不得从未发布旧画像直接回填。

#### 解读（demo chat）

**Request**

```json
{
  "message": "请根据我的四维画像给出解读摘要",
  "student_id": "student_001",
  "mode": "demo"
}
```

**Response**

```json
{
  "success": true,
  "data": {
    "engine": "demo",
    "reply": "演示模式已接收你的问题。当前可展示职业探索、能力画像、人岗匹配和学习路径规划链路；接入 LLM_API_KEY 后可切换到 langgraph 模式。",
    "session_id": "demo_session_001",
    "elapsed_seconds": 0,
    "tool_calls": [],
    "active_subagent": "demo_subagent"
  }
}
```

**Fixture:** `cpa-xingtu/public/fixtures/agent/chat.demo.json`

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| 雷达图动画 | 前端 |
| `useAgentPortrait` 假流式 | 可保留为 UI 特效，数据源换 API |
| 七维展示 | 现有 `careerPortraits` mock；与后端 4D 并存时 adapter 映射 |

## Demo 说明

- `student_001` + `job_001`；冒烟 `profile.student.4d` / `profile.job` PASS

## 后续实现备注

- `src/mock/careerPortraits.ts`：岗位七维目录；与 BE `portrait_json` 对齐需单独映射表
- 前端七维 vs 后端四维：产品层以 4D API 为准，七维可标为展示增强（本地）
