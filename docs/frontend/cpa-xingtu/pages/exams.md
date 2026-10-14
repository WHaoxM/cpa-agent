# 技能自评

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/exams` |
| name | `exams` |
| roles | 需登录 |
| immersive | false |
| public | false |

## 页面职责

答题自评技能掌握度，提交后得到弱项与推荐方向。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 加载题库 | 进入页 | （理想）本地或 `GET` 题包 | — | questions[] | `learning/exams.questions.json` | backend-gap |
| 提交答卷 | 交卷 | `POST /api/pipeline/ext/exams` | 见下 | assessment | `pipeline/exams.create.json` | backend-ready |
| 查结果 | 历史 | `GET /api/pipeline/ext/exams/<assessment_id>` | — | 详情 | `pipeline/exams.detail.json` | backend-ready |
| 当前页面提交 | 交卷 | `POST /api/career/assessment` | `assessed_skills: string[]` + `assessed_abilities: Record<string,0..100>`；后端按差距提示保存，不推断缺失等级 | `gap_only/partial`、weak | `career/assessment.create.json` | backend-ready（需正式登录） |
| 最新自评 | 摘要 | `GET /api/career/assessment/student_001` | — | 最新行 | `career/assessment.latest.json` | backend-ready |

### 提交答卷 Request（pipeline/ext）

```json
{
  "student_id": "student_001",
  "target_role": "前端开发工程师",
  "answers": [
    { "question_id": "q1", "choice": "A" }
  ]
}
```

**Client 建议:** `api.exams.submit(answers)` → 优先 `pipeline/ext/exams`；题库仍 mock

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| `mockQuestions` / 错题本 | `data.ts` |
| 计时器 UI | 前端 |

## Demo 说明

- 题库 fixture + mock 提交可演示；当前 HTTP 提交的 `assessed_skills` 形状与后端不一致，尚不能按真实自评完成验收。

## 后续实现备注

- 前端未接线；两套后端自评 API 并存，产品选一主路径即可
