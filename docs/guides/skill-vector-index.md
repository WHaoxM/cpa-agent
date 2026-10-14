# 公共技能向量索引重建

`skill_vectors` 的权威来源是当前 MySQL `skill_taxonomy`，不是历史生成 JSON 或 LLM 扩写。`backend/scripts/milvus/seed_skill_vectors.py` 现在只读 `id/skill_name/category/sub_category/tier/dimension`；实际嵌入文本只含技能名、类别、子类。`description`、`heat_score`、`proficiency_criteria` 等可能混有未经核验的市场或能力叙述，**不进入文本或向量**，脚本也不更新 SQL。

每条向量的 JSON metadata 带 `source=mysql.skill_taxonomy`、`source_format=taxonomy-structure-v1`、`taxonomy_id`、结构字段、`embedding_model` 与内容 SHA-256。当前 Milvus 集合主键是自动 ID，因此重建器先查询现有行：相同来源/摘要跳过；变更先插入并回读全部目标，再删除旧版、重复或源中已删除的受管行，最后回读核验。插入或删除中断可以重跑；不识别的旧行默认触发拒绝，不会被悄悄收编或删除。确需与旧行并存时使用显式 `--allow-unmanaged`，输出会报告其数量；这不代表旧行可信。当前不支持并发重建者，执行时须保证没有另一个写入器操作该集合。

适配参考：本机 `pymilvus 3.0.1` 的 `pymilvus.milvus_client.milvus_client.MilvusClient` 已提供 `query`、`insert`、`delete`、`get_load_state` 与 `load_collection`；本项目复用这些原语，只在来源识别/重放状态上加薄适配，不另造通用向量客户端。已有集合为 `auto_id=True`，不能直接用 taxonomy ID upsert；若未来改为确定性主键，须显式迁移并独立验证兼容边界。

在 `backend/`、`mkg` Python 环境下先只读预检：

```powershell
python -B -m scripts.milvus.seed_skill_vectors
```

核对源行数后显式执行；若源行数变化则拒绝，先重新预检。模型不可用、向量维度不对或 Milvus 查询失败都会报错，不会随机填充或返回伪成功：

```powershell
python -B -m scripts.milvus.seed_skill_vectors --apply --expect-count 99
```

`--milvus-uri <absolute-.db-path>` 可指定隔离 Milvus Lite；脚本默认使用当前 `CPA_MILVUS_URI` 配置。重复执行应显示 `inserted=0, deleted=0`。回滚只可按 `source` 精确筛选受管行或恢复索引备份，不要删除整个混合集合。旧版脚本的 LLM 丰富描述以及 MySQL `description` 回写已停用；旧时已写入 SQL 的描述不因本次重建而自动清理。

## 管理员写入与公共读取的同一来源门禁

`POST /api/pipeline/ext/vector/store/skills` 不再接收浏览器提交的 `skills:[{name,description,...}]` 文本；该旧格式在触达 MySQL/Milvus 前返回 400，避免把无来源的文字追加到正式技能索引。管理员现在只可显式提交 `{"source":"mysql.skill_taxonomy","expected_source_count":99}`，服务端读当前 taxonomy 并执行与上方 CLI 相同的可重放 reconcile；源行数不符或存在历史未受管索引行时返回 409，不报“写入成功”。首次大量构建可能超过 HTTP 超时，仍应优先使用 CLI；该接口是小规模同步核验/修复入口，不是假异步任务。

公开 `/api/pipeline/ext/vector/search` 的 `skill` 命中会逐项用当前 taxonomy 的结构字段、嵌入模型名、完整 metadata 和文本摘要重新绑定；Milvus 原始未受管/旧版正文不直接对外返回。先多取有限命中再过滤，保留有效候选；若混入无法核对的行，返回 `data.status=partial`、`rejected_unverified_hits.skill_vectors` 与重建提示，而非把索引污染伪装为可靠完整结果。taxonomy 不可读时单独技能查询 503；`all` 可保留另一可验证集合并标部分结果。这个绑定不能保证向量排序质量，且当前每次公共技能查询会扫描 taxonomy 结构字段，规模扩大后须做有版本失效的快照/批量查询优化。

## 2026-10-14 本机核验

- 真 MySQL 读出 99 行。隔离临时 Milvus Lite + **仅测试用**确定性 1024 维 embedding 的读取、插入、实际搜索、重复执行、结构字段单项变化再恢复，以及“插入首条后失败再重跑”测试：`CPA_TEST_LIVE_MYSQL=1`、`CPA_TEST_LIVE_MILVUS_LITE=1` 下 `python -B -m pytest backend/tests/test_skill_vector_index.py -q`，3 passed。由于本机 C 盘临时目录已满，本次用 `TEMP/TMP` 与 pytest `--basetemp` 指向 D 盘；此前 C 盘上的合并测试因空间不足失败，不是索引代码回归。
- 本机离线缓存 `BAAI/bge-m3` 单条向量实测 1024 维；默认配置的 `backend/milvus_data/milvus.db` 原先受管/非受管行均为 0。执行 `--apply --expect-count 99` 后 `inserted=99, verified=99, unmanaged=0`，新进程重放 `inserted=0, deleted=0, verified=99`。真实 BGE-M3 查询 `Python` 的首条结果为对应 Python 技能行。这只证明当前索引的供给和一次检索，不证明全部语义匹配质量或下游用户旅程。
- 独立进程重新打开 Lite 时既有集合可能处于 `released`；重建器在读之前调用 `load_collection`，通用 `MilvusClient.search` 首次查询检查加载状态并按需加载，后续热路径不重复做状态 RPC；外部释放后仅对显式 `released` 错误重试一次。隔离 Lite 的冷启动、两次热查询和外部释放后恢复测试通过；默认索引的新进程真实 BGE-M3 `Python` 查询亦首条命中 Python。尚未把这一点等同于完整用户端语义旅程或召回质量验收。
- 又以当前 Flask 应用的公开 `POST /api/pipeline/ext/vector/search`、`collection=skill,query=Python,top_k=3` 真 HTTP 入口、真 BGE-M3 与默认 Milvus Lite 重放：`CPA_TEST_LIVE_SKILL_INDEX=1` 运行 `backend/tests/test_skill_vector_default_http.py`，**1 passed**；响应 `status=complete`、无失败集合，首条 metadata 的 `source=mysql.skill_taxonomy,skill_name=Python`。模型冷启动约 15 秒，已将原日志误把 Unix 时间戳显示为加载耗时的问题改为单调时钟耗时；这不是接口低延迟保证。
- 进一步用真实 TCP HTTP、MySQL 99 行、隔离 Milvus Lite 与测试专用确定性向量验证：旧浏览器自报技能正文 400，源行数核对的管理员同步幂等，模拟历史未受管近邻后公共搜索只保留 Python 真来源并标 `partial`，再次同步因未受管行 409。`CPA_TEST_LIVE_MYSQL=1,CPA_TEST_LIVE_MILVUS_LITE=1` 的 `backend/tests/test_skill_public_provenance_http_mysql.py` **1 passed**；默认索引未修改。另用真实 BGE-M3/default Lite 重跑公开 HTTP **1 passed**，验证新读取门禁不误删 99 条当前可信索引。
