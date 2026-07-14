// Neo4j 冷启动种子 — 关系边 (REQUIRES / PREREQUISITE / PROMOTION / TRANSFER)
// 边类型对齐 graph_storage.py: PROMOTION=晋升, TRANSFER=换岗

// ── REQUIRES (岗位→必备技能) ──
MATCH (r:CareerNode {position_name:'大模型算法工程师'}), (s:Skill) WHERE s.name IN ['Python','PyTorch','Transformer','CUDA','深度学习']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'算法工程师'}), (s:Skill) WHERE s.name IN ['Python','机器学习','数据结构','统计学']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'多模态算法工程师'}), (s:Skill) WHERE s.name IN ['Python','PyTorch','深度学习','计算机视觉','NLP']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'强化学习工程师'}), (s:Skill) WHERE s.name IN ['Python','深度学习','强化学习']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'NLP工程师'}), (s:Skill) WHERE s.name IN ['Python','NLP','Transformer','深度学习']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'计算机视觉工程师'}), (s:Skill) WHERE s.name IN ['Python','计算机视觉','PyTorch','深度学习']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'AI智能体工程师'}), (s:Skill) WHERE s.name IN ['Python','LangChain','RAG','Prompt Engineering']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'MLOps工程师'}), (s:Skill) WHERE s.name IN ['Python','Docker','Kubernetes','CI/CD']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'RAG工程师'}), (s:Skill) WHERE s.name IN ['Python','RAG','向量数据库','LangChain']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'前端开发'}), (s:Skill) WHERE s.name IN ['HTML','CSS','JavaScript','Vue.js','TypeScript']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'后端开发'}), (s:Skill) WHERE s.name IN ['Java','MySQL','Redis','SQL']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'全栈开发'}), (s:Skill) WHERE s.name IN ['JavaScript','React','MySQL','Python']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'测试开发'}), (s:Skill) WHERE s.name IN ['Python','CI/CD']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'数据分析师'}), (s:Skill) WHERE s.name IN ['SQL','Python','统计学']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'数据工程师'}), (s:Skill) WHERE s.name IN ['Python','SQL','Spark']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'DevOps工程师'}), (s:Skill) WHERE s.name IN ['Linux','Docker','Kubernetes','CI/CD']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'AI Infra工程师'}), (s:Skill) WHERE s.name IN ['CUDA','Kubernetes','Linux','深度学习']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'网络安全工程师'}), (s:Skill) WHERE s.name IN ['Linux','网络安全']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'AI安全工程师'}), (s:Skill) WHERE s.name IN ['Python','网络安全','深度学习']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'具身智能工程师'}), (s:Skill) WHERE s.name IN ['Python','强化学习','ROS']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

MATCH (r:CareerNode {position_name:'机器人算法工程师'}), (s:Skill) WHERE s.name IN ['ROS','SLAM','Linux']
MERGE (r)-[:REQUIRES {importance:'required'}]->(s);

// ── PREREQUISITE (技能→前置技能) ──
MATCH (a:Skill {name:'CSS'}), (b:Skill {name:'HTML'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'JavaScript'}), (b:Skill {name:'HTML'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'JavaScript'}), (b:Skill {name:'CSS'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'TypeScript'}), (b:Skill {name:'JavaScript'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Vue.js'}), (b:Skill {name:'JavaScript'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'React'}), (b:Skill {name:'JavaScript'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'机器学习'}), (b:Skill {name:'Python'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'深度学习'}), (b:Skill {name:'机器学习'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'PyTorch'}), (b:Skill {name:'深度学习'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Transformer'}), (b:Skill {name:'深度学习'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Transformer'}), (b:Skill {name:'PyTorch'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'强化学习'}), (b:Skill {name:'深度学习'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'CUDA'}), (b:Skill {name:'深度学习'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'LangChain'}), (b:Skill {name:'Python'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'RAG'}), (b:Skill {name:'LangChain'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'RAG'}), (b:Skill {name:'向量数据库'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'NLP'}), (b:Skill {name:'深度学习'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'计算机视觉'}), (b:Skill {name:'深度学习'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'SLAM'}), (b:Skill {name:'计算机视觉'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'SLAM'}), (b:Skill {name:'ROS'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Kubernetes'}), (b:Skill {name:'Docker'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Docker'}), (b:Skill {name:'Linux'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Redis'}), (b:Skill {name:'MySQL'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Spring Boot'}), (b:Skill {name:'Java'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'Spark'}), (b:Skill {name:'SQL'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'渗透测试'}), (b:Skill {name:'网络安全'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'CI/CD'}), (b:Skill {name:'Docker'}) MERGE (a)-[:PREREQUISITE]->(b);
MATCH (a:Skill {name:'CI/CD'}), (b:Skill {name:'Git'}) MERGE (a)-[:PREREQUISITE]->(b);

// ── PROMOTION (晋升路径, R-06: ≥3链) ──
MATCH (a:CareerNode {position_name:'前端开发'}), (b:CareerNode {position_name:'全栈开发'})
  MERGE (a)-[:PROMOTION {type:'晋升',years:'2-3'}]->(b);
MATCH (a:CareerNode {position_name:'后端开发'}), (b:CareerNode {position_name:'全栈开发'})
  MERGE (a)-[:PROMOTION {type:'晋升',years:'2-3'}]->(b);
MATCH (a:CareerNode {position_name:'算法工程师'}), (b:CareerNode {position_name:'大模型算法工程师'})
  MERGE (a)-[:PROMOTION {type:'晋升',years:'1-2'}]->(b);
MATCH (a:CareerNode {position_name:'NLP工程师'}), (b:CareerNode {position_name:'大模型算法工程师'})
  MERGE (a)-[:PROMOTION {type:'晋升',years:'1-2'}]->(b);
MATCH (a:CareerNode {position_name:'计算机视觉工程师'}), (b:CareerNode {position_name:'多模态算法工程师'})
  MERGE (a)-[:PROMOTION {type:'晋升',years:'1-2'}]->(b);
MATCH (a:CareerNode {position_name:'AI智能体工程师'}), (b:CareerNode {position_name:'MLOps工程师'})
  MERGE (a)-[:PROMOTION {type:'晋升',years:'2-3'}]->(b);
MATCH (a:CareerNode {position_name:'DevOps工程师'}), (b:CareerNode {position_name:'AI Infra工程师'})
  MERGE (a)-[:PROMOTION {type:'晋升',years:'2-3'}]->(b);
MATCH (a:CareerNode {position_name:'数据分析师'}), (b:CareerNode {position_name:'数据工程师'})
  MERGE (a)-[:PROMOTION {type:'晋升',years:'1-2'}]->(b);
MATCH (a:CareerNode {position_name:'网络安全工程师'}), (b:CareerNode {position_name:'AI安全工程师'})
  MERGE (a)-[:PROMOTION {type:'晋升',years:'1-2'}]->(b);
MATCH (a:CareerNode {position_name:'产品经理'}), (b:CareerNode {position_name:'AI产品经理'})
  MERGE (a)-[:PROMOTION {type:'晋升',years:'1-2'}]->(b);
MATCH (a:CareerNode {position_name:'强化学习工程师'}), (b:CareerNode {position_name:'具身智能工程师'})
  MERGE (a)-[:PROMOTION {type:'晋升',years:'1-2'}]->(b);
MATCH (a:CareerNode {position_name:'机器人算法工程师'}), (b:CareerNode {position_name:'具身智能工程师'})
  MERGE (a)-[:PROMOTION {type:'晋升',years:'2-3'}]->(b);

// ── TRANSFER (横向换岗, R-07: ≥5岗位×≥2路径) ──
MATCH (a:CareerNode {position_name:'前端开发'}), (b:CareerNode {position_name:'后端开发'})
  MERGE (a)-[:TRANSFER {jaccard:0.3,difficulty:'中等'}]->(b);
MATCH (a:CareerNode {position_name:'后端开发'}), (b:CareerNode {position_name:'前端开发'})
  MERGE (a)-[:TRANSFER {jaccard:0.3,difficulty:'中等'}]->(b);
MATCH (a:CareerNode {position_name:'后端开发'}), (b:CareerNode {position_name:'DevOps工程师'})
  MERGE (a)-[:TRANSFER {jaccard:0.4,difficulty:'容易'}]->(b);
MATCH (a:CareerNode {position_name:'数据分析师'}), (b:CareerNode {position_name:'数据工程师'})
  MERGE (a)-[:TRANSFER {jaccard:0.5,difficulty:'容易'}]->(b);
MATCH (a:CareerNode {position_name:'算法工程师'}), (b:CareerNode {position_name:'数据分析师'})
  MERGE (a)-[:TRANSFER {jaccard:0.4,difficulty:'容易'}]->(b);
MATCH (a:CareerNode {position_name:'算法工程师'}), (b:CareerNode {position_name:'AI智能体工程师'})
  MERGE (a)-[:TRANSFER {jaccard:0.35,difficulty:'中等'}]->(b);
MATCH (a:CareerNode {position_name:'测试开发'}), (b:CareerNode {position_name:'后端开发'})
  MERGE (a)-[:TRANSFER {jaccard:0.4,difficulty:'容易'}]->(b);
MATCH (a:CareerNode {position_name:'提示词工程师'}), (b:CareerNode {position_name:'AI智能体工程师'})
  MERGE (a)-[:TRANSFER {jaccard:0.5,difficulty:'容易'}]->(b);
MATCH (a:CareerNode {position_name:'RAG工程师'}), (b:CareerNode {position_name:'AI智能体工程师'})
  MERGE (a)-[:TRANSFER {jaccard:0.6,difficulty:'容易'}]->(b);
MATCH (a:CareerNode {position_name:'网络安全工程师'}), (b:CareerNode {position_name:'AI安全工程师'})
  MERGE (a)-[:TRANSFER {jaccard:0.4,difficulty:'中等'}]->(b);
MATCH (a:CareerNode {position_name:'产品经理'}), (b:CareerNode {position_name:'AI产品经理'})
  MERGE (a)-[:TRANSFER {jaccard:0.5,difficulty:'容易'}]->(b);

// ── 补充 TRANSFER 边 (满足 R-07: ≥5岗位×≥2换岗路径) ──
// 前端→全栈 (技能重叠高)
MATCH (a:CareerNode {position_name:'前端开发'}), (b:CareerNode {position_name:'全栈开发'})
  MERGE (a)-[:TRANSFER {jaccard:0.6,difficulty:'容易'}]->(b);
// 前端→测试 (JS/自动化重叠)
MATCH (a:CareerNode {position_name:'前端开发'}), (b:CareerNode {position_name:'测试开发'})
  MERGE (a)-[:TRANSFER {jaccard:0.35,difficulty:'中等'}]->(b);
// 测试→DevOps (CI/CD/Docker重叠)
MATCH (a:CareerNode {position_name:'测试开发'}), (b:CareerNode {position_name:'DevOps工程师'})
  MERGE (a)-[:TRANSFER {jaccard:0.4,difficulty:'容易'}]->(b);
// 数据分析→算法 (Python/统计学重叠)
MATCH (a:CareerNode {position_name:'数据分析师'}), (b:CareerNode {position_name:'算法工程师'})
  MERGE (a)-[:TRANSFER {jaccard:0.4,difficulty:'中等'}]->(b);
// 提示词→AI产品 (Prompt/需求分析重叠)
MATCH (a:CareerNode {position_name:'提示词工程师'}), (b:CareerNode {position_name:'AI产品经理'})
  MERGE (a)-[:TRANSFER {jaccard:0.45,difficulty:'容易'}]->(b);
// NLP→大模型算法 (Transformer/深度学习重叠)
MATCH (a:CareerNode {position_name:'NLP工程师'}), (b:CareerNode {position_name:'大模型算法工程师'})
  MERGE (a)-[:TRANSFER {jaccard:0.55,difficulty:'容易'}]->(b);
// CV→多模态 (深度学习/PyTorch重叠)
MATCH (a:CareerNode {position_name:'计算机视觉工程师'}), (b:CareerNode {position_name:'多模态算法工程师'})
  MERGE (a)-[:TRANSFER {jaccard:0.55,difficulty:'容易'}]->(b);
// DevOps→MLOps (Docker/K8s/CI/CD重叠)
MATCH (a:CareerNode {position_name:'DevOps工程师'}), (b:CareerNode {position_name:'MLOps工程师'})
  MERGE (a)-[:TRANSFER {jaccard:0.6,difficulty:'容易'}]->(b);
// RAG→MLOps (向量库/部署重叠)
MATCH (a:CareerNode {position_name:'RAG工程师'}), (b:CareerNode {position_name:'MLOps工程师'})
  MERGE (a)-[:TRANSFER {jaccard:0.35,difficulty:'中等'}]->(b);
// 数据工程→后端 (SQL/Python/中间件重叠)
MATCH (a:CareerNode {position_name:'数据工程师'}), (b:CareerNode {position_name:'后端开发'})
  MERGE (a)-[:TRANSFER {jaccard:0.4,difficulty:'中等'}]->(b);
// 全栈→前端 (反向)
MATCH (a:CareerNode {position_name:'全栈开发'}), (b:CareerNode {position_name:'前端开发'})
  MERGE (a)-[:TRANSFER {jaccard:0.6,difficulty:'容易'}]->(b);
// 全栈→后端 (反向)
MATCH (a:CareerNode {position_name:'全栈开发'}), (b:CareerNode {position_name:'后端开发'})
  MERGE (a)-[:TRANSFER {jaccard:0.5,difficulty:'容易'}]->(b);
// AI安全→网络安全 (反向)
MATCH (a:CareerNode {position_name:'AI安全工程师'}), (b:CareerNode {position_name:'网络安全工程师'})
  MERGE (a)-[:TRANSFER {jaccard:0.4,difficulty:'中等'}]->(b);
// AI产品→产品 (反向)
MATCH (a:CareerNode {position_name:'AI产品经理'}), (b:CareerNode {position_name:'产品经理'})
  MERGE (a)-[:TRANSFER {jaccard:0.5,difficulty:'容易'}]->(b);
// MLOps→DevOps (反向)
MATCH (a:CareerNode {position_name:'MLOps工程师'}), (b:CareerNode {position_name:'DevOps工程师'})
  MERGE (a)-[:TRANSFER {jaccard:0.6,difficulty:'容易'}]->(b);
