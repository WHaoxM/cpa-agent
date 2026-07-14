// ===========================================
// Neo4j 契约约束初始化脚本
// 对应: .trae/rules/neo4j-contract.md
// 全部使用 IF NOT EXISTS，可重复执行
// ===========================================

// 一、Graph A: 技能与职业知识图谱 - 唯一约束
CREATE CONSTRAINT skill_name IF NOT EXISTS FOR (s:Skill) REQUIRE s.name IS UNIQUE;
CREATE CONSTRAINT portrait_id IF NOT EXISTS FOR (j:JobPortrait) REQUIRE j.portrait_id IS UNIQUE;
CREATE CONSTRAINT category_name IF NOT EXISTS FOR (c:SkillCategory) REQUIRE c.name IS UNIQUE;

// 二、Graph A: 查询索引
CREATE INDEX skill_category IF NOT EXISTS FOR (s:Skill) ON (s.category);
CREATE INDEX career_family IF NOT EXISTS FOR (c:CareerNode) ON (c.family);
CREATE INDEX career_level IF NOT EXISTS FOR (c:CareerNode) ON (c.level);

// 三、Graph A: 全文索引 (支持 Text-to-Cypher 模糊查询)
CREATE FULLTEXT INDEX skill_fulltext IF NOT EXISTS FOR (s:Skill) ON EACH [s.name, s.aliases];

// 四、Graph B: 简历实体关系网络 - 索引
CREATE INDEX person_student IF NOT EXISTS FOR (p:Person) ON (p.student_id);
CREATE INDEX entity_name_b IF NOT EXISTS FOR (n:Skill) ON (n.name);
