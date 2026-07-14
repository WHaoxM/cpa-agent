"""
L1 JD 聚合模块 — v2.0
同岗位多条 JD 合并形成画像 (R-05: 相同岗位合并后的结果)

流程:
1. 从 job_posting 按 position_normalized 分组
2. 合并同岗位的技能要求 (TF 加权)
3. 合并学历/专业/证书要求 (取交集或并集)
4. 生成 job_portrait_4d (2B Schema)
"""

import json
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .portrait_generator import PortraitGenerator, DIMENSION_WEIGHTS


class JDAggregator:
    """JD 聚合器 — 同岗位合并形成画像"""

    def __init__(self, mysql_client=None):
        self.mysql = mysql_client
        self.portrait_gen = PortraitGenerator()

    def aggregate_all(self, min_jd_count: int = 1) -> List[Dict]:
        """聚合所有岗位，返回 job_portrait_4d 列表"""
        if not self.mysql:
            raise RuntimeError("MySQL client required for aggregation")

        # 按 position_normalized 分组获取 JD
        rows = self.mysql.fetch_all(
            """SELECT job_id, position_normalized, posting_json, is_it_position
               FROM job_posting WHERE is_it_position = 1""",
            {}
        )
        if not rows:
            return []

        # 分组
        groups = defaultdict(list)
        for row in rows:
            pn = row.get("position_normalized", row.get(1, ""))
            pj = row.get("posting_json", row.get(2, ""))
            posting = json.loads(pj) if isinstance(pj, str) else (pj or {})
            posting["job_id"] = row.get("job_id", row.get(0, ""))
            groups[pn].append(posting)

        portraits = []
        for position_name, postings in groups.items():
            if len(postings) < min_jd_count:
                continue
            portrait = self.aggregate_position(position_name, postings)
            portraits.append(portrait)

        return portraits

    def aggregate_position(self, position_name: str, postings: List[Dict]) -> Dict:
        """聚合同一岗位的多条 JD"""
        jd_count = len(postings)

        # 合并技能 (TF 加权)
        skill_tf = Counter()
        skill_levels = defaultdict(list)
        for p in postings:
            skills = p.get("skills", p.get("required_skills", []))
            for s in skills:
                if isinstance(s, dict):
                    name = s.get("name", s.get("skill", ""))
                    level = s.get("level", "必须")
                    weight = s.get("weight", s.get("tf_score", 1.0))
                else:
                    name = str(s)
                    level = "必须"
                    weight = 1.0
                if name:
                    skill_tf[name] += weight
                    skill_levels[name].append(level)

        # 按出现频率决定 level
        skills_list = []
        for name, tf in skill_tf.most_common(30):
            levels = skill_levels.get(name, ["必须"])
            level_counts = Counter(levels)
            dominant_level = level_counts.most_common(1)[0][0]
            avg_tf = round(tf / jd_count, 3)
            # 频率 >= 50% 的为必须，否则按原始
            if tf / jd_count >= 0.5:
                final_level = "必须"
            elif tf / jd_count >= 0.3:
                final_level = "偏好"
            else:
                final_level = "加分"
            skills_list.append({"name": name, "level": final_level, "weight": avg_tf})

        # 合并学历要求
        degrees = set()
        for p in postings:
            d = p.get("required_degree", p.get("degree", ""))
            if d: degrees.add(d)
        required_degree = list(degrees)[0] if len(degrees) == 1 else ""

        # 合并专业要求
        majors = set()
        for p in postings:
            ms = p.get("required_majors", p.get("majors", []))
            if isinstance(ms, list): majors.update(ms)
            elif ms: majors.add(str(ms))
        required_majors = list(majors)

        # 合并证书要求
        certs = set()
        for p in postings:
            cs = p.get("required_certs", p.get("certificates", []))
            if isinstance(cs, list): certs.update(cs)
            elif cs: certs.add(str(cs))
        required_certs = list(certs)

        # 合并语言要求
        langs = set()
        for p in postings:
            ls = p.get("required_languages", p.get("languages", []))
            if isinstance(ls, list): langs.update(ls)
            elif ls: langs.add(str(ls))
        required_languages = list(langs)

        # 薪资范围
        salary_min_list, salary_max_list = [], []
        for p in postings:
            sr = p.get("salary_range", {})
            if isinstance(sr, dict):
                if sr.get("min"): salary_min_list.append(float(sr["min"]))
                if sr.get("max"): salary_max_list.append(float(sr["max"]))
        salary_range = None
        if salary_min_list and salary_max_list:
            salary_range = {
                "min": round(sum(salary_min_list) / len(salary_min_list)),
                "max": round(sum(salary_max_list) / len(salary_max_list)),
            }

        # 构建 job_profile
        job_profile = {
            "required_degree": required_degree,
            "major_strict": len(majors) <= 2,
            "required_majors": required_majors,
            "required_certs": required_certs,
            "required_languages": required_languages,
        }

        # 生成画像
        portrait_id = f"jp_{uuid.uuid4().hex[:8]}"
        portrait = self.portrait_gen.generate_job_portrait(
            job_id=portrait_id,
            position_name=position_name,
            skills=skills_list,
            salary_range=salary_range,
            job_profile=job_profile,
            jd_count=jd_count,
        )

        return portrait

    def save_to_mysql(self, portraits: List[Dict]) -> int:
        """保存聚合画像到 MySQL job_portrait_4d"""
        if not self.mysql:
            raise RuntimeError("MySQL client required")
        saved = 0
        for p in portraits:
            try:
                pid = p.get("portrait_id", "")
                pn = p.get("position_name", "")
                jc = p.get("jd_count", 1)
                pj = json.dumps(p, ensure_ascii=False)
                self.mysql.execute(
                    """INSERT INTO job_portrait_4d
                       (portrait_id, position_name, jd_count, portrait_json, updated_at)
                       VALUES (:pid, :pn, :jc, :pj, NOW())
                       ON DUPLICATE KEY UPDATE jd_count=:jc, portrait_json=:pj, updated_at=NOW()""",
                    {"pid": pid, "pn": pn, "jc": jc, "pj": pj},
                    commit=True,
                )
                saved += 1
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"保存画像失败 {pn}: {e}")
        return saved


def aggregate_job_postings(mysql_client=None) -> List[Dict]:
    """便捷函数: 聚合所有岗位 JD"""
    agg = JDAggregator(mysql_client)
    return agg.aggregate_all()
