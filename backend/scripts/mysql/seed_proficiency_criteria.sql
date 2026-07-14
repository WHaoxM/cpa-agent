-- 硬技能 proficiency_criteria 补丁 (D2维度)
USE cpa_agent;
SET NAMES utf8mb4;

-- 通用硬技能标准
UPDATE skill_taxonomy SET dimension='D2', proficiency_criteria='{"精通":"能独立设计大型项目架构,精通底层原理,能优化性能瓶颈","熟练":"能独立完成项目开发,熟练使用核心API和工具链","了解":"能写基本代码,看过文档,需参考示例","不会":"没学过或只听过名字"}'
WHERE category IN ('AI/算法','前端','后端','数据','运维','安全','通用') AND dimension='D2';

-- 特殊技能定制标准
UPDATE skill_taxonomy SET proficiency_criteria='{"精通":"能从零训练大模型,理解每个超参数的数学含义","熟练":"能微调开源模型并优化推理性能","了解":"用过HuggingFace推理,不理解训练细节","不会":"只听过GPT,没动手训练过"}'
WHERE skill_name='Transformer';

UPDATE skill_taxonomy SET proficiency_criteria='{"精通":"能设计Agent架构(规划/记忆/工具调用),处理复杂任务链","熟练":"能用LangChain构建RAG+工具调用的Agent","了解":"写过简单Prompt Chain","不会":"只听过Agent概念"}'
WHERE skill_name='LangChain';

UPDATE skill_taxonomy SET proficiency_criteria='{"精通":"能设计RAG架构(索引策略/检索优化/重排序),端到端落地","熟练":"能搭建RAG系统并调优检索质量","了解":"用过向量数据库做简单检索","不会":"只听说过RAG"}'
WHERE skill_name='RAG';

UPDATE skill_taxonomy SET proficiency_criteria='{"精通":"能设计高效Prompt体系,建立Prompt库和评估框架","熟练":"能写出稳定可复用的Prompt模板","了解":"用过ChatGPT写Prompt","不会":"只和ChatGPT聊过天"}'
WHERE skill_name='Prompt Engineering';

UPDATE skill_taxonomy SET proficiency_criteria='{"精通":"能从零实现ML算法,理解数学推导,能做特征工程和模型选型","熟练":"能用sklearn/PyTorch完成建模和调参","了解":"用过现成模型做预测","不会":"只听过机器学习概念"}'
WHERE skill_name='机器学习';

UPDATE skill_taxonomy SET proficiency_criteria='{"精通":"能设计新网络架构,理解反向传播/优化器数学原理","熟练":"能用PyTorch实现和训练常用模型","了解":"跑过notebook示例","不会":"只听说过深度学习"}'
WHERE skill_name='深度学习';

UPDATE skill_taxonomy SET proficiency_criteria='{"精通":"能写CUDA kernel优化算子,理解GPU内存层次","熟练":"能用CUDA加速模型推理","了解":"知道GPU编程概念","不会":"只听过CUDA"}'
WHERE skill_name='CUDA';

UPDATE skill_taxonomy SET proficiency_criteria='{"精通":"能设计分布式训练方案(数据/模型/流水线并行)","熟练":"能跑通单机多卡训练","了解":"知道分布式训练概念","不会":"不了解"}'
WHERE skill_name='CUDA' OR skill_name='Kubernetes' AND 1=0;
-- K8s单独标准
UPDATE skill_taxonomy SET proficiency_criteria='{"精通":"能设计K8s集群架构,自定义Operator,处理生产级故障","熟练":"能部署应用,写Helm Chart,配置网络/存储","了解":"用过kubectl部署简单服务","不会":"只听过K8s"}'
WHERE skill_name='Kubernetes';

UPDATE skill_taxonomy SET proficiency_criteria='{"精通":"能设计强化学习算法(新奖励函数/策略优化),在复杂环境中落地","熟练":"能实现PPO/SAC等主流算法并调参","了解":"跑过gym示例","不会":"只听说过强化学习"}'
WHERE skill_name='强化学习';

UPDATE skill_taxonomy SET proficiency_criteria='{"精通":"能设计SLAM系统(视觉/激光),处理大规模场景","熟练":"能用ORB-SLAM/LOAM跑通并调参","了解":"理解SLAM基本概念","不会":"不了解"}'
WHERE skill_name='SLAM';
