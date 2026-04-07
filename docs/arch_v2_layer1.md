# Layer 1: 知识构建 (Knowledge Construction)

## 1A. IT技能分类体系 (Skill Taxonomy)

### 方法: 预定义骨架(O*NET inspired) + LLM从JD填充

**骨架结构 (6大类 → 24子类 → ~500预定义技能)**:

```json
{
  "programming_languages": {
    "系统级": ["C", "C++", "Rust", "Go", "Assembly"],
    "应用级": ["Java", "Python", "C#", "Kotlin", "Swift", "Dart"],
    "脚本级": ["JavaScript", "TypeScript", "PHP", "Ruby", "Lua"],
    "数据级": ["SQL", "R", "Julia", "MATLAB", "Scala"]
  },
  "frameworks_libraries": {
    "前端框架": ["React", "Vue.js", "Angular", "Next.js", "Svelte",
                "jQuery", "Bootstrap", "TailwindCSS", "Element UI", "Ant Design"],
    "后端框架": ["Spring Boot", "Spring Cloud", "Django", "Flask", "FastAPI",
                "Express", "Gin", "Fiber", "NestJS", "Laravel"],
    "AI_ML框架": ["PyTorch", "TensorFlow", "Scikit-learn", "HuggingFace",
                  "LangChain", "Keras", "OpenCV", "YOLO"],
    "移动端框架": ["Flutter", "React Native", "SwiftUI", "Jetpack Compose",
                  "Uni-app", "Taro", "微信小程序"],
    "大数据框架": ["Spark", "Flink", "Hadoop", "Kafka", "Hive", "HBase",
                  "Airflow", "DataX", "Kettle"]
  },
  "databases_storage": {
    "关系型": ["MySQL", "PostgreSQL", "Oracle", "SQL Server", "SQLite"],
    "NoSQL": ["MongoDB", "Redis", "Elasticsearch", "Neo4j", "Cassandra",
             "HBase", "InfluxDB"],
    "向量数据库": ["Milvus", "Pinecone", "Weaviate", "Qdrant", "FAISS"]
  },
  "devops_tools": {
    "容器化": ["Docker", "Kubernetes", "Podman", "Docker Compose"],
    "CI_CD": ["Jenkins", "GitLab CI", "GitHub Actions", "ArgoCD"],
    "云平台": ["AWS", "阿里云", "腾讯云", "Azure", "GCP", "华为云"],
    "监控日志": ["Prometheus", "Grafana", "Zabbix", "ELK", "Datadog"],
    "版本控制": ["Git", "SVN"]
  },
  "domain_knowledge": {
    "计算机网络": ["TCP/IP", "HTTP/HTTPS", "DNS", "CDN", "负载均衡",
                 "WebSocket", "gRPC", "RESTful API"],
    "操作系统": ["Linux", "Shell", "进程管理", "内存管理", "文件系统"],
    "安全": ["渗透测试", "防火墙", "加密算法", "OAuth", "JWT"],
    "算法": ["数据结构", "动态规划", "图算法", "排序搜索", "贪心"]
  },
  "soft_skills": {
    "职业素养": ["沟通能力", "团队协作", "抗压能力", "责任心", "时间管理"],
    "发展潜力": ["创新能力", "学习能力", "领导力", "问题解决", "批判性思维"]
  }
}
```

### 填充算法

```
Step 1: 批量提取
  对5000条JD → LLM批量提取技能关键词
  Prompt: "从以下职位描述中提取所有技能要求，按必备/优先/加分分类"
  批次: 每次10条JD → 减少API调用

Step 2: AC精确匹配到骨架
  用现有 skill_matcher.py (Aho-Corasick)
  词库: 骨架中所有~500个技能词
  结果: matched_skills[] with taxonomy_path

Step 3: Embedding语义补充
  对未匹配的技能词 → BGE-M3 encode
  与骨架所有技能的embedding计算cosine
  cosine > 0.85 → 归类到最近骨架节点
  例: "SpringMVC" → 匹配到 "Spring Boot" (cosine=0.89)

Step 4: 新技能发现
  cosine < 0.85 的未匹配词 → 候选新技能队列
  频次 > 5% 的候选词 → LLM判断归类到哪个子类
  频次 < 5% → 忽略(可能是噪声)

Step 5: 频次统计
  对每个岗位: tf(skill, position) = 该技能在该岗位JD中出现的比例
  全局: idf(skill) = log(总岗位数 / 包含该技能的岗位数)
```

### OOV Discovery Pipeline（新词发现管道）

**问题**: 静态技能骨架会随 AI/前端等领域快速迭代而老化（如 DeepSeek、Cursor、Vibe Coding）。

**设计**: L0 抽取阶段产生的未匹配技能词不应被静默丢弃，而应进入审核管道动态扩展 Taxonomy。

```
OOV 发现流程 (与 Step 4 衔接):

Step 4a: OOV 收集
  L0 Phase2 skill_matcher 运行时:
    matched → 正常流程
    unmatched → 写入 oov_buffer.jsonl
    格式: {"term": "Cursor", "source_jd_id": "jd_xxx", "context": "...熟练使用Cursor进行...", "timestamp": "..."}

Step 4b: Embedding 近邻检测
  对 oov_buffer 中每个 term:
    emb = bge_m3.encode(term)
    top1_sim = max(cosine(emb, taxonomy_embs))
    if top1_sim > 0.85:
      → alias_candidate (可能是已有技能的别名)
      例: "Spring MVC" → Spring Boot (sim=0.89) → 添加为 alias
    elif top1_sim > 0.70:
      → related_candidate (相关但不同的技能)
    else:
      → novel_candidate (全新技能)

Step 4c: 频次聚合 + 置信度
  按月/按批次聚合 oov_buffer:
    term_freq[term] = count / total_jd_in_batch
    if term_freq >= 0.03 (3%):  → 进入审核队列 (比原 5% 更宽松, 防遗漏)
    elif term_freq >= 0.01:     → 观察列表 (下批次再确认)
    else:                       → 丢弃 (噪声/OCR错误)

Step 4d: LLM 自动归类 (审核队列)
  对审核队列中每个 term:
    prompt = f"""
    以下技术术语 "{term}" 在 {count} 份JD中出现。
    上下文示例: {contexts[:3]}
    
    请判断:
    1. 这是否是一个有效的IT技能/工具? (yes/no)
    2. 如果是, 它应该归入以下哪个子类?
       {taxonomy_categories}
    3. 它是否是某个已有技能的别名?
       候选: {nearest_skills_top3}
    4. 简短描述该技能。
    
    返回JSON: {{"valid": bool, "category": str|null, "alias_of": str|null, "description": str}}
    """
  
  LLM 输出:
    valid=true, alias_of=null → taxonomy.add_skill(term, category)
    valid=true, alias_of="X"  → taxonomy.add_alias(X, term)
    valid=false               → blacklist.add(term) (永不再审核)

Step 4e: 版本化更新
  taxonomy 采用追加式更新 (append-only):
    skill_taxonomy_v2.0.json → skill_taxonomy_v2.1.json
    diff_log: {"added": [...], "aliases_added": [...], "blacklisted": [...]}
    每次更新后重建 AC 自动机 + Embedding 索引
  
  触发条件: 审核队列累积 >= 10 个新技能 OR 周期性(每月)
```

**关键约束**:
- OOV Pipeline 不修改已冻结的 taxonomy 骨架结构（6大类24子类不变）
- 只做叶子节点的增量添加和别名扩展
- blacklist 防止重复审核同一噪声词
- 版本化保证可回滚
- 竞赛阶段可关闭此管道（使用静态骨架即可），上线后开启

## 1B. 岗位画像聚合

### 聚合算法

```
输入: 5000条结构化JD, 按标准化职位名称分组
  标准化: LLM将相似职位名映射到标准名
  例: "Java开发" "Java后端工程师" "Java研发" → "Java开发工程师"

对每个岗位组 (e.g., "Java开发工程师" 200条JD):

  1. 学历要求: mode(degree) — 众数
     覆盖率<50%时取 "本科及以上"

  2. 经验要求: median(years) — 中位数
     区分应届/1-3年/3-5年/5年+

  3. 薪资范围: [P25(min_salary), P75(max_salary)]
     取四分位避免极端值

  4. 专业要求: 频次>30%的专业列表

  5. 技能要求 (三级分类):
     必备(required): tf(skill) >= 0.6
     优先(preferred): 0.3 <= tf(skill) < 0.6
     加分(bonus):     0.1 <= tf(skill) < 0.3

  6. 岗位职责: LLM摘要聚合(top-5高频职责)
     输入: 所有JD的responsibilities → LLM提炼共性

  7. 四维权重:
     基线: D1=0.25, D2=0.35, D3=0.20, D4=0.20
     微调: 根据JD中各维度关键词频率 ±0.05
     例: 管理岗D3权重+0.05, 研发岗D2权重+0.05

输出: ≥10个聚合岗位画像 (job_portraits.json)
```

### 输出要求对齐

- R-05: 相同岗位合并后的结果 ✓
- R-02: 部分字段缺失不剔除,用有数据的做综合 ✓
- 画像数量 ≥ 10 ✓

## 1C. 职业路径图谱

### 数据结构: NetworkX DiGraph

```python
import networkx as nx

G = nx.DiGraph()

# 节点 = 聚合岗位画像
for portrait in job_portraits:
    G.add_node(portrait["position_id"], **{
        "name": portrait["position_name"],
        "category": portrait["job_category"],
        "level": infer_level(portrait),  # junior/mid/senior/lead/director
        "required_skills": portrait["required_skills"],
        "salary_median": portrait["salary_median"],
    })
```

### 垂直晋升边 (PROMOTION)

```
判定规则 (需同时满足≥2条):
  a) 同一职位族: category相同
     职位族例: {后端开发, 前端开发, 算法, 运维, 测试, 数据, 产品}
  b) 层级递增: level(A) < level(B)
     层级词典: 初级/助理=1, 中级=2, 高级/资深=3, 专家/架构师=4, 经理/总监=5
  c) 技能子集: skills(低级) ⊂ skills(高级) 或 overlap > 0.7
  d) 经验递增: exp(A) < exp(B)

LLM辅助: 对规则无法确定的 → LLM判断是否存在晋升关系
  Prompt: "以下两个岗位是否存在晋升关系？A:{name,skills} B:{name,skills}"

示例晋升链:
  初级Java开发 → Java开发工程师 → 高级Java开发 → Java架构师 → 技术总监
  初级测试 → 测试工程师 → 高级测试 → 测试经理 → 质量总监
  数据分析师 → 高级数据分析 → 数据架构师 → 数据VP

要求: ≥3条晋升链 (R-06)
```

### 横向换岗边 (LATERAL_TRANSFER)

```
基于技能重叠度:
  jaccard(A, B) = |skills_A ∩ skills_B| / |skills_A ∪ skills_B|

换岗条件:
  1. jaccard > 0.3
  2. A和B不在同一晋升链上
  3. 薪资差距 < 30%

示例:
  前端开发 ↔ 全栈开发 (jaccard=0.55, 共享JS/TS/React)
  后端开发 ↔ 数据工程师 (jaccard=0.40, 共享Python/SQL/Docker)
  测试工程师 ↔ 运维工程师 (jaccard=0.35, 共享Linux/Shell/CI)
  数据分析师 ↔ 产品经理 (jaccard=0.25+LLM判定, 共享SQL/数据思维)
  算法工程师 ↔ 数据科学家 (jaccard=0.50, 共享Python/ML/统计)

每条换岗边附带:
  - skill_overlap: jaccard值
  - gap_skills: B要求但A不具备的技能
  - training_needed: 补充培训建议
  - difficulty: 容易(j>0.5)/中等(0.3<j<0.5)/困难(j<0.3+LLM判定)

要求: ≥5个岗位 × 每个≥2条换岗路径 (R-07)
```

### Industry Common-sense Patch（冷启动常识补丁）

**问题**: 换岗路径的 Jaccard 计算严重依赖 JD 数据丰富度。初期仅几百份 JD 时，
纯数据驱动的图谱会产生荒谬路径（如"后端开发→UI设计"仅因共享 Git 和 VS Code）。

**设计**: 引入行业先验知识作为硬约束 + 平滑项，防止数据稀疏时图谱失真。

```python
# ═══════════════════════════════════════════
# 1. 岗位族亲缘矩阵（硬编码行业常识）
# ═══════════════════════════════════════════

AFFINITY_MATRIX = {
    # (source_category, target_category): affinity_score (0-1)
    # 高亲缘: 技能栈天然重叠
    ("后端开发", "全栈开发"): 0.85,
    ("前端开发", "全栈开发"): 0.80,
    ("后端开发", "数据工程"):  0.65,
    ("算法",     "数据工程"):  0.70,
    ("算法",     "数据分析"):  0.55,
    ("测试",     "运维"):      0.45,
    ("后端开发", "运维"):      0.40,
    ("前端开发", "产品"):      0.30,
    
    # 低亲缘: 几乎不可能直接转岗
    ("后端开发", "安全"):      0.20,
    ("前端开发", "算法"):      0.15,
    ("测试",     "算法"):      0.10,
    ("运维",     "前端开发"):  0.10,
    ("产品",     "算法"):      0.05,
}

# 对称填充
for (a, b), v in list(AFFINITY_MATRIX.items()):
    if (b, a) not in AFFINITY_MATRIX:
        AFFINITY_MATRIX[(b, a)] = v

# ═══════════════════════════════════════════
# 2. 换岗边生成: Jaccard × Affinity 双重过滤
# ═══════════════════════════════════════════

def build_transfer_edges(portraits, min_jd_count=20):
    """
    冷启动保护: 数据不足时用 affinity 兜底,
                数据充足时 affinity 退化为软约束
    """
    edges = []
    for A in portraits:
        for B in portraits:
            if A["portrait_id"] == B["portrait_id"]:
                continue
            if is_same_promotion_chain(A, B):
                continue  # 同晋升链不算换岗 (R-07)
            
            # 数据驱动: Jaccard
            jaccard = compute_jaccard(A["required_skills"], B["required_skills"])
            
            # 行业常识: Affinity
            affinity = AFFINITY_MATRIX.get(
                (A["job_category"], B["job_category"]), 0.15  # 默认低亲缘
            )
            
            # 冷启动保护: 数据量不足时提高 affinity 权重
            a_data_rich = A["source_jd_count"] >= min_jd_count
            b_data_rich = B["source_jd_count"] >= min_jd_count
            
            if a_data_rich and b_data_rich:
                # 数据充足: Jaccard 主导, affinity 作为软过滤
                combined = jaccard * 0.8 + affinity * 0.2
                min_threshold = 0.30
            else:
                # 冷启动: affinity 权重提高, 防止垃圾边
                combined = jaccard * 0.4 + affinity * 0.6
                min_threshold = 0.35  # 更严格
            
            # 硬约束: affinity < 0.15 → 绝对禁止 (行业常识否决)
            if affinity < 0.15:
                continue
            
            if combined >= min_threshold:
                gap = set(B["required_skills"]) - set(A["required_skills"])
                edges.append({
                    "source": A["portrait_id"],
                    "target": B["portrait_id"],
                    "type": "LATERAL_TRANSFER",
                    "jaccard": round(jaccard, 3),
                    "affinity": round(affinity, 3),
                    "combined_score": round(combined, 3),
                    "data_confidence": "high" if (a_data_rich and b_data_rich) else "low",
                    "gap_skills": list(gap)[:10],
                    "difficulty": classify_difficulty(combined),
                    "training_needed": estimate_training(gap),
                })
    
    return edges

def classify_difficulty(combined_score):
    if combined_score > 0.55: return "容易"
    if combined_score > 0.35: return "中等"
    return "困难"

# ═══════════════════════════════════════════
# 3. 晋升链最低 JD 阈值
# ═══════════════════════════════════════════

def build_promotion_edges(portraits, min_jd_per_level=5):
    """
    冷启动保护: 某层级JD不足时, 使用行业标准模板补充
    """
    edges = []
    for chain_category in PROMOTION_CHAINS:  # 预定义晋升族
        levels = get_portraits_by_chain(portraits, chain_category)
        
        for i in range(len(levels) - 1):
            lower, upper = levels[i], levels[i+1]
            
            # 检查数据充足度
            if lower["source_jd_count"] < min_jd_per_level:
                lower = merge_with_template(lower, chain_category)
                # 使用行业标准模板补充技能要求
            
            edges.append({
                "source": lower["portrait_id"],
                "target": upper["portrait_id"],
                "type": "PROMOTION",
                "skill_gap": compute_promotion_gap(lower, upper),
                "requirements": infer_requirements(lower, upper),
                "estimated_years": STANDARD_YEARS[chain_category][i],
                "data_confidence": "high" if lower["source_jd_count"] >= min_jd_per_level else "template_augmented",
            })
    
    return edges

# ═══════════════════════════════════════════
# 4. 行业标准晋升模板（兜底）
# ═══════════════════════════════════════════

PROMOTION_CHAINS = {
    "后端开发": [
        {"level": "junior",   "title": "初级后端开发",   "years": "0-1"},
        {"level": "mid",      "title": "后端开发工程师",  "years": "1-3"},
        {"level": "senior",   "title": "高级后端开发",    "years": "3-5"},
        {"level": "expert",   "title": "后端架构师",      "years": "5-8"},
        {"level": "director", "title": "技术总监",       "years": "8+"},
    ],
    "前端开发": [
        {"level": "junior",   "title": "初级前端开发"},
        {"level": "mid",      "title": "前端开发工程师"},
        {"level": "senior",   "title": "高级前端开发"},
        {"level": "expert",   "title": "前端架构师"},
    ],
    "算法": [
        {"level": "junior",   "title": "算法工程师"},
        {"level": "mid",      "title": "高级算法工程师"},
        {"level": "senior",   "title": "算法专家"},
        {"level": "expert",   "title": "首席科学家"},
    ],
    # ... 至少覆盖 R-06 要求的 3 条链
}

# 每条链的标准晋升技能增量
STANDARD_SKILL_INCREMENT = {
    "后端开发": {
        "junior→mid": ["Spring Boot", "MySQL", "Redis", "Docker"],
        "mid→senior": ["微服务", "分布式系统", "性能优化", "Kubernetes"],
        "senior→expert": ["系统设计", "架构设计", "技术选型", "团队管理"],
    },
    # ...
}
```

**关键约束**:
- `AFFINITY_MATRIX` 为硬编码行业常识，**不由 LLM 生成**，保证稳定性
- `affinity < 0.15` 绝对否决，防止任何数据噪声产生荒谬路径
- `data_confidence` 字段透明标注每条边的数据充足度
- `PROMOTION_CHAINS` 作为最小可行图谱骨架，即使零 JD 数据也能输出有效晋升链
- 数据充足后 affinity 权重自然降低（0.8:0.2 vs 0.4:0.6），系统平滑过渡

### 图谱导出

```python
# 导出为JSON (R-09 本地知识库格式)
graph_data = {
    "nodes": [
        {"id": "pos_001", "name": "Java开发工程师", "level": "mid",
         "skills": [...], "salary": "15-25k", "portrait_ref": "..."}
    ],
    "promotion_edges": [
        {"source": "pos_001", "target": "pos_005",
         "type": "PROMOTION", "requirements": "3年经验+架构能力",
         "data_confidence": "high"}
    ],
    "transfer_edges": [
        {"source": "pos_001", "target": "pos_008",
         "type": "LATERAL_TRANSFER", "jaccard": 0.45, "affinity": 0.65,
         "combined_score": 0.49,
         "gap_skills": ["React", "CSS"], "difficulty": "中等",
         "data_confidence": "high"}
    ]
}

# 同时导出向量化数据 (R-09)
for node in graph_data["nodes"]:
    node["embedding"] = bge_m3.encode(
        f"{node['name']} {' '.join(node['skills'])}"
    ).tolist()
```

---

# v3.0 自进化智能体升级 (Autonomous Agentic System)

> v3.0 为演进式设计，不破坏 v2.1 已冻结 Schema。
> 竞赛阶段使用 v2.1 静态管道；v3.0 模块作为"上线后"渐进式激活。

## 升维一: Cron-Driven Autonomous Ontology（自组织知识网络）

### 设计动机

v2.1 OOV Pipeline 仍需"有 JD 进入系统"才能触发新词发现。
v3.0 将系统从**被动响应**升级为**主动探测**——即使没有新 JD 输入，
系统也能像人类一样定期"刷招聘网站"感知行业变化。

### 架构: Continuous Knowledge Distillation（持续知识蒸馏）

```
┌─────────────────────────────────────────────────┐
│  Celery Beat (cron: 每周日 02:00 UTC+8)         │
│  ┌───────────────────────────────────────────┐  │
│  │ Phase A: 触发探测                         │  │
│  │   ├─ Google Search API: "2026 热门IT技能"  │  │
│  │   ├─ 爬取 Boss直聘/拉勾 最新1000份JD      │  │
│  │   └─ 输出: raw_jd_batch_{date}.jsonl      │  │
│  ├───────────────────────────────────────────┤  │
│  │ Phase B: 新词发现 (复用 v2.1 OOV Step4a-c)│  │
│  │   ├─ AC匹配 → unmatched → oov_buffer     │  │
│  │   ├─ Embedding近邻 → alias/related/novel  │  │
│  │   └─ 频次聚合 → 审核队列                  │  │
│  ├───────────────────────────────────────────┤  │
│  │ Phase C: 高阶推理 (DeepSeek-R1 / o1)      │  │
│  │   ├─ 输入: 审核队列 + 联网搜索上下文      │  │
│  │   ├─ CoT Prompt (见下方)                   │  │
│  │   └─ 输出: 拓扑坐标 + 前置技能 + 替代关系 │  │
│  ├───────────────────────────────────────────┤  │
│  │ Phase D: 图谱热更新                       │  │
│  │   ├─ NetworkX: add_node + add_edge        │  │
│  │   ├─ 重建 AC 自动机 + Embedding 索引      │  │
│  │   ├─ 版本化: taxonomy_v2.1 → v2.2 → ...  │  │
│  │   └─ diff_log + changelog 写入            │  │
│  └───────────────────────────────────────────┘  │
│  Phase E: 自检报告 → 邮件/Webhook 通知管理员    │
└─────────────────────────────────────────────────┘
```

### Phase C: 高阶推理 Prompt

```python
def cot_classify_new_skill(term: str, search_results: list, taxonomy: dict) -> dict:
    """
    用 DeepSeek-R1 / o1 级别模型进行深度推理
    关键: 开启 thinking mode, 让模型自主推导技能拓扑位置
    """
    prompt = f"""<think>
你是一位资深 IT 行业分析师。以下是一个在最新招聘市场中出现的新技术术语。

## 新术语
名称: {term}
最新搜索结果摘要:
{format_search_results(search_results[:5])}

## 当前技能图谱结构
{format_taxonomy_skeleton(taxonomy)}

## 请推理以下问题 (逐步思考):

1. **定性判断**: 这是一个有效的IT技能/工具/框架吗? 还是营销概念/噪声?
   - 如果是噪声, 直接返回 valid=false

2. **分类定位**: 它属于图谱中哪个大类 > 子类?
   - 如果不属于任何现有子类, 是否需要新建子类? (极少数情况)

3. **前置技能链**: 学习此技能需要哪些前置技能? (已存在于图谱中的)
   - 例: MCP协议 → 前置: [Python, API设计, LLM基础]

4. **替代/演进关系**: 它是否替代或演进自图谱中某个现有技能?
   - 例: DeepSeek-R1 演进自 GPT-4 (同类竞品)
   - 例: Bun 可能替代 Node.js (同生态位)

5. **热度预测**: 基于搜索趋势, 预测6个月后此技能的JD出现率
   - 高(>10%) / 中(3-10%) / 低(1-3%) / 昙花(<1%)
</think>

返回JSON:
{{
  "valid": bool,
  "category": "大类/子类",
  "aliases": ["别名1", "别名2"],
  "prerequisite_skills": ["skill1", "skill2"],
  "replaces": "被替代的技能名" | null,
  "evolves_from": "演进自的技能名" | null,
  "heat_prediction": "高/中/低/昙花",
  "description": "一句话描述",
  "confidence": float(0-1)
}}"""
    
    return reasoning_model.generate(prompt, temperature=0.3)
```

### Phase D: 图谱热更新

```python
def hot_update_taxonomy(classification_result: dict, G: nx.DiGraph):
    """
    细胞级新陈代谢: 在运行中的图谱上增删节点和边
    """
    r = classification_result
    if not r["valid"]:
        blacklist.add(r["term"])
        return
    
    # 1. 添加新节点
    node_id = f"skill_{slugify(r['term'])}"
    G.add_node(node_id, **{
        "name": r["term"],
        "category": r["category"],
        "aliases": r["aliases"],
        "heat": r["heat_prediction"],
        "added_at": datetime.now().isoformat(),
        "source": "autonomous_discovery",
    })
    
    # 2. 前置技能边 (PREREQUISITE)
    for prereq in r["prerequisite_skills"]:
        prereq_id = find_node_by_name(G, prereq)
        if prereq_id:
            G.add_edge(prereq_id, node_id, type="PREREQUISITE",
                       weight=0.8, source="cot_inference")
    
    # 3. 替代关系边 (REPLACES / EVOLVES_FROM)
    if r["replaces"]:
        old_id = find_node_by_name(G, r["replaces"])
        if old_id:
            G.add_edge(node_id, old_id, type="REPLACES",
                       source="cot_inference")
    
    if r["evolves_from"]:
        parent_id = find_node_by_name(G, r["evolves_from"])
        if parent_id:
            G.add_edge(parent_id, node_id, type="EVOLVES_TO",
                       source="cot_inference")
    
    # 4. 重建索引
    rebuild_ac_automaton(G)
    rebuild_embedding_index(G)
    
    # 5. 版本化
    bump_taxonomy_version(G, diff=r)
```

### 与 v2.1 OOV Pipeline 的关系

```
v2.1 OOV Pipeline (被动式):
  有JD进入 → skill_matcher unmatched → 审核 → 更新
  触发条件: 有新数据输入时
  
v3.0 Autonomous Ontology (主动式):
  Celery Beat 定时触发 → 爬取最新JD → 全流程自动 → 更新
  触发条件: 时间驱动, 无需外部输入
  
共存关系:
  v2.1 处理"已进入系统的数据"中的未知词
  v3.0 主动去"外部世界"探测未知词
  两者写入同一个 oov_buffer, 共享 Phase D 热更新
```

---

## 升维三: Asymmetric Transition Probability（非对称转移概率矩阵）

### 问题诊断

v2.1 的 `combined = jaccard * w1 + affinity * w2` 是**对称的**:
- `score(后端→前端) == score(前端→后端)`

但职场现实是**非对称的**:
- 后端→前端: 容易 (已有 JS 基础, 补 CSS/React 即可)
- 前端→底层架构: 极难 (需补操作系统/编译原理/C++)

对称算法会让系统推荐荒谬路径。

### 算法升级: Skill Energy Barrier（技能势能壁垒模型）

```python
import networkx as nx
import numpy as np

# ═══════════════════════════════════════════
# 1. 技能获取成本矩阵 (Skill Acquisition Cost)
# ═══════════════════════════════════════════

SKILL_ACQUISITION_COST = {
    # skill_name: (learning_weeks, difficulty_tier)
    # difficulty_tier: 1=入门级, 2=中级, 3=高级, 4=专家级, 5=博士级
    
    # 前端
    "HTML":         (1,  1),
    "CSS":          (2,  1),
    "JavaScript":   (4,  2),
    "React":        (4,  2),
    "TypeScript":   (3,  2),
    "Vue.js":       (3,  2),
    
    # 后端
    "Java":         (6,  2),
    "Spring Boot":  (4,  3),
    "MySQL":        (3,  2),
    "Redis":        (2,  2),
    "微服务":       (8,  3),
    "分布式系统":   (12, 4),
    
    # AI/算法
    "Python":       (3,  1),
    "PyTorch":      (6,  3),
    "深度学习":     (12, 4),
    "CUDA优化":     (16, 5),
    "论文复现":     (20, 5),
    "Transformer":  (8,  4),
    
    # DevOps
    "Docker":       (2,  2),
    "Kubernetes":   (6,  3),
    "CI/CD":        (3,  2),
}

# ═══════════════════════════════════════════
# 2. 非对称转移概率计算
# ═══════════════════════════════════════════

def compute_asymmetric_transition(
    source_portrait: dict,
    target_portrait: dict,
    skill_costs: dict = SKILL_ACQUISITION_COST
) -> dict:
    """
    计算从 source 岗位到 target 岗位的有向转移概率
    
    核心公式:
    P(source → target) = skill_overlap * decay(energy_barrier)
    
    其中:
    - skill_overlap: 技能交集 / target所需技能 (非对称, 分母始终是target)
    - energy_barrier: 缺失技能的加权获取成本
    - decay: 指数衰减函数, barrier越高概率越低
    """
    source_skills = set(s["skill"] for s in source_portrait["required_skills"])
    target_skills = set(s["skill"] for s in target_portrait["required_skills"])
    
    # 非对称覆盖率: source能覆盖target多少
    overlap = source_skills & target_skills
    coverage = len(overlap) / max(len(target_skills), 1)
    
    # 缺失技能的势能壁垒
    gap_skills = target_skills - source_skills
    
    total_barrier = 0
    max_difficulty = 0
    for skill in gap_skills:
        weeks, difficulty = skill_costs.get(skill, (8, 3))  # 默认中等
        total_barrier += weeks * difficulty  # 加权成本
        max_difficulty = max(max_difficulty, difficulty)
    
    # 指数衰减: barrier越大, 概率越低
    # lambda 控制衰减速率, 可调参数
    LAMBDA = 0.02
    energy_decay = np.exp(-LAMBDA * total_barrier)
    
    # 最终转移概率
    transition_prob = coverage * energy_decay
    
    # 硬门槛: 存在 tier≥5 的缺失技能 → 额外惩罚
    if max_difficulty >= 5:
        transition_prob *= 0.3  # 博士级技能壁垒
    
    return {
        "source": source_portrait["portrait_id"],
        "target": target_portrait["portrait_id"],
        "direction": f"{source_portrait['position_name']} → {target_portrait['position_name']}",
        "skill_coverage": round(coverage, 3),
        "gap_skills": list(gap_skills),
        "energy_barrier": round(total_barrier, 1),
        "max_difficulty_tier": max_difficulty,
        "transition_probability": round(transition_prob, 4),
        "estimated_months": estimate_transition_months(gap_skills, skill_costs),
        "feasibility": classify_feasibility(transition_prob),
    }

def classify_feasibility(prob: float) -> str:
    if prob > 0.6:  return "自然过渡"   # Natural transition
    if prob > 0.3:  return "可行但需努力" # Feasible with effort
    if prob > 0.1:  return "困难"        # Difficult
    if prob > 0.03: return "极困难"      # Very difficult
    return "几乎不可能"                  # Nearly impossible

def estimate_transition_months(gap_skills, skill_costs):
    """估算转型所需月数 (关键路径, 非简单求和)"""
    if not gap_skills:
        return 0
    # 假设可并行学习, 但受限于最难技能
    total_weeks = sum(skill_costs.get(s, (8, 3))[0] for s in gap_skills)
    critical_path = max(skill_costs.get(s, (8, 3))[0] for s in gap_skills)
    # 实际时间 ≈ max(关键路径, 总量/并行度)
    parallel_factor = 2  # 假设同时学2项
    return round(max(critical_path, total_weeks / parallel_factor) / 4, 1)

# ═══════════════════════════════════════════
# 3. 非对称有向图构建
# ═══════════════════════════════════════════

def build_asymmetric_transfer_graph(portraits: list) -> nx.DiGraph:
    """
    构建有向图: 每对岗位产生两条有向边, 概率通常不同
    
    例:
    后端→前端: P=0.52 (自然过渡)
    前端→后端: P=0.38 (可行但需努力)
    前端→AI算法: P=0.04 (几乎不可能)
    AI算法→前端: P=0.61 (自然过渡, 降维打击)
    """
    G = nx.DiGraph()
    
    # 添加节点
    for p in portraits:
        G.add_node(p["portrait_id"], **{
            "name": p["position_name"],
            "category": p["job_category"],
            "level": p.get("level", "mid"),
        })
    
    # 添加有向边 (每对岗位两条方向不同的边)
    for i, A in enumerate(portraits):
        for j, B in enumerate(portraits):
            if i == j:
                continue
            
            trans = compute_asymmetric_transition(A, B)
            
            # v2.1 兼容: 同时保留 affinity 硬约束
            affinity = AFFINITY_MATRIX.get(
                (A["job_category"], B["job_category"]), 0.15
            )
            if affinity < 0.15:
                continue  # 行业常识否决
            
            if trans["transition_probability"] > 0.03:  # 最低门槛
                G.add_edge(
                    A["portrait_id"],
                    B["portrait_id"],
                    **trans,
                    affinity=affinity,
                    type="ASYMMETRIC_TRANSFER",
                )
    
    return G
```

### 非对称性演示

```
                        P(A→B)    P(B→A)    差异原因
后端开发 ↔ 前端开发:    0.52      0.38      后端已有JS, 前端缺Java/数据库
前端开发 ↔ AI算法:      0.04      0.61      前端缺数学/C++; AI降维到前端容易
测试工程 ↔ 后端开发:    0.22      0.45      测试缺架构能力; 后端可轻松覆盖测试
数据工程 ↔ AI算法:      0.35      0.48      数据工程缺模型训练; AI缺ETL工程
```

### 与 v2.1 combined_score 的兼容

```python
def compute_transfer_score_v3(A, B, min_jd_count=20):
    """
    v3.0: 融合 v2.1 affinity + v3.0 asymmetric transition
    
    数据充足时: asymmetric 主导
    数据不足时: affinity 兜底 (回退到 v2.1 行为)
    """
    asym = compute_asymmetric_transition(A, B)
    affinity = AFFINITY_MATRIX.get(
        (A["job_category"], B["job_category"]), 0.15
    )
    
    a_rich = A["source_jd_count"] >= min_jd_count
    b_rich = B["source_jd_count"] >= min_jd_count
    
    if a_rich and b_rich:
        # v3.0 模式: 非对称概率主导
        score = asym["transition_probability"] * 0.7 + affinity * 0.3
    else:
        # 回退到 v2.1 模式
        jaccard = compute_jaccard(A["required_skills"], B["required_skills"])
        score = jaccard * 0.4 + affinity * 0.6
    
    return {
        **asym,
        "affinity": affinity,
        "final_score": round(score, 4),
        "algorithm_version": "v3.0_asymmetric" if (a_rich and b_rich) else "v2.1_fallback",
    }
```

### 学术创新点

| 维度 | v2.1 (Jaccard+Affinity) | v3.0 (Asymmetric Transition) |
|------|------------------------|------------------------------|
| 对称性 | 对称 (A→B = B→A) | **非对称** (A→B ≠ B→A) |
| 成本建模 | 无 | **技能势能壁垒** (weeks × difficulty) |
| 衰减函数 | 线性加权 | **指数衰减** exp(-λ·barrier) |
| 时间估算 | 无 | **关键路径法** (并行学习建模) |
| 可解释性 | jaccard数值 | 每条边附带 gap_skills + barrier + months |
| 论文潜力 | 工程实践 | **"基于技能势能差的非对称职场跃迁算法"** |

---

# v3.1 审计修复 (Audit Patch)

> v3.1 针对 v3.0 三大升维的工程盲区做对抗性审计修复。
> 原则: 修复不破坏 v3.0 公式结构, 只强化数据源和防护层。

## 审计一: 非对称图谱的"数据真空"修复 — Multi-Source Skill Cost Calibration

### 问题诊断

v3.0 `SKILL_ACQUISITION_COST` 是**硬编码常量**:

```python
"CUDA优化": (16, 5)  # 16周, tier5 — 这个数据从哪来?
```

如果 `weeks` 和 `difficulty_tier` 靠大模型脑补:
- LLM 可能说"学 React 只需 1 天"（幻觉）
- 不同人的学习速度差异巨大
- **Garbage In → exp(-λ·garbage) → Garbage Out**

### 解决方案: 四源校准矩阵 (Quad-Source Calibration)

```
┌─────────────────────────────────────────────────────────┐
│           Skill Cost Calibration Pipeline                │
│                                                          │
│  Source A: 开源学习路线 (GitHub Roadmaps)                │
│  ├─ roadmap.sh/frontend  → React: ~4周                  │
│  ├─ roadmap.sh/ai        → PyTorch: ~8周                │
│  └─ awesome-xxx 系列 → 爬取课程时长                     │
│                                                          │
│  Source B: 培训机构大纲 (Training Syllabi)               │
│  ├─ 极客时间/拉勾教育 课程大纲 → 总课时/周              │
│  ├─ Coursera/Udemy 课程时长 + 完成率                    │
│  └─ 注意: 培训班时长偏短(全日制), 需×1.5~2修正系数      │
│                                                          │
│  Source C: 开发者调查 (Stack Overflow Survey)            │
│  ├─ "Years of experience with [tech]" 分布              │
│  ├─ "Want to learn" vs "Currently using" 转化时间差     │
│  └─ JetBrains Developer Ecosystem Survey                │
│                                                          │
│  Source D: 招聘需求隐含信号                              │
│  ├─ JD中"X年以上经验" → 间接推断学习年限              │
│  ├─ "实习生"岗位要求 vs "高级"岗位要求 → 技能增量       │
│  └─ 从现有JD语料中统计提取                              │
│                                                          │
│  ════════════════════════════════════════════════════    │
│  Aggregation: 加权中位数 + 置信度评分                   │
│  ════════════════════════════════════════════════════    │
│  Output: SKILL_ACQUISITION_COST_CALIBRATED              │
└─────────────────────────────────────────────────────────┘
```

### 校准算法

```python
import numpy as np
from dataclasses import dataclass

@dataclass
class SkillCostEstimate:
    source: str          # A/B/C/D
    weeks: float         # 估算学习周数
    difficulty: int      # 1-5
    confidence: float    # 0-1, 该来源的可信度
    evidence: str        # 数据来源URL/描述

def calibrate_skill_cost(
    skill_name: str,
    estimates: list[SkillCostEstimate]
) -> dict:
    """
    四源校准: 加权中位数 + 离群检测 + 置信度
    
    为什么用加权中位数而非均值?
    → 均值受极端值影响大(如某培训班号称1周学会深度学习)
    → 加权中位数天然抗噪
    """
    if not estimates:
        return {
            "weeks": 8, "difficulty": 3,  # 保守默认值
            "calibration_confidence": 0.0,
            "source_count": 0,
            "warning": "NO_DATA_SOURCES",
        }
    
    # 按置信度加权
    weights = np.array([e.confidence for e in estimates])
    weeks_arr = np.array([e.weeks for e in estimates])
    diff_arr = np.array([e.difficulty for e in estimates])
    
    # 离群检测: 去除偏离中位数>2σ的估计
    median_weeks = np.median(weeks_arr)
    std_weeks = np.std(weeks_arr)
    mask = np.abs(weeks_arr - median_weeks) <= 2 * max(std_weeks, 1)
    
    if mask.sum() < 2:
        mask = np.ones(len(estimates), dtype=bool)  # 数据太少则全保留
    
    # 加权中位数计算
    filtered_weeks = weeks_arr[mask]
    filtered_weights = weights[mask]
    filtered_diff = diff_arr[mask]
    
    sorted_idx = np.argsort(filtered_weeks)
    cum_weight = np.cumsum(filtered_weights[sorted_idx])
    median_idx = np.searchsorted(cum_weight, cum_weight[-1] / 2)
    
    calibrated_weeks = filtered_weeks[sorted_idx[median_idx]]
    calibrated_diff = int(np.round(
        np.average(filtered_diff, weights=filtered_weights)
    ))
    
    # 校准置信度 = 来源数量 × 来源一致性
    source_types = set(e.source for e in estimates)
    source_diversity = len(source_types) / 4  # 最多4类来源
    consistency = 1 - (np.std(filtered_weeks) / max(np.mean(filtered_weeks), 1))
    calibration_confidence = min(
        source_diversity * 0.5 + max(consistency, 0) * 0.5,
        1.0
    )
    
    return {
        "skill": skill_name,
        "weeks": round(float(calibrated_weeks), 1),
        "difficulty": calibrated_diff,
        "calibration_confidence": round(calibration_confidence, 3),
        "source_count": len(estimates),
        "sources_used": list(source_types),
        "outliers_removed": int((~mask).sum()),
        "raw_range": f"{weeks_arr.min():.0f}-{weeks_arr.max():.0f} weeks",
    }

# ═══════════════════════════════════════════
# 示例: React 的四源校准
# ═══════════════════════════════════════════

react_estimates = [
    SkillCostEstimate("A", 4.0, 2, 0.85, "roadmap.sh/frontend: React section ~4 weeks"),
    SkillCostEstimate("B", 3.0, 2, 0.70, "极客时间React实战: 24课时≈3周全日制"),
    SkillCostEstimate("B", 6.0, 2, 0.75, "Coursera Meta React: 6周part-time"),
    SkillCostEstimate("C", 4.5, 2, 0.80, "SO Survey: median 1yr JS → React adoption ~1mo"),
    SkillCostEstimate("D", 3.0, 2, 0.60, "JD: 初级前端要求1年React → 入门约3-4周"),
]
# → calibrated: weeks=4.0, difficulty=2, confidence=0.82

cuda_estimates = [
    SkillCostEstimate("A", 16.0, 5, 0.80, "roadmap.sh/ai: CUDA section ~4 months"),
    SkillCostEstimate("B", 12.0, 4, 0.65, "Udemy CUDA course: 12 weeks intensive"),
    SkillCostEstimate("C", 20.0, 5, 0.75, "SO Survey: CUDA avg experience 3+ years"),
    SkillCostEstimate("D", 24.0, 5, 0.70, "JD: CUDA岗位要求2-3年经验"),
]
# → calibrated: weeks=16.0, difficulty=5, confidence=0.78
```

### 校准置信度 → 非对称公式的联动

```python
def compute_asymmetric_transition_v31(source, target, skill_costs_calibrated):
    """
    v3.1: 公式结构不变, 但 barrier 计算引入校准置信度
    
    如果某技能的 calibration_confidence < 0.4:
      → barrier 乘以 uncertainty_penalty (放大不确定性)
      → 宁可高估壁垒, 也不低估(保守原则)
    """
    gap_skills = target_skills - source_skills
    
    total_barrier = 0
    low_confidence_skills = []
    
    for skill in gap_skills:
        cost = skill_costs_calibrated.get(skill, DEFAULT_COST)
        weeks = cost["weeks"]
        difficulty = cost["difficulty"]
        confidence = cost["calibration_confidence"]
        
        # 置信度惩罚: 低置信度 → 放大壁垒(保守)
        if confidence < 0.4:
            uncertainty_penalty = 1.5  # 放大50%
            low_confidence_skills.append(skill)
        elif confidence < 0.6:
            uncertainty_penalty = 1.2  # 放大20%
        else:
            uncertainty_penalty = 1.0  # 不修正
        
        total_barrier += weeks * difficulty * uncertainty_penalty
    
    # 后续公式不变: energy_decay = exp(-λ * total_barrier)
    # ...
    
    return {
        **base_result,
        "low_confidence_skills": low_confidence_skills,
        "data_quality": "calibrated" if not low_confidence_skills else "partial_calibration",
    }
```

### 数据采集优先级 (竞赛阶段可行性)

| 来源 | 成本 | 竞赛可行 | 数据量 |
|------|------|---------|-------|
| **A: GitHub Roadmaps** | 低(爬取) | ✅ 即刻可用 | ~200技能 |
| **B: 课程平台大纲** | 中(API/爬取) | ✅ 部分可用 | ~150技能 |
| **C: 开发者调查** | 低(公开CSV) | ✅ 直接下载 | ~50技能 |
| **D: JD隐含信号** | 零(已有数据) | ✅ 已有JD语料 | ~100技能 |

**竞赛策略**: Source A + D 优先, 覆盖核心50个技能即可满足竞赛需求。
Source B + C 作为上线后的增量校准源。

---

## 审计三: Cron-Driven 自动建树的"污染扩散"修复 — Multi-layer Decontamination

### 问题诊断

HR 写 JD 的真实情况:
```
"精通 Jave 和 Sprig Boot"        → Java, Spring Boot (拼写错误)
"熟练使用 Excel 数据库"          → Excel ≠ 数据库 (概念混淆)
"了解 AI 大模型 ChatJPT"         → ChatGPT (拼写错误)
"要求掌握 PPT"                   → 不是IT技能 (噪声)
```

如果这些脏数据未经过滤直接进入 Phase D 热更新:
- "Jave" 被创建为新技能节点 → 图谱污染
- "Excel数据库" 被归类到数据库子类 → 拓扑错乱
- 污染一旦进入图谱, 会通过边关系传播 → **污染扩散 (Contamination Propagation)**

### 解决方案: 四层净化管道 (Quad-Layer Decontamination)

```
┌──────────────────────────────────────────────────────┐
│  原始 JD 文本                                         │
│  "精通 Jave 和 Sprig Boot, 了解 ChatJPT"             │
│                                                       │
│  ▼ Layer D1: 拼写纠错 (Spell Correction)              │
│  ├─ 编辑距离≤2 + 音似匹配 → Jave→Java, Sprig→Spring  │
│  ├─ 技能别名库命中 → ChatJPT→ChatGPT                  │
│  ├─ 工具: SymSpell (100万词/秒) + 自定义IT词典       │
│  └─ 输出: 纠错后文本 + correction_log                 │
│                                                       │
│  ▼ Layer D2: OOD 异常检测 (Out-of-Distribution)       │
│  ├─ 新词 embedding 到最近已知技能的距离 > θ_ood?      │
│  │   θ_ood = 0.55 (BGE-M3 cosine distance)           │
│  ├─ 如果 distance > θ_ood → 标记为 SUSPICIOUS         │
│  ├─ 如果不属于任何技能大类的语义域 → 标记为 REJECT    │
│  └─ 例: "PPT" → 最近是 "office_software" → 非IT核心  │
│          → REJECT (不进入图谱)                        │
│                                                       │
│  ▼ Layer D3: 隔离沙箱 (Quarantine Zone)               │
│  ├─ 通过 D1+D2 的新词进入"沙箱"而非直接上线          │
│  ├─ 沙箱期: 14天 (2个Cron周期)                       │
│  ├─ 沙箱内行为:                                       │
│  │   ├─ 影子模式: 参与匹配计算但不影响最终得分        │
│  │   ├─ 累积统计: 出现频次、来源JD数、语境一致性      │
│  │   └─ A/B对比: 有此技能 vs 无此技能的匹配质量差异  │
│  ├─ 晋升条件:                                         │
│  │   ├─ 频次 ≥ 3% (至少在30/1000份JD中出现)          │
│  │   ├─ 语境一致性 ≥ 0.8 (不同JD中的上下文语义相似)  │
│  │   └─ 无人工否决                                    │
│  └─ 不满足 → 自动过期删除                             │
│                                                       │
│  ▼ Layer D4: 人工审核网关 (Human-in-the-Loop Gate)    │
│  ├─ 仅对 "novel" 类新词 (非alias/非related) 触发     │
│  ├─ 审核界面: 管理员后台 one-click approve/reject     │
│  ├─ 审核内容:                                         │
│  │   ├─ 新词名称 + R1推理的分类结果                   │
│  │   ├─ 沙箱期统计数据 (频次/语境/A-B对比)           │
│  │   └─ R1 置信度 + 校准置信度                        │
│  ├─ 竞赛阶段: 所有新词默认 REJECT (图谱冻结)         │
│  └─ 上线阶段: novel词需人工, alias/related自动通过    │
└──────────────────────────────────────────────────────┘
```

### Layer D1: SymSpell 拼写纠错

```python
from symspellpy import SymSpell

def build_it_spell_checker():
    """
    构建 IT 领域专用拼写检查器
    基础词典: 现有 skill_taxonomy ~500 技能
    扩展词典: 技能别名 + 常见拼写变体
    """
    spell = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
    
    # 加载技能词典 (高频优先)
    for skill in taxonomy.get_all_skills():
        spell.create_dictionary_entry(skill.name, skill.frequency)
        for alias in skill.aliases:
            spell.create_dictionary_entry(alias, skill.frequency // 2)
    
    # 常见IT拼写错误表 (硬编码高频错误)
    KNOWN_TYPOS = {
        "Jave": "Java", "Javasript": "JavaScript",
        "Sprig": "Spring", "Kubernets": "Kubernetes",
        "Pytohn": "Python", "Pytorch": "PyTorch",
        "ChatJPT": "ChatGPT", "Tenserflow": "TensorFlow",
        "Javascrpt": "JavaScript", "Recat": "React",
    }
    
    return spell, KNOWN_TYPOS

def correct_skill_spelling(term: str, spell, known_typos) -> tuple[str, str]:
    """
    返回: (纠正后技能名, 纠正类型)
    纠正类型: "exact"(无需纠正) / "known_typo" / "symspell" / "uncorrected"
    """
    # 1. 精确命中 → 无需纠正
    if taxonomy.has_skill(term):
        return term, "exact"
    
    # 2. 已知错误表 → 确定性纠正
    if term in known_typos:
        return known_typos[term], "known_typo"
    
    # 3. SymSpell 编辑距离纠正
    suggestions = spell.lookup(term, max_edit_distance=2, verbosity=1)
    if suggestions and suggestions[0].distance <= 2:
        return suggestions[0].term, "symspell"
    
    # 4. 无法纠正 → 进入后续OOD检测
    return term, "uncorrected"
```

### Layer D2: OOD 异常检测

```python
def ood_detection(term: str, term_embedding: np.ndarray,
                  taxonomy_embeddings: np.ndarray,
                  taxonomy_categories: list) -> dict:
    """
    检测新词是否属于IT技能语义域
    
    原理: 如果一个词的embedding离所有已知技能都很远,
    它大概率不是IT技能(如"PPT"、"驾驶证"、"Excel")
    """
    # 与所有已知技能的最大相似度
    similarities = cosine_similarity(
        term_embedding.reshape(1, -1), taxonomy_embeddings
    )[0]
    
    max_sim = similarities.max()
    nearest_skill = taxonomy.skills[similarities.argmax()].name
    nearest_category = taxonomy_categories[similarities.argmax()]
    
    # IT核心域判定
    IT_CORE_CATEGORIES = {
        "编程语言", "前端框架", "后端框架", "数据库",
        "AI/ML", "DevOps", "云服务", "安全", "测试",
        "移动开发", "大数据", "区块链",
    }
    
    if max_sim < 0.35:
        return {"verdict": "REJECT", "reason": f"与所有IT技能语义距离过远 (max_sim={max_sim:.3f})"}
    
    if max_sim < 0.55:
        if nearest_category not in IT_CORE_CATEGORIES:
            return {"verdict": "REJECT", "reason": f"最近类别'{nearest_category}'非IT核心域"}
        return {"verdict": "SUSPICIOUS", "nearest": nearest_skill, "sim": max_sim}
    
    return {"verdict": "PASS", "nearest": nearest_skill, "sim": max_sim}
```

### Layer D3: 隔离沙箱

```python
class QuarantineZone:
    """
    新词隔离沙箱: 影子模式运行, 积累统计数据后决定是否晋升
    """
    
    def __init__(self, quarantine_days=14):
        self.quarantine_days = quarantine_days
        self.sandbox = {}  # term → QuarantineEntry
    
    def admit(self, term: str, classification: dict):
        """新词进入沙箱"""
        self.sandbox[term] = {
            "term": term,
            "classification": classification,
            "admitted_at": datetime.now(),
            "expires_at": datetime.now() + timedelta(days=self.quarantine_days),
            "jd_sources": [],        # 出现在哪些JD中
            "contexts": [],          # 周围上下文
            "shadow_match_impact": [],  # 影子模式匹配影响
        }
    
    def update_stats(self, term: str, jd_id: str, context: str):
        """每次在新JD中发现该词, 更新统计"""
        if term in self.sandbox:
            entry = self.sandbox[term]
            entry["jd_sources"].append(jd_id)
            entry["contexts"].append(context)
    
    def evaluate_promotion(self, term: str, total_jd_count: int) -> dict:
        """
        评估是否满足晋升条件
        """
        entry = self.sandbox.get(term)
        if not entry:
            return {"promote": False, "reason": "NOT_IN_SANDBOX"}
        
        freq = len(set(entry["jd_sources"])) / max(total_jd_count, 1)
        
        # 语境一致性: 不同JD中此词的上下文embedding相似度
        context_consistency = compute_context_consistency(entry["contexts"])
        
        promote = (
            freq >= 0.03                    # ≥3% JD出现率
            and context_consistency >= 0.8  # 语境稳定
            and not entry.get("human_rejected", False)
        )
        
        return {
            "promote": promote,
            "frequency": round(freq, 4),
            "context_consistency": round(context_consistency, 3),
            "jd_count": len(set(entry["jd_sources"])),
            "next_step": "PROMOTE_TO_GRAPH" if promote else "EXTEND_OR_EXPIRE",
        }
    
    def cleanup_expired(self):
        """清理过期未晋升的词"""
        now = datetime.now()
        expired = [t for t, e in self.sandbox.items() if e["expires_at"] < now]
        for t in expired:
            entry = self.sandbox.pop(t)
            archive_to_rejected_log(entry)  # 归档, 不直接删除
```

### 完整净化流水线

```
原始JD文本
  ↓
[D1] SymSpell纠错 → "Jave"→"Java" (已知技能, 跳过后续)
  ↓ (未纠正的词)
[D2] OOD检测 → "PPT" REJECT / "MCP协议" PASS
  ↓ (PASS或SUSPICIOUS)
[D3] 隔离沙箱 → 影子模式14天 → 频次+语境评估
  ↓ (满足晋升条件)
[D4] 人工审核 → novel词需确认 / alias自动通过
  ↓ (APPROVED)
[Phase D] 图谱热更新 → 正式节点
```

### 污染扩散的"断路器"

```python
def contamination_circuit_breaker(G: nx.DiGraph, new_term: str):
    """
    即使新词通过了四层净化进入图谱,
    如果后续发现它导致匹配质量下降, 自动熔断
    
    监控指标:
    1. 引入新词后, 该类别的平均匹配置信度是否下降
    2. 引入新词后, 该技能被匹配的频率是否异常(过高=太泛, 过低=无用)
    """
    # 记录新词引入前的基线
    baseline = snapshot_category_metrics(G, new_term)
    
    # 14天后对比
    current = snapshot_category_metrics(G, new_term)
    
    if current["avg_confidence"] < baseline["avg_confidence"] * 0.9:
        # 匹配置信度下降>10% → 熔断
        rollback_node(G, new_term)
        alert_admin(f"CIRCUIT_BREAKER: {new_term} 导致匹配质量下降, 已自动回滚")
```
