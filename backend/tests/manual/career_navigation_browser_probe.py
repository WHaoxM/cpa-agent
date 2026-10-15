"""Opt-in headless Chrome proof of the confirmed-resume supplement UI.

Prerequisites: current Flask on 5002, cpa-xingtu backend-dev Vite proxying it
on 5175, and isolated headless Chrome CDP on 9223. Uses a disposable account
and controlled L0 candidate; never claims OCR/model parsing was exercised.
"""

import base64
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid
from urllib.parse import quote

import requests
import websocket
from argon2 import PasswordHasher

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.services.db import get_mysql_client


BASE = os.environ.get("CPA_BROWSER_PROBE_BASE", "http://127.0.0.1:5175")
CDP = "http://127.0.0.1:9223"
OUT = Path(__file__).resolve().parents[3] / "output" / "career-nav-browser-check"


class Page:
    def __init__(self, url):
        target = requests.put(f"{CDP}/json/new?{quote(url, safe='')}", timeout=5).json()
        self.target_id = target["id"]
        self.socket = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=15,
                                                  suppress_origin=True)
        self.next_id = 0
        # A newly opened CDP tab is not guaranteed to be foreground. Background
        # timer throttling otherwise makes UI polling appear stuck after the
        # backend has already completed the report.
        self.call("Page.bringToFront")

    def call(self, method, params=None):
        self.next_id += 1
        command_id = self.next_id
        self.socket.send(json.dumps({"id": command_id, "method": method,
                                     "params": params or {}}))
        while True:
            message = json.loads(self.socket.recv())
            if message.get("id") == command_id:
                if message.get("error"):
                    raise RuntimeError(message["error"])
                return message.get("result") or {}

    def evaluate(self, script):
        result = self.call("Runtime.evaluate", {"expression": script,
                                                  "returnByValue": True,
                                                  "awaitPromise": True})
        if result.get("exceptionDetails"):
            raise RuntimeError(result["exceptionDetails"])
        return result.get("result", {}).get("value")

    def screenshot(self, name):
        OUT.mkdir(parents=True, exist_ok=True)
        data = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})["data"]
        path = OUT / name
        path.write_bytes(base64.b64decode(data))
        return path

    def close(self):
        self.socket.close()
        requests.get(f"{CDP}/json/close/{self.target_id}", timeout=5)


def wait_until(page, script, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = page.evaluate(script)
            if value:
                return value
        except (RuntimeError, websocket.WebSocketTimeoutException):
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Browser condition did not arrive: {script}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-skills", action="store_true",
                        help="check a confirmed resume with project evidence but no skill entries")
    parser.add_argument("--skill-exploration", action="store_true",
                        help="save self-reported technical skills and verify separate direction exploration")
    parser.add_argument("--dense", action="store_true",
                        help="render a controlled, evidence-backed dense resume for portrait layout review")
    args = parser.parse_args()
    if args.skill_exploration:
        args.no_skills = True
    mysql = get_mysql_client()
    session = requests.Session()
    session.trust_env = False
    suffix = uuid.uuid4().hex[:12]
    username = f"navprobe_{suffix}"
    password = f"Disposable-{suffix}-2026!"
    task_id = f"task_{uuid.uuid4().hex[:16]}"
    owner = None
    page = None
    try:
        # Registration is deliberately rate-limited. Seed only this disposable
        # local account; login still exercises the real auth/session route.
        owner = f"acct_{uuid.uuid4().hex}"
        mysql.execute("INSERT INTO user_account(id,username,password_hash,name,role,status,created_at,updated_at) "
                      "VALUES (:id,:username,:hash,:name,'student','active',NOW(),NOW())",
                      {"id": owner, "username": username,
                       "hash": PasswordHasher().hash(password), "name": "浏览器验收夹具"},
                      commit=True)
        csrf = session.get(f"{BASE}/api/auth/csrf", timeout=10).json()["csrfToken"]
        logged_in = session.post(f"{BASE}/api/auth/login", json={
            "username": username, "password": password},
            headers={"X-CSRFToken": csrf}, timeout=10)
        logged_in.raise_for_status()

        candidate = {"name": "浏览器验收夹具", "school": "示例大学", "major": "计算机科学",
                     "degree": "本科", "skills": [] if args.no_skills else ["Python", "Vue"],
                     "projects": [{"name": "课程项目", "role": "开发", "desc": "负责数据界面"}]}
        if args.dense:
            candidate["skills"] = ["PostgreSQL", "Redis", "Celery", "RabbitMQ", "Dify Workflow",
                                   "Socket.IO", "Docker Compose", "Next.js", "TypeScript", "MinerU",
                                   "YOLOv10", "Qwen LoRA", "LangGraph", "ReAct", "Tool Calling",
                                   "GraphRAG", "Neo4j", "Milvus", "MySQL", "Python", "FastAPI",
                                   "Node.js", "SQLite", "SSE", "OpenAI-compatible API", "Codex CLI",
                                   "React", "Vite", "pnpm workspaces", "Fastify", "Supabase",
                                   "Tavily Search", "Hardhat", "Solidity", "React"]
            candidate["projects"] = [
                {"name": f"示例项目 {index}", "role": "开发", "is_team": True,
                 "desc": "与同伴协作完成系统设计、接口实现和测试，保留原文项目顺序。"}
                for index in range(1, 5)
            ]
            candidate["competitions"] = [
                {"name": name, "level": level, "award": "其他"}
                for name, level in [
                    ("示例设计竞赛", "国家级"), ("示例程序设计竞赛", "国家级"),
                    ("示例创新创业竞赛", "国家级"), ("校园数据挑战赛", "省级"),
                    ("示例设计竞赛", "省级"), ("示例程序设计竞赛", "省级"),
                    ("示例创新创业竞赛", "省级"), ("Kaggle 竞赛", "其他")]]
        source_text = " ".join(candidate["skills"] + [project["name"] + project["desc"]
                            for project in candidate["projects"]] + [competition["name"] + competition["level"]
                            for competition in candidate.get("competitions", [])])
        evidence = {"page_count": 1, "observed_pages": [1], "missing_pages": [],
                     "blocks": [{"block_id": "fixture_1", "page": 1,
                                 "block_type": "text", "text": source_text}]}
        outcome = {"task_id": task_id, "student_id": owner, "status": "completed",
                   "confirmed": True, "profile_ready": True,
                   "candidate_profile": candidate, "structure_validation": {"ok": True},
                   "source_evidence": evidence}
        profile = {**candidate, "_resume_confirmation": {
            "task_id": task_id, "student_id": owner, "source_evidence": evidence,
            "confirmed_at": "2026-10-14T20:00:00+08:00"}}
        mysql.execute("INSERT INTO pipeline_task(task_id, student_id, pipeline, status, result_json) "
                      "VALUES (:tid,:sid,'L0_parse_resume','completed',:result)",
                      {"tid": task_id, "sid": owner, "result": json.dumps(outcome, ensure_ascii=False)},
                      commit=True)
        mysql.execute("INSERT INTO student_profile(student_id, profile_json) VALUES (:sid,:profile)",
                      {"sid": owner, "profile": json.dumps(profile, ensure_ascii=False)}, commit=True)
        partial = session.post(f"{BASE}/api/profile/student/pipeline",
                               json={"pipeline": "L2_generate_portrait"},
                               headers={"X-CSRFToken": csrf}, timeout=30)
        partial.raise_for_status()
        assert partial.json()["data"]["status"] == "partial", partial.text
        assert "communication" in partial.json()["data"]["missing_fields"]
        if args.no_skills:
            assert "skills" in partial.json()["data"]["missing_fields"]
            assert partial.json()["data"]["portrait_candidate"]["D2_skill"]["dimension_score"] is None
        if args.dense:
            data = partial.json()["data"]
            d2 = data["portrait_candidate"]["D2_skill"]
            assert d2["skill_count"] == 34, d2
            assert len(d2["skill_list"]) == 34, d2
            raw_row = mysql.fetch_all("SELECT profile_json FROM student_profile WHERE student_id=:sid",
                                      {"sid": owner})[0]["profile_json"]
            raw_profile = json.loads(raw_row) if isinstance(raw_row, str) else raw_row
            assert len(raw_profile["skills"]) == 35
            result_row = mysql.fetch_all("SELECT result_json FROM pipeline_task "
                                         "WHERE task_id=:tid AND student_id=:sid",
                                         {"tid": data["task_id"], "sid": owner})[0]["result_json"]
            persisted = json.loads(result_row) if isinstance(result_row, str) else result_row
            assert persisted["portrait_candidate"]["D2_skill"]["skill_count"] == 34
        if not args.dense:
            assert "growth_evidence" in partial.json()["data"]["missing_fields"]
            assert partial.json()["data"]["portrait_candidate"]["D4_potential"]["dimension_score"] is None

        page = Page("about:blank")
        for domain in ("Page", "Runtime", "Network"):
            page.call(f"{domain}.enable")
        page.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900,
                                                          "deviceScaleFactor": 1, "mobile": False})
        for cookie in session.cookies:
            page.call("Network.setCookie", {"name": cookie.name, "value": cookie.value,
                                             "url": BASE + "/"})
        page.call("Page.navigate", {"url": f"{BASE}/app/student/career-navigation?task_id={task_id}"
                                             f"&preferred_role={quote('前端开发')}"})
        if args.dense:
            wait_until(page, "document.querySelectorAll('.tp-real__project-card').length === 4 && "
                             "document.querySelectorAll('.tp-real__award-row').length === 5 && "
                             "document.querySelectorAll('.tp-real__skill-list li').length === 34", 45)
            assert not page.evaluate("!!document.querySelector('.rp-task-details, .tp-real__source, .rp-right-dashboard-pane')")
            assert not page.evaluate("document.body.innerText.includes('技术任务记录')")
            assert not page.evaluate("document.querySelector('.tp-real__skill-area').open")
            assert page.evaluate("document.querySelectorAll('.tp-real__award-row')[0]"
                                 ".innerText.includes('请核对是否为不同届次或奖项')")
            page.evaluate("document.querySelector('.tp-real__highlights').scrollIntoView({block:'start'}); true")
            time.sleep(.3)
            stories = page.screenshot("desktop-dense-stories.png")
            page.evaluate("document.querySelector('.tp-real__skill-area').scrollIntoView({block:'center'}); true")
            time.sleep(.3)
            skills = page.screenshot("desktop-dense-skills-collapsed.png")
            page.evaluate("document.querySelector('.tp-real__skill-area summary').click(); true")
            wait_until(page, "document.querySelector('.tp-real__skill-area').open", 10)
            assert page.evaluate("document.querySelector('.tp-real__skill-area').innerText.includes('提及 2 次')")
            assert page.evaluate("document.querySelectorAll('.tp-real__award-row')[0]"
                                 ".innerText.includes('国家级、省级')")
            page.evaluate("document.querySelector('.tp-real__skill-area').scrollIntoView({block:'start'}); true")
            time.sleep(.3)
            expanded = page.screenshot("desktop-dense-skills-expanded.png")
            page.call("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844,
                                                              "deviceScaleFactor": 1, "mobile": True})
            wait_until(page, "document.querySelector('.tp-real__skill-area')?.getBoundingClientRect().width > 0", 10)
            assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 2")
            page.evaluate("document.querySelector('.tp-real__skill-area').scrollIntoView({block:'start'}); true")
            time.sleep(.3)
            mobile = page.screenshot("mobile-dense-skills-expanded.png")
            print(json.dumps({"dense_visual_fixture": True, "projects": 4, "competition_groups": 5,
                              "skill_terms": 34, "stories": str(stories), "collapsed": str(skills),
                              "expanded": str(expanded), "mobile": str(mobile)}, ensure_ascii=False))
            return
        if args.no_skills:
            wait_until(page, "document.querySelector('.rp-confirm-wrap h3')?.innerText.includes('继续探索与补充')", 30)
            wait_until(page, "document.querySelector('.rp-missing-facts')?.textContent.includes('简历技能的可核对依据')", 20)
            wait_until(page, "Number(getComputedStyle(document.querySelector('.rp-left')).opacity) > .99", 10)
            browser_state = page.evaluate("""(() => {
                const rows=[...document.querySelectorAll('.tp-portrait__dim-item')];
                const skill=rows.find(row => row.innerText.includes('职业技能'));
                return {title:document.querySelector('.rp-confirm-wrap h3')?.innerText,
                    missing:document.querySelector('.rp-missing-facts')?.textContent,
                    skillScore:skill?.querySelector('.tp-portrait__dim-score')?.innerText,
                    leftOpacity:getComputedStyle(document.querySelector('.rp-left')).opacity}; })()""")
            assert browser_state["skillScore"] == "—", browser_state
            assert float(browser_state["leftOpacity"]) > .99, browser_state
            if args.skill_exploration:
                page.evaluate("""(() => { const input=document.getElementById('skill-exploration-input');
                    input.value='Vue、TypeScript'; input.dispatchEvent(new Event('input',{bubbles:true}));
                    document.querySelector('.rp-role-guide__supplement button').click(); return true; })()""")
                wait_until(page, "document.querySelector('.rp-role-guide__saved')?.innerText.includes('Vue、TypeScript')", 20)
                wait_until(page, "document.querySelector('.rp-role-guide')?.innerText.includes('用户自述技能的方向探索')", 20)
                wait_until(page, "!document.querySelector('.rp-role-guide')?.innerText.includes('正在分别对照技能与职业方向')", 30)
                exploration_text = page.evaluate("document.querySelector('.rp-role-guide').innerText")
                assert "自述技能对应的目录线索：" in exploration_text and "前端开发" in exploration_text
                stored = mysql.fetch_one("SELECT profile_json FROM student_profile WHERE student_id=:sid",
                                         {"sid": owner})["profile_json"]
                stored = json.loads(stored) if isinstance(stored, str) else stored
                assert stored.get("skills", []) == []
                assert stored["_skill_exploration"]["skills"] == ["Vue", "TypeScript"]
                assert stored["_skill_exploration"]["scoring_eligible"] is False
                replay = session.get(f"{BASE}/api/profile/student/candidate/{task_id}", timeout=10)
                replay.raise_for_status()
                assert replay.json()["data"]["portrait_task"]["task_id"] == partial.json()["data"]["task_id"]
                assert replay.json()["data"]["portrait_task"]["portrait_candidate"][
                    "D2_skill"]["dimension_score"] is None
                page.call("Page.reload", {"ignoreCache": True})
                wait_until(page, "performance.getEntriesByType('navigation')[0]?.type === 'reload' "
                                 "&& document.querySelector('.rp-role-guide__saved')?.innerText.includes('Vue、TypeScript')", 20)
                wait_until(page, "!document.querySelector('.rp-role-guide')?.innerText.includes('正在分别对照技能与职业方向')", 30)
                assert "自述技能对应的目录线索：" in page.evaluate("document.querySelector('.rp-role-guide').innerText")
            diagnostic = None
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                response = session.get(f"{BASE}/api/report/resume-diagnostic", timeout=10)
                if response.status_code == 200:
                    diagnostic = response.json()["data"]
                    break
                assert response.status_code == 404, response.text
                time.sleep(.4)
            assert diagnostic and diagnostic["source_confirmation_task_id"] == task_id
            if not args.skill_exploration:
                page.evaluate("document.querySelector('.rp-missing-facts').open = true; true")
            target = ".rp-role-guide" if args.skill_exploration else ".rp-missing-facts"
            page.evaluate(f"document.querySelector({json.dumps(target)}).scrollIntoView({{block:'center'}}); true")
            time.sleep(.4)
            screenshot = page.screenshot("desktop-skill-exploration.png" if args.skill_exploration
                                         else "desktop-no-skills-partial.png")
            mobile_screenshot = None
            if args.skill_exploration:
                page.call("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844,
                                                                  "deviceScaleFactor": 1, "mobile": True})
                wait_until(page, "document.querySelector('.rp-role-guide__supplement')?.getBoundingClientRect().width > 0", 10)
                mobile_state = page.evaluate("""(() => {
                    const form=document.querySelector('.rp-role-guide__supplement');
                    return {width:form.getBoundingClientRect().width,
                        viewport:document.documentElement.clientWidth,
                        scrollWidth:document.documentElement.scrollWidth}; })()""")
                assert 260 < mobile_state["width"] < mobile_state["viewport"], mobile_state
                assert mobile_state["scrollWidth"] <= mobile_state["viewport"] + 2, mobile_state
                page.evaluate("document.querySelector('.rp-role-guide').scrollIntoView({block:'start'}); true")
                time.sleep(.4)
                mobile_screenshot = page.screenshot("mobile-skill-exploration.png")
            print(json.dumps({"no_skills_partial": browser_state,
                              "skill_exploration": args.skill_exploration,
                              "resume_report_status": diagnostic["status"],
                              "screenshot": str(screenshot),
                              "mobile_screenshot": str(mobile_screenshot) if mobile_screenshot else None},
                             ensure_ascii=False))
            return
        wait_until(page, "document.querySelector('.rp-self-report') && "
                         "document.querySelector('.rp-self-report').getBoundingClientRect().width > 0", 30)
        wait_until(page, """document.querySelector('.rp-role-guide__list')?.innerText.includes('前端开发')
            && performance.getEntriesByType('resource').some(item => item.name.includes('/api/profile/student/confirmed-resume/roles'))""", 30)
        assert page.evaluate("document.querySelector('.rp-role-guide__choice select').value") == "前端开发"
        role_request_count = page.evaluate("""performance.getEntriesByType('resource')
            .filter(item => item.name.includes('/api/profile/student/confirmed-resume/roles')).length""")
        assert not page.evaluate("""performance.getEntriesByType('resource')
            .some(item => item.name.includes('/api/career/infer-role'))""")
        page.evaluate("""(() => { const select=document.querySelector('.rp-role-guide__choice select');
            select.value='后端开发'; select.dispatchEvent(new Event('change',{bubbles:true})); return true; })()""")
        wait_until(page, """new URLSearchParams(location.search).get('preferred_role') === '后端开发'
            && performance.getEntriesByType('resource')
                .filter(item => item.name.includes('/api/profile/student/confirmed-resume/roles')).length > """
                   + str(role_request_count), 30)
        wait_until(page, "document.querySelector('.rp-role-guide__list')?.innerText.includes('后端开发')", 30)
        assert "含你的偏好" in page.evaluate("document.querySelector('.rp-role-guide__list').innerText")
        rejected_skills = session.post(f"{BASE}/api/profile/student/confirmed-resume/roles",
                                       json={"task_id": task_id, "skills": ["伪造技能"]},
                                       headers={"X-CSRFToken": csrf}, timeout=10)
        assert rejected_skills.status_code == 400, rejected_skills.text
        bound_roles = session.post(f"{BASE}/api/profile/student/confirmed-resume/roles",
                                   json={"task_id": task_id, "preferred_role": "后端开发"},
                                   headers={"X-CSRFToken": csrf}, timeout=15)
        bound_roles.raise_for_status()
        assert bound_roles.json()["data"]["source_confirmation_task_id"] == task_id
        assert bound_roles.json()["data"]["source_kind"] == "confirmed_resume"
        assert bound_roles.json()["data"]["roles"]["skill_count"] == 2
        same_portrait = session.get(f"{BASE}/api/profile/student/candidate/{task_id}", timeout=10)
        same_portrait.raise_for_status()
        assert same_portrait.json()["data"]["portrait_task"]["task_id"] == partial.json()["data"]["task_id"]
        # This fixture seeds a confirmed profile directly instead of using the
        # confirmation transaction, so no automatic R1 intent exists. Request
        # the report through the visible legacy action before expecting it.
        page.evaluate("""(() => { const button=document.querySelector('.rp-report-progress__actions button');
            if (button?.innerText.includes('生成简历诊断报告')) button.click(); return true; })()""")
        deadline = time.monotonic() + 90
        diagnostic = None
        while time.monotonic() < deadline:
            response = session.get(f"{BASE}/api/report/resume-diagnostic", timeout=10)
            if response.status_code == 200:
                diagnostic = response.json()["data"]
                break
            assert response.status_code == 404, response.text
            time.sleep(.4)
        assert diagnostic and diagnostic["source_confirmation_task_id"] == task_id
        page.evaluate("document.querySelectorAll('.rp-real-view-tabs button')[0].click(); true")
        wait_until(page, "!!document.querySelector('.tp-portrait--real .tp-portrait__dim-item')", 10)
        wait_until(page, "document.querySelector('.rp-left') && "
                         "Number(getComputedStyle(document.querySelector('.rp-left')).opacity) > .99", 5)
        before = page.evaluate("""(() => { const left=document.querySelector('.rp-left');
            const form=document.querySelector('.rp-self-report'); const work=document.querySelector('.rp-workspace');
            return {path:location.pathname, leftWidth:left.getBoundingClientRect().width,
                formWidth:form.getBoundingClientRect().width,
                leftOpacity:getComputedStyle(left).opacity,
                immersive:work.classList.contains('rp-workspace--immersive'),
                technicalRail:!!document.querySelector('.rp-right-dashboard-pane'),
                text:form.innerText.slice(0,220)}; })()""")
        assert before["leftWidth"] >= 280 and before["formWidth"] > 0, before
        assert float(before["leftOpacity"]) > .99 and not before["immersive"], before
        assert not before["technicalRail"], before
        assert page.evaluate("""(() => { const rows=[...document.querySelectorAll('.tp-portrait__dim-item')];
            const growth=rows.find(row => row.innerText.includes('成长潜力'));
            return growth?.querySelector('.tp-portrait__dim-score')?.innerText; })()""") == '—'
        desktop = page.screenshot("desktop-before-supplement.png")

        page.evaluate("""(() => { const field=document.querySelector('.rp-self-report__field');
            const select=field.querySelector('select'); select.value='中';
            select.dispatchEvent(new Event('change',{bubbles:true}));
            const textarea=field.querySelector('textarea');
            textarea.value='我在课程项目中协调同伴明确分工，持续沟通并按期完成数据界面。';
            textarea.dispatchEvent(new Event('input',{bubbles:true}));
            document.querySelector('.rp-self-report button.rp-parse-btn').click();
            return true; })()""")
        wait_until(page, "document.querySelector('.rp-self-report__saved') && "
                         "document.querySelector('.rp-self-report__saved').innerText.includes('第 1 版')", 30)
        report = mysql.fetch_all("SELECT profile_json FROM student_profile WHERE student_id=:sid",
                                 {"sid": owner})[0]["profile_json"]
        report = json.loads(report) if isinstance(report, str) else report
        assert report["_user_self_report"]["items"]["communication"]["level"] == "中"
        assert report["_user_self_report"]["source_kind"] == "user_self_report"
        assert "target_role" not in report  # page preference is not a resume/scoring fact
        retained_report = session.get(f"{BASE}/api/report/resume-diagnostic", timeout=10)
        retained_report.raise_for_status()
        assert retained_report.json()["data"]["report_id"] == diagnostic["report_id"]
        latest_data = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            latest = session.get(f"{BASE}/api/profile/student/candidate/{task_id}", timeout=10)
            latest.raise_for_status()
            latest_data = latest.json()["data"]
            if latest_data.get("portrait_task"):
                break
            time.sleep(.2)
        assert latest_data and latest_data.get("portrait_task"), latest_data
        assert latest_data["portrait_task"]["portrait_candidate"][
            "D3_quality"]["communication"]["provenance"]["kind"] == "user_self_report"
        after = page.screenshot("desktop-after-supplement.png")

        page.call("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844,
                                                          "deviceScaleFactor": 1, "mobile": True})
        time.sleep(1)
        mobile_state = page.evaluate("""(() => { const form=document.querySelector('.rp-self-report');
            const left=document.querySelector('.rp-left');
            const work=document.querySelector('.rp-workspace');
            return {formWidth:form.getBoundingClientRect().width,
                leftWidth:left.getBoundingClientRect().width,
                workScrollHeight:work.scrollHeight, workHeight:work.clientHeight,
                leftScrollHeight:left.scrollHeight, leftHeight:left.clientHeight,
                immersive:work.classList.contains('rp-workspace--immersive')}; })()""")
        assert mobile_state["formWidth"] > 0 and not mobile_state["immersive"], mobile_state
        assert mobile_state["workScrollHeight"] > mobile_state["workHeight"], mobile_state
        assert mobile_state["leftHeight"] >= mobile_state["leftScrollHeight"] - 2, mobile_state
        mobile = page.screenshot("mobile-after-supplement.png")
        page.evaluate("document.querySelector('.rp-self-report').scrollIntoView({block:'start'}); true")
        time.sleep(.3)
        mobile_form = page.screenshot("mobile-supplement-form.png")
        page.evaluate("document.querySelector('.rp-role-guide').scrollIntoView({block:'start'}); true")
        time.sleep(.3)
        mobile_roles = page.screenshot("mobile-role-guide.png")
        page.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900,
                                                          "deviceScaleFactor": 1, "mobile": False})
        page.evaluate("document.querySelector('.rp-role-guide').scrollIntoView({block:'start'}); true")
        time.sleep(.3)
        desktop_roles = page.screenshot("desktop-role-guide.png")

        # A previously persisted L2 could contain the old unsupported D4=0.
        # The current candidate route must retain the confirmed resume while
        # hiding that result, then allow a fresh L2 run from the page.
        old_portrait_id = latest_data["portrait_task"]["task_id"]
        row = mysql.fetch_all("SELECT result_json FROM pipeline_task WHERE task_id=:tid AND student_id=:sid",
                              {"tid": old_portrait_id, "sid": owner})[0]["result_json"]
        legacy = json.loads(row) if isinstance(row, str) else row
        legacy["portrait_candidate"]["D4_potential"] = {"dimension_score": 0, "evidence": []}
        mysql.execute("UPDATE pipeline_task SET result_json=:result WHERE task_id=:tid AND student_id=:sid",
                      {"result": json.dumps(legacy, ensure_ascii=False),
                       "tid": old_portrait_id, "sid": owner}, commit=True)
        stale = session.get(f"{BASE}/api/profile/student/candidate/{task_id}", timeout=10)
        stale.raise_for_status()
        assert stale.json()["data"]["confirmed"] is True
        assert stale.json()["data"]["portrait_task"] is None
        assert stale.json()["data"]["portrait_needs_refresh"] is True
        page.call("Page.reload", {"ignoreCache": True})
        wait_until(page, """[...document.querySelectorAll('.rp-evidence-warning')]
            .some(item => item.innerText.includes('旧画像曾把没有成长经历依据'))""", 20)
        page.evaluate("""(() => { const button=[...document.querySelectorAll('.rp-confirm-wrap button')]
            .find(item => item.innerText.includes('尝试生成有证据的画像'));
            if (!button) throw Error('fresh portrait action missing'); button.click(); return true; })()""")
        deadline = time.monotonic() + 30
        current_data = None
        while time.monotonic() < deadline:
            current = session.get(f"{BASE}/api/profile/student/candidate/{task_id}", timeout=10)
            current.raise_for_status()
            current_data = current.json()["data"]
            if (current_data.get("portrait_task")
                    and current_data["portrait_task"]["task_id"] != old_portrait_id):
                break
            time.sleep(.2)
        assert current_data and current_data["portrait_needs_refresh"] is False, current_data
        assert current_data["portrait_task"]["task_id"] != old_portrait_id
        assert current_data["portrait_task"]["portrait_candidate"]["D4_potential"]["dimension_score"] is None
        wait_until(page, """document.querySelector('.rp-confirm-wrap h3')?.innerText.includes('继续探索与补充')
            && [...document.querySelectorAll('.rp-evidence-warning')]
                .every(item => !item.innerText.includes('旧画像曾把没有成长经历依据'))""", 20)
        print(json.dumps({"desktop_before": before, "mobile_after": mobile_state,
                          "saved_revision": report["_user_self_report"]["revision"],
                          "resume_report_status": diagnostic["status"],
                          "legacy_portrait_refreshed": True,
                          "screenshots": [str(desktop), str(after), str(mobile), str(mobile_form),
                                          str(mobile_roles), str(desktop_roles)]}, ensure_ascii=False))
    finally:
        if page:
            page.close()
        if owner:
            from sqlalchemy import text
            with mysql.engine.begin() as conn:
                for table in ("pipeline_task_event", "pipeline_task", "student_portrait_4d",
                              "resume_diagnostic_report", "student_profile"):
                    conn.execute(text(f"DELETE FROM {table} WHERE student_id=:sid"), {"sid": owner})
                conn.execute(text("DELETE FROM user_account WHERE id=:sid"), {"sid": owner})


if __name__ == "__main__":
    main()
