"""
简历NER训练数据生成Prompt
参考真实简历数据格式

Block类型: PERSONAL_INFO, SKILLS_CERTS, EDUCATION, WORK_EXPERIENCE, PROJECT_EXPERIENCE
实体类型: PERSON_NAME, POSITION, SKILL, DATE_RANGE, PROJECT_ROLE, SCHOOL, MAJOR, DEGREE, COMPANY, PROJECT_NAME, TOOL
"""

# 简历文本块生成Prompt
RESUME_BLOCK_GENERATION_SYSTEM = """你是一个简历数据生成专家，擅长生成多样化的简历文本数据。

核心原则：
1. 生成真实、自然的简历文本
2. 不要用占位符，直接生成完整内容
3. 不要生成任何解释，直接输出文本
4. 文本要符合中文简历的书写习惯"""

RESUME_BLOCK_GENERATION_USER = """请生成一份简历文本，包含以下部分：

部分要求：
- 姓名：{name}
- 求职方向：{position}
- 教育背景：{education}
- 工作经验：{experience}年

内容要求：
1. 专业技能（SKILLS_CERTS块）：
   - 列出8-15项技能，包括编程语言、框架、工具、数据库等
   - 每项技能有熟悉程度描述
   - 可以包含AI相关技能（LangChain、RAG、Agent等）

2. 项目经历（PROJECT_EXPERIENCE块）：
   - 至少3个项目
   - 每个项目包含：项目名称、时间、角色、项目描述、主要工作、项目成果
   - 项目内容要具体，有技术细节和成果数据

3. 工作经历（WORK_EXPERIENCE块）：
   - 1-2段工作经历
   - 每段包含：公司名称、职位、时间、主要工作内容
   - 工作内容要与技能匹配

4. 教育经历（EDUCATION块）：
   - 学校名称、专业、学历、时间
   - 可包含主修课程、成绩、获奖等

输出格式：
直接输出纯文本，不要JSON格式，用自然段落组织内容。

直接输出简历文本："""


# NER训练数据生成Prompt
NER_TRAINING_SYSTEM = """你是一个NER训练数据生成专家，生成符合以下格式的标注数据：

输出格式要求：
1. 输入：简历文本块
2. 输出：实体列表，包含type和span

实体类型定义：
- PERSON_NAME: 人名
- POSITION: 职位名称
- SKILL: 技能（编程语言、框架、工具、库）
- DATE_RANGE: 日期范围（如2024/01 - 至今）
- PROJECT_ROLE: 项目中的角色（如核心开发者、成员）
- SCHOOL: 学校名称
- MAJOR: 专业名称
- DEGREE: 学历
- COMPANY: 公司名称
- PROJECT_NAME: 项目名称
- TOOL: 工具软件
- CERTIFICATE: 证书
- LANGUAGE: 编程语言（与SKILL重叠时优先SKILL）"""

NER_TRAINING_USER = """给定以下简历文本块，提取所有实体：

{resume_text}

要求：
1. 识别所有实体及其类型
2. span必须是原文中的连续字符序列
3. 日期范围要包含起止时间
4. 只输出实体列表

输出JSON格式：
{{"entities": [{{"type": "实体类型", "span": "原文"}}]}}

直接输出JSON："""


# 上下文继承训练数据
CONTEXT_INHERITANCE_SYSTEM = """你是一个上下文推断专家，根据上下文推断省略信息"""

CONTEXT_INHERITANCE_USER = """上下文信息：
{context}

当前文本：
{current_text}

已知实体：
- 项目：{projects}
- 公司：{companies}

请推断当前文本中省略的主语/归属。

输出JSON：
{{"inferred_subject": "推断的主语", "belongs_to": "归属项目/公司", "confidence": 0.95}}

直接输出JSON："""


# 块分类训练数据
BLOCK_CLASSIFICATION_SYSTEM = """你是一个简历块分类专家"""

BLOCK_CLASSIFICATION_USER = """给定以下简历文本块，判断其类型：

文本内容：
{text}

可选类型：
- PERSONAL_INFO: 个人信息（姓名、联系方式、求职意向）
- SKILLS_CERTS: 专业技能
- EDUCATION: 教育经历
- WORK_EXPERIENCE: 工作经历
- PROJECT_EXPERIENCE: 项目经历
- OTHER: 其他信息

输出JSON：
{{"block_type": "类型", "confidence": 0.95, "reason": "判断理由"}}

直接输出JSON："""


def generate_resume_text_prompt(
    name: str = None,
    position: str = "大模型应用开发工程师",
    education: str = "本科",
    experience: int = 0,
) -> str:
    """生成简历文本Prompt"""
    return RESUME_BLOCK_GENERATION_USER.format(
        name=name or "随机生成",
        position=position,
        education=education,
        experience=experience,
    )


def generate_ner_prompt(resume_text: str) -> str:
    """生成NER训练Prompt"""
    return NER_TRAINING_USER.format(resume_text=resume_text)


# 批量生成多份简历文本
BATCH_RESUME_TEXT_SYSTEM = """你是一个简历数据生成专家。"""

BATCH_RESUME_TEXT_USER = """请一次性生成{number}份不同的简历文本，每份包含多个文本块。

要求：
1. 格式为JSONL（每行一个JSON对象）
2. 每份简历包含多个块（block），每个块有block_type和text
3. 块类型包括：PERSONAL_INFO, SKILLS_CERTS, EDUCATION, WORK_EXPERIENCE, PROJECT_EXPERIENCE
4. 内容要多样化：不同姓名、学校、公司、技能、项目
5. 每份简历要有3-5个PROJECT_EXPERIENCE块

输出格式：
每行一个JSON：
{{"blocks": [{{"block_type": "类型", "text": "文本内容"}}]}}

直接输出{number}行JSON，不要有任何其他文字："""


def get_resume_prompts() -> dict:
    return {
        "block_generation_system": RESUME_BLOCK_GENERATION_SYSTEM,
        "block_generation_user": RESUME_BLOCK_GENERATION_USER,
        "ner_system": NER_TRAINING_SYSTEM,
        "ner_user": NER_TRAINING_USER,
        "context_system": CONTEXT_INHERITANCE_SYSTEM,
        "context_user": CONTEXT_INHERITANCE_USER,
        "classification_system": BLOCK_CLASSIFICATION_SYSTEM,
        "classification_user": BLOCK_CLASSIFICATION_USER,
        "batch_resume_system": BATCH_RESUME_TEXT_SYSTEM,
        "batch_resume_user": BATCH_RESUME_TEXT_USER,
    }