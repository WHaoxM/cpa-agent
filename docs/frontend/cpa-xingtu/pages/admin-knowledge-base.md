# 管理端 · 知识库维护

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/admin/knowledge-base` |
| name | `admin-knowledge-base` |
| roles | admin |
| immersive | false |
| public | false |

## 页面职责

管理员维护知识库条目：列表、上传元数据、删除；智能入库属重型。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 加载列表 | 进入页面 | `GET /api/admin/knowledge-base` | — | 见下 | `admin/kb.list.json` | backend-ready |
| 上传元数据 | 上传 | `POST /api/admin/knowledge-base/upload` | 见下 | id/message | `admin/kb.upload.ok.json` | backend-ready |
| 删除 | 删除 | `DELETE /api/admin/knowledge-base/<kb_id>` | — | message | `admin/kb.delete.ok.json` | backend-ready |
| 智能上传 | 智能入库 | `POST /api/admin/knowledge-base/smart-upload` | JSON/multipart | 解析结果 | — | heavy-excluded |
| 批量更新 | 批处理 | `POST /api/admin/knowledge-base/batch-update` | updates[] | — | — | backend-ready |

### Request / Response 展开

#### 加载列表

**Request**

```http
GET /api/admin/knowledge-base
```

**Response**

```json
{
  "success": true,
  "data": [
    {
      "id": "kb_001",
      "title": "前端技能词条",
      "type": "skill",
      "status": "published",
      "created_at": "2026-07-12T10:00:00Z"
    }
  ]
}
```

**Fixture:** `cpa-xingtu/public/fixtures/admin/kb.list.json`  
**Client 建议:** `api.admin.listKnowledgeBase()`

#### 上传元数据

**Request**

```json
{
  "title": "职业路径补充",
  "type": "text"
}
```

**Response**

```json
{
  "success": true,
  "data": { "id": "kb_new_001", "status": "pending", "content_ingested": false, "message": "元数据已创建，内容未入库" }
}
```

当前页面的 JSON 调用只写入知识库元数据，不上传或解析正文；新记录的 `status` 由服务端置为 `pending`，客户端传入的状态不生效。响应明确标 `content_ingested=false`；`title` 与可选 `type` 在写库前校验，非法输入返回 400。后端另有 multipart TXT 私有暂存及管理员内容回读，但本页尚未接线，不执行实体解析/入图。见 `docs/specs/admin-source-staging.md`。

**Fixture:** `cpa-xingtu/public/fixtures/admin/kb.upload.ok.json`  
**Client 建议:** `api.admin.uploadKnowledgeBase(meta)`

#### 删除

**Request**

```http
DELETE /api/admin/knowledge-base/kb_001
```

**Response**

```json
{
  "success": true,
  "data": { "message": "deleted" }
}
```

**Fixture:** `cpa-xingtu/public/fixtures/admin/kb.delete.ok.json`

#### 智能上传（heavy）

**Request（示意）**

```json
{
  "title": "未清洗 JD 文本",
  "content": "…"
}
```

**BE 状态:** heavy-excluded — demo 隐藏或仅 UI mock

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| 文档表格 | 前端 |
| 类型筛选 | 本地 |

## Demo 说明

- 冒烟含 KB list；写操作用 fixture
- Admin 身份同 job-dataset

## 后续实现备注

- 现有：`KnowledgeBase.vue` 内联 `documents[]`
- smart-upload 与 batch-update 可二期再接
