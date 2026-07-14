"""scripts/mysql 共享的数据库连接辅助模块

所有 scripts/mysql/ 下的脚本统一通过本模块获取数据库连接参数,
避免在 .py 文件中硬编码密码 / 主机 / 端口等敏感配置。

合规范例参考: scripts/mysql/init_mysql.py
"""
import os
import sys
from pathlib import Path

# 把 backend/ 加入 sys.path, 使 from app.config import Config 可用
# _db_helper.py 位于 backend/scripts/mysql/, 需上溯 3 级到 backend/
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.config import Config  # noqa: E402


def get_mysql_conn(database=None):
    """返回 pymysql 连接 (密码走 Config, 不出现在代码字面量中)

    Args:
        database: 指定数据库, None 则用 Config.MYSQL_DATABASE
    """
    import pymysql
    return pymysql.connect(
        host=Config.MYSQL_HOST,
        port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER,
        password=Config.MYSQL_PASSWORD,
        database=database or Config.MYSQL_DATABASE,
        charset='utf8mb4',
    )


def get_mysql_cmd(database=None, with_table=False):
    """返回 mysql 命令行参数列表 (不含密码)

    密码应通过 get_mysql_env() 返回的 MYSQL_PWD 环境变量传入 subprocess,
    避免出现在进程列表中。
    """
    cmd = [
        "mysql",
        "-h", Config.MYSQL_HOST,
        "-P", str(Config.MYSQL_PORT),
        "-u", Config.MYSQL_USER,
        "--default-character-set=utf8mb4",
    ]
    if database:
        cmd.append(database)
    if with_table:
        cmd.append("--table")
    return cmd


def get_mysql_env():
    """返回包含 MYSQL_PWD 的环境变量字典 (用于 subprocess.run 的 env 参数)"""
    return {**os.environ, "MYSQL_PWD": Config.MYSQL_PASSWORD}


def run_mysql_sql(sql_content, database=None, with_table=False):
    """执行 SQL 内容 (通过 stdin), 返回 subprocess.CompletedProcess"""
    import subprocess
    cmd = get_mysql_cmd(database=database, with_table=with_table)
    return subprocess.run(
        cmd,
        input=sql_content,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        env=get_mysql_env(),
    )


def run_mysql_file(sql_file, database=None, with_table=False):
    """执行 SQL 文件, 返回 subprocess.CompletedProcess"""
    with open(sql_file, 'r', encoding='utf-8') as f:
        sql_content = f.read()
    return run_mysql_sql(sql_content, database=database, with_table=with_table)


def get_neo4j_driver():
    """返回 Neo4j driver (密码走 Config)"""
    from neo4j import GraphDatabase
    return GraphDatabase.driver(
        Config.NEO4J_URI,
        auth=(Config.NEO4J_USER, Config.NEO4J_PASSWORD),
    )


def get_redis_client():
    """返回 Redis client (URL 走 Config.REDIS_URL)"""
    import redis
    return redis.Redis.from_url(Config.REDIS_URL, decode_responses=True)


def get_sqlalchemy_url():
    """返回 SQLAlchemy 数据库 URL (密码从 Config 取, 不出现在字面量中)

    格式: mysql+pymysql://user:password@host:port/database?charset=utf8mb4
    """
    from urllib.parse import quote_plus
    password = quote_plus(Config.MYSQL_PASSWORD)
    return (
        f"mysql+pymysql://{Config.MYSQL_USER}:{password}"
        f"@{Config.MYSQL_HOST}:{Config.MYSQL_PORT}/{Config.MYSQL_DATABASE}"
        f"?charset=utf8mb4"
    )
