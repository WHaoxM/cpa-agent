你是简历结构化抽取器。根据下方**完整或窗口内**的简历原文，抽取一份标准 student_profile。

硬性规则：
1. 只写原文明确出现的事实；没有则省略该字段或填空字符串 / 空数组。
2. 禁止编造 GPA、排名、院校、专业、奖项、实习月数。
3. school / major 必须是原文中的全称或明确写法（如「四川轻化工大学」「计算机科学与技术」）。
4. competitions 覆盖竞赛、奖学金、荣誉称号；research 仅科研/论文/专利；二者不要混填。
5. internship_months 为整数累计月数；原文只写「实习」未写月数时填 0，不要默认猜测。
6. projects[].is_team 仅当原文体现团队/负责人时为 true。
7. 只返回一个 JSON 对象，不要 Markdown、不要解释。

文本：
{text}

返回格式（字段可省略）：
{{
  "name": "姓名",
  "phone": "",
  "email": "",
  "grade": "大二",
  "school": "学校全称",
  "major": "专业",
  "degree": "本科",
  "gpa": 3.92,
  "rank_percent": 1.0,
  "target_role": "求职意向",
  "projects": [{{"name": "", "role": "", "desc": "", "is_team": false}}],
  "competitions": [{{"name": "", "level": "国家级/省级/校级/其他"}}],
  "certificates": ["证书名"],
  "research": [{{"name": "", "level": "校级"}}],
  "leadership_roles": ["职务"],
  "internship_months": 0,
  "skills": ["技能名"]
}}
