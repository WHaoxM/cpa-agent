#!/usr/bin/env python3
"""
简历NER训练数据生成器
参考真实简历数据格式：entity_extraction_result_nsle.json

生成数据格式与真实数据一致：
- blocks: 包含block_id, block_type, heading, confidence, original_text, masked_text, tagged_text
- pre_extracted: skills, dates, degrees, roles
- llm_output: entities列表
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
        temperature: float = 0.85,
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


class ResumeBlockGenerator:
    """
    简历文本块生成器
    生成与真实简历格式一致的文本块
    """

    SYSTEM_PROMPT = """你是一个简历数据生成专家，擅长生成多样化的简历文本数据。

核心原则：
1. 生成真实、自然的简历文本
2. 不要用占位符，直接生成完整内容
3. 直接输出JSON，不要有任何解释
4. 文本要符合中文简历的书写习惯"""

    USER_PROMPT_TEMPLATE = """请生成一份简历JSON，包含多个文本块(block)：

场景约束：
- 姓名：{name}
- 求职方向：{position}
- 教育背景：{education}
- 工作经验：{experience}

要求：
生成JSON格式如下：
{{
  "blocks": [
    {{
      "block_type": "PERSONAL_INFO",
      "heading": "姓名或职位标题",
      "text": "钟梓珉\n大模型应用/Agent开发工程师"
    }},
    {{
      "block_type": "SKILLS_CERTS",
      "heading": "专业技能",
      "text": "熟悉 Python、Java，具备后端开发与 AI 应用开发基础\\n熟悉 FastAPI、Flask、Spring Boot，能够完成接口开发与服务搭建\\n熟悉 LangChain、LangGraph，具备 Agent 工作流编排与多 Agent 协作实践经验\\n熟悉 MySQL、Neo4j、Milvus，了解关系型、图数据库与向量数据库的基本应用\\n熟练使用 Git、Docker，具备基础版本管理与容器化部署能力\\n了解 RAG、知识图谱、多模态应用 的基本原理与实践方式"
    }},
    {{
      "block_type": "PROJECT_EXPERIENCE",
      "heading": "项目名称",
      "text": "项目描述和主要内容..."
    }},
    {{
      "block_type": "EDUCATION",
      "heading": "学校名称",
      "text": "专业 · 学历\\n主修课程：...\\n2024/09 - 至今"
    }}
  ]
}}

内容要求：
1. PERSONAL_INFO块：包含真实姓名和职位
2. SKILLS_CERTS块：列出8-15项技能，每项一行
3. PROJECT_EXPERIENCE块：至少3个，每个包含时间、角色、项目描述、主要工作
4. WORK_EXPERIENCE块：1-2段工作经历
5. EDUCATION块：学校、专业、学历、时间

数据要多样化！不同简历要有不同的技能、项目、经历。

直接输出JSON："""

    NAMES = [
        "随机生成中文或英文姓名"
    ]

    POSITIONS = [
        "大模型应用开发工程师",
        "后端开发工程师",
        "算法工程师",
        "全栈开发工程师",
        "前端开发工程师",
        "数据工程师",
    ]

    EDUCATIONS = [
        "本科",
        "硕士",
        "博士",
        "大专",
    ]

    def __init__(self, client: TeacherModelClient):
        self.client = client

    def generate(self, index: int) -> Optional[Dict[str, Any]]:
        """生成单份简历的blocks"""

        user_prompt = self.USER_PROMPT_TEMPLATE.format(
            name="随机生成",
            position=random.choice(self.POSITIONS),
            education=random.choice(self.EDUCATIONS),
            experience=random.randint(0, 5),
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


class NERAnnotationGenerator:
    """
    NER标注生成器
    对简历文本进行实体标注
    """

    SYSTEM_PROMPT = """你是一个NER标注专家，擅长从简历文本中提取实体。

实体类型定义：
- PERSON_NAME: 人名
- POSITION: 职位名称
- SKILL: 技能（编程语言、框架、工具、库）
- DATE_RANGE: 日期范围（如2024/01 - 至今，或 至今）
- PROJECT_ROLE: 项目中的角色（如核心开发者、成员、技术负责人）
- SCHOOL: 学校名称
- MAJOR: 专业名称
- DEGREE: 学历
- COMPANY: 公司名称
- PROJECT_NAME: 项目名称
- TOOL: 工具软件
- CERTIFICATE: 证书名称

输出要求：
1. 直接输出JSON
2. 只输出entities列表
3. span必须是原文中的连续字符"""

    USER_PROMPT_TEMPLATE = """给定以下简历文本块，提取所有实体：

{block_type}块：
{text}

要求：
1. 识别所有实体及其类型
2. span必须是原文中的连续字符序列
3. 日期范围要包含起止时间
4. 只输出entities列表

输出格式：
{{"entities": [{{"type": "实体类型", "span": "原文"}}]}}

直接输出JSON："""

    def __init__(self, client: TeacherModelClient):
        self.client = client

    def annotate(self, block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """对单个block进行标注"""

        user_prompt = self.USER_PROMPT_TEMPLATE.format(
            block_type=block.get("block_type", ""),
            text=block.get("text", ""),
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
    一次调用生成多份简历
    """

    SYSTEM_PROMPT = """你是一个简历数据生成专家。"""

    USER_PROMPT_TEMPLATE = """请一次性生成{number}份简历JSON，每份包含多个文本块。

要求：
1. 输出格式为JSONL（每行一个JSON）
2. 每份简历包含blocks数组
3. 块类型：PERSONAL_INFO, SKILLS_CERTS, EDUCATION, WORK_EXPERIENCE, PROJECT_EXPERIENCE
4. 内容要多样化：不同姓名、技能、项目、学校
5. 不要用"某大学"、"某公司"等模糊表述
6. 每份简历要有3-5个PROJECT_EXPERIENCE块

JSON格式：
{{"blocks": [{{"block_type": "类型", "heading": "标题", "text": "内容"}}]}}

直接输出{number}行JSON："""

    def __init__(self, client: TeacherModelClient):
        self.client = client

    def generate_batch(self, count: int = 10) -> List[Dict[str, Any]]:
        """批量生成"""

        user_prompt = self.USER_PROMPT_TEMPLATE.format(number=count)

        response = self.client.generate(
            self.SYSTEM_PROMPT,
            user_prompt,
            temperature=0.9,
            max_tokens=8192,
        )

        if not response:
            return []

        results = []
        for line in response.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                if line.startswith("```"):
                    line = line.strip("```").strip()
                    if line.startswith("json"):
                        line = line[4:].strip()
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        return results


def main():
    parser = argparse.ArgumentParser(description="简历NER训练数据生成器")

    parser.add_argument("--mode", type=str, required=True,
                       choices=["resume_text", "ner", "batch"],
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

    if args.mode == "resume_text":
        generator = ResumeBlockGenerator(client)
        output_path = Path(args.output or "resume_blocks.jsonl")
        batch_size = 1
    elif args.mode == "ner":
        output_path = Path(args.output or "ner_annotations.jsonl")
        batch_size = 1
    else:
        generator = BatchResumeGenerator(client)
        output_path = Path(args.output or "resume_blocks.jsonl")
        batch_size = 10

    logger.info("=" * 60)
    logger.info("开始生成: %s", args.mode)
    logger.info("目标: %d", args.count)
    logger.info("输出: %s", output_path)
    logger.info("=" * 60)

    success = 0
    failed = 0

    if args.mode == "batch":
        for i in range(0, args.count, batch_size):
            current_batch = min(batch_size, args.count - i)
            results = generator.generate_batch(current_batch)

            for result in results:
                with output_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")
                success += 1

            logger.info("进度: %d/%d | 成功: %d | 失败: %d",
                       min(i + batch_size, args.count), args.count, success, failed)
            time.sleep(1)
    else:
        generator = ResumeBlockGenerator(client)
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