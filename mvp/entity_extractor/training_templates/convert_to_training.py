"""
数据转换脚本：将现有JSON数据转换为SFT训练格式
=============================================

功能：
    1. 将 entity_extraction_result_nsle.json 转换为训练数据
    2. 生成简历抽取训练样本
    3. 生成岗位描述抽取训练样本（待扩展）
"""

import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime


class ResumeToTrainingConverter:
    """将简历提取结果转换为SFT训练数据"""

    def __init__(self, system_prompt: Optional[str] = None):
        self.system_prompt = system_prompt or self._default_system_prompt()

    def _default_system_prompt(self) -> str:
        return """你是一个专业的简历信息抽取专家。
你的任务是从简历文本中准确抽取结构化信息。

【抽取要求】
1. 严格按照原文语义抽取，不要推测未明确提及的信息
2. 技能分为三个等级：了解(知晓概念)、熟悉(会用)、精通(深入掌握)
3. 日期格式统一为 YYYY-MM 或 YYYY-MM-DD
4. 所有抽取内容必须出现在原文中，不可自己编造

【输出格式】
严格按照以下JSON格式输出，不要包含任何其他内容：
{
  "person_name": "姓名",
  "position": "求职意向/职位",
  "skills": [
    {"name": "技能名称", "level": "了解|熟悉|精通", "source": "原文描述"}
  ],
  "education": [
    {
      "school": "学校名称",
      "degree": "学位",
      "major": "专业",
      "start_date": "YYYY-MM",
      "end_date": "YYYY-MM或至今"
    }
  ],
  "work_experience": [
    {
      "company": "公司名称",
      "position": "职位",
      "start_date": "YYYY-MM",
      "end_date": "YYYY-MM或至今",
      "description": "工作描述"
    }
  ],
  "project_experience": [
    {
      "project_name": "项目名称",
      "role": "角色",
      "start_date": "YYYY-MM",
      "end_date": "YYYY-MM或至今",
      "description": "项目描述"
    }
  ],
  "certificates": ["证书1", "证书2"]
}"""

    def convert_block(self, block: Dict) -> Optional[Dict]:
        """将单个block转换为训练数据"""
        block_type = block.get("block_type", "")

        if block_type == "PERSONAL_INFO":
            return self._convert_personal_info(block)
        elif block_type == "SKILLS_CERTS":
            return self._convert_skills(block)
        elif block_type == "EDUCATION":
            return self._convert_education(block)
        elif block_type == "WORK_EXPERIENCE":
            return self._convert_work(block)
        elif block_type == "PROJECT_EXPERIENCE":
            return self._convert_project(block)
        return None

    def _convert_personal_info(self, block: Dict) -> Dict:
        """转换个人信息块"""
        original_text = block.get("original_text", "")
        heading = block.get("heading", "")

        # 尝试提取姓名和职位
        person_name = heading if heading else "未知"
        position = original_text.strip() if original_text != heading else ""

        return {
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"请从以下简历文本中抽取信息：\n\n{original_text}"},
                {"role": "assistant", "content": json.dumps({
                    "person_name": person_name,
                    "position": position,
                    "skills": [],
                    "education": [],
                    "work_experience": [],
                    "project_experience": [],
                    "certificates": []
                }, ensure_ascii=False, indent=2)}
            ]
        }

    def _convert_skills(self, block: Dict) -> Dict:
        """转换技能块"""
        original_text = block.get("original_text", "")

        # 解析预提取的技能
        pre_extracted = block.get("pre_extracted", {})
        skills = pre_extracted.get("skills", [])

        # 构建技能列表
        skills_list = []
        for skill in skills:
            level = self._infer_skill_level(original_text, skill)
            skills_list.append({
                "name": skill,
                "level": level,
                "source": self._get_skill_context(original_text, skill)
            })

        return {
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"请从以下简历文本中抽取信息：\n\n{original_text}"},
                {"role": "assistant", "content": json.dumps({
                    "person_name": "",
                    "position": "",
                    "skills": skills_list,
                    "education": [],
                    "work_experience": [],
                    "project_experience": [],
                    "certificates": []
                }, ensure_ascii=False, indent=2)}
            ]
        }

    def _convert_education(self, block: Dict) -> Dict:
        """转换教育经历块"""
        original_text = block.get("original_text", "")
        pre_extracted = block.get("pre_extracted", {})

        educations = pre_extracted.get("education", [])
        degrees = pre_extracted.get("degrees", [])

        edu_list = []
        for edu in educations:
            edu_list.append({
                "school": edu.get("school", ""),
                "degree": edu.get("degree", degrees[0] if degrees else ""),
                "major": edu.get("major", ""),
                "start_date": edu.get("start_date", ""),
                "end_date": edu.get("end_date", "")
            })

        return {
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"请从以下简历文本中抽取信息：\n\n{original_text}"},
                {"role": "assistant", "content": json.dumps({
                    "person_name": "",
                    "position": "",
                    "skills": [],
                    "education": edu_list if edu_list else [{"school": "", "degree": "", "major": "", "start_date": "", "end_date": ""}],
                    "work_experience": [],
                    "project_experience": [],
                    "certificates": []
                }, ensure_ascii=False, indent=2)}
            ]
        }

    def _convert_work(self, block: Dict) -> Dict:
        """转换工作经历块"""
        original_text = block.get("original_text", "")
        heading = block.get("heading", "")

        return {
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"请从以下简历文本中抽取信息：\n\n{heading}\n{original_text}"},
                {"role": "assistant", "content": json.dumps({
                    "person_name": "",
                    "position": "",
                    "skills": [],
                    "education": [],
                    "work_experience": [{
                        "company": "",
                        "position": "",
                        "start_date": "",
                        "end_date": "",
                        "description": original_text
                    }],
                    "project_experience": [],
                    "certificates": []
                }, ensure_ascii=False, indent=2)}
            ]
        }

    def _convert_project(self, block: Dict) -> Dict:
        """转换项目经历块"""
        original_text = block.get("original_text", "")
        heading = block.get("heading", "")

        return {
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"请从以下简历文本中抽取信息：\n\n{heading}\n{original_text}"},
                {"role": "assistant", "content": json.dumps({
                    "person_name": "",
                    "position": "",
                    "skills": [],
                    "education": [],
                    "work_experience": [],
                    "project_experience": [{
                        "project_name": heading,
                        "role": "",
                        "start_date": "",
                        "end_date": "",
                        "description": original_text
                    }],
                    "certificates": []
                }, ensure_ascii=False, indent=2)}
            ]
        }

    def _infer_skill_level(self, text: str, skill: str) -> str:
        """推断技能掌握程度"""
        text_lower = text.lower()
        skill_lower = skill.lower()

        if f"精通{skill}" in text or f"熟练{skill}" in text:
            return "精通"
        elif f"熟悉{skill}" in text or f"掌握{skill}" in text:
            return "熟悉"
        elif f"了解{skill}" in text or f"会{skill}" in text:
            return "了解"
        return "熟悉"  # 默认

    def _get_skill_context(self, text: str, skill: str) -> str:
        """获取技能相关的上下文描述"""
        lines = text.split("\n")
        for line in lines:
            if skill in line:
                return line.strip()
        return ""


def convert_nsle_to_training(
    nsle_json_path: str,
    output_path: str,
    sample_ratio: float = 1.0
) -> Dict[str, Any]:
    """
    将NSLE JSON转换为SFT训练数据

    Args:
        nsle_json_path: NSLE输出JSON文件路径
        output_path: 输出文件路径
        sample_ratio: 采样比例（用于快速验证）

    Returns:
        转换统计信息
    """
    # 加载数据
    with open(nsle_json_path, "r", encoding="utf-8") as f:
        nsle_data = json.load(f)

    converter = ResumeToTrainingConverter()
    training_data = []

    # 遍历每个blocks
    blocks = nsle_data.get("blocks", [])
    for block in blocks:
        converted = converter.convert_block(block)
        if converted:
            training_data.append(converted)

    # 采样（如果需要）
    if sample_ratio < 1.0:
        random.seed(42)
        n = int(len(training_data) * sample_ratio)
        training_data = random.sample(training_data, n)

    # 保存
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for item in training_data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    return {
        "input_file": nsle_json_path,
        "output_file": str(output_path),
        "total_blocks": len(blocks),
        "converted_samples": len(training_data),
        "output_format": "jsonl"
    }


if __name__ == "__main__":
    import sys

    print("=== NSLE JSON → SFT训练数据 转换工具 ===\n")

    # 默认路径
    default_input = Path(__file__).parent.parent / "entity_extraction_result_nsle.json"
    default_output = Path(__file__).parent / "training_data" / "resume_sft_data.jsonl"

    if len(sys.argv) > 1:
        input_path = sys.argv[1]
    else:
        input_path = default_input

    if len(sys.argv) > 2:
        output_path = sys.argv[2]
    else:
        output_path = default_output

    print(f"输入文件: {input_path}")
    print(f"输出文件: {output_path}")
    print("-" * 50)

    if default_input.exists():
        result = convert_nsle_to_training(str(default_input), str(default_output))
        print(f"\n转换完成!")
        print(f"  - 总块数: {result['total_blocks']}")
        print(f"  - 转换样本: {result['converted_samples']}")
        print(f"  - 输出格式: {result['output_format']}")
    else:
        print(f"\n警告: 示例文件不存在 ({default_input})")
        print("\n使用方式:")
        print("  python convert_to_training.py <输入JSON> <输出JSONL>")
