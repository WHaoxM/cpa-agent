"""
Teacher Model Prompt Templates
教师模型简历生成Prompt模板库
"""

RESUME_GENERATION_SYSTEM_PROMPT = """你是一个专业的简历生成专家，擅长根据用户需求生成高质量、结构化的简历JSON数据。

生成规则：
1. 所有日期格式必须为 YYYY-MM 或 YYYY-MM-DD
2. "至今"统一用"至今"表示，不要用"现在"、"Present"等
3. 教育经历和工作经历必须按时间倒序排列
4. 技术栈必须与项目/工作经历匹配
5. 所有文本使用中文，姓名可以是中文或英文
6. JSON输出必须严格符合提供的Schema格式
7. 不要生成任何解释性文字，直接输出JSON
8. phone必须脱敏（格式：138****8888）
9. 简历内容要真实可信，不要生成明显虚假信息（如不存在的公司）
10. 项目描述要有具体数字和成果（如"提升性能30%"）"""


RESUME_GENERATION_USER_PROMPT_TEMPLATE = """请生成一份简历JSON数据，要求如下：

**背景信息：**
- 目标岗位：{target_position}
- 工作年限：{working_years}
- 最高学历：{education_level}
- 专业方向：{major_domain}

**多样性要求：**
{diversity_requirements}

**生成要求：**
1. 必须包含至少{project_count}个项目经历
2. 必须包含至少{skill_count}种技能
3. 教育经历和公司名称要有变化，不要重复

请直接输出JSON，不要有任何解释："""


# 多样性变化模板
DIVERSITY_TEMPLATES = [
    """
**多样性控制：**
- 行业：互联网/金融/教育/医疗中的一个随机行业
- 公司类型：初创公司/上市公司/跨国公司混合
- 技术栈：主流技术栈（Python/Java/Go）+ 新兴技术（AI/云原生）
- 项目类型：后台服务/数据系统/AI应用/平台开发
""",
    """
**多样性控制：**
- 行业：电商/社交/企业服务/游戏中的一个随机行业
- 公司类型：独角兽/传统企业数字化转型/外资企业
- 技术栈：全栈技能（前端+后端+DevOps）
- 项目类型：用户产品/工具平台/数据平台/基础设施
""",
    """
**多样性控制：**
- 行业：金融科技/医疗健康/智能制造/新能源汽车中的一个
- 公司类型：国企/民企/外企混合
- 技术栈：Java技术栈（Spring全家桶）/ Python技术栈
- 项目类型：业务系统/数据分析/算法应用/自动化工具
""",
]


# 单一简历生成Prompt（用于API调用）
SINGLE_RESUME_PROMPT = """你是一个专业的简历生成专家。

请生成一份完整的简历JSON，要求：
1. 姓名：{name}
2. 目标岗位：{target_position}
3. 工作年限：{working_years}年
4. 学历：{degree}
5. 专业：{major}

技能偏好：{skill_preference}
项目偏好：{project_preference}

输出格式（必须是合法JSON）：
{{
  "resume_id": "resume_{index}",
  "personal_info": {{
    "name": "姓名",
    "contact": {{"phone": "138****8888", "email": "email@example.com"}},
    "position": "目标岗位",
    "working_years": 年限
  }},
  "education": [...],
  "work_experience": [...],
  "project_experience": [...],
  "skills": {{...}},
  "certificates": [...],
  "languages": [...]
}}

要求：
- 至少2段工作经历
- 至少3个项目经历
- 至少8种不同技能
- 真实可信的公司名称
- 所有日期格式为 YYYY-MM 或 至今

直接输出JSON，不要有任何解释文字。"""


# 批量生成Prompt（用于一次生成多条）
BATCH_RESUME_PROMPT = """你是一个专业的简历生成专家。

请一次性生成{number}份不同的简历JSON，输出格式为JSONL（一行一个JSON）。

多样性和变化要求：
- 姓名：10个不同的中英文姓名
- 目标岗位：后端开发/算法工程师/全栈开发/运维/DBA等不同岗位
- 工作年限：1-10年不等
- 公司：使用真实存在的知名公司名称（阿里巴巴、腾讯、字节跳动、百度、美团、京东、网易、快手、小米、华为等）
- 学校：使用真实985/211高校名称
- 技术栈：Python+AI、Java+Spring、Go+云原生、JavaScript+前端等不同组合

每份简历必须包含：
1. personal_info（姓名、联系方式、求职岗位、工作年限）
2. education（学校、专业、学历、时间）
3. work_experience（公司、职位、工作内容）
4. project_experience（项目名、角色、技术栈、成果）
5. skills（编程语言、框架、数据库、工具）
6. certificates（可选）
7. languages（可选）

输出格式：每行一个完整JSON，不要有任何分隔符或解释文字。
直接开始生成："""


# 用于微调的简历片段Prompt（短文本）
RESUME_BLOCK_PROMPT = """从以下简历文本片段中提取实体：

{resume_block}

提取以下类型实体：
- SKILL: 技能（编程语言、框架、工具等）
- PROJECT_NAME: 项目名称
- COMPANY: 公司名称
- SCHOOL: 学校名称
- POSITION: 职位名称
- PROJECT_ROLE: 项目中的角色
- DATE_RANGE: 日期范围
- DEGREE: 学历
- CERTIFICATE: 证书
- TOOL: 工具

输出JSON格式：
{{"entities": [{{"type": "类型", "span": "原文"}}]}}"""


# 上下文继承训练Prompt
CONTEXT_INHERITANCE_PROMPT = """给定上下文和当前文本，预测实体的上下文归属：

上下文（前面文本）：
{context}

当前文本：
{current_text}

已知上下文中的项目/公司：
- 项目：{projects}
- 公司：{companies}

当前文本中提到的实体应该归属到哪个项目/公司？

输出JSON格式：
{{"context_relations": [{{"entity": "实体", "belongs_to": "归属项目/公司", "reason": "原因"}}]}}"""


# 实体类型一致性训练Prompt
ENTITY_TYPE_NORMALIZATION_PROMPT = """给定实体和上下文，判断其正确类型：

实体：{entity}
上下文：{context}

可选类型：
- SKILL: 技能
- PROJECT_NAME: 项目名
- TOOL: 工具
- POSITION: 职位
- PROJECT_ROLE: 项目角色

判断这个实体最可能的类型，并说明理由。

输出JSON格式：
{{"type": "类型", "confidence": 0.95, "reason": "理由"}}"""


# 日期推理训练Prompt
DATE_INFERENCE_PROMPT = """从以下文本中提取和推理日期：

文本：{text}

已知信息：
{known_dates}

请提取所有日期，并推理缺失的日期（如只知道年份，推断为该年1月至12月）。

输出JSON格式：
{{"dates": [{{"text": "原文", "inferred_start": "YYYY-MM", "inferred_end": "YYYY-MM"}}]}}"""


def generate_single_resume_prompt(
    name: str = None,
    target_position: str = "后端开发工程师",
    working_years: int = 3,
    degree: str = "本科",
    major: str = "计算机科学与技术",
    skill_preference: str = "Python, Java, MySQL",
    project_preference: str = "后台服务系统",
    index: int = 1,
) -> str:
    """生成单份简历的Prompt"""
    return SINGLE_RESUME_PROMPT.format(
        name=name or "随机生成",
        target_position=target_position,
        working_years=working_years,
        degree=degree,
        major=major,
        skill_preference=skill_preference,
        project_preference=project_preference,
        index=index,
    )


def generate_batch_resume_prompt(number: int = 10) -> str:
    """生成批量简历的Prompt"""
    import random
    diversity = random.choice(DIVERSITY_TEMPLATES)

    return BATCH_RESUME_PROMPT.format(
        number=number,
        diversity_requirements=diversity,
    )


def get_resume_generation_prompts() -> dict:
    """获取所有Prompt模板"""
    return {
        "system": RESUME_GENERATION_SYSTEM_PROMPT,
        "single": SINGLE_RESUME_PROMPT,
        "batch": BATCH_RESUME_PROMPT,
        "block_extraction": RESUME_BLOCK_PROMPT,
        "context_inheritance": CONTEXT_INHERITANCE_PROMPT,
        "entity_type_normalization": ENTITY_TYPE_NORMALIZATION_PROMPT,
        "date_inference": DATE_INFERENCE_PROMPT,
    }


# 示例用法
if __name__ == "__main__":
    # 生成单份简历Prompt
    prompt = generate_single_resume_prompt(
        name="张三",
        target_position="算法工程师",
        working_years=5,
    )
    print("=== 单份简历Prompt ===")
    print(prompt)
    print()

    # 生成批量Prompt
    batch_prompt = generate_batch_resume_prompt(10)
    print("=== 批量简历Prompt ===")
    print(batch_prompt)