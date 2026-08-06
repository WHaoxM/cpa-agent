"""
CPA-Agent Backend 启动入口
"""

import os
import sys
from pathlib import Path

# 项目根目录 (backend/ 的父目录)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 模型缓存分层 (基于项目根目录推导, 可被 .env 覆盖) ──
# 缓解 C 盘空间压力: 将 HF / ModelScope / SentenceTransformer 缓存统一迁至项目内
_MODELS_ROOT = _PROJECT_ROOT / 'models'

# MinerU / ModelScope 缓存 (PDF 解析模型, ~3GB)
# 默认 modelscope 仅用于首次拉取；kit 齐备后 prefer_local_mineru_models 升为 local。
os.environ.setdefault('MODELSCOPE_CACHE', str(_MODELS_ROOT / 'mineru'))
os.environ.setdefault('MINERU_MODEL_SOURCE', 'modelscope')
# Windows: 禁止 ModelScope 再建易碎 symlink（会指到旧 modelscope_cache 造成死循环下载）
os.environ.setdefault('MODELSCOPE_HUB_CREATE_SYMLINK', '0')
os.environ.setdefault('MODELSCOPE_SYMLINK', '0')
os.environ.setdefault('MODELSCOPE_SYMLINK_FILES_IN_ROOT_ENABLED', 'false')

# HuggingFace / SentenceTransformer 缓存 (BGE-M3 等, ~2GB)
os.environ.setdefault('HF_HOME', str(_MODELS_ROOT / 'embeddings'))
os.environ.setdefault('SENTENCE_TRANSFORMERS_HOME', str(_MODELS_ROOT / 'embeddings'))
os.environ.setdefault('HF_HUB_CACHE', str(_MODELS_ROOT / 'embeddings' / 'hub'))
os.environ.setdefault('TRANSFORMERS_CACHE', str(_MODELS_ROOT / 'embeddings' / 'transformers'))

# Windows：强制 UTF-8（控制台乱码多为 CP936 误读 UTF-8 源/日志）
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
os.environ.setdefault('PYTHONUTF8', '1')
if sys.platform == 'win32':
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.config import Config


def main():
    """主函数"""
    print(f"[DEBUG] Python executable: {sys.executable}")
    print(f"[DEBUG] Python version: {sys.version}")

    if sys.platform == 'win32':
        try:
            from app.pipeline.entity_extractor.modelscope_win_links import (
                ensure_modelscope_windows_links,
            )
            fixed = ensure_modelscope_windows_links(os.environ.get('MODELSCOPE_CACHE'))
            if fixed:
                print("[INFO] ModelScope Windows cache links:")
                for line in fixed:
                    print(f"  - {line}")
        except Exception as e:
            print(f"[WARN] ModelScope Windows link fix skipped: {e}")

    try:
        from app.pipeline.entity_extractor.mineru_local import prefer_local_mineru_models

        local_fixed = prefer_local_mineru_models(os.environ.get('MODELSCOPE_CACHE'))
        if local_fixed:
            print("[INFO] MinerU local models:")
            for line in local_fixed:
                print(f"  - {line}")
        else:
            print(
                f"[INFO] MinerU model source: "
                f"{os.environ.get('MINERU_MODEL_SOURCE', 'modelscope')}"
            )
    except Exception as e:
        print(f"[WARN] MinerU local model prefer skipped: {e}")

    errors = Config.validate(strict=not Config.DEMO_MODE)
    if errors:
        print("配置错误:")
        for err in errors:
            print(f"  - {err}")
        print("\n请检查 .env 文件中的配置")
        sys.exit(1)
    if Config.DEMO_MODE:
        print("[INFO] CPA_DEMO_MODE=true: 外部 LLM/数据库配置缺失不会阻止基础服务启动。")

    app = create_app()

    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_PORT', 5001))
    debug = Config.DEBUG

    print(f"\n  CPA-Agent Backend running at http://{host}:{port}")
    print(f"  Health check: http://localhost:{port}/health\n")

    app.run(host=host, port=port, debug=debug, threaded=True, use_reloader=False)


if __name__ == '__main__':
    main()
