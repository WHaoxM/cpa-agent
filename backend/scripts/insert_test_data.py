"""插入测试数据到 MySQL"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.db.mysql_client import MySQLClient
import json

def insert_test_data():
    print("=" * 50)
    print("插入测试数据")
    print("=" * 50)

    mysql = MySQLClient()

    students = [
        {
            "student_id": "stu_demo_001",
            "name": "张三",
            "phone": "138****1234",
            "email": "zhangsan@example.com",
            "degree": "本科",
            "school": "清华大学",
            "major": "计算机科学与技术",
            "graduation_year": 2025,
            "profile_json": json.dumps({"skills": ["Python", "JavaScript", "Vue.js", "MySQL", "Git"], "projects": []}),
            "data_quality": json.dumps({"accuracy": 0.95}),
            "completeness": 0.85,
            "source_file": "resume_zhangsan.pdf",
            "source_type": "pdf"
        },
        {
            "student_id": "stu_demo_002",
            "name": "李四",
            "phone": "139****5678",
            "email": "lisi@example.com",
            "degree": "硕士",
            "school": "北京大学",
            "major": "软件工程",
            "graduation_year": 2024,
            "profile_json": json.dumps({"skills": ["Python", "Java", "Spring Boot", "Redis"], "projects": []}),
            "data_quality": json.dumps({"accuracy": 0.92}),
            "completeness": 0.90,
            "source_file": "resume_lisi.pdf",
            "source_type": "pdf"
        }
    ]

    print("\n[1] 插入学生简历...")
    sql = """INSERT INTO student_profile (student_id, name, phone, email, degree, school, major, graduation_year, profile_json, data_quality, completeness, source_file, source_type) VALUES (:student_id, :name, :phone, :email, :degree, :school, :major, :graduation_year, :profile_json, :data_quality, :completeness, :source_file, :source_type)"""
    for s in students:
        try:
            mysql.execute(sql, s, commit=True)
            print(f"  ✅ {s['name']}")
        except Exception as e:
            print(f"  ⚠️  {s['name']}: {e}")

    print("\n[2] 插入岗位画像...")
    jobs = [
        {"portrait_id": "portrait_job_001", "position_name": "Python后端开发工程师", "jd_count": 150, "required_degree": "本科", "major_strict": 0, "required_skill_cnt": 8, "d1_weight": 0.25, "d2_weight": 0.35, "d3_weight": 0.20, "d4_weight": 0.20, "portrait_json": json.dumps({})},
        {"portrait_id": "portrait_job_002", "position_name": "前端开发工程师", "jd_count": 120, "required_degree": "本科", "major_strict": 0, "required_skill_cnt": 6, "d1_weight": 0.25, "d2_weight": 0.35, "d3_weight": 0.20, "d4_weight": 0.20, "portrait_json": json.dumps({})}
    ]
    sql = """INSERT INTO job_portrait_4d (portrait_id, position_name, jd_count, required_degree, major_strict, required_skill_cnt, d1_weight, d2_weight, d3_weight, d4_weight, portrait_json) VALUES (:portrait_id, :position_name, :jd_count, :required_degree, :major_strict, :required_skill_cnt, :d1_weight, :d2_weight, :d3_weight, :d4_weight, :portrait_json)"""
    for j in jobs:
        try:
            mysql.execute(sql, j, commit=True)
            print(f"  ✅ {j['position_name']}")
        except Exception as e:
            print(f"  ⚠️  {j['position_name']}: {e}")

    print("\n[3] 插入学生四维画像...")
    portraits = [
        {"student_id": "stu_demo_001", "target_job_id": "portrait_job_001", "d1_score": 88.5, "d2_score": 75.0, "d3_score": 82.0, "d4_score": 85.0, "competitiveness": 82.0, "competitiveness_lvl": "良好", "completeness": 0.85, "skill_coverage": 0.72, "skill_count": 5, "portrait_json": "{}"},
        {"student_id": "stu_demo_002", "target_job_id": "portrait_job_001", "d1_score": 92.0, "d2_score": 85.0, "d3_score": 78.0, "d4_score": 88.0, "competitiveness": 86.5, "competitiveness_lvl": "强", "completeness": 0.90, "skill_coverage": 0.85, "skill_count": 6, "portrait_json": "{}"}
    ]
    sql = """INSERT INTO student_portrait_4d (student_id, target_job_id, d1_score, d2_score, d3_score, d4_score, competitiveness, competitiveness_lvl, completeness, skill_coverage, skill_count, portrait_json) VALUES (:student_id, :target_job_id, :d1_score, :d2_score, :d3_score, :d4_score, :competitiveness, :competitiveness_lvl, :completeness, :skill_coverage, :skill_count, :portrait_json)"""
    for p in portraits:
        try:
            mysql.execute(sql, p, commit=True)
            print(f"  ✅ {p['student_id']} (竞争力: {p['competitiveness']})")
        except Exception as e:
            print(f"  ⚠️  {p['student_id']}: {e}")

    print("\n[4] 插入匹配结果...")
    matches = [
        {"student_id": "stu_demo_001", "job_portrait_id": "portrait_job_001", "d1_match": 88.5, "d2_match": 72.0, "d3_match": 78.0, "d4_match": 82.0, "total_score": 78.4, "d1_pass": 1, "skill_coverage": 0.72, "is_matched": 1, "match_level": "良好", "result_json": "{}"},
        {"student_id": "stu_demo_002", "job_portrait_id": "portrait_job_001", "d1_match": 92.0, "d2_match": 85.0, "d3_match": 75.0, "d4_match": 86.0, "total_score": 85.2, "d1_pass": 1, "skill_coverage": 0.85, "is_matched": 1, "match_level": "良好", "result_json": "{}"}
    ]
    sql = """INSERT INTO match_result (student_id, job_portrait_id, d1_match, d2_match, d3_match, d4_match, total_score, d1_pass, skill_coverage, is_matched, match_level, result_json) VALUES (:student_id, :job_portrait_id, :d1_match, :d2_match, :d3_match, :d4_match, :total_score, :d1_pass, :skill_coverage, :is_matched, :match_level, :result_json)"""
    for m in matches:
        try:
            mysql.execute(sql, m, commit=True)
            print(f"  ✅ {m['student_id']} ↔ {m['job_portrait_id']} (匹配度: {m['total_score']})")
        except Exception as e:
            print(f"  ⚠️  {m['student_id']}: {e}")

    print("\n" + "=" * 50)
    print("测试数据插入完成!")

if __name__ == "__main__":
    insert_test_data()
