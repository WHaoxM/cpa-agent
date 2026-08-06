# 代码可读性与可维护性待办 — 未实施文档

> **状态**: admin.py 批次已完成 (Completed); 分层改造试点进行中 (Layering Pilot In Progress)
> **记录日期**: 2026-07-15
> **触发**: 用户发起 `backend/app/api/admin.py` 代码可读性/可维护性审查与修复
> **范围**: 首批聚焦 `backend/app/api/` 下路由文件；后续可扩展至其他模块
> **原则**: KISS + 单一职责 + 关注点分离（参考 `python-design-patterns` skill）

---

## 变更日志

| 日期 | 版本 | 变更内容 |
|------|------|---------|
| 2026-07-15 | v1.0 | 初始创建，录入 `admin.py` 13 项问题，按 P0-P3 分级 |
| 2026-07-15 | v1.1 | admin.py 全部 13 项完成：P0(2)+P1(4)+P2(3)+P3(4)；新增 `_helpers.py` 的 `api_handler`/`safe_bad_request`/`safe_not_found`/`_iso` 4 个 helper；Q07/Q11 评估不实施（KISS） |
| 2026-07-15 | v1.2 | 扩展覆盖 `favorites.py` / `agent.py` / `career.py`，新增 Q14-Q33 共 20 项，含跨文件共性问题 |
| 2026-07-15 | v1.3 | 扩展覆盖 `graph.py` / `learning.py` / `match.py` / `profile.py` / `report.py`，新增 Q34-Q47 共 14 项，含 2 个 P0 潜在 bug |
| 2026-07-15 | v1.4 | Q14-Q33 全部完成：P1 批次2(Q14-Q18,Q20,Q21) + P2 批次2(Q22评估不实施,Q23-Q29) + P3 批次2(Q30-Q33)；agent.py 新增 `_parse_sse_content`/`_compute_tool_stats` 2 helper + `_MAX_TB_LEN`/`_DEMO_REPLY` 2 常量；career.py 新增 `_cache_key`/`_parse_json_fields`/`_query_promotion_chains`/`_mysql_fuzzy_match`/`_milvus_semantic_match` 5 helper；Q22 评估不实施(同 Q07 KISS) |
| 2026-07-15 | v2.0 | **架构分层改造启动**：用户提出 API 层不应直接干数据库的事；引入 Repository 层；新建 `app/repositories/` 目录 + `favorites_repo.py` 试点；favorites.py 9 处 SQL 全部下沉到 repository，API 层 0 处 DB 调用；148 路由不变。后续按 Q48 推进其他文件 |
| 2026-08-06 | v2.1 | **P0 收尾**：Q34 `graph.py:318` `n.labels` 补括号 + 空标签兜底（`labels[0] if labels else 'Unknown'`）完成；Q35 `profile.py` SELECT 补 `profile_json` 列（schema `student.py:19` 要求返回，之前恒 None 为死代码）完成。路由数由 148 增至 174（Agent Runtime / 职途导航等新路由）。基线测试：`mkg` 环境 254 passed / 2 failed（2 个为 Neo4j 容器未启动的环境失败）。 |

---

## 一、优先级总表

| 编号 | 优先级 | 问题 | 文件 | 状态 |
|------|--------|------|------|------|
| Q01 | P0 | `import os` 未使用，直接删除 | `admin.py:8` | 已完成 |
| Q02 | P0 | `get_neo4j_client` 函数内导入，与顶部 `get_mysql_client` 不一致 | `admin.py:432` | 已完成 |
| Q03 | P1 | 11 个路由重复 `try/except` 样板，抽装饰器 | `admin.py` 全文 | 已完成 |
| Q04 | P1 | `get_graph_status` God Function (~75 行, 4 职责) | `admin.py:420-491` | 已完成 |
| Q05 | P1 | `approve_oov` 多次写库无事务，破坏原子性 | `admin.py:347-377` | 已完成 |
| Q06 | P1 | `list_oov_pending` 静默吞错，伪装成功 | `admin.py:275-285` | 已完成 |
| Q07 | P2 | SQL 字符串散落在路由里，数据访问与 HTTP 混在一起 | `admin.py` 全文 | 评估不实施 |
| Q08 | P2 | 错误响应格式不统一，部分缺 `code` 字段 | `admin.py:156,202,333,341` | 已完成 |
| Q09 | P2 | 魔法字符串散落 (status/source/entity_type) | `admin.py` 全文 | 已完成 |
| Q10 | P3 | `.isoformat() if x else None` 重复 4 次 | `admin.py:216-218,296` | 已完成 |
| Q11 | P3 | `list_oov_pending` 手动 row→dict 字段映射 | `admin.py:287-297` | 评估不实施 |
| Q12 | P3 | `get_graph_status` 用 f-string 拼 SQL | `admin.py:468` | 已完成 |
| Q13 | P3 | `trigger_pipeline_build` TODO 注释应入 issue tracker | `admin.py:175` | 已完成 |
| Q14 | P1 | `agent.py` 8 个路由中 6 个无 try/except，异常返回 Flask 默认 HTML 而非 JSON | `agent.py` 全文 | 已完成 |
| Q15 | P1 | 错误响应格式不统一（裸 `{"success": False, "error"}` 缺 `code`），跨 3 文件 | `agent.py:58,186,290,321,352,358` `career.py:240,396,421,637,749,751,841` `favorites.py` 全文 | 已完成 |
| Q16 | P1 | `agent.py` 部分路由响应缺 `data` 键（`delete_session`/`update_session_context` 只返回 `message`） | `agent.py:313-316,363-366` | 已完成 |
| Q17 | P1 | `chat()` God Function (~120 行, 5 职责混合) | `agent.py:49-175` | 已完成 |
| Q18 | P1 | `chat_stream` 不校验 session 存在性，`chat` 校验 — 同文件内不一致 | `agent.py:195` vs `agent.py:80-83` | 已完成 |
| Q19 | P0 | `career.py:699` 冗余 `import json`（顶部 L7 已导入） | `career.py:699` | 已完成 |
| Q20 | P1 | `career.py` Cypher `.format(depth=depth)` 拼接，深度参数未校验范围 | `career.py:539,557` | 已完成 |
| Q21 | P1 | `career.py` `get_transfer_options` 同字段 `skill_overlap` 两套单位（0-1 vs 百分比） | `career.py:662,684` | 已完成 |
| Q22 | P2 | SQL/Cypher 字符串散落路由内（同 Q07，跨 3 文件） | `favorites.py` `agent.py:407-426` `career.py` 全文 | 评估不实施 |
| Q23 | P2 | 函数体内 import（同 Q02，跨 `career.py` + `agent.py`） | `career.py:457,525,640,699,788` `agent.py:169,222,233,404` | 已完成 |
| Q24 | P2 | `favorites.py` `get_role_market` 硬编码假数据兜底（15K-30K/22000/50） | `favorites.py:175-184` | 已完成 |
| Q25 | P2 | `career.py` `get_career_paths`/`get_transfer_options` 硬编码假数据兜底 | `career.py:604-615,724-736` | 已完成 |
| Q26 | P2 | `agent.py` `chat_stream` `generate()` 闭包复杂：嵌套 try/except/finally + 脆弱 SSE 解析 + 重复 import | `agent.py:210-248` | 已完成 |
| Q27 | P2 | `agent.py` `get_agent_stats` 4 层嵌套逻辑（for→if→for→if） | `agent.py:432-450` | 已完成 |
| Q28 | P2 | `career.py` `get_career_paths` 3 个近乎相同的 Cypher 分支 | `career.py:530-581` | 已完成 |
| Q29 | P2 | `career.py` `semantic_explore` ~85 行，混合 MySQL 模糊 + Milvus 语义 + 去重 | `career.py:413-501` | 已完成 |
| Q30 | P3 | `favorites.py` 字段名双重回退 `data.get('role') or data.get('role_name')` | `favorites.py:117` | 已完成 |
| Q31 | P3 | `career.py` 缓存 key 构造重复 6 处（手动 `f"cache:...:{a or 'all'}"`） | `career.py:155,266,339,381,425,824` | 已完成 |
| Q32 | P3 | `career.py` JSON 字段解析循环重复 7 处 | `career.py:198-200,243-244,303-305,366-367,399,451-452,844-845` | 已完成 |
| Q33 | P3 | `agent.py` 硬编码 demo 回复字符串 + `MAX_TB_LEN=2000` 魔法数 | `agent.py:66-69,171` | 已完成 |
| Q34 | P0 | `graph.py:318` `n.labels` (无括号) 取方法引用恒为真，应 `n.labels()` — 潜在 IndexError | `graph.py:318` | 已完成 (2026-08-06) |
| Q35 | P0 | `profile.py:120-142` SQL 未 SELECT `profile_json` 但 L142 解析它 → 恒为 None，死代码/bug | `profile.py:120-142` | 已完成 (2026-08-06) |
| Q36 | P1 | `match.py:43` 外部调用私有方法 `_run_l3_run_match` — 封装破坏 | `match.py:43` | 待办 |
| Q37 | P1 | `graph.py` `graph_query()` 4 层嵌套 ~35 行 (record→key-value→hasattr→list→item) | `graph.py:196-228` | 待办 |
| Q38 | P1 | `report.py:434` `LIKE CONCAT('%', report_id, '%')` 做 JSON join — 脆弱+性能差 | `report.py:434` | 待办 |
| Q39 | P1 | `match.py:190,270` + `profile.py:198,359` 函数内 `import json` (顶部缺失) — 跨 2 文件 | `match.py` `profile.py` | 待办 |
| Q40 | P1 | if/else 分支重复 ~15 行 SQL 列表 (match.py/profile.py/report.py 共 4 处) | `match.py:222-260` `profile.py:162-188,280-313` `report.py:365-380` | 待办 |
| Q41 | P2 | `learning.py` `recommend_courses()` ~100 行, 4 策略, O(n²) dedup 重复 3 次 | `learning.py:137-251` | 待办 |
| Q42 | P2 | `profile.py:48` `ALLOWED_EXTENSIONS` 定义在函数内 — 应提模块常量 | `profile.py:48` | 待办 |
| Q43 | P2 | `report.py:449-466` if/elif status→progress + status→current_chapter — 应用 dict 查表 | `report.py:449-466` | 待办 |
| Q44 | P2 | `graph.py:122` `_execute_safe_query` 参数严格相等校验 — 不允许可选参数，过严 | `graph.py:120-123` | 待办 |
| Q45 | P3 | `report.py:153` 字段双重回退 `job_portrait_id or job_id` (同 favorites.py Q30) | `report.py:153` | 待办 |
| Q46 | P3 | `match.py:342-351` `build_sub_dimensions` 循环重复 (vs L135-143) | `match.py:135-143,342-351` | 待办 |
| Q47 | P3 | `report.py:19-83` `_build_demo_report_payload` 硬编码 demo snapshot/chapters | `report.py:19-83` | 待办 |
| Q48 | P1 | **架构分层**: API 层 18 文件 117 处直接 `mysql.execute` — 引入 Repository 层, 试点 favorites.py | `api/` 全部 | 试点完成 |

---

## 二、P0 — 立即修复（零风险）

### Q01: `import os` 未使用

- **文件**: [admin.py:8](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/admin.py#L8)
- **问题**: 导入了 `os` 但全文未引用，属于死代码。
- **调整**: 直接删除该行。

### Q02: `get_neo4j_client` 函数内导入

- **文件**: [admin.py:432](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/admin.py#L432)
- **问题**: `from ..services.db import get_neo4j_client` 写在 `get_graph_status` 函数体内，而 `get_mysql_client` 在文件顶部导入。风格不一致，且每次调用都要查模块缓存。
- **调整**: 提到文件顶部与 `get_mysql_client` 并列导入。

---

## 三、P1 — 可读性/正确性（优先处理）

### Q03: 11 个路由重复 `try/except` 样板

- **文件**: `admin.py` 全文 11 个路由
- **问题**: 每个路由都是同一个壳：

  ```python
  try:
      mysql = get_mysql_client()
      ...
      return jsonify({"success": True, "data": ...})
  except Exception as e:
      return jsonify(safe_error("xxx", e)), 500
  ```
- **调整**: 在 `_helpers.py` 新增装饰器（KISS，不引入类层级）：

  ```python
  def api_handler(context: str):
      """统一 try/except + success 包装，消除样板"""
      def decorator(fn):
          @functools.wraps(fn)
          def wrapper(*args, **kwargs):
              try:
                  return jsonify({"success": True, "data": fn(*args, **kwargs)})
              except Exception as e:
                  return jsonify(safe_error(context, e)), 500
          return wrapper
      return decorator
  ```

  路由改为返回纯 `data`，从 ~12 行压到 ~5 行。
- **注意**: 需要返回非 `{"success": True, "data": ...}` 结构的路由（如带 `next_steps` 的 `trigger_pipeline_build`）保持显式写法，不强套装饰器。

### Q04: `get_graph_status` God Function

- **文件**: [admin.py:420-491](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/admin.py#L420-L491)
- **问题**: 单函数 ~75 行，混合 4 个职责：Neo4j 节点统计 + Neo4j 边统计 + MySQL 6 张表统计 + Milvus 状态 + overall 判定。
- **调整**: 拆 3 个私有纯函数（不需要建类）：

  ```python
  def _neo4j_stats() -> dict: ...
  def _mysql_stats() -> dict: ...
  def _milvus_stats() -> dict: ...

  @admin_bp.route('/graph/status')
  @api_handler("获取图谱状态")
  def get_graph_status():
      neo = _neo4j_stats()
      return {
          "neo4j": neo,
          "mysql": _mysql_stats(),
          "milvus": _milvus_stats(),
          "overall_status": "healthy" if neo.get("total_nodes", 0) > 0 else "warning",
      }
  ```

### Q05: `approve_oov` 多次写库无事务

- **文件**: [admin.py:347-377](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/admin.py#L347-L377)
- **问题**: 顺序执行 3 个独立写操作：
  1. INSERT `canonical_entity` (L352)
  2. SELECT `LAST_INSERT_ID()` (L361)
  3. UPDATE `oov_entity` (L373)

  若中途失败，前者已提交，破坏原子性。管理员审核状态与规范库不一致。
- **调整**: 包在一个事务里（`mysql` client 若支持 `begin/commit`，或显式传同一连接）。失败时整体回滚。

### Q06: `list_oov_pending` 静默吞错

- **文件**: [admin.py:275-285](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/admin.py#L275-L285)
- **问题**: 内层 `except` 捕获后返回 `{"success": True, "oov_list": [], "note": "oov_entity表可能不存在"}`，伪装成功。与其他路由的 `safe_error` 模式不一致，管理员看不到真实错误。
- **调整**: 治本方案是建表/迁移；过渡方案至少 `logger.error` 记录完整错误，并在响应中标记 `"partial": true`，不要假装成功。

---

## 四、P2 — 中期可维护性

### Q07: SQL 字符串散落在路由里

- **文件**: `admin.py` 全文（L24, L47-52, L82, L95-100, L162-166, L198, L250-255, L262-267, L271, L338, L347-351, L365-372, L402-409, L468）
- **问题**: 所有 SQL 内联在 handler 中，改表名/字段时要全文搜索。数据访问与 HTTP 逻辑混在一起。
- **调整**: 把 SQL 集中到模块顶部常量或新建 `_admin_queries.py`。**不强求** repository 类（项目规模不到那个程度），集中即可。

### Q08: 错误响应格式不统一

- **文件**:
  - [admin.py:156](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/admin.py#L156) — `source_id 必填`
  - [admin.py:202](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/admin.py#L202) — `任务不存在`
  - [admin.py:333](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/admin.py#L333) — `canonical_name 必填`
  - [admin.py:341](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/admin.py#L341) — `OOV记录不存在`
- **问题**: 这 4 处用裸 `{"success": False, "error": "xxx"}`，没有 `code` 字段；其余路由用 `safe_error` 带 `code`。前端无法统一处理。
- **调整**: 在 `_helpers.py` 新增 `safe_bad_request(context, message)`（带 `code=VALIDATION_ERROR`），4 处统一调用。

### Q09: 魔法字符串散落

- **文件**: `admin.py` 全文
- **问题**: 字面量到处出现：
  - 状态: `'pending'` / `'approved'` / `'rejected'`
  - 数据源: `'dataset'` / `'knowledge_base'` / `'upload'`
  - 实体类型: `'skill'` / `'position'` / `'major'` / `'certificate'`
- **调整**: 模块顶部定义 `Enum`：

  ```python
  class OovStatus(str, Enum):
      PENDING = "pending"
      APPROVED = "approved"
      REJECTED = "rejected"
  ```

---

## 五、P3 — 代码整洁

### Q10: `.isoformat() if x else None` 重复

- **文件**:
  - [admin.py:216-218](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/admin.py#L216-L218) — 连续 3 次
  - [admin.py:296](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/admin.py#L296)
- **问题**: 同一 datetime→ISO 字符串模式重复 4 次。
- **调整**: `_helpers.py` 新增 `_iso(dt)` helper。

### Q11: `list_oov_pending` 手动 row→dict 字段映射

- **文件**: [admin.py:287-297](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/admin.py#L287-L297)
- **问题**: 循环里逐字段拼 dict，`context_json` 又单独 `parse_json_field`。这种 row→dict 转换在多个路由出现。
- **调整**: 统一一个 `row_to_dict(row, fields)` helper，或直接在 SQL 里 `SELECT` 出想要的列。

### Q12: `get_graph_status` 用 f-string 拼 SQL

- **文件**: [admin.py:468](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/admin.py#L468)
- **问题**: `sql = f"SELECT COUNT(*) as count FROM {table}"`。虽然 `table` 来自硬编码列表无注入风险，但混用 f-string SQL 和参数化 SQL 风格不一致，容易被复制到有风险的场景。
- **调整**: 保持参数化或白名单校验。此处可保留 f-string 但加注释说明 `table` 已白名单化。

### Q13: `trigger_pipeline_build` TODO 注释

- **文件**: [admin.py:175](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/admin.py#L175)
- **问题**: `# TODO: 接入异步任务队列` 长期留在代码里，易被遗忘。
- **调整**: 迁移到 issue tracker 跟踪，代码里保留一行指针注释引用 issue 编号即可（或直接删除，由文档记录）。

---

## 六、执行顺序建议

1. **P0 批次** (Q01, Q02): 删 `import os` + 提导入到顶部 → 跑 smoke test
2. **P1 批次** (Q03, Q04, Q05, Q06): 装饰器 + 拆函数 + 事务 + 吞错修复 → 跑 smoke test
3. **P2 批次** (Q07, Q08, Q09): SQL 集中 + 错误格式 + Enum → 跑 smoke test
4. **P3 批次** (Q10, Q11, Q12, Q13): helper 抽取 + 清理 → 跑 smoke test

每批次完成后：
- 更新本文档对应行的"状态"列为"已完成"
- 更新变更日志
- 运行 `runbooks/local-smoke-test.md` 中的 smoke test 验证

---

## 七、扩展规则

本文档首批只覆盖 `admin.py`。后续若对其他 API 文件（如 `pipeline_ext_*.py`、`match.py`、`profile.py`）做同类审查，按相同格式追加新章节，编号续接（Q14, Q15, ...），不要混用编号。

---

## 八、跨文件共性问题 (favorites.py / agent.py / career.py)

> v1.2 新增。以下问题在 2-3 个文件中重复出现，调整时应统一处理。

### Q14: `agent.py` 6 个路由无 try/except

- **文件**: [agent.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py) — `chat_stream`(L178)、`create_session`(L261)、`get_history`(L281)、`delete_session`(L306)、`get_session_context`(L324)、`update_session_context`(L346)、`list_sessions`(L369)
- **问题**: 与 `favorites.py`(6/6 全有)、`career.py`(9/9 全有)、`admin.py`(11/11 全有) 相反，`agent.py` 8 个路由中只有 `chat`(L102 部分) 和 `get_agent_stats`(L403) 有 try/except。其余 6 个路由一旦 `session_mgr` 抛异常，Flask 返回默认 HTML 500 页面，前端 JSON 解析崩溃。
- **注意**: `chat()` 的 try/except 也只覆盖 L102-175 的 agent 执行段，L78-100 的 session 解析段未覆盖。
- **调整**: 复用 admin.py 已有的 `api_handler` 装饰器（Q03 产物），套到这 6 个路由上。`chat`/`chat_stream` 因含流式/多分支逻辑可保留显式 try/except，但需扩大覆盖范围。

### Q15: 错误响应格式不统一（跨 3 文件）

- **文件**:
  - [agent.py:58](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L58), [agent.py:186](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L186), [agent.py:290](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L290), [agent.py:321](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L321), [agent.py:352](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L352), [agent.py:358](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L358)
  - [career.py:240](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L240), [career.py:396](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L396), [career.py:421](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L421), [career.py:637](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L637), [career.py:749](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L749), [career.py:751](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L751), [career.py:841](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L841)
- **问题**: 这 13 处用裸 `{"success": False, "error": "xxx"}`，没有 `code` 字段。与 admin.py Q08 同类问题，但跨 3 文件。
- **调整**: 复用 admin.py 已有的 `safe_bad_request`（400）和 `safe_not_found`（404）helper（Q08 产物）。

### Q16: `agent.py` 部分路由响应缺 `data` 键

- **文件**: [agent.py:313-316](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L313-L316) (`delete_session`), [agent.py:363-366](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L363-L366) (`update_session_context`)
- **问题**: 这两个路由返回 `{"success": True, "message": "..."}`，没有 `data` 键。其他所有路由都是 `{"success": True, "data": {...}}`。前端 `response.data` 取到 `undefined`。
- **调整**: 统一为 `{"success": True, "data": {"message": "..."}}`。

### Q22: SQL/Cypher 字符串散落路由内（同 Q07，跨 3 文件）

- **文件**: `favorites.py`(L25,28,45-49,78,95,98,114,134,149-158)、`agent.py`(L407-416,421-426)、`career.py`(L103-113,180-194,202,234-236,291-301,358-363,388-393,432-444,470-475,532-539,550-557,568-574,647-656,668-678,692-697,770-782,791-796,831-837)
- **问题**: 同 admin.py Q07，但范围扩大到 3 个文件共 30+ 处 SQL/Cypher 内联。
- **调整**: 同 Q07 评估结论——不强求 repository 类，但每个文件内 SQL 应集中到模块顶部常量。各文件独立处理，不跨文件合并。

### Q23: 函数体内 import（同 Q02，跨 career.py + agent.py）

- **文件**:
  - [career.py:457](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L457) `from ..services.vector import get_milvus_client`
  - [career.py:525](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L525) `from ..services.db import get_neo4j_client`
  - [career.py:640](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L640) `from ..services.db import get_neo4j_client`（与 L525 重复）
  - [career.py:788](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L788) `import uuid`（顶部未导入）
  - [agent.py:169](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L169) `import traceback`
  - [agent.py:222](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L222) `import json as _json`
  - [agent.py:233](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L233) `import json`（与 L222 重复且与闭包外顶部逻辑混乱）
  - [agent.py:404](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L404) `from ..services.db import get_mysql_client`
- **注意**: `agent.py:44` 的 `from ..services.langgraph_agent import ...` 是有意的延迟加载（避免循环依赖），可保留但需加注释说明。
- **调整**: 除 L44 外，全部提到文件顶部。`agent.py` 顶部需新增 `import traceback` 和 `from ..services.db import get_mysql_client`。

---

## 九、favorites.py 专属问题

### Q24: `get_role_market` 硬编码假数据兜底

- **文件**: [favorites.py:175-184](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/favorites.py#L175-L184)
- **问题**: 当 `job_posting` 表查不到匹配时，返回硬编码 `salaryRange: "15K-30K", medianSalary: 22000, demandCount: 50`，伪装成真实数据。前端无法区分真假，用户被误导。
- **调整**: 返回 404 或 `{"success": True, "data": None, "message": "暂无该岗位市场数据"}`，不要返回假数字。同时 L170 的 `"高" if demand_count > 100 else "中"` 缺 "低" 档且 100 是魔法数。

### Q30: 字段名双重回退

- **文件**: [favorites.py:117](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/favorites.py#L117)
- **问题**: `data.get('role') or data.get('role_name')` — 前端契约不明确，两个字段名都接受。DELETE 路由 L134 又只用 `role_name`，不一致。
- **调整**: 统一为一个字段名（建议 `role_name` 与 DB 列对齐），删除双重回退。

> **注**: `favorites.py` 的 IDOR 漏洞（`user_id` 从 query param 获取，L22/92/132）属安全问题，已在 `specs/pending-authz-rbac.md` 2.2 节跟踪，本文档不重复。

---

## 十、agent.py 专属问题

### Q17: `chat()` God Function

- **文件**: [agent.py:49-175](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L49-L175) (~120 行)
- **问题**: 单函数混合 5 个职责：参数校验 + demo 模式分支 + session 解析 + LangGraph 分支 + ReAct 分支 + 异常处理。
- **调整**: 拆 3 个私有函数：

  ```python
  def _resolve_session(session_mgr, session_id, student_id) -> tuple[str, Session]: ...
  def _run_langgraph(agent, message, session_id, student_context, history) -> dict: ...
  def _run_react(agent, message, student_context, history) -> dict: ...
  ```

  `chat()` 只做：校验 → demo 短路 → resolve_session → 分支调度。

### Q18: `chat_stream` 不校验 session 存在性

- **文件**: [agent.py:195](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L195) vs [agent.py:80-83](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L80-L83)
- **问题**: `chat()` 传入 `session_id` 时校验存在性，不存在返回 404。`chat_stream()` 不校验，L195 用 `session.student_id if session else None` 静默放行，下游 agent 收到空 context。
- **调整**: `chat_stream` 也加同样的 404 校验。

### Q26: `chat_stream` `generate()` 闭包复杂

- **文件**: [agent.py:210-248](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L210-L248)
- **问题**:
  1. 嵌套 try/except/finally 三层
  2. SSE 解析 `sse_chunk.replace("data: ", "").strip()` 脆弱——若 chunk 格式微变即崩
  3. L222 `import json as _json` 与 L233 `import json` 重复导入
  4. "D-18" 注释标签散落
- **调整**: 抽 `_parse_sse_content(chunk)` 纯函数；import 提到顶部；"D-18" 标签移到函数 docstring。

### Q27: `get_agent_stats` 4 层嵌套

- **文件**: [agent.py:432-450](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L432-L450)
- **问题**: `for row → if tool_calls → parse → if list → for tc → if result` 4 层嵌套，~20 行难读。
- **调整**: 抽 `_compute_tool_stats(tool_results) -> tuple[dict, int, int]` 纯函数，主路由只调用。

### Q33: 硬编码 demo 回复 + 魔法数

- **文件**: [agent.py:66-69](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L66-L69) (demo 回复长字符串), [agent.py:171](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py#L171) (`MAX_TB_LEN = 2000`)
- **调整**: demo 回复提为模块常量 `_DEMO_REPLY`；`MAX_TB_LEN` 提为模块常量。

---

## 十一、career.py 专属问题

### Q19: 冗余 `import json`

- **文件**: [career.py:699](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L699)
- **问题**: `import json` 在 `get_transfer_options` 函数体内，但顶部 L7 已导入。死代码。
- **调整**: 直接删除 L699。

### Q20: Cypher `.format(depth=depth)` 拼接

- **文件**: [career.py:539](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L539), [career.py:557](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L557)
- **问题**: Cypher 的 `*1..{depth}` 不支持参数化，用 `.format()` 拼接。`depth` 来自 `request.args.get('depth', 3, type=int)`，Flask 的 `type=int` 已过滤非整数，但未限范围——用户传 `depth=999` 会拖垮 Neo4j。
- **调整**: 加 `depth = min(max(depth, 1), 5)` 上限校验。

### Q21: `skill_overlap` 两套单位

- **文件**: [career.py:662](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L662) vs [career.py:684](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L684)
- **问题**: 同一字段 `skill_overlap`：
  - L662 (to_role 分支): `r["skill_overlap"]` — 原始值（Neo4j 存的 0-1 小数）
  - L684 (无 to_role 分支): `round(r["skill_overlap"] * 100, 1)` — 百分比
  - 前端拿到同字段两套单位，渲染混乱。
- **调整**: 统一为百分比（或统一为 0-1），两处保持一致。

### Q25: 硬编码假数据兜底

- **文件**: [career.py:604-615](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L604-L615) (`get_career_paths`), [career.py:724-736](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L724-L736) (`get_transfer_options`)
- **问题**: Neo4j 查询失败时返回硬编码 3 条职业链/3 条转岗选项，带 `"fallback": True` 标记。但前端是否检查 `fallback` 字段存疑——若无检查，用户看到假数据以为是真实推荐。
- **调整**: 同 Q24，返回空列表 + `fallback: True`，或返回 503。不要返回假链路。

### Q28: `get_career_paths` 3 个近乎相同的 Cypher 分支

- **文件**: [career.py:530-581](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L530-L581)
- **问题**: `if role / elif category / else` 三个分支，每个分支都是：构造 Cypher → execute → for r in results: paths.append(...)。循环体完全相同。
- **调整**: 抽 `_query_promotion_chains(cypher, params) -> list` helper，三个分支只负责构造 cypher 和 params。

### Q29: `semantic_explore` ~85 行混合三职责

- **文件**: [career.py:413-501](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L413-L501)
- **问题**: 单函数做：MySQL 模糊匹配 + Milvus 语义搜索 + 结果去重合并。
- **调整**: 拆 `_mysql_fuzzy_match(query, top_k)` 和 `_milvus_semantic_match(query, top_k, existing_results)` 两个私有函数。

### Q31: 缓存 key 构造重复

- **文件**: [career.py:155](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L155), [career.py:266](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L266), [career.py:339](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L339), [career.py:381](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L381), [career.py:425](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L425), [career.py:824](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L824)
- **问题**: 6 处手动 `f"cache:career:xxx:{a or 'all'}:{b or 'all'}:{c or 'all'}"` 模式。
- **调整**: 加 `_cache_key(prefix, **parts) -> str` helper。

### Q32: JSON 字段解析循环重复

- **文件**: [career.py:198-200](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L198-L200), [career.py:243-244](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L243-L244), [career.py:303-305](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L303-L305), [career.py:366-367](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L366-L367), [career.py:399](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L399), [career.py:451-452](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L451-L452), [career.py:844-845](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py#L844-L845)
- **问题**: 7 处 `for field in (...): row[field] = parse_json_field(row.get(field))` 模式。
- **调整**: 加 `parse_json_fields(row, fields) -> None` helper（原地修改）。

---

## 十二、新增批次执行顺序建议

在原六节执行顺序之后，追加：

5. **P0 批次 2** (Q19): 删 `career.py` 冗余 `import json` → 跑 smoke test
6. **P1 批次 2** (Q14, Q15, Q16, Q17, Q18, Q20, Q21): `agent.py` 路由补 try/except + 错误格式统一 + 响应壳统一 + 拆 `chat()` + session 校验对齐 + Cypher depth 校验 + skill_overlap 单位统一 → 跑 smoke test
7. **P2 批次 2** (Q22, Q23, Q24, Q25, Q26, Q27, Q28, Q29): SQL 集中 + import 提顶 + 假数据兜底改空 + SSE 闭包拆分 + 嵌套逻辑抽函数 + Cypher 分支合并 + semantic_explore 拆分 → 跑 smoke test
8. **P3 批次 2** (Q30, Q31, Q32, Q33): 字段名统一 + 缓存 key helper + JSON 解析 helper + 魔法数提常量 → 跑 smoke test
9. **P0 批次 3** (Q34, Q35): `graph.py` labels() 括号修复 + `profile.py` profile_json 死代码 → 跑 smoke test
10. **P1 批次 3** (Q36, Q37, Q38, Q39, Q40): 封装修复 + 嵌套拆分 + JSON join 改查 + import 提顶 + SQL 列表去重 → 跑 smoke test
11. **P2 批次 3** (Q41, Q42, Q43, Q44): recommend 拆分 + 常量提顶 + dict 查表 + 参数校验放宽 → 跑 smoke test
12. **P3 批次 3** (Q45, Q46, Q47): 字段名统一 + sub_dimensions helper + demo 常量化 → 跑 smoke test

---

## 十三、graph.py / learning.py / match.py / profile.py / report.py 专属问题

> v1.3 新增。跨文件共性问题 (函数内 import / SQL 散落 / 裸错误响应) 延续 Q22/Q23/Q15，不重复立项；本节仅列文件特有问题。

### Q34: `graph.py` `n.labels` 无括号 — 潜在 IndexError

- **文件**: [graph.py:318](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/graph.py#L318)
- **问题**: `list(n.labels())[0] if hasattr(n, 'labels') and n.labels else 'Unknown'` — 条件里的 `n.labels` (无括号) 取的是方法引用对象，恒为真。原意应是 `n.labels()` 判断标签列表是否非空。当 `n.labels()` 返回空列表时，条件仍为 True，`list(n.labels())[0]` 触发 IndexError。
- **调整**: 改为 `n.labels() if hasattr(n, 'labels') else []`，再判空取首元素。

### Q35: `profile.py` SQL 未 SELECT `profile_json` 但代码解析它 — 死代码/bug

- **文件**: [profile.py:120-142](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/profile.py#L120-L142)
- **问题**: L120-127 的 SQL `SELECT` 列表不含 `profile_json`，但 L142 执行 `profile['profile_json'] = parse_json_field(profile.get('profile_json'))`。`profile.get('profile_json')` 恒为 `None`，`parse_json_field(None)` 恒为 `None`。该行永远把 `profile_json` 设为 `None`，是死代码，且暗示原本想返回完整画像却漏选列。
- **调整**: 二选一 —— 若前端需要 `profile_json`，SQL 加上该列；若不需要，删除 L142。

### Q36: `match.py` 外部调用私有方法 `_run_l3_run_match`

- **文件**: [match.py:43](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/match.py#L43)
- **问题**: `pipeline_tool._run_l3_run_match(params)` — 调用 `PipelineRunTool` 的下划线前缀私有方法。破坏封装，且该方法签名/返回值变更不会被视为公共 API 变更，易引入隐性 bug。
- **调整**: 在 `PipelineRunTool` 暴露公共方法 (如 `run_l3_match`)，或改用 `PipelineRunner` + `PipelineLevel.L3_RUN_MATCH` (与 `report.py` L186-195 一致)。

### Q37: `graph.py` `graph_query()` 4 层嵌套

- **文件**: [graph.py:196-228](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/graph.py#L196-L228) (~35 行)
- **问题**: `for record → for key,value → if hasattr(labels) / elif hasattr(type) / elif isinstance(list) → for item → if hasattr(labels) / elif hasattr(type)` 4 层嵌套，节点/边构造逻辑重复 2 次 (外层和 list 内层)。
- **调整**: 抽 2 个纯函数：

  ```python
  def _node_from_value(value) -> dict | None: ...
  def _edge_from_value(value) -> dict | None: ...
  ```

  主循环只做 `for record → for value → _node_from_value or _edge_from_value`。

### Q38: `report.py` `LIKE CONCAT` 做 JSON join — 脆弱+性能差

- **文件**: [report.py:434](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/report.py#L434)
- **问题**: `LEFT JOIN pipeline_task pt ON pt.result_json LIKE CONCAT('%', cr.report_id, '%')` — 用 `LIKE` 在 JSON 文本里搜 `report_id` 做 join。问题：
  1. `report_id` 是 UUID 片段，可能误匹配其他字段的相同子串
  2. `LIKE '%...%'` 无法走索引，全表扫描
  3. `result_json` 是 JSON 列，应使用 `JSON_EXTRACT` 或在 `pipeline_task` 增加 `report_id` 索引列
- **调整**: 在 `pipeline_task` 表增加 `report_id` 列 (或从 `params_json` 用 `JSON_EXTRACT` 取)，join 改为等值连接。

### Q39: `match.py` + `profile.py` 函数内 `import json` (顶部缺失)

- **文件**:
  - [match.py:190](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/match.py#L190), [match.py:270](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/match.py#L270) — `import json` 在函数内，且 `match.py` 顶部根本没导入 `json`
  - [profile.py:198](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/profile.py#L198), [profile.py:359](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/profile.py#L359) — 同样
- **问题**: 与 `admin.py`(L6 顶部) / `career.py`(L7 顶部) / `report.py`(L6 顶部) 不一致。且 `match.py`/`profile.py` 实际只用 `parse_json_field` helper，这两处 `import json` 后并未使用 `json` 模块 — 是无用导入。
- **调整**: 删除这 4 处函数内 `import json` (它们是无用的，`parse_json_field` 已在顶部导入)。

### Q40: if/else 分支重复 ~15 行 SQL 列表

- **文件**:
  - [match.py:222-260](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/match.py#L222-L260) `get_career_recommendation` — if/else 两分支 SQL 列列表完全相同，仅 WHERE 不同
  - [profile.py:162-188](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/profile.py#L162-L188) `get_student_4d` — 同模式
  - [profile.py:280-313](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/profile.py#L280-L313) `list_job_portraits` — 同模式
  - [report.py:365-380](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/report.py#L365-L380) `list_reports` — `where_clause` 拼接已较好，但仍重复 count 逻辑
- **问题**: 4 处 if/else 分支把 ~15 行 SQL 列列表复制两份，改字段要改两处。与 `career.py` Q28 (Cypher 分支重复) 同类。
- **调整**: 把 `SELECT 列列表` + `FROM` 提为常量，分支只构造 `WHERE` 子句和 `params`。参考 `report.py:382-398` 的 `where_clause` 拼接模式 (已较好)。

### Q41: `learning.py` `recommend_courses()` ~100 行 4 策略

- **文件**: [learning.py:137-251](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/learning.py#L137-L251)
- **问题**:
  1. 单函数 ~100 行，4 个策略块 (weak_skills / target_role / student_id / 兜底) 串行 if
  2. O(n²) 去重 `if r["id"] not in [c["id"] for c in recommended]` 重复 3 次 (L196, L226, 隐式)
  3. 每个策略块内 SQL + 循环 + `match_reason` 赋值混在一起
- **调整**:
  - 抽 `_match_by_weak_skills(weak_skills, limit) -> list`
  - 抽 `_match_by_target_role(target_role, limit) -> list`
  - 抽 `_match_by_student_history(student_id, limit) -> list`
  - 用 `seen_ids: set` 替代列表推导去重，O(n²)→O(n)

### Q42: `profile.py` `ALLOWED_EXTENSIONS` 定义在函数内

- **文件**: [profile.py:48](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/profile.py#L48)
- **问题**: `ALLOWED_EXTENSIONS = {'pdf', 'docx', ...}` 定义在 `upload_resume` 函数体内，每次请求重建集合。且若其他路由需要校验扩展名无法复用。
- **调整**: 提到模块顶部常量 `_ALLOWED_EXTENSIONS`。

### Q43: `report.py` if/elif status→progress + status→current_chapter

- **文件**: [report.py:449-466](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/report.py#L449-L466)
- **问题**: 两个 if/elif 链分别把 `status` 映射到 `progress` 和 `current_chapter`，共 ~18 行。每加一个状态要改两处。
- **调整**: 用 dict 查表：

  ```python
  _PROGRESS_BY_STATUS = {"pending": 0, "generating": 50, "completed": 100, "failed": -1}
  _CHAPTER_BY_STATUS = {"generating": "正在生成报告内容", "completed": "报告生成完成", "failed": "报告生成失败"}
  progress = _PROGRESS_BY_STATUS.get(status, 0) if progress is None else progress
  current_chapter = _CHAPTER_BY_STATUS.get(status)
  ```

### Q44: `graph.py` `_execute_safe_query` 参数严格相等校验

- **文件**: [graph.py:120-123](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/graph.py#L120-L123)
- **问题**: `if required_params != provided_params: raise` — 严格集合相等。调用方传了多余的可选参数 (如 `limit`) 会直接报错，即使 Cypher 模板不需要。过严，限制扩展。
- **调整**: 改为 `if not required_params.issubset(provided_params)` — 只校验必需参数齐全，允许多余参数。

### Q45: `report.py` 字段双重回退 (同 favorites.py Q30)

- **文件**: [report.py:153](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/report.py#L153)
- **问题**: `data.get('job_portrait_id') or data.get('job_id')` — 同 `favorites.py:117` Q30 模式，前端契约不明确。
- **调整**: 统一为 `job_portrait_id` (与 DB 列对齐)，删除 `job_id` 回退。或反之，但全文统一。

### Q46: `match.py` `build_sub_dimensions` 循环重复

- **文件**: [match.py:135-143](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/match.py#L135-L143) (`get_match_result`) vs [match.py:342-351](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/match.py#L342-L351) (`list_match_results`)
- **问题**: 同一 `build_sub_dimensions(...)` 7 参数调用重复 2 次，参数构造逻辑相同。
- **调整**: 抽 `_build_sub_dims_from_row(row) -> dict` helper，两处调用。

### Q47: `report.py` `_build_demo_report_payload` 硬编码 demo

- **文件**: [report.py:19-83](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/report.py#L19-L83)
- **问题**: 65 行硬编码 demo snapshot + 6 章 chapters 字典。与 `agent.py` Q33 (demo 回复)、`career.py` Q25 (假数据兜底) 同类。修改 demo 内容要改源码。
- **调整**: demo snapshot 提为模块常量 `_DEMO_SNAPSHOT`，chapters 模板提为 `_DEMO_CHAPTERS_TEMPLATE`，函数只做参数填充。

---

## 十四、跨文件问题延续说明

v1.3 新增的 5 个文件中，以下共性问题延续已立项的条目，不重复编号：

| 共性问题 | 延续条目 | 新增文件位置 |
|---------|---------|-------------|
| 函数体内 import | Q23 | `graph.py:363` `match.py:35` `profile.py:53,68,82` `report.py:186` |
| SQL/Cypher 散落路由内 | Q22 | 5 文件全文 (graph.py 已用 `SAFE_CYPHER_TEMPLATES` 集中，是良好示例) |
| 裸 `{"success": False, "error"}` 缺 `code` | Q15 | `learning.py:53,85` `match.py:25-27,57-58,107-110,183-186,263-266` `profile.py:32-35,50,132-136,190-194,251-255,351-355,380-384` `report.py:156-159,258-261,312-315,321-324,333-336,441-444,509-513` `graph.py:136-139,178-182,184-188,324-327,455-459` |
| IDOR (user_id 从 query param) | `pending-authz-rbac.md` 2.2 | `learning.py:62,80,112` `profile.py:115,155,235,267` |

**注**: `graph.py` 的 `SAFE_CYPHER_TEMPLATES` + `_execute_safe_query` 是当前代码库中 SQL/Cypher 集中的**最佳实践示例**，可作为 Q07/Q22 调整时的参考模板。

---

## 十五、验证记录

### admin.py 批次 (2026-07-15)

- **P0 批次** (Q01, Q02): 删 `import os` + `get_neo4j_client` 提至顶部 → 148 路由不变
- **P1 批次** (Q03, Q04, Q05, Q06): 
  - Q03: `api_handler` 装饰器覆盖 9 个路由 (CRUD + 状态查询), `trigger_pipeline_build` 因含 `next_steps` 保持显式
  - Q04: `get_graph_status` 拆为 `_neo4j_stats`/`_mysql_stats`/`_milvus_stats` 3 个私有函数
  - Q05: `approve_oov` 用 `with conn.begin()` 事务包裹 INSERT+SELECT LAST_INSERT_ID+UPDATE
  - Q06: `list_oov_pending` 吞错改为 `logger.error(exc_info=True)` + `partial: True` 标记
  - 顺带完成 Q12 (白名单注释 + f-string 保留) + Q13 (TODO 改 issue tracker 指针)
  - 验证: 15 admin 路由 + 148 总路由不变
- **P2 批次** (Q07, Q08, Q09):
  - Q07: 评估不实施 — 每路由仅 1 个 SQL 无重复, 抽出为常量反而割裂可读性 (KISS)
  - Q08: `_helpers.py` 新增 `safe_bad_request`/`safe_not_found`, 4 处裸错误统一带 `code` 字段
  - Q09: 新增 `OovStatus`/`PipelineSourceType` Enum, 替换 5 处魔法字符串
  - 验证: Enum 正确加载, 148 路由不变
- **P3 批次** (Q10, Q11, Q12, Q13):
  - Q10: `_helpers.py` 新增 `_iso` helper, 4 处 `.isoformat() if x else None` 替换
  - Q11: 评估不实施 — 含字段重命名和类型转换, 不适合简单 row_to_dict 抽象 (KISS)
  - Q12/Q13: 已在 P1 完成
  - 验证: 148 路由不变

### 新增 helper 清单 (`_helpers.py`)

| helper | 用途 | 用法 |
|--------|------|------|
| `api_handler(context)` | 路由装饰器, 消除 try/except 样板 | `@api_handler("获取列表")` |
| `safe_bad_request(message)` | 400 参数错误响应 | `return jsonify(safe_bad_request("xxx 必填")), 400` |
| `safe_not_found(message)` | 404 资源不存在响应 | `return jsonify(safe_not_found("xxx 不存在")), 404` |
| `_iso(dt)` | datetime → ISO 字符串, None 安全 | `_iso(task["created_at"])` |

### agent.py / favorites.py / career.py 批次 (2026-07-15, v1.4)

- **P0 批次 2** (Q19): 删 `career.py` 冗余 `import json` → 148 路由不变
- **P1 批次 2** (Q14, Q15, Q16, Q17, Q18, Q20, Q21):
  - Q14: `agent.py` 6 路由 (create_session/get_history/delete_session/get_session_context/update_session_context/list_sessions) 套 `@api_handler`
  - Q15: 跨 3 文件裸错误改 `safe_bad_request`/`safe_not_found`
  - Q16: `delete_session`/`update_session_context` 响应补 `data` 键
  - Q17: `chat()` 拆为 `_resolve_session`/`_run_langgraph`/`_run_react` 3 个私有函数
  - Q18: `chat_stream` 补 session 404 校验
  - Q20: `depth = min(max(depth, 1), 5)` 上限保护
  - Q21: `get_transfer_options` to_role 分支 `skill_overlap` 改为 `round(r["skill_overlap"] * 100, 1)` 统一百分比
  - 验证: 148 路由不变
- **P2 批次 2** (Q22, Q23, Q24, Q25, Q26, Q27, Q28, Q29):
  - Q22: 评估不实施 — 同 Q07, 每路由仅 1-2 个 SQL, 抽出为常量反而割裂可读性 (KISS)
  - Q23: `agent.py` 顶部新增 `import json`/`import traceback`/`from ..services.db import get_mysql_client`; `career.py` 顶部新增 `import uuid` + `get_neo4j_client`/`get_milvus_client` (注: `get_milvus_client` 实际在 `..services.db` 不在 `..services.vector`, 已修正); 删除 8 处函数内 import; L52 延迟加载保留加注释
  - Q24: `favorites.py` `get_role_market` 无数据时返回 `data: None` + message, 不再返回硬编码 15K-30K/22000/50; 需求级别补 "低" 档 (100/30 阈值)
  - Q25: `career.py` `get_career_paths`/`get_transfer_options` 兜底改空列表 + `fallback: True` + message
  - Q26: `agent.py` 抽 `_parse_sse_content(sse_chunk)` 纯函数, `generate()` 闭包简化为 try/except/finally 单层
  - Q27: `agent.py` 抽 `_compute_tool_stats(tool_results) -> tuple` 纯函数, 4 层嵌套压为 2 层
  - Q28: `career.py` 抽 `_query_promotion_chains(cypher, params)` helper, 3 个分支只构造 cypher 和 params
  - Q29: `career.py` 拆 `_mysql_fuzzy_match(query, top_k)` + `_milvus_semantic_match(query, top_k, existing)` 2 个私有函数
  - 验证: 148 路由不变
- **P3 批次 2** (Q30, Q31, Q32, Q33):
  - Q30: `favorites.py` `add_target_role` 字段名统一为 `role_name`, 删除 `data.get('role') or` 回退
  - Q31: `career.py` 新增 `_cache_key(prefix, **parts)` helper, 6 处手动 `f"cache:...:{a or 'all'}"` 替换
  - Q32: `career.py` 新增 `_parse_json_fields(row, fields)` helper, 6 处循环替换 (L307 因 `default=row.get(field)` 特殊语义保留)
  - Q33: `agent.py` 新增 `_MAX_TB_LEN = 2000` + `_DEMO_REPLY` 模块常量, `chat()` 内引用替换
  - 验证: 148 路由不变

### 新增 helper 清单 (`agent.py` / `career.py` 模块内)

| helper | 文件 | 用途 |
|--------|------|------|
| `_resolve_session(session_mgr, session_id, student_id)` | agent.py | 解析或创建会话, 返回 (session_id, session) |
| `_run_langgraph(message, session_id, student_context, history)` | agent.py | 执行 LangGraph Agent |
| `_run_react(message, student_context, history)` | agent.py | 执行 ReAct Agent |
| `_parse_sse_content(sse_chunk)` | agent.py | 从 SSE chunk 提取 assistant 内容片段 |
| `_compute_tool_stats(tool_results)` | agent.py | 聚合工具调用统计, 返回 (tool_stats, total, success) |
| `_cache_key(prefix, **parts)` | career.py | 构造缓存 key, None 统一为 'all' |
| `_parse_json_fields(row, fields)` | career.py | 原地解析 row 中多个 JSON 字段 |
| `_query_promotion_chains(cypher, params)` | career.py | 执行晋升链 Cypher 并转 dict 列表 |
| `_mysql_fuzzy_match(query, top_k)` | career.py | MySQL 模糊匹配职业方向 |
| `_milvus_semantic_match(query, top_k, existing)` | career.py | Milvus 语义搜索 + 反查 related_roles |

### 新增模块常量 (`agent.py`)

| 常量 | 值 | 用途 |
|------|-----|------|
| `_MAX_TB_LEN` | 2000 | traceback 截断长度 |
| `_DEMO_REPLY` | (演示模式回复字符串) | demo 模式硬编码回复 |

---

## 十四、架构分层改造 (v2.0, Q48)

### 14.1 问题

API 层 18 文件共 117 处直接 `mysql.execute(sql)` / `neo4j.execute(cypher)`, HTTP + 业务逻辑 + 数据访问三合一。`models/` 已有完整 SQLAlchemy ORM 但 API 层完全没用, 形同虚设。SQL 散落导致无法集中优化、无法单测数据访问、无法替换存储。

### 14.2 方案决策 (用户确认)

- **分层模式**: Repository 层 only — 不引入 Service 层, 避免过度分层 (KISS)
- **推进节奏**: 试点先行 — 先做 favorites.py 验证范式, 再推广
- **试点文件**: favorites.py (最小, 187 行 9 处 SQL, 业务简单 CRUD)

### 14.3 Repository 层范式 (供后续文件参考)

**目录结构**:
```
app/repositories/
├── __init__.py          # 包文档 + 设计原则
└── favorites_repo.py    # 按业务域分文件
```

**设计原则** (见 `repositories/__init__.py`):
1. Repository 方法返回纯数据 (dict/list), 不含 HTTP 概念
2. Repository 不做业务逻辑判断 (如 demand_level 映射), 那是 API 层的事
3. 方法名用业务语义 (`save_job` 而非 `insert_saved_job`)
4. 用 mysql_client 的 `execute`/`execute_write`, 与现有模式一致 (不强制 ORM)
5. 每个业务域一个文件, 不跨域合并
6. 模块级单例 (Repository 无状态, 每次方法内获取 client)

**API 层职责** (改造后):
- HTTP 参数解析 + 校验
- 调用 repository 方法
- 业务逻辑判断 (如 demand_count → demand_level 映射)
- 响应包装 (jsonify + success/data 结构)
- 异常处理 (safe_error)

**Repository 层职责** (新增):
- SQL/Cypher 字符串集中
- 参数化查询执行
- 返回原始数据 (dict/list/None)

**试点验证** (favorites.py):
- 改造前: 9 处 `mysql.execute`/`execute_write` 内联
- 改造后: API 层 0 处 DB 调用, 全部通过 `favorites_repo`
- 148 路由不变 ✅
- `favorites_repo.py` 提供 7 个方法: `list_saved_jobs`/`save_job`/`delete_saved_job`/`list_target_roles`/`add_target_role`/`delete_target_role`/`get_role_market_stats`

### 14.4 后续推进顺序 (按业务域分批)

| 批次 | 文件 | SQL 数 | 复杂度 | 优先级 |
|------|------|--------|--------|--------|
| 1 (试点) | favorites.py | 9 | 低 | ✅ 完成 |
| 2 | profile.py | 11 | 中 | P1 |
| 3 | learning.py | 11 | 中 | P1 |
| 4 | match.py | 6 | 中 | P1 |
| 5 | report.py | 7 | 中 | P1 |
| 6 | admin.py | 16 | 高 (含事务) | P2 |
| 7 | career.py | 16 | 高 (含 Neo4j/Milvus/Redis) | P2 |
| 8 | admin_kb.py | 13 | 中 | P2 |
| 9 | pipeline.py + pipeline_ext_*.py (12 文件) | 28 | 中 | P3 |
| 10 | agent.py + graph.py | 3 | 低 | P3 |
