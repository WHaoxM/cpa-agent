下面是同一份简历经多个文本窗口分别抽取得到的 partial student_profile JSON 列表。请合并为**一份**完整、无重复的 canonical student_profile。

合并规则：
1. 标量字段（name/school/major/degree/gpa/…）：取信息最完整且互不矛盾的值；冲突时优先更长、更具体的院校/专业全称。
2. 列表字段：按名称去重合并；competitions/research/projects/certificates/skills/leadership_roles 均去重。
3. internship_months：取各窗口中的最大值（勿相加重复实习）。
4. 禁止引入 partials 中不存在的新事实。
5. 只返回一个 JSON 对象，格式与单次 student_profile 抽取相同。

partials：
{partials_json}
