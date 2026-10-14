# 管理端 · 岗位数据集

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/admin/job-dataset` |
| name | `admin-job-dataset` |
| roles | admin（`meta.role`） |
| immersive | false |
| public | false |

## 页面职责

管理员查看、上传元数据、删除岗位数据集记录。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 加载列表 | 进入页面 | `GET /api/admin/dataset` | — | 见下 | `admin/dataset.list.json` | backend-ready |
| 上传元数据 | 上传表单 | `POST /api/admin/dataset/upload` | 见下 | id/message | `admin/dataset.upload.ok.json` | backend-ready |
| 删除 | 删除按钮 | `DELETE /api/admin/dataset/<ds_id>` | — | message | `admin/dataset.delete.ok.json` | backend-ready |
| 触发建库管线 | 「构建」 | `POST /api/admin/pipeline/build` | source… | 503，无 task | — | backend-gap：元数据不等于可建图内容，执行器未接入 |

### Request / Response 展开

#### 加载列表

**Request**

```http
GET /api/admin/dataset
```

**Response**

```json
{
  "success": true,
  "data": [
    {
      "id": "ds_001",
      "title": "AI 算法岗位包",
      "category": "AI",
      "count": 800,
      "status": "ready",
      "description": "AI 算法工程师、NLP、CV 等岗位数据",
      "created_at": "2026-07-12T10:00:00Z",
      "error_message": null
    }
  ]
}
```

**Fixture:** `cpa-xingtu/public/fixtures/admin/dataset.list.json`  
**Client 建议:** `api.admin.listDatasets()`

#### 上传元数据

**Request**

```json
{
  "title": "前端岗位增量包",
  "count": 120,
  "category": "前端"
}
```

**Response**

```json
{
  "success": true,
  "data": { "id": "ds_new_001", "status": "pending", "content_ingested": false, "message": "元数据已创建，岗位未入库" }
}
```

当前页面的 JSON 调用只写入数据集元数据，不上传岗位正文或运行导入；新记录的 `status` 由服务端置为 `pending`，实际岗位 `count` 为 `0`，客户端传入的状态或数量不作为完成/入库证据。响应明确标 `content_ingested=false`。`title` 与可选 `category` 在写库前校验，非法输入返回 400。后端另有 multipart JSON 文件私有暂存及管理员内容回读，但本页尚未接线；暂存条目数是 `staged_record_count`，不是 `count`，建库管线仍 503。见 `docs/specs/admin-source-staging.md`。

**Fixture:** `cpa-xingtu/public/fixtures/admin/dataset.upload.ok.json`  
**Client 建议:** `api.admin.uploadDataset(meta)`

#### 删除

**Request**

```http
DELETE /api/admin/dataset/ds_001
```

**Response**

```json
{
  "success": true,
  "data": { "message": "deleted" }
}
```

**Fixture:** `cpa-xingtu/public/fixtures/admin/dataset.delete.ok.json`

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| 表格排序筛选 | 前端 |
| 上传进度条 UI | 本地假进度（元数据接口瞬时返回） |

## Demo 说明

- Admin 账号见 auth fixtures；冒烟 `admin.dataset.list` PASS
- Pages：列表 fixture，写操作返回 ok fixture

## 后续实现备注

- 现有：`JobDataset.vue` 内联 mock `records[]`
- 接线替换为 admin client
