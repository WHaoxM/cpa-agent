from app.services.agent_runtime.prefix import build_mode_prefix, clear_prefix_cache
from app.services.agent_runtime.modes import InteractionMode
from app.services.agent_runtime.reply_egress import sanitize_user_reply


def test_sanitize_user_reply_rewrites_internals():
    raw = (
        "我不会自动生成画像（需调用 portrait_generate），"
        "也不会修改黑板业务态（如 portrait.current）。"
        "目前只看到 student_001。"
    )
    out = sanitize_user_reply(raw)
    assert "portrait_generate" not in out
    assert "portrait.current" not in out
    assert "student_001" not in out
    assert "个人职业画像" in out or "建立个人画像" in out
    assert "当前用户" in out


def test_ask_prefix_excludes_work_manual_jargon():
    clear_prefix_cache()
    prefix = build_mode_prefix(InteractionMode.ASK)
    assert "portrait_generate" not in prefix
    assert "portrait.current" not in prefix
    assert "黑板 ACL" not in prefix
