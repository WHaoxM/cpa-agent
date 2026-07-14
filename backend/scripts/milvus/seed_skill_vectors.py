"""
KB-3: LLM丰富skill描述 + BGE-M3 embedding → Milvus skill_vectors
用法: python -m scripts.milvus.seed_skill_vectors [--dry-run]
"""
import sys, os, json, argparse
# seed_skill_vectors.py 位于 backend/scripts/milvus/, 需上溯 3 级到 backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
from app.utils.logger import get_logger
from config.prompt_loader import load_prompt
logger = get_logger('cpa-agent.seed_skill_vectors')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    # 1. 读MySQL
    from app.services.db import get_mysql_client
    mysql = get_mysql_client()
    skills = mysql.execute("""
        SELECT skill_name, category, sub_category, tier, difficulty,
               heat_score, dimension, proficiency_criteria, description
        FROM skill_taxonomy ORDER BY heat_score DESC""")
    logger.info(f"MySQL读取 {len(skills)} 条技能")

    # 2. LLM丰富描述
    try:
        from app.services.llm import get_llm_client
        llm = get_llm_client()
        for i, s in enumerate(skills):
            orig = s.get('description') or f"{s['skill_name']}是{s.get('category','')}领域技能"
            prompt = load_prompt(
                "script.vector.skill_seed_enrich.user",
                name=s['skill_name'], cat=s.get('category',''),
                sub=s.get('sub_category',''), tier=s.get('tier',2), orig=orig)
            try:
                desc = llm.generate(prompt, temperature=0.3, max_tokens=400).strip()
                s['rich_desc'] = desc
                logger.info(f"[{i+1}/{len(skills)}] {s['skill_name']}: LLM OK ({len(desc)}字)")
            except Exception as e:
                logger.warning(f"[{i+1}/{len(skills)}] {s['skill_name']}: LLM失败: {e}")
                s['rich_desc'] = orig
    except Exception as e:
        logger.warning(f"LLM不可用,用原始描述: {e}")
        for s in skills:
            s['rich_desc'] = s.get('description') or s['skill_name']

    # 3. 生成embedding
    from app.services.embedding_service import get_embedding_service
    emb = get_embedding_service()
    texts = [f"{s['skill_name']}。{s['rich_desc']}" for s in skills]
    logger.info(f"生成 {len(texts)} 条BGE-M3 embedding...")
    vectors = emb.encode(texts, batch_size=16)
    for i, vec in enumerate(vectors):
        skills[i]['embedding'] = vec
    logger.info(f"Embedding完成, dim={len(vectors[0]) if vectors else 0}")

    if args.dry_run:
        out = os.path.join(os.path.dirname(__file__), 'skill_vectors_preview.json')
        preview = [{"skill_name": s['skill_name'], "text": texts[i], "dim": len(vectors[i])} for i, s in enumerate(skills)]
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(preview, f, ensure_ascii=False, indent=2)
        logger.info(f"dry-run: 预览写入 {out}")
        return

    # 4. 写入Milvus
    from app.services.db import get_milvus_client
    milvus = get_milvus_client()
    if not milvus.connect():
        logger.error("Milvus连接失败")
        return

    milvus.insert(
        collection_name="skill_vectors",
        texts=texts,
        vectors=[s['embedding'] for s in skills],
        metadata=[{
            "skill_name": s['skill_name'],
            "category": s.get('category', ''),
            "sub_category": s.get('sub_category', ''),
            "dimension": s.get('dimension', 'D2'),
            "tier": s.get('tier', 2),
            "heat_score": s.get('heat_score', 0.5),
        } for s in skills])

    # 5. 回写rich_desc到MySQL
    for s in skills:
        try:
            mysql.execute_write(
                "UPDATE skill_taxonomy SET description=:d WHERE skill_name=:n",
                {"d": s['rich_desc'], "n": s['skill_name']})
        except Exception:
            pass
    logger.info(f"完成! {len(skills)}条技能向量已写入Milvus, rich_desc已回写MySQL")


if __name__ == '__main__':
    main()
