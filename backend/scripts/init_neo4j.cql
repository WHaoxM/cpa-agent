// ==========================================
// CPA-Agent Neo4j 图谱初始化
// ==========================================

CREATE CONSTRAINT skill_name IF NOT EXISTS FOR (s:Skill) REQUIRE s.name IS UNIQUE;
CREATE CONSTRAINT portrait_id IF NOT EXISTS FOR (j:JobPortrait) REQUIRE j.portrait_id IS UNIQUE;
CREATE CONSTRAINT category_name IF NOT EXISTS FOR (c:SkillCategory) REQUIRE c.name IS UNIQUE;

CREATE INDEX skill_category IF NOT EXISTS FOR (s:Skill) ON (s.category);
CREATE INDEX career_family IF NOT EXISTS FOR (c:CareerNode) ON (c.family);
CREATE INDEX career_level IF NOT EXISTS FOR (c:CareerNode) ON (c.level);

CREATE FULLTEXT INDEX skill_fulltext IF NOT EXISTS FOR (s:Skill) ON EACH [s.name, s.aliases];

CREATE (:SkillCategory {name: '前端', level: 'major', parent_name: NULL, skill_count: 0});
CREATE (:SkillCategory {name: '后端', level: 'major', parent_name: NULL, skill_count: 0});
CREATE (:SkillCategory {name: '移动端', level: 'major', parent_name: NULL, skill_count: 0});
CREATE (:SkillCategory {name: '算法', level: 'major', parent_name: NULL, skill_count: 0});
CREATE (:SkillCategory {name: '运维', level: 'major', parent_name: NULL, skill_count: 0});
CREATE (:SkillCategory {name: '通用', level: 'major', parent_name: NULL, skill_count: 0});

CREATE (:Skill {name: 'HTML/CSS', aliases: ['HTML5', 'CSS3', '样式表'], category: '前端', sub_category: '基础', frequency: 100, proficiency_levels: ['入门', '熟悉', '掌握', '精通'], tier: 'foundation', heat: 95, created_at: datetime()});
CREATE (:Skill {name: 'JavaScript', aliases: ['JS', 'ECMAScript'], category: '前端', sub_category: '基础', frequency: 100, proficiency_levels: ['入门', '熟悉', '掌握', '精通'], tier: 'foundation', heat: 98, created_at: datetime()});
CREATE (:Skill {name: 'Vue 3', aliases: ['Vue', 'Vue.js'], category: '前端', sub_category: '框架', frequency: 85, proficiency_levels: ['入门', '熟悉', '掌握', '精通'], tier: 'junior', heat: 90, created_at: datetime()});
CREATE (:Skill {name: 'React', aliases: ['React.js', 'ReactJS'], category: '前端', sub_category: '框架', frequency: 90, proficiency_levels: ['入门', '熟悉', '掌握', '精通'], tier: 'junior', heat: 92, created_at: datetime()});
CREATE (:Skill {name: 'Python', aliases: ['Python3'], category: '后端', sub_category: '语言', frequency: 100, proficiency_levels: ['入门', '熟悉', '掌握', '精通'], tier: 'foundation', heat: 96, created_at: datetime()});
CREATE (:Skill {name: 'Java', aliases: ['Java SE', 'Java EE'], category: '后端', sub_category: '语言', frequency: 95, proficiency_levels: ['入门', '熟悉', '掌握', '精通'], tier: 'foundation', heat: 94, created_at: datetime()});
CREATE (:Skill {name: 'Spring Boot', aliases: ['Spring'], category: '后端', sub_category: '框架', frequency: 88, proficiency_levels: ['入门', '熟悉', '掌握', '精通'], tier: 'mid', heat: 88, created_at: datetime()});
CREATE (:Skill {name: 'MySQL', aliases: ['MySQL数据库'], category: '后端', sub_category: '数据库', frequency: 92, proficiency_levels: ['入门', '熟悉', '掌握', '精通'], tier: 'foundation', heat: 90, created_at: datetime()});
CREATE (:Skill {name: 'Redis', aliases: ['Redis缓存'], category: '后端', sub_category: '数据库', frequency: 85, proficiency_levels: ['入门', '熟悉', '掌握', '精通'], tier: 'mid', heat: 85, created_at: datetime()});
CREATE (:Skill {name: '数据结构', aliases: ['算法与数据结构'], category: '算法', sub_category: '基础', frequency: 100, proficiency_levels: ['入门', '熟悉', '掌握', '精通'], tier: 'foundation', heat: 97, created_at: datetime()});
CREATE (:Skill {name: '机器学习', aliases: ['ML', 'Machine Learning'], category: '算法', sub_category: 'AI', frequency: 80, proficiency_levels: ['入门', '熟悉', '掌握', '精通'], tier: 'mid', heat: 88, created_at: datetime()});

MATCH (js:Skill {name: 'JavaScript'}), (vue:Skill {name: 'Vue 3'}) CREATE (vue)-[:PREREQUISITE {confidence: 0.9}]->(js);
MATCH (js:Skill {name: 'JavaScript'}), (react:Skill {name: 'React'}) CREATE (react)-[:PREREQUISITE {confidence: 0.9}]->(js);
MATCH (ds:Skill {name: '数据结构'}), (ml:Skill {name: '机器学习'}) CREATE (ml)-[:PREREQUISITE {confidence: 0.85}]->(ds);
MATCH (py:Skill {name: 'Python'}), (ml:Skill {name: '机器学习'}) CREATE (ml)-[:PREREQUISITE {confidence: 0.9}]->(py);

CREATE (:JobPortrait {portrait_id: 'portrait_frontend_001', position_name: '前端开发工程师', jd_count: 150, required_degree: '本科', d1_weight: 0.25, d2_weight: 0.35, d3_weight: 0.20, d4_weight: 0.20, created_at: datetime()});
CREATE (:JobPortrait {portrait_id: 'portrait_backend_001', position_name: '后端开发工程师', jd_count: 200, required_degree: '本科', d1_weight: 0.25, d2_weight: 0.35, d3_weight: 0.20, d4_weight: 0.20, created_at: datetime()});
CREATE (:JobPortrait {portrait_id: 'portrait_algorithm_001', position_name: '算法工程师', jd_count: 100, required_degree: '硕士', d1_weight: 0.30, d2_weight: 0.30, d3_weight: 0.20, d4_weight: 0.20, created_at: datetime()});

MATCH (j:JobPortrait {portrait_id: 'portrait_frontend_001'}), (js:Skill {name: 'JavaScript'}) CREATE (j)-[:REQUIRES {level: '必须', tf_score: 0.95}]->(js);
MATCH (j:JobPortrait {portrait_id: 'portrait_frontend_001'}), (vue:Skill {name: 'Vue 3'}) CREATE (j)-[:REQUIRES {level: '必须', tf_score: 0.85}]->(vue);
MATCH (j:JobPortrait {portrait_id: 'portrait_frontend_001'}), (html:Skill {name: 'HTML/CSS'}) CREATE (j)-[:REQUIRES {level: '必须', tf_score: 0.90}]->(html);
MATCH (j:JobPortrait {portrait_id: 'portrait_backend_001'}), (py:Skill {name: 'Python'}) CREATE (j)-[:REQUIRES {level: '必须', tf_score: 0.90}]->(py);
MATCH (j:JobPortrait {portrait_id: 'portrait_backend_001'}), (mysql:Skill {name: 'MySQL'}) CREATE (j)-[:REQUIRES {level: '必须', tf_score: 0.85}]->(mysql);
MATCH (j:JobPortrait {portrait_id: 'portrait_backend_001'}), (redis:Skill {name: 'Redis'}) CREATE (j)-[:PREFERS {level: '加分', tf_score: 0.70}]->(redis);

CREATE (:CareerNode {position_name: '初级前端工程师', level: 'junior', family: '前端开发', created_at: datetime()});
CREATE (:CareerNode {position_name: '中级前端工程师', level: 'mid', family: '前端开发', created_at: datetime()});
CREATE (:CareerNode {position_name: '高级前端工程师', level: 'senior', family: '前端开发', created_at: datetime()});
CREATE (:CareerNode {position_name: '前端技术专家', level: 'lead', family: '前端开发', created_at: datetime()});
CREATE (:CareerNode {position_name: '前端架构师', level: 'director', family: '前端开发', created_at: datetime()});

MATCH (junior:CareerNode {position_name: '初级前端工程师'}), (mid:CareerNode {position_name: '中级前端工程师'}) CREATE (mid)-[:PROMOTION {years: 2, skill_delta: 'Vue/React + 工程化能力'}]->(junior);
MATCH (mid:CareerNode {position_name: '中级前端工程师'}), (senior:CareerNode {position_name: '高级前端工程师'}) CREATE (senior)-[:PROMOTION {years: 3, skill_delta: '系统设计 + 前端架构'}]->(mid);
MATCH (senior:CareerNode {position_name: '高级前端工程师'}), (expert:CareerNode {position_name: '前端技术专家'}) CREATE (expert)-[:PROMOTION {years: 3, skill_delta: '技术深度 + 团队管理'}]->(senior);
MATCH (expert:CareerNode {position_name: '前端技术专家'}), (arch:CareerNode {position_name: '前端架构师'}) CREATE (arch)-[:PROMOTION {years: 4, skill_delta: '架构能力 + 业务洞察'}]->(expert);

MATCH (fe:CareerNode {position_name: '中级前端工程师'}), (be:CareerNode {position_name: '中级后端工程师'}) CREATE (fe)-[:TRANSFER {jaccard: 0.3, affinity: 0.4, skill_overlap: 'JavaScript → Node.js'}]->(be);

MATCH (s:Skill {category: '前端'}), (c:SkillCategory {name: '前端'}) CREATE (s)-[:BELONGS_TO]->(c);
MATCH (s:Skill {category: '后端'}), (c:SkillCategory {name: '后端'}) CREATE (s)-[:BELONGS_TO]->(c);
MATCH (s:Skill {category: '算法'}), (c:SkillCategory {name: '算法'}) CREATE (s)-[:BELONGS_TO]->(c);

RETURN 'Neo4j 图谱初始化完成' AS status;
