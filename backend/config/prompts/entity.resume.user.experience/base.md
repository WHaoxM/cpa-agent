提取工作或项目经历。文本中<role>标签是角色，<date>标签是时间，<skill>标签是技能，必须全部填入JSON。{confirmed}

**重要提示**：
1. 工作/项目经历可能在文本的任何位置（开头、中间或末尾）
2. 请仔细查找公司/项目名称、职位、时间和技能信息
3. 如果文本较长，建议先搜索工作/项目相关关键词定位

标题分类规则：
- 标题含"公司/实验室/集团/科技/网络/有限"→ 填入 company
- 标题含"系统/平台/项目/课题/引擎/知识库"→ 填入 project
- 例如："省重点人工智能实验室" → project（不是company）

正例：
输入：【字节跳动】
<role>后端开发</role>
<date>2023/07-2024/06</date>
<skill>Python</skill>
输出：{{"company": "字节跳动", "project": "", "position": "后端开发", "start_date": "2023/07", "end_date": "2024/06", "skills": ["Python"]}}

反例（错误）：
输入：【省重点人工智能实验室】
<role>核心开发者</role>
<date>2024/11-至今</date>
错误输出：{{"company": "省重点人工智能实验室", ...}}  ← 实验室不是公司，应填入project
正确输出：{{"company": "", "project": "省重点人工智能实验室", "position": "核心开发者", "start_date": "2024/11", "end_date": "至今", "skills": []}}

文本：
{indexed_text}

输出JSON：
{{"company": "", "project": "", "position": "", "start_date": "", "end_date": "", "skills": []}}
