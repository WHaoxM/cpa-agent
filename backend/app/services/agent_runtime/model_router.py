"""
Qwen 模型路由 — 按任务类型选择最经济的 Qwen 模型。

路由原则：
- lead_classify: qwen-turbo（结构化 JSON，不需要强推理）
- ask_answer:    qwen-plus（需要理解力 + 自然对话）
- portrait_gen:  qwen-plus（结构化抽取 + 判断）
- report_gen:    qwen-max（长文本 + 章节组织）
- skill_extract: qwen-turbo（纯抽取，模式化）
- cypher_gen:    qwen-turbo（代码生成，精确）
- default:       qwen-plus
"""

from __future__ import annotations

from typing import Any, Dict, Optional

ROUTING_TABLE: Dict[str, Dict[str, Any]] = {
    "lead_classify": {
        "model": "qwen-turbo",
        "temperature": 0.1,
        "max_tokens": 700,
    },
    "ask_answer": {
        "model": "qwen-plus",
        "temperature": 0.4,
        "max_tokens": 1500,
    },
    "portrait_gen": {
        "model": "qwen-plus",
        "temperature": 0.2,
        "max_tokens": 2000,
    },
    "report_gen": {
        "model": "qwen-max",
        "temperature": 0.3,
        "max_tokens": 4000,
    },
    "skill_extract": {
        "model": "qwen-turbo",
        "temperature": 0.1,
        "max_tokens": 800,
    },
    "cypher_gen": {
        "model": "qwen-turbo",
        "temperature": 0.0,
        "max_tokens": 500,
    },
    "quality_judge": {
        "model": "qwen-turbo",
        "temperature": 0.1,
        "max_tokens": 200,
    },
}

DEFAULT_ROUTING = {
    "model": "qwen-plus",
    "temperature": 0.3,
    "max_tokens": 1500,
}


def route_model(task: str) -> Dict[str, Any]:
    """返回该任务的模型配置（model, temperature, max_tokens）。"""
    return ROUTING_TABLE.get(task, DEFAULT_ROUTING).copy()
