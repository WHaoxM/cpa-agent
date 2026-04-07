# Layer 2: 画像生成 (Profile Generation)

## 重要发现: 现有 student_profiles.jsonl Schema 不一致

分析 outdata/student_profiles.jsonl 发现多种 Schema 混用:
- 条目1: professional_skills 是 object{frontend,backend,...}
- 条目2: professional_skills 是 array[{skill,level,evidence}]
- 条目3: general_abilities 是 object{innovation_ability:{score,evidence}}
- 条目4: general_abilities 是 array[{ability,score,evidence}]
- career_preference 字段名不统一: target_position vs target_positions

**Layer 2 必须统一输出 Schema，消除上述不一致。**

---

## 2A. 学生四维画像评分

### 输入
Layer 0 输出的 student_profile.json (已冻结Schema: hr_entity_extraction_prompt.json v2.0)

### D1 基础要求 (权重0.25) — 纯规则评分

```
D1_score 计算:

sub_degree (0-30):
  博士=30, 硕士=25, 本科=20, 大专=12

sub_school (0-15):
  985/C9=15, 211=12, 双一流=10, 普通一本=7, 二本=5, 大专=3
  判定方法: school_name match 预定义985/211列表(~150所)

sub_major (0-25):
  计算机科学与技术/软件工程/人工智能/数据科学=25
  信息安全/物联网/网络工程/电子信息=20
  数学/统计/自动化/通信=15
  其他理工=10, 文科/商科=5
  判定: major match IT_MAJOR_LIST

sub_cert (0-15):
  每个IT相关证书: 软考高级+8, 软考中级+5, AWS/CKA等+5
  CET-6 ≥ 500: +3, 雅思 ≥ 6.5: +5, 日语N2+: +3
  上限15分

sub_language (0-15):
  CET-6 ≥ 500: 12, CET-4 ≥ 500: 8, CET-4: 5, 无: 2
  + 小语种加分

D1_raw = sub_degree + sub_school + sub_major + sub_cert + sub_language
D1_score = D1_raw  (满分100)
```

**R-04对齐**: 学历/专业/证书三项从Layer0精确提取,准确率>90%

### D2 职业技能 (权重0.35) — Hybrid匹配评分

```
D2_score 计算 (需要目标岗位作为参照):

Step 1: 技能精确匹配 (AC算法)
  student_skills = Layer0提取的skills列表
  job_required = 目标岗位的required_skills
  exact_matches = AC_match(student_skills, job_required)
  exact_count = len(exact_matches)

Step 2: 技能语义匹配 (BGE-M3)
  remaining_student = student_skills - exact_matches
  remaining_job = job_required - exact_matches
  for s in remaining_student:
    for j in remaining_job:
      sim = cosine(bge_m3.encode(s), bge_m3.encode(j))
      if sim > 0.85:
        semantic_matches.append((s, j, sim))
  semantic_count = len(semantic_matches)

Step 3: 冲突消解
  如果exact和semantic匹配到同一个job_skill → exact优先
  semantic匹配的可信度 = sim值 (0.85~1.0映射到0.8~1.0)

Step 4: 覆盖率
  total_match = exact_count + semantic_count * 0.8
  skill_coverage = total_match / len(job_required)  # 0-1

Step 5: 经验加分
  project_score:   0个=0, 1个=15, 2个=25, 3个+=35 (满分35)
  internship_score: 0月=0, 1-3月=10, 3-6月=20, 6月+=30 (满分30)

D2_score = skill_coverage * 35 + project_score + internship_score
  (满分100: 技能覆盖35 + 项目35 + 实习30)
```

**R-03对齐**: 技能知识点匹配率 = skill_coverage ≥ 80%

### D3 职业素养 (权重0.20) — LLM特征提取 + 规则评分

```
Step 1: LLM特征提取
  Prompt: 
  "从以下简历信息中评估候选人的职业素养,对每项给出等级和证据:
   1. 沟通能力: 强/中/弱 + 1条证据
   2. 团队协作: 强/中/弱 + 1条证据
   3. 抗压能力: 强/中/弱 + 1条证据
   4. 责任心:   强/中/弱 + 1条证据
   简历信息: {internships + projects + campus_experience + self_evaluation}"

Step 2: 等级→分数转换
  强=25, 中=15, 弱=8, 无证据=5 (每项满分25)

Step 3: 规则化加分
  有≥1段实习: +5 (覆盖到总分)
  有团队项目经历: +5
  有leader/组织者角色: +8
  有学生会/社团干部: +3

Step 4: Shannon Entropy不确定性
  如果LLM输出logits可用 → 计算entropy
  entropy > threshold(1.5) → 降低该项可信度,标记为low_confidence
  (用于Layer3匹配时降权)

D3_score = sum(4项等级分数) + 规则加分, 上限100
```

### D4 发展潜力 (权重0.20) — LLM特征提取 + 规则评分

```
Step 1: 规则化评分 (可量化部分)

sub_competition (0-25):
  国家级/ACM区域赛: +25
  省级一等: +18, 二等: +12, 三等: +8
  校级: +5
  Kaggle银牌+: +15

sub_research (0-25):
  CCF A/SCI Q1论文: +25
  CCF B/SCI Q2: +18
  EI/普通期刊: +10
  专利: +8

sub_academic (0-25):
  GPA ≥ 3.8/4.0: +25
  GPA ≥ 3.5: +18
  GPA ≥ 3.0: +12
  排名前5%: +25, 前10%: +20, 前20%: +12
  奖学金: 国家级+10, 校级+5

sub_leadership (0-25):
  学生会主席/副主席: +20
  部长/社团负责人: +12
  班级干部: +8
  项目负责人: +10

Step 2: LLM补充评估 (不可量化部分)
  Prompt:
  "评估候选人的学习能力(自学/转行/快速上手新技术的证据)"
  → 强/中/弱, 对sub_academic做±5调整

D4_score = sub_competition + sub_research + sub_academic + sub_leadership
  上限100
```

### 汇总

```
competitiveness_score = D1*0.25 + D2*0.35 + D3*0.20 + D4*0.20

competitiveness_level:
  ≥ 85: "极强"
  ≥ 70: "强"
  ≥ 55: "中等"
  ≥ 40: "偏弱"
  < 40: "弱"

completeness_score (0-100):
  各字段填充率的加权平均
  权重: 关键信息(学历/专业/证书)×3, 技能×2, 其他×1
```

## 2B. 岗位四维画像

从Layer 1B聚合画像提取:

```
D1_requirements:
  required_degree: "本科"
  required_majors: ["计算机科学与技术", "软件工程", ...]
  required_certs: ["CET-4"]
  required_languages: ["英语"]

D2_requirements:
  required_skills: [{skill, level, tf_score}]   # tf >= 0.6
  preferred_skills: [{skill, level, tf_score}]   # 0.3 <= tf < 0.6
  bonus_skills: [{skill, tf_score}]              # 0.1 <= tf < 0.3

D3_requirements:
  communication: {required: true, weight: 0.3}
  teamwork: {required: true, weight: 0.3}
  stress_tolerance: {required: false, weight: 0.2}
  responsibility: {required: true, weight: 0.2}

D4_requirements:
  innovation_weight: 0.2  (从JD"创新/研发"关键词频率推断)
  learning_weight: 0.3    (从"学习/成长"关键词频率)
  leadership_weight: 0.1  (从"管理/带队"关键词频率)
  internship_preferred: true
  project_count_min: 1

dimension_weights: {D1: 0.25, D2: 0.35, D3: 0.20, D4: 0.20}
  (基线值,根据岗位特征微调±0.05)
```
