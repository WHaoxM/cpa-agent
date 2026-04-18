#!/usr/bin/env python3
"""
自主学习闭环控制器 (Autonomous Learning Loop)

3-Phase 架构：
  Phase 1: 教师数据工厂 (Teacher Data Factory)
    - 教师模型 API 提取 + NS-LE 规则引擎预提取
    - ConsensusGuard 交叉共识质检
    - 冲突 → Human-in-the-loop 队列 / 一致 → Gold Data
  Phase 1.5: 合成数据生成
    - 教师模型学习 Gold 数据模式 → 批量生成合成简历
    - ConsensusGuard 验证 → Silver Data
  Phase 2: 渐进式蒸馏 (Progressive Distillation)
    - ReplayBuffer 经验回放（防灾难性遗忘）
    - DistillationPipeline + SFTTrainer → Qwen3-0.6B LoRA 微调
  Phase 3: 边缘自主推理 (Edge Autonomous Inference)
    - 学生模型推理 + OOD 分布外检测
    - confidence > 0.95 → 直接输出
    - confidence < 0.95 / 规则报错 → 回流 Phase 1

用法：
    loop = AutonomousLearningLoop(api_keys={"deepseek": "sk-xxx"})
    result = loop.ingest_resume("简历文本...")
    status = loop.get_loop_status()
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  配置
# ---------------------------------------------------------------------------

@dataclass
class LoopConfig:
    """闭环控制器配置"""
    # Phase 1
    gold_dir: str = "./self_learning_data/gold"
    silver_dir: str = "./self_learning_data/silver"
    human_queue_dir: str = "./self_learning_data/human_queue"
    consensus_threshold: float = 0.8
    min_entity_count: int = 3

    # Phase 1.5 触发条件
    synthesis_trigger_count: int = 5       # 每累积 N 份 Gold → 触发合成
    synthetic_per_trigger: int = 10        # 每次生成 N 条合成数据

    # Phase 2 触发条件
    training_trigger_count: int = 100      # Gold+Silver ≥ N → 触发 SFT
    replay_buffer_capacity: int = 500      # 经验回放池容量

    # Phase 3
    ood_confidence_threshold: float = 0.95 # 高于此直接输出
    ood_method: str = "cosine"             # cosine | mahalanobis

    # API
    request_timeout: int = 120
    max_retries: int = 3


# ---------------------------------------------------------------------------
#  ReplayBuffer — 经验回放池
# ---------------------------------------------------------------------------

class ReplayBuffer:
    """
    经验回放池 — 防灾难性遗忘
    按 entity_type 和 difficulty 分层采样，保持训练数据分布平衡
    """

    def __init__(self, capacity: int = 500):
        self.capacity = capacity
        self._buffer: List[Dict[str, Any]] = []
        self._type_index: Dict[str, List[int]] = defaultdict(list)
        self._difficulty_index: Dict[str, List[int]] = defaultdict(list)

    def add(self, samples: List[Dict[str, Any]]):
        """添加样本到回放池"""
        for sample in samples:
            if len(self._buffer) >= self.capacity:
                self._evict()

            idx = len(self._buffer)
            self._buffer.append(sample)

            # 建立索引
            for entity in sample.get("entities", []):
                etype = entity.get("type", "UNKNOWN")
                self._type_index[etype].append(idx)

            difficulty = sample.get("difficulty", "medium")
            self._difficulty_index[difficulty].append(idx)

    def sample(self, batch_size: int, strategy: str = "stratified") -> List[Dict[str, Any]]:
        """
        采样训练 batch

        Args:
            batch_size: 采样数量
            strategy: 采样策略
                - "uniform": 均匀随机采样
                - "stratified": 按 entity_type 分层采样
                - "difficulty": 按难度分层采样（hard 样本更高概率）
        """
        if not self._buffer:
            return []

        batch_size = min(batch_size, len(self._buffer))

        if strategy == "uniform":
            indices = random.sample(range(len(self._buffer)), batch_size)

        elif strategy == "stratified":
            indices = self._stratified_sample(batch_size)

        elif strategy == "difficulty":
            indices = self._difficulty_weighted_sample(batch_size)

        else:
            indices = random.sample(range(len(self._buffer)), batch_size)

        return [self._buffer[i] for i in indices]

    def _stratified_sample(self, batch_size: int) -> List[int]:
        """按 entity_type 分层采样"""
        all_types = list(self._type_index.keys())
        if not all_types:
            return random.sample(range(len(self._buffer)), min(batch_size, len(self._buffer)))

        per_type = max(1, batch_size // len(all_types))
        indices = set()

        for etype in all_types:
            type_indices = self._type_index[etype]
            valid = [i for i in type_indices if i < len(self._buffer)]
            if valid:
                sampled = random.sample(valid, min(per_type, len(valid)))
                indices.update(sampled)

        # 补足
        remaining = batch_size - len(indices)
        if remaining > 0:
            available = set(range(len(self._buffer))) - indices
            if available:
                extra = random.sample(list(available), min(remaining, len(available)))
                indices.update(extra)

        return list(indices)[:batch_size]

    def _difficulty_weighted_sample(self, batch_size: int) -> List[int]:
        """按难度加权采样（hard 样本 3x 权重）"""
        weights = {"easy": 1.0, "medium": 2.0, "hard": 3.0}
        weighted_indices = []

        for diff, idx_list in self._difficulty_index.items():
            w = weights.get(diff, 1.0)
            valid = [i for i in idx_list if i < len(self._buffer)]
            weighted_indices.extend([(i, w) for i in valid])

        if not weighted_indices:
            return random.sample(range(len(self._buffer)), min(batch_size, len(self._buffer)))

        indices, ws = zip(*weighted_indices)
        total_w = sum(ws)
        probs = [w / total_w for w in ws]

        chosen = np.random.choice(list(indices), size=min(batch_size, len(indices)),
                                  replace=False, p=probs[:len(indices)])
        return chosen.tolist()

    def _evict(self):
        """驱逐策略：移除最早的 easy 样本"""
        easy_indices = self._difficulty_index.get("easy", [])
        valid = [i for i in easy_indices if i < len(self._buffer)]

        if valid:
            remove_idx = valid[0]
        else:
            remove_idx = 0

        self._buffer.pop(remove_idx)
        self._rebuild_indices()

    def _rebuild_indices(self):
        """重建索引"""
        self._type_index.clear()
        self._difficulty_index.clear()

        for idx, sample in enumerate(self._buffer):
            for entity in sample.get("entities", []):
                etype = entity.get("type", "UNKNOWN")
                self._type_index[etype].append(idx)
            difficulty = sample.get("difficulty", "medium")
            self._difficulty_index[difficulty].append(idx)

    def rebalance(self):
        """重平衡：确保各类型数据比例均匀"""
        type_counts = {k: len(v) for k, v in self._type_index.items()}
        if not type_counts:
            return

        avg_count = sum(type_counts.values()) / len(type_counts)
        logger.info("ReplayBuffer 重平衡: %s (avg=%.1f)", type_counts, avg_count)

    def __len__(self) -> int:
        return len(self._buffer)

    def get_stats(self) -> Dict[str, Any]:
        """获取回放池统计"""
        return {
            "size": len(self._buffer),
            "capacity": self.capacity,
            "type_distribution": {k: len(v) for k, v in self._type_index.items()},
            "difficulty_distribution": {k: len(v) for k, v in self._difficulty_index.items()},
        }


# ---------------------------------------------------------------------------
#  OODDetector — 分布外异常检测
# ---------------------------------------------------------------------------

class OODDetector:
    """
    分布外异常检测器
    用于 Phase 3 判断学生模型是否遇到训练分布外的输入

    方案：
    - cosine: embedding 与 Gold 质心的余弦距离（轻量，适合 MVP）
    - mahalanobis: Mahalanobis 距离（更准，需要协方差矩阵）
    """

    def __init__(self, method: str = "cosine", threshold: float = 0.95):
        self.method = method
        self.threshold = threshold
        self._centroid: Optional[np.ndarray] = None
        self._embeddings: List[np.ndarray] = []
        self._covariance_inv: Optional[np.ndarray] = None
        self._is_fitted = False

    def fit(self, embeddings: List[List[float]]):
        """
        用 Gold 数据的 embeddings 建立分布基线

        Args:
            embeddings: Gold 数据的 embedding 向量列表
        """
        if not embeddings:
            logger.warning("OODDetector.fit: 空 embeddings，跳过")
            return

        arr = np.array(embeddings, dtype=np.float32)
        self._embeddings = [e for e in arr]
        self._centroid = arr.mean(axis=0)

        if self.method == "mahalanobis" and len(arr) > 1:
            cov = np.cov(arr.T)
            try:
                self._covariance_inv = np.linalg.inv(cov + np.eye(cov.shape[0]) * 1e-6)
            except np.linalg.LinAlgError:
                logger.warning("协方差矩阵奇异，回退到 cosine 方法")
                self.method = "cosine"

        self._is_fitted = True
        logger.info("OODDetector fitted: method=%s, n_samples=%d, dim=%d",
                     self.method, len(arr), arr.shape[1])

    def score(self, embedding: List[float]) -> float:
        """
        计算 OOD 分数

        Args:
            embedding: 待检测样本的 embedding

        Returns:
            in-distribution 置信度 [0, 1]，越高越正常
        """
        if not self._is_fitted or self._centroid is None:
            return 0.5  # 未拟合时返回中等置信度

        emb = np.array(embedding, dtype=np.float32)

        if self.method == "cosine":
            return self._cosine_score(emb)
        elif self.method == "mahalanobis":
            return self._mahalanobis_score(emb)
        else:
            return self._cosine_score(emb)

    def _cosine_score(self, emb: np.ndarray) -> float:
        """余弦相似度方法"""
        norm_emb = np.linalg.norm(emb)
        norm_centroid = np.linalg.norm(self._centroid)

        if norm_emb == 0 or norm_centroid == 0:
            return 0.0

        cosine_sim = np.dot(emb, self._centroid) / (norm_emb * norm_centroid)
        # 将 [-1, 1] 映射到 [0, 1]
        return float((cosine_sim + 1.0) / 2.0)

    def _mahalanobis_score(self, emb: np.ndarray) -> float:
        """Mahalanobis 距离方法"""
        if self._covariance_inv is None:
            return self._cosine_score(emb)

        diff = emb - self._centroid
        dist = float(np.sqrt(diff @ self._covariance_inv @ diff))

        # 用 sigmoid 将距离映射到 [0, 1] 置信度
        confidence = 1.0 / (1.0 + math.exp(dist - 5.0))
        return confidence

    def is_ood(self, embedding: List[float]) -> bool:
        """判断是否 OOD"""
        return self.score(embedding) < self.threshold

    def get_stats(self) -> Dict[str, Any]:
        return {
            "method": self.method,
            "threshold": self.threshold,
            "is_fitted": self._is_fitted,
            "n_reference_samples": len(self._embeddings),
        }


# ---------------------------------------------------------------------------
#  FieldConstraintValidator — 字段约束校验器
# ---------------------------------------------------------------------------

class FieldConstraintValidator:
    """
    基于 resume_field_constraints.json 的字段约束校验器
    用于 Phase 1 的 Gold 数据质检 和 Phase 1.5 的合成数据验证
    """

    # 正则预编译
    PHONE_PATTERN = re.compile(r"^1[3-9]\d{1}\*{4}\d{4}$")
    DATE_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
    DATE_OR_NOW = re.compile(r"^(\d{4}-(0[1-9]|1[0-2])|至今)$")
    QUANTIFIER_PATTERN = re.compile(r"\d+[%万亿千百倍]|\d+\s*(个|条|次|人|台|ms|QPS|TPS)")
    CN_NAME_PATTERN = re.compile(r"^[\u4e00-\u9fa5]{2,6}$")
    EN_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z .'-]{1,30}$")
    GPA_PATTERN = re.compile(r"^\d{1}\.\d{1,2}/[45]\.0$")

    VALID_DEGREES = {"博士", "硕士", "本科", "专科", "高中", "PhD", "Master", "Bachelor", "Associate"}
    SCHOOL_SUFFIXES = ("大学", "学院", "学校", "University", "Institute", "College", "School")
    KNOWN_SCHOOL_ABBRS = {"MIT", "ETH", "CMU", "UCLA", "NYU", "NUS", "HKUST"}

    def validate(self, resume: Dict[str, Any]) -> Tuple[bool, float, List[str]]:
        """
        校验简历数据

        Returns:
            (is_valid, quality_score, error_messages)
        """
        errors = []
        warnings = []

        # personal_info
        pi = resume.get("personal_info", {})
        if not pi:
            errors.append("missing personal_info")
        else:
            self._validate_personal_info(pi, errors, warnings)

        # education
        edu = resume.get("education", [])
        if not edu:
            errors.append("missing education")
        else:
            self._validate_education(edu, errors, warnings)

        # work_experience
        work = resume.get("work_experience", [])
        self._validate_work_experience(work, errors, warnings)

        # project_experience
        projects = resume.get("project_experience", [])
        self._validate_project_experience(projects, errors, warnings)

        # skills
        skills = resume.get("skills", {})
        self._validate_skills(skills, errors, warnings)

        # 跨字段一致性
        self._validate_cross_field(resume, errors, warnings)

        # 质量评分
        score = self._compute_quality_score(resume, errors, warnings)

        is_valid = len(errors) == 0
        all_messages = [f"[ERROR] {e}" for e in errors] + [f"[WARN] {w}" for w in warnings]

        return is_valid, score, all_messages

    def _validate_personal_info(self, pi: dict, errors: list, warnings: list):
        name = pi.get("name", "")
        if name and not (self.CN_NAME_PATTERN.match(name) or self.EN_NAME_PATTERN.match(name)):
            if len(name) < 2 or len(name) > 20:
                errors.append(f"name length invalid: '{name}' ({len(name)} chars)")

        contact = pi.get("contact", {})
        phone = contact.get("phone", "")
        if phone and not self.PHONE_PATTERN.match(phone):
            warnings.append(f"phone format invalid: '{phone}' (expected: 138****8888)")

        email = contact.get("email", "")
        if email and "@" not in email:
            errors.append(f"email format invalid: '{email}'")

    def _validate_education(self, edu: list, errors: list, warnings: list):
        for i, entry in enumerate(edu):
            school = entry.get("school", "")
            if school:
                has_suffix = any(school.endswith(s) for s in self.SCHOOL_SUFFIXES)
                is_known = school.upper() in self.KNOWN_SCHOOL_ABBRS
                if not has_suffix and not is_known and len(school) < 4:
                    warnings.append(f"education[{i}].school unrecognizable: '{school}'")

            degree = entry.get("degree", "")
            if degree and degree not in self.VALID_DEGREES:
                warnings.append(f"education[{i}].degree unknown: '{degree}'")

            self._validate_duration(entry.get("duration", {}), f"education[{i}]", errors)

            gpa = entry.get("gpa", "")
            if gpa and not self.GPA_PATTERN.match(str(gpa)):
                warnings.append(f"education[{i}].gpa format: '{gpa}' (expected: X.X/4.0)")

    def _validate_work_experience(self, work: list, errors: list, warnings: list):
        for i, entry in enumerate(work):
            company = entry.get("company", "")
            if company and len(company) < 2:
                warnings.append(f"work_experience[{i}].company too short: '{company}'")

            position = entry.get("position", "")
            if position and len(position) < 2:
                warnings.append(f"work_experience[{i}].position too short: '{position}'")

            self._validate_duration(entry.get("duration", {}), f"work_experience[{i}]", errors)

            # 检查 achievements 量化
            achievements = entry.get("achievements", [])
            has_quantifier = any(self.QUANTIFIER_PATTERN.search(a) for a in achievements)
            if achievements and not has_quantifier:
                warnings.append(f"work_experience[{i}].achievements: no quantified metrics found")

    def _validate_project_experience(self, projects: list, errors: list, warnings: list):
        for i, entry in enumerate(projects):
            name = entry.get("name", "")
            if name and len(name) < 2:
                warnings.append(f"project_experience[{i}].name too short: '{name}'")

            self._validate_duration(entry.get("duration", {}), f"project_experience[{i}]", errors)

            results = entry.get("results", "")
            if results and not self.QUANTIFIER_PATTERN.search(results):
                warnings.append(f"project_experience[{i}].results: no quantified metrics")

    def _validate_skills(self, skills: dict, errors: list, warnings: list):
        total_count = 0
        for category in ["programming_languages", "frameworks", "databases", "tools", "ai_ml", "soft_skills"]:
            items = skills.get(category, [])
            if isinstance(items, list):
                total_count += len(items)

        if total_count < 5:
            warnings.append(f"skills total count too low: {total_count} (min: 5)")
        elif total_count > 50:
            warnings.append(f"skills total count too high: {total_count} (max: 50)")

    def _validate_duration(self, duration: dict, prefix: str, errors: list):
        if not duration:
            return
        start = duration.get("start", "")
        end = duration.get("end", "")

        if start and not self.DATE_PATTERN.match(start):
            errors.append(f"{prefix}.duration.start invalid: '{start}'")

        if end and not self.DATE_OR_NOW.match(end):
            errors.append(f"{prefix}.duration.end invalid: '{end}'")

        # 时间顺序
        if start and end and end != "至今":
            if self.DATE_PATTERN.match(start) and self.DATE_PATTERN.match(end):
                if start > end:
                    errors.append(f"{prefix}.duration: start ({start}) > end ({end})")

    def _validate_cross_field(self, resume: dict, errors: list, warnings: list):
        """跨字段一致性校验"""
        # CFR-003: 技能在经历中使用
        all_skills = set()
        skills = resume.get("skills", {})
        for category in ["programming_languages", "frameworks", "databases", "tools", "ai_ml"]:
            for s in skills.get(category, []):
                all_skills.add(s.lower())

        all_techs = set()
        for work in resume.get("work_experience", []):
            for t in work.get("technologies_used", []):
                all_techs.add(t.lower())
        for proj in resume.get("project_experience", []):
            for t in proj.get("technologies", []):
                all_techs.add(t.lower())

        if all_skills and all_techs:
            overlap = all_skills & all_techs
            if not overlap:
                warnings.append("CFR-003: skills and experience technologies have no overlap")

        # CFR-005: 未来日期
        current_date = datetime.now().strftime("%Y-%m")
        for section in ["education", "work_experience", "project_experience"]:
            for i, entry in enumerate(resume.get(section, [])):
                dur = entry.get("duration", {})
                for field_name in ["start", "end"]:
                    date_val = dur.get(field_name, "")
                    if date_val and date_val != "至今" and self.DATE_PATTERN.match(date_val):
                        if date_val > current_date:
                            warnings.append(f"CFR-005: {section}[{i}].{field_name} is future: {date_val}")

    def _compute_quality_score(self, resume: dict, errors: list, warnings: list) -> float:
        """四维质量评分"""
        scores = {}

        # Completeness (0.3)
        completeness_checks = [
            bool(resume.get("personal_info", {}).get("name")),
            bool(resume.get("personal_info", {}).get("contact", {}).get("phone") or
                 resume.get("personal_info", {}).get("contact", {}).get("email")),
            len(resume.get("education", [])) >= 1,
            self._count_all_skills(resume.get("skills", {})) >= 5,
            len(resume.get("work_experience", [])) >= 1 or len(resume.get("project_experience", [])) >= 1,
        ]
        scores["completeness"] = sum(completeness_checks) / len(completeness_checks)

        # Consistency (0.3)
        consistency_issues = len([e for e in errors]) + len([w for w in warnings if "CFR-" in w])
        scores["consistency"] = max(0.0, 1.0 - consistency_issues * 0.15)

        # Specificity (0.2)
        specificity_checks = []
        for work in resume.get("work_experience", []):
            for a in work.get("achievements", []):
                specificity_checks.append(bool(self.QUANTIFIER_PATTERN.search(a)))
        for proj in resume.get("project_experience", []):
            results = proj.get("results", "")
            if results:
                specificity_checks.append(bool(self.QUANTIFIER_PATTERN.search(results)))
        scores["specificity"] = (sum(specificity_checks) / max(1, len(specificity_checks)))

        # Chronology (0.2)
        chrono_ok = len([e for e in errors if "duration" in e.lower()]) == 0
        scores["chronology"] = 1.0 if chrono_ok else 0.5

        # 加权总分
        total = (
            scores["completeness"] * 0.3
            + scores["consistency"] * 0.3
            + scores["specificity"] * 0.2
            + scores["chronology"] * 0.2
        )

        return round(total, 3)

    @staticmethod
    def _count_all_skills(skills: dict) -> int:
        count = 0
        for category in ["programming_languages", "frameworks", "databases", "tools", "ai_ml", "soft_skills"]:
            items = skills.get(category, [])
            if isinstance(items, list):
                count += len(items)
        return count


# ---------------------------------------------------------------------------
#  TeacherModelClient — 教师模型 API 客户端
# ---------------------------------------------------------------------------

class TeacherModelClient:
    """
    统一的教师模型 API 客户端
    支持 OpenAI-compatible (DeepSeek/OpenAI/SiliconFlow) + Anthropic + Gemini
    """

    ENDPOINTS = {
        "openai": "https://api.openai.com/v1/chat/completions",
        "deepseek": "https://api.deepseek.com/v1/chat/completions",
        "siliconflow": "https://api.siliconflow.cn/v1/chat/completions",
    }

    DEFAULT_MODELS = {
        "openai": "gpt-4-turbo",
        "deepseek": "deepseek-chat",
        "siliconflow": "deepseek-ai/DeepSeek-V2.5",
    }

    def __init__(
        self,
        api_key: str,
        provider: str = "deepseek",
        model: Optional[str] = None,
        timeout: int = 120,
    ):
        self.api_key = api_key
        self.provider = provider
        self.model = model or self.DEFAULT_MODELS.get(provider, "deepseek-chat")
        self.timeout = timeout
        self.endpoint = self.ENDPOINTS.get(provider, self.ENDPOINTS["deepseek"])

        self._total_cost = 0.0
        self._total_calls = 0

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> Optional[str]:
        """调用教师模型 API"""
        import requests

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
                    timeout=self.timeout,
                )
                response.raise_for_status()
                data = response.json()

                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                total_tokens = usage.get("total_tokens", 0)

                # 估算成本
                cost_map = {"deepseek": 0.001, "openai": 0.01, "siliconflow": 0.001}
                cost = (total_tokens / 1000) * cost_map.get(self.provider, 0.001)
                self._total_cost += cost
                self._total_calls += 1

                return content

            except Exception as e:
                wait_time = (2 ** attempt) * random.uniform(1, 3)
                logger.warning("API 调用失败 (尝试 %d/3): %s, %.1f秒后重试",
                               attempt + 1, e, wait_time)
                if attempt < 2:
                    time.sleep(wait_time)

        return None

    def parse_json_response(self, response: str) -> Optional[Dict[str, Any]]:
        """解析 API 返回的 JSON"""
        if not response:
            return None

        # 去除 markdown code block
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # 跳过第一行 ```json 和最后一行 ```
            start = 1
            end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
            text = "\n".join(lines[start:end])

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试找到第一个 { 和最后一个 }
            first_brace = text.find("{")
            last_brace = text.rfind("}")
            if first_brace >= 0 and last_brace > first_brace:
                try:
                    return json.loads(text[first_brace:last_brace + 1])
                except json.JSONDecodeError:
                    pass

        logger.warning("JSON 解析失败: %.100s...", text[:100])
        return None

    def get_stats(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "total_calls": self._total_calls,
            "total_cost": round(self._total_cost, 4),
        }


# ---------------------------------------------------------------------------
#  TemplateRenderer — 教师模型模板渲染器
# ---------------------------------------------------------------------------

class TemplateRenderer:
    """加载并渲染 teacher_model_templates.json 中的模板"""

    def __init__(self, templates_path: Optional[Path] = None):
        if templates_path is None:
            templates_path = Path(__file__).parent / "teacher_model_templates.json"

        with open(templates_path, "r", encoding="utf-8") as f:
            self._templates = json.load(f)

    def get_template(self, template_id: str) -> Dict[str, Any]:
        """获取指定模板"""
        templates = self._templates.get("templates", {})
        if template_id not in templates:
            raise KeyError(f"Template '{template_id}' not found. Available: {list(templates.keys())}")
        return templates[template_id]

    def render(self, template_id: str, **kwargs) -> Tuple[str, str, Dict[str, Any]]:
        """
        渲染模板

        Returns:
            (system_prompt, user_prompt, generation_config)
        """
        template = self.get_template(template_id)
        system_prompt = template["system_prompt"]
        user_prompt_template = template["user_prompt_template"]

        # 渲染 user_prompt
        user_prompt = user_prompt_template
        for key, value in kwargs.items():
            placeholder = "{" + key + "}"
            if placeholder in user_prompt:
                user_prompt = user_prompt.replace(placeholder, str(value))

        generation_config = template.get("generation_config", {})
        return system_prompt, user_prompt, generation_config


# ---------------------------------------------------------------------------
#  AutonomousLearningLoop — 3-Phase 闭环控制器
# ---------------------------------------------------------------------------

class AutonomousLearningLoop:
    """
    自主学习闭环控制器

    用户只需调用 ingest_resume(text) → 系统自动完成：
    Phase 1  → 教师提取 + 规则提取 → 共识质检 → Gold/Human Queue
    Phase 1.5 → 模式学习 + 合成生成 → Silver
    Phase 2  → 经验回放 + 蒸馏 + SFT
    Phase 3  → 学生推理 + OOD 检测 → 输出/回流
    """

    def __init__(
        self,
        api_keys: Optional[Dict[str, str]] = None,
        config: Optional[LoopConfig] = None,
        base_dir: Optional[Path] = None,
    ):
        self.config = config or LoopConfig()
        self.base_dir = base_dir or Path(__file__).parent.parent

        # 初始化目录
        self._gold_dir = Path(self.config.gold_dir)
        self._silver_dir = Path(self.config.silver_dir)
        self._human_queue_dir = Path(self.config.human_queue_dir)
        for d in [self._gold_dir, self._silver_dir, self._human_queue_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # 教师模型客户端
        self._teacher: Optional[TeacherModelClient] = None
        if api_keys:
            provider = list(api_keys.keys())[0]
            self._teacher = TeacherModelClient(
                api_key=api_keys[provider],
                provider=provider,
                timeout=self.config.request_timeout,
            )

        # 模板渲染器
        self._renderer = TemplateRenderer()

        # 字段约束校验器
        self._validator = FieldConstraintValidator()

        # 经验回放池
        self._replay_buffer = ReplayBuffer(capacity=self.config.replay_buffer_capacity)

        # OOD 检测器
        self._ood_detector = OODDetector(
            method=self.config.ood_method,
            threshold=self.config.ood_confidence_threshold,
        )

        # 状态计数
        self._gold_count = self._count_jsonl(self._gold_dir / "gold_dataset.jsonl")
        self._silver_count = self._count_jsonl(self._silver_dir / "silver_dataset.jsonl")
        self._human_queue_count = self._count_jsonl(self._human_queue_dir / "human_queue.jsonl")
        self._last_synthesis_gold_count = 0
        self._total_ingested = 0
        self._training_rounds = 0

        logger.info("AutonomousLearningLoop initialized: gold=%d, silver=%d, human_queue=%d",
                     self._gold_count, self._silver_count, self._human_queue_count)

    # ------------------------------------------------------------------
    #  Phase 1: 教师数据工厂
    # ------------------------------------------------------------------

    def ingest_resume(self, resume_text: str, resume_id: Optional[str] = None) -> Dict[str, Any]:
        """
        用户入口：输入一份简历，自动触发整个学习循环

        Args:
            resume_text: 原始简历文本
            resume_id: 简历 ID（可选，自动生成）

        Returns:
            {
                "resume_id": str,
                "phase": "1",
                "quality_tier": "gold|silver|human_queue",
                "quality_score": float,
                "entities_count": int,
                "triggered_synthesis": bool,
                "triggered_training": bool,
            }
        """
        if not resume_id:
            resume_id = f"resume_{hashlib.md5(resume_text.encode()).hexdigest()[:8]}"

        self._total_ingested += 1
        logger.info("[Phase 1] 开始处理简历: %s", resume_id)

        # Step 1: 教师模型提取
        teacher_result = self._teacher_extract(resume_text)

        # Step 2: NS-LE 规则引擎预提取
        rule_result = self._rule_extract(resume_text)

        # Step 3: 交叉共识质检
        quality_tier, consensus_score, merged_result = self._consensus_check(
            teacher_result, rule_result, resume_text
        )

        # Step 4: 字段约束校验
        if merged_result:
            is_valid, quality_score, messages = self._validator.validate(merged_result)
        else:
            is_valid, quality_score, messages = False, 0.0, ["merged_result is None"]

        # Step 5: 分流
        record = {
            "resume_id": resume_id,
            "raw_text": resume_text[:2000],  # 截断保存
            "structured_data": merged_result,
            "quality_tier": quality_tier,
            "consensus_score": consensus_score,
            "quality_score": quality_score,
            "validation_messages": messages,
            "timestamp": datetime.now().isoformat(),
        }

        triggered_synthesis = False
        triggered_training = False

        if quality_tier == "gold" and is_valid:
            self._save_to_pool(record, "gold")
            self._gold_count += 1
            logger.info("[Phase 1] → Gold (score=%.3f, consensus=%.2f)", quality_score, consensus_score)

            # 检查是否触发 Phase 1.5
            if self._should_trigger_synthesis():
                logger.info("[Phase 1.5] 触发合成数据生成...")
                self._run_synthesis()
                triggered_synthesis = True

        elif quality_tier == "silver":
            self._save_to_pool(record, "silver")
            self._silver_count += 1
            logger.info("[Phase 1] → Silver (score=%.3f)", quality_score)

        else:
            self._add_to_human_queue(record)
            self._human_queue_count += 1
            logger.info("[Phase 1] → Human Queue (score=%.3f, issues=%d)", quality_score, len(messages))

        # 检查是否触发 Phase 2
        if self._should_trigger_training():
            logger.info("[Phase 2] 触发蒸馏训练...")
            self._run_distillation()
            triggered_training = True

        entities_count = 0
        if merged_result:
            for section in ["work_experience", "project_experience", "education"]:
                entities_count += len(merged_result.get(section, []))
            entities_count += self._validator._count_all_skills(merged_result.get("skills", {}))

        return {
            "resume_id": resume_id,
            "phase": "1",
            "quality_tier": quality_tier,
            "quality_score": quality_score,
            "consensus_score": consensus_score,
            "entities_count": entities_count,
            "validation_messages": messages[:5],  # 只返回前5条
            "triggered_synthesis": triggered_synthesis,
            "triggered_training": triggered_training,
        }

    def _teacher_extract(self, text: str) -> Optional[Dict[str, Any]]:
        """Phase 1: 教师模型提取"""
        if not self._teacher:
            logger.warning("教师模型未配置，跳过 API 提取")
            return None

        try:
            system_prompt, user_prompt, gen_config = self._renderer.render(
                "extract", resume_text=text
            )

            response = self._teacher.call(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=gen_config.get("temperature", 0.1),
                max_tokens=gen_config.get("max_tokens", 8192),
            )

            return self._teacher.parse_json_response(response)

        except Exception as e:
            logger.error("教师模型提取失败: %s", e)
            return None

    def _rule_extract(self, text: str) -> Optional[Dict[str, Any]]:
        """Phase 1: NS-LE 规则引擎预提取（轻量版）"""
        entities = []

        # 技能提取
        known_skills = [
            "Python", "Java", "JavaScript", "TypeScript", "Go", "Rust", "C++", "C#",
            "Spring", "Django", "Flask", "FastAPI", "React", "Vue", "Angular", "Node.js",
            "TensorFlow", "PyTorch", "LangChain", "LangGraph", "RAG",
            "MySQL", "PostgreSQL", "MongoDB", "Redis", "Neo4j", "Milvus", "Elasticsearch",
            "Docker", "Kubernetes", "Git", "Jenkins", "AWS", "GCP", "Azure", "Linux",
            "Kafka", "RabbitMQ", "Nginx",
        ]
        text_lower = text.lower()
        for skill in known_skills:
            if skill.lower() in text_lower:
                entities.append({"type": "SKILL", "span": skill, "source": "rule"})

        # 日期提取
        date_pattern = re.compile(r"(\d{4}[-/]\d{1,2})")
        for match in date_pattern.finditer(text):
            entities.append({"type": "DATE_RANGE", "span": match.group(), "source": "rule"})

        # 学校提取
        for suffix in self._validator.SCHOOL_SUFFIXES:
            pattern = re.compile(rf"([\u4e00-\u9fa5]{{2,15}}{re.escape(suffix)})")
            for match in pattern.finditer(text):
                entities.append({"type": "SCHOOL", "span": match.group(), "source": "rule"})

        # 公司提取
        company_suffixes = ["有限公司", "股份有限公司", "集团", "科技", "网络"]
        for suffix in company_suffixes:
            pattern = re.compile(rf"([\u4e00-\u9fa5]{{2,20}}{re.escape(suffix)})")
            for match in pattern.finditer(text):
                entities.append({"type": "COMPANY", "span": match.group(), "source": "rule"})

        return {"entities": entities, "source": "rule_engine"}

    def _consensus_check(
        self,
        teacher_result: Optional[Dict[str, Any]],
        rule_result: Optional[Dict[str, Any]],
        raw_text: str,
    ) -> Tuple[str, float, Optional[Dict[str, Any]]]:
        """
        交叉共识质检

        Returns:
            (quality_tier, consensus_score, merged_result)
        """
        # 如果教师结果完整且规则结果也有实体 → 计算共识
        if teacher_result and rule_result:
            # 从规则结果提取 entity spans
            rule_spans = set()
            for e in rule_result.get("entities", []):
                span = e.get("span", "").lower().strip()
                if span:
                    rule_spans.add(span)

            # 从教师结果提取技能（嵌套在 skills 字段中）
            teacher_spans = set()
            skills = teacher_result.get("skills", {})
            for category in ["programming_languages", "frameworks", "databases", "tools", "ai_ml"]:
                for s in skills.get(category, []):
                    teacher_spans.add(s.lower().strip())

            # 也从 work_experience/project_experience 提取技术
            for section in ["work_experience", "project_experience"]:
                for entry in teacher_result.get(section, []):
                    for t in entry.get("technologies_used", entry.get("technologies", [])):
                        teacher_spans.add(t.lower().strip())

            # 计算一致性
            if teacher_spans or rule_spans:
                intersection = teacher_spans & rule_spans
                union = teacher_spans | rule_spans
                consensus_score = len(intersection) / max(1, len(union))
            else:
                consensus_score = 0.0

            # 判定质量等级
            if consensus_score >= self.config.consensus_threshold:
                return "gold", consensus_score, teacher_result
            elif consensus_score >= 0.4:
                return "silver", consensus_score, teacher_result
            else:
                return "human_queue", consensus_score, teacher_result

        elif teacher_result:
            # 只有教师结果，降为 silver
            return "silver", 0.5, teacher_result

        elif rule_result:
            # 只有规则结果，降为 human_queue
            return "human_queue", 0.0, None

        else:
            return "human_queue", 0.0, None

    def _add_to_human_queue(self, record: Dict[str, Any]):
        """将冲突/低质量样本加入人工修正队列"""
        queue_file = self._human_queue_dir / "human_queue.jsonl"
        with open(queue_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _save_to_pool(self, record: Dict[str, Any], pool: str):
        """保存到 Gold/Silver 数据池"""
        if pool == "gold":
            target = self._gold_dir / "gold_dataset.jsonl"
        else:
            target = self._silver_dir / "silver_dataset.jsonl"

        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------
    #  Phase 1.5: 合成数据生成
    # ------------------------------------------------------------------

    def _should_trigger_synthesis(self) -> bool:
        """判断是否触发合成数据生成"""
        new_gold = self._gold_count - self._last_synthesis_gold_count
        return new_gold >= self.config.synthesis_trigger_count

    def _run_synthesis(self):
        """Phase 1.5: 从 Gold 学习模式 → 生成合成数据"""
        if not self._teacher:
            logger.warning("教师模型未配置，跳过合成")
            return

        # Step 1: 加载 Gold 数据
        gold_data = self._load_pool("gold")
        if not gold_data:
            logger.warning("Gold 数据池为空，跳过合成")
            return

        # Step 2: 教师学习模式 (learn_patterns)
        gold_json_str = json.dumps(gold_data[:20], ensure_ascii=False, indent=2)[:8000]
        try:
            system_prompt, user_prompt, gen_config = self._renderer.render(
                "learn_patterns",
                sample_count=len(gold_data),
                gold_data_json=gold_json_str,
            )

            patterns_response = self._teacher.call(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=gen_config.get("temperature", 0.3),
                max_tokens=gen_config.get("max_tokens", 4096),
            )

            learned_patterns = self._teacher.parse_json_response(patterns_response)
        except Exception as e:
            logger.error("模式学习失败: %s", e)
            learned_patterns = {"error": str(e)}

        # Step 3: 生成合成数据 (generate_synthetic)
        count = self.config.synthetic_per_trigger
        try:
            patterns_str = json.dumps(learned_patterns or {}, ensure_ascii=False)[:4000]

            system_prompt, user_prompt, gen_config = self._renderer.render(
                "generate_synthetic",
                count=count,
                learned_patterns=patterns_str,
                diversity_requirements="balanced",
            )

            synthetic_response = self._teacher.call(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=gen_config.get("temperature", 0.85),
                max_tokens=gen_config.get("max_tokens", 8192),
            )

            if synthetic_response:
                synthetic_data = self._parse_jsonl_response(synthetic_response)
                validated_count = 0

                for item in synthetic_data:
                    is_valid, score, messages = self._validator.validate(item)
                    if score >= 0.6:  # Silver 阈值
                        record = {
                            "resume_id": f"synthetic_{hashlib.md5(json.dumps(item).encode()).hexdigest()[:8]}",
                            "structured_data": item,
                            "quality_tier": "silver",
                            "quality_score": score,
                            "source": "synthetic",
                            "timestamp": datetime.now().isoformat(),
                        }
                        self._save_to_pool(record, "silver")
                        self._silver_count += 1
                        validated_count += 1

                logger.info("[Phase 1.5] 合成完成: 生成=%d, 通过校验=%d", len(synthetic_data), validated_count)

        except Exception as e:
            logger.error("合成数据生成失败: %s", e)

        self._last_synthesis_gold_count = self._gold_count

    def _parse_jsonl_response(self, response: str) -> List[Dict[str, Any]]:
        """解析 JSONL 格式的响应"""
        results = []
        # 去除 markdown 包裹
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        for line in text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                results.append(obj)
            except json.JSONDecodeError:
                continue

        return results

    # ------------------------------------------------------------------
    #  Phase 2: 渐进式蒸馏
    # ------------------------------------------------------------------

    def _should_trigger_training(self) -> bool:
        """判断是否触发 SFT 训练"""
        total = self._gold_count + self._silver_count
        return total >= self.config.training_trigger_count

    def _run_distillation(self):
        """
        Phase 2: 经验回放 + 数据蒸馏 + SFT 微调

        注意：实际的 SFT 训练需要 GPU 环境，此处准备数据并调用现有 pipeline
        """
        logger.info("[Phase 2] 开始渐进式蒸馏...")

        # Step 1: 加载 Gold + Silver 数据
        gold_data = self._load_pool("gold")
        silver_data = self._load_pool("silver")

        # Step 2: 构建训练样本
        training_samples = []

        for record in gold_data:
            structured = record.get("structured_data")
            raw_text = record.get("raw_text", "")
            if structured and raw_text:
                sample = {
                    "input": f"从以下简历文本中提取结构化信息：\n{raw_text}",
                    "target": json.dumps(structured, ensure_ascii=False),
                    "source": "gold",
                    "difficulty": "medium",
                    "entities": [],
                }
                training_samples.append(sample)

        for record in silver_data:
            structured = record.get("structured_data")
            raw_text = record.get("raw_text", "")
            if structured and raw_text:
                sample = {
                    "input": f"从以下简历文本中提取结构化信息：\n{raw_text}",
                    "target": json.dumps(structured, ensure_ascii=False),
                    "source": "silver",
                    "difficulty": "easy" if record.get("source") == "synthetic" else "medium",
                    "entities": [],
                }
                training_samples.append(sample)

        if not training_samples:
            logger.warning("[Phase 2] 无训练样本，跳过")
            return

        # Step 3: 添加到 ReplayBuffer
        self._replay_buffer.add(training_samples)

        # Step 4: 从 ReplayBuffer 采样训练 batch
        train_batch = self._replay_buffer.sample(
            batch_size=min(len(training_samples), 200),
            strategy="stratified"
        )

        # Step 5: 保存蒸馏数据（供 SFTTrainer 使用）
        distillation_file = self._gold_dir.parent / "distillation_ready.jsonl"
        with open(distillation_file, "w", encoding="utf-8") as f:
            for sample in train_batch:
                f.write(json.dumps(sample, ensure_ascii=False) + "\n")

        logger.info("[Phase 2] 蒸馏数据已准备: %d 条 (Gold=%d, Silver=%d, Buffer=%d)",
                     len(train_batch), len(gold_data), len(silver_data), len(self._replay_buffer))

        # Step 6: 尝试调用现有 SFTTrainer（如果 GPU 可用）
        try:
            from .sft_trainer import TrainingConfig, SFTTrainer

            config = TrainingConfig(
                model_name="Qwen/Qwen2.5-0.5B-Instruct",
                output_dir=str(self._gold_dir.parent / "sft_output"),
                num_train_epochs=3,
                use_lora=True,
            )
            trainer = SFTTrainer(config=config)

            if trainer.setup_model():
                sft_data = [
                    {"input": s["input"], "target": s["target"]}
                    for s in train_batch
                ]
                trainer.prepare_dataset(sft_data)
                logger.info("[Phase 2] SFT 训练器已准备就绪，可调用 trainer.train()")
                # trainer.train()  # 实际训练需要 GPU 和时间
            else:
                logger.info("[Phase 2] GPU 不可用，蒸馏数据已保存到 %s", distillation_file)

        except ImportError:
            logger.info("[Phase 2] SFTTrainer 不可用，蒸馏数据已保存到 %s", distillation_file)
        except Exception as e:
            logger.warning("[Phase 2] SFT 初始化失败: %s", e)

        self._training_rounds += 1

    # ------------------------------------------------------------------
    #  Phase 3: 边缘自主推理
    # ------------------------------------------------------------------

    def infer(self, text: str) -> Dict[str, Any]:
        """
        Phase 3: 使用学生模型推理

        Args:
            text: 输入简历文本

        Returns:
            {
                "result": 提取结果,
                "confidence": OOD 置信度,
                "action": "output" | "fallback_to_phase1",
                "source": "student_model" | "teacher_fallback"
            }
        """
        # 尝试用学生模型推理
        student_result, confidence, embedding = self._student_infer(text)

        if student_result and confidence >= self.config.ood_confidence_threshold:
            # 高置信度：直接输出
            logger.info("[Phase 3] 学生模型输出 (conf=%.3f)", confidence)
            return {
                "result": student_result,
                "confidence": confidence,
                "action": "output",
                "source": "student_model",
            }
        else:
            # 低置信度 / OOD：回流 Phase 1
            logger.info("[Phase 3] 低置信度 (conf=%.3f)，回流 Phase 1", confidence)

            # 触发 Phase 1 教师标注
            phase1_result = self.ingest_resume(text)

            return {
                "result": phase1_result,
                "confidence": confidence,
                "action": "fallback_to_phase1",
                "source": "teacher_fallback",
            }

    def _student_infer(self, text: str) -> Tuple[Optional[Dict], float, Optional[List[float]]]:
        """
        学生模型推理（占位实现）

        实际实现需要：
        1. 加载微调后的 Qwen3-0.6B
        2. tokenize + forward
        3. 从 logits 计算 entropy/confidence
        4. 提取 last_hidden_state 作为 embedding

        Returns:
            (result, confidence, embedding)
        """
        # 检查是否有微调后的模型
        sft_output = self._gold_dir.parent / "sft_output"
        if not sft_output.exists():
            return None, 0.0, None

        try:
            import torch
            from transformers import AutoTokenizer, AutoModelForCausalLM

            model_path = str(sft_output)
            tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
            model = AutoModelForCausalLM.from_pretrained(model_path, trust_remote_code=True)
            model.eval()

            prompt = f"从以下简历文本中提取结构化信息：\n{text[:1500]}"
            inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=2048)

            with torch.no_grad():
                outputs = model.generate(
                    **inputs,
                    max_new_tokens=2048,
                    temperature=0.1,
                    do_sample=False,
                    output_hidden_states=True,
                    return_dict_in_generate=True,
                )

            # 解析输出
            generated_ids = outputs.sequences[0][inputs["input_ids"].shape[1]:]
            generated_text = tokenizer.decode(generated_ids, skip_special_tokens=True)

            result = self._teacher.parse_json_response(generated_text) if self._teacher else None

            # 计算置信度（基于生成 token 的 entropy）
            if hasattr(outputs, "scores") and outputs.scores:
                entropies = []
                for logits in outputs.scores:
                    probs = torch.softmax(logits[0], dim=-1)
                    entropy = -torch.sum(probs * torch.log(probs + 1e-10)).item()
                    entropies.append(entropy)
                avg_entropy = sum(entropies) / len(entropies) if entropies else 10.0
                # 将 entropy 映射到置信度（entropy 越低越确定）
                confidence = max(0.0, min(1.0, 1.0 - avg_entropy / 10.0))
            else:
                confidence = 0.5

            # 提取 embedding（用于 OOD 检测）
            embedding = None
            if hasattr(outputs, "hidden_states") and outputs.hidden_states:
                last_hidden = outputs.hidden_states[-1]
                if isinstance(last_hidden, tuple):
                    last_hidden = last_hidden[-1]
                embedding = last_hidden[0, -1, :].cpu().numpy().tolist()

                # OOD 检测
                if embedding and self._ood_detector._is_fitted:
                    ood_score = self._ood_detector.score(embedding)
                    confidence = min(confidence, ood_score)

            return result, confidence, embedding

        except Exception as e:
            logger.debug("[Phase 3] 学生模型推理失败: %s", e)
            return None, 0.0, None

    # ------------------------------------------------------------------
    #  工具方法
    # ------------------------------------------------------------------

    def _load_pool(self, pool: str) -> List[Dict[str, Any]]:
        """加载 Gold/Silver 数据池"""
        if pool == "gold":
            path = self._gold_dir / "gold_dataset.jsonl"
        else:
            path = self._silver_dir / "silver_dataset.jsonl"

        data = []
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            data.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        return data

    @staticmethod
    def _count_jsonl(path: Path) -> int:
        """计算 JSONL 文件行数"""
        if not path.exists():
            return 0
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def approve_human_queue(self, resume_id: str, corrected_data: Dict[str, Any]) -> bool:
        """
        Human-in-the-loop: 人工审核修正后的数据，提升为 Gold

        Args:
            resume_id: 简历 ID
            corrected_data: 人工修正后的结构化数据

        Returns:
            是否成功
        """
        is_valid, score, messages = self._validator.validate(corrected_data)

        record = {
            "resume_id": resume_id,
            "structured_data": corrected_data,
            "quality_tier": "gold",
            "quality_score": score,
            "source": "human_corrected",
            "timestamp": datetime.now().isoformat(),
        }

        self._save_to_pool(record, "gold")
        self._gold_count += 1

        logger.info("Human-in-the-loop: %s → Gold (score=%.3f)", resume_id, score)
        return True

    def get_loop_status(self) -> Dict[str, Any]:
        """获取闭环状态报告"""
        return {
            "phase_status": {
                "phase_1": {
                    "total_ingested": self._total_ingested,
                    "gold_count": self._gold_count,
                    "silver_count": self._silver_count,
                    "human_queue_count": self._human_queue_count,
                },
                "phase_1_5": {
                    "last_synthesis_at_gold": self._last_synthesis_gold_count,
                    "next_synthesis_at_gold": self._last_synthesis_gold_count + self.config.synthesis_trigger_count,
                },
                "phase_2": {
                    "training_rounds": self._training_rounds,
                    "replay_buffer": self._replay_buffer.get_stats(),
                    "training_trigger_at": self.config.training_trigger_count,
                    "current_total": self._gold_count + self._silver_count,
                },
                "phase_3": {
                    "ood_detector": self._ood_detector.get_stats(),
                    "confidence_threshold": self.config.ood_confidence_threshold,
                },
            },
            "teacher_stats": self._teacher.get_stats() if self._teacher else None,
            "config": {
                "consensus_threshold": self.config.consensus_threshold,
                "synthesis_trigger": self.config.synthesis_trigger_count,
                "training_trigger": self.config.training_trigger_count,
            },
        }

    def get_human_queue(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取待人工审核的样本"""
        queue_file = self._human_queue_dir / "human_queue.jsonl"
        items = []
        if queue_file.exists():
            with open(queue_file, "r", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i >= limit:
                        break
                    line = line.strip()
                    if line:
                        try:
                            items.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        return items
