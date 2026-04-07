#!/usr/bin/env python3
"""
赛题数据批量生成器
基于AI的大学生职业规划智能体 - 训练数据生成

设计原则：
- 最小约束，最大自由
- 让LLM根据语义自行生成多样化内容
- 只提供必要的主题/场景约束，不限制具体值
"""

import argparse
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


class TeacherModelClient:
    """教师模型客户端"""

    def __init__(
        self,
        api_key: str,
        provider: str = "siliconflow",
        model: str = None,
        request_timeout: int = 120,
    ):
        self.api_key = api_key
        self.provider = provider
        self.request_timeout = request_timeout

        if provider == "deepseek":
            self.endpoint = "https://api.deepseek.com/v1/chat/completions"
            self.model = model or "deepseek-chat"
        elif provider == "siliconflow":
            self.endpoint = "https://api.siliconflow.cn/v1/chat/completions"
            self.model = model or "deepseek-ai/DeepSeek-V2.5"
        else:
            self.endpoint = "https://api.openai.com/v1/chat/completions"
            self.model = model or "gpt-4-turbo"

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.9,
        max_tokens: int = 4096,
    ) -> Optional[str]:
        """调用API生成"""
        for attempt in range(3):
            try:
                response = requests.post(
                    self.endpoint,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                    timeout=self.request_timeout,
                )

                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]

            except Exception as e:
                logger.warning("API调用失败 (尝试 %d/3): %s", attempt + 1, e)
                if attempt < 2:
                    time.sleep((2 ** attempt) * random.uniform(1, 3))

        return None


class StudentProfileGenerator:
    """
    学生画像生成器
    原则：只给场景/主题约束，让LLM自由发挥生成多样化内容
    """

    SYSTEM_PROMPT = """你是一个专业的数据生成专家，擅长生成多样化的学生就业能力画像数据。

核心原则：
1. 生成的数据必须真实、可信、多样化
2. 姓名、学校、专业、公司、职位等都要有真实感
3. 不要生成任何解释，直接输出JSON
4. phone必须脱敏格式：138****8888 或 1******88888
5. 所有技能和经历必须相互匹配、自洽"""

    USER_PROMPT_TEMPLATE = """请生成一份多样化的学生就业能力画像JSON：

场景约束：
- 学历层次：{education_constraint}
- 专业大类：{major_domain}
- 求职方向：{career_direction}

要求：
1. basic_info: 姓名、学校、专业、学历必须真实可信（不要用"某大学"、"某公司"这种模糊表述）
2. professional_skills: 技能要与专业和求职方向匹配
3. general_abilities: 必须包含innovation_ability、learning_ability、anti_pressure_ability、communication_ability，每个有score(0-100)和evidence
4. practical_experience: 1-2段实习经历，2-3个项目经历，每段都要有具体工作内容和成果
5. personality_traits: MBTI、优势、劣势
6. career_preference: 目标岗位、行业、期望薪资、工作地点

数据要多样化！不同的学生应该有完全不同的背景、经历、技能。不要重复。

直接输出JSON，不要有任何解释文字："""

    EDUCATION_CONSTRAINTS = [
        "大专应届毕业生",
        "本科应届毕业生",
        "本科1年工作经验",
        "本科3年工作经验",
        "硕士应届毕业生",
        "硕士2年工作经验",
        "博士应届毕业生",
    ]

    MAJOR_DOMAINS = [
        "计算机科学与技术相关专业",
        "软件工程相关专业",
        "人工智能相关专业",
        "数据科学相关专业",
        "电子信息工程相关专业",
        "自动化相关专业",
        "机械工程相关专业",
        "金融学相关专业",
    ]

    CAREER_DIRECTIONS = [
        "后端开发/服务器端开发",
        "前端开发/客户端开发",
        "全栈开发",
        "算法工程师/AI工程师",
        "数据工程师/数据分析",
        "运维/DevOps/SRE",
        "测试开发/QA",
        "产品经理",
        "UI/UX设计",
        "嵌入式开发",
    ]

    def __init__(self, client: TeacherModelClient):
        self.client = client

    def generate(self, index: int) -> Optional[Dict[str, Any]]:
        """生成单个学生画像"""

        user_prompt = self.USER_PROMPT_TEMPLATE.format(
            education_constraint=random.choice(self.EDUCATION_CONSTRAINTS),
            major_domain=random.choice(self.MAJOR_DOMAINS),
            career_direction=random.choice(self.CAREER_DIRECTIONS),
        )

        response = self.client.generate(self.SYSTEM_PROMPT, user_prompt)

        if not response:
            return None

        try:
            response = response.strip()
            if response.startswith("```"):
                lines = response.split("\n")
                response = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            return json.loads(response)
        except json.JSONDecodeError:
            logger.debug("JSON解析失败: %.100s...", response[:100])
            return None


class JobPostingGenerator:
    """
    岗位画像生成器
    原则：只给场景/主题约束，让LLM自由发挥
    """

    SYSTEM_PROMPT = """你是一个专业的数据生成专家，擅长生成多样化的就业岗位画像数据。

核心原则：
1. 生成的数据必须真实、可信、多样化
2. 公司名称、职位描述、技能要求都要具体真实
3. 不要生成任何解释，直接输出JSON
4. 薪资范围要符合市场行情
5. 换岗路径要体现真实的职业发展规律"""

    USER_PROMPT_TEMPLATE = """请生成一份多样化的就业岗位画像JSON：

场景约束：
- 岗位类型：{job_type}
- 所属行业：{industry}
- 岗位级别：{level}

要求：
1. job_id: 格式如 JD_xxxxxx
2. job_title: 具体真实的职位名称
3. company_info: 真实的知名公司或可信的公司名
4. salary_range: 符合市场行情的薪资范围
5. job_description: 具体的岗位职责描述
6. job_requirements: 学历、专业、经验、证书要求
7. professional_skills: required_skills列表，每个有skill、level、weight
8. general_abilities: innovation_ability、learning_ability、anti_pressure_ability、communication_ability、teamwork，每个有required、weight、description
9. development_potential: 实习经验要求、项目经验要求
10. career_path: entry_level、mid_level、senior_level、management_level
11. job_transfer_paths: 至少3条换岗路径，每条有target_job、transfer_reason、skill_overlap(0-1)、difficulty(容易/中等/困难)、training_needed

数据要多样化！不同的岗位应该有完全不同的要求和发展路径。

直接输出JSON，不要有任何解释文字："""

    JOB_TYPES = [
        "后端开发工程师",
        "前端开发工程师",
        "全栈开发工程师",
        "算法工程师",
        "机器学习工程师",
        "深度学习工程师",
        "数据工程师",
        "数据分析工程师",
        "测试开发工程师",
        "运维工程师",
        "DevOps工程师",
        "SRE工程师",
        "DBA",
        "架构师",
        "技术经理",
        "产品经理",
        "UI设计师",
    ]

    INDUSTRIES = [
        "互联网",
        "金融科技",
        "电子商务",
        "教育培训",
        "医疗健康",
        "游戏",
        "企业服务",
        "社交娱乐",
        "智能硬件",
        "新能源汽车",
    ]

    LEVELS = [
        "初级（1-3年）",
        "中级（3-5年）",
        "高级（5年以上）",
        "专家/架构师级别",
    ]

    def __init__(self, client: TeacherModelClient):
        self.client = client

    def generate(self, index: int) -> Optional[Dict[str, Any]]:
        """生成单个岗位画像"""

        user_prompt = self.USER_PROMPT_TEMPLATE.format(
            job_type=random.choice(self.JOB_TYPES),
            industry=random.choice(self.INDUSTRIES),
            level=random.choice(self.LEVELS),
        )

        response = self.client.generate(self.SYSTEM_PROMPT, user_prompt)

        if not response:
            return None

        try:
            response = response.strip()
            if response.startswith("```"):
                lines = response.split("\n")
                response = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            return json.loads(response)
        except json.JSONDecodeError:
            logger.debug("JSON解析失败: %.100s...", response[:100])
            return None


class BatchResumeGenerator:
    """
    批量简历生成器
    一次调用生成多条数据，提高效率
    """

    SYSTEM_PROMPT = """你是一个专业的简历数据生成专家。

核心原则：
1. 生成真实、可信、多样化的数据
2. 不要用"某大学"、"某公司"等模糊表述
3. 直接输出JSONL格式（每行一个JSON），不要有任何解释
4. phone脱敏格式：138****8888"""

    USER_PROMPT_TEMPLATE = """请一次性生成{number}份多样化的学生就业能力画像，输出为JSONL格式（每行一个JSON）。

场景多样性要求：
- 学历：大专、本科、硕士、博士都要有
- 专业：计算机、软件、数据、AI、电子、机械、金融等
- 经验：应届、1年、3年、5年经验
- 行业：互联网、金融、教育、医疗、游戏等
- 岗位：开发、算法、数据、产品、设计、运维等

每份简历必须包含：
1. basic_info: 姓名、学校（真实高校名）、专业、学历、毕业年份
2. professional_skills: 编程语言、框架、工具、数据库、AI技能
3. general_abilities: 创新、学习、抗压、沟通能力（每项0-100分+证据）
4. practical_experience: 实习（1-2段）、项目（2-3个）
5. personality_traits: MBTI、优势、劣势
6. career_preference: 目标岗位、期望薪资、工作地点

要求{number}份简历之间必须有明显差异，不能模式化。

直接输出{number}行JSONL，不要有任何其他文字："""

    def __init__(self, client: TeacherModelClient):
        self.client = client

    def generate_batch(self, count: int = 10) -> List[Dict[str, Any]]:
        """批量生成"""

        user_prompt = self.USER_PROMPT_TEMPLATE.format(number=count)

        response = self.client.generate(
            self.SYSTEM_PROMPT,
            user_prompt,
            temperature=0.95,
            max_tokens=8192,
        )

        if not response:
            return []

        results = []
        text = response.strip()

        # 去除 markdown 代码块包裹
        if text.startswith("```"):
            lines = text.split("\n")
            start = 1
            end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
            text = "\n".join(lines[start:end])

        # 尝试逐行解析 JSONL
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        # 如果逐行解析无结果，尝试整体解析（可能是多行 JSON 或 JSON 数组）
        if not results:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    results = parsed
                elif isinstance(parsed, dict):
                    results = [parsed]
            except json.JSONDecodeError:
                pass

        return results


def main():
    parser = argparse.ArgumentParser(description="赛题数据生成器")

    parser.add_argument("--mode", type=str, required=True,
                       choices=["student", "job", "batch_student", "batch_job"],
                       help="生成模式")
    parser.add_argument("--count", type=int, default=100, help="生成数量")
    parser.add_argument("--output", type=str, help="输出文件")
    parser.add_argument("--api-key", type=str, help="API Key")
    parser.add_argument("--provider", type=str, default="siliconflow",
                       choices=["deepseek", "openai", "siliconflow"])

    args = parser.parse_args()

    api_key = args.api_key
    if not api_key:
        if args.provider == "deepseek":
            api_key = os.environ.get("DEEPSEEK_API_KEY")
        elif args.provider == "siliconflow":
            api_key = os.environ.get("SILICONFLOW_API_KEY")
        else:
            api_key = os.environ.get("OPENAI_API_KEY")

    if not api_key:
        logger.error("请提供API Key: --api-key 或设置 SILICONFLOW_API_KEY / DEEPSEEK_API_KEY")
        return

    client = TeacherModelClient(api_key, args.provider)

    if args.mode == "student":
        generator = StudentProfileGenerator(client)
        output_path = Path(args.output or "student_profiles.jsonl")
        batch_size = 1
    elif args.mode == "job":
        generator = JobPostingGenerator(client)
        output_path = Path(args.output or "job_postings.jsonl")
        batch_size = 1
    elif args.mode == "batch_student":
        generator = BatchResumeGenerator(client)
        output_path = Path(args.output or "student_profiles.jsonl")
        batch_size = 10
    elif args.mode == "batch_job":
        generator = JobPostingGenerator(client)
        output_path = Path(args.output or "job_postings.jsonl")
        batch_size = 1
    else:
        logger.error("未知模式: %s", args.mode)
        return

    logger.info("=" * 60)
    logger.info("开始生成: %s", args.mode)
    logger.info("目标: %d", args.count)
    logger.info("输出: %s", output_path)
    logger.info("=" * 60)

    success = 0
    failed = 0

    if args.mode in ["batch_student", "batch_job"]:
        for i in range(0, args.count, batch_size):
            current_batch = min(batch_size, args.count - i)

            if args.mode == "batch_student":
                results = generator.generate_batch(current_batch)
            else:
                results = generator.generate_batch(current_batch)

            for result in results:
                with output_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")
                success += 1

            logger.info("进度: %d/%d | 成功: %d | 失败: %d",
                       min(i + batch_size, args.count), args.count, success, failed)
            time.sleep(1)
    else:
        for i in range(args.count):
            result = generator.generate(i)

            if result:
                with output_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")
                success += 1
            else:
                failed += 1

            if (i + 1) % 10 == 0:
                logger.info("进度: %d/%d | 成功: %d | 失败: %d",
                           i + 1, args.count, success, failed)

            time.sleep(0.5)

    logger.info("=" * 60)
    logger.info("生成完成: 成功=%d, 失败=%d", success, failed)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()