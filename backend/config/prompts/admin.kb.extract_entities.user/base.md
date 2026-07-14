从以下内容提取职业/技能实体，返回JSON。

类型：{source_type}

内容：
{content}

提取字段：
- skills: 技能名称数组
- roles: 职业或岗位名称数组
- relations: 技能与职业关系数组，元素格式为 {{"skill": "技能", "role": "职业", "type": "关系类型"}}
- salary_info: 薪资信息数组，元素格式为 {{"role": "职业", "low": 最低薪资, "high": 最高薪资}}
- demand_info: 需求信息数组，元素格式为 {{"role": "职业", "level": "需求等级"}}
