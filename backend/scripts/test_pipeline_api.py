"""测试 Pipeline Ext API"""
import requests

BASE = "http://localhost:5001"

print("=" * 50)
print("Pipeline Ext API 测试")
print("=" * 50)

# 1. 技能抽取
print("\n[1] /api/pipeline/ext/extract")
r = requests.post(f"{BASE}/api/pipeline/ext/extract", json={
    "text": "Python开发，熟练掌握Django、Flask、MySQL、Redis，了解Docker"
})
data = r.json()
print(f"    success: {data.get('success')}")
if data.get('success'):
    skills = data['data']['skills']
    print(f"    技能数量: {len(skills)}")
    print(f"    技能: {[s['name'] for s in skills]}")

# 2. 人岗匹配
print("\n[2] /api/pipeline/ext/match")
r = requests.post(f"{BASE}/api/pipeline/ext/match", json={
    "student_skills": [
        {"name": "Python", "level": "掌握", "confidence": 0.8},
        {"name": "Django", "level": "熟悉", "confidence": 0.7},
        {"name": "MySQL", "level": "掌握", "confidence": 0.8}
    ],
    "job_skills": [
        {"name": "Python", "level": "必须", "weight": 0.9},
        {"name": "Django", "level": "必须", "weight": 0.8},
        {"name": "MySQL", "level": "必须", "weight": 0.85},
        {"name": "Redis", "level": "加分", "weight": 0.5}
    ]
})
data = r.json()
print(f"    success: {data.get('success')}")
if data.get('success'):
    match = data['data']
    print(f"    总分: {match['total_score']} ({match['match_level']})")
    print(f"    覆盖率: {match['skill_coverage']}")
    print(f"    已匹配: {match['matched_skills']}")
    print(f"    缺失: {match['missing_skills']}")

# 3. 技能推荐
print("\n[3] /api/pipeline/ext/recommend")
r = requests.get(f"{BASE}/api/pipeline/ext/recommend", params={
    "skills": "Python,Django,MySQL",
    "limit": 3
})
data = r.json()
print(f"    success: {data.get('success')}")
if data.get('success'):
    recs = data['data']['recommendations']
    print(f"    推荐: {[r['name'] for r in recs]}")

print("\n" + "=" * 50)
print("测试完成!")
