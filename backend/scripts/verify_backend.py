"""后端快速验证脚本"""
import sys
import os

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)
os.chdir(backend_dir)

print("=" * 50)
print("CPA-Agent 后端代码验证")
print("=" * 50)

# 1. 配置验证
print("\n[1/5] 配置验证...")
try:
    from app.config import Config
    errors = Config.validate()
    if errors:
        print(f"  ❌ 配置错误: {errors}")
    else:
        print("  ✅ 配置验证通过")
except Exception as e:
    print(f"  ❌ 配置加载失败: {e}")

# 2. 模块导入验证
print("\n[2/5] 核心模块导入...")
try:
    from app.services.db.mysql_client import MySQLClient
    from app.services.db.neo4j_client import Neo4jClient
    from app.services.db.redis_client import RedisClient
    from app.services.db.milvus_client import MilvusClient
    from app.services.session_manager import SessionManager
    from app.services.react_agent import ReactAgent
    from app.services.pipeline import PipelineRunner
    print("  ✅ 所有核心模块导入成功")
except Exception as e:
    print(f"  ❌ 模块导入失败: {e}")
    import traceback
    traceback.print_exc()

# 3. 工具注册验证
print("\n[3/5] 工具注册验证...")
try:
    from app.services.tools import (
        GraphQueryTool, DbQueryTool, SemanticSearchTool,
        PipelineRunTool, LlmReasonTool, JdCleanerTool
    )
    tools = {
        "GraphQueryTool": GraphQueryTool(),
        "DbQueryTool": DbQueryTool(),
        "SemanticSearchTool": SemanticSearchTool(),
        "PipelineRunTool": PipelineRunTool(),
        "LlmReasonTool": LlmReasonTool(),
        "JdCleanerTool": JdCleanerTool(),
    }
    print(f"  ✅ 6 个工具全部实例化成功")
    for name, tool in tools.items():
        print(f"     - {name}: {tool.name}")
except Exception as e:
    print(f"  ❌ 工具注册失败: {e}")

# 4. Flask 应用验证
print("\n[4/5] Flask 应用验证...")
try:
    from app import create_app
    app = create_app()
    print("  ✅ Flask 应用创建成功")
    with app.test_client() as client:
        resp = client.get('/health')
        print(f"  ✅ Health: {resp.json}")
        resp = client.get('/metrics')
        print(f"  ✅ Metrics: {resp.json}")
except Exception as e:
    print(f"  ❌ Flask 应用失败: {e}")

# 5. 外部服务状态
print("\n[5/5] 外部服务状态...")
print("  ⏭️  MySQL: 需启动 MySQL 服务")
print("  ⏭️  Neo4j: 需启动 Neo4j 服务")
print("  ⏭️  Redis: 需启动 Redis 服务")
print("  ⏭️  Milvus: 需启动 Milvus Lite")
print("\n  详见: backend-atomic-checklist.md")

print("\n" + "=" * 50)
print("代码验证完成 - 外部服务需单独启动")
print("=" * 50)
