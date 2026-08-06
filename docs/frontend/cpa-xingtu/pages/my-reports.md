# 我的报告

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/student/my-reports` |
| name | `student-my-reports` |
| roles | student |
| immersive | false |
| public | false |

## 页面职责

列出当前学生历史报告，打开详情或删除（若产品支持）。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 加载列表 | 进入页面 | `GET /api/report/list?student_id=student_001` | 见下 | 见下 | `report/list.student_001.json` | backend-ready |
| 打开详情 | 点击行 | `GET /api/report/<report_id>` | — | 见 career-report | `report/detail.report_001.json` | backend-ready |
| 删除报告 | 删除（若启用） | （理想）`DELETE /api/report/<id>` | — | — | — | backend-gap |
| AI 成长分析 | 分析按钮 | `POST /api/ai/growth-analysis` | 见下 | AIAnalysisResult | — | backend-gap |

### Request / Response 展开

#### 加载列表

**Request**

```http
GET /api/report/list?student_id=student_001&limit=20&offset=0
```

**Response**

```json
{
  "success": true,
  "count": 1,
  "data": [
    {
      "report_id": "report_001",
      "student_id": "student_001",
      "job_portrait_id": "job_001",
      "title": "职业生涯发展报告",
      "status": "completed",
      "created_at": "2026-07-14T11:33:58Z",
      "elapsed_seconds": 38.5,
      "html_path": null,
      "pdf_path": null
    }
  ]
}
```

**Fixture:** `cpa-xingtu/public/fixtures/report/list.student_001.json`  
**Client 建议:** `api.report.list({ studentId })`  
**注意:** 不是 `GET /api/reports?userId=`

#### 打开详情

同 [career-report.md](career-report.md) 读详情样例。

#### AI 成长分析（理想，backend-gap）

**Request**

```json
{
  "userId": "student_001",
  "reportIds": ["report_001"],
  "snapshots": [],
  "targetRoles": ["前端开发工程师"]
}
```

**Response（对齐现有 `AIAnalysisResult`）**

```json
{
  "generatedAt": "2026-07-18T00:00:00Z",
  "summary": "整体稳步提升",
  "growthScore": 72,
  "scoreTooltip": "基于历史报告对比",
  "topAbilities": ["工程实现", "学习速度"],
  "topAbilityDetails": [],
  "improvementAreas": ["系统设计"],
  "improvementDetails": [],
  "careerSuggestions": ["加强算法基础"],
  "timeline": [],
  "nextSteps": ["完成一门系统设计课"]
}
```

**BE 状态:** backend-gap — 继续用 `src/mock/aiAnalysis.ts` / `api/aiAnalysis.ts`

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| Tab portrait/career 过滤 | 前端对 snapshot.type |
| 空列表占位 | UI |

## Demo 说明

- `student_001`；后端 list 已有数据，页面仍 mock → 接线即 `backend-ready`
- 冒烟：`report.list` / `report.detail` PASS

## 后续实现备注

- `getReportList` 现返回 `[]`；替换为真实 client
- `mockReportRecords` 迁到 fixtures 作 Pages 回退
