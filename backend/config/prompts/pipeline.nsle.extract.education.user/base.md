从以下文本中抽取教育经历，返回JSON。务必抽取原文中出现的 GPA、专业排名（若有）；没有则省略对应字段，禁止编造。

文本：
{text}

返回格式：
{{"education": [{{"school": "学校名", "major": "专业", "degree": "学历", "duration": "时间", "gpa": 3.9, "rank_percent": 1.0}}]}}

说明：gpa 为数字；rank_percent 为排名百分数（前1% → 1.0）。无则不要填假值。
