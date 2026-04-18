"""测试完整 Pipeline"""
import sys
sys.path.insert(0, '.')

from app.pipeline.entity_extractor.entity_pipeline import run_entity_pipeline
from app.pipeline.entity_extractor.graph_storage import GraphStorage
from app.pipeline.entity_extractor.matcher import calculate_match_score

print("=" * 60)
print("完整 Pipeline 测试")
print("=" * 60)

student_text = """
Python 后端开发工程师

熟练掌握：
- Python, Django, Flask
- MySQL, Redis, PostgreSQL
- Docker, Kubernetes
- Git, Jenkins

了解：
- Vue.js, React
- TensorFlow, 机器学习
- AWS, 阿里云

3年以上后端开发经验。
"""

job_text = """
Python 后端开发工程师

岗位要求：
- 精通 Python, Django 或 Flask
- 熟悉 MySQL, Redis
- 了解 Docker 容器化
- 有大型分布式系统经验优先

加分项：
- 熟悉 Kubernetes
- 有微服务开发经验
- 了解机器学习
"""

print("\n[1] 学生技能抽取 + 存入图谱")
print("-" * 50)
student_result = run_entity_pipeline(student_text, entity_id="test_student_001")
if student_result["success"]:
    data = student_result["data"]
    print(f"实体ID: {data['entity_id']}")
    print(f"技能数量: {data['summary']['skills_count']}")
    print("技能列表:")
    for skill in data["l1"]["skills"]:
        print(f"  - {skill['name']} ({skill['category']})")

print("\n[2] 岗位技能抽取")
print("-" * 50)
job_result = run_entity_pipeline(job_text, entity_id="test_job_001")
if job_result["success"]:
    data = job_result["data"]
    print(f"岗位技能: {data['summary']['skills_count']} 个")
    job_skills = data["l1"]["skills"]
    for skill in job_skills:
        print(f"  - {skill['name']}")

print("\n[3] 人岗匹配")
print("-" * 50)
student_skills = student_result["data"]["l1"]["skills"]
match_result = calculate_match_score(
    [{"name": s["name"], "level": "掌握" if s["confidence"] > 0.6 else "熟悉", "confidence": s["confidence"]} for s in student_skills],
    [{"name": s["name"], "level": "必须", "weight": 0.8} for s in job_skills]
)

print(f"\n匹配总分: {match_result['total_score']} ({match_result['match_level']})")
print(f"技能覆盖率: {match_result['skill_coverage']}")
print(f"已匹配: {match_result['matched_skills']}")
print(f"缺失: {match_result['missing_skills']}")

print("\n[4] 技能推荐")
print("-" * 50)
storage = GraphStorage()
student_skill_names = [s["name"] for s in student_skills]
recommendations = storage.recommend_skills(student_skill_names, limit=5)
if recommendations:
    print("推荐技能:")
    for rec in recommendations:
        print(f"  - {rec['name']} ({rec['category']}): {rec['reason']}")
else:
    print("暂无推荐 (图谱关系数据不足)")

print("\n" + "=" * 60)
print("测试完成!")
