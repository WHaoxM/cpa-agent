# Landing

## 路由

| 项 | 值 |
|----|-----|
| path | `/` |
| name | `landing` |
| roles | public |
| immersive | false |
| public | true |

## 页面职责

品牌落地页：介绍产品并引导至登录/进入应用。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 浏览落地 | 打开站点 | — | — | 静态文案/资源 | — | — |
| 去登录 | CTA | — | `router.push('/login')` | — | — | — |
| 已登录直达 | CTA | — | `router.push('/app/dashboard')` | — | — | — |

无后端 API。

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| 全部视觉与文案 | 前端静态 |

## Demo 说明

- Pages 入口；不依赖 fixtures

## 后续实现备注

- 无
