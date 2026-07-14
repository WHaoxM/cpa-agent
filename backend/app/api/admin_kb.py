"""
知识库智能上传 — 实体解析 + 意图识别 + 触发更新
挂载在 admin_bp 下
"""

import json
import uuid
from flask import request, jsonify

from config.prompt_loader import load_prompt

from . import admin_bp
from ._helpers import safe_error
from ..services.db import get_mysql_client
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.admin_kb')


@admin_bp.route('/knowledge-base/smart-upload', methods=['POST'])
def smart_upload_knowledge():
    """智能上传 — 文本/JSON/文件 → 实体解析 → 意图识别 → 触发更新"""
    try:
        content, source_type, title = None, 'text', '未命名知识'

        if request.is_json:
            body = request.get_json(force=True)
            content = body.get('content', '')
            source_type = body.get('type', 'text')
            title = body.get('title', '未命名知识')
        elif 'file' in request.files:
            file = request.files['file']
            title = request.form.get('title', file.filename)
            source_type = request.form.get('type', 'text')
            raw = file.read()
            try:
                content = raw.decode('utf-8')
            except UnicodeDecodeError:
                content = raw.decode('gbk', errors='ignore')
        else:
            return jsonify({"success": False, "error": "需要JSON body或文件上传"}), 400

        if not content or not content.strip():
            return jsonify({"success": False, "error": "内容不能为空"}), 400

        mysql = None
        try:
            mysql = get_mysql_client()
        except Exception as e:
            logger.warning(f"MySQL 不可用，知识库上传进入演示降级: {e}")

        # Step 1: 记录上传
        kb_id = f"kb_{uuid.uuid4().hex[:12]}"
        if mysql:
            try:
                mysql.execute_write(
                    "INSERT INTO knowledge_base (id,title,type,status,created_at) VALUES (:id,:title,:type,'processing',NOW())",
                    {"id": kb_id, "title": title, "type": source_type})
            except Exception:
                logger.warning("knowledge_base表不存在,跳过记录")

        # Step 2: 实体解析
        parsed = _extract_entities(content, source_type)

        # Step 3: 意图识别
        intent = _identify_intent(parsed, source_type)

        # Step 4: 触发更新
        update_result = _trigger_update(parsed, intent, mysql)

        if mysql:
            try:
                mysql.execute_write("UPDATE knowledge_base SET status='completed' WHERE id=:id", {"id": kb_id})
            except Exception:
                pass

        return jsonify({"success": True, "data": {
            "kb_id": kb_id, "title": title,
            "parsed_entities": parsed, "intent": intent, "update_result": update_result,
            "source": "runtime" if mysql else "demo_fallback"}})

    except Exception as e:
        logger.error(f"智能上传失败: {e}")
        return jsonify({"success": False, "error": "操作失败，请稍后重试", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@admin_bp.route('/knowledge-base/batch-update', methods=['POST'])
def batch_update_knowledge():
    """批量更新 — 直接传入结构化数据更新 skill_taxonomy / career_insights"""
    try:
        body = request.get_json(force=True)
        updates = body.get('updates', [])
        mysql = get_mysql_client()
        results = []
        for upd in updates:
            table, action, data = upd.get('table'), upd.get('action'), upd.get('data', {})
            if table == 'skill_taxonomy':
                r = _update_skill(action, data, mysql)
            elif table == 'career_insights':
                r = _update_career(action, data, mysql)
            else:
                r = {"success": False, "error": f"不支持的表: {table}"}
            results.append({"table": table, "action": action, "result": r})
        return jsonify({"success": True, "data": results})
    except Exception as e:
        return jsonify(safe_error("批量更新知识库", e)), 500


# ── 内部函数 ──

def _extract_entities(content: str, source_type: str) -> dict:
    """LLM实体解析, 降级到关键词匹配"""
    try:
        from ..utils.llm_client import get_llm_client
        llm = get_llm_client()
        prompt = load_prompt(
            "admin.kb.extract_entities.user",
            source_type=source_type,
            content=content[:3000],
        )
        resp = llm.generate(prompt, temperature=0.1).strip()
        if resp.startswith("```"):
            resp = resp.split("```")[1]
            if resp.startswith("json"):
                resp = resp[4:]
        return json.loads(resp)
    except Exception as e:
        logger.warning(f"LLM解析降级: {e}")
        return _fallback_extract(content)


def _fallback_extract(content: str) -> dict:
    # 业务型数据走 YAML 字典 — config/defaults/dictionaries/{skill,position}_lexicon.yaml
    from config.dictionary_loader import get_known_skills_flat, get_position_names_flat
    known_skills = get_known_skills_flat()
    known_roles = get_position_names_flat()
    return {
        "skills": [s for s in known_skills if s in content],
        "roles": [r for r in known_roles if r in content],
        "relations": [], "salary_info": [], "demand_info": []}


def _identify_intent(parsed: dict, source_type: str) -> dict:
    intents = []
    if source_type == 'jd' or parsed.get('roles'):
        intents.append("update_career_insights")
    if source_type == 'skill' or parsed.get('skills'):
        intents.append("update_skill_taxonomy")
    if parsed.get('relations'):
        intents.append("update_relations")
    if parsed.get('salary_info'):
        intents.append("update_salary")
    if parsed.get('demand_info'):
        intents.append("update_demand")
    if not intents:
        intents.append("general_update")
    return {"intents": intents,
            "new_skills": len(parsed.get('skills', [])),
            "new_roles": len(parsed.get('roles', []))}


def _trigger_update(parsed: dict, intent: dict, mysql) -> dict:
    """根据意图触发知识库更新"""
    result = {"updated_skills": 0, "updated_careers": 0, "errors": []}
    if mysql is None:
        result["source"] = "demo_fallback"
        result["errors"].append("MySQL 不可用，已跳过持久化更新")
        return result

    skills = parsed.get('skills', [])
    roles = parsed.get('roles', [])

    # 新增/更新技能
    if "update_skill_taxonomy" in intent['intents'] and skills:
        for skill_name in skills:
            try:
                # 检查是否已存在
                existing = mysql.execute(
                    "SELECT id FROM skill_taxonomy WHERE skill_name=:n", {"n": skill_name})
                if not existing:
                    mysql.execute_write("""
                        INSERT IGNORE INTO skill_taxonomy (skill_name,category,sub_category,tier,difficulty,dimension,heat_score)
                        VALUES (:n,'待分类','待分类',2,'中','D2',0.5)""", {"n": skill_name})
                    result["updated_skills"] += 1
                else:
                    # 已存在则提升热度
                    mysql.execute_write(
                        "UPDATE skill_taxonomy SET heat_score=LEAST(heat_score+0.05,1.0) WHERE skill_name=:n",
                        {"n": skill_name})
                    result["updated_skills"] += 1
            except Exception as e:
                result["errors"].append(f"skill {skill_name}: {e}")

    # 新增/更新职业方向
    if "update_career_insights" in intent['intents'] and roles:
        for role_name in roles:
            try:
                existing = mysql.execute(
                    "SELECT id FROM career_insights WHERE role_name=:n", {"n": role_name})
                if not existing:
                    mysql.execute_write("""
                        INSERT IGNORE INTO career_insights (role_name,category,sub_category,demand_level,growth_trend,skill_count)
                        VALUES (:n,'待分类','待分类','中','上升',0)""", {"n": role_name})
                    result["updated_careers"] += 1
                else:
                    mysql.execute_write(
                        "UPDATE career_insights SET demand_level='高' WHERE role_name=:n AND demand_level='中'",
                        {"n": role_name})
                    result["updated_careers"] += 1
            except Exception as e:
                result["errors"].append(f"role {role_name}: {e}")

    # 更新薪资
    for si in parsed.get('salary_info', []):
        try:
            mysql.execute_write(
                "UPDATE career_insights SET avg_salary_low=:lo, avg_salary_high=:hi WHERE role_name=:n",
                {"lo": si.get('low'), "hi": si.get('high'), "n": si.get('role')})
        except Exception as e:
            result["errors"].append(f"salary: {e}")

    return result


def _update_skill(action: str, data: dict, mysql) -> dict:
    if action == 'insert':
        mysql.execute_write("""
            INSERT IGNORE INTO skill_taxonomy (skill_name,category,sub_category,tier,difficulty,dimension,heat_score,proficiency_criteria,description)
            VALUES (:name,:cat,:sub,:tier,:diff,:dim,:heat,:criteria,:desc)""",
            {"name": data.get('skill_name'), "cat": data.get('category', '待分类'),
             "sub": data.get('sub_category'), "tier": data.get('tier', 2),
             "diff": data.get('difficulty', '中'), "dim": data.get('dimension', 'D2'),
             "heat": data.get('heat_score', 0.5),
             "criteria": json.dumps(data.get('proficiency_criteria', {}), ensure_ascii=False) if data.get('proficiency_criteria') else None,
             "desc": data.get('description')})
        return {"success": True}
    elif action == 'update':
        sets, params = [], {"name": data.get('skill_name')}
        for k in ('category','sub_category','tier','difficulty','heat_score','description'):
            if k in data:
                sets.append(f"{k}=:{k}")
                params[k] = data[k]
        if 'proficiency_criteria' in data:
            sets.append("proficiency_criteria=:criteria")
            params['criteria'] = json.dumps(data['proficiency_criteria'], ensure_ascii=False)
        if sets:
            mysql.execute_write(f"UPDATE skill_taxonomy SET {','.join(sets)} WHERE skill_name=:name", params)
        return {"success": True}
    return {"success": False, "error": f"未知action: {action}"}


def _update_career(action: str, data: dict, mysql) -> dict:
    if action == 'insert':
        mysql.execute_write("""
            INSERT IGNORE INTO career_insights (role_name,category,sub_category,demand_level,growth_trend,skill_count,description)
            VALUES (:name,:cat,:sub,:demand,:trend,:sc,:desc)""",
            {"name": data.get('role_name'), "cat": data.get('category', '待分类'),
             "sub": data.get('sub_category'), "demand": data.get('demand_level', '中'),
             "trend": data.get('growth_trend', '上升'), "sc": data.get('skill_count', 0),
             "desc": data.get('description')})
        return {"success": True}
    elif action == 'update':
        sets, params = [], {"name": data.get('role_name')}
        for k in ('category','sub_category','demand_level','growth_trend','avg_salary_low','avg_salary_high','description'):
            if k in data:
                sets.append(f"{k}=:{k}")
                params[k] = data[k]
        if 'core_skills' in data:
            sets.append("core_skills=:skills")
            params['skills'] = json.dumps(data['core_skills'], ensure_ascii=False)
        if sets:
            mysql.execute_write(f"UPDATE career_insights SET {','.join(sets)} WHERE role_name=:name", params)
        return {"success": True}
    return {"success": False, "error": f"未知action: {action}"}
