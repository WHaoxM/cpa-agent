"""测试 L2/L3 Pipeline API"""
import requests

BASE = "http://localhost:5001"

print("=" * 50)
print("L2/L3 Pipeline API 测试")
print("=" * 50)

student_skills = [
    {"name": "Python", "category": "编程语言", "confidence": 0.85},
    {"name": "Django", "category": "后端框架", "confidence": 0.75},
    {"name": "MySQL", "category": "数据库", "confidence": 0.8},
    {"name": "Redis", "category": "数据库", "confidence": 0.6},
    {"name": "Docker", "category": "DevOps", "confidence": 0.65},
]

job_skills = [
    {"name": "Python", "category": "编程语言", "level": "必须", "weight": 0.9},
    {"name": "Django", "category": "后端框架", "level": "必须", "weight": 0.8},
    {"name": "MySQL", "category": "数据库", "level": "必须", "weight": 0.85},
    {"name": "Redis", "category": "数据库", "level": "加分", "weight": 0.5},
    {"name": "Kubernetes", "category": "DevOps", "level": "加分", "weight": 0.4},
]

# 1. 学生画像
print("\n[1] L2: /api/pipeline/ext/portrait/student")
r = requests.post(f"{BASE}/api/pipeline/ext/portrait/student", json={
    "student_id": "stu_test_001",
    "skills": student_skills
})
data = r.json()
print(f"    success: {data.get('success')}")
if data.get('success'):
    p = data['data']
    print(f"    总分: {p['total_score']} ({p['level']})")
    print(f"    D1: {p['dimensions']['D1']['score']}, D2: {p['dimensions']['D2']['score']}")
    print(f"    D3: {p['dimensions']['D3']['score']}, D4: {p['dimensions']['D4']['score']}")

# 2. 岗位画像
print("\n[2] L2: /api/pipeline/ext/portrait/job")
r = requests.post(f"{BASE}/api/pipeline/ext/portrait/job", json={
    "job_id": "job_test_001",
    "position_name": "Python后端开发工程师",
    "skills": job_skills
})
data = r.json()
print(f"    success: {data.get('success')}")
if data.get('success'):
    p = data['data']
    print(f"    岗位: {p['position_name']}")
    print(f"    难度: {p['difficulty_score']} ({p['difficulty_level']})")

# 3. 人岗匹配报告
print("\n[3] L3: /api/pipeline/ext/report/match")
r = requests.post(f"{BASE}/api/pipeline/ext/report/match", json={
    "student_id": "stu_test_001",
    "student_skills": student_skills,
    "job_id": "job_test_001",
    "job_name": "Python后端开发工程师",
    "job_skills": job_skills
})
data = r.json()
print(f"    success: {data.get('success')}")
if data.get('success'):
    rpt = data['data']
    print(f"    报告ID: {rpt['report_id']}")
    print(f"    匹配总分: {rpt['summary']['match_score']} ({rpt['summary']['match_level']})")
    print(f"    结论: {rpt['summary']['conclusion']}")
    print(f"    缺失技能: {rpt['summary']['missing_count']} 个")
    print(f"    建议数: {len(rpt['recommendations'])} 条")

# 4. 职业发展报告
print("\n[4] L3: /api/pipeline/ext/report/career")
r = requests.post(f"{BASE}/api/pipeline/ext/report/career", json={
    "student_id": "stu_test_001",
    "skills": student_skills
})
data = r.json()
print(f"    success: {data.get('success')}")
if data.get('success'):
    rpt = data['data']
    print(f"    当前水平: {rpt['career_analysis']['current_level']}")
    print(f"    发展方向: {len(rpt['career_analysis']['potential_direction'])} 个")

print("\n" + "=" * 50)
print("L2/L3 测试完成!")
