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

按目标角色展示能力图谱与角色简介。默认以四类分组气泡总览查看技能分布；可切换到 G6 关系网络查看所有技能及其实际关系。分组位置固定，技能气泡可在所属分组内拖动；气泡半径只服务于标签排布，不代表热度、匹配度或掌握度。

## 图谱交互

| 视图 | 表达职责 | 操作 |
|------|----------|------|
| 气泡总览 | 展示四类能力分组和技能分布 | 技能可在固定分组区域内拖动；桌面空白处拖动平移、滚轮缩放；触屏可拖技能并用双指平移/缩放；键盘焦点后按 Enter/Space 查看详情 |
| 关系网络 | 展示全部技能及前置、协同、依赖关系 | G6 力导向布局按技能类别聚拢；节点可拖动，画布可平移/缩放；默认只画关系线，关系说明按需打开 |

两种视图共享搜索、技能选中状态和详情面板。聚焦技能时只突出它的直接关系；切换视图和调整窗口会保留当前摆放，重置视图会恢复默认布局。关系网络不画岗位/板块归属线，类别由聚拢布局与节点颜色表达。

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
