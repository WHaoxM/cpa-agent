"""MySQL 数据库初始化脚本"""
import pymysql
import sys
import os

# init_mysql.py 位于 backend/scripts/mysql/, 需上溯 3 级到 backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.config import Config

def init_mysql():
    print("=" * 50)
    print("MySQL 数据库初始化")
    print("=" * 50)

    try:
        conn = pymysql.connect(
            host=Config.MYSQL_HOST,
            port=Config.MYSQL_PORT,
            user=Config.MYSQL_USER,
            password=Config.MYSQL_PASSWORD,
            charset='utf8mb4'
        )
        print(f"✅ MySQL 连接成功 ({Config.MYSQL_HOST}:{Config.MYSQL_PORT})")
    except Exception as e:
        print(f"❌ MySQL 连接失败: {e}")
        return False

    cursor = conn.cursor()

    # 创建数据库
    cursor.execute("CREATE DATABASE IF NOT EXISTS cpa_agent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    print("✅ 数据库 cpa_agent 创建/已存在")

    # 使用数据库
    cursor.execute("USE cpa_agent")

    # 读取并执行初始化脚本 (与本脚本同目录)
    sql_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'init_mysql.sql')
    print(f"\n读取 SQL 文件: {sql_file}")

    with open(sql_file, 'r', encoding='utf-8') as f:
        sql_content = f.read()

    # 分割并执行每个语句
    statements = []
    current_stmt = []

    for line in sql_content.split('\n'):
        line = line.strip()
        if not line or line.startswith('--'):
            continue
        if line.startswith('USE '):
            continue
        if line.startswith('SELECT '):
            continue
        current_stmt.append(line)
        if line.endswith(';'):
            statements.append(' '.join(current_stmt))
            current_stmt = []

    print(f"执行 {len(statements)} 个 SQL 语句...")

    for i, stmt in enumerate(statements):
        try:
            cursor.execute(stmt)
            print(f"  ✅ [{i+1}/{len(statements)}] 执行成功")
        except Exception as e:
            print(f"  ⚠️  [{i+1}/{len(statements)}] {e}")

    conn.commit()

    # 验证表创建
    cursor.execute("SHOW TABLES")
    tables = cursor.fetchall()
    print(f"\n✅ 共创建 {len(tables)} 张表:")
    for t in tables:
        print(f"   - {t[0]}")

    cursor.close()
    conn.close()

    print("\n✅ MySQL 初始化完成!")
    return True

if __name__ == "__main__":
    success = init_mysql()
    sys.exit(0 if success else 1)
