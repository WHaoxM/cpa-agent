"""
L4 报告生成模块 (Report Generation)
模板化报告输出 + LLM摘要生成 + PDF/HTML导出
"""

import uuid
import os
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path


REPORT_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; background: #f5f7fa; color: #333; }}
        .container {{ max-width: 900px; margin: 0 auto; padding: 40px 20px; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 40px; border-radius: 16px 16px 0 0; text-align: center; }}
        .header h1 {{ font-size: 28px; margin-bottom: 10px; }}
        .header .subtitle {{ opacity: 0.9; font-size: 14px; }}
        .card {{ background: white; border-radius: 12px; margin-bottom: 20px; padding: 24px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }}
        .section-title {{ font-size: 18px; color: #667eea; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 2px solid #f0f0f0; }}
        .score-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 20px; }}
        .score-item {{ background: #f8f9fc; padding: 16px; border-radius: 8px; text-align: center; }}
        .score-item .label {{ font-size: 12px; color: #666; margin-bottom: 4px; }}
        .score-item .value {{ font-size: 24px; font-weight: bold; color: #667eea; }}
        .score-item .sub {{ font-size: 11px; color: #999; }}
        .match-bar {{ background: #e8eaff; height: 24px; border-radius: 12px; overflow: hidden; margin: 12px 0; }}
        .match-bar .fill {{ height: 100%; background: linear-gradient(90deg, #667eea, #764ba2); border-radius: 12px; display: flex; align-items: center; justify-content: flex-end; padding-right: 8px; color: white; font-size: 11px; }}
        .skill-tag {{ display: inline-block; background: #e8eaff; color: #667eea; padding: 4px 12px; border-radius: 16px; margin: 4px; font-size: 13px; }}
        .skill-tag.matched {{ background: #d4edda; color: #155724; }}
        .skill-tag.missing {{ background: #f8d7da; color: #721c24; }}
        .gap-item {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 12px 16px; margin: 8px 0; border-radius: 0 8px 8px 0; }}
        .gap-item.critical {{ background: #f8d7da; border-left-color: #dc3545; }}
        .recommendation {{ background: #d1ecf1; border-left: 4px solid #17a2b8; padding: 12px 16px; margin: 8px 0; border-radius: 0 8px 8px 0; }}
        .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
        .footer {{ text-align: center; color: #999; font-size: 12px; padding: 20px; }}
        .summary-box {{ background: linear-gradient(135deg, #f8f9fc 0%, #e8eaff 100%); padding: 20px; border-radius: 12px; margin: 16px 0; }}
        .conclusion {{ font-size: 16px; color: #333; line-height: 1.8; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{title}</h1>
            <div class="subtitle">生成时间: {generated_at} | 报告ID: {report_id}</div>
        </div>

        <div class="card">
            <h2 class="section-title">总览</h2>
            <div class="score-grid">
                <div class="score-item">
                    <div class="label">匹配总分</div>
                    <div class="value">{total_score}</div>
                    <div class="sub">{match_level}</div>
                </div>
                <div class="score-item">
                    <div class="label">技能覆盖</div>
                    <div class="value">{skill_coverage}%</div>
                    <div class="sub">已匹配/总要求</div>
                </div>
                <div class="score-item">
                    <div class="label">已匹配</div>
                    <div class="value">{matched_count}</div>
                    <div class="sub">项技能</div>
                </div>
                <div class="score-item">
                    <div class="label">缺失</div>
                    <div class="value">{missing_count}</div>
                    <div class="sub">项技能</div>
                </div>
            </div>

            <div class="match-bar">
                <div class="fill" style="width: {skill_coverage}%">{skill_coverage}%</div>
            </div>

            <div class="summary-box">
                <div class="conclusion">{conclusion}</div>
            </div>
        </div>

        <div class="two-col">
            <div class="card">
                <h2 class="section-title">学生画像</h2>
                <p><strong>学生ID:</strong> {student_id}</p>
                <p><strong>技能数量:</strong> {student_skill_count}</p>
                <p><strong>D2技能分:</strong> {student_d2_score}</p>
                <h3 style="margin: 12px 0 8px; font-size: 14px;">已掌握技能</h3>
                <div>{student_skills_html}</div>
            </div>

            <div class="card">
                <h2 class="section-title">岗位画像</h2>
                <p><strong>岗位:</strong> {job_name}</p>
                <p><strong>技能要求:</strong> {job_skill_count}</p>
                <p><strong>难度评分:</strong> {job_difficulty}</p>
                <h3 style="margin: 12px 0 8px; font-size: 14px;">岗位要求</h3>
                <div>{job_skills_html}</div>
            </div>
        </div>

        <div class="card">
            <h2 class="section-title">四维评分详情</h2>
            <div class="score-grid">
                <div class="score-item">
                    <div class="label">D1 基础</div>
                    <div class="value">{d1_score}</div>
                    <div class="sub">{d1_pass}</div>
                </div>
                <div class="score-item">
                    <div class="label">D2 技能</div>
                    <div class="value">{d2_score}</div>
                    <div class="sub">覆盖率{d2_coverage}%</div>
                </div>
                <div class="score-item">
                    <div class="label">D3 素养</div>
                    <div class="value">{d3_score}</div>
                    <div class="sub">软技能</div>
                </div>
                <div class="score-item">
                    <div class="label">D4 潜力</div>
                    <div class="value">{d4_score}</div>
                    <div class="sub">发展空间</div>
                </div>
            </div>
        </div>

        <div class="card">
            <h2 class="section-title">技能差距分析</h2>
            {gap_analysis_html}
        </div>

        <div class="card">
            <h2 class="section-title">学习建议</h2>
            {recommendations_html}
        </div>

        <div class="card">
            <h2 class="section-title">LLM 智能摘要</h2>
            <div class="summary-box">
                <div class="conclusion">{llm_summary}</div>
            </div>
        </div>

        <div class="footer">
            <p>CPA-Agent 人岗智能匹配系统 | 报告由 AI 自动生成</p>
        </div>
    </div>
</body>
</html>
"""


class L4ReportGenerator:
    """L4 报告生成器"""

    def __init__(self):
        self.template = REPORT_TEMPLATE

    def generate_html(
        self,
        report_data: Dict,
        llm_summary: str = ""
    ) -> str:
        """
        生成 HTML 报告

        Args:
            report_data: 报告数据
            llm_summary: LLM 生成的摘要

        Returns:
            HTML 字符串
        """
        summary = report_data.get("summary", {})
        match_details = report_data.get("match_details", {})
        gap_analysis = report_data.get("gap_analysis", {})
        recommendations = report_data.get("recommendations", [])
        student_portrait = report_data.get("student_portrait", {})
        job_portrait = report_data.get("job_portrait", {})

        student_skills = summary.get("student_skills", [])
        job_skills = summary.get("job_skills", [])
        matched = set(summary.get("matched_skills", []))

        html = self.template.format(
            title=f"人岗匹配报告 - {report_data.get('job_name', '未知岗位')}",
            report_id=report_data.get("report_id", ""),
            generated_at=report_data.get("generated_at", "")[:19],

            total_score=summary.get("match_score", 0),
            match_level=summary.get("match_level", "-"),
            skill_coverage=int(summary.get("skill_coverage", 0) * 100),
            matched_count=summary.get("matched_count", 0),
            missing_count=summary.get("missing_count", 0),
            conclusion=summary.get("conclusion", ""),

            student_id=report_data.get("student_id", ""),
            student_skill_count=len(student_skills),
            student_d2_score=match_details.get("D2", {}).get("student_score", 0),
            student_skills_html=self._format_skills(student_skills, matched, True),

            job_name=report_data.get("job_name", ""),
            job_skill_count=len(job_skills),
            job_difficulty=job_portrait.get("difficulty_score", 0),
            job_skills_html=self._format_skills(job_skills, matched, False),

            d1_score=match_details.get("D1", {}).get("student_score", 0),
            d1_pass="通过" if match_details.get("D1", {}).get("passed") else "未通过",
            d2_score=match_details.get("D2", {}).get("student_score", 0),
            d2_coverage=int(summary.get("skill_coverage", 0) * 100),
            d3_score=match_details.get("D3", {}).get("student_score", 0),
            d4_score=match_details.get("D4", {}).get("student_score", 0),

            gap_analysis_html=self._format_gap_analysis(gap_analysis),
            recommendations_html=self._format_recommendations(recommendations),
            llm_summary=llm_summary or "暂无AI摘要"
        )

        return html

    def _format_skills(self, skills: List, matched: set, is_student: bool) -> str:
        """格式化技能标签"""
        import html
        def e(s):
            return html.escape(str(s), quote=True)

        if is_student:
            matched_html = "".join(
                f'<span class="skill-tag matched">{e(s)}</span>'
                for s in skills if s in matched
            )
            if matched_html:
                return matched_html
            return "".join(f'<span class="skill-tag">{e(s)}</span>' for s in skills[:10])
        else:
            return "".join(
                f'<span class="skill-tag matched">{e(s)}</span>' if s in matched
                else f'<span class="skill-tag missing">{e(s)}</span>'
                for s in skills
            )

    def _format_gap_analysis(self, gap_analysis: Dict) -> str:
        """格式化差距分析"""
        import html
        def e(s):
            return html.escape(str(s), quote=True)

        if not gap_analysis.get("details"):
            return "<p>无技能差距</p>"

        html_out = ""
        for gap in gap_analysis.get("details", []):
            priority_class = "critical" if gap.get("priority") == "critical" else ""
            html_out += f'''
            <div class="gap-item {priority_class}">
                <strong>{e(gap.get("skill", ""))}</strong>
                <span style="color: #666; font-size: 12px;"> (差距: {e(str(gap.get("gap", 0)))})</span>
            </div>
            '''
        return html_out

    def _format_recommendations(self, recommendations: List) -> str:
        """格式化建议"""
        import html
        def e(s):
            return html.escape(str(s), quote=True)

        if not recommendations:
            return "<p>暂无建议</p>"

        rec_type_map = {
            "positive": "#28a745",
            "priority": "#dc3545",
            "bonus": "#17a2b8",
            "general": "#6c757d"
        }

        html_out = ""
        for rec in recommendations:
            rec_type = rec.get("type", "general")
            color = rec_type_map.get(rec_type, "#6c757d")
            content = rec.get("content", "")
            skill = rec.get("skill", "")
            reason = rec.get("reason", "")

            if skill:
                html_out += f'''
            <div style="margin-bottom: 8px; padding: 8px; border-left: 3px solid {color};">
                <strong>{e(skill)}</strong>
                <p style="margin: 4px 0; color: #666; font-size: 13px;">{e(content)}</p>
                <p style="margin: 2px 0; color: #999; font-size: 12px;">原因: {e(reason)}</p>
            </div>
                '''
            else:
                html_out += f'''
            <div style="margin-bottom: 8px; padding: 8px; border-left: 3px solid {color};">
                <p style="margin: 4px 0; color: #666; font-size: 13px;">{e(content)}</p>
            </div>
                '''
        return html_out

    def generate_pdf(self, report_data: Dict, llm_summary: str = "", output_path: Optional[str] = None) -> bytes:
        """
        生成 PDF 报告

        Args:
            report_data: 报告数据
            llm_summary: LLM 摘要
            output_path: 输出路径

        Returns:
            PDF 字节数据
        """
        try:
            import weasyprint

            html = self.generate_html(report_data, llm_summary)
            pdf = weasyprint.HTML(string=html).write_pdf()

            if output_path:
                with open(output_path, 'wb') as f:
                    f.write(pdf)
                return b""

            return pdf

        except ImportError:
            raise ImportError("请安装 weasyprint: pip install weasyprint")

    def save_html(self, report_data: Dict, llm_summary: str, output_path: str):
        """保存 HTML 报告"""
        html = self.generate_html(report_data, llm_summary)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)


def generate_llm_summary(report_data: Dict) -> str:
    """
    使用 LLM 生成报告摘要

    Args:
        report_data: 报告数据

    Returns:
        LLM 生成的摘要
    """
    from app.utils.llm_client import get_llm_client
    from config.prompt_loader import load_prompt

    summary = report_data.get("summary", {})
    gap_analysis = report_data.get("gap_analysis", {})
    recommendations = report_data.get("recommendations", [])

    matched = summary.get("matched_skills", [])
    missing = summary.get("missing_skills", [])
    gap_details = gap_analysis.get("details", [])

    gap_lines = (
        "\n".join([f"- {g.get('skill', '')}: {g.get('priority', '')}" for g in gap_details[:3]])
        if gap_details else "无"
    )

    prompt = load_prompt(
        "report.summary.user",
        match_score=summary.get("match_score", 0),
        match_level=summary.get("match_level", ""),
        skill_coverage_pct=int(summary.get("skill_coverage", 0) * 100),
        matched_skills=", ".join(matched[:5]) if matched else "无",
        missing_skills=", ".join(missing[:5]) if missing else "无",
        gap_lines=gap_lines,
    )

    try:
        llm = get_llm_client()
        response = llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=300
        )
        return str(response)[:200]
    except Exception as e:
        return f"AI摘要生成失败: {str(e)[:80]}"


def generate_full_report(
    student_id: str,
    student_skills: List[Dict],
    job_id: str,
    job_name: str,
    job_skills: List[Dict],
    use_llm_summary: bool = True
) -> Dict:
    """
    生成完整报告（整合 L3 + L4）

    Args:
        student_id: 学生ID
        student_skills: 学生技能
        job_id: 岗位ID
        job_name: 岗位名称
        job_skills: 岗位技能
        use_llm_summary: 是否生成LLM摘要

    Returns:
        完整报告数据
    """
    from .report_generator import generate_match_report

    report = generate_match_report(
        student_id, student_skills,
        job_id, job_name, job_skills
    )

    if use_llm_summary:
        try:
            llm_summary = generate_llm_summary(report)
            report["llm_summary"] = llm_summary
        except Exception:
            report["llm_summary"] = "LLM摘要生成失败"

    return report


if __name__ == "__main__":
    from .report_generator import generate_match_report

    student_skills = [
        {"name": "Python", "category": "编程语言", "confidence": 0.85},
        {"name": "Django", "category": "后端框架", "confidence": 0.75},
        {"name": "MySQL", "category": "数据库", "confidence": 0.8},
        {"name": "Redis", "category": "数据库", "confidence": 0.6},
        {"name": "Docker", "category": "DevOps", "confidence": 0.65},
    ]

    job_skills = [
        {"name": "Python", "level": "必须", "weight": 0.9},
        {"name": "Django", "level": "必须", "weight": 0.8},
        {"name": "MySQL", "level": "必须", "weight": 0.85},
        {"name": "Redis", "level": "加分", "weight": 0.5},
        {"name": "Kubernetes", "level": "加分", "weight": 0.4},
    ]

    print("=" * 50)
    print("L4 报告生成测试")
    print("=" * 50)

    report = generate_match_report(
        "stu_test_001",
        student_skills,
        "job_test_001",
        "Python后端开发工程师",
        job_skills
    )

    report["student_skills"] = [s["name"] for s in student_skills]
    report["job_skills"] = [s["name"] for s in job_skills]

    generator = L4ReportGenerator()
    html = generator.generate_html(report, "这是一段测试摘要")

    output_path = "test_report.html"
    generator.save_html(report, "这是一段测试摘要", output_path)

    print(f"\nHTML 报告已生成: {output_path}")
    print(f"报告长度: {len(html)} 字符")
