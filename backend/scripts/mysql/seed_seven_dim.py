"""
填充七维子维度 mock 数据 (D5-D10)
基于 D1-D4 推导: D5(证书)≈D1*0.8, D6(创新)≈D4*0.9, D7(学习)≈D4*0.85,
D8(抗压)≈D3*0.7, D9(沟通)≈D3*0.8, D10(实习)≈D1*0.6
用法: python -m scripts.mysql.seed_seven_dim
"""
import sys, os, random
# seed_seven_dim.py 位于 backend/scripts/mysql/, 需上溯 3 级到 backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
from app.utils.logger import get_logger
logger = get_logger('cpa-agent.seed_seven_dim')


def clamp(v: float) -> float:
    """限制在 0-100"""
    return max(0.0, min(100.0, round(v, 1)))


def derive_sub_scores(d1: float, d2: float, d3: float, d4: float) -> dict:
    """从 D1-D4 推导 D5-D10，加 ±5 随机扰动"""
    return {
        "d5_score": clamp(d1 * 0.8 + random.uniform(-5, 5)),   # 证书资质 ≈ 基础要求
        "d6_score": clamp(d4 * 0.9 + random.uniform(-5, 5)),   # 创新能力 ≈ 发展潜力
        "d7_score": clamp(d4 * 0.85 + random.uniform(-5, 5)),  # 学习能力 ≈ 发展潜力
        "d8_score": clamp(d3 * 0.7 + random.uniform(-5, 5)),   # 抗压能力 ≈ 职业素养
        "d9_score": clamp(d3 * 0.8 + random.uniform(-5, 5)),   # 沟通能力 ≈ 职业素养
        "d10_score": clamp(d1 * 0.6 + random.uniform(-5, 5)),  # 实习经验 ≈ 基础要求
    }


def main():
    random.seed(42)
    from app.services.db import get_mysql_client
    mysql = get_mysql_client()

    # ── 1. 填充 student_portrait_4d 的 d5-d10 score ──
    students = mysql.execute(
        "SELECT id, d1_score, d2_score, d3_score, d4_score FROM student_portrait_4d"
    )
    logger.info(f"读取 {len(students)} 条学生画像")

    updated = 0
    for s in students:
        sub = derive_sub_scores(s['d1_score'], s['d2_score'], s['d3_score'], s['d4_score'])
        mysql.execute_write(
            """UPDATE student_portrait_4d
               SET d5_score=:d5, d6_score=:d6, d7_score=:d7,
                   d8_score=:d8, d9_score=:d9, d10_score=:d10
               WHERE id=:id""",
            {"id": s['id'], "d5": sub['d5_score'], "d6": sub['d6_score'],
             "d7": sub['d7_score'], "d8": sub['d8_score'],
             "d9": sub['d9_score'], "d10": sub['d10_score']}
        )
        updated += 1
    logger.info(f"学生七维 score 已更新: {updated} 条")

    # ── 2. 填充 job_portrait_4d 的 d5-d10 weight ──
    # 子维度权重：每个子维度占其父维度的 50%（另一半由父维度其他子维度承担）
    # D5(证书)→D1: 0.12, D10(实习)→D1: 0.13 (D1=0.25)
    # D6(创新)→D4: 0.10, D7(学习)→D4: 0.10 (D4=0.20)
    # D8(抗压)→D3: 0.10, D9(沟通)→D3: 0.10 (D3=0.20)
    jobs = mysql.execute("SELECT id FROM job_portrait_4d")
    logger.info(f"读取 {len(jobs)} 条岗位画像")

    job_updated = 0
    for j in jobs:
        mysql.execute_write(
            """UPDATE job_portrait_4d
               SET d5_weight=0.12, d6_weight=0.10, d7_weight=0.10,
                   d8_weight=0.10, d9_weight=0.10, d10_weight=0.13
               WHERE id=:id""",
            {"id": j['id']}
        )
        job_updated += 1
    logger.info(f"岗位七维 weight 已更新: {job_updated} 条")

    # ── 3. 验证 ──
    sample = mysql.execute(
        "SELECT student_id, d5_score, d6_score, d7_score, d8_score, d9_score, d10_score FROM student_portrait_4d LIMIT 3"
    )
    for row in sample:
        logger.info(f"验证 {row['student_id']}: D5={row['d5_score']} D6={row['d6_score']} D7={row['d7_score']} D8={row['d8_score']} D9={row['d9_score']} D10={row['d10_score']}")


if __name__ == '__main__':
    main()
