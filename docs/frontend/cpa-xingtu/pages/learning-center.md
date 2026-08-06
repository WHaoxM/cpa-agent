# 技能提升（学习中心）

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/student/learning` |
| name | `student-learning` |
| roles | student |
| immersive | false |
| public | false |

### 别名 / redirect

| path | 说明 |
|------|------|
| `/app/courses` | redirect → `/app/student/learning` |

## 页面职责

浏览课程、查看进度、按目标角色筛选推荐课。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 课程列表 | 进入页 | `GET /api/learning/courses?limit=20` | — | `{ data: { courses, count } }` | `learning/courses.list.json` | backend-ready |
| 课程详情 | 点进课程 | `GET /api/learning/courses/<course_id>` | — | course 行 | `learning/courses.course_001.json` | backend-ready |
| 读进度 | 进度条 | `GET /api/learning/learning/progress/student_001` | — | progress[] | `learning/progress.list.json` | backend-ready |
| 写进度 | 学完章节 | `POST /api/learning/learning/progress` | 见下 | message | `learning/progress.write.ok.json` | backend-ready |
| 推荐课 | 「为你推荐」 | `GET /api/learning/recommend?student_id=student_001` | query | courses+reason | `learning/recommend.json` | backend-ready |
| 章节视频流 | 播放器 | — | — | — | — | backend-gap |

### 写进度 Request

```json
{
  "user_id": "student_001",
  "course_id": "course_001",
  "chapter_id": "ch_1",
  "progress": 40,
  "completed": false
}
```

**Client 建议:** `api.learning.listCourses` / `getProgress` / `saveProgress` / `recommend`

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| 分类侧栏 | `mockCategories` / `careerCourses` |
| 笔记 UI | `mockNotes` |

## Demo 说明

- `student_001` / `course_001`；冒烟 courses + progress PASS

## 后续实现备注

- store 部分 TODO；`careerCourses.ts` 可作角色筛选本地增强
