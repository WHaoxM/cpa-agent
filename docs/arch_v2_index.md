# CPA-Agent v3.0 Architecture Spec (Autonomous Agentic System)

> 生成时间: 2026-04-04
> 基于: 12篇论文 + 6个开源项目 + 4轮多轮对话 + 专家深度评审
> v2.1 patch: 修复3项工程隐患 (静态老化 / 长尾延迟 / 冷启动数据荒)
> v3.0 升维: 自进化智能体 (自组织知识网络 / Actor-Critic审查 / 非对称转移算法)

## 架构总览

```
Layer 0: 数据摄入与解析 → student_profile.json / job_postings.json
Layer 1: 知识构建       → skill_taxonomy / job_portraits / career_graph
  ├─ v2.1: OOV Discovery Pipeline + Industry Common-sense Patch
  └─ v3.0: Cron-Driven Autonomous Ontology + Asymmetric Transition
Layer 2: 画像生成       → student_portrait(D1-D4) / job_portrait(D1-D4)
Layer 3: 匹配与分析     → match_result / skill_gap / career_path
  └─ v3.0: Actor-Critic Reflexion Loop (Planner ⇌ Critique Agent)
Layer 4: 报告生成       → career_report.html / .pdf
  └─ v2.1: Template-first + Streaming (延迟 15-30s → 3-5s)
```

## 各层技术规格

| 层 | 文件 | 核心技术 | v2.1 补丁 | v3.0 升维 |
|----|------|----------|-----------|-----------|
| Layer 0 | [arch_v2_layer0.md](arch_v2_layer0.md) | MinerU+YOLOv10+Qwen3-0.6B-SFT | — | — |
| Layer 1 | [arch_v2_layer1.md](arch_v2_layer1.md) | O*NET骨架+LLM填充, NetworkX图 | +OOV Pipeline +冷启动补丁 | **自组织知识网络 + 非对称转移概率** |
| Layer 2 | [arch_v2_layer2.md](arch_v2_layer2.md) | 四维评分:规则+Hybrid+LLM特征提取 | — | — |
| Layer 3 | [arch_v2_layer3.md](arch_v2_layer3.md) | D1硬约束+D2 AC+Embedding+D3/D4加权 | — | **Actor-Critic Reflexion Loop** |
| Layer 4 | [arch_v2_layer4.md](arch_v2_layer4.md) | Template-first+Streaming+Jinja2 | 延迟↓60-80% | — |

## v3.0 三大升维

| 升维 | 模块 | 核心概念 | 学术亮点 |
|------|------|---------|---------|
| 升维一 | L1 Autonomous Ontology | Celery Beat + 联网JD爬取 + DeepSeek-R1 CoT推理 → 图谱热更新 | Continuous Knowledge Distillation |
| 升维二 | L3 Actor-Critic | Planner提路径 → Critique驳回 → 负约束重规划 (max 3轮) | Reflexion Loop + 零和博弈 |
| 升维三 | L1 Asymmetric Transition | 技能势能壁垒 × 指数衰减 → 有向转移概率 P(A→B) ≠ P(B→A) | "基于技能势能差的非对称职场跃迁算法" |

## v3.1 审计修复 (对抗性工程审计)

| 审计项 | 靶向问题 | 修复方案 | 文件 |
|--------|---------|---------|------|
| 审计一 | 非对称图谱"数据真空" — weeks/tier靠LLM脑补=GIGO | **Quad-Source Calibration**: GitHub Roadmaps + 培训大纲 + SO Survey + JD隐含信号 → 加权中位数+离群检测; 低置信度→uncertainty_penalty放大壁垒 | [arch_v2_layer1.md](arch_v2_layer1.md) |
| 审计二 | Actor-Critic"灾难性延迟" — 3轮串行LLM=18-30s | **Pre-flight剪枝 + Confidence Early Stop + Negative Cache**: 60%早停(3s) + 缓存死路; 加权均值8.4s→4.8s (↓43%) | [arch_v2_layer3.md](arch_v2_layer3.md) |
| 审计三 | Cron自动建树"污染扩散" — HR拼写错误/概念混淆 | **Quad-Layer Decontamination**: SymSpell纠错 → OOD异常检测 → 14天隔离沙箱 → 人工审核网关 + 断路器自动回滚 | [arch_v2_layer1.md](arch_v2_layer1.md) |

## 论文依据

| 论文 | 用途 |
|------|------|
| SmartResume (arXiv:2510.09722) | Layer 0 版面解析+LLM抽取基础 |
| GNN for CJM (Springer 2025) | Layer 1/3 图结构匹配思路 |
| Xu et al. (Nature 2021) | Layer 1 O*NET技能分类映射方法 |
| Quasi-Metric Learning (TPAMI 2025) | Layer 3 非对称匹配建模 |
| LLM Agents in Recruitment (ACL 2024) | Layer 2/3 LLM特征提取>直接评分 |
| Hybrid Search (Elastic 2024) | Layer 2/3 AC+Embedding最佳实践 |

## 竞赛指标覆盖

| 规则 | 要求 | 覆盖层 | 状态 |
|------|------|--------|------|
| R-01 | 只保留IT岗位 | L0 JD过滤 | ✅ |
| R-02 | 缺失数据不剔除 | L0/L1 | ✅ |
| R-03 | 技能匹配≥80% | L3 D2匹配 | ✅ |
| R-04 | 关键信息>90% | L0 SFT F1=0.964 | ✅ |
| R-05 | 岗位画像合并 | L1B 聚合 | ✅ |
| R-06 | 晋升≥3条链 | L1C 图谱 | ✅ |
| R-07 | 换岗≥5×2 | L1C 图谱 | ✅ |
| R-08 | 两核心任务 | L3 匹配+差距 | ✅ |
| R-09 | JSON知识库 | L1 导出 | ✅ |
| R-10 | API KEY管理 | 全局配置 | ✅ |
| R-12 | 粗粒度润色 | L4C | ✅ |
| R-13 | 评估周期 | L4A 第六章 | ✅ |
