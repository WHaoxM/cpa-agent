从以下文本中抽取实习/工作/项目经历，返回JSON数组。项目经历也必须抽取；没有的字段可空，禁止编造公司或项目。

文本：
{text}

返回格式：
{{"experiences": [{{"company": "公司或空", "position": "角色/职位", "duration": "时间", "description": "描述", "type": "internship|work|project", "name": "项目或经历名称"}}]}}

type 取值：实习→internship，正式工作→work，科研/课程/个人项目→project。
