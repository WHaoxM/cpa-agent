"""
L4 报告生成模块 — v2.0 冻结 Schema 对齐
6章: ch1匹配/ch2路径/ch3行动/ch4差距/ch5评估/ch6建议
"""

import uuid
from datetime import datetime
from typing import Dict, List, Optional
from .portrait_generator import PortraitGenerator
from .matcher import calculate_match_score


class ReportGenerator:
    """报告生成器 — v2.0"""

    def __init__(self):
        self.portrait_gen = PortraitGenerator()

    def generate_match_report(self, student_id: str, student_skills: List[Dict],
                              job_id: str, job_name: str, job_skills: List[Dict],
                              student_profile: Optional[Dict] = None) -> Dict:
        sp = self.portrait_gen.generate_student_portrait(student_id, student_skills, match_job_id=job_id, student_profile=student_profile, target_job_skills=job_skills)
        jp = self.portrait_gen.generate_job_portrait(job_id, job_name, job_skills)
        mr = calculate_match_score(student_skills, job_skills, student_profile)
        gp = mr.get("gap_priorities", [])
        ms = mr.get("missing_skills", [])
        return {
            "report_id": f"rpt_{uuid.uuid4().hex[:12]}", "student_id": student_id, "job_id": job_id, "job_name": job_name,
            "summary": {"total_score": mr.get("total_score", 0), "match_level": mr.get("match_level", ""),
                        "skill_coverage": mr.get("skill_coverage", 0), "is_matched": mr.get("is_matched", False),
                        "matched_count": len(mr.get("matched_skills", [])), "missing_count": len(ms),
                        "conclusion": self._conclusion(mr)},
            "student_portrait": sp, "job_portrait": jp,
            "match_details": {
                "D1": {"name": "基础要求", "student_score": mr.get("d1_score", 0), "passed": mr.get("d1_score", 0) >= 60},
                "D2": {"name": "职业技能", "student_score": mr.get("d2_score", 0), "skill_coverage": mr.get("skill_coverage", 0),
                       "matched_skills": mr.get("matched_skills", []), "passed": mr.get("skill_coverage", 0) >= 0.8},
                "D3": {"name": "职业素养", "student_score": mr.get("d3_score", 0)},
                "D4": {"name": "发展潜力", "student_score": mr.get("d4_score", 0)},
            },
            "gap_analysis": {"total_gaps": len(ms), "critical_gaps": [g for g in gp if g.get("priority") == "critical"],
                             "quick_learn_gaps": [g for g in gp if g.get("priority") == "quick_learn"], "details": gp},
            "recommendations": self._recommendations(student_skills, ms, gp),
            "generated_at": datetime.now().isoformat(),
        }

    def generate_career_report(self, student_id: str, student_skills: List[Dict],
                               student_profile: Optional[Dict] = None,
                               target_job_id: Optional[str] = None,
                               target_job_name: Optional[str] = None,
                               target_job_skills: Optional[List[Dict]] = None) -> Dict:
        portrait = self.portrait_gen.generate_student_portrait(student_id, student_skills, student_profile=student_profile, target_job_skills=target_job_skills)
        mr = calculate_match_score(student_skills, target_job_skills, student_profile) if target_job_skills else None
        paths = self._fetch_career_paths(target_job_name or "")
        return {
            "report_id": f"career_{uuid.uuid4().hex[:12]}", "student_id": student_id,
            "student_name": portrait.get("name", ""), "target_job_id": target_job_id,
            "target_job_name": target_job_name,
            "ch1_match_analysis": self._ch1(portrait, mr, target_job_name),
            "ch2_career_target": self._ch2(portrait, paths, target_job_name),
            "ch3_action_plan": self._ch3(portrait, mr),
            "ch4_gap_analysis": self._ch4(portrait, mr),
            "ch5_evaluation": self._ch5(portrait),
            "ch6_recommendations": self._ch6(portrait, mr),
            "portrait_summary": portrait.get("summary", {}),
            "generated_at": datetime.now().isoformat(),
        }

    def _ch1(self, p, mr, tjn):
        d1,d2,d3,d4 = p.get("D1_basic",{}), p.get("D2_skill",{}), p.get("D3_quality",{}), p.get("D4_potential",{})
        mr_data = {"total_score":(mr or{}).get("total_score",0),"is_matched":(mr or{}).get("is_matched",False),
                   "skill_coverage":(mr or{}).get("skill_coverage",0),"missing_skills":(mr or{}).get("missing_skills",[])} if mr else {"total_score":0,"is_matched":False,"skill_coverage":0,"missing_skills":[]}
        return {"title":"职业探索与岗位匹配","target_position":tjn or "未指定",
                "four_dimensions":{"D1_basic":{"score":d1.get("dimension_score",0),"evidence":d1.get("evidence",[])},
                                  "D2_skill":{"score":d2.get("dimension_score",0),"skill_coverage":d2.get("skill_coverage",0),"evidence":d2.get("evidence",[])},
                                  "D3_quality":{"score":d3.get("dimension_score",0),"confidence":d3.get("confidence",0)},
                                  "D4_potential":{"score":d4.get("dimension_score",0),"evidence":d4.get("evidence",[])}},
                "match_result":mr_data}

    def _ch2(self, p, paths, tjn):
        cats = set(s.get("category","") for s in p.get("D2_skill",{}).get("skill_list",[]))
        trends = []
        if "AI/ML" in cats: trends.append({"field":"人工智能","trend":"上升","demand":"高"})
        if "大数据" in cats: trends.append({"field":"大数据","trend":"上升","demand":"高"})
        if "后端框架" in cats or "编程语言" in cats: trends.append({"field":"后端开发","trend":"稳定","demand":"中"})
        if not trends: trends.append({"field":"计算机/IT","trend":"稳定","demand":"中"})
        return {"title":"职业目标与路径规划","current_level":p.get("summary",{}).get("competitiveness_level","中等"),
                "target_position":tjn or "待确定","industry_trends":trends,"career_paths":paths,
                "recommended_direction":paths[0] if paths else {"direction":"待确定","path":[]}}

    def _ch3(self, p, mr):
        gp = (mr or {}).get("gap_priorities", [])
        crit = [g["skill"] for g in gp if g.get("priority") == "critical"]
        quick = [g["skill"] for g in gp if g.get("priority") == "quick_learn"]
        short = {"period": "1-3个月", "goals": [], "actions": []}
        if quick:
            short["goals"].append(f"快速掌握加分技能: {', '.join(quick[:3])}")
            short["actions"].extend([{"skill": s, "method": "在线课程+实战", "duration": "2-4周"} for s in quick[:3]])
        if crit:
            short["goals"].append(f"补齐关键缺失技能: {', '.join(crit[:2])}")
            short["actions"].extend([{"skill": s, "method": "系统学习+项目实践", "duration": "4-8周"} for s in crit[:2]])
        if not short["goals"]: short["goals"].append("巩固现有技能，拓展技术广度")
        mid = {"period": "3-6个月", "goals": [], "actions": []}
        if crit:
            mid["goals"].append(f"全面掌握核心技能: {', '.join(crit)}")
            mid["actions"].extend([{"skill": s, "method": "项目实战+开源贡献", "duration": "6-12周"} for s in crit])
        if p.get("D2_skill", {}).get("project_count", 0) < 3:
            mid["goals"].append("积累 3+ 个项目经验")
            mid["actions"].append({"skill": "项目经验", "method": "参与开源/实习项目", "duration": "持续"})
        if not mid["goals"]: mid["goals"].append("深化专业技能，准备晋升")
        return {"title": "行动计划", "short_term": short, "mid_term": mid}

    def _ch4(self, p, mr):
        if not mr: return {"title": "技能差距分析", "gaps": [], "summary": "未指定目标岗位，无法分析差距"}
        gp = mr.get("gap_priorities", [])
        ms = mr.get("missing_skills", [])
        return {"title": "技能差距分析", "total_gaps": len(ms),
                "critical": [g for g in gp if g.get("priority") == "critical"],
                "quick_learn": [g for g in gp if g.get("priority") == "quick_learn"],
                "medium": [g for g in gp if g.get("priority") == "medium"],
                "summary": f"共 {len(ms)} 项技能差距，其中 {len([g for g in gp if g.get('priority')=='critical'])} 项关键"}

    def _ch5(self, p):
        return {"title": "评估指标与周期",
                "assessment_cycles": [
                    {"period": "1个月", "metrics": ["技能学习进度", "练习项目完成度"], "method": "自评+导师反馈"},
                    {"period": "3个月", "metrics": ["技能覆盖率变化", "项目经验积累", "匹配度提升"], "method": "系统自动评估"},
                    {"period": "6个月", "metrics": ["竞争力评分变化", "岗位匹配结果", "职业路径进展"], "method": "综合评估报告"},
                ],
                "key_indicators": [
                    {"name": "技能覆盖率", "target": "≥0.8", "current": p.get("D2_skill", {}).get("skill_coverage", 0)},
                    {"name": "竞争力评分", "target": "≥70", "current": p.get("summary", {}).get("competitiveness_score", 0)},
                    {"name": "完整度评分", "target": "≥80", "current": p.get("summary", {}).get("completeness_score", 0)},
                ]}

    def _ch6(self, p, mr):
        recs = []
        comp = p.get("summary", {}).get("competitiveness_score", 0)
        level = p.get("summary", {}).get("competitiveness_level", "中等")
        completeness = p.get("summary", {}).get("completeness_score", 0)
        if comp >= 70: recs.append({"type": "strength", "content": f"竞争力{level}，建议积极投递目标岗位"})
        elif comp >= 55: recs.append({"type": "improvement", "content": f"竞争力{level}，建议补齐关键差距后投递"})
        else: recs.append({"type": "warning", "content": f"竞争力{level}，建议先系统学习再投递"})
        if completeness < 60: recs.append({"type": "completeness", "content": f"画像完整度{completeness}%，建议补充简历信息"})
        if mr and mr.get("missing_skills"):
            recs.append({"type": "skill_gap", "content": f"优先学习: {', '.join(mr['missing_skills'][:3])}"})
        return {"title": "建议与润色", "recommendations": recs,
                "completeness_check": {"score": completeness, "status": "通过" if completeness >= 60 else "需补充"}}

    def _fetch_career_paths(self, job_name: str) -> List[Dict]:
        """从 Neo4j 获取职业路径 (PROMOTION + TRANSFER)"""
        try:
            from .graph_storage import GraphStorage
            gs = GraphStorage()
            promo = gs.get_promotion_chains(job_name) if job_name else []
            transfer = gs.get_transfer_options(job_name) if job_name else []
            paths = []
            for chain in promo[:3]:
                paths.append({"direction": "晋升路径", "path": chain, "type": "promotion"})
            for opt in transfer[:5]:
                paths.append({"direction": "换岗路径", "path": opt, "type": "transfer"})
            return paths
        except Exception:
            return self._fallback_career_paths(job_name)

    def _fallback_career_paths(self, job_name: str) -> List[Dict]:
        """Neo4j 不可用时的降级路径"""
        if not job_name: return []
        if "后端" in job_name: return [
            {"direction": "晋升路径", "path": ["初级后端", "中级后端", "高级后端", "架构师"], "type": "promotion"},
            {"direction": "换岗路径", "path": ["后端→全栈", "后端→DevOps"], "type": "transfer"}]
        if "前端" in job_name: return [
            {"direction": "晋升路径", "path": ["初级前端", "中级前端", "高级前端", "前端架构师"], "type": "promotion"},
            {"direction": "换岗路径", "path": ["前端→全栈", "前端→Node.js后端"], "type": "transfer"}]
        if "AI" in job_name or "算法" in job_name: return [
            {"direction": "晋升路径", "path": ["算法工程师", "高级算法", "AI架构师"], "type": "promotion"},
            {"direction": "换岗路径", "path": ["算法→数据工程", "算法→AI产品"], "type": "transfer"}]
        return [{"direction": "晋升路径", "path": ["初级", "中级", "高级", "专家"], "type": "promotion"}]

    def _conclusion(self, mr):
        cov = mr.get("skill_coverage", 0)
        if cov >= 0.9: return "高度匹配，建议申请"
        if cov >= 0.7: return "较好匹配，可尝试申请并补充差距"
        if cov >= 0.5: return "部分匹配，需针对性学习"
        return "匹配度较低，建议先提升基础技能"

    def _recommendations(self, skills, missing, gp):
        recs = []
        if not missing: return [{"type": "positive", "title": "技能完全匹配", "content": "可尝试更高难度岗位"}]
        crit = [g for g in gp if g.get("priority") == "critical"]
        quick = [g for g in gp if g.get("priority") == "quick_learn"]
        if crit: recs.append({"type": "priority", "title": "优先弥补关键差距", "content": f"建议优先学习: {', '.join([g['skill'] for g in crit[:3]])}", "skills": [g['skill'] for g in crit[:3]]})
        if quick: recs.append({"type": "bonus", "title": "快速提升加分项", "content": f"可快速学习: {', '.join([g['skill'] for g in quick[:3]])}", "skills": [g['skill'] for g in quick[:3]]})
        recs.append({"type": "general", "title": "学习路径建议", "content": "按 基础→进阶→高级 顺序系统学习"})
        return recs


def generate_match_report(student_id, student_skills, job_id, job_name, job_skills, student_profile=None):
    return ReportGenerator().generate_match_report(student_id, student_skills, job_id, job_name, job_skills, student_profile)

def generate_career_report(student_id, student_skills, student_profile=None, target_job_id=None, target_job_name=None, target_job_skills=None):
    return ReportGenerator().generate_career_report(student_id, student_skills, student_profile, target_job_id, target_job_name, target_job_skills)


if __name__ == "__main__":
    ss = [{"name":"Python","category":"编程语言","confidence":0.85},{"name":"Django","category":"后端框架","confidence":0.75},
          {"name":"MySQL","category":"数据库","confidence":0.8},{"name":"Redis","category":"数据库","confidence":0.6},
          {"name":"Docker","category":"DevOps","confidence":0.65}]
    js = [{"name":"Python","level":"必须","weight":0.9},{"name":"Django","level":"必须","weight":0.8},
          {"name":"MySQL","level":"必须","weight":0.85},{"name":"Redis","level":"加分","weight":0.5},
          {"name":"Kubernetes","level":"加分","weight":0.4}]
    sp = {"name":"张三","degree":"本科","school":"浙江大学","major":"计算机科学与技术","gpa":3.6,
          "certificates":["CET-6"],"languages":["英语"],"projects":[{"name":"电商系统","is_team":True,"role":"负责人"}],
          "internship_months":6,"competitions":[{"level":"省级一等"}],"research":[{"level":"EI"}],
          "leadership_roles":[{"role":"项目负责人"}],"soft_skills":{"communication":"强","teamwork":"强"}}
    gen = ReportGenerator()
    print("=" * 60)
    print("L4 报告生成测试 — v2.0 6章结构")
    print("=" * 60)
    r = gen.generate_career_report("stu_001", ss, student_profile=sp, target_job_id="job_001",
                                    target_job_name="Python后端开发工程师", target_job_skills=js)
    print(f"报告ID: {r['report_id']}")
    print(f"ch1 匹配: {r['ch1_match_analysis']['match_result']}")
    print(f"ch2 路径: {len(r['ch2_career_target']['career_paths'])} 条")
    print(f"ch3 行动: 短期{len(r['ch3_action_plan']['short_term']['goals'])}项 中期{len(r['ch3_action_plan']['mid_term']['goals'])}项")
    print(f"ch4 差距: {r['ch4_gap_analysis']['summary']}")
    print(f"ch5 评估: {len(r['ch5_evaluation']['assessment_cycles'])} 个周期")
    print(f"ch6 建议: {len(r['ch6_recommendations']['recommendations'])} 条")
