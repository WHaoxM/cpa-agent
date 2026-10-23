# 个人能力画像

## HTTP 结果首页：经历解读与具体改进

当前职途导航内嵌此组件，真实四维、未知灰色及原花形算法不变；历史花形仅提供材质、浮动和分栏参考，不恢复演示七维／排名。简历解析是前置过程，不是成果正文。

- 首屏以本人经历为主：简要资料、花形概览、当前诊断中带有效事实引用的经历解读。四维依据和待核对项可展开，不以未知数量替代个人解释；模型解读不等于能力认证。没有有效解读时展示已有项目，不能编造优势。
- `ResumeImprovementPanel` 共用在画像与独立简历诊断中，最多三条建议；展开显示原文、问题、补充价值、事实问题和待填写提纲。每个事实至多一条，不凑数量。缺页明确限定已读片段，未提及不等于没有能力；完整事实章节与岗位方向在诊断页折叠查阅。
- 普通 GET 不调用模型。旧报告缺少建议时为 `pending_upgrade`，提供显式生成入口；新建议的 `improvements_status` 与报告原状态分开，失败／预算不足不填通用清单。建议只能引用该报告支持的事实，服务端赋予稳定内容标识和目标模块。
- “去补充这段经历”先取得只读转换预览，再让本人决定初始化或继续既有草稿。提纲从不自动写入，后续流程归[简历制作页](resume-builder.md#从诊断承接本人草稿)。来源未核验、过期或账号切换不得继续使用旧响应。

原始点字矩阵波动仍未定位，不把本轮花形保留视为该问题恢复。实际截图、模型样本和链路结论统一补入[原页面修复报告](../../../reports/2026-10-22-real-page-experience-repair.md#花形画像与简历改进闭环)。下方演示接口只属于显式 Mock，不作为真实成果契约。

真实画像保留原花形与未知维度灰色，不补零分或填满图形。助手读取身份／当前版本的解释置于可展开“资料说明”，不与个人结论同层堆叠；诊断中的方向和经历为正文，依据可展开，内部摘要与记录引用不投影为学生正文。当前诊断响应没有物理／逻辑页码区分，阅读组件保守省略证据页码，不据逻辑 `page=1` 声称“第1页”；缺页警示仍保留。

## 移动端显示（2026-10-21）

- 沿用 768px 断点、原有单列纵向滚动及主题；独立页保留深色花形能力图，嵌入导航时保留组件既有配色，不改成新雷达卡片页。
- 手机花形绘图区为 340px，使用现有 `D3FlowerChart.zoom` 参数（手机 1.25）提高利用率，不改花形算法、维度或分值。真实成果的未知值/待核对状态及 Agent 产物专属图形不作演示填充。
- 分项说明至少 12px，经历及综合说明 14px，增加行高和分区间距；手机不再将项目描述裁成两行。原保存、匹配、打印和简历操作在底部两列排列，窄屏按钮文案换行，触控高度至少 44px；启用条件不变。
- 同时约束独立和内嵌容器宽度。桌面布局及普通/真实/产物的数据源、保存和导出合同不变；本轮没有修复另行记录的 Mock 报告列表保存问题。

独立与内嵌回放、前后截图和待真机验证项见[三页移动端修整记录](../../../reports/2026-10-21-mobile-career-chain-replay.md)。

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/student/career-portrait` |
| name | `student-career-portrait` |
| roles | student |
| immersive | true |
| public | false |

## 页面职责

展示学生四维（及子维）画像，可对照岗位画像；支持流式「解读」动效（本地或 agent）。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 加载学生 4D | 进入页面 | `GET /api/profile/student/student_001/4d` | 可选 `target_job_id` | 见下 | `profile/student.4d.student_001.json` | backend-ready |
| 加载岗位画像 | 选择对照岗位 | `GET /api/profile/job/job_001` | — | 见下 | `profile/job.job_001.json` | field-mismatch：仅已发布快照，旧四维 fixture 字段不保证 |
| 岗位列表 | 下拉选择 | `GET /api/profile/job/list` | `limit` | 列表 | `profile/job.list.json` | field-mismatch：仅已发布摘要，真实目录当前可能为空 |
| 流式人设解读 | 「生成解读」 | `POST /api/agent/chat` | `mode:demo` | reply | `agent/chat.demo.json` | backend-ready |
| 真 LLM 流式 | 同左 | `POST /api/agent/chat/stream` | — | SSE | — | heavy-excluded |

### Request / Response 展开

#### 加载学生 4D

**Request**

```http
GET /api/profile/student/student_001/4d?target_job_id=job_001
```

**Response**

```json
{
  "success": true,
  "data": {
    "student_id": "student_001",
    "completeness": 95.0,
    "dimensions": {
      "D1": { "name": "基础要求", "score": 85.0, "description": "学历、学校、专业等基础条件" },
      "D2": { "name": "职业技能", "score": 80.0, "description": "技术技能匹配程度" },
      "D3": { "name": "项目经历", "score": 78.0, "description": "项目与实践深度" },
      "D4": { "name": "综合素质", "score": 82.0, "description": "软技能与潜力" }
    },
    "sub_dimensions": {
      "skill": { "score": 80.0 },
      "cert": { "score": 70.0 }
    },
    "total_score": 81.0,
    "skill_coverage": 0.72,
    "skill_count": 18
  }
}
```

**Fixture:** `cpa-xingtu/public/fixtures/profile/student.4d.student_001.json`  
**Client 建议:** `api.profile.getStudent4d(studentId, { targetJobId })`

#### 加载岗位画像

**Request**

```http
GET /api/profile/job/job_001
```

**Response（节选）**

```json
{
  "success": true,
  "data": {
    "portrait_id": "job_001",
    "title": "前端开发工程师",
    "d1_weight": 0.25,
    "d2_weight": 0.35,
    "d3_weight": 0.2,
    "d4_weight": 0.2,
    "portrait_json": {}
  }
}
```

**Fixture:** `cpa-xingtu/public/fixtures/profile/job.job_001.json`  
**Client 建议:** `api.profile.getJobPortrait(portraitId)`

**HTTP 模式注意：**上面的 `title`、`d1_weight` 等是旧 mock fixture，不是现行后端公开发布合同。真实接口只接受当前有效发布的岗位 ID，返回来源审核后的快照；未发布或已撤回的岗位为 404。若页面仍需四维权重对照，需要先建立经审核的权重来源与明确的适配契约，不得从未发布旧画像直接回填。

#### 解读（demo chat）

**Request**

```json
{
  "message": "请根据我的四维画像给出解读摘要",
  "student_id": "student_001",
  "mode": "demo"
}
```

**Response**

```json
{
  "success": true,
  "data": {
    "engine": "demo",
    "reply": "演示模式已接收你的问题。当前可展示职业探索、能力画像、人岗匹配和学习路径规划链路；接入 LLM_API_KEY 后可切换到 langgraph 模式。",
    "session_id": "demo_session_001",
    "elapsed_seconds": 0,
    "tool_calls": [],
    "active_subagent": "demo_subagent"
  }
}
```

**Fixture:** `cpa-xingtu/public/fixtures/agent/chat.demo.json`

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| 雷达图动画 | 前端 |
| `useAgentPortrait` 假流式 | 可保留为 UI 特效，数据源换 API |
| 七维展示 | 现有 `careerPortraits` mock；与后端 4D 并存时 adapter 映射 |

## Demo 说明

- `student_001` + `job_001`；冒烟 `profile.student.4d` / `profile.job` PASS

## 后续实现备注

- `src/mock/careerPortraits.ts`：岗位七维目录；与 BE `portrait_json` 对齐需单独映射表
- 前端七维 vs 后端四维：产品层以 4D API 为准，七维可标为展示增强（本地）
