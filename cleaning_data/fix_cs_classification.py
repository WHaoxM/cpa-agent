#!/usr/bin/env python3
"""
重新严格判断计算机岗位 - 修复宽松判定问题
"""
import json
import os
from tqdm import tqdm
from openai import OpenAI

API_KEY = os.getenv("DEEPSEEK_API_KEY")
client = OpenAI(api_key=API_KEY, base_url="https://api.deepseek.com/v1")

INPUT_FILE = "structured_jd_knowledge_base.json"
OUTPUT_FILE = "structured_jd_knowledge_base_filtered.json"

def strict_classify(job):
    """严格判断是否为纯计算机/IT/互联网岗位"""
    system_prompt = """你是资深IT行业HR。请严格判断岗位是否属于"纯计算机/IT/互联网/软件/信息化"行业。

判定标准（必须满足以下之一）：
1. 岗位名称明确是开发、测试、运维、算法、数据分析、架构师、产品经理(UI/UX)等
2. 所属行业是计算机软件、互联网、IT服务、人工智能、数据服务等纯IT行业
3. 日常工作主要内容是编写代码、系统设计、软件测试、技术架构等

**排除以下情况**（这些不算计算机岗位）：
- 传统行业（石油、化工、机械、制造、环保、医疗等）的技术支持/实施工程师
- 仅使用CAD/ERP/Office作为工具，但非软件研发类工作
- 硬件维修、设备调试、网络布线等偏硬件/运维实施类
- 销售技术支持、售前售后支持等非研发类

必须严格以JSON格式输出：
{"is_strict_cs": true/false, "reason": "一句话说明原因"}
"""
    user_content = f"""
岗位名称：{job.get('岗位名称', '')}
所属行业：{job.get('所属行业', '')}
岗位详情：{str(job.get('岗位详情', ''))[:300]}
硬技能：{job.get('hard_skills', [])}
"""
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        result = json.loads(response.choices[0].message.content)
        return result
    except Exception as e:
        print(f"\nError: {e}")
        return {"is_strict_cs": False, "reason": "API错误"}

def main():
    # 读取已处理的数据
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        jobs = json.load(f)
    
    print(f"总数据: {len(jobs)} 条")
    
    # 快速规则预筛（明显非IT的直接排除）
    industry_blacklist = ["石油", "石化", "化工", "环保", "医疗", "临床", "房地产", 
                          "保安", "保洁", "厨师", "普工", "销售", "制造"]
    
    candidates = []
    auto_exclude = []
    
    for job in jobs:
        industry = str(job.get("所属行业", ""))
        title = str(job.get("岗位名称", ""))
        
        # 明显非IT行业直接排除
        if any(k in industry or k in title for k in industry_blacklist):
            auto_exclude.append({**job, "reason": f"行业/标题含非IT关键词: {industry}/{title}"})
        else:
            candidates.append(job)
    
    print(f"自动排除: {len(auto_exclude)} 条")
    print(f"需要LLM判断: {len(candidates)} 条")
    
    # LLM 严格判断
    keep = []
    exclude = auto_exclude
    
    for job in tqdm(candidates, desc="严格判断中"):
        result = strict_classify(job)
        if result.get("is_strict_cs"):
            job["_reason"] = result.get("reason", "通过")
            keep.append(job)
        else:
            job["_reason"] = result.get("reason", "未通过")
            exclude.append(job)
    
    # 保存结果
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(keep, f, ensure_ascii=False, indent=4)
    
    with open("excluded_jobs.json", 'w', encoding='utf-8') as f:
        json.dump(exclude, f, ensure_ascii=False, indent=4)
    
    print(f"\n{'='*50}")
    print(f"原数据: {len(jobs)} 条")
    print(f"最终保留: {len(keep)} 条 ({len(keep)/len(jobs)*100:.1f}%)")
    print(f"排除: {len(exclude)} 条")
    print(f"\n保留文件: {OUTPUT_FILE}")
    print(f"排除文件: excluded_jobs.json")
    
    # 打印样本
    print(f"\n{'='*50}")
    print("保留样本:")
    for j in keep[:3]:
        print(f"  ✓ {j['岗位名称']} | {j['所属行业']} | {j.get('_reason', '')}")
    
    print("\n排除样本:")
    for j in exclude[:3]:
        print(f"  ✗ {j['岗位名称']} | {j['所属行业']} | {j.get('_reason', '')}")

if __name__ == "__main__":
    main()
