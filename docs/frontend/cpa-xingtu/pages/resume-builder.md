# 快速制作简历

## 路由

| 项 | 值 |
|----|-----|
| path | `/app/student/resume-builder` |
| name | `student-resume-builder` |
| roles | student |
| immersive | true |
| public | false |

## 页面职责

本地编辑简历结构；可选粘贴/上传解析填充字段。

## 用户操作表

| 操作 | UI 触发 | Method Path | Request 样例 | Response 样例 | Fixture | BE 状态 |
|------|---------|-------------|--------------|---------------|---------|---------|
| 粘贴解析 | 导入文本 | `POST /api/pipeline/ext/parse/text` | 见 career-navigation | 结构化字段 | `pipeline/parse.text.json` | backend-ready |
| 上传解析 | 选文件 | `POST /api/pipeline/ext/parse/upload/resume` | multipart | text/blocks | `pipeline/parse.upload.json` | backend-ready |
| 真 L0 入库 | 「深度解析」 | `POST /api/profile/student/upload` | multipart | task_id | — | heavy-excluded |
| 保存简历 | 保存 | —（localStorage） | resume JSON | — | `pipeline/resume.local.json` | backend-gap |
| 导出 PDF | 导出 | — | — | — | — | backend-gap |

### parse/text

同 [career-navigation.md](career-navigation.md)；Client：`api.pipeline.parseText`。

## 本地-only

| 状态 / 行为 | 说明 |
|-------------|------|
| 表单分节编辑 | 前端 |
| `seedResumeStore` | `resumeSeed.ts` → localStorage |
| 打印样式 | CSS |

## Demo 说明

- 预置钟梓珉简历 seed；解析用 fixture，不跑 L0

## 后续实现备注

- 主路径保持本地编辑 + 轻量 parse；上传入库二期
