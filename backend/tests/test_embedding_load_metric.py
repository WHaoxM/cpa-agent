"""Model load logs elapsed time, not the Unix timestamp stored in stats."""

import sys
from types import SimpleNamespace

from app.services import embedding_service


def test_embedding_load_log_reports_duration_not_epoch(monkeypatch):
    class FakeSentenceTransformer:
        def __init__(self, _name, *, cache_folder):
            assert cache_folder

    service = object.__new__(embedding_service.EmbeddingService)
    service.model_name = "test-model"
    service.dimension = 1024
    service._model = None
    service._load_time = None
    service._call_count = 0
    ticks = iter((10.0, 12.5))
    messages = []
    monkeypatch.setitem(sys.modules, "sentence_transformers",
                        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer))
    monkeypatch.setattr(embedding_service.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(embedding_service.time, "time", lambda: 1234567890.0)
    monkeypatch.setattr(embedding_service.logger, "info", messages.append)

    service._load_model()

    assert service.get_stats()["load_time"] == 1234567890.0
    assert messages[-1] == "BGE-M3 模型加载成功, 耗时: 2.50s"
