"""
从 JSON 知识库生成 Milvus 向量文件

数据源 (已归档到 archive/early-prototypes/knowledge_base-sample/):
  - knowledge_base/01_jobs/01_filtered_jobs.json  -> jd_vectors
  - knowledge_base/02_skills/03_skill_taxonomy.json -> skill_vectors

输出:
  - knowledge_base/04_vectors/05_jd_vectors.json
  - knowledge_base/04_vectors/06_skill_vectors.json

注意: 原始 knowledge_base/ 目录已归档, 运行前需重建目录结构或从归档恢复。

用法:
  python scripts/milvus/generate_vectors.py [--dry-run] [--use-embedding]

--dry-run:       预览模式，不实际生成embedding，只检查数据结构
--use-embedding: 使用 BGE-M3 模型生成真实向量（默认使用随机向量降级）
"""

import sys
import os
import json
import argparse
import time

# generate_vectors.py 位于 backend/scripts/milvus/, 上溯 4 级到项目根目录
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'backend'))

KB_ROOT = os.path.join(PROJECT_ROOT, 'knowledge_base')
KB_JOBS = os.path.join(KB_ROOT, '01_jobs', '01_filtered_jobs.json')
KB_SKILLS = os.path.join(KB_ROOT, '02_skills', '03_skill_taxonomy.json')
KB_VECTORS = os.path.join(KB_ROOT, '04_vectors')

OUT_JD = os.path.join(KB_VECTORS, '05_jd_vectors.json')
OUT_SKILL = os.path.join(KB_VECTORS, '06_skill_vectors.json')

EMB_DIM = 1024

# Prompt 来自 config/prompts/registry.yaml:
#   - script.vector.skill_enrich.user
#   - script.vector.jd_chunk.user
from config.prompt_loader import load_prompt


def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def generate_fallback_vector():
    import random
    return [random.random() for _ in range(EMB_DIM)]


def generate_embeddings_with_bge(texts):
    try:
        sys.path.insert(0, os.path.join(PROJECT_ROOT, 'backend'))
        from app.services.embedding_service import get_embedding_service
        emb_service = get_embedding_service()
        vectors = emb_service.encode(texts, batch_size=16)
        return vectors, True
    except Exception as e:
        print(f"  BGE-M3 embedding 失败: {e}")
        print("  降级为随机向量")
        return [generate_fallback_vector() for _ in texts], False


def enrich_skill_description(skill, use_llm=False):
    name = skill.get('name', '')
    cat = skill.get('category', '')
    sub = skill.get('sub_category', '')
    tier = skill.get('tier', 1)
    heat = skill.get('heat_score', 0.5)

    aliases = skill.get('aliases', [])
    aliases_str = ', '.join(aliases) if aliases else ''

    desc = skill.get('description', '')
    if desc:
        return desc

    desc = f"{name}是{cat}领域的技能，属于{sub}子类，难度等级{tier}。"

    if skill.get('prerequisites'):
        desc += f"学习前需要掌握: {', '.join(skill['prerequisites'])}。"

    if skill.get('related_roles'):
        desc += f"适用于岗位: {', '.join(skill['related_roles'])}。"

    if aliases_str:
        desc += f"常见别名: {aliases_str}。"

    desc += f"热度评分{heat}。"

    if use_llm:
        try:
            from app.services.llm import get_llm_client
            llm = get_llm_client()
            prompt = load_prompt("script.vector.skill_enrich.user", name=name, cat=cat, sub=sub, tier=tier)
            enriched = llm.generate(prompt, temperature=0.3, max_tokens=300).strip()
            if len(enriched) > 50:
                desc = enriched
        except Exception as e:
            print(f"  LLM 丰富技能描述失败 ({name}): {e}")

    return desc


def enrich_jd_description(job, use_llm=False):
    position = job.get('position_name', '')
    company = job.get('company_name', '')
    category = job.get('job_category', '')

    required = job.get('required_skills', [])
    preferred = job.get('preferred_skills', [])
    bonus = job.get('bonus_skills', [])

    desc = f"{company}招聘{position}岗位，属于{category}类别。"

    if required:
        desc += f"必备技能: {', '.join(required[:10])}。"

    if preferred:
        desc += f"优选技能: {', '.join(preferred[:10])}。"

    if bonus:
        desc += f"加分项: {', '.join(bonus[:10])}。"

    degree = job.get('degree_requirement', '')
    exp_min = job.get('experience_min', 0)
    salary = f"{job.get('salary_min', 0)}-{job.get('salary_max', 0)}"

    if degree:
        desc += f"学历要求: {degree}。"
    if exp_min:
        desc += f"最低经验: {exp_min}年。"
    desc += f"薪资范围: {salary}元/月。"

    if use_llm:
        try:
            from app.services.llm import get_llm_client
            llm = get_llm_client()
            prompt = load_prompt("script.vector.jd_chunk.user", position=position, company=company, category=category)
            enriched = llm.generate(prompt, temperature=0.3, max_tokens=300).strip()
            if len(enriched) > 50:
                desc = enriched
        except Exception as e:
            print(f"  LLM 丰富JD描述失败 ({position}): {e}")

    return desc


def generate_skill_vectors(skills_data, use_embedding=False, use_llm=False):
    print(f"\n[1/2] 处理技能向量 (skill_vectors)")
    print(f"  源文件: {KB_SKILLS}")

    if isinstance(skills_data, dict):
        skills_data = skills_data.get('skills', [])

    print(f"  读取 {len(skills_data)} 条技能数据")

    texts = []
    metadata_list = []

    for i, skill in enumerate(skills_data):
        name = skill.get('name', f'Unknown_{i}')
        cat = skill.get('category', '')

        rich_desc = enrich_skill_description(skill, use_llm=use_llm)
        text = f"{name}。{rich_desc}"
        texts.append(text)

        metadata_list.append({
            'skill_name': name,
            'category': cat,
            'sub_category': skill.get('sub_category', ''),
            'tier': skill.get('tier', 1),
            'difficulty': skill.get('difficulty', ''),
            'heat_score': skill.get('heat_score', 0.5),
            'dimension': skill.get('dimension', 'D2'),
        })

        if (i + 1) % 50 == 0:
            print(f"  处理进度: {i + 1}/{len(skills_data)}")

    print(f"  生成 {len(texts)} 条技能描述文本")

    if use_embedding:
        print("  使用 BGE-M3 生成真实向量...")
        vectors, success = generate_embeddings_with_bge(texts)
        if not success:
            print("  降级为随机向量")
            vectors = [generate_fallback_vector() for _ in texts]
    else:
        print("  跳过真实embedding，使用随机占位向量")
        vectors = [generate_fallback_vector() for _ in texts]

    vec_data = []
    for i, (text, meta, vec) in enumerate(zip(texts, metadata_list, vectors)):
        vec_data.append({
            'id': i + 1,
            'skill_name': meta['skill_name'],
            'category': meta['category'],
            'sub_category': meta['sub_category'],
            'tier': meta['tier'],
            'difficulty': meta['difficulty'],
            'heat_score': meta['heat_score'],
            'dimension': meta['dimension'],
            'text': text,
            'embedding': vec,
        })

    print(f"  生成 {len(vec_data)} 条技能向量记录")
    return vec_data


def generate_jd_vectors(jobs_data, use_embedding=False, use_llm=False):
    print(f"\n[2/2] 处理岗位向量 (jd_vectors)")
    print(f"  源文件: {KB_JOBS}")

    if isinstance(jobs_data, dict):
        jobs_data = jobs_data.get('jobs', [])

    print(f"  读取 {len(jobs_data)} 条岗位数据")

    texts = []
    metadata_list = []

    for i, job in enumerate(jobs_data):
        job_id = job.get('job_id', f'Unknown_{i}')
        position = job.get('position_name', '')
        company = job.get('company_name', '')

        rich_desc = enrich_jd_description(job, use_llm=use_llm)
        texts.append(rich_desc)

        metadata_list.append({
            'job_id': job_id,
            'position_name': position,
            'company_name': company,
            'job_category': job.get('job_category', ''),
            'degree_requirement': job.get('degree_requirement', ''),
            'experience_min': job.get('experience_min', 0),
            'salary_min': job.get('salary_min', 0),
            'salary_max': job.get('salary_max', 0),
            'is_it_position': job.get('is_it_position', 1),
        })

        if (i + 1) % 50 == 0:
            print(f"  处理进度: {i + 1}/{len(jobs_data)}")

    print(f"  生成 {len(texts)} 条岗位描述文本")

    if use_embedding:
        print("  使用 BGE-M3 生成真实向量...")
        vectors, success = generate_embeddings_with_bge(texts)
        if not success:
            print("  降级为随机向量")
            vectors = [generate_fallback_vector() for _ in texts]
    else:
        print("  跳过真实embedding，使用随机占位向量")
        vectors = [generate_fallback_vector() for _ in texts]

    vec_data = []
    for i, (text, meta, vec) in enumerate(zip(texts, metadata_list, vectors)):
        vec_data.append({
            'id': i + 1,
            'job_id': meta['job_id'],
            'position_name': meta['position_name'],
            'company_name': meta['company_name'],
            'job_category': meta['job_category'],
            'degree_requirement': meta['degree_requirement'],
            'experience_min': meta['experience_min'],
            'salary_min': meta['salary_min'],
            'salary_max': meta['salary_max'],
            'is_it_position': meta['is_it_position'],
            'text_chunk': text,
            'embedding': vec,
        })

    print(f"  生成 {len(vec_data)} 条岗位向量记录")
    return vec_data


def save_vectors(output_path, data, schema_desc, contract_desc):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    output = {
        '_schema': schema_desc,
        '_contract': contract_desc,
        '_generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        '_count': len(data),
        'vectors': data,
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"  已保存: {output_path} ({len(data)} 条)")


def main():
    parser = argparse.ArgumentParser(description='从JSON知识库生成Milvus向量文件')
    parser.add_argument('--dry-run', action='store_true', help='预览模式，不生成向量文件')
    parser.add_argument('--use-embedding', action='store_true', help='使用BGE-M3生成真实向量')
    parser.add_argument('--use-llm', action='store_true', help='使用LLM丰富描述文本')
    args = parser.parse_args()

    print("=" * 60)
    print("知识库向量生成工具")
    print("=" * 60)
    print(f"模式: {'真实向量 (BGE-M3)' if args.use_embedding else '随机占位向量'}")
    print(f"LLM:  {'启用' if args.use_llm else '禁用'}")

    print(f"\n加载数据...")
    skills_data = load_json(KB_SKILLS)
    jobs_data = load_json(KB_JOBS)

    if args.dry_run:
        print(f"\n[预览模式]")
        print(f"  技能数据: {len(skills_data) if isinstance(skills_data, list) else len(skills_data.get('skills', []))} 条")
        print(f"  岗位数据: {len(jobs_data) if isinstance(jobs_data, list) else len(jobs_data.get('jobs', []))} 条")

        print(f"\n技能样本 (前3条):")
        sample_skills = skills_data[:3] if isinstance(skills_data, list) else skills_data.get('skills', [])[:3]
        for s in sample_skills:
            print(f"  - {s.get('name', 'N/A')} | {s.get('category', 'N/A')} | tier={s.get('tier', 'N/A')}")

        print(f"\n岗位样本 (前3条):")
        sample_jobs = jobs_data[:3] if isinstance(jobs_data, list) else jobs_data.get('jobs', [])[:3]
        for j in sample_jobs:
            print(f"  - {j.get('position_name', 'N/A')} @ {j.get('company_name', 'N/A')} | {j.get('job_category', 'N/A')}")

        return

    skill_vectors = generate_skill_vectors(
        skills_data,
        use_embedding=args.use_embedding,
        use_llm=args.use_llm,
    )

    jd_vectors = generate_jd_vectors(
        jobs_data,
        use_embedding=args.use_embedding,
        use_llm=args.use_llm,
    )

    print(f"\n保存结果...")

    save_vectors(
        OUT_SKILL,
        skill_vectors,
        schema_desc='技能向量（Milvus skill_vectors collection）',
        contract_desc='skill_vectors Collection — id(PK) / skill_name / category / sub_category / tier / embedding(1024dim, BGE-M3)',
    )

    save_vectors(
        OUT_JD,
        jd_vectors,
        schema_desc='JD 文本向量（Milvus jd_vectors collection）',
        contract_desc='jd_vectors Collection — id(PK) / job_id / position_name / text_chunk / embedding(1024dim, BGE-M3)',
    )

    print(f"\n{'=' * 60}")
    print(f"生成完成!")
    print(f"  技能向量: {len(skill_vectors)} 条 -> {OUT_SKILL}")
    print(f"  岗位向量: {len(jd_vectors)} 条 -> {OUT_JD}")
    print(f"{'=' * 60}")

    if not args.use_embedding:
        print(f"\n注意: 当前使用随机占位向量")
        print(f"运行以下命令生成真实BGE-M3向量:")
        print(f"  python scripts/milvus/generate_vectors.py --use-embedding")


if __name__ == '__main__':
    main()
