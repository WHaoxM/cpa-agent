# Layer 4: 报告生成 (Report Generation)

## 4A. 职业发展报告结构

### 报告模板 (6大章节)

```
第一章: 个人画像摘要
  ├─ 基本信息: 姓名/学校/专业/学历
  ├─ 四维画像雷达图数据: D1/D2/D3/D4分数
  ├─ 综合竞争力: 分数 + 等级(极强/强/中等/偏弱/弱)
  └─ 核心亮点: top-3 highlights (来自hr_assessment)

第二章: 目标岗位分析
  ├─ 岗位画像: 必备技能/学历/经验要求
  ├─ 行业趋势: LLM生成(基于JD数据统计)
  └─ 岗位竞争态势: 薪资范围/供需比(可选)

第三章: 人岗匹配度分析 (核心)
  ├─ 总匹配分: X分 (匹配/不匹配)
  ├─ D1 基础要求: 分数 + 学历/专业/证书对比表
  ├─ D2 职业技能: 分数 + 技能匹配详情表
  │   ├─ 精确匹配的技能列表
  │   ├─ 语义匹配的技能列表(含相似度)
  │   └─ 缺失的关键技能列表
  ├─ D3 职业素养: 分数 + 各项评估
  └─ D4 发展潜力: 分数 + 竞赛/论文/GPA证据

第四章: 职业发展路径
  ├─ 垂直晋升路径: 当前→中级→高级→管理 (含时间线)
  ├─ 横向换岗选项: 可转岗位 + 技能重叠度 + 难度
  └─ 推荐发展方向: 基于个人优势的最优路径

第五章: 差距分析与学习规划
  ├─ 技能差距总览: 关键缺失/可快速补充
  ├─ 短期计划(1-3个月):
  │   ├─ 优先补充的技能(按重要度排序)
  │   ├─ 推荐学习资源(课程/书籍/项目)
  │   └─ 里程碑目标
  ├─ 中期计划(3-12个月):
  │   ├─ 深化技能 + 项目实践
  │   ├─ 证书考取计划
  │   └─ 实习/项目经历补充
  └─ 能力提升建议: D3/D4弱项改进方案

第六章: 评估周期与指标 (R-13)
  ├─ 复评周期: 每3个月
  ├─ 量化指标:
  │   ├─ 技能掌握数增长
  │   ├─ 项目完成数
  │   ├─ 证书获取数
  │   └─ 匹配分数变化
  └─ 行动检查清单
```

## 4B. Template-first + LLM 补充策略（v2.1 延迟优化）

### 问题诊断

L0 层 1.22s 极速解析会被 L4 层长文本 LLM 生成（15-30s）拖垮，产生头重脚轻的体验。

### 核心思路: 结构由模板填充，LLM 只写"灵魂"

将报告内容分为两类：
- **确定性内容（~70%）**: 直接从 L2/L3 输出的结构化 JSON 填充模板，零延迟
- **生成性内容（~30%）**: 仅在需要个性化解读的段落调用 LLM

```python
def generate_report_v2(student, job, match_result, gap, path) -> dict:
    """
    Template-first: 结构化数据直接渲染, LLM只生成解读性文本
    目标: 总延迟 < 8s (从原来的 15-30s 降至 1/3)
    """
    report = {}
    
    # ═══════════════════════════════════════════
    # Phase 1: 模板直填 (0ms, 纯数据映射)
    # ═══════════════════════════════════════════
    
    # ch1: 雷达图数据 + 基本信息 → 100%模板
    report["ch1"] = {
        "title": "个人画像摘要",
        "basic_info": student["candidate_profile"],
        "radar_data": {
            "D1": student["D1_basic"]["dimension_score"],
            "D2": student["D2_skill"]["dimension_score"],
            "D3": student["D3_quality"]["dimension_score"],
            "D4": student["D4_potential"]["dimension_score"],
        },
        "competitiveness": student["summary"],
        "highlights": student["hr_assessment"]["highlights"][:3],
        "content": None,  # Phase 2 LLM 填充
    }
    
    # ch3: 匹配详情表 → 90%模板, LLM只写总结段
    report["ch3"] = {
        "title": "人岗匹配度分析",
        "match_result": match_result["match_result"],
        "total_score": match_result["total_score"],
        "D1_table": match_result["D1"],     # 直接渲染为对比表
        "D2_table": {                       # 技能匹配详情表
            "exact": match_result["D2"]["exact_matches"],
            "semantic": match_result["D2"]["semantic_matches"],
            "missing": match_result["D2"]["missing_required"],
            "coverage": match_result["D2"]["skill_coverage"],
        },
        "D3_scores": match_result["D3"],
        "D4_scores": match_result["D4"],
        "content": None,  # Phase 2 LLM 只写 200字解读
    }
    
    # ch4: 路径图 → 100%模板 (NetworkX数据直接渲染时间线)
    report["ch4"] = {
        "title": "职业发展路径",
        "promotion_path": path["promotion_path"],
        "lateral_options": path["lateral_options"],
        "content": None,  # Phase 2 LLM 只写方向建议
    }
    
    # ch5: 学习规划 → 70%模板
    report["ch5"] = {
        "title": "差距分析与学习规划",
        "skill_gap": gap["skill_gap"],
        "ability_gap": gap["ability_gap"],
        "short_term": gap["recommended_learning"][:5],
        "mid_term": gap["recommended_learning"][5:],
        "content": None,  # Phase 2 LLM 写个性化建议
    }
    
    # ch6: 评估指标 → 100%模板 (无需LLM)
    report["ch6"] = build_evaluation_section(gap, match_result)
    
    # ═══════════════════════════════════════════
    # Phase 2: LLM 并行生成解读性文本 (~5-8s)
    # ═══════════════════════════════════════════
    
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            "ch1": pool.submit(llm_insight, "profile_summary",
                student, max_tokens=400),
            "ch3": pool.submit(llm_insight, "match_analysis",
                match_result, max_tokens=600),
            "ch4": pool.submit(llm_insight, "career_direction",
                path, max_tokens=300),
            "ch5": pool.submit(llm_insight, "learning_advice",
                gap, max_tokens=500),
        }
        for key, future in futures.items():
            report[key]["content"] = future.result()
    
    # ch2: 行业趋势(可选, 非核心, 可异步/缓存)
    report["ch2"] = gen_job_analysis_cached(job)
    
    return report

def build_evaluation_section(gap, match_result) -> dict:
    """ch6: 纯模板生成, 不调用LLM"""
    gap_count = gap["skill_gap"]["total_gap_count"]
    return {
        "title": "评估周期与指标",
        "review_cycle": "每3个月",
        "metrics": [
            f"技能掌握数: 当前{match_result['D2']['skill_coverage']*100:.0f}% → 目标80%+",
            f"待补技能数: {gap_count}项 → 目标0项",
            "项目完成数: 目标+2个/季度",
            "证书获取: 按规划执行",
        ],
        "action_checklist": [
            f"短期优先补充: {', '.join(gap['skill_gap']['critical'][:3])}",
            f"可快速上手: {', '.join(gap['skill_gap']['quick_learn'][:3])}",
        ],
        "content": None,  # 无需LLM
    }
```

### LLM Insight Prompt（精简版, 只生成解读）

```python
def llm_insight(insight_type: str, data: dict, max_tokens: int) -> str:
    """
    LLM只生成高浓度解读, 不生成结构化内容
    每段150-400字, 总计~4次调用并行, 延迟=max(4次)≈3-5s
    """
    PROMPTS = {
        "profile_summary": """
基于以下四维画像数据, 用3-4句话概括该学生的核心竞争优势和待提升方向。
不要重复数据本身, 只给出洞察性结论。150-200字。
数据: {data_json}""",
        
        "match_analysis": """
基于以下匹配结果, 写一段专业的匹配度解读。
重点: 为什么匹配/不匹配, 最关键的技能差距是什么, 语气客观鼓励。
200-300字, 不要列表, 用段落形式。
数据: {data_json}""",
        
        "career_direction": """
基于以下职业路径数据, 给出一段个性化的发展方向建议。
综合晋升路径和换岗选项, 推荐最适合的方向并说明理由。
150-200字。
数据: {data_json}""",
        
        "learning_advice": """
基于以下技能差距, 写一段个性化的学习建议寄语。
突出优先级和学习顺序的逻辑, 语气积极务实。
200-300字。
数据: {data_json}""",
    }
    
    prompt = PROMPTS[insight_type].format(
        data_json=json.dumps(data, ensure_ascii=False)[:2000]  # 截断防超长
    )
    return llm_generate(prompt, max_tokens=max_tokens)
```

### 延迟对比

```
原方案 (v2.0):
  6次LLM调用(前3并行+后3串行) × 每次3-8s ≈ 15-30s
  
新方案 (v2.1 Template-first):
  Phase 1: 模板填充 ≈ 0ms
  Phase 2: 4次LLM并行, 每次max_tokens=300-600 ≈ 3-5s
  ch2缓存命中: 0ms (同岗位共享)
  ─────────────────────
  总计: 3-5s (降低60-80%)
```

### Streaming 渲染（前端配合）

```
前端收到 Phase 1 数据后立即渲染:
  T=0s:   骨架HTML + 雷达图 + 匹配详情表 + 路径图 → 用户已可浏览
  T=3-5s: LLM解读文本陆续到达 → 流式插入到对应占位符

实现: SSE (Server-Sent Events)
  /api/report/stream?student_id=xxx&job_id=xxx
  
  event: skeleton    → 完整的模板数据 (JSON)
  event: ch1_insight → 个人画像解读文本
  event: ch3_insight → 匹配分析解读文本
  event: ch4_insight → 职业方向建议文本
  event: ch5_insight → 学习建议寄语文本
  event: complete    → 报告生成完毕
```

## 4C. 报告润色 (R-12: 粗粒度即可)

```python
def polish_report(report: dict) -> dict:
    """
    R-12: 粗粒度润色
    v2.1: 因为LLM只生成解读段落(~1000字), 润色范围大幅缩小
    """
    # 只润色LLM生成的content字段, 不触碰模板数据
    contents = [
        report["ch1"]["content"],
        report["ch3"]["content"],
        report["ch4"]["content"],
        report["ch5"]["content"],
    ]
    combined = "\n\n---\n\n".join([c for c in contents if c])
    
    prompt = f"""请对以下报告解读段落进行粗粒度润色:
1. 检查数据引用是否与结构化数据一致
2. 统一用语风格(专业、客观、鼓励性)
3. 确保段落间逻辑连贯
4. 不要大幅改写, 保持原始含义
5. 总字数控制在1200字以内

原始段落:
{combined}
"""
    polished = llm_generate(prompt, max_tokens=2000)
    
    # 分段回写
    segments = polished.split("---")
    for i, key in enumerate(["ch1", "ch3", "ch4", "ch5"]):
        if i < len(segments):
            report[key]["content"] = segments[i].strip()
    
    return report
```

## 4D. 导出

```python
def export_report(report_data: dict, format: str = "html") -> str:
    """
    JSON → HTML/PDF 导出
    使用Jinja2模板引擎
    """
    if format == "html":
        template = jinja2_env.get_template("career_report.html")
        html = template.render(
            student=report_data["student"],
            job=report_data["job"],
            match=report_data["match_result"],
            sections=report_data["sections"],
            radar_data=report_data["radar_chart_data"],
            skill_table=report_data["skill_match_table"],
            timeline=report_data["career_timeline"],
        )
        return html
    
    elif format == "pdf":
        html = export_report(report_data, "html")
        # weasyprint 或 playwright PDF
        return html_to_pdf(html)

# HTML模板包含:
# - 四维雷达图 (ECharts/Chart.js)
# - 技能匹配详情表
# - 职业路径时间线图
# - 学习规划甘特图
```

## 4E. 完整数据流总结

```
Layer 0 输出:
  student_profile.json  — 简历结构化数据
  job_postings.json     — JD结构化数据

Layer 1 输出:
  skill_taxonomy.json   — IT技能分类体系
  job_portraits.json    — ≥10个聚合岗位画像
  career_graph.json     — 职业路径图谱(晋升+换岗)

Layer 2 输出:
  student_portrait.json — 学生四维画像(D1-D4分数+证据)
  job_portrait.json     — 岗位四维画像(D1-D4要求)

Layer 3 输出:
  match_result.json     — 四维匹配结果(分数+详情)
  skill_gap.json        — 技能差距分析
  career_path.json      — 推荐职业路径

Layer 4 输出:
  career_report.json    — 结构化报告数据
  career_report.html    — HTML格式报告
  career_report.pdf     — PDF格式报告(可选)
```
