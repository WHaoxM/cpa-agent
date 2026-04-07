"""
赛题要求的数据生成Prompt模板
基于AI的大学生职业规划智能体 - 训练数据生成

关键维度：
1. 学生画像：专业技能、证书、创新能力、学习能力、抗压能力、沟通能力、实习能力
2. 岗位画像：基础要求、职业技能、职业素养、发展潜力
3. 人岗匹配：4个维度的匹配分析
4. 换岗路径：每个岗位至少2条换岗路径
"""

# ============================================================
# 学生画像生成Prompt
# ============================================================

STUDENT_PROFILE_SYSTEM_PROMPT = """你是一个专业的简历数据生成专家，擅长生成多样化的学生就业能力画像数据。

生成规则：
1. 严格按照JSON Schema格式输出
2. 能力评分0-100，evidence必须有具体事例支撑
3. 自我评价不超过200字
4. 所有技能和经历必须相互匹配、自洽
5. 不要生成任何解释，直接输出JSON
6. phone必须脱敏格式：138****8888
7. 姓名使用真实感的中文或英文名
8. 论文、专利、项目等必须有具体名称和成果
9. 注重体现创新能力、学习能力、抗压能力、沟通能力等维度
10. 实习和项目经验要有具体的工作内容和成果"""

STUDENT_PROFILE_GENERATION_PROMPT = """请生成一份学生就业能力画像JSON数据：

基础信息：
- 姓名：{name}
- 学历：{education_level}
- 专业：{major}
- 学校：{school}
- 毕业年份：{graduation_year}

多样性要求：
- 性格特点：{personality}
- 求职偏好：{preferences}
- 能力特长：{strengths}

要求：
1. 包含完整的basic_info、professional_skills、general_abilities、practical_experience
2. general_abilities必须包含innovation_ability、learning_ability、anti_pressure_ability、communication_ability
3. 每个能力维度必须有score和evidence
4. practical_experience必须包含internships和projects
5. 技能和经历必须自洽匹配
6. 输出格式必须是合法JSON

直接输出JSON，不要有任何解释文字："""


# ============================================================
# 岗位画像生成Prompt
# ============================================================

JOB_POSTING_SYSTEM_PROMPT = """你是一个专业的岗位数据生成专家，擅长生成多样化的就业岗位画像数据。

生成规则：
1. 严格按照JSON Schema格式输出
2. 每个维度必须有weight权重（0-1）
3. 技能要求必须具体，不能模糊
4. 换岗路径必须至少2条，且要体现血缘关系
5. 不要生成任何解释，直接输出JSON
6. 公司名称使用真实存在的知名公司
7. 薪资范围要符合市场行情
8. 职位描述要有具体的岗位职责
9. 注重体现基础要求、职业技能、职业素养、发展潜力4个维度
10. 换岗路径要体现技能重叠度和转换难度"""

JOB_POSTING_GENERATION_PROMPT = """请生成一份就业岗位画像JSON数据：

基本信息：
- 职位名称：{job_title}
- 公司类型：{company_type}
- 所属行业：{industry}

多样性要求：
- 岗位层级：{level}
- 薪资范围：{salary_range}
- 核心技能：{core_skills}

要求：
1. 包含完整的company_info、job_requirements、professional_skills、general_abilities、development_potential
2. job_requirements必须包含education、major、experience、certifications
3. professional_skills必须包含required_skills（含权重）
4. general_abilities必须包含innovation_ability、learning_ability、anti_pressure_ability、communication_ability、teamwork
5. development_potential必须包含internship_exp_required、project_exp_required
6. career_path必须包含entry_level、mid_level、senior_level、management_level
7. job_transfer_paths必须包含至少2条换岗路径，每条包含target_job、transfer_reason、skill_overlap、difficulty、training_needed
8. 所有字段必须自洽匹配
9. 输出格式必须是合法JSON

直接输出JSON，不要有任何解释文字："""


# ============================================================
# 人岗匹配训练数据生成Prompt
# ============================================================

MATCHING_TRAINING_SYSTEM_PROMPT = """你是一个专业的人岗匹配分析专家，擅长生成人岗匹配训练数据。

生成规则：
1. 严格按照JSON格式输出
2. 匹配分析必须从基础要求、职业技能、职业素养、发展潜力4个维度进行
3. 每个维度必须有具体的得分和理由
4. 差距分析必须具体指出不足和提升建议
5. 不要生成任何解释，直接输出JSON
6. 匹配度计算要合理（考虑权重）
7. 每个维度的权重必须标注
8. 最终匹配结果要有可解释的理由"""

MATCHING_TRAINING_PROMPT = """给定学生画像和岗位画像，生成人岗匹配分析JSON：

学生画像：
{student_profile}

岗位画像：
{job_posting}

要求：
1. 生成完整的matching_result，包含：
   - dimension_scores: 4个维度的得分（基础要求、职业技能、职业素养、发展潜力）
   - dimension_weights: 4个维度的权重
   - overall_score: 综合匹配度（0-100）
   - gap_analysis: 差距分析，包含每个维度的具体差距
   - recommendations: 提升建议
2. 基础要求维度：对比学生的学历、专业、证书与岗位要求
3. 职业技能维度：对比学生的编程语言、框架、工具与岗位技能要求
4. 职业素养维度：对比学生的创新能力、学习能力、抗压能力、沟通能力
5. 发展潜力维度：对比学生的实习经历、项目经验与岗位要求
6. 每个维度的得分必须有理有据
7. gap_analysis必须具体指出学生的不足之处
8. recommendations必须给出可操作的学习建议

输出格式：
{{
  "match_id": "match_001",
  "student_id": "学生ID",
  "job_id": "岗位ID",
  "dimension_scores": {{
    "basic_requirements": {{"score": 85, "weight": 0.25, "reason": "原因"}},
    "professional_skills": {{"score": 70, "weight": 0.35, "reason": "原因"}},
    "professional_quality": {{"score": 80, "weight": 0.2, "reason": "原因"}},
    "development_potential": {{"score": 75, "weight": 0.20, "reason": "原因"}}
  }},
  "overall_score": 综合得分,
  "gap_analysis": {{
    "basic_requirements": {{"gap": "差距描述", "missing": ["缺失项"]}},
    "professional_skills": {{"gap": "差距描述", "missing": ["缺失技能"]}},
    "professional_quality": {{"gap": "差距描述", "missing": ["缺失能力"]}},
    "development_potential": {{"gap": "差距描述", "missing": ["缺失经验"]}}
  }},
  "recommendations": [
    {{"dimension": "维度", "action": "具体行动", "priority": "高/中/低"}}
  ],
  "can_match": true/false,
  "match_reasons": ["匹配原因"]
}}

直接输出JSON，不要有任何解释文字："""


# ============================================================
# 换岗路径图谱训练数据生成Prompt
# ============================================================

CAREER_PATH_SYSTEM_PROMPT = """你是一个专业的职业发展分析专家，擅长生成职业路径和换岗路径数据。

生成规则：
1. 严格按照JSON Schema格式输出
2. 换岗路径必须体现血缘关系和技能重叠
3. 路径必须至少2条，且要体现不同的转换方向
4. 每个路径必须有具体的培训需求
5. 不要生成任何解释，直接输出JSON
6. 路径要符合真实的职业发展规律
7. 技能重叠度计算要合理
8. 转换难度评估要考虑市场供需"""

CAREER_PATH_GENERATION_PROMPT = """基于以下岗位，生成职业发展路径和换岗路径JSON：

目标岗位：{job_title}
当前岗位：{current_position}

要求：
1. 生成完整的career_path，包含：
   - entry_level: 入门级岗位
   - mid_level: 中级岗位
   - senior_level: 高级岗位
   - management_level: 管理岗位
   - promotion_requirements: 晋升要求
2. 生成至少2条job_transfer_paths，每条包含：
   - target_job: 目标岗位
   - transfer_reason: 转换原因
   - skill_overlap: 技能重叠度（0-1）
   - difficulty: 转换难度（容易/中等/困难）
   - training_needed: 需要补充的培训
3. 换岗路径要体现不同的职业发展方向
4. 路径之间要有差异化，不能完全相同
5. 所有路径必须符合真实的职业发展规律

直接输出JSON，不要有任何解释文字："""


# ============================================================
# 职业报告生成训练数据Prompt
# ============================================================

CAREER_REPORT_SYSTEM_PROMPT = """你是一个专业的职业规划报告生成专家，擅长生成可操作的职业发展规划报告。

生成规则：
1. 严格按照JSON格式输出
2. 报告必须包含职业探索、目标设定、行动计划、成果展示
3. 每个阶段必须有具体的量化指标
4. 行动计划必须可操作、可执行
5. 不要生成任何解释，直接输出JSON
6. 报告要有逻辑递进关系
7. 评估周期和指标必须明确
8. 要体现阶段性的成果展示"""

CAREER_REPORT_GENERATION_PROMPT = """基于以下人岗匹配分析结果，生成职业发展规划报告JSON：

学生信息：
{student_info}

目标岗位：
{target_job}

匹配分析：
{matching_analysis}

要求：
1. 生成完整的career_report，包含：
   - career_exploration: 职业探索
     * 行业分析
     * 岗位发展分析
     * 企业需求分析
   - career_objectives: 职业目标设定
     * 短期目标（1年内）
     * 中期目标（3年）
     * 长期目标（5年）
   - action_plan: 行动计划
     * 学习路径
     * 实践安排
     * 评估周期与指标
   - achievement_display: 成果展示
     * 阶段性成果
     * 能力提升证据
2. 行动计划必须包含具体的学习内容和时间节点
3. 评估周期必须有明确的复评时间
4. 要体现阶段性的里程碑
5. 报告要有可解释性和可操作性

直接输出JSON，不要有任何解释文字："""


# ============================================================
# 辅助函数
# ============================================================

def generate_student_profile_prompt(
    name: str = None,
    education_level: str = "本科",
    major: str = "计算机科学与技术",
    school: str = None,
    graduation_year: int = 2025,
    personality: str = "内向但善于思考",
    preferences: str = "互联网行业，技术导向",
    strengths: str = "编程能力强，喜欢解决复杂问题",
) -> str:
    """生成学生画像Prompt"""
    return STUDENT_PROFILE_GENERATION_PROMPT.format(
        name=name or "随机生成",
        education_level=education_level,
        major=major,
        school=school or "随机生成",
        graduation_year=graduation_year,
        personality=personality,
        preferences=preferences,
        strengths=strengths,
    )


def generate_job_posting_prompt(
    job_title: str = "Java开发工程师",
    company_type: str = "互联网",
    industry: str = "互联网",
    level: str = "中级",
    salary_range: str = "15k-25k",
    core_skills: str = "Java, Spring, MySQL",
) -> str:
    """生成岗位画像Prompt"""
    return JOB_POSTING_GENERATION_PROMPT.format(
        job_title=job_title,
        company_type=company_type,
        industry=industry,
        level=level,
        salary_range=salary_range,
        core_skills=core_skills,
    )


def generate_matching_training_prompt(
    student_profile: str,
    job_posting: str,
) -> str:
    """生成人岗匹配训练Prompt"""
    return MATCHING_TRAINING_PROMPT.format(
        student_profile=student_profile,
        job_posting=job_posting,
    )


def get_all_prompts() -> dict:
    """获取所有Prompt模板"""
    return {
        "student_system": STUDENT_PROFILE_SYSTEM_PROMPT,
        "student_generation": STUDENT_PROFILE_GENERATION_PROMPT,
        "job_system": JOB_POSTING_SYSTEM_PROMPT,
        "job_generation": JOB_POSTING_GENERATION_PROMPT,
        "matching_system": MATCHING_TRAINING_SYSTEM_PROMPT,
        "matching_generation": MATCHING_TRAINING_PROMPT,
        "career_path_system": CAREER_PATH_SYSTEM_PROMPT,
        "career_path_generation": CAREER_PATH_GENERATION_PROMPT,
        "report_system": CAREER_REPORT_SYSTEM_PROMPT,
        "report_generation": CAREER_REPORT_GENERATION_PROMPT,
    }


# 示例用法
if __name__ == "__main__":
    # 学生画像Prompt
    print("=== 学生画像Prompt ===")
    print(generate_student_profile_prompt(
        name="张三",
        education_level="本科",
        major="软件工程",
    ))
    print()

    # 岗位画像Prompt
    print("=== 岗位画像Prompt ===")
    print(generate_job_posting_prompt(
        job_title="前端开发工程师",
        company_type="互联网",
        industry="互联网",
    ))
    print()

    # 换岗路径Prompt
    print("=== 换岗路径Prompt ===")
    print(CAREER_PATH_GENERATION_PROMPT.format(
        job_title="前端开发工程师",
        current_position="初级前端开发工程师",
    ))