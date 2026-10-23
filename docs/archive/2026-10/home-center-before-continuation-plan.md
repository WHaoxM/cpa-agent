# 首页（Home Center）

> 归档于2026-10-22：这是早期理想接口与Mock方案，不代表当前HTTP首页。现行页面及“接着做”计划由 `docs/frontend/cpa-xingtu/pages/home-center.md` 维护；原文保留供追溯。

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/dashboard` |
| name | `dashboard` |
| roles | 需登录 |
| immersive | true（`keepTopNav: true`） |
| public | false |

### 别名 / redirect

| path | 说明 |
|------|------|
| `/app` | redirect → `/app/dashboard` |
| `/app/student/career` | redirect → `/app/dashboard` |

## 页面职责

聚合入口：跳转职途导航、分析、画像、收藏、报告、学习与 AI；展示本地浏览/任务摘要。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 进入首页 | 路由到达 | — | — | 卡片配置 / 最近记录 | `public/fixtures/home/dashboard.json`（可选） | backend-gap |
| 打开功能入口 | 点击卡片 | —（前端路由） | — | — | — | — |
| 读取最近报告摘要 | 展示「我的报告」预览 | `GET /api/report/list?student_id=student_001` | 见下 | 见 my-reports / catalog | `report/list.student_001.json` | backend-ready |
| 读取目标方向数 | 展示收藏摘要 | `GET /api/target-roles?user_id=student_001` | — | 列表 | `favorites/target-roles.list.json` | backend-ready |

首页**无独立后端聚合 API**。可选组合已有读接口；默认可用纯本地 fixture。

### Request / Response 展开

#### 可选：最近报告摘要

**Request**

```http
GET /api/report/list?student_id=student_001&limit=3
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
      "elapsed_seconds": 38.5
    }
  ]
}
```

**Fixture:** `cpa-xingtu/public/fixtures/report/list.student_001.json`  
**Client 建议:** `api.report.list({ studentId })`

#### 可选首页聚合 fixture（mock-only）

```json
{
  "success": true,
  "data": {
    "student_id": "student_001",
    "quick_links": [
      { "title": "职途导航", "route": "student-career-navigation" },
      { "title": "职业分析", "route": "student-career-analysis" },
      { "title": "个人画像", "route": "student-career-portrait" },
      { "title": "心仪岗位", "route": "student-favorites" }
    ],
    "recent_activity": []
  }
}
```

**Fixture:** `cpa-xingtu/public/fixtures/home/dashboard.json`  
**BE 状态:** backend-gap

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| immersive 布局 | 路由 meta |
| 入口卡片文案 | 前端常量 |
| 浏览历史 | localStorage |

## Demo 说明

- 身份 `student_001`；不依赖后端即可浏览全部入口

## 后续实现备注

- 现有：视图内 store + 路由；无专用 mock 文件
- 若联调，优先只接 report list / target-roles 作角标，其余保持本地
