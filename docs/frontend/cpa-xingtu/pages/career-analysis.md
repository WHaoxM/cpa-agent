# 职业分析

## 当前真实模式：方向探索与一周尝试（2026-10-22）

维护者从这里接续真实页面。用户复审后已纠正整页替换：**默认进入原岗位舆图，新旅程只能通过明确入口进入**。下文历史地区、薪资样例不能作为市场可信的证明。

- `/app/student/career-analysis` 默认使用原 `CareerAnalysis.vue` 的气泡、地图卷轴、概览卡和侧栏；保留 D3 动效、键盘、搜索和地图选区。真实目录从既有 `/api/career/landscape` 及 `/api/career/insights/<role>` 读取，按实际分类每页五组，不把真实岗位改名成五个预设方向；损坏标签不重新编码或用演示补齐。真实气泡的悬停说明也不运行旧薪资推算。
- 原市场组件仍保留，但真实模式未有可信统计时，需求、薪资、地区排行显示未就绪，地图只表示用户选定的地区范围，不显示假热力、分数或“AI 市场洞察”。原 Mock 图表及 `artifact_id` 指标阅读保留原职责；artifact 查询参数优先于新旅程。
- 主动点击“探索与实践”或“把这个方向变成一次尝试”，以 `view=exploration` 进入 `CareerExplorationWorkspace`；已有 `exploration_id` 链接继续直接恢复。返回岗位舆图保留入口的 role 查询参数，不依赖会被 `route.fullPath` 重建丢失的内存变量。正常环境新接口未启用时，新入口显示中文不可用并可返回，不挡住原舆图，也不尝试提交新记录。
- 新旅程沿用原纸纹、标题字体、配色和分栏；`careerAnalysis.css` 仍由两个内容层共同引用。它是原分析后的实践延伸，不是“HTTP 新版 / Mock 旧版”两套默认职业分析。上一轮仅保留样式就宣称旧功能保留的判断，已在[原验证报告补记](../../../reports/2026-10-22-career-exploration-practice-replay.md#用户复审纠偏恢复原职业分析默认入口)纠正。
- 学生先比较至多两个目录方向，再亲自填写理由、验证问题和本周时间，确认一轮选择；生成计划是另一个明确操作。方向来自服务端 `career_insights` 结构字段，最多读取 60 条，部分不可用明确提示。每页十个气泡，支持搜索、翻页、完整无障碍标题、Enter/Space 和减少动态效果。大小和位置仅为布局，不表示适合程度或市场规模；真实模式不显示无可信依据的薪资、需求、地区图。
- 左栏区分本人确认简历、另行自述与技能自评；未知不补零。新资料由学生明确核对更新；旧轮次资料不被重写。比较区只列目录技能、与简历精确同名的线索及尚待了解项，不输出匹配分或胜任判断。
- 计划使用现有学习中心成果物详情：进度、文字和附件仍在原计划中保存；“返回方向探索”带回同一探索，不要求翻聊天记录。页面内直接阅读复盘并提交本人的继续／调整／换方向理由；模型不能保存决定。显式下一轮保留旧选择、计划、成果、复盘和决定。
- 未生成计划的选择可以明确“调整选择重新准备”：保留为未开始历史，不伪造成实践决定；资料过期时先刷新再确认新轮次。已生成计划不能用此操作丢弃练习。
- 保存失败保留本页文字，未知结果先重新读取核对；CAS 冲突不能静默覆盖另一页。账号变化／卸载后旧请求无权恢复内容。重新登录读取 MySQL，而非共享浏览器缓存。记录选择器当前展示本人最近二十份探索；单份探索保留其全部轮次，不宣称跨探索无限分页。
- 桌面保留独立分栏滚动；850px 以下统一为“当前阶段正文 → 本人资料及轮次 → 操作表单”的纵向阅读。长复盘不压进小卡片；学生自述原文、待核对的模型解读、未知及下一步分别分节。附件实际读取状态在独立展开项中，不把勾选当成果认证。

### 真实接口与持久化

集合 `GET/POST /api/career/explorations`，详情 `GET/PATCH /<id>`；轮次 `POST /<id>/rounds`，轮次操作 `POST /<id>/rounds/<round>/plan|review|restart`、`PUT /<id>/rounds/<round>/decision`，`POST /<id>/session` 连接原助手会话。owner 仅来自 Principal，草稿、复盘、决定和重新准备带 `expected_revision`，冲突为 409；计划重试返回同一成果。

数据库需 Alembic `0016_career_exploration`（前置 `0015_self_reflection`）。本轮不自动升级正常开发库。未迁移环境显示真实错误，不回退旧演示。计划来源与附件边界见[计划持久化约定](../../../guides/agent-plan-persistence.md#职业方向尝试来源2026-10-22)。

实际命令、失败与修复、同视口截图、真实模型输入及清理从[本轮回放记录](../../../reports/2026-10-22-career-exploration-practice-replay.md)进入。方向建议质量、市场准确性和真机移动不由链路通过代验。

## 原演示／成果物入口的响应式行为（2026-10-21）

- `CareerAnalysis.vue` 在 **767px 及以下**使用两行页头和一个主体纵向滚动区；内容依次为方向概览、全部岗位气泡、地区 TOP 10、地图、选省后的薪资图、洞察及后续操作。768px 及以上沿用既有分栏，不借此轮改排平板与桌面。
- 手机绘图保留五个领域与嵌套岗位，不新增分组切换器：560px SVG、两列三行初始化、岗位半径 24px、12px 短标签；完整岗位名保留在无障碍名称、SVG title 和选中概览。点击或 Enter/Space 仍调用原选岗逻辑。
- 两层 D3 simulation 沿用原碰撞机制；手机完成布局后停住，避免触控目标漂移。桌面开启 `prefers-reduced-motion` 时也停止持续漂移；现有 ResizeObserver 重绘前清理旧 simulation，尺寸变化不清空选中岗位。
- 地区排行恢复展示并复用原选省条件；地图、薪资各自有 360px 绘图高度。洞察正文不再另套小滚动区；信息说明支持点击展开和关闭，AI 抽屉不超出视口。
- 本轮仅改布局与交互可达性，不更改演示统计、评分和 Agent 产物加载/错误守卫。下方历史接口表不代表本轮已验真实后端。

实际浏览器回放、截图和未覆盖范围见[三页移动端修整记录](../../../reports/2026-10-21-mobile-career-chain-replay.md)。

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/student/career-analysis` |
| name | `student-career-analysis` |
| roles | student |
| immersive | true |
| public | false |

## 页面职责

展示职业赛道全景、洞察与探索搜索，支撑方向选择。

## 原目录接口说明（非当前真实旅程接线表）

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 加载全景 | 进入页面 | `GET /api/career/landscape` | — | 见下 | `career/landscape.json` | backend-ready |
| 加载洞察列表 | 筛选/分页 | `GET /api/career/insights` | 见下 | 见下 | `career/insights.list.json` | backend-ready |
| 打开角色洞察 | 点击角色 | `GET /api/career/insights/<role_name>` | path 编码 | 单行洞察 | `career/insights.detail.json` | backend-ready |
| 关键词探索 | 搜索框 | `GET /api/career/explore?q=&top_k=` | 见下 | fuzzy/semantic | `career/explore.json` | backend-ready |
| 技能树（可选） | 进阶面板 | `GET /api/career/skill-tree` | query | skills | `career/skill-tree.json` | backend-ready |

### Request / Response 展开

#### 加载全景

**Request**

```http
GET /api/career/landscape
```

**Response**

```json
{
  "success": true,
  "count": 15,
  "data": [
    {
      "category": "AI算法",
      "role_count": 6,
      "avg_salary_range": "19K-38K",
      "roles": [
        "计算机视觉工程师",
        "NLP工程师",
        "多模态算法工程师",
        "算法工程师",
        "大模型算法工程师",
        "强化学习工程师"
      ]
    }
  ]
}
```

**Fixture:** `cpa-xingtu/public/fixtures/career/landscape.json`  
**Client 建议:** `api.career.getLandscape()`

#### 加载洞察列表

**Request**

```http
GET /api/career/insights?limit=5&offset=0
```

**Response**

```json
{
  "success": true,
  "count": 5,
  "data": [
    {
      "role_name": "算法工程师",
      "category": "AI",
      "avg_salary_low": 18,
      "avg_salary_high": 32,
      "demand_level": "high",
      "core_skills": ["Python", "机器学习", "PyTorch"]
    }
  ],
  "total": 100,
  "limit": 5,
  "offset": 0
}
```

**Fixture:** `cpa-xingtu/public/fixtures/career/insights.list.json`  
**Client 建议:** `api.career.listInsights(params)`

#### 关键词探索

**Request**

```http
GET /api/career/explore?q=%E5%89%8D%E7%AB%AF&top_k=5
```

**Response**

```json
{
  "success": true,
  "data": {
    "fuzzy_matches": [{ "role_name": "前端开发工程师", "score": 0.92 }],
    "semantic_matches": []
  }
}
```

**Fixture:** `cpa-xingtu/public/fixtures/career/explore.json`  
**Client 建议:** `api.career.explore({ q, topK })`

## 原页内探索陪伴（2026-10-22）

原岗位舆图仍是默认入口，不由 HTTP 模式替换整页。探索区继续使用原气泡、纸张与分栏：选择一至两个方向后，学生主动点击“帮我一起看看”才启动分析；选择气泡本身只改变比较范围。原先要求先填理由、验证问题和时间的表单不再作为讨论入口。

讨论使用 `POST /api/career/explorations/<id>/analysis-turns`，带预期探索版本及 `Idempotency-Key`；服务端绑定资料和当前轮次。页面按 `/api/agent/runs/<id>/events?after_seq=...` 读取真实队列、步骤和资料读取事件，不使用旧聊天 SSE 回放模拟实时过程，不显示假百分比。详情回读恢复消息、澄清问题和待确认方案；普通刷新不清空当前输入，账号／探索／轮次切换才撤销迟到响应权限。

正文、方向比较、一个必要的澄清问题和依据组件分别呈现。待确认方案可修改问题与时间，但须先让助手更新方案；最后填写本人简短理由并确认。确认通过现有 rounds 入口提交 `proposal_id`，服务端保存同一份步骤，不再次调用模型。旧计划只读及学习中心成果入口保持兼容。讨论和 AI 复盘不等于最终决定，也不更改简历或画像。

新能力要求 additive `0018_exploration_analysis` 和消费 `cpa.agent` 的 worker。未迁移时旧探索仍可读，讨论显示暂未接通；本轮不自动升级共享库。实际执行状态、截图及尚未验收部分统一见[原探索回放报告](../../../reports/2026-10-22-career-exploration-practice-replay.md)，不以本节功能说明当作通过证据。

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| 图表配色 / 布局 | 前端 |
| 选中 category 高亮 | 本地 state |

## Demo 说明

- 不强制 `student_id`；展示数据与用户无关时可直接用 landscape fixture
- 冒烟：`career.landscape` / `career.insights` PASS

## 历史实现备注（真实旅程不再采用此接线方式）

- 现有：`useCareerInsights` 等 mock 图数据
- 接线后替换为 catalog 上述四读接口
