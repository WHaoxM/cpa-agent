# 私有岗位数据集到人工发布的来源绑定

状态：2026-10-14 后端切片。它让**单条**已暂存 `ds_` 记录成为管理员审核的候选，不执行批量导入、建图、模型自动批准，也不把私有上传认定为官方来源。

## 记录与请求契约

管理员上传的 UTF-8 JSON 数组仍按 `dataset/upload` 的 2 MiB、1–1000 条上限暂存。欲进入可评分岗位人工预检的一项须为 `{"title":"与岗位名称相同","snapshot":{...}}`。`snapshot` 采用既有 [岗位人工发布](admin-job-publication-http.md) 候选字段：HTTP(S) 来源 URI、原文与文本 SHA、获取时间、许可、逐字段引文、技能和完整硬约束；不得带 `reviewed_*` 或 `staged_*` 服务端元数据。只含 `title` 的旧 dataset 行仍可暂存和回读，但不能发布。

`POST /api/admin/job-publications/preview` 的 dataset 模式发送 `job_portrait_id`、`publication_version`、`source_dataset_id`（`ds_...`）、`source_record_index`（0 起）、`expected_dataset_sha256`（上传返回的文件 SHA-256）。可选 `expected_record_sha256`，若提供也须一致。**不得发送 `snapshot`**。服务端从 MySQL 找待处理且未入库的 dataset 行，并从限定路径重读文件字节、长度、文件 SHA、条目数及所选记录；验证 title 与 `snapshot.position_name` 一致，注入不可由客户端自报的 `source_provenance_kind=admin_staged_dataset`、dataset ID/位置/文件 SHA/记录 SHA。沿用可评分字段门禁，返回待审核快照、候选摘要、记录摘要和十分钟 token。预检不写发布表。

同一正式管理员再次 `POST /api/admin/job-publications` 时，发送完全相同的 ID/版本/dataset 引用，加预检返回的 `expected_record_sha256`、token、候选/原文摘要及四项布尔人工确认；仍**不得发送 `snapshot`**。服务端在验签前再次重读单条来源，提交事务中锁定 dataset DB 行后再读一次，并比较原候选摘要；任何更换位置、记录、文件或状态均拒绝，不写 `job_catalog_publication` 或审计成功行。正式发布快照保留 server-owned dataset/record SHA，审计与发布同行提交。来源文件损坏/缺失返回 503；dataset 版本/记录摘要冲突返回 409；字段不完整或不可评分沿用 400/422。原有独立人工外部来源 `snapshot` 模式仍在，不得通过客户端直接自报 dataset 绑定字段；其来源真实性仍是管理员人工核对声明，不应被误称为同等服务端文件绑定。

发布者仍必须是平台自建、当前数据库状态有效的管理员账号，Cookie/CSRF 和 `confirm_review` 四项均不能被 dataset 摘要代替。dataset 是**未核验的私有运营资料**，`source_uri` 看起来像官方域名也不改变信任级别；系统不通过此流程抓取网页、核对许可/招聘时效、自动建图或运行评分。发布之后 L3/L4 仍按已发布、可评分门禁独立处理，当前匹配质量仍为 `policy_estimate/partial`。

## 回归证据与限制

`CPA_TEST_LIVE_MYSQL=1` 时运行 `D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest -q backend/tests/test_admin_dataset_publication_http_mysql.py backend/tests/test_admin_job_publication_http_mysql.py`。测试使用真实 TCP Flask HTTP、自建账号登录/CSRF、真实 MySQL/Redis、D 盘临时文件；核对学生越权、伪造 dataset 元数据、坏摘要、来源文件变更、首次校验与事务内再次校验之间变更、正式发布/审计/数据库快照。合成岗位行和账号在 finally 中清除。此测试不证明真实招聘源、人工语义审核质量或前端页面已接入。

本机 2026-10-14 实跑上述组合 **3 passed**；另加模型审核回读及账号暂存路径组合 **5 passed**。均为测试服务和合成数据，未迁移正式库、未发布真实岗位。运行中的长期 Flask/worker 仍需在部署窗口重启才能加载此变更。

文件系统和 MySQL 无原子共同事务：事务内核验的是当时读到的**原文字节**；管理员随后删除暂存文件时，发布快照及文件/记录摘要继续可审计，但原始文件不再能通过暂存 API 回读。运营若要长期重审，需把来源卷和 MySQL 成对备份，或进一步引入不可删除来源存档。此切片没有把 `dataset/delete` 改成发布引用保护，也没有迁移旧 CLI 的独立信任路径。
