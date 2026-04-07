"""
端到端实体抽取训练数据模板 (SFT Distillation Template)
=====================================================

用途：
    1. 简历实体提取训练
    2. 岗位描述(JD)实体提取训练
    3. 统一人岗匹配画像训练

数据格式：Messages格式 (兼容Qwen/Llama等主流模型)
"""

# ============================================================
# 模板1: 简历实体提取 (Resume Entity Extraction)
# ============================================================
RESUME_TEMPLATE = {
    "messages": [
        {
            "role": "system",
            "content": """你是一个专业的简历信息抽取专家。
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
        },
        {
            "role": "user",
            "content": "请从以下简历文本中抽取信息：\n\n{resume_text}"}
        ,
        {
            "role": "assistant",
            "content": """{
  "person_name": "{person_name}",
  "position": "{position}",
  "skills": [
    {skills_list}
  ],
  "education": [
    {education_list}
  ],
  "work_experience": [
    {work_list}
  ],
  "project_experience": [
    {project_list}
  ],
  "certificates": [{certificates_list}]
}"""
        }
    ]
}


# ============================================================
# 模板2: 岗位描述实体提取 (Job Description Entity Extraction)
# ============================================================
JD_TEMPLATE = {
    "messages": [
        {
            "role": "system",
            "content": """你是一个专业的岗位信息抽取专家。
你的任务是从岗位描述文本中准确抽取结构化信息。

【抽取要求】
1. 严格按照原文语义抽取，不要推测未明确提及的信息
2. 技能要求分为三个等级：必须具备(必要条件)、优先考虑(加分项)、了解即可(辅助参考)
3. 学历要求：严格按原文表述，如"本科及以上"、"硕士优先"
4. 所有抽取内容必须出现在原文中，不可自己编造

【输出格式】
严格按照以下JSON格式输出，不要包含任何其他内容：
{
  "job_title": "岗位名称",
  "company": "公司名称",
  "location": "工作地点",
  "salary_range": "薪资范围(如无可留空)",
  "industry": "所属行业",
  "company_scale": "公司规模(如无可留空)",
  "required_skills": [
    {
      "name": "技能名称",
      "level": "必须具备|优先考虑|了解即可",
      "source": "原文描述"
    }
  ],
  "education_requirements": {
    "degree": "学历要求",
    "major": "专业要求(如无可留空)",
    "source": "原文描述"
  },
  "experience_requirements": {
    "years": "工作年限要求(如无可留空)",
    "type": "经验类型(必须具备经验/可接受应届生)",
    "source": "原文描述"
  },
  "soft_skills": ["软技能1", "软技能2"],
  "growth_potential": "发展潜力描述(如晋升通道、培养计划等)",
  "job_responsibilities": ["职责1", "职责2"]
}"""
        },
        {
            "role": "user",
            "content": "请从以下岗位描述中抽取信息：\n\n{jd_text}"
        },
        {
            "role": "assistant",
            "content": """{
  "job_title": "{job_title}",
  "company": "{company}",
  "location": "{location}",
  "salary_range": "{salary_range}",
  "industry": "{industry}",
  "company_scale": "{company_scale}",
  "required_skills": [
    {skills_list}
  ],
  "education_requirements": {
    "degree": "{degree}",
    "major": "{major}",
    "source": "{edu_source}"
  },
  "experience_requirements": {
    "years": "{years}",
    "type": "{exp_type}",
    "source": "{exp_source}"
  },
  "soft_skills": [{soft_skills_list}],
  "growth_potential": "{growth_potential}",
  "job_responsibilities": [{responsibilities_list}]
}"""
        }
    ]
}


# ============================================================
# 模板3: 人岗匹配分析 (Person-Job Matching)
# ============================================================
MATCHING_TEMPLATE = {
    "messages": [
        {
            "role": "system",
            "content": """你是一个专业的人岗匹配分析师。
你的任务是基于简历和岗位信息，分析候选人与岗位的匹配程度。

【分析维度】
1. 技能匹配度：核心技能是否匹配，缺失技能有哪些
2. 学历符合度：学历和专业是否满足要求
3. 经验匹配度：相关工作/项目经验是否足够
4. 发展潜力：候选人能力是否超出岗位要求(有成长空间)

【评分标准】
- 技能匹配率 = 匹配技能数 / 岗位要求技能数 × 100%
- 评分等级：90-100%优秀, 80-89%良好, 70-79%一般, <70%不匹配

【输出格式】
严格按照以下JSON格式输出：
{
  "overall_score": 85,
  "skill_match_rate": 0.85,
  "skill_analysis": {
    "matched": [{"skill": "Python", "candidate_level": "熟悉", "required_level": "熟悉"}],
    "gap": [{"skill": "Kubernetes", "required_level": "熟悉", "gap_level": "缺失"}]
  },
  "education_match": {
    "score": 100,
    "analysis": "学历和专业均满足要求"
  },
  "experience_match": {
    "score": 80,
    "analysis": "有相关项目经验"
  },
  "growth_potential": {
    "score": 90,
    "analysis": "候选人技能超出岗位要求，有较大成长空间"
  },
  "recommendation": "建议面试",
  "improvement_suggestions": ["建议补充Kubernetes相关经验", "可考虑内部转岗机会"]
}"""
        },
        {
            "role": "user",
            "content": """请分析以下候选人与岗位的匹配程度：

【简历信息】
{resume_info}

【岗位信息】
{job_info}"""
        },
        {
            "role": "assistant",
            "content": """{
  "overall_score": {overall_score},
  "skill_match_rate": {skill_match_rate},
  "skill_analysis": {
    "matched": [{matched_skills}],
    "gap": [{gap_skills}]
  },
  "education_match": {
    "score": {edu_score},
    "analysis": "{edu_analysis}"
  },
  "experience_match": {
    "score": {exp_score},
    "analysis": "{exp_analysis}"
  },
  "growth_potential": {
    "score": {growth_score},
    "analysis": "{growth_analysis}"
  },
  "recommendation": "{recommendation}",
  "improvement_suggestions": [{suggestions}]
}"""
        }
    ]
}


# ============================================================
# 模板4: 能力画像生成 (Persona Profile Generation)
# ============================================================
PROFILE_TEMPLATE = {
    "messages": [
        {
            "role": "system",
            "content": """你是一个专业的职业发展顾问。
你的任务是基于候选人的简历和岗位信息，生成详细的职业画像。

【画像维度】
1. 技术栈分布：按技能分类（编程语言、框架、工具、领域知识）
2. 经验分布：按项目类型、工作经历分类
3. 能力雷达图数据：专业技能、通用素质、学习能力、创新能力等
4. 竞争力分析：优势、劣势、发展建议

【输出格式】
严格按照以下JSON格式输出：
{
  "persona_summary": "一句话概括候选人定位",
  "technical_stack": {
    "languages": ["Python", "Java"],
    "frameworks": ["FastAPI", "Spring Boot"],
    "tools": ["Git", "Docker"],
    "domain_knowledge": ["RAG", "知识图谱", "Agent"]
  },
  "experience_summary": {
    "total_years": 2,
    "domains": ["AI应用开发", "后端开发"],
    "project_types": ["RAG系统", "Agent系统", "知识图谱"]
  },
  "competency_radar": {
    "专业技能": 85,
    "学习能力": 90,
    "创新能力": 75,
    "沟通能力": 80,
    "抗压能力": 70,
    "团队协作": 85
  },
  "strengths": ["技能全面", "有AI落地经验"],
  "weaknesses": ["缺乏大规模系统经验"],
  "career_direction": "AI应用开发工程师",
  "development_suggestions": ["建议深入一个领域成为专家"]
}"""
        },
        {
            "role": "user",
            "content": """请基于以下信息生成职业画像：

{candidate_info}

【目标岗位方向】
{job_direction}"""
        },
        {
            "role": "assistant",
            "content": """{
  "persona_summary": "{persona_summary}",
  "technical_stack": {
    "languages": [{languages}],
    "frameworks": [{frameworks}],
    "tools": [{tools}],
    "domain_knowledge": [{domain_knowledge}]
  },
  "experience_summary": {
    "total_years": {total_years},
    "domains": [{domains}],
    "project_types": [{project_types}]
  },
  "competency_radar": {
    "专业技能": {skill_score},
    "学习能力": {learning_score},
    "创新能力": {innovation_score},
    "沟通能力": {communication_score},
    "抗压能力": {stress_score},
    "团队协作": {teamwork_score}
  },
  "strengths": [{strengths}],
  "weaknesses": [{weaknesses}],
  "career_direction": "{career_direction}",
  "development_suggestions": [{suggestions}]
}"""
        }
    ]
}


# ============================================================
# 模板5: 职业路径规划 (Career Path Planning)
# ============================================================
CAREER_PATH_TEMPLATE = {
    "messages": [
        {
            "role": "system",
            "content": """你是一个专业的职业规划顾问。
你的任务是基于候选人的当前能力和目标岗位，规划职业发展路径。

【规划要求】
1. 提供分阶段目标（短期1年、中期3年、长期5年）
2. 每阶段包含具体可执行的学习/实践计划
3. 考虑技能提升的逻辑顺序（先打好基础，再拓展高级技能）
4. 预测每阶段的成长后的市场竞争力

【输出格式】
严格按照以下JSON格式输出：
{
  "current_position": "当前定位",
  "target_position": "目标岗位",
  "short_term_plan": {
    "duration": "1年",
    "goals": ["目标1", "目标2"],
    "actions": [
      {"action": "具体行动", "expected_outcome": "预期成果", "timeline": "时间节点"}
    ],
    "expected_salary_range": "薪资范围"
  },
  "medium_term_plan": {
    "duration": "3年",
    "goals": ["目标1", "目标2"],
    "actions": [
      {"action": "具体行动", "expected_outcome": "预期成果", "timeline": "时间节点"}
    ],
    "expected_salary_range": "薪资范围"
  },
  "long_term_plan": {
    "duration": "5年",
    "goals": ["目标1", "目标2"],
    "actions": [
      {"action": "具体行动", "expected_outcome": "预期成果", "timeline": "时间节点"}
    ],
    "expected_salary_range": "薪资范围"
  },
  "skill_gap_analysis": {
    "current_skills": ["当前技能"],
    "target_skills": ["目标技能"],
    "gap_skills": ["需要补足的技能"]
  }
}"""
        },
        {
            "role": "user",
            "content": """请为候选人规划职业发展路径：

【候选人当前能力】
{current_profile}

【目标岗位】
{target_position}

【行业背景】
{industry_info}"""
        },
        {
            "role": "assistant",
            "content": """{
  "current_position": "{current_position}",
  "target_position": "{target_position}",
  "short_term_plan": {
    "duration": "1年",
    "goals": [{short_goals}],
    "actions": [
      {short_actions}
    ],
    "expected_salary_range": "{short_salary}"
  },
  "medium_term_plan": {
    "duration": "3年",
    "goals": [{medium_goals}],
    "actions": [
      {medium_actions}
    ],
    "expected_salary_range": "{medium_salary}"
  },
  "long_term_plan": {
    "duration": "5年",
    "goals": [{long_goals}],
    "actions": [
      {long_actions}
    ],
    "expected_salary_range": "{long_salary}"
  },
  "skill_gap_analysis": {
    "current_skills": [{current_skills}],
    "target_skills": [{target_skills}],
    "gap_skills": [{gap_skills}]
  }
}"""
        }
    ]
}


# ============================================================
# 辅助函数：生成训练样本
# ============================================================

def format_skill(skill_name, level, source=""):
    return f'{{"name": "{skill_name}", "level": "{level}", "source": "{source}"}}'


def format_education(school, degree, major, start_date, end_date):
    return f'{{"school": "{school}", "degree": "{degree}", "major": "{major}", "start_date": "{start_date}", "end_date": "{end_date}"}}'


def format_work(company, position, start_date, end_date, description):
    return f'{{"company": "{company}", "position": "{position}", "start_date": "{start_date}", "end_date": "{end_date}", "description": "{description}"}}'


def format_project(project_name, role, start_date, end_date, description):
    return f'{{"project_name": "{project_name}", "role": "{role}", "start_date": "{start_date}", "end_date": "{end_date}", "description": "{description}"}}'


if __name__ == "__main__":
    # 示例：生成一条训练数据
    print("=== 训练数据模板示例 ===")
    print("\n1. 简历抽取模板 (RESUME_TEMPLATE)")
    print("- 用于训练模型从简历文本中抽取结构化信息")
    print("- 支持：person_name, skills, education, work_experience, project_experience, certificates")

    print("\n2. 岗位描述模板 (JD_TEMPLATE)")
    print("- 用于训练模型从岗位描述中抽取岗位要求")
    print("- 支持：job_title, required_skills, education_requirements, experience_requirements, soft_skills")

    print("\n3. 人岗匹配模板 (MATCHING_TEMPLATE)")
    print("- 用于训练模型进行人岗匹配分析")
    print("- 输出：overall_score, skill_match_rate, education_match, experience_match, recommendation")

    print("\n4. 能力画像模板 (PROFILE_TEMPLATE)")
    print("- 用于训练模型生成候选人能力画像")
    print("- 输出：technical_stack, competency_radar, strengths, weaknesses, career_direction")

    print("\n5. 职业路径规划模板 (CAREER_PATH_TEMPLATE)")
    print("- 用于训练模型规划职业发展路径")
    print("- 输出：short_term_plan, medium_term_plan, long_term_plan, skill_gap_analysis")

    print("\n" + "="*50)
    print("数据格式: messages (Role-Content对)")
    print("兼容模型: Qwen, Llama, ChatGLM等主流LLM")
    print("训练方式: SFT / LoRA / QLoRA")
