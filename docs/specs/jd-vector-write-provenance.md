# JD 向量写入来源与版本边界

## 当前 HTTP 契约

`POST /api/pipeline/ext/vector/store/jd` 仅接受服务端管理员身份，JSON 请求体严格为：

```json
{
  "job_id": "job_…",
  "publication_version": "v1",
  "content_sha256": "<当前发布快照的 64 位小写 SHA-256>"
}
```

旧的 `text_chunks`、`position_name` 及其它额外字段一律拒绝。服务端同时读取请求指定版本和该岗位最新发布版本，只有二者仍是同一条当前 `published` 快照、且摘要与调用方预期摘要完全一致，才会继续；撤回、版本过期、未知岗位或摘要不符都不会访问 Milvus。

向量正文和标题只从 `job_catalog_publication.snapshot_json.source_text`、`position_name` 读取。Milvus `jd_vectors` 每条 metadata 形状为：

```json
{
  "job_portrait_id": "job_…",
  "position_name": "…",
  "publication_version": "v1",
  "content_sha256": "<job_catalog_publication.content_sha256>"
}
```

`content_sha256` 是完整不可变发布快照摘要，不是来源正文摘要。读取方须以 `job_portrait_id` 查询当前正式发布快照，并同时比对 `publication_version` 和 `content_sha256`；任一缺失或不匹配的旧索引命中都不得作为可信来源。索引文本只可作为排序信号，不能直接回显给用户。

`/api/match/hybrid-candidates` 和公开 `/api/pipeline/ext/vector/search` 的读取侧现在都实行上述三字段门禁。旧索引不会因仅有正确岗位 ID 而复活；真实 Milvus 历史记录尚未迁移或清理，因此在重建前可能降低向量召回，关键词已核验候选仍可独立返回。

## 运行边界

- 该端点每次只索引一个岗位快照的一份完整 `source_text`，不接受浏览器分块，也不声称做了语义切块；若后续引入切块，需要保留相同的三项版本绑定 metadata，并明确切块策略版本。
- 写入不删除历史 Milvus 向量。撤回或新版本会使旧向量失去当前版本匹配资格；读取路径必须实时回查发布仓库，不能信任 Milvus 自身状态。
- 这次改动没有迁移旧索引或验证当前 Milvus 集合内每一条历史记录。没有上述绑定字段的旧记录应由读取侧拒绝，并在单独的可回滚重建计划中清理/重建。
- 正式发布以外的仅来源展示岗位（如 `source_csb_`）不能通过 `read_published_job` 校验，因此也不能写入可评分岗位向量集合。

## 回归验证

`backend/tests/test_p0_admin_vector_scope.py` 覆盖旧客户端正文注入被拒、合法快照正文和 metadata 精确写入、过期版本在访问 Milvus 前被拒。路由测试使用 Milvus 替身验证边界，不代表真实 Milvus 写入或历史集合迁移已验收。

补充真实适配器验收（2026-10-13）：先以隔离的临时 Milvus Lite 数据库复现 `MilvusClient.insert` 把整列数组当单行发送，向量字段报 `DataNotMatchException`；改为一实体一字典后，真实写入/检索成功。再把真实 MySQL 的唯一合成发布快照、Flask JD 写入/公开搜索路由和隔离 Milvus Lite 串接，发现 PyMilvus 返回的主键容器不能直接 JSON 序列化，路由虽已写入却 500；适配器将主键转为普通列表后，HTTP 写入 200、公开搜索只返回当前发布快照投影，追加撤回版本后同一索引命中不再公开。`CPA_TEST_LIVE_MYSQL=1` 与 `CPA_TEST_LIVE_MILVUS_LITE=1` 下运行 `backend/tests/test_milvus_lite_jd_roundtrip.py` 为 **2 passed**；测试发布行与临时索引按唯一夹具清理。这里使用确定性测试向量而非真实 BGE-M3，**不证明语义召回质量或当前生产 Milvus 历史索引状态**。
