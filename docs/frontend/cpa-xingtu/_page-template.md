# 单页文档模板

复制到 `pages/<name>.md` 后替换尖括号内容。P0 页写满 Request/Response；非 P0 允许 Response「见 catalog 同形」或短样例。

```markdown
# <页面中文名>

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/...` |
| name | `route-name` |
| roles | student / admin / any（需登录） |
| immersive | true / false |
| public | true / false |

### 别名 / redirect

| path | 说明 |
|------|------|
| （无则删本节） | |

## 页面职责

<一句话：用户在此页完成什么>

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 加载列表 | 进入页面 | `GET /api/...` | 见下 | 见下 | `public/fixtures/...` | backend-ready |

### Request / Response 展开（P0 必填）

#### <操作名>

**Request**

```http
GET /api/example?user_id=student_001
```

```json
{}
```

**Response**

```json
{
  "success": true,
  "data": {}
}
```

**Fixture:** `cpa-xingtu/public/fixtures/<domain>/<name>.json`

**Client 建议:** `api.<domain>.<methodName>()`

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| UI 展开折叠 | 无后端 |

## Demo 说明

- 身份：`user_id` / `student_id` = `student_001`
- Pages mock：上述 Fixture；登录账号见 [mock/README](../mock/README.md)

## 后续实现备注

- 现有 mock：`src/mock/<file>.ts` → 迁到 fixtures
- Adapter：`VITE_API_MODE=mock|http`
- 已知 mismatch：列出或写「无」
```

## 状态标签

只用 [status-legend.md](contracts/status-legend.md) 四态：`backend-ready` | `backend-gap` | `field-mismatch` | `heavy-excluded`。

纯前端、刻意无 API 的登录/Landing 等：操作表可写「—」并在本地-only 说明；catalog 可不登记。
