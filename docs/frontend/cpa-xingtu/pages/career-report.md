# 职业生涯发展报告

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/student/career-report` |
| name | `student-career-report` |
| roles | student |
| immersive | true |
| public | false |

## 页面职责

基于学生与目标岗位生成/展示多章节职业报告；可保存到「我的报告」。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 读已有报告 | 带 report_id 进入 | `GET /api/report/<report_id>` | — | 见下 | `report/detail.report_001.json` | backend-ready |
| 触发生成 | 「生成报告」 | `POST /api/report/generate` | 见下 | report_id | — | heavy-excluded |
| 轮询进度 | 生成中 | `GET /api/report/<id>/progress` | — | progress | `report/progress.sample.json` | backend-ready |
| 导出 | 下载按钮 | `GET /api/report/<id>/export?format=html` | — | download_url | — | backend-ready |
| 本地预览 | 无后端时 | — | snapshot | 前端拼章节 | `report/detail.mock-snapshot.json` | backend-gap（本地路径） |

### Request / Response 展开

#### 读已有报告

**Request**

```http
GET /api/report/report_001
```

**Response（节选）**

```json
{
  "success": true,
  "data": {
    "report_id": "report_001",
    "student_name": "张同学",
    "chapters": {
      "ch1": { "summary": "基础背景与能力概述" },
      "ch2": { "summary": "目标岗位与 JD 对照" },
      "ch3": { "summary": "匹配与缺口" },
      "ch4": { "summary": "学习路径" },
      "ch5": { "summary": "行动计划" },
      "ch6": { "summary": "总结建议" }
    },
    "report_json": {},
    "download_urls": {},
    "elapsed_seconds": 38.5
  }
}
```

**Fixture:** `cpa-xingtu/public/fixtures/report/detail.report_001.json`  
**Client 建议:** `api.report.getDetail(reportId)`

#### 触发生成（heavy）

**Request**

```json
{
  "student_id": "student_001",
  "job_portrait_id": "job_001",
  "title": "职业生涯发展报告",
  "type": "career"
}
```

**Response**

```json
{
  "success": true,
  "data": {
    "report_id": "report_new_001",
    "status": "pending"
  }
}
```

**BE 状态:** heavy-excluded — Pages 用本地 `careerReportData` / mock snapshot，不写真 generate

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| 地铁图 / 攀升动画 | `careerLines.ts` |
| `deriveStudentSevenDim` | `careerReportData.ts` |
| `reportStore.addRecord` | 本地列表（与 my-reports 衔接） |

## Demo 说明

- 读 `report_001` 可走 http；生成走 mock snapshot
- 冒烟：`report.detail` PASS；generate SKIP

## 后续实现备注

- `src/api/report.ts` stub 路径 `/api/reports` 错误 → 改为 `/api/report/*`
- 类型 `ReportRecord` 与 BE `report_id`/`chapters` 需 adapter
