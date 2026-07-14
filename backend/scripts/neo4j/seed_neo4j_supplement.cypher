// Neo4j 补充种子 — 新增Skill节点 + PREREQUISITE边 + REQUIRES边
// 与 seed_skill_supplement.sql 对齐

// ═══ 新增 Skill 节点 ═══
// AI/算法
MERGE (s:Skill {name:'C/C++'}) SET s.cat='AI/算法',s.tier=1,s.heat=0.82;
MERGE (s:Skill {name:'TensorFlow'}) SET s.cat='AI/算法',s.tier=2,s.heat=0.75;
MERGE (s:Skill {name:'Hugging Face'}) SET s.cat='AI/算法',s.tier=2,s.heat=0.88;
MERGE (s:Skill {name:'LoRA/微调'}) SET s.cat='AI/算法',s.tier=2,s.heat=0.82;
MERGE (s:Skill {name:'vLLM'}) SET s.cat='AI/算法',s.tier=3,s.heat=0.72;
MERGE (s:Skill {name:'OpenCV'}) SET s.cat='AI/算法',s.tier=2,s.heat=0.78;
MERGE (s:Skill {name:'MLflow'}) SET s.cat='AI/算法',s.tier=2,s.heat=0.65;
MERGE (s:Skill {name:'数据挖掘'}) SET s.cat='AI/算法',s.tier=2,s.heat=0.72;

// 前端
MERGE (s:Skill {name:'Angular'}) SET s.cat='前端',s.tier=2,s.heat=0.72;
MERGE (s:Skill {name:'Next.js'}) SET s.cat='前端',s.tier=2,s.heat=0.78;
MERGE (s:Skill {name:'Tailwind CSS'}) SET s.cat='前端',s.tier=1,s.heat=0.75;
MERGE (s:Skill {name:'Webpack/Vite'}) SET s.cat='前端',s.tier=2,s.heat=0.7;
MERGE (s:Skill {name:'Node.js'}) SET s.cat='前端/后端',s.tier=1,s.heat=0.82;
MERGE (s:Skill {name:'Flutter/Dart'}) SET s.cat='前端/移动',s.tier=2,s.heat=0.65;
MERGE (s:Skill {name:'Figma'}) SET s.cat='前端/设计',s.tier=1,s.heat=0.72;

// 后端
MERGE (s:Skill {name:'Spring Cloud'}) SET s.cat='后端',s.tier=3,s.heat=0.7;
MERGE (s:Skill {name:'MyBatis'}) SET s.cat='后端',s.tier=2,s.heat=0.78;
MERGE (s:Skill {name:'Django'}) SET s.cat='后端',s.tier=2,s.heat=0.65;
MERGE (s:Skill {name:'FastAPI'}) SET s.cat='后端',s.tier=2,s.heat=0.78;
MERGE (s:Skill {name:'Nginx'}) SET s.cat='后端/运维',s.tier=1,s.heat=0.8;
MERGE (s:Skill {name:'PostgreSQL'}) SET s.cat='后端',s.tier=2,s.heat=0.72;
MERGE (s:Skill {name:'MongoDB'}) SET s.cat='后端',s.tier=2,s.heat=0.65;
MERGE (s:Skill {name:'Elasticsearch'}) SET s.cat='后端/数据',s.tier=2,s.heat=0.7;
MERGE (s:Skill {name:'GraphQL'}) SET s.cat='后端',s.tier=2,s.heat=0.62;
MERGE (s:Skill {name:'gRPC'}) SET s.cat='后端',s.tier=3,s.heat=0.6;
MERGE (s:Skill {name:'Rust'}) SET s.cat='后端/系统',s.tier=2,s.heat=0.68;

// 数据
MERGE (s:Skill {name:'Hadoop'}) SET s.cat='数据',s.tier=2,s.heat=0.6;
MERGE (s:Skill {name:'Flink'}) SET s.cat='数据',s.tier=3,s.heat=0.65;
MERGE (s:Skill {name:'Hive'}) SET s.cat='数据',s.tier=2,s.heat=0.62;
MERGE (s:Skill {name:'Airflow'}) SET s.cat='数据',s.tier=2,s.heat=0.68;
MERGE (s:Skill {name:'Snowflake'}) SET s.cat='数据',s.tier=2,s.heat=0.62;
MERGE (s:Skill {name:'Power BI'}) SET s.cat='数据',s.tier=2,s.heat=0.7;
MERGE (s:Skill {name:'ETL'}) SET s.cat='数据',s.tier=2,s.heat=0.72;

// 运维
MERGE (s:Skill {name:'Terraform'}) SET s.cat='运维',s.tier=2,s.heat=0.72;
MERGE (s:Skill {name:'Ansible'}) SET s.cat='运维',s.tier=2,s.heat=0.68;
MERGE (s:Skill {name:'Prometheus/Grafana'}) SET s.cat='运维',s.tier=2,s.heat=0.7;
MERGE (s:Skill {name:'Jenkins'}) SET s.cat='运维',s.tier=2,s.heat=0.65;

// 安全
MERGE (s:Skill {name:'OWASP'}) SET s.cat='安全',s.tier=2,s.heat=0.62;
MERGE (s:Skill {name:'安全运维'}) SET s.cat='安全',s.tier=2,s.heat=0.6;

// 通用
MERGE (s:Skill {name:'REST API'}) SET s.cat='通用',s.tier=1,s.heat=0.88;
MERGE (s:Skill {name:'设计模式'}) SET s.cat='通用',s.tier=2,s.heat=0.72;
MERGE (s:Skill {name:'敏捷开发'}) SET s.cat='通用',s.tier=1,s.heat=0.7;
MERGE (s:Skill {name:'自动化测试'}) SET s.cat='通用',s.tier=2,s.heat=0.75;

// ═══ 新增 PREREQUISITE 边 ═══
// AI/算法
MATCH (a:Skill {name:'TensorFlow'}), (b:Skill {name:'深度学习'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Hugging Face'}), (b:Skill {name:'深度学习'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'LoRA/微调'}), (b:Skill {name:'Transformer'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'vLLM'}), (b:Skill {name:'深度学习'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'OpenCV'}), (b:Skill {name:'Python'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'MLflow'}), (b:Skill {name:'Python'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'数据挖掘'}), (b:Skill {name:'SQL'}) MERGE (a)-[:PREREQUISITE]->(b);

// 前端
MATCH (a:Skill {name:'Angular'}), (b:Skill {name:'TypeScript'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Next.js'}), (b:Skill {name:'React'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Tailwind CSS'}), (b:Skill {name:'CSS'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Webpack/Vite'}), (b:Skill {name:'Node.js'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Node.js'}), (b:Skill {name:'JavaScript'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Flutter/Dart'}), (b:Skill {name:'JavaScript'}) MERGE (a)-[:PREREQUISITE]->(b);

// 后端
MATCH (a:Skill {name:'Spring Cloud'}), (b:Skill {name:'Spring Boot'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'MyBatis'}), (b:Skill {name:'Java'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Django'}), (b:Skill {name:'Python'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'FastAPI'}), (b:Skill {name:'Python'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Nginx'}), (b:Skill {name:'Linux'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'PostgreSQL'}), (b:Skill {name:'SQL'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'MongoDB'}), (b:Skill {name:'JavaScript'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Elasticsearch'}), (b:Skill {name:'Linux'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'GraphQL'}), (b:Skill {name:'REST API'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'gRPC'}), (b:Skill {name:'网络协议'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Rust'}), (b:Skill {name:'C/C++'}) MERGE (a)-[:PREREQUISITE]->(b);

// 数据
MATCH (a:Skill {name:'Hadoop'}), (b:Skill {name:'Linux'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Flink'}), (b:Skill {name:'Kafka'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Hive'}), (b:Skill {name:'SQL'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Airflow'}), (b:Skill {name:'Python'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Snowflake'}), (b:Skill {name:'SQL'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Power BI'}), (b:Skill {name:'SQL'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'ETL'}), (b:Skill {name:'SQL'}) MERGE (a)-[:PREREQUISITE]->(b);

// 运维
MATCH (a:Skill {name:'Terraform'}), (b:Skill {name:'Linux'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Ansible'}), (b:Skill {name:'Linux'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Prometheus/Grafana'}), (b:Skill {name:'Docker'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Jenkins'}), (b:Skill {name:'Git'}) MERGE (a)-[:PREREQUISITE]->(b);

// 安全
MATCH (a:Skill {name:'OWASP'}), (b:Skill {name:'网络安全'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'安全运维'}), (b:Skill {name:'Docker'}) MERGE (a)-[:PREREQUISITE]->(b);

// ═══ 补充 REQUIRES 边 (新技能→岗位) ═══
MATCH (r:CareerNode {position_name:'大模型算法工程师'}), (s:Skill) WHERE s.name IN ['C/C++','Hugging Face','LoRA/微调','vLLM']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'AI Infra工程师'}), (s:Skill) WHERE s.name IN ['C/C++','Rust','vLLM','Terraform','Nginx','Prometheus/Grafana']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'MLOps工程师'}), (s:Skill) WHERE s.name IN ['MLflow','Airflow','FastAPI','Prometheus/Grafana','Terraform']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'算法工程师'}), (s:Skill) WHERE s.name IN ['C/C++','TensorFlow','数据挖掘']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'NLP工程师'}), (s:Skill) WHERE s.name IN ['Hugging Face','LoRA/微调']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'计算机视觉工程师'}), (s:Skill) WHERE s.name IN ['OpenCV','TensorFlow']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'AI智能体工程师'}), (s:Skill) WHERE s.name IN ['FastAPI','Hugging Face']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'RAG工程师'}), (s:Skill) WHERE s.name IN ['FastAPI','Elasticsearch','Hugging Face','LoRA/微调']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'前端开发'}), (s:Skill) WHERE s.name IN ['Node.js','Tailwind CSS','Webpack/Vite','Figma','Next.js','Angular','自动化测试']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'后端开发'}), (s:Skill) WHERE s.name IN ['Spring Cloud','MyBatis','Nginx','PostgreSQL','Redis','REST API','Node.js','FastAPI','GraphQL','自动化测试']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'全栈开发'}), (s:Skill) WHERE s.name IN ['Node.js','Next.js','FastAPI','PostgreSQL','REST API','Tailwind CSS','Figma']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'测试开发'}), (s:Skill) WHERE s.name IN ['自动化测试','Jenkins','REST API']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'数据分析师'}), (s:Skill) WHERE s.name IN ['Power BI','数据挖掘','ETL','Snowflake','Hive']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'数据工程师'}), (s:Skill) WHERE s.name IN ['Hadoop','Flink','Hive','Airflow','ETL','Elasticsearch','Snowflake']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'DevOps工程师'}), (s:Skill) WHERE s.name IN ['Terraform','Ansible','Prometheus/Grafana','Jenkins','Nginx','安全运维']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'网络安全工程师'}), (s:Skill) WHERE s.name IN ['OWASP','安全运维']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'AI安全工程师'}), (s:Skill) WHERE s.name IN ['OWASP','安全运维']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'具身智能工程师'}), (s:Skill) WHERE s.name IN ['C/C++','OpenCV']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'机器人算法工程师'}), (s:Skill) WHERE s.name IN ['C/C++','OpenCV']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'AI产品经理'}), (s:Skill) WHERE s.name IN ['REST API','敏捷开发','Figma','LoRA/微调']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'产品经理'}), (s:Skill) WHERE s.name IN ['REST API','敏捷开发','Figma']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'UI/UX设计师'}), (s:Skill) WHERE s.name IN ['Figma','Tailwind CSS','敏捷开发']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'移动端开发'}), (s:Skill) WHERE s.name IN ['Flutter/Dart','JavaScript','REST API','Figma']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'提示词工程师'}), (s:Skill) WHERE s.name IN ['Prompt Engineering','REST API','Python']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

// ═══ 补充 TRANSFER 边 (移动端开发) ═══
MATCH (a:CareerNode {position_name:'移动端开发'}), (b:CareerNode {position_name:'前端开发'})
MERGE (a)-[:TRANSFER {jaccard:0.4,difficulty:'中等'}]->(b);
MATCH (a:CareerNode {position_name:'前端开发'}), (b:CareerNode {position_name:'移动端开发'})
MERGE (a)-[:TRANSFER {jaccard:0.35,difficulty:'中等'}]->(b);
