# Mock / Fixtures / Adapter 约定

Status: active  
Last updated: 2026-07-18  
Fixtures + dual client：**已落地**（见 `cpa-xingtu/public/fixtures/`、`cpa-xingtu/src/api/`）。

## 目标

GitHub Pages demo 与本地开发共用同一套静态 fixtures；切 `http` 时走真实后端，无需改页面。

## Fixture 根路径

推荐（Pages 可直接 `fetch`）：

```text
cpa-xingtu/public/fixtures/{domain}/{name}.json
```

备选（需打包进 bundle）：`cpa-xingtu/src/fixtures/` —— **本项目选定 public**。

### Domain 建议

| domain | 用途 |
|--------|------|
| `auth` | demo 用户列表（无后端 auth） |
| `career` | landscape / insights |
| `profile` | student 4d / job portrait |
| `favorites` | saved-jobs / target-roles / role-market |
| `report` | list / detail |
| `agent` | chat demo reply |
| `learning` | courses / progress |
| `admin` | dataset / knowledge-base |
| `pipeline` | parse/text、status 样例 |
| `home` | 首页聚合、消息列表 |
| `ability` | 能力图谱 / 角色简介（backend-gap） |
| `match` | result / explain / skill-gap / hybrid-candidates |

### 命名

- 读：`{resource}.list.json`、`{resource}.detail.{id}.json`
- 写成功：`{resource}.create.ok.json`、`{resource}.delete.ok.json`
- 错误样例（可选）：`{resource}.error.{code}.json`

示例：

```text
public/fixtures/favorites/saved-jobs.list.json
public/fixtures/report/list.student_001.json
public/fixtures/agent/chat.demo.json
```

## JSON 形状

与理想 FE 契约一致时：

1. **优先对齐后端 envelope**：`{ "success": true, "data": ... }`（及后端已有的 `count` / `message`）。
2. 若页面类型为 camelCase，由 **httpAdapter 映射**，fixtures 存 **snake_case 后端形**（避免双份真相）；mockAdapter 可在 loader 内做同一映射。
3. 文件顶层即响应 body（不含 HTTP 状态码）。

最小 schema 约定：

```json
{
  "success": true,
  "data": null
}
```

列表可带 `count`：

```json
{
  "success": true,
  "count": 1,
  "data": []
}
```

## Demo 身份

| 键 | 值 |
|----|-----|
| `User.id` / `student_id` / `user_id` | `student_001` |
| 登录（mock） | 用户名 `student001`，密码 `123456` |
| 展示名 | 钟同学（见现有 `src/mock/data.ts`） |
| 样例岗位 / 报告 / 课程 | `job_001` / `report_001` / `course_001` |

Admin demo 账号沿用现有 `mockUsers` 中 admin 条目（fixtures `auth/users.json` 落地时一并迁出）。

### 统一 client（已实现）

```ts
// src/api/config.ts + adapters + 域模块
import { getApiMode } from '@/api/config'
import { listSavedJobs } from '@/api/favorites'

const jobs = await listSavedJobs('student_001')
// mock → fetch BASE_URL/fixtures/favorites/saved-jobs.list.json
// http → GET {VITE_API_BASE_URL}/api/saved-jobs?user_id=
```

入口：`src/api/index.ts`（`authApi` / `favoritesApi` / `reportApi` / …）。  
`target-roles` 写入统一发 **`role_name`**。

环境变量：

```bash
VITE_API_MODE=mock   # 默认 Pages / 无后端
VITE_API_MODE=http   # 本地联调

# http 推荐：BASE 留空，走 Vite proxy；端口只改 PROXY_TARGET
VITE_API_BASE_URL=
VITE_API_PROXY_TARGET=http://127.0.0.1:5001

# 或直连任意 origin（不经 proxy）
# VITE_API_BASE_URL=http://192.168.1.10:8080
```

`mockAdapter`：`fetch(`${import.meta.env.BASE_URL}fixtures/...`)`。  
`httpAdapter`：`BASE` 为空时请求同源 `/api/...`；否则直连 `VITE_API_BASE_URL`。

## 与现有 `src/mock/*.ts` 的关系

| 现有文件 | 迁移方向 |
|----------|----------|
| `data.ts` | 拆到 auth / learning / favorites / report / agent fixtures |
| `careerPortraits.ts` / `careerLines.ts` / `careerReportData.ts` | profile / report / 图谱本地数据；能后端化的标 backend-gap |
| `careerCourses.ts` | learning fixtures 或继续本地图数据 |
| `aiAnalysis.ts` | agent / 本地分析；无后端则 backend-gap |
| `resumeSeed.ts` | pipeline parse 样例 + localStorage seed 说明 |

迁移完成前，页文档「后续实现备注」指向现有 TS；fixtures 路径仍按本 README 预留。

## Pages 注意

- `BASE_URL` 可能是子路径；fixtures 必须相对 `import.meta.env.BASE_URL`。
- 勿把密钥写入 fixtures；demo 密码仅本地 mock 身份。
