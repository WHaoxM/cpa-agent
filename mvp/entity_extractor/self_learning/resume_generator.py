#!/usr/bin/env python3
"""
Teacher Model Resume Generator
使用教师模型批量生成简历数据

功能：
1. 调用DeepSeek/GPT-4批量生成多样化简历
2. 支持断点续传
3. 自动去重和格式验证
4. 输出标准JSONL格式

使用方式：
    # 生成200份简历
    python resume_generator.py --count 200 --output resumes.jsonl

    # 继续生成（断点续传）
    python resume_generator.py --count 200 --output resumes.jsonl --resume
"""

import argparse
import hashlib
import json
import logging
import os
import random
import re
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class ResumeSpec:
    """简历规格定义"""
    target_position: str
    working_years: int
    degree: str
    major: str
    skill_focus: str
    project_focus: str
    company_type: str
    industry: str


class TeacherModelClient:
    """教师模型客户端"""

    def __init__(
        self,
        api_key: str,
        provider: str = "deepseek",
        model: str = None,
        request_timeout: int = 120,
    ):
        self.api_key = api_key
        self.provider = provider
        self.request_timeout = request_timeout

        # 配置不同provider的endpoint
        if provider == "deepseek":
            self.endpoint = "https://api.deepseek.com/v1/chat/completions"
            self.model = model or "deepseek-chat"
        elif provider == "openai":
            self.endpoint = "https://api.openai.com/v1/chat/completions"
            self.model = model or "gpt-4-turbo"
        elif provider == "siliconflow":
            self.endpoint = "https://api.siliconflow.cn/v1/chat/completions"
            self.model = model or "deepseek-ai/DeepSeek-V2.5"
        else:
            raise ValueError(f"Unknown provider: {provider}")

    def generate_resumes(
        self,
        specs: List[ResumeSpec],
        batch_size: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        批量生成简历

        Args:
            specs: 简历规格列表
            batch_size: 每批生成数量

        Returns:
            生成的简历列表
        """

        all_resumes = []

        for i in range(0, len(specs), batch_size):
            batch = specs[i:i+batch_size]

            logger.info("生成批次 %d/%d (%d 份)...",
                       i//batch_size + 1,
                       (len(specs) + batch_size - 1)//batch_size,
                       len(batch))

            # 构建批量Prompt
            prompt = self._build_batch_prompt(batch, len(batch))

            # 调用API
            response = self._call_api(prompt)

            if response:
                resumes = self._parse_jsonl_response(response)
                all_resumes.extend(resumes)

                logger.info("成功生成 %d 份，当前总计: %d",
                          len(resumes), len(all_resumes))
            else:
                logger.error("批次 %d 生成失败", i//batch_size + 1)

            # 避免速率限制
            time.sleep(1)

        return all_resumes

    def _build_batch_prompt(self, specs: List[ResumeSpec], index_offset: int = 0) -> str:
        """构建批量生成Prompt"""

        positions = [
            "后端开发工程师", "算法工程师", "全栈开发工程师",
            "前端开发工程师", "数据工程师", "DevOps工程师",
            "测试开发工程师", "运维工程师", "DBA", "架构师",
        ]

        companies = [
            "阿里巴巴", "腾讯科技", "字节跳动", "百度", "美团",
            "京东", "网易", "快手科技", "小米科技", "华为技术",
            "拼多多", "滴滴出行", "携程旅行", "哔哩哔哩", "小红书",
        ]

        schools = [
            "清华大学", "北京大学", "浙江大学", "上海交通大学",
            "复旦大学", "中国科学技术大学", "南京大学", "哈尔滨工业大学",
            "西安交通大学", "同济大学", "北京航空航天大学", "武汉大学",
        ]

        skill_sets = [
            {"lang": "Python", "frame": "FastAPI/Django", "db": "MySQL/Redis", "ai": "TensorFlow/LangChain"},
            {"lang": "Java", "frame": "Spring Boot", "db": "PostgreSQL/MongoDB", "ai": "PyTorch"},
            {"lang": "Go", "frame": "Gin", "db": "MySQL/Neo4j", "ai": ""},
            {"lang": "JavaScript", "frame": "React/Vue", "db": "MongoDB/Redis", "ai": ""},
            {"lang": "C++", "frame": "", "db": "ClickHouse", "ai": "PyTorch"},
        ]

        project_templates = [
            "智能推荐系统", "实时数据处理平台", "知识图谱系统", "多模态Agent平台",
            "RAG检索系统", "分布式爬虫系统", "自动化测试平台", "微服务架构平台",
            "用户画像系统", "风控决策引擎", "日志分析平台", "配置管理中心",
        ]

        prompt_parts = [
            "你是一个专业的简历生成专家。请一次性生成%d份不同的简历JSON。\n" % len(specs),
            "要求：\n",
            "1. 每份简历必须包含：personal_info, education, work_experience, project_experience, skills\n",
            "2. 使用不同的姓名（中文或英文）、公司（从列表中选择）、学校（从列表中选择）\n",
            "3. 日期格式：YYYY-MM 或 至今\n",
            "4. phone格式：138****8888（脱敏）\n",
            "5. 每份简历的work_experience至少2段，project_experience至少3个\n",
            "6. 输出格式：每行一个完整JSON，以JSONL格式输出\n\n",
            "参考信息池：\n",
            f"公司池：{', '.join(companies)}\n",
            f"学校池：{', '.join(schools)}\n",
            f"岗位池：{', '.join(positions)}\n",
            f"项目池：{', '.join(project_templates)}\n\n",
            "请直接输出JSONL，不要有任何解释文字：\n",
        ]

        return "".join(prompt_parts)

    def _call_api(self, prompt: str, retry_count: int = 3) -> Optional[str]:
        """调用API"""

        for attempt in range(retry_count):
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
                            {"role": "system", "content": "你是一个专业的简历生成专家。输出必须是合法的JSONL格式，每行一个JSON，不要有任何其他文字。"},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.8,
                        "max_tokens": 8192,
                    },
                    timeout=self.request_timeout,
                )

                response.raise_for_status()
                data = response.json()

                return data["choices"][0]["message"]["content"]

            except requests.exceptions.Timeout:
                logger.warning("API超时 (尝试 %d/%d)", attempt + 1, retry_count)
            except Exception as e:
                logger.error("API调用失败: %s", e)

            # 指数退避
            if attempt < retry_count - 1:
                wait_time = (2 ** attempt) * random.uniform(1, 3)
                logger.info("%.1f秒后重试...", wait_time)
                time.sleep(wait_time)

        return None

    def _parse_jsonl_response(self, content: str) -> List[Dict[str, Any]]:
        """解析JSONL响应"""

        resumes = []

        for line in content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue

            # 尝试解析
            try:
                # 处理可能的markdown代码块
                if line.startswith("```"):
                    line = line.strip("```").strip()
                    if line.startswith("json"):
                        line = line[4:].strip()

                resume = json.loads(line)
                resumes.append(resume)

            except json.JSONDecodeError as e:
                logger.debug("JSON解析失败: %s, 内容: %.50s...", e, line[:100])

        return resumes


class ResumeValidator:
    """简历验证器"""

    REQUIRED_FIELDS = ["resume_id", "personal_info", "education", "work_experience", "skills"]

    def validate(self, resume: Dict[str, Any]) -> Tuple[bool, str]:
        """验证简历格式"""

        # 检查必填字段
        for field in self.REQUIRED_FIELDS:
            if field not in resume:
                return False, f"缺少必填字段: {field}"

        # 检查personal_info
        pi = resume.get("personal_info", {})
        if "name" not in pi:
            return False, "缺少姓名"

        if "contact" in pi:
            contact = pi["contact"]
            if "phone" in contact:
                phone = contact["phone"]
                if not re.match(r"\d{3}\*{4}\d{4}", phone):
                    return False, f"手机号格式错误: {phone}"

        # 检查教育经历
        education = resume.get("education", [])
        if not education:
            return False, "缺少教育经历"

        # 检查工作经历
        work_exp = resume.get("work_experience", [])
        if not work_exp:
            return False, "缺少工作经历"

        # 检查技能
        skills = resume.get("skills", {})
        if not skills:
            return False, "缺少技能"

        return True, "OK"

    def validate_batch(self, resumes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """批量验证"""

        valid = []
        invalid = []

        for resume in resumes:
            is_valid, msg = self.validate(resume)
            if is_valid:
                valid.append(resume)
            else:
                invalid.append({
                    "resume": resume,
                    "error": msg,
                })

        return {
            "total": len(resumes),
            "valid": len(valid),
            "invalid": len(invalid),
            "valid_resumes": valid,
            "invalid_resumes": invalid,
        }


class ResumeGenerator:
    """
    简历生成器 - 主控制器
    """

    def __init__(
        self,
        output_path: Path,
        api_key: str,
        provider: str = "deepseek",
    ):
        self.output_path = output_path
        self.api_key = api_key
        self.provider = provider

        self.client = TeacherModelClient(api_key, provider)
        self.validator = ResumeValidator()

        # 已生成的简历ID集合（用于去重）
        self.generated_ids: Set[str] = set()
        self.duplicate_count = 0

        # 加载已有数据
        self._load_existing()

    def _load_existing(self):
        """加载已生成的简历"""
        if self.output_path.exists():
            with self.output_path.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        rid = data.get("resume_id", "")
                        if rid:
                            self.generated_ids.add(rid)
                    except:
                        pass

            logger.info("已加载 %d 份已有简历", len(self.generated_ids))

    def generate(
        self,
        target_count: int,
        batch_size: int = 10,
    ) -> Dict[str, Any]:
        """
        生成简历

        Args:
            target_count: 目标数量
            batch_size: 每批生成数量

        Returns:
            生成统计
        """

        logger.info("=" * 60)
        logger.info("开始生成简历")
        logger.info("目标: %d 份", target_count)
        logger.info("已存在: %d 份", len(self.generated_ids))
        logger.info("=" * 60)

        generated = 0
        attempts = 0
        max_attempts = target_count * 2  # 最多尝试2倍数量

        while generated < target_count and attempts < max_attempts:
            attempts += 1

            # 构建规格
            specs = self._generate_specs(min(batch_size, target_count - generated))

            # 调用API生成
            resumes = self.client.generate_resumes(specs)

            if not resumes:
                logger.warning("批次生成失败，重试...")
                continue

            # 验证和去重
            for resume in resumes:
                rid = resume.get("resume_id", f"resume_{len(self.generated_ids):06d}")

                # 去重
                if rid in self.generated_ids:
                    self.duplicate_count += 1
                    logger.debug("去重: %s", rid)
                    continue

                # 验证
                is_valid, msg = self.validator.validate(resume)
                if not is_valid:
                    logger.debug("验证失败: %s - %s", rid, msg)
                    continue

                # 保存
                resume["resume_id"] = rid
                self._save_resume(resume)
                self.generated_ids.add(rid)
                generated += 1

                if generated >= target_count:
                    break

            # 进度报告
            logger.info("进度: %d/%d (%.1f%%) | 去重: %d",
                       generated, target_count,
                       generated/target_count*100,
                       self.duplicate_count)

        # 保存最终统计
        stats = {
            "target": target_count,
            "generated": generated,
            "attempts": attempts,
            "duplicates": self.duplicate_count,
            "total_in_file": len(self.generated_ids),
        }

        logger.info("=" * 60)
        logger.info("生成完成")
        logger.info("目标: %d | 生成: %d | 去重: %d | 总计: %d",
                    target_count, generated, self.duplicate_count,
                    len(self.generated_ids))
        logger.info("=" * 60)

        return stats

    def _generate_specs(self, count: int) -> List[ResumeSpec]:
        """生成简历规格"""

        positions = [
            "后端开发工程师", "算法工程师", "全栈开发工程师",
            "前端开发工程师", "数据工程师", "DevOps工程师",
        ]

        majors = [
            "计算机科学与技术", "软件工程", "人工智能",
            "数据科学与大数据技术", "网络工程", "信息安全",
        ]

        skill_focus = [
            "Python/LangChain", "Java/Spring", "Go/云原生",
            "JavaScript/React", "Python/数据分析", "C++/系统",
        ]

        project_focus = [
            "AI应用开发", "后台服务", "数据平台", "分布式系统",
            "微服务架构", "推荐系统", "搜索引擎", "实时计算",
        ]

        specs = []
        for i in range(count):
            spec = ResumeSpec(
                target_position=random.choice(positions),
                working_years=random.randint(1, 10),
                degree=random.choice(["本科", "硕士", "博士"]),
                major=random.choice(majors),
                skill_focus=random.choice(skill_focus),
                project_focus=random.choice(project_focus),
                company_type=random.choice(["互联网", "金融", "教育"]),
                industry=random.choice(["互联网", "电商", "社交"]),
            )
            specs.append(spec)

        return specs

    def _save_resume(self, resume: Dict[str, Any]):
        """保存简历到文件"""
        with self.output_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(resume, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Teacher Model简历生成器")

    parser.add_argument("--count", type=int, default=200, help="生成数量")
    parser.add_argument("--output", type=str, default="./generated_resumes.jsonl", help="输出文件")
    parser.add_argument("--provider", type=str, default="deepseek", choices=["deepseek", "openai", "siliconflow"],
                       help="API提供商")
    parser.add_argument("--api-key", type=str, help="API Key（可从环境变量读取）")
    parser.add_argument("--batch-size", type=int, default=10, help="每批生成数量")
    parser.add_argument("--resume", action="store_true", help="断点续传")

    args = parser.parse_args()

    # 获取API Key
    api_key = args.api_key
    if not api_key:
        # 尝试环境变量
        if args.provider == "deepseek":
            api_key = os.environ.get("DEEPSEEK_API_KEY")
        elif args.provider == "openai":
            api_key = os.environ.get("OPENAI_API_KEY")
        elif args.provider == "siliconflow":
            api_key = os.environ.get("SILICONFLOW_API_KEY")

    if not api_key:
        logger.error("请提供API Key --api-key 或设置环境变量")
        return

    # 创建生成器
    output_path = Path(args.output)

    generator = ResumeGenerator(
        output_path=output_path,
        api_key=api_key,
        provider=args.provider,
    )

    # 信号处理
    def signal_handler(signum, frame):
        logger.info("收到中断信号，正在保存...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # 生成
    stats = generator.generate(
        target_count=args.count,
        batch_size=args.batch_size,
    )

    # 验证生成的数据
    logger.info("\n验证生成的数据...")

    validator = ResumeValidator()
    valid_count = 0
    invalid_count = 0

    with output_path.open(encoding="utf-8") as f:
        for line in f:
            try:
                resume = json.loads(line.strip())
                is_valid, _ = validator.validate(resume)
                if is_valid:
                    valid_count += 1
                else:
                    invalid_count += 1
            except:
                invalid_count += 1

    logger.info("验证结果: 有效=%d, 无效=%d", valid_count, invalid_count)


if __name__ == "__main__":
    main()