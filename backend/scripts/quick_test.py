"""快速 API 测试"""
import requests

BASE = "http://localhost:5001"

tests = [
    # 基础
    ("/health", "Health"),
    ("/metrics", "Metrics"),

    # Pipeline
    ("/api/pipeline/list", "Pipeline List"),

    # Graph
    ("/api/graph/overview", "Graph Overview"),

    # Profile
    ("/api/profile/student/demo", "Student Profile"),
    ("/api/profile/job/list", "Job List"),

    # Match
    ("/api/match/result/demo/portrait_job_001", "Match Result"),

    # Report
    ("/api/report/list", "Report List"),

    # Learning
    ("/api/learning/courses", "Courses"),
    ("/api/learning/learning/report/demo_user", "Learning Report"),

    # Favorites
    ("/api/saved-jobs", "Saved Jobs"),
    ("/api/target-role-market/后端开发", "Target Role Market"),

    # Admin
    ("/api/admin/knowledge-base", "Knowledge Base"),
    ("/api/admin/dataset", "Job Dataset"),
]

print("=" * 50)
print("Demo API 完整测试")
print("=" * 50)

passed = 0
failed = 0

for path, name in tests:
    try:
        r = requests.get(BASE + path, timeout=5)
        data = r.json()
        if data.get('success', True):
            print(f"✅ {name}: OK")
            passed += 1
        else:
            print(f"⚠️  {name}: {data.get('error', 'error')}")
            failed += 1
    except Exception as e:
        print(f"❌ {name}: {e}")
        failed += 1

print("=" * 50)
print(f"测试完成: {passed} 通过, {failed} 失败")
