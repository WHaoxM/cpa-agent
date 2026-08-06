# cpa-xingtu 前端设计文档

Status: active  
Last updated: 2026-07-18  
Scope: 设计文档 + fixtures / dual client + **主路径页面已接线**（含 backend-gap 的 fixture 回退）。

面向 `cpa-xingtu` 的前端产品契约：全路由入册、理想 FE API、操作级样例、Mock/Pages 约定。

## Grill 摘要（已决）

| 项 | 决定 |
|----|------|
| 页面清单 | 以 [`cpa-xingtu/src/router/routes.ts`](../../../cpa-xingtu/src/router/routes.ts) 全量为准；redirect 合并进目标页 |
| Mock 形态 | 静态 JSON fixtures + 薄 loader；Git 可管，供 GitHub Pages demo |
| 运行时切换 | 统一 client：`mockAdapter` / `httpAdapter`（本轮只写约定） |
| 契约权威 | **理想 FE 产品契约**；每条 API 标四态（见 [status-legend](contracts/status-legend.md)） |
| 文档深度 | 每操作：UI → method/path → req/res → fixture 路径 → BE 状态 |
| 身份 | Demo 固定 `student_001`（登录仍 mock，无 `/api/auth`） |

## 阅读顺序

1. 本 README（索引与 P0）
2. [contracts/status-legend.md](contracts/status-legend.md) — 四态标签
3. [contracts/api-catalog.md](contracts/api-catalog.md) — 全 API 目录
4. [contracts/ai-assistant-materials.md](contracts/ai-assistant-materials.md) — AI 助手物料与推荐契约
5. [mock/README.md](mock/README.md) — fixtures / adapter 约定
6. [pages/](pages/) — 按页面操作表落地

复制新页时用 [_page-template.md](_page-template.md)。

## 目录树

```text
docs/frontend/cpa-xingtu/
  README.md
  _page-template.md
  mock/README.md
  contracts/
    api-catalog.md
    status-legend.md
  pages/
    landing.md … not-found.md
```

约定 fixture 根路径（后续实现）：

`cpa-xingtu/public/fixtures/{domain}/{name}.json`

## P0 页面（满样例）

| 文档 | 路由 name | path |
|------|-----------|------|
| [login.md](pages/login.md) | login | `/login` |
| [home-center.md](pages/home-center.md) | dashboard | `/app/dashboard` |
| [career-navigation.md](pages/career-navigation.md) | student-career-navigation | `/app/student/career-navigation` |
| [career-analysis.md](pages/career-analysis.md) | student-career-analysis | `/app/student/career-analysis` |
| [talent-portrait.md](pages/talent-portrait.md) | student-career-portrait | `/app/student/career-portrait` |
| [favorites.md](pages/favorites.md) | student-favorites | `/app/student/favorites` |
| [career-report.md](pages/career-report.md) | student-career-report | `/app/student/career-report` |
| [my-reports.md](pages/my-reports.md) | student-my-reports | `/app/student/my-reports` |
| [ai-assistant.md](pages/ai-assistant.md) | student-ai-assistant | `/app/student/ai-assistant` |
| [admin-job-dataset.md](pages/admin-job-dataset.md) | admin-job-dataset | `/app/admin/job-dataset` |
| [admin-knowledge-base.md](pages/admin-knowledge-base.md) | admin-knowledge-base | `/app/admin/knowledge-base` |

## 非 P0（同模板，response 可略缩）

| 文档 | 路由 name |
|------|-----------|
| [learning-center.md](pages/learning-center.md) | student-learning（含 `/app/courses` redirect） |
| [career-ability.md](pages/career-ability.md) | career-ability + 旧 graph/dual/workspace redirect |
| [course-system.md](pages/course-system.md) | course-system + skill-graph |
| [exams.md](pages/exams.md) | exams |
| [messages.md](pages/messages.md) | messages |
| [profile.md](pages/profile.md) | profile |
| [resume-builder.md](pages/resume-builder.md) | student-resume-builder |
| [landing.md](pages/landing.md) | landing |
| [not-found.md](pages/not-found.md) | not-found |

Redirect 不单独成文：`student/career` → dashboard；`courses` → learning；ability 子路径 → career-ability。

## 与旧 matrix 的关系

[`docs/specs/frontend-api-contract-matrix.md`](../../specs/frontend-api-contract-matrix.md) 保留为 **真库冒烟结果与缺口快照**（2026-07-18，22/22 PASS）。

本目录以 **cpa-xingtu 路由/产品视角** 重写理想契约；后端状态标签复用并细化 matrix 结论。若两者冲突，以本目录 + 当前后端路由为准，并回写 matrix 备注（不在本轮改 matrix）。

注意：matrix 曾写「`backend.ts` 已接」——仓库中 **无** `cpa-xingtu/src/api/backend.ts`；多数页面仍为 mock。文档状态按「理想契约 + 后端可用性」标注，不假装已接线。

## 当前实现对照（只读）

| 层 | 现状 |
|----|------|
| 前端 API | `src/api/report.ts`、`aiAnalysis.ts` 多为 stub；页面/store TODO |
| Mock | `src/mock/*.ts`（后续迁移到 `public/fixtures/`） |
| 后端冒烟 | `backend/scripts/smoke_frontend_contracts.py` |
