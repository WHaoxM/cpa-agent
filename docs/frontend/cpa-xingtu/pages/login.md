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

用本地用户表校验账号并写入会话，进入 `/app/dashboard`（无后端鉴权）。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 加载可选用户 | 进入页（若展示快捷登录） | — | — | 用户列表 | `public/fixtures/auth/users.json` | backend-gap |
| 提交登录 | 点击登录 | —（本地校验） | 见下 | 见下 | 同上 | backend-gap |
| 快捷学生登录 | 一键填入 student | — | username/password | 会话 User | 同上 | backend-gap |

### Request / Response 展开

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

**Client 建议:** `auth.loginLocal(username, password)` — 仅 mockAdapter；http 模式仍可走同一 fixture（产品接受无 `/api/auth`）。

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| 记住登录 | localStorage token/session |
| 已登录访问 `/login` | 路由守卫重定向 dashboard |
| 密码错误提示 | 前端字符串比对 |

## Demo 说明

- 学生：`student001` / `123456` → `id=student_001`
- Pages：始终 mock 登录；不调用后端

## 后续实现备注

- 现有：`src/mock/data.ts` → `mockUsers`、`getCurrentUser`
- 不引入真实 JWT，除非产品改口
