"""
Demo API 测试脚本
不与数据库交互，只测试 API 响应格式
运行: python scripts/demo_api_test.py
"""

import requests
import json
import time
from typing import Dict, Any

BASE_URL = "http://localhost:5001"


def test_health():
    """测试健康检查"""
    print("\n[1/15] 测试 /health")
    resp = requests.get(f"{BASE_URL}/health")
    data = resp.json()
    assert resp.status_code == 200
    assert data['status'] == 'ok'
    print(f"  ✅ {data}")
    return data


def test_metrics():
    """测试性能指标"""
    print("\n[2/15] 测试 /metrics")
    resp = requests.get(f"{BASE_URL}/metrics")
    data = resp.json()
    assert resp.status_code == 200
    assert 'cpu_percent' in data
    print(f"  ✅ CPU: {data['cpu_percent']}%, Memory: {data['memory_percent']}%")


def test_pipeline_list():
    """测试管线列表"""
    print("\n[3/15] 测试 /api/pipeline/list")
    resp = requests.get(f"{BASE_URL}/api/pipeline/list")
    data = resp.json()
    assert resp.status_code == 200
    assert data['success'] == True
    assert len(data['data']) == 5
    print(f"  ✅ 5 个管线: {[p['name'] for p in data['data']]}")
    return data


def test_pipeline_trigger():
    """测试触发管线 (Mock)"""
    print("\n[4/15] 测试 /api/pipeline/trigger (Mock)")
    resp = requests.post(f"{BASE_URL}/api/pipeline/trigger", json={
        "pipeline": "L0_parse_resume",
        "student_id": "demo_student_001"
    })
    data = resp.json()
    assert resp.status_code == 200
    assert data['success'] == True
    assert 'task_id' in data['data']
    task_id = data['data']['task_id']
    print(f"  ✅ task_id: {task_id}")
    return task_id


def test_pipeline_status(task_id: str):
    """测试获取任务状态 (Mock)"""
    print(f"\n[5/15] 测试 /api/pipeline/status/{task_id[:20]}... (Mock)")
    resp = requests.get(f"{BASE_URL}/api/pipeline/status/{task_id}")
    data = resp.json()
    assert resp.status_code == 200
    assert data['success'] == True
    print(f"  ✅ status: {data['data']['status']}")


def test_graph_overview():
    """测试图谱概览"""
    print("\n[6/15] 测试 /api/graph/overview")
    resp = requests.get(f"{BASE_URL}/api/graph/overview")
    data = resp.json()
    assert resp.status_code == 200
    assert data['success'] == True
    stats = data['data']['stats']
    print(f"  ✅ 技能: {stats.get('skill_count', 0)}, 画像: {stats.get('portrait_count', 0)}")


def test_graph_query():
    """测试图谱查询"""
    print("\n[7/15] 测试 /api/graph/query")
    resp = requests.post(f"{BASE_URL}/api/graph/query", json={
        "query": "Python 后端开发需要哪些技能"
    })
    data = resp.json()
    assert resp.status_code == 200
    assert data['success'] == True
    print(f"  ✅ 返回 {len(data['data'].get('nodes', []))} 个节点")


def test_profile_student():
    """测试获取学生画像"""
    print("\n[8/15] 测试 /api/profile/student/demo_student")
    resp = requests.get(f"{BASE_URL}/api/profile/student/demo_student")
    data = resp.json()
    assert resp.status_code == 200
    assert data['success'] == True
    profile = data['data']['profile']
    print(f"  ✅ student_id: {profile.get('student_id')}")


def test_profile_student_4d():
    """测试获取学生四维画像"""
    print("\n[9/15] 测试 /api/profile/student/demo_student/4d")
    resp = requests.get(f"{BASE_URL}/api/profile/student/demo_student/4d")
    data = resp.json()
    assert resp.status_code == 200
    assert data['success'] == True
    dims = data['data']['dimensions']
    print(f"  ✅ D1: {dims.get('D1', {}).get('score')}, D2: {dims.get('D2', {}).get('score')}")


def test_profile_job_list():
    """测试岗位列表"""
    print("\n[10/15] 测试 /api/profile/job/list")
    resp = requests.get(f"{BASE_URL}/api/profile/job/list")
    data = resp.json()
    assert resp.status_code == 200
    assert data['success'] == True
    jobs = data['data'].get('jobs', [])
    print(f"  ✅ 返回 {len(jobs)} 个岗位")


def test_match_result():
    """测试匹配结果"""
    print("\n[11/15] 测试 /api/match/result/demo_student/portrait_job_001")
    resp = requests.get(f"{BASE_URL}/api/match/result/demo_student/portrait_job_001")
    data = resp.json()
    assert resp.status_code == 200
    assert data['success'] == True
    print(f"  ✅ total_score: {data['data'].get('total_score')}")


def test_skill_gap():
    """测试技能差距"""
    print("\n[12/15] 测试 /api/match/skill-gap/demo_student/portrait_job_001")
    resp = requests.get(f"{BASE_URL}/api/match/skill-gap/demo_student/portrait_job_001")
    data = resp.json()
    assert resp.status_code == 200
    assert data['success'] == True
    gaps = data['data']
    print(f"  ✅ 关键缺失: {gaps.get('critical_count')}, 快速可学: {gaps.get('quick_learn_count')}")


def test_career_recommendation():
    """测试职业推荐"""
    print("\n[13/15] 测试 /api/match/career-recommendation/demo_student")
    resp = requests.get(f"{BASE_URL}/api/match/career-recommendation/demo_student")
    data = resp.json()
    assert resp.status_code == 200
    assert data['success'] == True
    rec = data['data']
    print(f"  ✅ 推荐方向: {rec.get('recommended_dir')}")


def test_report_generate():
    """测试生成报告"""
    print("\n[14/15] 测试 /api/report/generate")
    resp = requests.post(f"{BASE_URL}/api/report/generate", json={
        "student_id": "demo_student",
        "job_portrait_id": "portrait_job_001"
    })
    data = resp.json()
    assert resp.status_code == 200
    assert data['success'] == True
    print(f"  ✅ report_id: {data['data'].get('report_id')}")


def test_report_list():
    """测试报告列表"""
    print("\n[15/15] 测试 /api/report/list")
    resp = requests.get(f"{BASE_URL}/api/report/list")
    data = resp.json()
    assert resp.status_code == 200
    assert data['success'] == True
    reports = data['data'].get('reports', [])
    print(f"  ✅ 返回 {len(reports)} 个报告")


def main():
    print("=" * 60)
    print("CPA-Agent Demo API 测试 (不与数据库交互)")
    print("=" * 60)

    try:
        test_health()
        test_metrics()
        test_pipeline_list()
        task_id = test_pipeline_trigger()
        test_pipeline_status(task_id)
        test_graph_overview()
        test_graph_query()
        test_profile_student()
        test_profile_student_4d()
        test_profile_job_list()
        test_match_result()
        test_skill_gap()
        test_career_recommendation()
        test_report_generate()
        test_report_list()

        print("\n" + "=" * 60)
        print("🎉 全部 15 个 API 测试通过!")
        print("=" * 60)

    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        raise
    except requests.exceptions.ConnectionError:
        print("\n❌ 无法连接到后端服务")
        print("请先启动后端: cd backend && python run.py")
        raise


if __name__ == "__main__":
    main()
