#!/usr/bin/env python3
"""
使用本地 transformers 直接加载 Qwen3.5-4B，采样统计计算机岗位
"""

import json
import re
import random
import torch
from tqdm import tqdm
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_PATH = "model/Qwen3.5-4B"
SAMPLE_SIZE = 200  # 采样数量，可根据GPU调整

def load_model():
    """加载本地模型"""
    print("正在加载模型...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_PATH,
        trust_remote_code=True,
        torch_dtype=torch.bfloat16,
        device_map="auto"
    )
    model.eval()
    print(f"模型加载完成，设备: {model.device}")
    return model, tokenizer

def simple_rule_filter(job):
    """快速规则预筛选"""
    title = str(job.get("岗位名称", ""))
    industry = str(job.get("所属行业", ""))
    desc = str(job.get("岗位详情", ""))
    
    cs_keywords = r"软件|互联网|计算机|IT|数据|人工智能|AI|前端|后端|算法|运维|全栈|网络安全|开发|测试|UI|架构|实施|程序员|工程师|技术"
    
    return bool(re.search(cs_keywords, title + industry + desc, re.IGNORECASE))

def is_cs_job_local(model, tokenizer, job):
    """使用本地模型判断是否为计算机岗位"""
    title = job.get("岗位名称", "")
    industry = job.get("所属行业", "")
    desc = job.get("岗位详情", "")[:400]
    
    prompt = f"""判断以下岗位是否属于计算机/IT/信息化类行业。
只需要回答 true 或 false。

岗位名称：{title}
所属行业：{industry}
岗位详情：{desc[:300]}...

是否属于计算机类岗位（仅回答 true/false）："""

    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=10,
            temperature=0.1,
            do_sample=False
        )
    
    answer = tokenizer.decode(outputs[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)
    answer = answer.strip().lower()
    
    return "true" in answer or "是" in answer

def main():
    # 读取数据
    print("读取 JD 数据...")
    with open("cleaning_data/jd_data.json", "r", encoding="utf-8") as f:
        jobs = json.load(f)
    
    print(f"总 JD 数量: {len(jobs)}")
    
    # 第一步：规则快速筛选
    candidates = [j for j in jobs if simple_rule_filter(j)]
    print(f"规则初筛后候选: {len(candidates)} ({len(candidates)/len(jobs)*100:.1f}%)")
    
    # 采样
    if len(candidates) > SAMPLE_SIZE:
        samples = random.sample(candidates, SAMPLE_SIZE)
    else:
        samples = candidates
    
    print(f"\n采样 {len(samples)} 条进行本地模型判断...")
    
    # 加载模型
    model, tokenizer = load_model()
    
    # 统计
    cs_count = 0
    results = []
    
    for i, job in enumerate(tqdm(samples, desc="本地模型判断")):
        try:
            is_cs = is_cs_job_local(model, tokenizer, job)
            if is_cs:
                cs_count += 1
            results.append({
                "name": job.get("岗位名称", ""),
                "is_cs": is_cs
            })
            
            # 打印前3个示例
            if i < 3:
                print(f"  示例: {job.get('岗位名称')} -> {is_cs}")
                
        except Exception as e:
            print(f"\n处理失败: {e}")
    
    # 输出统计
    print(f"\n{'='*50}")
    print(f"采样统计结果:")
    print(f"  采样数量: {len(samples)}")
    print(f"  计算机类: {cs_count} ({cs_count/len(samples)*100:.1f}%)")
    print(f"  非计算机类: {len(samples)-cs_count} ({(len(samples)-cs_count)/len(samples)*100:.1f}%)")
    
    # 推算总量
    estimated = int(len(candidates) * (cs_count / len(samples)))
    print(f"\n推算计算机类岗位总数: ~{estimated} 条")
    print(f"占总JD比例: ~{estimated/len(jobs)*100:.1f}%")
    
    # 显示一些判断示例
    print(f"\n{'='*50}")
    print("判断示例:")
    for r in results[:10]:
        mark = "✓" if r["is_cs"] else "✗"
        print(f"  {mark} {r['name']}")

if __name__ == "__main__":
    main()
