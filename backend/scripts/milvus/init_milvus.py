"""
CPA-Agent Milvus 向量数据库初始化脚本
版本: v1.1
创建日期: 2026-04-17
更新日期: 2026-04-19 - 修复全零向量，使用真实 BGE-M3 embedding

使用 pymilvus Lite 嵌入式模式
运行: python scripts/milvus/init_milvus.py
"""

import json
import os
import sys

# init_milvus.py 位于 backend/scripts/milvus/, 需上溯 3 级到 backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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


def _get_embedding_service():
    """获取 Embedding 服务"""
    try:
        from app.services.embedding_service import EmbeddingService
        return EmbeddingService()
    except Exception as e:
        print(f"  ⚠️  EmbeddingService 初始化失败: {e}")
        return None


def insert_sample_data(client: MilvusClient):
    """插入示例数据 (使用真实 BGE-M3 embedding)"""

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

    embedding_svc = _get_embedding_service()

    if embedding_svc is None:
        print("  ⚠️  无法初始化 Embedding 服务，跳过示例数据插入")
        print("  提示: 请确保 BGE-M3 模型可用后重新运行此脚本")
        return

    try:
        texts = [s["text"] for s in skill_samples]
        print(f"  生成 {len(texts)} 条文本的 BGE-M3 embedding...")
        embeddings = embedding_svc.encode(texts)

        for i, sample in enumerate(skill_samples):
            sample["embedding"] = embeddings[i].tolist()

        client._client.insert(
            collection_name="skill_vectors",
            data={
                "text": [s["text"] for s in skill_samples],
                "embedding": [s["embedding"] for s in skill_samples],
                "metadata": [json.dumps(s["metadata"], ensure_ascii=False) for s in skill_samples],
            }
        )
        print(f"  ✅ 技能向量示例数据插入成功 ({len(skill_samples)} 条, 使用真实 embedding)")
    except Exception as e:
        print(f"  ⚠️  技能向量插入失败: {e}")


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
