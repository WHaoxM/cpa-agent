# 课程体系 / 技能图谱

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/student/course-system` |
| name | `course-system` |
| roles | student |
| immersive | true |
| public | false |

### 别名 / redirect

| path | 说明 |
|------|------|
| `/app/student/skill-graph` | **同组件** `CourseSystemGraph.vue`；`keepTopNav: true`；title「技能图谱」 |

## 页面职责

可视化课程/技能依赖图；两路由共用视图，meta 标题不同。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 加载图数据 | 进入页 | （理想）`GET /api/learning/course-system-graph` | `variant=course\|skill` | nodes/edges | `learning/course-system.graph.json` | backend-gap |
| 课程详情跳转 | 点击节点 | `GET /api/learning/courses/<id>` | — | 见 learning-center | `learning/courses.course_001.json` | backend-ready |
| 过滤层级 | UI 控件 | — | — | — | — | —（本地） |

### 理想图 Fixture 短样例

```json
{
  "success": true,
  "data": {
    "variant": "course",
    "nodes": [{ "id": "course_001", "label": "Vue 3 入门", "level": 1 }],
    "edges": []
  }
}
```

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| `useCourseSystem` | 本地构图 |
| skill vs course 标题 | 路由 meta |

## Demo 说明

- 无后端对等契约；Pages 用本地图 fixture

## 后续实现备注

- 不强制对齐 Neo4j 导出，除非后续 ADR 定图谱 API
