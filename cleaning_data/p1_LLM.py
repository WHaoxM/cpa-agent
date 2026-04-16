import json
import re
import os
import concurrent.futures
from tqdm import tqdm
from openai import OpenAI

# ================= 配置区 =================
# 官方要求：为了规范使用且避免泄露，把APIKEY写入了环境变量
# 请在终端运行前设置：export DEEPSEEK_API_KEY="你的apikey" (Windows为 set DEEPSEEK_API_KEY="...")
API_KEY = os.getenv("DEEPSEEK_API_KEY") 
BASE_URL = "https://api.deepseek.com/v1" # 替换为你使用的大模型API地址 (如阿里千问、智谱等)
MODEL_NAME = "deepseek-chat"             # 替换为具体模型名称

INPUT_FILE = "jd_data.json"
OUTPUT_FILE = "structured_jd_knowledge_base.json"
MAX_WORKERS = 10 # 并发线程数，根据你的 API 限制调整

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
# ==========================================

def rule_based_filter(job):
    """
    第一阶段：规则初筛
    作用：过滤掉明显不是计算机类的岗位，节省大模型 API 的调用成本和时间。
    """
    job_title = str(job.get("岗位名称", ""))
    industry = str(job.get("所属行业", ""))
    desc = str(job.get("岗位详情", ""))

    # 1. 白名单：明显属于计算机/信息化类的词汇
    whitelist_kw = r"软件|互联网|计算机|IT|数据|人工智能|前端|后端|算法|运维|全栈|网络安全|开发|测试|UI|架构|实施"
    
    # 2. 官方特例：虽然是传统行业，但涉及特定软件的（参考 QA.txt 里的 CAD/Solidworks 案例）
    software_kw = r"CAD|solidworks|ERP|MES|SAP|绘图软件|设计软件|系统支持"

    # 如果标题或行业在白名单内，或者描述里提到了特例软件，则保留（进入下一轮 LLM 提取）
    if re.search(whitelist_kw, job_title + industry, re.IGNORECASE) or \
       re.search(software_kw, desc, re.IGNORECASE):
        return True
    
    # 3. 明确的黑名单（纯传统行业且不涉及软件）
    blacklist_kw = r"纯销售|前台|保安|保洁|厨师|普工|临床|化工|房地产经纪"
    if re.search(blacklist_kw, job_title + industry, re.IGNORECASE):
        return False
        
    # 模糊地带默认放行，交由大模型做最终裁判
    return True

def llm_process_job(job):
    """
    第二阶段：大模型精准判断与技能抽取
    """
    system_prompt = """
    你是一个资深HR和IT行业专家。
    任务1：判断给定的岗位是否属于“计算机类信息化相关行业”。（注意：部分传统行业岗位若涉及CAD、ERP等软件使用与支持，也算作计算机类）。
    任务2：如果属于计算机类，请从岗位描述中提取具体的“硬技能(hard_skills)”和“软技能(soft_skills)”。
    
    必须严格以JSON格式输出：
    {
        "is_computer_related": true/false,
        "hard_skills": ["Python", "Vue", "MySQL", "CAD"], // 若无则为空数组
        "soft_skills": ["团队沟通", "抗压能力", "逻辑思维"]   // 若无则为空数组
    }
    """
    
    user_content = f"""
    岗位名称：{job.get('岗位名称', '')}
    所属行业：{job.get('所属行业', '')}
    岗位详情：{job.get('岗位详情', '')}
    """
    
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            response_format={"type": "json_object"},
            temperature=0.1 # 降低温度保证输出的一致性
        )
        
        result = json.loads(response.choices[0].message.content)
        return {"original_job": job, "llm_result": result}
        
    except Exception as e:
        print(f"[跳过] API请求出错：{job.get('岗位名称', '')}，错误：{e}")
        return None  # 返回None会被外层过滤

def main():
    print("开始读取原始数据...")
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        raw_data = json.load(f)
    
    print(f"共读取到 {len(raw_data)} 条原始岗位数据。")
    
    # 1. 本地规则初筛
    filtered_jobs = [job for job in raw_data if rule_based_filter(job)]
    print(f"经过规则初筛，剩余 {len(filtered_jobs)} 条疑似计算机类岗位，准备进行大模型深入提取...")
    
    # 2. 多线程并发请求大模型
    final_structured_jobs = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # 提交所有任务
        future_to_job = {executor.submit(llm_process_job, job): job for job in filtered_jobs}
        
        # 使用 tqdm 显示进度条
        for future in tqdm(concurrent.futures.as_completed(future_to_job), total=len(filtered_jobs), desc="大模型处理中"):
            res = future.result()
            if res and res.get("llm_result") and res.get("llm_result").get("is_computer_related"):
                # 将抽取的技能合并回原数据中
                job_data = res["original_job"]
                job_data["hard_skills"] = res["llm_result"].get("hard_skills", [])
                job_data["soft_skills"] = res["llm_result"].get("soft_skills", [])
                
                # 为了后续 GraphRAG 效果，清理一下冗长的 HTML 标签
                job_data["岗位详情"] = re.sub(r'<[^>]+>', ' ', job_data["岗位详情"]).strip()
                
                final_structured_jobs.append(job_data)

    # 3. 导出结果
    print(f"\n处理完成！最终确认符合赛题要求的计算机类岗位共 {len(final_structured_jobs)} 条。")
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(final_structured_jobs, f, ensure_ascii=False, indent=4)
    print(f"知识库数据已成功保存至：{OUTPUT_FILE} (可直接用于Neo4j和Milvus建库)")

if __name__ == "__main__":
    main()