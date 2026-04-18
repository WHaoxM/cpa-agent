"""
CPA-Agent Milvus 向量数据库初始化脚本
版本: v1.0
创建日期: 2026-04-17

使用 pymilvus Lite 嵌入式模式
运行: python init_milvus.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.db.milvus_client import MilvusClient
from app.config import Config


def create_collections(client: MilvusClient):
    """创建所有 Collection"""
    
    collections = [
        {
            "name": "skill_vectors",
            "dimension": 1024,
            "description": "技能向量 - 语义匹配、相似技能推荐",
            "metric_type": "COSINE",
        },
        {
            "name": "jd_vectors",
            "dimension": 1024,
            "description": "JD 描述向量 - 简历匹配、岗位推荐",
            "metric_type": "COSINE",
        },
        {
            "name": "resume_vectors",
            "dimension": 1024,
            "description": "简历向量 - 简历语义检索、Agent RAG",
            "metric_type": "COSINE",
        },
        {
            "name": "conversation_vectors",
            "dimension": 1024,
            "description": "对话记忆向量 - 跨会话 RAG 检索",
            "metric_type": "COSINE",
        },
    ]
    
    for coll in collections:
        try:
            if client._client.has_collection(coll["name"]):
                print(f"  ⬜ Collection {coll['name']} 已存在，跳过")
                continue
                
            client._client.create_collection(
                collection_name=coll["name"],
                dimension=coll["dimension"],
                description=coll["description"],
                metric_type=coll["metric_type"],
                auto_id=True,
                primary_field_name="id",
                vector_field_name="embedding",
            )
            print(f"  ✅ Collection {coll['name']} 创建成功 (dim={coll['dimension']})")
            
        except Exception as e:
            print(f"  ❌ Collection {coll['name']} 创建失败: {e}")


def insert_sample_data(client: MilvusClient):
    """插入示例数据"""
    
    print("\n插入示例数据...")
    
    skill_samples = [
        {"text": "Vue 3 前端框架", "metadata": {"skill_name": "Vue 3", "category": "前端框架"}},
        {"text": "React 前端库", "metadata": {"skill_name": "React", "category": "前端框架"}},
        {"text": "Python 后端语言", "metadata": {"skill_name": "Python", "category": "后端语言"}},
        {"text": "Java Spring Boot 框架", "metadata": {"skill_name": "Spring Boot", "category": "后端框架"}},
        {"text": "MySQL 数据库", "metadata": {"skill_name": "MySQL", "category": "数据库"}},
        {"text": "Redis 缓存", "metadata": {"skill_name": "Redis", "category": "缓存"}},
        {"text": "机器学习算法", "metadata": {"skill_name": "机器学习", "category": "算法"}},
        {"text": "数据结构与算法", "metadata": {"skill_name": "数据结构", "category": "基础"}},
    ]
    
    try:
        for sample in skill_samples:
            sample["embedding"] = [0.0] * 1024
        
        client._client.insert(
            collection_name="skill_vectors",
            data=[
                [s["text"] for s in skill_samples],
                [s["embedding"] for s in skill_samples],
                [s["metadata"] for s in skill_samples],
            ]
        )
        print(f"  ✅ 技能向量示例数据插入成功 ({len(skill_samples)} 条)")
    except Exception as e:
        print(f"  ⚠️  技能向量插入失败 (Collection 可能已存在数据): {e}")


def main():
    print("=" * 50)
    print("CPA-Agent Milvus 向量数据库初始化")
    print("=" * 50)
    
    uri = Config.MILVUS_URI
    print(f"\n连接 Milvus: {uri}")
    
    client = MilvusClient(uri=uri)
    
    if not client.connect():
        print("❌ Milvus 连接失败")
        return False
    
    print("✅ Milvus 连接成功")
    
    print("\n创建 Collection...")
    create_collections(client)
    
    print("\n初始化示例数据...")
    insert_sample_data(client)
    
    print("\n" + "=" * 50)
    print("Milvus 初始化完成")
    print("=" * 50)
    
    print("\nCollection 列表:")
    collections = client.list_collections()
    for name in collections:
        stats = client.get_collection_stats(name)
        print(f"  - {name}: {stats.get('num_entities', 0)} 条向量")
    
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
