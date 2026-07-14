import json
import re
import os
import concurrent.futures
import threading
import time
from tqdm import tqdm
from openai import OpenAI

# ================= 配置区 =================
API_KEY = os.getenv("DEEPSEEK_API_KEY")
BASE_URL = "https://api.deepseek.com/v1"
MODEL_NAME = "deepseek-chat"

INPUT_FILE = "cleaning_data/jd_data.json"
CHECKPOINT_FILE = "structured_jd_checkpoint.jsonl"
OUTPUT_FILE = "structured_jd_knowledge_base.json"
MAX_WORKERS = 16

CONF_THRESHOLD = 0.75

client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=60.0, max_retries=3)
file_write_lock = threading.Lock()
stats_lock = threading.Lock()

IT_KEYWORDS_RE = re.compile(r"软件(?![事业处部门主管])|互联网|计算机|IT(?!主管)|数据(?!分析员[^\w]?普通)|人工智能|AI|机器学习|深度学习|算法(?!工程师[^\w]?传统)|运维(?!管理)|云计算|云原生|网络安全|渗透测试|全栈|前端开发|前端|后端开发|后端|嵌入式(?!式)|系统架构|数据库(?!管理员[^\w]?基础)|DevOps|SRE|测试开发|自动化测试|区块链|物联网|IOT")
SOFTWARE_SUPPORT_RE = re.compile(r"CAD|solidworks|ERP|MES|SAP|PLM|三维设计|CAE")
STRICT_BLACKLIST_RE = re.compile(r"保安|保洁|厨师|普工|操作工|缝纫|财务助理|行政前台|文员|医药代表|护士|司机|送货|搬运|包装|质检|QC[^\w]|QA[^\w]|跟单|采购|仓库|物流|生产主管|车间主任|班组长|传统制造|纺织|服装|化工|水泥|矿山|农林牧渔|酒店|餐饮|旅游|教育(?!培训)|医疗(?!信息)|临床|口腔|眼科|美容|健身|销售|客服(?!技术)")
SOFT_BLACKLIST_RE = re.compile(r"^[^\w]*(保安|保洁|厨师|普工|操作工|文员|助理|跟单|采购|仓库|物流|质检)$")
TRADITIONAL_INDUSTRY_RE = re.compile(r"制造业|传统|加工|生产")

class ProcessingStats:
    def __init__(self):
        self.total = 0
        self.pre_filter_passed = 0
        self.pre_filter_rejected = 0
        self.llm_processing = 0
        self.llm_passed = 0
        self.llm_rejected = 0
        self.errors = 0
        self._start_time = time.time()
        self._last_llm_count = 0
        self._last_time = time.time()

    def update(self, key, value=1):
        with stats_lock:
            setattr(self, key, getattr(self, key) + value)

    def get_elapsed(self):
        return time.time() - self._start_time

    def get_llm_speed(self):
        with stats_lock:
            current = self.llm_processing
            now = time.time()
            elapsed = now - self._last_time
            if elapsed > 5:
                speed = (current - self._last_llm_count) / elapsed
                self._last_llm_count = current
                self._last_time = now
                return speed
            return 0

    def format_stats(self):
        elapsed = self.get_elapsed()
        mins, secs = divmod(int(elapsed), 60)
        speed = self.get_llm_speed()
        speed_str = f"{speed:.1f}条/秒" if speed > 0 else "计算中..."
        return (
            f"[{mins}m{secs}s] LLM处理:{self.llm_processing}/{self.pre_filter_passed} | "
            f"通过:{self.llm_passed} | 拒绝:{self.llm_rejected} | 速度:{speed_str}"
        )

stats = ProcessingStats()

def rule_based_pre_filter(job):
    """
    第一阶段：硬规则初筛（严格收紧，确保非IT岗位不进入LLM阶段）
    """
    title = str(job.get("岗位名称", ""))
    industry = str(job.get("所属行业", ""))
    detail = str(job.get("岗位详情", ""))

    content_lower = (title + industry + detail).lower()

    if IT_KEYWORDS_RE.search(content_lower):
        stats.update("pre_filter_passed")
        return True

    if SOFTWARE_SUPPORT_RE.search(detail):
        if not TRADITIONAL_INDUSTRY_RE.search(industry):
            stats.update("pre_filter_passed")
            return True

    if STRICT_BLACKLIST_RE.search(content_lower) or SOFT_BLACKLIST_RE.search(title):
        stats.update("pre_filter_rejected")
        return False

    stats.update("pre_filter_rejected")
    return False

def llm_process_job(job):
    """
    第二阶段：大模型深度清洗与维度对齐
    """
    stats.update("llm_processing")

    detail = job.get("岗位详情")
    if not isinstance(detail, str) or len(detail) < 10:
        stats.update("errors")
        return {"is_valid": False, "reason": "详情缺失"}

    system_prompt = """
    你是一个资深的IT行业专家。请根据提供的岗位信息完成两项任务：

    任务 1：严格行业判定
    - 判定该岗位是否属于"计算机/信息化相关行业"。
    - 准则：必须涉及软件开发、网络架构、数据分析、AI、系统运维或专业的软件技术支持（如CAD/ERP支持）。
    - 排除：纯传统制造（如水泥、纺织）、纯行政、纯传统销售、纯医疗临床。

    任务 2：对齐系统契约画像
    - job_category: 必须在 [TECHNICAL, MANAGEMENT, GENERAL] 中选一。
    - 提取 4 个核心维度的关键点（用于雷达图）：
        1. basic (基础): 学历、专业、证书（如：本科、英语四级）。
        2. skills (技能): 硬核技术栈、编程语言、框架。
        3. literacy (素养): 软素质（如：抗压能力、团队沟通）。
        4. potential (潜力): 发展潜力（如：快速学习、创新意识）。

    必须严格以 JSON 格式输出：
    {
        "is_computer_related": true/false,
        "confidence": 0.0 to 1.0,
        "job_category": "TECHNICAL/MANAGEMENT/GENERAL",
        "dimensions": {
            "basic": [], "skills": [], "literacy": [], "potential": []
        },
        "hard_skills": [],
        "soft_skills": []
    }

    重要：dimensions 每个维度至少要有 1 个有效条目，如果该维度确实无信息则返回空数组。
    """

    user_content = f"岗位名称: {job.get('岗位名称')}\n所属行业: {job.get('所属行业')}\n详情: {detail[:1500]}"

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}],
            response_format={"type": "json_object"},
            temperature=0.1
        )

        res = json.loads(response.choices[0].message.content)

        is_computer_related = res.get("is_computer_related", False)
        confidence = res.get("confidence", 0)

        dimensions = res.get("dimensions", {})
        has_valid_dimensions = (
            isinstance(dimensions, dict) and
            (len(dimensions.get("basic", [])) >= 1 or
            len(dimensions.get("skills", [])) >= 1 or
            len(dimensions.get("literacy", [])) >= 1 or
            len(dimensions.get("potential", [])) >= 1)
        )

        if not is_computer_related:
            stats.update("llm_rejected")
            return {"is_valid": False, "reason": "LLM判定为非IT相关"}

        if confidence < CONF_THRESHOLD:
            stats.update("llm_rejected")
            return {"is_valid": False, "reason": f"置信度不足 {confidence}"}

        if not has_valid_dimensions:
            stats.update("llm_rejected")
            return {"is_valid": False, "reason": "维度信息不足"}

        job.update({
            "job_category": res.get("job_category"),
            "dimensions": dimensions,
            "hard_skills": res.get("hard_skills", []),
            "soft_skills": res.get("soft_skills", [])
        })

        job["岗位详情"] = re.sub(r'<[^>]+>', ' ', detail).strip()

        stats.update("llm_passed")
        return {"is_valid": True, "data": job}

    except Exception as e:
        stats.update("errors")
        return {"is_valid": False, "error": str(e)}

def load_processed_ids():
    processed = set()
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line)
                    if "岗位编码" in data: processed.add(data["岗位编码"])
                except: continue
    return processed

def main():
    print("=" * 60)
    print(">>> 正在初始化数据...")
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    stats.total = len(raw_data)
    processed_ids = load_processed_ids()

    print(f">>> 原始数据: {stats.total} 条")
    print(f">>> 已处理（跳过）: {len(processed_ids)} 条")

    pending_jobs = [j for j in raw_data if j.get("岗位编码") not in processed_ids]

    pre_filtered = [j for j in pending_jobs if rule_based_pre_filter(j)]

    print(f">>> 预筛选后（进入LLM）: {len(pre_filtered)} 条")
    print(f">>> 预筛拒绝: {stats.pre_filter_rejected} 条")
    print("=" * 60)

    with open(CHECKPOINT_FILE, 'a', encoding='utf-8') as f_out:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_job = {executor.submit(llm_process_job, job): job for job in pre_filtered}

            pbar = tqdm(
                concurrent.futures.as_completed(future_to_job),
                total=len(pre_filtered),
                desc="LLM维度抽取",
                dynamic_ncols=True,
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}"
            )

            for future in pbar:
                res = future.result()

                if res.get("is_valid") and "data" in res:
                    with file_write_lock:
                        f_out.write(json.dumps(res["data"], ensure_ascii=False) + '\n')
                        f_out.flush()
                elif "error" in res:
                    pass
                elif "reason" in res:
                    pass

                pbar.set_postfix_str(stats.format_stats(), refresh=True)

    print("\n" + "=" * 60)
    print(">>> 正在生成最终知识库文件...")
    final_list = []
    with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                final_list.append(json.loads(line))
            except:
                continue

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(final_list, f, ensure_ascii=False, indent=4)

    print("=" * 60)
    print(f"✅ 处理完成！")
    print(f"   最终IT岗位: {len(final_list)} 条")
    print(f"   预筛拒绝: {stats.pre_filter_rejected} 条")
    print(f"   LLM通过: {stats.llm_passed} 条")
    print(f"   LLM拒绝: {stats.llm_rejected} 条")
    print(f"   错误: {stats.errors} 条")
    print("=" * 60)

if __name__ == "__main__":
    main()
