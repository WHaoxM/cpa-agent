# Layer 3: 匹配与分析 (Matching & Analysis)

## 3A. 四维匹配引擎

### 输入
- student_portrait (Layer 2A 输出)
- target_job_portrait (Layer 2B 输出)

### 匹配流程

```
                    student_portrait
                          │
          ┌───────────────┼───────────────┐
          │               │               │
    ┌─────▼─────┐   ┌────▼────┐   ┌──────▼──────┐
    │ D1 硬约束  │   │ D2 技能 │   │ D3/D4 素养  │
    │ (规则判定) │   │ (Hybrid)│   │ (LLM+规则)  │
    └─────┬─────┘   └────┬────┘   └──────┬──────┘
          │               │               │
          ▼               ▼               ▼
    pass/fail        0-100分          0-100分
          │               │               │
          └───────────────┼───────────────┘
                          ▼
                 加权汇总 + 匹配判定
```

### D1 匹配 — 硬约束门控

```python
def match_D1(student, job) -> tuple[bool, float]:
    """
    硬约束: 不通过 → 匹配失败 (R-08 Task1)
    返回: (通过/失败, D1分数)
    """
    # 学历硬约束
    DEGREE_ORDER = {"大专": 1, "本科": 2, "硕士": 3, "博士": 4}
    student_level = DEGREE_ORDER.get(student["degree"], 0)
    required_level = DEGREE_ORDER.get(job["required_degree"], 0)
    
    if student_level < required_level:
        return (False, 0)  # 学历不够 → 直接失败
    
    # 专业软约束 (有相关性即可)
    major_match = False
    if student["major"] in job["required_majors"]:
        major_match = True
    else:
        # 语义相关性: 用专业类别判断
        if student["major_category"] in ["计算机类", "电子信息类", "数学类"]:
            major_match = True
    
    if not major_match and job["major_strict"]:
        return (False, 0)  # 专业严格要求时失败
    
    # 证书匹配率
    cert_overlap = len(set(student["certs"]) & set(job["required_certs"]))
    cert_rate = cert_overlap / max(len(job["required_certs"]), 1)
    
    # D1分数
    degree_score = min((student_level - required_level + 1) * 25, 40)
    major_score = 30 if major_match else 10
    cert_score = cert_rate * 30
    
    D1_score = degree_score + major_score + cert_score
    return (True, min(D1_score, 100))
```

### D2 匹配 — Hybrid技能匹配

```python
def match_D2(student_skills: list, job_portrait: dict) -> tuple[float, dict]:
    """
    Hybrid = AC精确匹配(高Precision) + Embedding语义(高Recall)
    返回: (D2分数, 匹配详情)
    """
    required = job_portrait["required_skills"]   # tf >= 0.6
    preferred = job_portrait["preferred_skills"]  # 0.3 <= tf < 0.6
    
    # Stage 1: AC精确匹配
    from skill_matcher import SkillMatcher
    matcher = SkillMatcher(lexicon=[s["skill"] for s in required + preferred])
    student_text = " ".join(student_skills)
    exact_hits = matcher.match(student_text)  # → ["Python", "Docker", ...]
    
    # Stage 2: Embedding语义匹配
    # 对未精确匹配的技能
    remaining_student = [s for s in student_skills if s not in exact_hits]
    remaining_required = [s["skill"] for s in required if s["skill"] not in exact_hits]
    
    semantic_hits = []
    if remaining_student and remaining_required:
        stu_embs = bge_m3.encode(remaining_student)
        req_embs = bge_m3.encode(remaining_required)
        sim_matrix = stu_embs @ req_embs.T  # cosine (已L2归一化)
        
        for i, j in hungarian_assign(sim_matrix):  # 最优匹配
            if sim_matrix[i][j] > 0.85:
                semantic_hits.append({
                    "student_skill": remaining_student[i],
                    "job_skill": remaining_required[j],
                    "similarity": float(sim_matrix[i][j]),
                    "match_type": "semantic"
                })
    
    # Stage 3: 汇总
    required_matched = len([h for h in exact_hits 
                           if h in [s["skill"] for s in required]])
    required_semantic = len([h for h in semantic_hits 
                            if h["job_skill"] in [s["skill"] for s in required]])
    
    # 精确匹配权重1.0, 语义匹配权重0.8
    effective_match = required_matched + required_semantic * 0.8
    skill_coverage = effective_match / max(len(required), 1)
    
    # preferred技能额外加分
    preferred_names = [s["skill"] for s in preferred]
    preferred_matched = len([h for h in exact_hits if h in preferred_names])
    preferred_bonus = preferred_matched / max(len(preferred), 1) * 0.2
    
    D2_score = min((skill_coverage + preferred_bonus) * 100, 100)
    
    details = {
        "exact_matches": exact_hits,
        "semantic_matches": semantic_hits,
        "skill_coverage": skill_coverage,  # R-03 检查此值 >= 0.8
        "missing_required": [s["skill"] for s in required 
                            if s["skill"] not in exact_hits 
                            and s["skill"] not in [h["job_skill"] for h in semantic_hits]]
    }
    return (D2_score, details)
```

### D3/D4 匹配 — 特征向量相似度

```python
def match_D3(student_D3: dict, job_D3: dict) -> float:
    """
    学生D3特征 vs 岗位D3要求
    使用特征向量余弦相似度 + 加权
    """
    ABILITIES = ["communication", "teamwork", "stress_tolerance", 
                 "responsibility", "innovation"]
    
    score = 0
    total_weight = 0
    for ability in ABILITIES:
        if ability in job_D3 and job_D3[ability].get("required"):
            weight = job_D3[ability].get("weight", 0.2)
            total_weight += weight
            
            # 学生该项得分(已在Layer2标准化为0-100)
            stu_score = student_D3.get(ability, {}).get("score", 30)
            # 归一化到0-1
            score += (stu_score / 100) * weight
    
    if total_weight > 0:
        D3_score = (score / total_weight) * 100
    else:
        D3_score = 50  # 岗位无明确D3要求时给中间分
    
    return min(D3_score, 100)

# D4匹配同理, 用competition/research/academic/leadership子项
```

### 最终匹配判定

```python
def final_match(student_portrait, job_portrait) -> dict:
    # D1硬约束
    d1_pass, d1_score = match_D1(student_portrait, job_portrait)
    if not d1_pass:
        return {
            "match_result": False,
            "reason": "学历/专业不符合基础要求",
            "D1": {"score": 0, "pass": False},
            # D2-D4 仍然计算, 用于Task2差距分析
            ...
        }
    
    d2_score, d2_detail = match_D2(...)
    d3_score = match_D3(...)
    d4_score = match_D4(...)
    
    # 加权 (来自Layer 2B岗位画像)
    weights = job_portrait["dimension_weights"]
    total = (d1_score * weights["D1"] + d2_score * weights["D2"] + 
             d3_score * weights["D3"] + d4_score * weights["D4"])
    
    # 匹配判定
    match_pass = (d1_pass 
                  and d2_detail["skill_coverage"] >= 0.6  # 技能覆盖≥60%
                  and total >= 50)  # 总分≥50
    
    return {
        "match_result": match_pass,
        "total_score": round(total, 1),
        "D1": {"score": d1_score, "pass": d1_pass},
        "D2": {"score": d2_score, "skill_coverage": d2_detail["skill_coverage"],
               "exact_matches": d2_detail["exact_matches"],
               "semantic_matches": d2_detail["semantic_matches"],
               "missing_skills": d2_detail["missing_required"]},
        "D3": {"score": d3_score},
        "D4": {"score": d4_score},
        "dimension_weights": weights,
        "confidence": calculate_confidence(d1_score, d2_detail, d3_score, d4_score)
    }
```

## 3B. 技能差距分析

```python
def analyze_skill_gap(student, job, match_result) -> dict:
    """
    R-08 Task2: 差距分析 → 学习规划
    即使匹配失败也可执行(学生选择目标职位)
    """
    missing = match_result["D2"]["missing_skills"]
    
    # 分类: 关键缺失 vs 可快速补充
    critical_gap = []
    quick_learn = []
    
    for skill in missing:
        # 从技能骨架查找该技能的学习难度
        difficulty = skill_taxonomy.get_learning_difficulty(skill)
        
        # 检查学生是否有相关基础
        related = find_related_skills(skill, student["skills"])
        
        if difficulty == "高" and not related:
            critical_gap.append(skill)
        else:
            quick_learn.append(skill)
    
    # D3/D4差距
    d3_gap = identify_ability_gaps(student["D3"], job["D3_requirements"])
    d4_gap = identify_ability_gaps(student["D4"], job["D4_requirements"])
    
    return {
        "skill_gap": {
            "critical": critical_gap,      # 关键缺失(需要系统学习)
            "quick_learn": quick_learn,    # 可快速补充
            "total_gap_count": len(missing),
            "coverage_current": match_result["D2"]["skill_coverage"],
            "coverage_target": 0.80       # R-03
        },
        "ability_gap": {
            "D3_weaknesses": d3_gap,
            "D4_weaknesses": d4_gap
        },
        "recommended_learning": generate_learning_plan(
            critical_gap, quick_learn, d3_gap, d4_gap
        )
    }
```

## 3C. 职业路径推荐

```python
def recommend_career_path(student, match_results, career_graph) -> dict:
    """
    基于匹配结果 + 职业路径图谱, 推荐发展路径
    """
    # 找到匹配度最高的岗位
    best_match = max(match_results, key=lambda x: x["total_score"])
    current_node = best_match["job_id"]
    
    # 垂直晋升路径
    promotion_path = []
    node = current_node
    while True:
        successors = [
            (n, d) for n, d in career_graph.successors(node) 
            if d["type"] == "PROMOTION"
        ]
        if not successors:
            break
        next_node = successors[0]  # 取最直接的晋升目标
        promotion_path.append({
            "from": node, "to": next_node,
            "requirements": career_graph.edges[node, next_node]["requirements"],
            "estimated_years": career_graph.edges[node, next_node].get("years", "2-3年")
        })
        node = next_node
    
    # 横向换岗选项
    lateral_options = []
    for neighbor, data in career_graph[current_node].items():
        if data["type"] == "LATERAL":
            lateral_options.append({
                "target": neighbor,
                "skill_overlap": data["jaccard"],
                "gap_skills": data["gap_skills"],
                "difficulty": data["difficulty"]
            })
    
    return {
        "current_best_match": best_match["job_name"],
        "promotion_path": promotion_path,     # 垂直晋升
        "lateral_options": lateral_options,    # 横向换岗
        "recommended_direction": select_best_direction(
            student, promotion_path, lateral_options
        )
    }
```

---

# v3.0 自进化智能体升级 (Autonomous Agentic System)

> v3.0 为演进式设计，不破坏 v2.1 已冻结 Schema。
> 竞赛阶段使用 v2.1 静态管道；v3.0 模块作为"上线后"渐进式激活。

## 升维二: Actor-Critic 审查机制 (The Critique Agent)

### 设计动机

v2.1 的职业路径推荐是**单轨生成**——Planner 提出路径后直接输出，
没有任何对抗性校验。这意味着:
- LLM 可能过度乐观（"你可以转AI算法工程师！"但该生数学很差）
- 路径可能忽略**不可逾越的硬门槛**（学历壁垒、顶会要求）
- 系统缺乏**自我怀疑**的能力

### 架构: Planner-Critic Reflexion Loop

```
┌─────────────────────────────────────────────────────┐
│                  Reflexion Loop (最多3轮)             │
│                                                      │
│  ┌──────────────┐    proposed_path    ┌────────────┐ │
│  │ Planner Agent│ ──────────────────► │ Critique   │ │
│  │ (规划者)      │                     │ Agent      │ │
│  │              │ ◄────────────────── │ (审查者)    │ │
│  └──────┬───────┘  APPROVED/REJECTED  └────────────┘ │
│         │                                            │
│         │ if REJECTED:                               │
│         │   negative_constraints += rejection_reason  │
│         │   re-plan with constraints                 │
│         │                                            │
│         │ if APPROVED or max_rounds reached:          │
│         ▼                                            │
│  ┌──────────────┐                                    │
│  │ Final Output │ → career_path_recommendation.json  │
│  └──────────────┘                                    │
└─────────────────────────────────────────────────────┘
```

### Planner Agent (规划者)

```python
def planner_agent(
    student: dict,
    match_result: dict,
    career_graph: nx.DiGraph,
    negative_constraints: list = None
) -> dict:
    """
    规划者: 基于图谱 + 匹配结果, 提出职业发展路径
    
    如果收到 negative_constraints (来自 Critique 驳回),
    必须将这些约束作为硬排除项重新规划
    """
    constraint_text = ""
    if negative_constraints:
        constraint_text = f"""
## 硬约束 (来自审查者驳回, 必须遵守):
{chr(10).join(f"- {c}" for c in negative_constraints)}

你必须避开上述所有被驳回的路径和假设。
"""
    
    prompt = f"""你是一位职业发展规划师。基于以下数据, 提出最优职业发展路径。

## 学生画像
{json.dumps(student_summary(student), ensure_ascii=False)}

## 当前匹配结果
最佳匹配岗位: {match_result["current_best_match"]}
总分: {match_result["total_score"]}
D2技能覆盖率: {match_result["D2"]["skill_coverage"]}

## 可用路径 (来自职业图谱)
晋升路径: {json.dumps(get_promotion_options(career_graph, match_result), ensure_ascii=False)}
换岗路径: {json.dumps(get_transfer_options(career_graph, match_result), ensure_ascii=False)}

{constraint_text}

请输出:
1. 推荐的1-2条发展路径 (含每步所需技能和时间)
2. 推荐理由 (结合学生优势)
3. 风险提示 (可能的障碍)

返回JSON:
{{
  "proposed_paths": [
    {{
      "path": ["当前岗位", "中间岗位", "目标岗位"],
      "reasoning": "推荐理由",
      "risks": ["风险1", "风险2"],
      "total_months": int,
      "confidence": float(0-1)
    }}
  ]
}}"""
    
    return llm_generate(prompt, max_tokens=1500)
```

### Critique Agent (审查者)

```python
def critique_agent(
    student: dict,
    proposed_paths: list,
    career_graph: nx.DiGraph
) -> dict:
    """
    审查者: 极度严苛的"挑刺专家"
    
    System Prompt 设定为对抗性审查:
    - 检查学历硬门槛
    - 检查技能势能壁垒 (v3.0 非对称转移概率)
    - 检查时间可行性
    - 检查市场现实性
    """
    
    # 先做规则级硬检查 (不需要LLM)
    hard_rejections = []
    for path in proposed_paths:
        for step in path["path"]:
            node = career_graph.nodes.get(step, {})
            
            # 学历硬门槛
            required_degree = node.get("required_degree", "本科")
            if degree_rank(student["degree"]) < degree_rank(required_degree):
                hard_rejections.append({
                    "path_step": step,
                    "reason": f"学历不达标: 要求{required_degree}, 该生为{student['degree']}",
                    "severity": "FATAL",  # 不可逾越
                })
            
            # 非对称转移概率检查 (v3.0)
            prev_step = get_previous_step(path, step)
            if prev_step:
                trans = compute_asymmetric_transition(
                    get_portrait(prev_step), get_portrait(step)
                )
                if trans["transition_probability"] < 0.05:
                    hard_rejections.append({
                        "path_step": f"{prev_step} → {step}",
                        "reason": f"转移概率极低: P={trans['transition_probability']}, "
                                  f"势能壁垒={trans['energy_barrier']}, "
                                  f"缺失高难度技能: {trans['gap_skills'][:3]}",
                        "severity": "FATAL",
                    })
    
    # 如果有硬驳回, 直接返回 (不需要LLM)
    if any(r["severity"] == "FATAL" for r in hard_rejections):
        return {
            "verdict": "REJECTED",
            "hard_rejections": hard_rejections,
            "soft_concerns": [],
            "suggestion": "请重新规划, 避开上述不可逾越的障碍",
        }
    
    # LLM 做软性审查 (市场趋势、个人特质、时间可行性)
    prompt = f"""你是一位极度严苛的职业规划审查专家。你的角色是"挑刺"。
你必须找出以下规划方案中所有不切实际、过度乐观、或忽略客观条件的问题。

## 学生客观条件
学历: {student["degree"]}, 学校: {student["school_tier"]}
GPA: {student.get("gpa", "未知")}
竞赛: {student.get("competitions", "无")}
论文: {student.get("publications", "无")}
实习: {student.get("internship_months", 0)}个月

## 待审查的路径方案
{json.dumps(proposed_paths, ensure_ascii=False, indent=2)}

## 审查清单 (必须逐条检查):
1. 学历壁垒: 目标岗位是否有隐性学历要求 (如算法岗普遍要求硕士)?
2. 时间可行性: 总转型时间是否现实? (应届生通常只有6-12月窗口期)
3. 技能鸿沟: 是否存在需要数年才能弥补的技能差距?
4. 市场竞争力: 目标岗位的竞争激烈程度, 该生是否有竞争优势?
5. 沉没成本: 是否有更短、更确定的替代路径被忽略?

返回JSON:
{{
  "verdict": "APPROVED" | "REJECTED",
  "concerns": [
    {{"item": "具体问题", "severity": "FATAL|WARNING|INFO", "evidence": "依据"}}
  ],
  "suggestion": "如果驳回, 建议的替代方向",
  "overall_risk_level": "低/中/高/极高"
}}"""
    
    return llm_generate(prompt, max_tokens=1200, temperature=0.2)
```

### Reflexion Loop (反思循环)

```python
def reflexion_career_planning(
    student: dict,
    match_result: dict,
    career_graph: nx.DiGraph,
    max_rounds: int = 3
) -> dict:
    """
    Planner-Critic 对抗循环, 最多3轮
    
    流程:
    Round 1: Planner 自由规划 → Critic 审查
    Round 2: Planner 带约束重规划 → Critic 再审
    Round 3: 最终方案 (如仍被驳回, 输出保守路径)
    """
    negative_constraints = []
    history = []
    
    for round_num in range(1, max_rounds + 1):
        # Planner 规划
        plan = planner_agent(
            student, match_result, career_graph,
            negative_constraints=negative_constraints if round_num > 1 else None
        )
        
        # Critic 审查
        critique = critique_agent(student, plan["proposed_paths"], career_graph)
        
        history.append({
            "round": round_num,
            "plan": plan,
            "critique": critique,
        })
        
        if critique["verdict"] == "APPROVED":
            return {
                "final_paths": plan["proposed_paths"],
                "approval_round": round_num,
                "critique_history": history,
                "confidence": "high" if round_num == 1 else "medium",
            }
        
        # 收集驳回约束
        for rejection in critique.get("hard_rejections", []):
            negative_constraints.append(rejection["reason"])
        for concern in critique.get("concerns", []):
            if concern["severity"] == "FATAL":
                negative_constraints.append(concern["item"])
    
    # 3轮后仍未通过 → 输出最保守的方案
    return {
        "final_paths": generate_conservative_path(student, career_graph),
        "approval_round": "fallback",
        "critique_history": history,
        "confidence": "low",
        "warning": "经过3轮审查仍存在风险, 已输出最保守路径",
    }

def generate_conservative_path(student, career_graph):
    """
    保守路径: 只推荐当前最佳匹配岗位的垂直晋升
    不做任何横向跳转, 最安全
    """
    best = find_best_match_node(student, career_graph)
    chain = get_promotion_chain(career_graph, best)
    return [{
        "path": [n["name"] for n in chain[:3]],  # 最多展示3级
        "reasoning": "基于当前技能的最稳妥发展路线",
        "risks": ["发展路径相对单一"],
        "total_months": sum(n.get("estimated_years", 2) * 12 for n in chain[:2]),
        "confidence": 0.9,
    }]
```

### 审查机制的关键设计决策

| 设计点 | 决策 | 理由 |
|--------|------|------|
| **最大轮次** | 3轮 | 防止无限循环; 3轮已足够收敛 |
| **硬驳回不用LLM** | 规则先行 | 学历/转移概率是客观数据, LLM判断反而不稳定 |
| **软审查用LLM** | 市场/趋势 | 需要主观判断和最新知识 |
| **Critic温度=0.2** | 低温 | 审查者应该严谨确定, 不要"创造性"输出 |
| **保守路径兜底** | 垂直晋升 | 3轮驳回说明该生条件限制大, 不应冒险 |
| **全历史记录** | critique_history | 对用户透明: 展示"系统为什么不推荐某条路" |

### 与 v2.1 的融合点

```
v2.1 路径推荐 (单轨):
  career_graph → shortest_path → 直接输出
  问题: 无校验, 可能推荐不切实际的路径

v3.0 Actor-Critic (对抗):
  career_graph → Planner提议 → Critic审查 → 循环修正 → 输出
  
  v2.1 的 career_path_recommendation.json Schema 不变
  v3.0 新增:
    "critique_history": [...],     // 审查过程透明
    "approval_round": int,         // 第几轮通过
    "confidence": string,          // high/medium/low
    
  兼容策略:
    竞赛阶段: critique_history 为空, 使用 v2.1 单轨输出
    上线阶段: 激活 Reflexion Loop, 自动填充审查历史
```

### 前端展示: 审查过程可视化

```
用户视角:
┌──────────────────────────────────────────────┐
│ 📋 职业路径推荐                              │
│                                              │
│ ✅ 推荐路径: 前端开发 → AI应用工程师          │
│    预计转型: 6-9个月                          │
│    置信度: 高 (第1轮通过)                     │
│                                              │
│ ⚠️ 曾考虑但被排除的路径:                      │
│ ❌ 前端 → AI算法工程师                        │
│    驳回原因: 转移概率P=0.04, 缺失CUDA/深度学习│
│    该路径需要额外24个月+硕士学历              │
│                                              │
│ [展开查看完整审查记录 ▼]                      │
└──────────────────────────────────────────────┘
```

这种"展示被排除的路径"的设计, 比直接给一条路更有说服力——
用户能看到系统**认真思考过所有可能性**, 而不是随便推荐。

---

# v3.1 审计修复 (Audit Patch)

> v3.1 针对 v3.0 Actor-Critic 的延迟灾难做对抗性审计修复。
> 原则: 保留 Reflexion 的对抗校验能力, 消除灾难性延迟。

## 审计二: Actor-Critic 的"灾难性延迟惩罚"修复 — Early Stopping + Negative Cache

### 问题诊断

v3.0 最坏路径:
```
Round 1: Planner(3s) + Critic(3s) = 6s     → REJECTED
Round 2: Planner(3s) + Critic(3s) = 6s     → REJECTED
Round 3: Planner(3s) + Critic(3s) = 6s     → APPROVED/Fallback
                          + 图算法重算 ×3
─────────────────────────────────────────────
Total: 18-24s (串行LLM调用) + 图算法开销 ≈ 20-30s
```

叠加 L4 报告生成(3-5s), 总延迟可能突破 **30s-1min**。
C端产品中 >10s 就会导致用户流失。

### 解决方案: 三层加速策略

```
┌──────────────────────────────────────────────────────┐
│             Actor-Critic 延迟优化 (v3.1)              │
│                                                       │
│  策略A: Pre-flight 规则预检 (0ms)                     │
│  ├─ 在 Planner 调用前, 先做规则硬检                   │
│  ├─ 提前剪枝不可能的路径 → Planner 搜索空间缩小      │
│  └─ 减少 Planner 生成"注定被驳回"的路径              │
│                                                       │
│  策略B: Confidence-based Early Stopping               │
│  ├─ Round 1: Planner confidence > 0.85                │
│  │   AND 规则硬检全通过                               │
│  │   → 跳过 Critic LLM 软审, 直接输出                │
│  ├─ 节省: 1次 Critic LLM 调用 (3s)                   │
│  └─ 适用: ~60% 的常规候选人 (条件好, 路径明确)       │
│                                                       │
│  策略C: Negative Path Cache                           │
│  ├─ 缓存已知的"死路" (被 FATAL 驳回的路径模式)       │
│  ├─ 格式: (source_category, target_category,          │
│  │         constraint_type) → rejection_reason        │
│  ├─ Planner 生成前查缓存 → 直接排除死路             │
│  └─ 缓存命中 → 省去整轮 Planner+Critic 调用         │
└──────────────────────────────────────────────────────┘
```

### 策略A: Pre-flight 规则预检

```python
def preflight_prune(student: dict, career_graph: nx.DiGraph) -> dict:
    """
    在 Planner 调用前, 预先标记不可达节点
    
    效果: Planner 的搜索空间从 N 个岗位缩减到 M 个 (M << N)
    延迟: ~0ms (纯规则计算)
    """
    reachable_nodes = set()
    pruned_nodes = {}
    
    for node_id, node_data in career_graph.nodes(data=True):
        reasons = []
        
        # 学历硬门槛
        if degree_rank(student["degree"]) < degree_rank(node_data.get("required_degree", "本科")):
            reasons.append(f"学历不达标: 需{node_data['required_degree']}")
        
        # 非对称转移概率 (v3.0/v3.1)
        current_node = student.get("current_best_match_id")
        if current_node and current_node != node_id:
            trans = compute_asymmetric_transition_v31(
                get_portrait(current_node), get_portrait(node_id)
            )
            if trans["transition_probability"] < 0.05:
                reasons.append(
                    f"转移概率过低: P={trans['transition_probability']:.3f}"
                )
        
        # Negative Cache 命中
        cache_key = (student.get("major_category"), node_data.get("job_category"))
        cached_rejection = NEGATIVE_CACHE.get(cache_key)
        if cached_rejection:
            reasons.append(f"缓存驳回: {cached_rejection['reason']}")
        
        if reasons:
            pruned_nodes[node_id] = reasons
        else:
            reachable_nodes.add(node_id)
    
    return {
        "reachable_nodes": reachable_nodes,
        "pruned_nodes": pruned_nodes,
        "prune_rate": len(pruned_nodes) / max(len(career_graph.nodes), 1),
    }
```

### 策略B: Confidence-based Early Stopping

```python
def reflexion_career_planning_v31(
    student: dict,
    match_result: dict,
    career_graph: nx.DiGraph,
    max_rounds: int = 3
) -> dict:
    """
    v3.1: 带早停的 Reflexion Loop
    
    延迟预算:
    - 最佳情况 (Early Stop):  Planner(3s)                     = 3s
    - 典型情况 (1轮审查):     Planner(3s) + Critic(3s)        = 6s
    - 最坏情况 (3轮):         3 × (Planner+Critic)            = 18s
    - 实际均值 (加权):        0.6×3s + 0.3×6s + 0.1×18s       = 5.4s
    """
    negative_constraints = []
    history = []
    
    # ═══ Pre-flight: 规则预检, 缩小搜索空间 ═══
    preflight = preflight_prune(student, career_graph)
    pruned_graph = career_graph.subgraph(preflight["reachable_nodes"])
    
    for round_num in range(1, max_rounds + 1):
        # Planner 规划 (使用裁剪后的图)
        plan = planner_agent(
            student, match_result, pruned_graph,
            negative_constraints=negative_constraints if round_num > 1 else None
        )
        
        # ═══ Early Stopping 判定 ═══
        if round_num == 1 and should_early_stop(plan, preflight):
            history.append({
                "round": 1,
                "plan": plan,
                "critique": {"verdict": "EARLY_STOP", "reason": "High confidence + all rules passed"},
            })
            return {
                "final_paths": plan["proposed_paths"],
                "approval_round": 1,
                "critique_history": history,
                "confidence": "high",
                "optimization": "early_stop",
                "latency_saved": "~3s (skipped Critic LLM)",
            }
        
        # Critic 审查
        critique = critique_agent(student, plan["proposed_paths"], career_graph)
        
        history.append({
            "round": round_num,
            "plan": plan,
            "critique": critique,
        })
        
        if critique["verdict"] == "APPROVED":
            # 如果 Round 1 被驳回的路径具有通用性, 写入 Negative Cache
            update_negative_cache(history)
            return {
                "final_paths": plan["proposed_paths"],
                "approval_round": round_num,
                "critique_history": history,
                "confidence": "high" if round_num == 1 else "medium",
            }
        
        # 收集驳回约束
        for rejection in critique.get("hard_rejections", []):
            negative_constraints.append(rejection["reason"])
        for concern in critique.get("concerns", []):
            if concern["severity"] == "FATAL":
                negative_constraints.append(concern["item"])
    
    # 3轮后仍未通过 → 保守路径
    update_negative_cache(history)
    return {
        "final_paths": generate_conservative_path(student, career_graph),
        "approval_round": "fallback",
        "critique_history": history,
        "confidence": "low",
    }


def should_early_stop(plan: dict, preflight: dict) -> bool:
    """
    早停条件 (必须全部满足):
    1. Planner 第一条路径 confidence > 0.85
    2. 所有路径步骤都在 preflight 可达集中 (规则已预检通过)
    3. 路径不包含跨两级以上的跳转
    """
    if not plan.get("proposed_paths"):
        return False
    
    best_path = plan["proposed_paths"][0]
    
    # 条件1: 高置信度
    if best_path.get("confidence", 0) <= 0.85:
        return False
    
    # 条件2: 所有步骤在可达集中
    for step in best_path["path"]:
        if step not in preflight["reachable_nodes"]:
            return False
    
    # 条件3: 无激进跳转 (相邻步骤最多跨1个level)
    for i in range(len(best_path["path"]) - 1):
        level_gap = abs(
            get_job_level(best_path["path"][i+1]) - get_job_level(best_path["path"][i])
        )
        if level_gap > 1:
            return False
    
    return True
```

### 策略C: Negative Path Cache

```python
class NegativePathCache:
    """
    缓存已知的"死路"模式, 避免 Planner 重复生成注定被驳回的路径
    
    缓存粒度: (source_category, target_category, constraint_type)
    而非具体岗位ID → 提高命中率
    
    例:
    ("前端开发", "AI算法", "degree_barrier") → 
        "本科前端转AI算法: 学历壁垒(需硕士) + 势能壁垒(CUDA/深度学习)"
    """
    
    def __init__(self, max_size=500, ttl_days=30):
        self.cache = {}        # key → {reason, hit_count, created_at, expires_at}
        self.max_size = max_size
        self.ttl_days = ttl_days
    
    def get(self, source_cat: str, target_cat: str) -> dict | None:
        key = (source_cat, target_cat)
        entry = self.cache.get(key)
        if entry and entry["expires_at"] > datetime.now():
            entry["hit_count"] += 1
            return entry
        return None
    
    def put(self, source_cat: str, target_cat: str,
            constraint_type: str, reason: str):
        key = (source_cat, target_cat)
        self.cache[key] = {
            "constraint_type": constraint_type,
            "reason": reason,
            "hit_count": 0,
            "created_at": datetime.now(),
            "expires_at": datetime.now() + timedelta(days=self.ttl_days),
        }
        
        # LRU 淘汰
        if len(self.cache) > self.max_size:
            oldest = min(self.cache, key=lambda k: self.cache[k]["hit_count"])
            del self.cache[oldest]
    
    def warm_up_from_history(self, critique_histories: list):
        """
        从历史审查记录中预热缓存
        冷启动时批量加载, 后续增量更新
        """
        for history in critique_histories:
            for round_data in history:
                critique = round_data.get("critique", {})
                for rejection in critique.get("hard_rejections", []):
                    if rejection["severity"] == "FATAL":
                        # 提取通用模式
                        source_cat = extract_category(rejection.get("path_step", ""))
                        target_cat = extract_target_category(rejection)
                        if source_cat and target_cat:
                            self.put(source_cat, target_cat,
                                    rejection.get("constraint_type", "unknown"),
                                    rejection["reason"])

NEGATIVE_CACHE = NegativePathCache(max_size=500, ttl_days=30)
```

### 延迟分析: v3.0 vs v3.1

```
场景               v3.0 延迟     v3.1 延迟      节省
─────────────────────────────────────────────────────
条件好, 路径明确:   6s (1轮)     3s (早停)      50%
普通候选人:         6s (1轮)     6s (1轮)       0%
条件受限:          12s (2轮)     6s (缓存命中)   50%
极端受限:          18s (3轮)    12s (预检+缓存)  33%
─────────────────────────────────────────────────────
加权均值:          8.4s          4.8s           43%

人群分布假设:
- 60% 条件好 → Early Stop
- 25% 普通 → 1轮完整审查
- 10% 受限 → Negative Cache 命中
- 5%  极端 → 完整3轮 (不可避免)
```

### 延迟预算表 (端到端)

```
组件                    v2.1    v3.0(未优化)  v3.1(优化后)
───────────────────────────────────────────────────────
L0 解析:                2s      2s           2s
L1 知识构建:            0.5s    0.5s         0.5s
L2 画像生成:            1s      1s           1s
L3 匹配+路径(Reflexion): 1s     8.4s         4.8s
L4 报告生成:            3-5s    3-5s         3-5s
───────────────────────────────────────────────────────
Total:                  7-9s    15-17s       11-13s

目标: C端产品可接受范围 ≤ 15s
v3.1 满足: 11-13s ✅ (97.5%ile < 15s)
```

### 与 v2.1 Template-first Streaming 的协同

```
T=0s:    L0-L2 完成, L4 Phase1 骨架渲染 (用户已看到表格+图表)
T=0-5s:  L3 Reflexion 运行中 (用户已在浏览骨架报告)
T=5s:    L3 完成, L4 Phase2 触发 (4次并行LLM insight)
T=5-8s:  insight 流式插入
T=8s:    完整报告渲染完成

用户感知延迟: ~0s (骨架即时可见)
完整内容延迟: ~8-10s (可接受)
```
