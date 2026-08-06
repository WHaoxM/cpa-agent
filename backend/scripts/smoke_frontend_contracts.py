"""
前端主路径契约 — 真库 HTTP 实测（CPA_DEMO_MODE=false）

固定身份: student_001 / user_id=student_001
重型排除: L0-L4 真管线、向量重建、真 LLM（Agent 使用 mode=demo）
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

BASE = os.environ.get("CPA_SMOKE_BASE", "http://127.0.0.1:5001").rstrip("/")
USER = "student_001"
STUDENT = "student_001"
JOB = "job_001"
REPORT = "report_001"
COURSE = "course_001"


@dataclass
class CaseResult:
    name: str
    method: str
    path: str
    ok: bool
    detail: str
    status: int = 0
    body_preview: str = ""


@dataclass
class Suite:
    results: list[CaseResult] = field(default_factory=list)

    def add(self, r: CaseResult) -> None:
        self.results.append(r)
        mark = "PASS" if r.ok else "FAIL"
        print(f"[{mark}] {r.name} {r.method} {r.path} -> {r.status} {r.detail}")


def request(
    method: str,
    path: str,
    body: Optional[dict] = None,
    expect_success: bool = True,
) -> tuple[int, Any, str]:
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            return status, payload, ""
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        return e.code, payload, str(e)
    except Exception as e:
        return 0, None, str(e)


def check_success(payload: Any) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, f"non-json: {type(payload)}"
    if payload.get("success") is True:
        src = payload.get("source")
        data = payload.get("data")
        if src is None and isinstance(data, dict):
            src = data.get("source")
        if src == "demo_fallback":
            return False, "unexpected demo_fallback under real-DB mode"
        return True, f"ok source={src or 'runtime'}"
    return False, f"success!=true error={payload.get('error')}"


def main() -> int:
    suite = Suite()

    # health
    status, payload, err = request("GET", "/health")
    suite.add(CaseResult(
        "health", "GET", "/health",
        ok=status == 200 and isinstance(payload, dict) and payload.get("status") == "ok",
        detail=err or str(payload),
        status=status,
    ))

    # ensure Neo4j ability/course package loaded (B+C: empty graph = FAIL later)
    status, payload, err = request("POST", "/api/admin/graph/import-ability-course", {})
    ok_s, detail = check_success(payload)
    suite.add(CaseResult(
        "admin.graph.import", "POST", "/api/admin/graph/import-ability-course",
        ok=status == 200 and ok_s and isinstance((payload or {}).get("data"), dict),
        detail=detail if status else err,
        status=status,
    ))

    cases = [
        ("career.landscape", "GET", "/api/career/landscape", None),
        ("career.insights", "GET", "/api/career/insights?limit=5", None),
        ("agent.chat.demo", "POST", "/api/agent/chat", {"message": "你好", "mode": "demo"}),
        ("pipeline.list", "GET", "/api/pipeline/list", None),
        ("profile.student.4d", "GET", f"/api/profile/student/{STUDENT}/4d", None),
        ("profile.job", "GET", f"/api/profile/job/{JOB}", None),
        ("report.list", "GET", f"/api/report/list?student_id={STUDENT}", None),
        ("report.detail", "GET", f"/api/report/{REPORT}", None),
        ("learning.courses", "GET", "/api/learning/courses?limit=5", None),
        ("learning.course", "GET", f"/api/learning/courses/{COURSE}", None),
        ("learning.progress.get", "GET", f"/api/learning/learning/progress/{USER}", None),
        ("favorites.saved-jobs.get", "GET", f"/api/saved-jobs?user_id={USER}", None),
        ("favorites.target-roles.get", "GET", f"/api/target-roles?user_id={USER}", None),
        ("favorites.role-market", "GET", "/api/target-role-market/" + urllib.parse.quote("后端开发"), None),
        ("admin.dataset.list", "GET", "/api/admin/dataset", None),
        ("admin.kb.list", "GET", "/api/admin/knowledge-base", None),
        ("career.role-intro", "GET", "/api/career/role-intro?role=" + urllib.parse.quote("前端开发"), None),
        ("messages.list", "GET", f"/api/messages?user_id={USER}", None),
        ("ability.graph", "GET", "/api/ability-graph?role=" + urllib.parse.quote("前端开发"), None),
        (
            "learning.course-system-graph",
            "GET",
            "/api/learning/course-system-graph?role=" + urllib.parse.quote("前端开发"),
            None,
        ),
        # Match hybrid: retrieval only (no DB score row required)
        (
            "match.hybrid-candidates",
            "POST",
            "/api/match/hybrid-candidates",
            {"skills": ["Python", "SQL"], "preferred_role": "后端开发", "top_k": 3},
        ),
    ]

    for name, method, path, body in cases:
        status, payload, err = request(method, path, body)
        if name == "agent.chat.demo":
            ok = (
                status == 200
                and isinstance(payload, dict)
                and payload.get("success") is True
                and (payload.get("data") or {}).get("engine") == "demo"
            )
            detail = "demo body allowed (heavy LLM excluded)" if ok else (err or str(payload)[:200])
        elif name == "career.role-intro":
            data = (payload or {}).get("data") if isinstance(payload, dict) else None
            ok = (
                status == 200
                and isinstance(payload, dict)
                and payload.get("success") is True
                and isinstance(data, dict)
                and bool(data.get("title"))
                and isinstance(data.get("skills"), list)
            )
            detail = "role-intro has title+skills" if ok else (err or str(payload)[:200])
        elif name == "messages.list":
            data = (payload or {}).get("data") if isinstance(payload, dict) else None
            ok = (
                status == 200
                and isinstance(payload, dict)
                and payload.get("success") is True
                and isinstance(data, list)
                and len(data) >= 1
                and bool(data[0].get("id"))
            )
            detail = f"messages count={len(data) if isinstance(data, list) else 0}" if ok else (err or str(payload)[:200])
        elif name in ("ability.graph", "learning.course-system-graph"):
            data = (payload or {}).get("data") if isinstance(payload, dict) else None
            ok = (
                status == 200
                and isinstance(payload, dict)
                and payload.get("success") is True
                and isinstance(data, dict)
                and isinstance(data.get("nodes"), list)
                and len(data["nodes"]) >= 1
            )
            detail = f"nodes={len(data['nodes']) if isinstance(data, dict) and isinstance(data.get('nodes'), list) else 0}" if ok else (err or str(payload)[:200])
        elif name == "learning.course":
            ok_s, detail = check_success(payload)
            data = (payload or {}).get("data") if isinstance(payload, dict) else None
            chapters = (data or {}).get("chapters") if isinstance(data, dict) else None
            ok = status == 200 and ok_s and isinstance(chapters, list)
            detail = f"chapters={len(chapters) if isinstance(chapters, list) else 'missing'}" if ok else (err or detail)
        elif name == "match.hybrid-candidates":
            data = (payload or {}).get("data") if isinstance(payload, dict) else None
            cands = (data or {}).get("candidates") if isinstance(data, dict) else None
            ok = (
                status == 200
                and isinstance(payload, dict)
                and payload.get("success") is True
                and isinstance(data, dict)
                and isinstance(cands, list)
                and "total_score" not in data  # retrieve must not own match score
            )
            detail = f"candidates={len(cands) if isinstance(cands, list) else 0}" if ok else (err or str(payload)[:200])
        else:
            ok_s, detail = check_success(payload)
            ok = status == 200 and ok_s
            if err and not ok:
                detail = err
        suite.add(CaseResult(name, method, path, ok, detail, status=status, body_preview=str(payload)[:180]))

    # Match explain/result: 200+authoritative OR 404 (no seeded match_result)
    for name, path in (
        ("match.result", f"/api/match/result/{STUDENT}/{JOB}"),
        ("match.explain", f"/api/match/explain/{STUDENT}/{JOB}"),
        ("match.skill-gap", f"/api/match/skill-gap/{STUDENT}/{JOB}"),
    ):
        status, payload, err = request("GET", path)
        data = (payload or {}).get("data") if isinstance(payload, dict) else None
        if status == 404:
            ok = isinstance(payload, dict) and payload.get("success") is False
            detail = "no seeded row (acceptable)"
        elif name == "match.explain":
            ok = (
                status == 200
                and isinstance(payload, dict)
                and payload.get("success") is True
                and isinstance(data, dict)
                and data.get("scores_authoritative") is True
            )
            detail = "explain scores_authoritative" if ok else (err or str(payload)[:200])
        else:
            ok_s, detail = check_success(payload)
            ok = status == 200 and ok_s
        suite.add(CaseResult(name, "GET", path, ok, detail, status=status, body_preview=str(payload)[:180]))

    # messages write path — 使用独立 smoke_user，避免污染 student_001 收件箱
    smoke_msg_user = "smoke_user"
    status, payload, err = request(
        "POST",
        f"/api/messages/msg_001/read?user_id={smoke_msg_user}",
        {},
    )
    ok_s, detail = check_success(payload)
    suite.add(CaseResult(
        "messages.read", "POST", "/api/messages/msg_001/read",
        ok=status == 200 and ok_s, detail=detail if status else err, status=status,
    ))

    status, payload, err = request(
        "DELETE",
        f"/api/messages/msg_003?user_id={smoke_msg_user}",
    )
    ok_s, detail = check_success(payload)
    suite.add(CaseResult(
        "messages.delete", "DELETE", "/api/messages/msg_003",
        ok=status == 200 and ok_s, detail=detail if status else err, status=status,
    ))

    status, payload, err = request("GET", f"/api/messages?user_id={smoke_msg_user}")
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    ids = {str(m.get("id")) for m in data} if isinstance(data, list) else set()
    ok = status == 200 and isinstance(payload, dict) and payload.get("success") is True and "msg_003" not in ids
    suite.add(CaseResult(
        "messages.list.after-delete", "GET", f"/api/messages?user_id={smoke_msg_user}",
        ok=ok,
        detail="msg_003 hidden for smoke_user" if ok else (err or str(payload)[:200]),
        status=status,
    ))

    # profile PATCH (dual-write)
    status, payload, err = request(
        "PATCH",
        f"/api/profile/student/{STUDENT}",
        {"signature": "smoke-signature", "sync_student_profile": True},
    )
    ok_s, detail = check_success(payload)
    suite.add(CaseResult(
        "profile.patch", "PATCH", f"/api/profile/student/{STUDENT}",
        ok=status == 200 and ok_s, detail=detail if status else err, status=status,
    ))

    # AI routes — default smoke MUST call real LLM (B)
    status, payload, err = request(
        "POST",
        "/api/agent/career-suggestions",
        {"userId": USER, "targetRoles": [{"role": "前端开发"}]},
    )
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    ok = (
        status == 200
        and isinstance(payload, dict)
        and payload.get("success") is True
        and isinstance(data, list)
        and len(data) >= 1
    )
    suite.add(CaseResult(
        "agent.career-suggestions", "POST", "/api/agent/career-suggestions",
        ok=ok, detail="suggestions ok" if ok else (err or str(payload)[:240]), status=status,
    ))

    status, payload, err = request(
        "POST",
        "/api/ai/growth-analysis",
        {"userId": STUDENT, "reportIds": [REPORT]},
    )
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    ok = (
        status == 200
        and isinstance(payload, dict)
        and payload.get("success") is True
        and isinstance(data, dict)
        and "summary" in data
        and "growthScore" in data
        and "careerSuggestions" in data
    )
    suite.add(CaseResult(
        "ai.growth-analysis", "POST", "/api/ai/growth-analysis",
        ok=ok, detail="growth analysis ok" if ok else (err or str(payload)[:240]), status=status,
    ))

    # light writes
    status, payload, err = request(
        "POST",
        "/api/target-roles",
        {"user_id": USER, "role_name": "smoke_test_role", "priority": 9},
    )
    ok_s, detail = check_success(payload)
    suite.add(CaseResult(
        "favorites.target-roles.post", "POST", "/api/target-roles",
        ok=status in (200, 201) and ok_s, detail=detail if status else err, status=status,
    ))
    # 契约缺口：前端 backend.ts 发 {role}，后端要 role_name —— 见 matrix 文档

    status, payload, err = request(
        "DELETE",
        f"/api/target-roles/smoke_test_role?user_id={USER}",
    )
    ok_s, detail = check_success(payload)
    suite.add(CaseResult(
        "favorites.target-roles.delete", "DELETE", "/api/target-roles/smoke_test_role",
        ok=status == 200 and ok_s, detail=detail if status else err, status=status,
    ))

    status, payload, err = request(
        "POST",
        "/api/saved-jobs",
        {
            "job_id": "smoke_job_001",
            "user_id": USER,
            "job_title": "Smoke Job",
            "company": "CPA",
        },
    )
    ok_s, detail = check_success(payload)
    suite.add(CaseResult(
        "favorites.saved-jobs.post", "POST", "/api/saved-jobs",
        ok=status in (200, 201) and ok_s, detail=detail if status else err, status=status,
    ))

    status, payload, err = request("DELETE", "/api/saved-jobs/smoke_job_001")
    ok_s, detail = check_success(payload)
    suite.add(CaseResult(
        "favorites.saved-jobs.delete", "DELETE", "/api/saved-jobs/smoke_job_001",
        ok=status == 200 and ok_s, detail=detail if status else err, status=status,
    ))

    status, payload, err = request(
        "POST",
        "/api/learning/learning/progress",
        {
            "user_id": USER,
            "course_id": COURSE,
            "chapter_id": "ch_smoke_1",
            "progress": 10,
            "completed": False,
        },
    )
    ok_s, detail = check_success(payload)
    suite.add(CaseResult(
        "learning.progress.post", "POST", "/api/learning/learning/progress",
        ok=status in (200, 201) and ok_s, detail=detail if status else err, status=status,
    ))

    # heavy excluded markers (not executed)
    print("[SKIP] report.generate — DEMO_MODE=false triggers L4 heavy pipeline")
    print("[SKIP] pipeline.trigger real — may run L0-L4 worker")
    print("[SKIP] agent.chat langgraph — real LLM heavy")

    passed = sum(1 for r in suite.results if r.ok)
    failed = [r for r in suite.results if not r.ok]
    print("\n=== SUMMARY ===")
    print(f"passed={passed} failed={len(failed)} total={len(suite.results)}")
    for r in failed:
        print(f"  - {r.name}: {r.detail}")

    out = {
        "base": BASE,
        "passed": passed,
        "failed": len(failed),
        "results": [r.__dict__ for r in suite.results],
    }
    out_path = os.path.join(os.path.dirname(__file__), "smoke_frontend_contracts_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"wrote {out_path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
