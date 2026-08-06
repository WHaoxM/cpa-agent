# 消息

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/messages` |
| name | `messages` |
| roles | 需登录 |
| immersive | false |
| public | false |

## 页面职责

站内通知/消息列表；共享运营公告 + 按用户已读/删除。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 加载消息 | 进入页 | `GET /api/messages?user_id=student_001` | — | messages[] | `home/messages.list.json` | backend-ready |
| 标记已读 | 点击 | `POST /api/messages/<id>/read` | `?user_id=` | ok | — | backend-ready |
| 删除 | 删除 | `DELETE /api/messages/<id>` | `?user_id=` | ok | — | backend-ready |

### 理想列表 Response

```json
{
  "success": true,
  "data": [
    {
      "id": "msg_001",
      "title": "报告已生成",
      "body": "report_001 已可查看",
      "read": false,
      "created_at": "2026-07-18T00:00:00Z"
    }
  ]
}
```

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| 空状态 / 占位列表 | 视图内 mock |

## Demo 说明

- Pages 用空列表或静态 fixture；不宣称已接通

## 后续实现备注

- 无后端计划前保持 mock；勿在 catalog 外发明冲突路径
