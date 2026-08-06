# 前端页面 API 契约矩阵与真库实测

Status: active  
Last updated: 2026-07-18

## Grill 约定（已决）

| 项 | 决定 |
|----|------|
| 重型不测 | L0–L4 真管线、向量重建、真 LLM Agent（请求体可用 `mode=demo`） |
| 清单来源 | 前端页面契约（`routes.ts` + `api/backend.ts` + 页面 mock/TODO） |
| 推进 | 测已接通；缺口只记文档，本轮不补 API |
| 通过标准 | 真库（MySQL/Redis；相关页再加 Neo4j）；主路径读写打到存储 |
| 缺口范围 | 主路径完整契约；其余页只标 `mock-only / partial / missing` |
| 身份 | 固定 `user_id=student_001`、`student_id=student_001`；登录仍 mock |

环境：`docker-compose` + `docker-compose.dev.yml` 已起；`.env` 指向 `MYSQL:13306` / `REDIS:16379` / `NEO4J:17687`。  
实测时设 `CPA_DEMO_MODE=false`（避免读接口假通过）；Agent 仍用 body `mode=demo`。

**冲突处理：** `POST /api/report/generate` 在 `CPA_DEMO_MODE=false` 时会触发 **L4 真管线** → 属重型，本轮只测 `list` / `detail`；generate 标为 `heavy-excluded`。

---

## 主路径契约（完整）

| 页面 | 期望 API | 后端路由 | 前端接入 | 真库预期 |
|------|----------|----------|----------|----------|
| 职业分析 | GET landscape | `/api/career/landscape` | `backend.ts` 已接 | MySQL(+Redis 缓存) |
| AI 助手 | POST chat | `/api/agent/chat` | 已接，`mode=demo` | 本轮 demo 体（重型 LLM 排除） |
| 职途导航 | POST trigger / GET status / parse resume text | `/api/pipeline/*`、`/api/pipeline/ext/parse/resume` | 已接 | list/status 真库；trigger 在 DEMO_MODE=false 可能进真管线→慎测；parse/text 轻量可测 |
| 职业生涯报告 | generate / list / detail | `/api/report/*` | `api/report.ts` | list/detail 真库；generate=heavy-excluded |
| 我的报告 | list / detail | 同上 | **仍 mock** `MyReports.vue` | 后端有；前端未接 → gap |
| 个人能力画像 | GET student/4d、job portrait | `/api/profile/student/.../4d`、`/api/profile/job/...` | 部分接 | 真库有 `student_portrait_4d` |
| 心仪岗位 | saved-jobs CRUD、target-roles、role-market | `/api/saved-jobs*`、`/api/target-roles*`、`/api/target-role-market/*` | target-roles 已接；saved-jobs **TODO** | 真库表存在；**契约缺口**：前端 `addTargetRole` 发 `role`，后端要 `role_name` |
| 技能提升 | courses / progress | `/api/learning/courses*`、`/api/learning/learning/progress*` | store 部分接 | 真库有 course |
| 管理-岗位数据集 | dataset list/upload/delete | `/api/admin/dataset*` | 已接 | 真库 `job_dataset` |
| 管理-知识库 | kb list/upload/delete | `/api/admin/knowledge-base*` | 已接 | 真库 `knowledge_base` |

测试身份：`student_001`；岗位画像示例：`job_001`；报告示例：`report_001`；课程：`course_001`。

---

## 非主路径页面状态（仅标注）

| 页面 | 状态 | 说明 |
|------|------|------|
| 首页 HomeCenter | mock-only | 入口聚合，无独立后端契约 |
| 登录 | mock-only | 无 `/api/auth`（约定接受） |
| 能力图谱 career-ability | missing | composable TODO；无稳定对等 API |
| 课程体系 / 技能图谱 | missing | 前端图数据本地；后端无一一对应契约 |
| 技能自评 exams | missing / partial | 后端有 `pipeline/ext/exams`、career assessment，前端未接 |
| 消息 messages | missing | 无后端消息 API |
| 个人中心 profile | missing | 无用户资料 API |
| 简历编辑器 resume-builder | missing | 本地编辑；上传解析属重型 |
| 学习中心部分 | partial | courses API 有；章节视频等仍 mock |

---

## 实测命令

```powershell
$env:CPA_DEMO_MODE = 'false'
cd backend
python run.py
# 另开终端
python scripts/smoke_frontend_contracts.py
```

---

## 真库实测结果（2026-07-18）

`CPA_DEMO_MODE=false` + Docker MySQL/Redis/Neo4j：

| 结果 | 数量 |
|------|------|
| PASS | **22** |
| FAIL | 0 |
| SKIP（重型） | report.generate / pipeline.trigger 真跑 / agent langgraph |

明细见 `backend/scripts/smoke_frontend_contracts_result.json`。

### 已确认的契约/接线缺口（本轮不补，只记录）

1. ~~前端 `addTargetRole` 字段名~~：**已修复（2026-07-18）** — BE 兼容 `role`/`role_name`；FE 发 `role_name`；市场卡已补齐 FE 全字段（见 `docs/frontend/cpa-xingtu`）。
2. **MyReports**：后端 `report/list|detail` 已通，页面已接 dual client（仍可能混用本地 seed）。
3. **Favorites saved-jobs**：后端 CRUD 已通，前端已接；camelCase 别名已兼容。
4. **非主路径页**：能力图谱 / 课程体系 / 消息等 → `backend-gap`（见 `docs/frontend/cpa-xingtu`）。
