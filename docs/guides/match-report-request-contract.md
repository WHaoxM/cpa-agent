# Match / Report 请求体边界

`POST /api/match/run` 与 `POST /api/report/generate` 都先解析并确认请求者的 Principal，再校验请求体。请求体必须是 JSON 对象；`job_portrait_id` 或兼容别名 `job_id` 必须解析为 1–64 字符的非空文本（与发布表的 ID 列长度一致，首尾空白会去除）。顶层数组、数字、字符串、null，以及对象/数组/数字/布尔/空白串或超长 job ID 均返回 HTTP 400。

这些输入错误在调用 `PipelineRunner`、生成 report ID 或写入报告/任务持久层之前结束。未认证请求仍按身份边界返回 401，不因请求体形状得到信息差异。回归验证：`python -B -m pytest backend/tests/test_match_report_input_contract.py`；该测试使用已认证 Flask 路由，并将 Runner 替换为立即失败的哨兵，证明拒绝发生于任务持久化之前。
