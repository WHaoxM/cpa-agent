"""Opt-in real HTTP + Redis/Celery + MySQL L0 replay with fictional resume data.

Run against a Flask instance and an active matching pipeline worker (the
default ``cpa.pipeline`` queue or an isolated ``CPA_PIPELINE_QUEUE``).
``CPA_L0_PROBE_BASE`` and ``CPA_L0_PROBE_FRONTEND_BASE`` select the two URLs.
--pdf adds a fictional text PDF;
--scanned-pdf rasterizes it into an image-only PDF for a real OCR check; --browser
requires Vite and isolated Chrome CDP on 9223. With
--observe-pending, leave the worker stopped until ``pending_observed`` is
printed, then start it to let the same task finish. A nonterminal fixture is
deliberately retained rather than deleting a file that a worker may need.
Never use as a unit test or a parser mock.
"""

import io
import json
import os
from pathlib import Path
import sys
import time
import uuid

from argon2 import PasswordHasher
import requests
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.services.db import get_mysql_client


BASE = os.environ.get("CPA_L0_PROBE_BASE", "http://127.0.0.1:5002")
FRONTEND_BASE = os.environ.get("CPA_L0_PROBE_FRONTEND_BASE", "http://127.0.0.1:5175")
RESUME = """测试简历（虚构人物）
姓名：林小舟
学校：示例大学
专业：计算机科学与技术
学历：本科
求职方向：软件开发
技能：Python、Vue、SQL、Git。
项目经历：校园课程管理系统。2024 年 9 月至 2025 年 1 月，担任开发成员，
使用 Python 编写课程查询接口，用 Vue 构建选课页面，用 SQL 管理课程数据，
与另外两名同学协作完成需求讨论和功能测试。
"""


def main():
    use_scanned_pdf = "--scanned-pdf" in sys.argv[1:]
    use_pdf = "--pdf" in sys.argv[1:] or use_scanned_pdf
    use_browser = "--browser" in sys.argv[1:]
    observe_pending = "--observe-pending" in sys.argv[1:]
    screenshot_prefix = "l0-scanned-pdf" if use_scanned_pdf else "l0-text-pdf"
    if use_pdf:
        import pymupdf
        document = pymupdf.open()
        page = document.new_page()
        page.insert_font(fontname="fixture_chinese", fontfile=r"C:\Windows\Fonts\simhei.ttf")
        page.insert_text((72, 72), RESUME, fontname="fixture_chinese", fontsize=11,
                         lineheight=1.35)
        if use_scanned_pdf:
            raster = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
            scanned = pymupdf.open()
            image_page = scanned.new_page(width=page.rect.width, height=page.rect.height)
            image_page.insert_image(image_page.rect, stream=raster.tobytes("png"))
            assert not image_page.get_text("text").strip()
            content = scanned.tobytes()
            scanned.close()
        else:
            content = document.tobytes()
        document.close()
        filename = "fictional-resume.pdf"
        mime = "application/pdf"
    else:
        content = RESUME.encode("utf-8")
        filename = "fictional-resume.txt"
        mime = "text/plain"
    mysql = get_mysql_client()
    owner = f"acct_{uuid.uuid4().hex}"
    username = f"l0probe_{uuid.uuid4().hex[:12]}"
    password = f"Disposable-{uuid.uuid4().hex[:12]}-2026!"
    session = requests.Session()
    session.trust_env = False
    task_id = None
    terminal = False
    page = None
    try:
        mysql.execute(
            "INSERT INTO user_account(id,username,password_hash,name,role,status,created_at,updated_at) "
            "VALUES (:id,:username,:hash,'L0 验收夹具','student','active',NOW(),NOW())",
            {"id": owner, "username": username, "hash": PasswordHasher().hash(password)},
            commit=True,
        )
        csrf = session.get(f"{BASE}/api/auth/csrf", timeout=10).json()["csrfToken"]
        login = session.post(f"{BASE}/api/auth/login", json={"username": username,
                             "password": password}, headers={"X-CSRFToken": csrf}, timeout=10)
        login.raise_for_status()
        upload = session.post(f"{BASE}/api/profile/student/upload?async=1",
                              files={"file": (filename, io.BytesIO(content), mime)},
                              headers={"X-CSRFToken": csrf}, timeout=30)
        upload.raise_for_status()
        data = upload.json()["data"]
        assert data["student_id"] == owner and data["delivery"] == "queued", data
        task_id = data["task_id"]
        print(json.dumps({"upload": {"task_id": task_id, "delivery": data["delivery"]}},
                         ensure_ascii=False), flush=True)
        if observe_pending:
            if use_browser:
                from career_navigation_browser_probe import Page, wait_until
                page = Page("about:blank")
                for domain in ("Page", "Runtime", "Network"):
                    page.call(f"{domain}.enable")
                page.call("Emulation.setDeviceMetricsOverride", {
                    "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False,
                })
                for cookie in session.cookies:
                    page.call("Network.setCookie", {"name": cookie.name, "value": cookie.value,
                                                     "url": FRONTEND_BASE + "/"})
                page.call("Page.navigate", {"url": FRONTEND_BASE + "/app/student/career-navigation"
                                                 f"?task_id={task_id}"})
                wait_until(page, "document.querySelector('.rp-real-loading-title')?.innerText"
                                 ".includes('正在读懂这份简历')", 30)
                wait_until(page, "document.querySelector('.rp-loading-wrap .rp-evidence-warning')?"
                                 ".innerText.includes('后台暂未接手')", 35)
                page.screenshot("l0-worker-waiting-notice.png")
                page.close()
                page = None
            else:
                time.sleep(22)
            pending = session.get(f"{BASE}/api/pipeline/status/{task_id}", timeout=15)
            pending.raise_for_status()
            pending_trace = session.get(f"{BASE}/api/pipeline/trace/{task_id}", timeout=15)
            pending_trace.raise_for_status()
            pending_data = pending_trace.json()["data"]
            assert pending.json()["data"]["status"] == "pending"
            assert pending_data["task"]["current_attempt"] == 0
            assert [event["stage"] for event in pending_data["events"]] == ["file_received"]
            print(json.dumps({"pending_observed": True, "current_attempt": 0,
                              "trace_stages": ["file_received"],
                              "note": "Start matching isolated worker now to resume this queued task"},
                             ensure_ascii=False), flush=True)
        deadline = time.monotonic() + 240
        state = None
        while time.monotonic() < deadline:
            response = session.get(f"{BASE}/api/pipeline/status/{task_id}", timeout=15)
            response.raise_for_status()
            state = response.json()["data"]
            if state["status"] not in {"pending", "running"}:
                terminal = True
                break
            time.sleep(2)
        if not terminal:
            raise TimeoutError(f"L0 task did not reach terminal state: {state and state['status']}")
        trace_response = session.get(f"{BASE}/api/pipeline/trace/{task_id}", timeout=15)
        trace_response.raise_for_status()
        trace = trace_response.json()["data"]
        candidate_response = session.get(f"{BASE}/api/profile/student/candidate/{task_id}", timeout=15)
        print(json.dumps({"status": state["status"], "error": state.get("error"),
                          "candidate_http": candidate_response.status_code,
                          "trace_stages": [event["stage"] for event in trace["events"]]},
                         ensure_ascii=False), flush=True)
        candidate_response.raise_for_status()
        candidate = candidate_response.json()["data"]
        print(json.dumps({"validation": candidate["structure_validation"],
                          "source_engine": candidate["source_evidence"].get("engine"),
                          "observed_pages": candidate["source_evidence"].get("observed_pages")},
                         ensure_ascii=False), flush=True)
        assert state["status"] == "needs_input", state
        assert candidate["student_id"] == owner and candidate["structure_validation"]["ok"]
        assert candidate["source_evidence"]["blocks"]
        assert candidate["candidate_profile"].get("school") == "示例大学"
        assert candidate["candidate_profile"].get("major") == "计算机科学与技术"
        if use_scanned_pdf:
            assert candidate["source_evidence"]["engine"] == "sidecar"
            assert candidate["source_evidence"]["observed_pages"] == [1]
        assert not candidate["profile_ready"]
        assert mysql.fetch_one("SELECT 1 FROM student_profile WHERE student_id=:sid",
                               {"sid": owner}) is None
        if use_browser:
            from career_navigation_browser_probe import Page, wait_until
            from urllib.parse import quote

            page = Page("about:blank")
            for domain in ("Page", "Runtime", "Network"):
                page.call(f"{domain}.enable")
            page.call("Emulation.setDeviceMetricsOverride", {
                "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False,
            })
            for cookie in session.cookies:
                page.call("Network.setCookie", {"name": cookie.name, "value": cookie.value,
                                                 "url": FRONTEND_BASE + "/"})
            page.call("Page.navigate", {"url": FRONTEND_BASE + "/app/student/career-navigation"
                                             f"?task_id={task_id}&preferred_role={quote('前端开发')}"})
            wait_until(page, "document.querySelector('.rp-confirm-wrap .rp-parse-btn') && "
                             "document.querySelector('.rp-candidate-fields')?.innerText.includes('示例大学')", 30)
            assert page.evaluate("document.querySelector('.rp-confirm-wrap h3').innerText") == "请核对提取出的资料"
            wait_until(page, "document.querySelector('.cnip-team')?.children.length >= 5", 10)
            assert page.evaluate("""(() => { const progress=document.querySelector('.rp-right-idle-preview');
                return progress?.innerText.includes('本次简历的解析进度')
                    && !progress?.innerText.includes('执行记录')
                    && !progress?.querySelector('.cnip-trace-details, .cnip-trace-history'); })()""")
            wait_until(page, "Number(getComputedStyle(document.querySelector('.rp-left')).opacity) > .99", 5)
            page.screenshot(f"{screenshot_prefix}-confirmation.png")
            page.evaluate("document.querySelector('.rp-confirm-wrap .rp-parse-btn').click()")
            wait_until(page, "document.querySelector('.rp-self-report') && "
                             "document.querySelector('.rp-confirm-wrap h3')?.innerText.includes('继续探索与补充')", 60)
            try:
                wait_until(page, "document.querySelector('.rp-report-progress__state')?.innerText.includes('简历章节已完成')", 120)
            except TimeoutError:
                observed = page.evaluate("""(() => ({
                    title: document.querySelector('.rp-confirm-wrap h3')?.innerText,
                    report: document.querySelector('.rp-report-progress')?.innerText,
                    error: document.querySelector('.rp-parse-error')?.innerText,
                    pending: document.querySelector('.rp-portrait-pending')?.innerText,
                }))()""")
                state = session.get(f"{BASE}/api/profile/student/workbench", timeout=15)
                report_read = session.get(f"{BASE}/api/report/resume-diagnostic", timeout=15)
                print(json.dumps({"browser_timeout": observed,
                                  "workbench_http": state.status_code,
                                  "workbench": state.json() if state.ok else state.text[:500],
                                  "report_http": report_read.status_code,
                                  "report_read": report_read.json() if not report_read.ok else
                                  {"status": report_read.json().get("status")}},
                                 ensure_ascii=False), flush=True)
                raise
            wait_until(page, "document.querySelector('.rp-role-guide__list')?.innerText.includes('前端开发') "
                             "&& performance.getEntriesByType('resource').some(item => "
                             "item.name.includes('/api/profile/student/confirmed-resume/roles'))", 30)
            assert not page.evaluate("""performance.getEntriesByType('resource')
                .some(item => item.name.includes('/api/career/infer-role'))""")
            assert page.evaluate("document.querySelector('.rp-left').getBoundingClientRect().width") >= 280
            layout = page.evaluate("""(() => {
                const workspace=document.querySelector('.rp-workspace');
                const portrait=document.querySelector('.rp-right-portrait-pane');
                const actions=document.querySelector('.rp-left');
                const process=document.querySelector('.rp-right-dashboard-pane');
                return {pageScroll:workspace.scrollHeight>workspace.clientHeight,
                    portraitBottom:portrait.getBoundingClientRect().bottom,
                    actionsTop:actions.getBoundingClientRect().top,
                    actionsOverflow:getComputedStyle(actions).overflowY,
                    processVisible:!!process,
                    technicalDetails:!!document.querySelector('.rp-task-details, .tp-real__source')};
            })()""")
            assert layout["pageScroll"] and layout["actionsOverflow"] == "visible", layout
            assert layout["actionsTop"] >= layout["portraitBottom"] - 2, layout
            assert not layout["processVisible"] and not layout["technicalDetails"], layout
            page.screenshot(f"{screenshot_prefix}-confirmed.png")
            page.evaluate("document.querySelector('.rp-left').scrollIntoView({block:'start'}); true")
            page.screenshot(f"{screenshot_prefix}-next-steps.png")
            page.evaluate("document.querySelectorAll('.rp-real-view-tabs button')[1].click(); true")
            assert page.evaluate("!document.querySelector('.rp-workspace').classList.contains('rp-workspace--immersive') && !!document.querySelector('.rp-self-report')")
            wait_until(page, "!!document.querySelector('.resume-diagnostic')", 10)
            page.evaluate("document.querySelector('.rp-workspace').scrollTop = 0; true")
            page.screenshot(f"{screenshot_prefix}-report.png")
            assert not page.evaluate("!!document.querySelector('.rp-right-dashboard-pane')")
            page.evaluate("document.querySelectorAll('.rp-real-view-tabs button')[0].click(); true")
        else:
            confirm = session.post(f"{BASE}/api/profile/student/confirm",
                                   json={"task_id": task_id, "confirmed": True},
                                   headers={"X-CSRFToken": csrf}, timeout=30)
            confirm.raise_for_status()
            assert confirm.json()["data"]["resource_id"] == owner
        profile = mysql.fetch_one("SELECT profile_json FROM student_profile WHERE student_id=:sid",
                                  {"sid": owner})
        assert profile is not None
        stored = profile["profile_json"]
        if isinstance(stored, str):
            stored = json.loads(stored)
        assert stored["_resume_confirmation"]["task_id"] == task_id
        if not use_browser:
            # Deliberately issue no L2/report POST and open no frontend page.
            # The two committed intents must settle in the independent worker.
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                aggregate = session.get(f"{BASE}/api/profile/student/workbench", timeout=15)
                aggregate.raise_for_status()
                states = aggregate.json()["data"]
                if (states["portrait"]["state"] in {"ready_with_unknowns", "ready_scored"}
                        and states["resume_diagnostic"]["state"] in {"ready", "partial"}):
                    break
                time.sleep(2)
            else:
                raise TimeoutError(f"background artifacts did not settle: {states}")
        workbench = session.get(f"{BASE}/api/profile/student/workbench", timeout=15)
        workbench.raise_for_status()
        workbench_data = workbench.json()["data"]
        assert workbench_data["source_confirmation_task_id"] == task_id
        assert workbench_data["run"]["state"] == "settled"
        assert workbench_data["portrait"]["state"] in {"ready_with_unknowns", "ready_scored"}
        assert workbench_data["resume_diagnostic"]["state"] in {"ready", "partial"}
        assert workbench_data["analysis_run"]["state"] == "settled"
        portrait_job = mysql.fetch_one(
            "SELECT t.status,d.attempts FROM pipeline_task t JOIN pipeline_portrait_dispatch d "
            "ON d.task_id=t.task_id WHERE t.task_id=:tid AND t.student_id=:sid",
            {"tid": workbench_data["portrait"]["task_id"], "sid": owner})
        diagnostic_job = mysql.fetch_one(
            "SELECT t.status,d.attempts FROM pipeline_task t JOIN resume_diagnostic_dispatch d "
            "ON d.task_id=t.task_id WHERE t.task_id=:tid AND t.student_id=:sid",
            {"tid": workbench_data["analysis_run"]["diagnostic_task_id"], "sid": owner})
        report_row = mysql.fetch_one(
            "SELECT status,content_sha256 FROM resume_diagnostic_report "
            "WHERE report_id=:rid AND student_id=:sid",
            {"rid": workbench_data["resume_diagnostic"]["report_id"], "sid": owner})
        assert portrait_job and portrait_job["status"] in {"partial", "completed"} and portrait_job["attempts"] >= 1
        assert diagnostic_job and diagnostic_job["status"] in {"partial", "completed"} and diagnostic_job["attempts"] >= 1
        assert report_row and report_row["status"] == diagnostic_job["status"] and report_row["content_sha256"]
        snapshot_id = workbench_data["portrait"].get("snapshot_id")
        assert isinstance(snapshot_id, str) and snapshot_id.startswith("ps_")
        snapshot_row = mysql.fetch_one(
            "SELECT source_task_id,status,content_sha256 FROM student_portrait_snapshot "
            "WHERE snapshot_id=:snapshot_id AND student_id=:sid",
            {"snapshot_id": snapshot_id, "sid": owner})
        assert snapshot_row and snapshot_row["source_task_id"] == workbench_data["portrait"]["task_id"]
        assert snapshot_row["status"] == portrait_job["status"] and snapshot_row["content_sha256"]
        versions = session.get(f"{BASE}/api/profile/student/portrait-snapshots", timeout=15)
        versions.raise_for_status()
        assert versions.json()["data"]["current_snapshot_id"] == snapshot_id
        assert len(versions.json()["data"]["items"]) == 1
        snapshot_detail = session.get(
            f"{BASE}/api/profile/student/portrait-snapshots/{snapshot_id}", timeout=15)
        snapshot_detail.raise_for_status()
        assert snapshot_detail.json()["data"]["is_current"] is True
        assert snapshot_detail.json()["data"]["dimensions"]["D4"] is None
        if use_browser:
            current = session.get(f"{BASE}/api/profile/student/candidate/{task_id}", timeout=15)
            current.raise_for_status()
            assert current.json()["data"]["portrait_task"]["status"] == "partial"
            diagnostic = session.get(f"{BASE}/api/report/resume-diagnostic", timeout=15)
            diagnostic.raise_for_status()
            assert diagnostic.json()["data"]["status"] == "completed"
            assert workbench_data["portrait"]["state"] == "ready_with_unknowns", workbench_data
            assert workbench_data["portrait"]["scoring_eligible"] is False
            assert workbench_data["resume_diagnostic"]["state"] == "ready", workbench_data
            assert page.evaluate("document.querySelector('.tp-real__title-row h1')?.innerText") == "当前可了解的能力信息"
            assert "综合竞争力" not in page.evaluate("document.querySelector('.tp-portrait--real')?.innerText || ''")
            wait_until(page, "document.querySelector('.rp-portrait-history summary')?.innerText.includes('已保存的画像版本')", 15)
            page.evaluate("document.querySelector('.rp-portrait-history summary').click()")
            page.evaluate("document.querySelector('.rp-portrait-history button').click()")
            wait_until(page, "document.querySelector('.rp-portrait-history__detail')?.innerText.includes('当前画像版本')", 10)
            page.evaluate("document.querySelector('.rp-portrait-history').scrollIntoView({block:'center'})")
            page.screenshot(f"{screenshot_prefix}-history.png")
            page.evaluate("document.querySelector('.rp-portrait-history summary').click()")
            page.call("Emulation.setDeviceMetricsOverride", {
                "width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True,
            })
            assert not page.evaluate("!!document.querySelector('.rp-process-trigger')")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            page.screenshot(f"{screenshot_prefix}-confirmed-mobile.png")
        print(json.dumps({"confirmed": True, "candidate_fields": sorted(candidate["candidate_profile"]),
                          "evidence_blocks": len(candidate["source_evidence"]["blocks"]),
                          "browser_verified": use_browser,
                          "workbench_portrait": workbench_data["portrait"]["state"],
                          "workbench_report": workbench_data["resume_diagnostic"]["state"],
                          "background_attempts": {"portrait": portrait_job["attempts"],
                                                  "diagnostic": diagnostic_job["attempts"]}},
                         ensure_ascii=False), flush=True)
    finally:
        if page:
            page.close()
        if task_id and not terminal:
            # A running worker may still need the source file and account.
            print(f"NONTERMINAL_FIXTURE_RETAINED owner={owner} task={task_id}", flush=True)
        else:
            with mysql.engine.begin() as conn:
                if task_id:
                    row = conn.execute(text("SELECT params_json FROM pipeline_task WHERE task_id=:tid"),
                                       {"tid": task_id}).mappings().first()
                    conn.execute(text("DELETE FROM pipeline_task_trace WHERE task_id=:tid"), {"tid": task_id})
                    conn.execute(text("DELETE FROM pipeline_resume_dispatch WHERE task_id=:tid"), {"tid": task_id})
                    conn.execute(text("DELETE FROM pipeline_task_event WHERE student_id=:sid"), {"sid": owner})
                    conn.execute(text("DELETE FROM student_portrait_4d WHERE student_id=:sid"), {"sid": owner})
                    conn.execute(text("DELETE FROM resume_diagnostic_report WHERE student_id=:sid"), {"sid": owner})
                    conn.execute(text("DELETE FROM pipeline_task WHERE student_id=:sid"), {"sid": owner})
                    if row:
                        params = row["params_json"]
                        if isinstance(params, str):
                            params = json.loads(params)
                        path = Path(params.get("file_path") or "")
                        if (path.is_file() and path.parent.name == owner
                                and path.name.startswith("resume_") and path.suffix in {".txt", ".pdf"}):
                            path.unlink()
                conn.execute(text("DELETE FROM student_profile WHERE student_id=:sid"), {"sid": owner})
                conn.execute(text("DELETE FROM user_account WHERE id=:sid"), {"sid": owner})


if __name__ == "__main__":
    main()
