"""
BGE-M3 Embedding 服务
提供文本向量化和语义相似度计算

使用 sentence-transformers 加载 BAAI/bge-m3 模型
支持 singleton 模式和结果缓存
"""

import os
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from functools import lru_cache

from app.config import Config
from app.utils.logger import get_logger

logger = get_logger('cpa-agent.embedding')

# 项目根目录 (backend/app/services/ 向上 3 级)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
# BGE-M3 / SentenceTransformer 缓存目录 (与 run.py 环境变量保持一致)
_EMBEDDING_CACHE = _PROJECT_ROOT / 'models' / 'embeddings'


class EmbeddingService:
    """
    BGE-M3 Embedding 服务 (Singleton)

    使用 BAAI/bge-m3 多语言模型生成 1024 维向量
    支持 cosine 相似度计算
    """

    _instance: Optional['EmbeddingService'] = None
    _model = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.model_name = Config.EMBEDDING_MODEL
        self.dimension = Config.EMBEDDING_DIM
        self._initialized = True
        self._load_time = None
        self._call_count = 0

    def _load_model(self):
        """延迟加载模型"""
        if self._model is not None:
            return

        try:
            from sentence_transformers import SentenceTransformer
            logger.info(f"正在加载 BGE-M3 模型: {self.model_name}")

            # HF 镜像端点从 embedding.yaml 读取 (不再硬编码)
            try:
                from config.integration_loader import get_embedding_config
                hf_endpoint = get_embedding_config().get('huggingface', {}).get('endpoint')
            except Exception:
                hf_endpoint = None
            if hf_endpoint:
                os.environ.setdefault('HF_ENDPOINT', hf_endpoint)
            # 显式指定 cache_folder, 确保走项目内 models/embeddings/ 而非 C 盘默认缓存
            cache_folder = os.environ.get('SENTENCE_TRANSFORMERS_HOME', str(_EMBEDDING_CACHE))
            self._model = SentenceTransformer(self.model_name, cache_folder=cache_folder)
            self._load_time = time.time()

            logger.info(f"BGE-M3 模型加载成功, 耗时: {self._load_time:.2f}s")

        except ImportError:
            logger.error("sentence-transformers 未安装，请运行: pip install sentence-transformers")
            raise
        except Exception as e:
            logger.error(f"BGE-M3 模型加载失败: {e}")
            raise

    @property
    def model(self):
        """获取模型 (延迟加载)"""
        if self._model is None:
            self._load_model()
        return self._model

    def encode(
        self,
        texts: List[str],
        normalize: bool = True,
        batch_size: int = 32
    ) -> List[List[float]]:
        """
        生成文本向量

        Args:
            texts: 文本列表
            normalize: 是否归一化 (cosine 相似度需要)
            batch_size: 批处理大小

        Returns:
            向量列表
        """
        if not texts:
            return []

        self._call_count += 1

        try:
            embeddings = self.model.encode(
                texts,
                normalize_embeddings=normalize,
                batch_size=batch_size,
                show_progress_bar=False
            )

            return embeddings.tolist()

        except Exception as e:
            logger.error(f"向量化失败: {e}")
            raise

    def encode_single(self, text: str, normalize: bool = True) -> List[float]:
        """
        生成单个文本向量

        Args:
            text: 文本
            normalize: 是否归一化

        Returns:
            向量
        """
        return self.encode([text], normalize=normalize)[0]

    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """
        计算两个向量的 cosine 相似度

        Args:
            vec1: 向量1
            vec2: 向量2

        Returns:
            相似度 (0-1)
        """
        if not vec1 or not vec2:
            return 0.0

        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = sum(a * a for a in vec1) ** 0.5
        norm2 = sum(b * b for b in vec2) ** 0.5

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot_product / (norm1 * norm2)

    def search_similar(
        self,
        query: str,
        texts: List[str],
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        在文本列表中搜索最相似的文本

        Args:
            query: 查询文本
            texts: 文本列表
            top_k: 返回数量

        Returns:
            相似文本列表 (含相似度)
        """
        if not texts:
            return []

        query_vec = self.encode_single(query)

        text_vecs = self.encode(texts)

        similarities = []
        for i, text_vec in enumerate(text_vecs):
            sim = self.cosine_similarity(query_vec, text_vec)
            similarities.append({
                "index": i,
                "text": texts[i],
                "similarity": round(sim, 4)
            })

        similarities.sort(key=lambda x: x["similarity"], reverse=True)

        return similarities[:top_k]

    def get_stats(self) -> Dict[str, Any]:
        """获取服务统计信息"""
        return {
            "model": self.model_name,
            "dimension": self.dimension,
            "load_time": self._load_time,
            "call_count": self._call_count,
            "loaded": self._model is not None
        }


_embedding_service: Optional[EmbeddingService] = None
_embedding_service_lock = __import__('threading').Lock()


def get_embedding_service() -> EmbeddingService:
    """获取全局 Embedding 服务单例（线程安全）"""
    global _embedding_service
    if _embedding_service is None:
        with _embedding_service_lock:
            if _embedding_service is None:
                _embedding_service = EmbeddingService()
    return _embedding_service


def encode_texts(texts: List[str]) -> List[List[float]]:
    """便捷函数: 批量生成向量"""
    return get_embedding_service().encode(texts)


def encode_text(text: str) -> List[float]:
    """便捷函数: 生成单个向量"""
    return get_embedding_service().encode_single(text)


if __name__ == "__main__":
    print("=" * 50)
    print("BGE-M3 Embedding 服务测试")
    print("=" * 50)

    service = get_embedding_service()

    test_texts = [
        "Python 编程语言",
        "Java 后端开发",
        "机器学习算法",
        "前端 React 框架",
        "数据库 MySQL"
    ]

    print(f"\n模型信息: {service.get_stats()}")

    print("\n测试向量化...")
    vectors = service.encode(test_texts)
    print(f"向量化成功: {len(vectors)} 个向量, 维度: {len(vectors[0]) if vectors else 0}")

    print("\n测试相似度搜索...")
    results = service.search_similar("Python 后端开发", test_texts, top_k=3)
    for r in results:
        print(f"  [{r['similarity']:.4f}] {r['text']}")

    print("\n测试完成")