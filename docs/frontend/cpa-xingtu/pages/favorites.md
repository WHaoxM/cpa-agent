# 心仪岗位（收藏）

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/student/favorites` |
| name | `student-favorites` |
| roles | student |
| immersive | true |
| public | false |

## 页面职责

管理已收藏岗位与目标职业方向，并展示方向市场卡片。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 加载收藏岗位 | Tab「岗位」 | `GET /api/saved-jobs?user_id=student_001` | — | 见下 | `favorites/saved-jobs.list.json` | backend-ready |
| 添加收藏 | 收藏按钮 | `POST /api/saved-jobs` | 见下 | ok | `favorites/saved-jobs.create.ok.json` | backend-ready |
| 取消收藏 | 删除 | `DELETE /api/saved-jobs/<job_id>` | — | message | `favorites/saved-jobs.delete.ok.json` | backend-ready |
| 加载目标方向 | Tab「方向」 | `GET /api/target-roles?user_id=student_001` | — | 见下 | `favorites/target-roles.list.json` | backend-ready |
| 添加目标方向 | 添加 | `POST /api/target-roles` | 见下 | ok | `favorites/target-roles.create.ok.json` | backend-ready |
| 删除目标方向 | 删除 | `DELETE /api/target-roles/<role>?user_id=student_001` | — | ok | `favorites/target-roles.delete.ok.json` | backend-ready |
| 方向市场卡 | 展开方向 | `GET /api/target-role-market/<role>` | URL 编码 | 见下 | `favorites/role-market.backend.json` | backend-ready |

### Request / Response 展开

#### 加载收藏岗位

**Request**

```http
GET /api/saved-jobs?user_id=student_001
```

**Response（后端形）**

```json
{
  "success": true,
  "data": [
    {
      "id": 1,
      "job_id": "job_001",
      "user_id": "student_001",
      "job_title": "前端开发工程师",
      "company": "字节跳动",
      "industry": "互联网",
      "salary": "20-35K",
      "location": "北京",
      "match_score": 86,
      "required_skills": ["Vue", "TypeScript"],
      "saved_at": "2026-07-14T11:33:58Z"
    }
  ]
}
```

**FE 视图模型（adapter 映射后）**

```json
{
  "jobId": "job_001",
  "jobTitle": "前端开发工程师",
  "matchScore": 86,
  "requiredSkills": ["Vue", "TypeScript"],
  "savedAt": "2026-07-14T11:33:58Z"
}
```

**Fixture:** `cpa-xingtu/public/fixtures/favorites/saved-jobs.list.json`  
**Client 建议:** `api.favorites.listSavedJobs(userId)`  
**BE 状态:** backend-ready（casing → adapter，不单独标 mismatch，除非漏映射）

#### 添加收藏

**Request**

```json
{
  "job_id": "job_001",
  "user_id": "student_001",
  "job_title": "前端开发工程师",
  "company": "字节跳动",
  "industry": "互联网",
  "salary": "20-35K",
  "location": "北京",
  "match_score": 86,
  "required_skills": ["Vue", "TypeScript"]
}
```

**Response**

```json
{
  "success": true,
  "data": { "job_id": "job_001", "message": "ok" }
}
```

#### 添加目标方向

**Request（权威）**

```json
{
  "user_id": "student_001",
  "role_name": "后端开发",
  "priority": 1
}
```

后端亦接受别名 `{ "role": "后端开发" }`。

**Response**

```json
{
  "success": true,
  "data": { "message": "ok" }
}
```

**Fixture:** `cpa-xingtu/public/fixtures/favorites/target-roles.create.ok.json`  
**Client 建议:** `api.favorites.addTargetRole({ userId, roleName, priority })`  
**BE 状态:** backend-ready

#### 方向市场卡

**Request**

```http
GET /api/target-role-market/%E5%90%8E%E7%AB%AF%E5%BC%80%E5%8F%91
```

**Response（全字段；无统计时 `data` 可为 null）**

```json
{
  "success": true,
  "data": {
    "role": "后端开发",
    "salaryRange": "14K-26K",
    "medianSalary": 20,
    "demandLevel": "高",
    "demandCount": 120,
    "hotCities": ["北京", "上海", "深圳"],
    "industries": ["互联网"],
    "skillTags": ["Java", "MySQL"],
    "sampleJobs": ["后端开发"],
    "trendNote": "…",
    "referenceMatch": 120
  }
}
```

**Fixture（demo 丰富卡）:** `cpa-xingtu/public/fixtures/favorites/role-market.backend.json`  
**BE 状态:** backend-ready — 页面用 `getTargetRoleMarketCard` + 本地兜底

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| Tab 切换 | 前端 |
| 乐观更新列表 | store |

## Demo 说明

- `user_id=student_001`；冒烟 saved-jobs / target-roles PASS；role-market 已补全字段

## 后续实现备注

- FE：`favoritesApi` + `mergeTargetRoleMarket`；BE 已兼容 `role`/`role_name` 与 camelCase