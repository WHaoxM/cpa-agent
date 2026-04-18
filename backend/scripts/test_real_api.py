"""快速测试真实 API"""
import requests

BASE = "http://localhost:5001"

print("=" * 50)
print("真实 API 测试")
print("=" * 50)

# Graph API (Neo4j)
print("\n[Graph API - Neo4j]")
r = requests.get(f"{BASE}/api/graph/overview")
print(f"  /graph/overview: {r.json().get('success')}")

r = requests.post(f"{BASE}/api/graph/query", json={"query": "Python需要哪些技能"})
print(f"  /graph/query: {r.json().get('success')}")

# Profile API (MySQL)
print("\n[Profile API - MySQL]")
r = requests.get(f"{BASE}/api/profile/student/test_student_001")
print(f"  /profile/student: {r.json().get('success')}")

r = requests.get(f"{BASE}/api/profile/job/list")
print(f"  /profile/job/list: {r.json().get('success')}")

# Health
print("\n[Health]")
r = requests.get(f"{BASE}/health")
print(f"  /health: {r.json().get('status')}")

print("\n" + "=" * 50)
print("真实 API 测试完成")
