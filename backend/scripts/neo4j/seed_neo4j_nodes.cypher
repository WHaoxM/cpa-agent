// Neo4j 冷启动种子 — CareerNode + Skill 节点
// 执行: 逐条运行或通过 neo4j-cli 导入

// CareerNode 节点
MERGE (r:CareerNode {position_name:'大模型算法工程师'}) SET r.category='AI算法',r.sub='大模型',r.sal_lo=25,r.sal_hi=50,r.demand='极高',r.trend='上升';
MERGE (r:CareerNode {position_name:'算法工程师'}) SET r.category='AI算法',r.sub='传统算法',r.sal_lo=15,r.sal_hi=35,r.demand='极高',r.trend='上升';
MERGE (r:CareerNode {position_name:'多模态算法工程师'}) SET r.category='AI算法',r.sub='多模态',r.sal_lo=25,r.sal_hi=45,r.demand='高',r.trend='上升';
MERGE (r:CareerNode {position_name:'强化学习工程师'}) SET r.category='AI算法',r.sub='强化学习',r.sal_lo=20,r.sal_hi=40,r.demand='中',r.trend='上升';
MERGE (r:CareerNode {position_name:'NLP工程师'}) SET r.category='AI算法',r.sub='NLP',r.sal_lo=15,r.sal_hi=30,r.demand='高',r.trend='上升';
MERGE (r:CareerNode {position_name:'计算机视觉工程师'}) SET r.category='AI算法',r.sub='CV',r.sal_lo=15,r.sal_hi=30,r.demand='高',r.trend='上升';
MERGE (r:CareerNode {position_name:'AI智能体工程师'}) SET r.category='AI工程',r.sub='Agent',r.sal_lo=15,r.sal_hi=30,r.demand='极高',r.trend='上升';
MERGE (r:CareerNode {position_name:'MLOps工程师'}) SET r.category='AI工程',r.sub='MLOps',r.sal_lo=20,r.sal_hi=40,r.demand='高',r.trend='上升';
MERGE (r:CareerNode {position_name:'提示词工程师'}) SET r.category='AI工程',r.sub='Prompt',r.sal_lo=10,r.sal_hi=25,r.demand='中',r.trend='上升';
MERGE (r:CareerNode {position_name:'RAG工程师'}) SET r.category='AI工程',r.sub='RAG',r.sal_lo=15,r.sal_hi=30,r.demand='高',r.trend='上升';
MERGE (r:CareerNode {position_name:'前端开发'}) SET r.category='开发',r.sub='前端',r.sal_lo=8,r.sal_hi=18,r.demand='高',r.trend='上升';
MERGE (r:CareerNode {position_name:'后端开发'}) SET r.category='开发',r.sub='后端',r.sal_lo=10,r.sal_hi=22,r.demand='极高',r.trend='上升';
MERGE (r:CareerNode {position_name:'全栈开发'}) SET r.category='开发',r.sub='全栈',r.sal_lo=12,r.sal_hi=25,r.demand='高',r.trend='上升';
MERGE (r:CareerNode {position_name:'测试开发'}) SET r.category='开发',r.sub='测试',r.sal_lo=8,r.sal_hi=18,r.demand='中',r.trend='平稳';
MERGE (r:CareerNode {position_name:'数据分析师'}) SET r.category='数据',r.sub='分析',r.sal_lo=8,r.sal_hi=20,r.demand='高',r.trend='上升';
MERGE (r:CareerNode {position_name:'数据工程师'}) SET r.category='数据',r.sub='工程',r.sal_lo=12,r.sal_hi=25,r.demand='高',r.trend='上升';
MERGE (r:CareerNode {position_name:'DevOps工程师'}) SET r.category='运维',r.sub='DevOps',r.sal_lo=12,r.sal_hi=28,r.demand='高',r.trend='上升';
MERGE (r:CareerNode {position_name:'AI Infra工程师'}) SET r.category='运维',r.sub='AI基础设施',r.sal_lo=25,r.sal_hi=50,r.demand='极高',r.trend='上升';
MERGE (r:CareerNode {position_name:'网络安全工程师'}) SET r.category='安全',r.sub='攻防',r.sal_lo=10,r.sal_hi=25,r.demand='高',r.trend='上升';
MERGE (r:CareerNode {position_name:'AI安全工程师'}) SET r.category='安全',r.sub='AI安全',r.sal_lo=15,r.sal_hi=30,r.demand='高',r.trend='上升';
MERGE (r:CareerNode {position_name:'AI产品经理'}) SET r.category='产品',r.sub='AI产品',r.sal_lo=15,r.sal_hi=30,r.demand='极高',r.trend='上升';
MERGE (r:CareerNode {position_name:'产品经理'}) SET r.category='产品',r.sub='互联网产品',r.sal_lo=10,r.sal_hi=25,r.demand='高',r.trend='平稳';
MERGE (r:CareerNode {position_name:'具身智能工程师'}) SET r.category='嵌入式',r.sub='具身智能',r.sal_lo=20,r.sal_hi=40,r.demand='极高',r.trend='上升';
MERGE (r:CareerNode {position_name:'机器人算法工程师'}) SET r.category='嵌入式',r.sub='机器人',r.sal_lo=15,r.sal_hi=30,r.demand='高',r.trend='上升';
MERGE (r:CareerNode {position_name:'UI/UX设计师'}) SET r.category='设计',r.sub='交互',r.sal_lo=8,r.sal_hi=20,r.demand='中',r.trend='平稳';
MERGE (r:CareerNode {position_name:'移动端开发'}) SET r.category='开发',r.sub='移动端',r.sal_lo=8,r.sal_hi=20,r.demand='中',r.trend='平稳';

// Skill 节点
MERGE (s:Skill {name:'Python'}) SET s.cat='AI/算法',s.tier=1,s.heat=0.95;
MERGE (s:Skill {name:'机器学习'}) SET s.cat='AI/算法',s.tier=2,s.heat=0.9;
MERGE (s:Skill {name:'深度学习'}) SET s.cat='AI/算法',s.tier=2,s.heat=0.92;
MERGE (s:Skill {name:'PyTorch'}) SET s.cat='AI/算法',s.tier=2,s.heat=0.88;
MERGE (s:Skill {name:'Transformer'}) SET s.cat='AI/算法',s.tier=3,s.heat=0.95;
MERGE (s:Skill {name:'强化学习'}) SET s.cat='AI/算法',s.tier=3,s.heat=0.78;
MERGE (s:Skill {name:'CUDA'}) SET s.cat='AI/算法',s.tier=3,s.heat=0.82;
MERGE (s:Skill {name:'LangChain'}) SET s.cat='AI/算法',s.tier=2,s.heat=0.85;
MERGE (s:Skill {name:'RAG'}) SET s.cat='AI/算法',s.tier=2,s.heat=0.88;
MERGE (s:Skill {name:'Prompt Engineering'}) SET s.cat='AI/算法',s.tier=1,s.heat=0.9;
MERGE (s:Skill {name:'向量数据库'}) SET s.cat='AI/算法',s.tier=2,s.heat=0.8;
MERGE (s:Skill {name:'NLP'}) SET s.cat='AI/算法',s.tier=2,s.heat=0.85;
MERGE (s:Skill {name:'计算机视觉'}) SET s.cat='AI/算法',s.tier=2,s.heat=0.82;
MERGE (s:Skill {name:'ROS'}) SET s.cat='AI/算法',s.tier=2,s.heat=0.7;
MERGE (s:Skill {name:'SLAM'}) SET s.cat='AI/算法',s.tier=3,s.heat=0.72;
MERGE (s:Skill {name:'HTML'}) SET s.cat='前端',s.tier=1,s.heat=0.9;
MERGE (s:Skill {name:'CSS'}) SET s.cat='前端',s.tier=1,s.heat=0.85;
MERGE (s:Skill {name:'JavaScript'}) SET s.cat='前端',s.tier=1,s.heat=0.95;
MERGE (s:Skill {name:'TypeScript'}) SET s.cat='前端',s.tier=2,s.heat=0.88;
MERGE (s:Skill {name:'Vue.js'}) SET s.cat='前端',s.tier=2,s.heat=0.82;
MERGE (s:Skill {name:'React'}) SET s.cat='前端',s.tier=2,s.heat=0.9;
MERGE (s:Skill {name:'Java'}) SET s.cat='后端',s.tier=1,s.heat=0.85;
MERGE (s:Skill {name:'Go'}) SET s.cat='后端',s.tier=2,s.heat=0.78;
MERGE (s:Skill {name:'MySQL'}) SET s.cat='后端',s.tier=1,s.heat=0.92;
MERGE (s:Skill {name:'Redis'}) SET s.cat='后端',s.tier=2,s.heat=0.75;
MERGE (s:Skill {name:'Spring Boot'}) SET s.cat='后端',s.tier=2,s.heat=0.8;
MERGE (s:Skill {name:'SQL'}) SET s.cat='数据',s.tier=1,s.heat=0.95;
MERGE (s:Skill {name:'统计学'}) SET s.cat='数据',s.tier=1,s.heat=0.75;
MERGE (s:Skill {name:'Spark'}) SET s.cat='数据',s.tier=2,s.heat=0.78;
MERGE (s:Skill {name:'Linux'}) SET s.cat='运维',s.tier=1,s.heat=0.9;
MERGE (s:Skill {name:'Docker'}) SET s.cat='运维',s.tier=2,s.heat=0.85;
MERGE (s:Skill {name:'Kubernetes'}) SET s.cat='运维',s.tier=3,s.heat=0.78;
MERGE (s:Skill {name:'CI/CD'}) SET s.cat='运维',s.tier=2,s.heat=0.8;
MERGE (s:Skill {name:'网络安全'}) SET s.cat='安全',s.tier=2,s.heat=0.75;
MERGE (s:Skill {name:'渗透测试'}) SET s.cat='安全',s.tier=3,s.heat=0.7;
MERGE (s:Skill {name:'Git'}) SET s.cat='通用',s.tier=1,s.heat=0.95;
MERGE (s:Skill {name:'数据结构'}) SET s.cat='通用',s.tier=1,s.heat=0.9;
