# 职业能力图谱

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/student/career-ability` |
| name | `career-ability` |
| roles | student |
| immersive | true |
| public | false |

### 别名 / redirect

| path | 说明 |
|------|------|
| `/app/student/career-ability/graph` | → `career-ability`（保留 query） |
| `/app/student/career-ability/dual` | 同上 |
| `/app/student/career-ability/workspace` | 同上 |
| `/app/student/career-ability-graph` | 同上 |

## 页面职责

按目标角色展示能力图谱（节点/边）与角色简介；当前为本地图 + 假流式生成。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 加载能力图 | 进入/选角色 | `GET /api/ability-graph?role=` | `role=前端开发` | `{ nodes, edges }` | `ability/graph.json` | backend-gap |
| 角色简介 | 侧栏 | `GET /api/career/role-intro?role=` | — | intro 文案 | `ability/role-intro.frontend.json` | backend-ready |
| 可选：技能树兜底 | 降级 | `GET /api/career/skill-tree?role=` | — | skills/by_tier | `career/skill-tree.json` | backend-ready |
| 图生成流式 | 「生成」 | （理想 SSE） | — | 增量节点 | — | backend-gap |

### 理想 ability-graph Response（短样例）

```json
{
  "success": true,
  "data": {
    "role": "前端开发工程师",
    "nodes": [{ "id": "vue", "label": "Vue", "tier": 1 }],
    "edges": [{ "from": "js", "to": "vue" }]
  }
}
```

**Client 建议:** `api.ability.getGraph(role)`（mockAdapter 读 fixture）

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| Shell 布局 / 双栏 | `CareerAbilityShell.vue` |
| `useAbilityGraph` / `useGraphGeneration` | mock SSE |

## Demo 说明

- Pages 完全依赖 `ability/graph.json`；不调用不存在的后端

## 后续实现备注

- 产品契约先于后端；可用 skill-tree 作临时数据源（需映射）
