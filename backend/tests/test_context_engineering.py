from app.services.agent_runtime.blackboard import Blackboard
from app.services.agent_runtime.modes import InteractionMode
from app.services.agent_runtime.scoped_invoke import _build_subagent_system
from app.services.agent_runtime.task_packet import TaskPacket


def test_read_slice_for_model_strips_constraint_text_and_student_id():
    bb = Blackboard()
    bb.write(
        "student.profile_ref",
        {"student_id": "student_001", "summary": "student_001"},
        "supervisor",
    )
    bb.write(
        "constraints.user",
        {"summary": "想做算法", "text": "完整约束长文不应进模型" * 20},
        "supervisor",
    )
    proj = bb.read_slice_for_model(["student.profile_ref", "constraints.user"])
    assert proj["student.profile_ref"]["summary"] == "已关联当前用户"
    assert "student_001" not in str(proj)
    assert "text" not in proj["constraints.user"]
    assert "完整约束长文" not in str(proj)


def test_capability_card_filled_only_skips_empties():
    bb = Blackboard()
    bb.write("portrait.current", {"summary": "已有画像"}, "profile")
    card = bb.capability_card(filled_only=True)
    assert "portrait.current" in card or "个人职业画像" in card or "已有画像" in card
    assert "(empty)" not in card
    assert "match.current" not in card


def test_ask_scoped_system_uses_ask_manual_not_work_manual():
    packet = TaskPacket(
        subagent="qa",
        goal="读附件",
        user_utterance="总结一下",
        read_keys=["attachments.mounted"],
        write_keys=[],
    )
    system = _build_subagent_system(
        "qa", packet, {"attachments.mounted": {"summary": "1 mounted"}},
        interaction_mode=InteractionMode.ASK,
    )
    assert "portrait_generate" not in system
    assert "黑板 ACL" not in system
