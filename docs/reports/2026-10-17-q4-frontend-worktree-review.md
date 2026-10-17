# Q4 独立前端仓分流与 Mock 岗位介绍回放（2026-10-17）

**范围：**`cpa-xingtu/` 是独立 Git 仓。审查基线为该仓 `main` 的 `1e567d2`，当时有 13 个未跟踪文件、无已跟踪修改；主仓不跟踪其源码。以下按文件实际内容与当前页面/构建结果分流，不推断文件作者，也不把历史设计稿当作现行产品指令。该仓本地没有 `AGENTS.md`，未擅自补写。

## 13 个未跟踪文件的独立处置

| 组别 | 文件与现场证据 | 结论 |
|---|---|---|
| 本机编辑器状态（1） | `.vscode/extensions.json` 仅推荐 Vue Volar。 | 非运行依赖；待仓库维护者决定是否分发，当前保留未跟踪。 |
| 协作历史（1） | `.workbuddy/memory/2026-08-10.md` 是多轮设计/实现回顾，不是可验证的现行页面或协作规则。 | 本机保留，不提交推送；不从中自动采用设计决策。 |
| G6 调试截图（6） | 根目录 `career-g6-{concentric-board,drag-stable,drag,search-focus,slow-drag,true-drag}.png` 均为 1600×900 的职业能力图谱交互画面，当前已跟踪文档/源码没有引用；画面含演示身份与运行日志。 | 作为本机调试证据保留，未核验发布授权与脱敏边界前不提交推送，也不当作当前页面验收截图。 |
| 设计与原型（3） | `design/DESIGN_AUDIT.md`、`design/REVIEW_LIVE.md`、`design/redesign-prototype.html` 是 8 月评审/暖色 v2 原型；其中暗色方向的评审语句与暖色原型彼此不一致。当前 `LandingView.vue` 和实际 Mock 落地页是深色星空并使用 `CareerStarMap`，不能直接照搬任一旧稿。 | 保留原件待前端设计工作流确认归属、适用范围和新版本，不混入本次窄域修复。主仓 `.ulpi/design/` 两份另属未跟踪候选，不计入这 13 件。 |
| 可运行脚本（2） | `tools/test-pages-spa-redirect.mjs` 对已跟踪 `public/404.html`/`index.html` 的 SPA 回跳执行 **2 passed**，但没有被 `package.json`/CI 接线；`tools/check-ability-intro.mjs` 已被已跟踪 `package.json` 的 `check:ability-intro` 引用，却未跟踪，直接运行因“自动化测试工程师、商业数据分析师、大模型应用工程师”三项本地介绍为空而退出 1。 | 前者是待认领回归候选；后者是**新克隆缺脚本且本机检查为红**的独立问题，须先明确这三类岗位介绍的正确内容/覆盖合同，再决定修脚本、补准确数据或改包入口；不能为了绿灯复用无关岗位文案。两文件均保留未提交。 |

## 一条从输入到页面的窄域缺陷与修复

`npm run build` 在 `main` 基线与修正后均成功（包含 `vue-tsc`）；构建仍提示 `lottie-web` 的 `eval` 与大 chunk，均不等于本缺陷。用 `npx vite --mode demo --host 127.0.0.1 --port 5175 --strictPort` 启动本机 Mock 页面后，在浏览器实际导入现行模块调用 `getRoleIntroAsync`：修正前 `SRE工程师` 的同步 getter 返回本岗位空壳，但异步 getter 返回“前端开发工程师”的完整文案；`商业数据分析师` 也得到同一前端文案。原因是 `src/api/ability.ts` 的 `roleKey` 默认值为 `frontend`，缺少非前端 fixture 时又回退到 `role-intro.frontend.json`。页面的双栏/工作台组件会消费该异步结果，这不是单纯测试脚本的错误。

在独立分支 `codex/q4-role-intro-mock-boundary` 中，新增先红的 `tools/test-role-intro-mock.mjs`（对真实 `ability.ts` 捆绑运行，前端 fixture 为当前仓文件）；修正只针对 Mock 岗位介绍：未知岗位不能因图谱的默认 key 读取前端介绍，缺失所属 fixture 时返回 `null`，由现有 `getRoleIntroAsync` 使用该岗位本地介绍或诚实空壳。HTTP 分支和图谱路径未改。回归转绿：`npm run test:role-intro-mock` **1 passed**，`npm run build` 成功。浏览器重新加载后，以演示学生进入 `/app/student/career-ability?role=SRE工程师`，实际页面出现“该岗位暂无岗位介绍数据”；改为 `Vue 前端工程师`，页面仍显示相应前端摘要。修复已在**前端仓自己的分支**以 `da7a3fa` 提交并推送，未混入主仓或 13 个未跟踪文件；前端 `main` 未改。

**剩余边界：**上述回放是 Mock 页面，不是后端 HTTP/正式账号验收。`check:ability-intro` 的三项缺口仍为红；`fetchAbilityGraph` 有不同的 fixture fallback 路径，本轮未宣称它正确或错误，若要修改须单独复现用户可见结果。设计稿取舍、截图发布和本机协作历史归属也仍待对应维护者确认。本轮不删除这些文件。
