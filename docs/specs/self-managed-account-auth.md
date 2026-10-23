# 平台自建账号认证：第一条真实后端闭环

> 迁移入口已改为 [MySQL 统一迁移](../runbooks/mysql-migrations.md) 的 `alembic upgrade head`。下文 `migrate-account-session-epoch` 为原实施记录，不再是推荐部署步骤。

状态：第一切片已实现并局部验证（2026-10-13）；不等于完整上线。用户已选择平台自建账号作为正式管理员身份来源；不把 `X-Dev-Student-Id` 或客户端 role 当作正式凭证。

## 范围与契约

- 学生可注册新账号；服务端生成随机 `user_account.id`，强制 role=`student`，绝不按自报 ID/用户名认领既存 `student_profile`。历史孤儿资料的迁移需独立核验。
- 管理员仅可信本机 CLI 开户，不提供公开的 admin 注册或提权 API。教师开户、密码找回、多因子认证、邮件验证、历史用户映射不在此切片。
- `/api/auth/csrf` 获取 CSRF token；注册、登录、改密、登出及所有已登录 cookie 的变更请求须 `X-CSRFToken`。`/api/auth/me` 返回 DB 当前 role/status 的非秘密资料。已登录者可用 `POST /api/auth/change-password` 携带当前密码及新密码自助改密；不提供无当前密码的公开找回入口。
- `Flask-Login` 管理登录状态，每请求用 MySQL `user_account` 重载身份，disabled 立即拒绝；`Flask-Session` 把会话置于 Redis，使登出可撤销；`Flask-WTF` 验证 CSRF；`Flask-Limiter` 在 Redis 上限制注册/登录；`argon2-cffi` 保存 Argon2id 哈希。没有安全 `SECRET_KEY` 或 strict 模式未配置时，认证入口故障关闭，不使用项目现有弱默认值。
- 每次登录把数据库 `auth_epoch` 快照写入 Redis 会话；`user_account` 的 `BEFORE UPDATE` 触发器在 `status`、`role` 或 `password_hash` 发生真实变化时递增代数，不相关资料修改不递增。每请求重载时必须同时满足账号 active、会话有代数、代数等于数据库当前值；因此停用→重新启用期间完全闲置的多个旧 cookie 也不能复活，重新登录才会取得新代数。这是**访问拒绝**而非立即物理删除 Redis 中所有 session 键；键仍按配置过期。MySQL 是身份权威：无论 CLI、ORM、HTTP 或直接 SQL **UPDATE** 修改上述列，触发器均执行；`REPLACE`、删除后重建同 ID 属于另一个未实现的账号生命周期操作，不能据此宣称已覆盖。
- 服务端仅从已重载的 DB 用户注入 `Principal(id,id,source='account')` 与 `g.cpa_admin`；同名请求头、请求体的 role/id 不采纳。DB/Redis 不可用时不降级为 demo/开发身份。全站 cookie HttpOnly、SameSite=Lax，生产 HTTPS 需 Secure；跨域仅 allowlist origin 可带凭据，`*` 不可带凭据。
- 账号适配器启用后，即使 `CPA_DEV_AUTH=1` 且请求来自 loopback，`X-Dev-Student-Id` 也不能注入身份；开发头只适用于正式认证未配置的本地调试。
- 禁止已有 session 用户使用公开注册；登录前后轮换 Redis session ID；登出清空 session；管理端仍经 `admin_bp.before_request` 复核。
- `RequestScope` 当前读取全局 `Config.DEMO_MODE`。部署必须由根 `.env` 的 `CPA_DEMO_MODE=false` 同时驱动全局与 app 配置；若自定义 `create_app(config_class)` 与全局 Config 的 demo 值分叉，账号适配器故障关闭为 503，不把登录身份绑定到 demo 资源。
- 新注册账号尚无简历或画像时，本人 `/api/profile/student/<id>/basic` 和 `/4d` 返回 404，`/api/report/list` 返回成功的空列表，`/api/report/resume-diagnostic` 返回 `409 needs_input`；这不表示登录失败，也不能从旧 `student_001` 借用成果。本人资料 `PATCH /api/profile/student/<id>` 只接受 JSON 对象；数组、标量或无效 JSON 在写入账号前返回 400。

## 验收

以 Flask HTTP route + 真实 MySQL/Redis 验证：注册行真实写入，role 不能伪造；有效登录后 owner API 可回读；学生访问管理员路由 403；可信 CLI 管理员可访问；DB disabled 后旧 cookie 立即失效；无/错 CSRF 拒绝变更且不写库；错密码与限速；登出旧 cookie 不能重放。测试清理只删除本次随机账号，残留 Redis session 因账号行不存在不能授权且随 TTL 过期；完整套件单独回归。

## 开源参照与适配边界

- 现有项目：`backend/app/api/_helpers.py` 的 `Principal/RequestScope`、`backend/app/api/admin.py` 服务端 gate、`user_account` 既有表；只接认证入口，不改 owner 语义。
- `opencode` 本机快照 `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/permission/index.ts`、Codex 本机快照 `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`：二者是 Agent 工具权限/执行策略，不适合替代 Flask 人类账号与 cookie 会话。
- 当前 `mkg` 安装 Flask-Login 0.6.3、Flask-Session 0.8.0、Flask-WTF 1.3.0、Flask-Limiter 4.1.1、argon2-cffi 25.1.0：复用 `flask_login.logout_user`、`argon2.PasswordHasher.verify/hash`、Flask-WTF 的 cookie 写 CSRF 和 Flask-Limiter 的登录/改密限速；SQLAlchemy 2 的 `Engine.begin()` 在同一事务中锁账号、校验旧密码及 `auth_epoch`、更新哈希并回读触发器产生的新代数。只在项目薄适配层处理 `user_account`、`Principal` 和业务 role/status，不重造会话/加密状态机。`opencode` 快照 `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/permission/index.ts`、Codex 快照 `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 属 Agent 工具授权，不承载人类账号改密；没有复用其权限流。参考：[Flask-Login](https://flask-login.readthedocs.io/en/latest/)、[Flask-Session Redis 配置](https://flask-session.readthedocs.io/en/latest/config.html)、[Flask-WTF CSRF](https://flask-wtf.readthedocs.io/en/latest/csrf/)、[Flask-Limiter 配置](https://flask-limiter.readthedocs.io/en/stable/configuration.html)、[argon2-cffi](https://argon2-cffi.readthedocs.io/en/stable/api.html)。

## 退出与异步身份刷新边界

- 普通退出只撤销当前 Redis SID，不递增账号 `auth_epoch`，不登出其他设备。保留现有接口、CSRF、角色重载与限流；无新增 MySQL 迁移或会话数据字段。
- 复用本机 Flask-Session **0.8.0** 的 `RedisSessionInterface`、`RedisSession` 与原 serializer；项目 `account_session` 薄适配只记录本请求是否从存储载入 SID，并将该 SID 的保存改成原子 `SET ... XX`。已被退出删除或已过期的 SID 不得由迟到请求重新创建；保存被拒绝时既不设置也不删除浏览器 Cookie，避免覆盖后续登录。新匿名会话、登录 SID 轮换、编码和现有 12 小时过期规则仍走原库。Redis 真故障不作为“已撤销保存”吞掉，不伪报退出成功。
- HTTP 账号响应统一 `Cache-Control: no-store`，前端认证请求也使用 `cache: 'no-store'`。否则本机 Chrome 中新的同 URL 身份查询会等待尚未结束的旧查询，妨碍退出后立即换账号。
- HTTP 前端的登录/退出开启新身份代次、使原身份查询失效；迟到成功、失败以及查询结束回调均不得覆盖新身份、清掉新查询或更新其缓存时间。退出成功后才清除本地持久化身份；失败仍保留原身份并明确提示。Mock 登录不进入这套 HTTP 状态约定。
- 兼容既有 Redis 会话载荷，不要求清库或全员改密。应用此修复须更新并重启全部处理账号 HTTP 的 Flask 进程；混用仍会无条件 SET 的旧进程不在撤销保证内。已在退出前通过授权的业务请求可能完成，本修复不追溯回滚其写入。
- 真实 MySQL/Redis/HTTP 的确定性重现及真实 Vue 账号切换入口见[用户旅程报告中的退出修复部分](../reports/2026-10-21-job-source-to-student-catalog-replay.md#退出与账号切换修复复验)。该报告记录服务/替身边界，不代表全站客户端缓存或生产多节点已审计。

## 已执行验证（本机 Docker）

- `CPA_TEST_LIVE_MYSQL=1`，mkg Python、D 盘 TEMP：`pytest backend/tests/test_account_auth_http_mysql.py -q` → **4 passed**，包括一个真 socket HTTP 往返与真实 MySQL/Redis，以及开发头不可旁路负测。
- 邻近 startup/dev auth/owner/admin 回归连同上述测试 → **32 passed**。
- 弱默认 `SECRET_KEY` 下手工探测 `/api/auth/csrf` 为 503、`/health` 为 200。旧 BCrypt 行没有迁移，正式账号开户只能生成 Argon2id。
- 后续账号全设备撤销回归：先以真实本地 HTTP 和两个独立 `requests.Session` 登录，同一账号两个浏览器在停用期间均不发请求；真 MySQL 状态 disabled→active 后 `auth_epoch` 增加 2，两个旧 cookie 对 `/api/auth/me` 和 owner API 均获 401；普通姓名修改不增加代数。角色降级/恢复也撤销旧管理员会话，需重新登录。`CPA_TEST_LIVE_MYSQL=1` 执行 `backend/tests/test_account_auth_http_mysql.py` → **6 passed**；未迁移 schema 的认证入口模拟验证 503，开发头无法旁路。
- 自助改密回归 `backend/tests/test_account_password_change_http_mysql.py`：真实本地 HTTP + MySQL/Redis 的两个已登录浏览器；无 CSRF、旧密码错误、太短/未变化的新密码不改 DB；正确改密事务内回读哈希和代数 +1，两个旧 cookie 都 401，旧密码不能登录，新密码可以。请求成功明确 `reauthentication_required=true` 且当前会话清除。该 opt-in 文件 **1 passed**，唯一随机账号行按用户名精确清理。数据库/Redis 不可用时不保留成功响应，不支持找回/邮件重置。

### 自助改密请求

已登录用户先获取 `/api/auth/csrf`，发送 `POST /api/auth/change-password`，请求头 `X-CSRFToken`，JSON 为 `{"current_password":"旧密码","new_password":"12 至 128 字符的新密码"}`。旧密码必须正确，新密码必须不同；成功 `200 {"success":true,"reauthentication_required":true}`，所有设备（包括本次浏览器）均须重新登录。当前密码错误或会话失效返回 401，形状/长度错误或相同密码返回 400，限速返回 429，账号服务故障返回 503。服务端不接受账号 ID/role 字段来改他人密码。

## 既有 MySQL volume 迁移与故障恢复

1. 维护窗口先备份 `user_account` 表定义/数据与该表触发器定义。不要清空 Redis 或重建 Docker volume；本迁移不回填、更改或删除账号行。
2. 在仓库 `backend/` 目录，以 `mkg` 环境执行 `python -B -m flask --app app migrate-account-session-epoch`（Windows 可用 `D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m flask --app app migrate-account-session-epoch`）。命令检查信息模式，缺列时 `ALTER TABLE` 追加默认 0 的 `BIGINT UNSIGNED auth_epoch`，缺触发器时创建 `cpa_user_account_auth_epoch_v1`，再校验两者；重复执行无害。
3. **重启所有后端 worker**：认证 schema readiness 在进程启动时检查并缓存；未迁移、数据库不可达或无法读取触发器元数据时账号入口 503，已有 cookie 不授权，不能靠开发头绕过。新 Docker volume 由 `init_mysql.sql` 与 `init_account_session_epoch_trigger.sql` 顺序安装；Python 旧初始化脚本之后也须运行迁移命令。
4. 迁移前颁发的会话不含代数，升级后自动失效，用户需重新登录。部署后确认新登录和 owner/admin 路由，并以 `CPA_TEST_LIVE_MYSQL=1` 的账号测试做本地回放；生产多节点仍需逐节点验证同一版本与同一 MySQL/Redis。
5. 如果 DDL 在加列后、加触发器前中断，账号认证保持 503；修复 MySQL 权限/可用性后**重跑同一命令**再重启。若同名触发器存在但定义不符，命令失败而不是悄悄覆盖；先人工审查冲突定义。不要在旧登录代码继续运行时删除列或触发器。代码回退时先停止/关闭账号认证流量，保留 additive schema，恢复安全版本后再重新开放；回到不校验代数的旧版本会使闲置会话复活，不能作为安全回退。

边界：请求已通过身份检查后，与并发停用之间尚无逐个业务写入的同事务锁；停用提交后的**下一次请求**被拒绝。删除后重建同一 `user_account.id`、`REPLACE`、直接篡改 DB/Redis、删除触发器或使用滞后只读副本不在已验证撤销保证内；当前验收使用单主 MySQL 和真实 Redis。

## 非目标与遗留风险

本机 `backend-dev` 前端现已使用真实账号 Cookie/CSRF；浏览器注册、登录、刷新恢复、已登录上传请求与退出已回放，演示版 Pages 仍使用 Mock。此切片不证明所有功能页都已完成真实后端接线，也不证明生产域名/HTTPS、账号恢复/审计/多因子已完成。账号与旧简历不能仅凭同名自动合并。
