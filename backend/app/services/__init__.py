"""
业务服务模块
"""

__all__ = [
    'ReactAgent',
    'PipelineRunner',
    'PipelineLevel',
    'PipelineStatus',
    'EmbeddingService',
    'get_embedding_service',
    'encode_texts',
    'encode_text',
]


def __getattr__(name):
    """惰性导出，避免非LLM测试被 openai 等重依赖阻塞"""
    if name == 'ReactAgent':
        from .react_agent import ReactAgent
        return ReactAgent
    if name in ('PipelineRunner', 'PipelineLevel', 'PipelineStatus'):
        from .pipeline import PipelineRunner, PipelineLevel, PipelineStatus
        return {
            'PipelineRunner': PipelineRunner,
            'PipelineLevel': PipelineLevel,
            'PipelineStatus': PipelineStatus,
        }[name]
    if name in ('EmbeddingService', 'get_embedding_service', 'encode_texts', 'encode_text'):
        from .embedding_service import EmbeddingService, get_embedding_service, encode_texts, encode_text
        return {
            'EmbeddingService': EmbeddingService,
            'get_embedding_service': get_embedding_service,
            'encode_texts': encode_texts,
            'encode_text': encode_text,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
