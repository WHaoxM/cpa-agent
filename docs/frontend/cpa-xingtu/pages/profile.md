# 个人中心

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/profile` |
| name | `profile` |
| roles | 需登录 |
| immersive | false |
| public | false |

## 页面职责

查看/编辑个人资料与偏好；退出登录。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 读资料 | 进入页 | `GET /api/profile/student/student_001` | — | student_profile | `profile/student.detail.json` | backend-ready |
| 读基础字段 | 轻量 | `GET /api/profile/student/student_001/basic` | — | basic | `profile/student.basic.json` | backend-ready |
| 更新资料 | 保存 | `PUT /api/profile/student/student_001`（理想） | 见下 | ok | — | backend-gap |
| 退出登录 | 退出 | — | 清 session | — | — | —（本地） |

### 理想更新 Request

```json
{
  "display_name": "钟同学",
  "location_preference": "北京/上海",
  "bio": "大三，前端方向"
}
```

**说明:** 后端现有 GET student，**无**稳定 update 路由 → 更新标 backend-gap；可读档案用 backend-ready。

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| `api.updateProfile` 占位 | ProfileView TODO |
| 头像上传 UI | 本地 |

## Demo 说明

- 展示 `student_001`；编辑仅改 Pinia，刷新可丢

## 后续实现备注

- 可先只读对接 GET；写接口另开后端任务
