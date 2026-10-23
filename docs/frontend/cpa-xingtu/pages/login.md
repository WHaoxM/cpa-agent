# 登录

## 路由

| 项 | 值 |
|----|-----|
| path | `/login` |
| name | `login` |
| roles | public |
| immersive | false |
| public | true |

## 页面职责

保留山水背景、产品展示、登录／注册卡片和原入场动画，不用简化页面代替真实接入。HTTP 模式通过平台 Cookie 会话认证；显式 Mock 模式才使用本地 fixture。旧版“HTTP 仍可走 fixture、无后端鉴权”不再适用。

完整账号、CSRF、撤销及限流约定由[自建账号规格](../../../specs/self-managed-account-auth.md)维护，本页不另立认证协议。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 身份恢复 | 首次进入 | `GET /api/auth/me` | Cookie | 服务端身份，未认证401 | 不使用 | 已接入 |
| 提交登录 | 点击登录 | `GET /api/auth/csrf` → `POST /api/auth/login` | username/password + CSRF | user／失败原因 | HTTP 不使用 | 已接入 |
| 学生注册 | 注册表单 | `GET /api/auth/csrf` → `POST /api/auth/register` | username/password/name + CSRF | 成功／失败原因 | 不使用 | 已接入 |
| 退出 | 首页账号菜单 | `GET /api/auth/csrf` → `POST /api/auth/logout` | Cookie + CSRF | 成功后退出 | HTTP 不使用 | 已接入 |

### 显式 Mock 模式的历史样例（不是 HTTP 凭据）

#### 提交登录（理想本地契约）

**Request（表单）**

```json
{
  "username": "student001",
  "password": "123456"
}
```

**Response（写入 Pinia / localStorage 的 User）**

```json
{
  "id": "student_001",
  "username": "student001",
  "displayName": "钟同学",
  "role": "student"
}
```

**Fixture:** `cpa-xingtu/public/fixtures/auth/users.json`

```json
{
  "success": true,
  "data": [
    {
      "id": "student_001",
      "username": "student001",
      "password": "123456",
      "displayName": "钟同学",
      "role": "student"
    },
    {
      "id": "admin_001",
      "username": "admin",
      "password": "admin123",
      "displayName": "管理员",
      "role": "admin"
    }
  ]
}
```

`src/api/auth.ts` 的 `loginLocal` 为兼容名称：HTTP 分支实际调用平台认证接口，不读取上述 fixture，也不在失败时回退到本地校验。真实模式不显示演示账号快捷入口。

## 模式边界

| 状态 / 行为 | 说明 |
|-------------|------|
| HTTP 身份 | 服务端 Cookie 会话；不以 localStorage 中的身份自证登录 |
| 已登录访问 `/login` | 路由检查本人身份后重定向 dashboard |
| 密码错误提示 | HTTP 使用服务端错误；Mock 才做本地比对 |
| 退出完成 | 等待服务端确认；浏览器回放另读 `/api/auth/me` 核对401，不只看跳转 |

## Demo 说明

- 学生：`student001` / `123456` → `id=student_001`
- 未显式配置 HTTP 的 Pages 静态演示默认 Mock，不调用后端；不能据此推断本机 `backend-dev` 模式。

## 冷加载与字体边界

- `index.html` 的远程字体 CSS 是视觉增强，不得成为 HTML 解析、Vue 启动或登录表单出现的前提。
- 字体样式表初始 `media="print"`，实际 `load` 后切为 `all`；失败时沿用已有系统字体回退，不自动刷新或重试登录。`display=swap` 不能单独解除 CSS 的启动阻塞。
- 所有原字体族和字重在同一个入口保留；`base.css` 不重复 `@import` 远程字体。设计 token、布局和动画不变。
- 字体迟到可以替换外观，不可重建表单或丢弃输入。不要以清浏览器缓存、增加固定等待或放宽身份校验处理空白。

若再次发生空白，先保存首轮 DOM、资源与异常：区分 HTML 尚无 `body`、模块加载失败、身份检查等待和页面呈现问题，不能把本次字体阻塞当成所有空白的唯一原因。

## 回放与证据

在仓库根目录，使用项目 `mkg` 环境运行 `backend/tests/manual/login_cold_load_browser_probe.py`。逐次创建独立临时浏览器配置；`--fonts normal|hold|block` 验证正常、字体持续等待及断网，`--quick-refresh` 在主入口响应后刷新，`--release-fonts-after-render` 验证字体恢复与输入保留。`--account-file` 只读取本机合成账号凭据，不提交该文件或输出密码。

每次首次失败都保留并让命令失败；失败后放行字体只用于定因，不改记为通过。正常配置与显式故障注入分别记录，不把后者当真实上游可用性证明。

当前证据见[同一回放报告的登录冷加载修复补记](../../../reports/2026-10-22-student-resume-builder-replay.md#登录页冷加载空白诊断与修复)。这轮只验证桌面 Chrome、现有 Vite／Flask 与真实账号会话；不据此宣称全浏览器、生产静态托管或所有启动故障均已验收。
