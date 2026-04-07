"""
Agent 结构化日志记录器
参考 MiroFish ReportLogger, 将 Agent 每步动作写入 JSONL 文件
用于调试、前端展示 Agent 思考过程、以及竞赛答辩演示
"""

import os
import json
from typing import Dict, Any, Optional
from datetime import datetime

from ..config import Config


class AgentLogger:
    """
    Agent 动作日志记录器

    写入 logs/agent_sessions/{session_id}/agent_log.jsonl
    每行一个 JSON 对象, 包含时间戳、动作类型、详细内容
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.log_dir = os.path.join(
            os.path.dirname(__file__), '../../logs/agent_sessions', session_id
        )
        self.log_file = os.path.join(self.log_dir, 'agent_log.jsonl')
        self.start_time = datetime.now()
        os.makedirs(self.log_dir, exist_ok=True)

    def _elapsed(self) -> float:
        return (datetime.now() - self.start_time).total_seconds()

    def log(
        self,
        action: str,
        details: Dict[str, Any],
        iteration: Optional[int] = None
    ):
        """写入一条日志"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "elapsed_seconds": round(self._elapsed(), 2),
            "session_id": self.session_id,
            "action": action,
            "iteration": iteration,
            "details": details
        }
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def log_user_input(self, message: str):
        self.log("user_input", {"message": message})

    def log_thought(self, thought: str, iteration: int):
        self.log("thought", {"thought": thought}, iteration)

    def log_tool_call(self, tool_name: str, parameters: Dict[str, Any], iteration: int):
        self.log("tool_call", {
            "tool_name": tool_name,
            "parameters": parameters
        }, iteration)

    def log_tool_result(self, tool_name: str, result: str, elapsed: float, iteration: int):
        self.log("tool_result", {
            "tool_name": tool_name,
            "result": result,
            "result_length": len(result),
            "elapsed_seconds": round(elapsed, 2)
        }, iteration)

    def log_final_answer(self, answer: str, total_tool_calls: int, total_iterations: int):
        self.log("final_answer", {
            "answer_preview": answer[:500],
            "answer_length": len(answer),
            "total_tool_calls": total_tool_calls,
            "total_iterations": total_iterations,
            "total_elapsed": round(self._elapsed(), 2)
        })

    def log_error(self, error: str, iteration: Optional[int] = None):
        self.log("error", {"error": error}, iteration)
