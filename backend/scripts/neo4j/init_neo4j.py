"""Neo4j 图谱初始化脚本"""
from neo4j import GraphDatabase
import sys
import os

# init_neo4j.py 位于 backend/scripts/neo4j/, 需上溯 3 级到 backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.config import Config

def init_neo4j():
    print("=" * 50)
    print("Neo4j 图谱初始化")
    print("=" * 50)

    try:
        driver = GraphDatabase.driver(
            Config.NEO4J_URI,
            auth=(Config.NEO4J_USER, Config.NEO4J_PASSWORD)
        )
        print(f"✅ Neo4j 连接成功 ({Config.NEO4J_URI})")
    except Exception as e:
        print(f"❌ Neo4j 连接失败: {e}")
        return False

    with driver.session() as session:
        # 读取 CQL 文件 (与本脚本同目录)
        cql_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'init_neo4j.cql')
        print(f"\n读取 CQL 文件: {cql_file}")

        with open(cql_file, 'r', encoding='utf-8') as f:
            cql_content = f.read()

        # 分割语句 (按 // 分隔，但保留 CREATE/MATCH 等语句)
        lines = cql_content.split('\n')
        statements = []
        current_stmt = []

        for line in lines:
            line = line.strip()
            # 跳过空行和注释
            if not line or line.startswith('//'):
                continue
            current_stmt.append(line)
            if line.endswith(';'):
                statements.append('\n'.join(current_stmt))
                current_stmt = []

        print(f"执行 {len(statements)} 个 CQL 语句...")

        for i, stmt in enumerate(statements):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                session.run(stmt)
                print(f"  ✅ [{i+1}/{len(statements)}] 执行成功")
            except Exception as e:
                err_msg = str(e)
                if 'already exists' in err_msg or 'duplicate' in err_msg.lower():
                    print(f"  ⚠️  [{i+1}/{len(statements)}] 已存在，跳过")
                else:
                    print(f"  ⚠️  [{i+1}/{len(statements)}] {err_msg[:80]}")

        # 验证
        try:
            result = session.run("MATCH (s:Skill) RETURN count(s) as count")
            skill_count = result.single()['count']
            print(f"\n✅ 技能节点数量: {skill_count}")
        except:
            print(f"\n⚠️  技能节点查询失败")

        try:
            result = session.run("MATCH (j:JobPortrait) RETURN count(j) as count")
            portrait_count = result.single()['count']
            print(f"✅ 岗位画像节点数量: {portrait_count}")
        except:
            print(f"⚠️  岗位画像节点查询失败")

    driver.close()
    print("\n✅ Neo4j 初始化完成!")
    return True

if __name__ == "__main__":
    success = init_neo4j()
    sys.exit(0 if success else 1)
