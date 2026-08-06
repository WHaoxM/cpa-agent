# 职业分析

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/student/career-analysis` |
| name | `student-career-analysis` |
| roles | student |
| immersive | true |
| public | false |

## 页面职责

展示职业赛道全景、洞察与探索搜索，支撑方向选择。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 加载全景 | 进入页面 | `GET /api/career/landscape` | — | 见下 | `career/landscape.json` | backend-ready |
| 加载洞察列表 | 筛选/分页 | `GET /api/career/insights` | 见下 | 见下 | `career/insights.list.json` | backend-ready |
| 打开角色洞察 | 点击角色 | `GET /api/career/insights/<role_name>` | path 编码 | 单行洞察 | `career/insights.detail.json` | backend-ready |
| 关键词探索 | 搜索框 | `GET /api/career/explore?q=&top_k=` | 见下 | fuzzy/semantic | `career/explore.json` | backend-ready |
| 技能树（可选） | 进阶面板 | `GET /api/career/skill-tree` | query | skills | `career/skill-tree.json` | backend-ready |

### Request / Response 展开

#### 加载全景

**Request**

```http
GET /api/career/landscape
```

**Response**

```json
{
  "success": true,
  "count": 15,
  "data": [
    {
      "category": "AI算法",
      "role_count": 6,
      "avg_salary_range": "19K-38K",
      "roles": [
        "计算机视觉工程师",
        "NLP工程师",
        "多模态算法工程师",
        "算法工程师",
        "大模型算法工程师",
        "强化学习工程师"
      ]
    }
  ]
}
```

**Fixture:** `cpa-xingtu/public/fixtures/career/landscape.json`  
**Client 建议:** `api.career.getLandscape()`

#### 加载洞察列表

**Request**

```http
GET /api/career/insights?limit=5&offset=0
```

**Response**

```json
{
  "success": true,
  "count": 5,
  "data": [
    {
      "role_name": "算法工程师",
      "category": "AI",
      "avg_salary_low": 18,
      "avg_salary_high": 32,
      "demand_level": "high",
      "core_skills": ["Python", "机器学习", "PyTorch"]
    }
  ],
  "total": 100,
  "limit": 5,
  "offset": 0
}
```

**Fixture:** `cpa-xingtu/public/fixtures/career/insights.list.json`  
**Client 建议:** `api.career.listInsights(params)`

#### 关键词探索

**Request**

```http
GET /api/career/explore?q=%E5%89%8D%E7%AB%AF&top_k=5
```

**Response**

```json
{
  "success": true,
  "data": {
    "fuzzy_matches": [{ "role_name": "前端开发工程师", "score": 0.92 }],
    "semantic_matches": []
  }
}
```

**Fixture:** `cpa-xingtu/public/fixtures/career/explore.json`  
**Client 建议:** `api.career.explore({ q, topK })`

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| 图表配色 / 布局 | 前端 |
| 选中 category 高亮 | 本地 state |

## Demo 说明

- 不强制 `student_id`；展示数据与用户无关时可直接用 landscape fixture
- 冒烟：`career.landscape` / `career.insights` PASS

## 后续实现备注

- 现有：`useCareerInsights` 等 mock 图数据
- 接线后替换为 catalog 上述四读接口
