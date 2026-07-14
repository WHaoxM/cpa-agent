#!/usr/bin/env python3
"""
使用本地 Qwen3.5 4B 统计 JD 中的计算机岗位数量
"""

import json
import re
from tqdm import tqdm

# 本地模型 API 配置
BASE_URL = "http://localhost:8000/v1"  # vLLM / llama.cpp server 默认端口
API_KEY = "not-needed"  # 本地模型通常不需要 API key

# 尝试导入 openai
try:
    from openai import OpenAI
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
except ImportError:
    print("请先安装 openai: pip install openai")
    exit(1)

def simple_rule_filter(job):
    """快速规则预筛选"""
    title = str(job.get("岗位名称", ""))
    industry = str(job.get("所属行业", ""))
    desc = str(job.get("岗位详情", ""))
    
    cs_keywords = r"软件|互联网|计算机|IT|数据|人工智能|AI|前端|后端|算法|运维|全栈|网络安全|开发|测试|UI|架构|实施|程序员|工程师|技术"
    
    if re.search(cs_keywords, title + industry + desc, re.IGNORECASE):
        return True
    return False

def ask_local_llm(job):
    """调用本地 Qwen3.5 判断是否为计算机岗位"""
    title = job.get("岗位名称", "")
    industry = job.get("所属行业", "")
    desc = job.get("岗位详情", "")[:500]  # 截断避免过长
    
    prompt = f"""判断以下岗位是否属于计算机/IT/信息化类行业。
只需要回答 true 或 false。

岗位名称：{title}
所属行业：{industry}
岗位详情：{desc[:300]}...

是否属于计算机类岗位（仅回答 true/false）："""

    try:
        response = client.chat.completions.create(
            model="qwen3.5-4b",  # 本地模型名称，根据实际调整
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=10
        )
        
        answer = response.choices[0].message.content.strip().lower()
        return "true" in answer or "是" in answer
        
    except Exception as e:
        print(f"\nAPI 错误: {e}")
        return None

def main():
    # 读取数据
    with open("cleaning_data/jd_data.json", "r", encoding="utf-8") as f:
        jobs = json.load(f)
    
    print(f"总 JD 数量: {len(jobs)}")
    
    # 第一步：规则快速筛选
    candidates = [j for j in jobs if simple_rule_filter(j)]
    print(f"规则初筛后候选: {len(candidates)} ({len(candidates)/len(jobs)*100:.1f}%)")
    
    # 第二步：本地 LLM 精筛（采样前100条测试）
    test_samples = candidates[:100]
    print(f"\n采样前 {len(test_samples)} 条进行本地 LLM 测试...")
    
    cs_count = 0
    for job in tqdm(test_samples, desc="本地模型判断中"):
        is_cs = ask_local_llm(job)
        if is_cs:
            cs_count += 1
        # 打印示例
        if job == test_samples[0]:
            print(f"\n示例判断: {job.get('岗位名称')} -> {is_cs}")
    
    print(f"\n测试结果: {cs_count}/{len(test_samples)} 是计算机类 ({cs_count/len(test_samples)*100:.1f}%)")
    
    # 推算总量
    estimated_total = int(len(candidates) * (cs_count / len(test_samples)))
    print(f"推算计算机类岗位总数: ~{estimated_total} 条")

if __name__ == "__main__":
    main()
