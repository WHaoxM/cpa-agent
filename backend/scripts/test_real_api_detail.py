"""详细测试真实 API"""
import requests

BASE = "http://localhost:5001"

print("=" * 50)
print("详细 API 测试")
print("=" * 50)

# Graph API
print("\n[1] Graph Overview")
r = requests.get(f"{BASE}/api/graph/overview")
data = r.json()
print(f"    success: {data.get('success')}")
if data.get('data'):
    print(f"    stats: {data.get('data', {}).get('stats')}")

print("\n[2] Graph Query (自然语言)")
r = requests.post(f"{BASE}/api/graph/query", json={"query": "Python需要哪些技能"})
data = r.json()
print(f"    success: {data.get('success')}")
if not data.get('success'):
    print(f"    error: {data.get('error')}")
else:
    print(f"    nodes: {len(data.get('data', {}).get('nodes', []))}")

print("\n[3] Profile Student (stu_demo_001)")
r = requests.get(f"{BASE}/api/profile/student/stu_demo_001")
data = r.json()
print(f"    success: {data.get('success')}")
if data.get('success'):
    print(f"    name: {data.get('data', {}).get('name')}")
else:
    print(f"    error: {data.get('error')}")

print("\n[4] Profile Job List")
r = requests.get(f"{BASE}/api/profile/job/list")
data = r.json()
print(f"    success: {data.get('success')}")
if data.get('success'):
    jobs = data.get('data', [])
    print(f"    jobs count: {len(jobs)}")

print("\n[5] Pipeline List")
r = requests.get(f"{BASE}/api/pipeline/list")
print(f"    status: {r.status_code}")
print(f"    content type: {r.headers.get('content-type', '')}")
if 'application/json' in r.headers.get('content-type', ''):
    data = r.json()
    print(f"    success: {data.get('success')}")
    if data.get('success'):
        print(f"    pipelines: {len(data.get('data', []))}")
else:
    print(f"    response: {r.text[:200]}")

print("\n[6] Match Result")
r = requests.get(f"{BASE}/api/match/result/stu_demo_001/portrait_job_001")
data = r.json()
print(f"    success: {data.get('success')}")
if data.get('success'):
    print(f"    total_score: {data.get('data', {}).get('total_score')}")

print("\n" + "=" * 50)
