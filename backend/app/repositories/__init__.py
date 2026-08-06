"""
数据访问层 (Repositories)
集中 SQL/Cypher 查询, 与 API 层解耦

设计原则:
1. Repository 方法返回纯数据 (dict/list), 不含 HTTP 概念
2. Repository 不做业务逻辑判断 (如 demand_level 映射), 那是 API 层的事
3. 方法名用业务语义 (save_job 而非 insert_saved_job)
4. 用 mysql_client 的 execute/execute_write, 与现有模式一致
5. 每个业务域一个文件, 不跨域合并
"""
