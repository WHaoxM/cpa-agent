"""
Tool 6: jd_cleaner — 岗位数据清洗工具
智能体自主调用清洗任务，过滤非IT岗位并生成结构化IT岗位知识库
"""

import sys
import os
import json
from typing import Dict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from .base import BaseTool
from ...utils.logger import get_logger

logger = get_logger('cpa-agent.tool.jd_cleaner')


class JdCleanerTool(BaseTool):
    """岗位数据清洗工具"""

    @property
    def name(self) -> str:
        return "jd_cleaner"

    @property
    def description(self) -> str:
        return """\
【岗位数据清洗 — Jd Cleaner】
清洗招聘岗位数据，筛选IT相关岗位，生成结构化IT岗位知识库。

【功能】
- 从 jd_data.json 读取原始岗位数据
- 预筛选：严格过滤非IT岗位
- LLM深度处理：行业判定、维度对齐、置信度校验
- 断点续传：自动跳过已处理的岗位
- 实时进度：显示处理统计和进度条

【使用场景】
- "清洗岗位数据" → 执行完整清洗流程
- "重新处理岗位" → 删除断点文件并重新清洗

【返回内容】
- 处理状态 (成功/失败)
- 详细统计 (总数、预筛通过、LLM通过等)
- 输出文件路径"""

    @property
    def parameters(self) -> Dict[str, str]:
        return {
            "action": "操作类型 (run / reset / status)",
            "input_file": "可选：输入文件路径 (默认 cleaning_data/jd_data.json)",
            "output_file": "可选：输出文件路径 (默认 cleaning_data/structured_jd_knowledge_base.json)"
        }

    def _get_cleaning_module(self):
        """获取清洗模块"""
        cleaning_dir = Path(__file__).parent.parent.parent.parent / "cleaning_data"
        if str(cleaning_dir) not in sys.path:
            sys.path.insert(0, str(cleaning_dir))
        import p1_LLM
        return p1_LLM

    def _reset_checkpoint(self) -> str:
        """重置断点文件"""
        cleaning_dir = Path(__file__).parent.parent.parent.parent / "cleaning_data"
        checkpoint_path = cleaning_dir / "structured_jd_checkpoint.jsonl"
        if checkpoint_path.exists():
            checkpoint_path.unlink()
            return "断点文件已删除，可重新开始"
        return "无断点文件需要删除"

    def _get_status(self) -> str:
        """获取当前状态"""
        cleaning_dir = Path(__file__).parent.parent.parent.parent / "cleaning_data"
        input_path = cleaning_dir / "jd_data.json"
        checkpoint_path = cleaning_dir / "structured_jd_checkpoint.jsonl"
        output_path = cleaning_dir / "structured_jd_knowledge_base.json"

        status = []

        if input_path.exists():
            with open(input_path, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
            status.append(f"原始数据: {len(raw_data)} 条")

        if checkpoint_path.exists():
            count = sum(1 for line in open(checkpoint_path, 'r', encoding='utf-8'))
            status.append(f"已处理: {count} 条")

        if output_path.exists():
            with open(output_path, 'r', encoding='utf-8') as f:
                final_data = json.load(f)
            status.append(f"最终IT岗位: {len(final_data)} 条")

        if status:
            return "\n".join(status)
        return "未找到数据文件"

    def _run_cleaning(self, input_file: str = None, output_file: str = None) -> str:
        """运行清洗任务"""
        cleaning_dir = Path(__file__).parent.parent.parent.parent / "cleaning_data"
        p1_LLM = self._get_cleaning_module()

        original_input = p1_LLM.INPUT_FILE
        original_output = p1_LLM.OUTPUT_FILE
        original_checkpoint = p1_LLM.CHECKPOINT_FILE

        original_cwd = os.getcwd()
        os.chdir(str(cleaning_dir.parent))

        try:
            if input_file:
                p1_LLM.INPUT_FILE = input_file
            if output_file:
                p1_LLM.OUTPUT_FILE = output_file

            p1_LLM.main()

            output_path = Path(p1_LLM.OUTPUT_FILE)
            if not output_path.is_absolute():
                output_path = cleaning_dir.parent / output_path

            if output_path.exists():
                with open(output_path, 'r', encoding='utf-8') as f:
                    final_list = json.load(f)
            else:
                final_list = []

            return f"""\
[jd_cleaner] 清洗完成！
最终IT岗位: {len(final_list)} 条
输出文件: {p1_LLM.OUTPUT_FILE}"""

        except Exception as e:
            logger.error(f"清洗任务失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return f"[jd_cleaner] 清洗失败，请稍后重试"
        finally:
            os.chdir(original_cwd)
            p1_LLM.INPUT_FILE = original_input
            p1_LLM.OUTPUT_FILE = original_output
            p1_LLM.CHECKPOINT_FILE = original_checkpoint

    def execute(self, **kwargs) -> str:
        action = kwargs.get("action", "run")
        input_file = kwargs.get("input_file")
        output_file = kwargs.get("output_file")

        logger.info(f"jd_cleaner 执行: action={action}, input={input_file}, output={output_file}")

        valid_actions = {"run", "reset", "status"}
        if action not in valid_actions:
            return f"[jd_cleaner] 错误: 未知操作 '{action}', 可选: {', '.join(sorted(valid_actions))}"

        if action == "reset":
            return self._reset_checkpoint()
        elif action == "status":
            return self._get_status()
        else:
            return self._run_cleaning(input_file, output_file)