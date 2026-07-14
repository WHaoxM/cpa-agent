# CPA-Agent Deployment

部署文件按层组织：

- `docker-compose.yml`: 基础层，定义 MySQL、Neo4j、Redis 和 volumes，不暴露宿主机端口。
- `docker-compose.dev.yml`: 开发覆盖层，暴露本机联调端口。当前主要使用这一层。
- `docker-compose.prod.yml`: 生产覆盖层，加入 backend 容器，数据库只走 Compose 内部网络。

当前 dev 级 Docker 部署只负责数据库依赖：MySQL、Neo4j、Redis。后端服务在本机 Python 环境运行，避免把还在硬化中的应用镜像、模型缓存、LLM 密钥和前端构建混进数据库联调。

## 目标

- 一条命令启动后端所需数据库依赖。
- 使用固定、可预测的本地端口，匹配仓库根目录 `.env` / `.env.example`。
- MySQL 在新 volume 上自动建表并导入基础 seed。
- Neo4j 默认只启动服务，图谱初始化用本机 Python 脚本执行，避免容器 `cypher-shell -f` 中文编码问题。
- Milvus 开发默认走 Lite 文件模式：`MILVUS_URI=./milvus_data/milvus.db`。

## 启动数据库

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d mysql neo4j redis
docker compose -f docker-compose.yml -f docker-compose.dev.yml ps
```

## 端口

| Service | Host | Container | Purpose |
|---|---:|---:|---|
| MySQL | `13306` | `3306` | 业务数据、任务、会话落库 |
| Neo4j HTTP | `17668` | `7474` | Neo4j Browser |
| Neo4j Bolt | `17687` | `7687` | 后端图数据库连接 |
| Redis | `16379` | `6379` | 会话与缓存 |

后端本机环境建议写入仓库根目录 `.env`。当前 `backend/app/config.py` 优先读取仓库根 `.env`：

```env
MYSQL_HOST=localhost
MYSQL_PORT=13306
MYSQL_USER=root
MYSQL_PASSWORD=!Rootmysql123
MYSQL_DATABASE=cpa_agent

NEO4J_URI=bolt://localhost:17687
NEO4J_USER=neo4j
NEO4J_PASSWORD=neo4jpass

REDIS_URL=redis://localhost:16379/0
MILVUS_URI=./milvus_data/milvus.db
```

## MySQL 初始化

MySQL 只在 `mysql_data` volume 首次创建时自动执行：

- `backend/scripts/mysql/init_mysql.sql`
- `backend/scripts/mysql/seed_cold_start_1.sql`
- `backend/scripts/mysql/seed_cold_start_2.sql`
- `backend/scripts/mysql/seed_cold_start_3.sql`
- `backend/scripts/mysql/seed_skill_supplement.sql`
- `backend/scripts/mysql/seed_soft_skills.sql`
- `backend/scripts/mysql/seed_proficiency_criteria.sql`

这些脚本在 `USE cpa_agent;` 后显式执行 `SET NAMES utf8mb4;`，避免 Docker entrypoint 导入中文 seed 时按错误客户端字符集写入乱码。

如果要重新初始化 MySQL：

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml stop mysql
docker compose -f docker-compose.yml -f docker-compose.dev.yml rm -f mysql
docker volume rm cpa-agent-dev_mysql_data
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d mysql
docker exec cpa-agent-dev-redis-1 redis-cli DEL cache:career:landscape
```

最后一行用于清理职业全景缓存；否则 MySQL 已重建但 API 仍可能返回 Redis 中的旧数据。如果 Compose project name 不是 `cpa-agent`，先用 `docker volume ls` 查看实际 volume 名称。

验证中文 seed 是否正确：

```powershell
docker exec cpa-agent-dev-mysql-1 mysql --default-character-set=utf8mb4 -uroot -p"!Rootmysql123" cpa_agent -e "SELECT category, HEX(category), role_name FROM career_insights LIMIT 3;"
```

## Neo4j 初始化

Neo4j 脚本不默认自动执行。首次需要基础图谱时运行：

```powershell
cd backend
python -B scripts/neo4j/init_neo4j.py
```

注意：不要使用容器内 `cypher-shell -f` 直接执行当前中文 Cypher 脚本；在当前镜像环境下会把部分中文字面量写成 `??????`，导致唯一约束冲突。需要重置图谱时：

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml stop neo4j
docker compose -f docker-compose.yml -f docker-compose.dev.yml rm -f neo4j
docker volume rm cpa-agent-dev_neo4j_data cpa-agent-dev_neo4j_logs
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d neo4j
cd backend
python -B scripts/neo4j/init_neo4j.py
```

## 后端本机启动

当前开发环境使用 Conda 环境 `mkg`。

```powershell
conda activate mkg
cd backend
python -m pip install -r requirements.txt
$env:CPA_DEMO_MODE='true'
python -B run.py
```

基础检查：

```powershell
curl http://localhost:5001/health
curl http://localhost:5001/api/pipeline/list
```

完整后端测试：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_ADDOPTS='-p no:cacheprovider'
python -B -m pytest backend/tests
```

## 常用运维命令

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml ps
docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f mysql
docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f neo4j
docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f redis
docker compose -f docker-compose.yml -f docker-compose.dev.yml restart mysql neo4j redis
docker compose -f docker-compose.yml -f docker-compose.dev.yml down
```

连接检查：

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec mysql mysqladmin ping -h 127.0.0.1 -uroot -p"!Rootmysql123"
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec neo4j cypher-shell -a bolt://127.0.0.1:7687 -u neo4j -p neo4jpass "RETURN 1"
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec redis redis-cli ping
```

## 不在当前 dev Compose 中的内容

- Backend 容器：暂不纳入 dev compose，后端在本机跑，便于调试 Python 依赖和模型文件。
- Frontend 容器：前端使用 `npm run dev` 或静态部署。
- Milvus Server：开发默认使用 Milvus Lite 文件模式；需要独立 Milvus 时再单独设计 server compose。
- 模型下载与缓存：不通过 compose 管理，避免数据库启动被模型体积和网络状态拖慢。

## Production Layer

生产层会把 backend 容器加入 Compose：

```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

生产层要求至少提供：

```env
SECRET_KEY=<strong-secret>
LLM_API_KEY=<provider-key>
NEO4J_PASSWORD=<strong-neo4j-password>
MYSQL_ROOT_PASSWORD=<strong-mysql-password>
CPA_DEMO_MODE=false
```

当前生产层只作为分层骨架，优先级低于 dev 数据库联调。正式上线前仍需补齐反向代理、HTTPS、日志轮转、备份和资源限制。
