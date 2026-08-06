"""一次性对齐 LangGraph checkpoint 表 collation（utf8mb4_0900_ai_ci）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.langgraph_agent import (  # noqa: E402
    _align_checkpoint_collation,
    get_checkpointer,
)


def main() -> None:
    # 确保表已由 setup() 创建
    cp = get_checkpointer()
    print("checkpointer:", type(cp).__name__)
    _align_checkpoint_collation()
    # 冒烟：读一个不存在的 thread
    from langchain_core.runnables import RunnableConfig

    cfg: RunnableConfig = {"configurable": {"thread_id": "collation-smoke", "checkpoint_ns": ""}}
    tup = cp.get_tuple(cfg)
    print("get_tuple ok:", tup)


if __name__ == "__main__":
    main()
