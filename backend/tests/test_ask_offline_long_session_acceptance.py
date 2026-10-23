from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json

from app.services.agent_runtime.conversation_state import validate_conversation_state
from app.services.agent_runtime.turn_contract import normalize_message_parts
from app.services.session_manager import SessionManager, SessionState, SessionStatus
from app.services.tools.component_snapshot_discover import create_component_snapshot_discover_tool
from app.services.tools.component_snapshot_read import create_component_snapshot_read_tool


def _fact(text: str, message_id: str, quote: str) -> dict:
    return {
        "text": text,
        "source_message_id": message_id,
        "source_quote": quote,
    }


def _offline_100_round_messages() -> list[dict]:
    messages = []
    for round_number in range(1, 101):
        user_text = f"第 {round_number} 轮：请结合当前目标简短回答。"
        if round_number == 1:
            user_text = "我的目标是后端开发。"
        elif round_number == 30:
            user_text = "更正：我的目标改为数据分析师；后端开发不再是当前目标。"
        elif round_number == 70:
            user_text = "更正：我的目标改为数据平台工程；数据分析师不再是当前目标。"
        messages.append({
            "message_id": f"round_{round_number:03d}_user",
            "role": "user",
            "content": user_text,
        })

        assistant = {
            "message_id": f"round_{round_number:03d}_assistant",
            "role": "assistant",
            "content": f"离线合成答复 {round_number}。",
        }
        if round_number == 10:
            card = normalize_message_parts([{
                "type": "data_card",
                "schema_version": 1,
                "title": "岗位比较：第十轮历史快照",
                "card_type": "comparison",
                "source_refs": [{
                    "source_id": "fixture:offline:100-rounds",
                    "label": "合成岗位样例",
                    "version": "v1",
                }],
                "items": [
                    {"label": "第一项", "value": "产品分析师", "entity_id": "ent_offline_job_a", "entity_version": "job:a:v1"},
                    {"label": "第二项", "value": "数据分析师", "entity_id": "ent_offline_job_b", "entity_version": "job:b:v1"},
                    {"label": "第三项", "value": "数据平台工程师", "entity_id": "ent_offline_job_c", "entity_version": "job:c:v1"},
                ],
            }])[0]
            assistant["metadata"] = {"parts_v1": [card]}
        messages.append(assistant)
    return messages


def test_offline_100_rounds_preserve_corrections_and_old_card_selection(monkeypatch):
    """Synthetic continuity only: no live model, database, browser, or search."""
    session_id = "offline_100_round_ask"
    messages = _offline_100_round_messages()
    state = SessionState(
        session_id=session_id,
        student_id="synthetic-owner",
        status=SessionStatus.ACTIVE,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
        messages=messages,
    )
    manager = SessionManager.__new__(SessionManager)
    manager._redis = None
    manager._cache = {session_id: state}
    manager._save_to_redis = lambda _state: None

    class DeterministicRollup:
        """A fixed response fixture that exercises server validation/cursors."""

        def chat_json(self, messages, **_kwargs):
            prompt = messages[0]["content"]
            start = prompt.index("<ROLLUP_INPUT_JSON>\n") + len("<ROLLUP_INPUT_JSON>\n")
            end = prompt.index("\n</ROLLUP_INPUT_JSON>", start)
            source = json.loads(prompt[start:end])
            previous = deepcopy(source["previous_conversation_state_v1"])
            current_goal = previous.get("current_goal")
            superseded = list(previous.get("superseded") or [])

            for message in source["messages"]:
                if message.get("role") != "user":
                    continue
                content = str(message.get("excerpt") or "")
                message_id = str(message.get("message_id") or "")
                if content == "我的目标是后端开发。" and current_goal is None:
                    current_goal = _fact("目标是后端开发", message_id, content)
                elif "更正：我的目标改为数据分析师" in content:
                    replacement = _fact("目标是数据分析师", message_id, "我的目标改为数据分析师")
                    if current_goal is not None:
                        superseded.append({"superseded": current_goal, "replacement": replacement})
                    current_goal = replacement
                elif "更正：我的目标改为数据平台工程" in content:
                    replacement = _fact("目标是数据平台工程", message_id, "我的目标改为数据平台工程")
                    if current_goal is not None:
                        superseded.append({"superseded": current_goal, "replacement": replacement})
                    current_goal = replacement

            next_state = {
                "schema_version": 1,
                "through_message_count": (
                    previous["through_message_count"] + len(source["messages"])
                ),
                "current_goal": current_goal,
                "active_constraints": [],
                "confirmed_decisions": [],
                "open_questions": [],
                "superseded": superseded[-8:],
            }
            summary = f"【当前目标】{current_goal['text']}；【相关历史引用】{current_goal['source_message_id']}"
            return {"summary": summary, "conversation_state_v1": next_state}

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: DeterministicRollup())
    monkeypatch.setattr("app.services.session_manager.get_session_manager", lambda: manager)

    # 200 messages represent 100 complete user/assistant rounds. The exact
    # recent 16-message window is retained while old history advances in
    # contiguous 20-message batches under the deterministic rollup fixture.
    for _ in range(10):
        manager.maybe_rollup_summary(session_id, keep_messages=16, compact_summary=True)

    assert state.summary_message_count == 184
    assert state.conversation_state_message_count == 184
    validated = validate_conversation_state(
        state.conversation_state_v1,
        state.messages,
        expected_through_count=184,
    )
    assert validated.current_goal.text == "目标是数据平台工程"
    assert validated.current_goal.source_message_id == "round_070_user"
    assert [
        (item["superseded"]["text"], item["replacement"]["text"])
        for item in state.conversation_state_v1["superseded"]
    ] == [
        ("目标是后端开发", "目标是数据分析师"),
        ("目标是数据分析师", "目标是数据平台工程"),
    ]
    assert "目标是数据平台工程" in state.conversation_summary

    restored = SessionState.from_dict(state.to_dict())
    manager._cache[session_id] = restored
    assert manager.get_validated_conversation_state(session_id, "synthetic-owner")["current_goal"]["text"] == "目标是数据平台工程"
    assert manager.get_validated_conversation_state(session_id, "another-owner") is None
    assert "第 100 轮" in manager.get_history_for_agent(session_id, max_messages=16)[-2]["content"]

    discovery = create_component_snapshot_discover_tool(
        student_id="synthetic-owner",
        session_id=session_id,
    )
    discovered_raw = discovery.invoke({"query": "岗位比较", "offset": 0, "limit": 5})
    discovered = json.loads(discovered_raw)
    assert discovered["status"] == "completed"
    assert discovered["scanned_messages"] == 200
    assert discovered["cards"][0]["title"] == "岗位比较：第十轮历史快照"
    assert [item["entity_id"] for item in discovered["cards"][0]["items"][1:3]] == [
        "ent_offline_job_b",
        "ent_offline_job_c",
    ]

    read = create_component_snapshot_read_tool(
        student_id="synthetic-owner",
        session_id=session_id,
    )
    selected = []
    for item in discovered["cards"][0]["items"][1:3]:
        request = {
            "component_id": discovered["cards"][0]["component_id"],
            "schema_version": 1,
            "entity_id": item["entity_id"],
            "entity_version": item["entity_version"],
        }
        first = read.invoke(request)
        second = read.invoke(request)
        assert first == second  # duplicate historical read is stable and side-effect free
        selected.append(json.loads(first)["entity"]["value"])
    assert selected == ["数据分析师", "数据平台工程师"]
    assert len(restored.messages) == 200
