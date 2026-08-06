# API 状态标签定义

Status: active  
Last updated: 2026-07-18

页文档与 [api-catalog.md](api-catalog.md) **必须使用下列四态**，勿混用旧 matrix 的 `mock-only` / `partial` / `missing`（旧词仅出现在 matrix 历史表中）。

## 四态

| 标签 | 含义 | 文档写法 |
|------|------|----------|
| `backend-ready` | 后端路由存在且真库冒烟已通（或等价轻量读/写已验证）；前端理想契约可直接对接（字段名已对齐或由 adapter 映射且无已知硬伤） | 操作表 / catalog 标此态 |
| `backend-gap` | 产品需要该 API，但后端无对等路由，或仅有部分能力不足以支撑页面主路径 | 写清理想 path；不强行对齐不存在的后端 |
| `field-mismatch` | 后端路由可用，但请求/响应字段与理想 FE 契约不一致（接通会错或丢字段） | **契约与页文档双标**；写明 FE 字段 ↔ BE 字段 |
| `heavy-excluded` | 后端存在，但触发 L0–L4 真管线、向量重建或真 LLM；demo/冒烟不跑 | 仍写理想调用；注明用 `mode=demo` 或 SKIP |

## 组合规则

- 一条 API 只标一个主标签。若既 mismatch 又 heavy：主标 `field-mismatch`，备注 heavy；或主标 `heavy-excluded`，备注字段问题。
- 「页面仍 mock、后端已通」→ 对 API 标 `backend-ready`（或 `field-mismatch`），**不要**因前端未接线改成 gap。
- 刻意无后端（登录、消息、404）→ 不进 catalog，或标 `backend-gap` 且说明「产品接受无 API」。

## Heavy 边界（与 grill 一致）

| 能力 | 处理 |
|------|------|
| `POST /api/report/generate`（`CPA_DEMO_MODE=false`） | `heavy-excluded`（触 L4） |
| `POST /api/pipeline/trigger` 真跑 L0–L4 | `heavy-excluded` |
| `POST /api/agent/chat` + `mode=langgraph` / `react` | `heavy-excluded` |
| `POST /api/agent/chat` + `mode=demo` | 可测 → 标 `backend-ready` |
| `POST /api/pipeline/ext/parse/text` | 轻量 → `backend-ready` |
| 向量重建 / 真 LLM 批处理 | `heavy-excluded` |

## 身份约定

- Demo：`student_001`
- Favorites / learning 查询参数：`user_id`
- Profile / report / match / 部分 agent：`student_id`
- Adapter 负责从 `User.id` 映射，不在业务页写死两套 ID。

## 参考

- 冒烟：`backend/scripts/smoke_frontend_contracts.py`（含 career.role-intro / messages）
- 快照：`docs/specs/frontend-api-contract-matrix.md`
