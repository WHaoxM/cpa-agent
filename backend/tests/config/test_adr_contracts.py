"""
test_adr_contracts.py — D1.7 ADR-0001 负向契约
- #1 未知键拒绝（未在 whitelist 的 workspace key 启动报错）
- #2 base.md 不可被 workspace 覆盖
- #3 tone.md 可被 workspace 整文件替换
- #6 deep merge 不污染 defaults
"""
import pytest
import yaml
from pathlib import Path
from config.loader import (
    RulesConfigError,
    init_rules_config,
    competitiveness_level,
    get_rules_config,
)
from config.prompt_loader import (
    init_prompt_registry,
    get_prompt_registry,
    load_prompt,
)


# ─────────────────────────────────────────────────────────
# #1 未知键拒绝
# ─────────────────────────────────────────────────────────

class TestUnknownKeyRejection:
    """workspace 写未在 override_whitelist 里的 key → 启动报错"""

    def test_unknown_top_key_rejected(self, tmp_workspace, monkeypatch):
        """完全无关的 key 拒绝"""
        (tmp_workspace / "overrides").mkdir(parents=True, exist_ok=True)
        (tmp_workspace / "overrides" / "rules.yaml").write_text(
            "hacker_key: 0.99\n",
            encoding="utf-8"
        )
        with pytest.raises(RulesConfigError, match="未授权键"):
            init_rules_config(force=True)

    def test_unknown_subkey_rejected(self, tmp_workspace, monkeypatch):
        """白名单键的未授权子键也拒绝"""
        (tmp_workspace / "overrides").mkdir(parents=True, exist_ok=True)
        (tmp_workspace / "overrides" / "rules.yaml").write_text(
            "matching:\n  bogus_subkey: 0.5\n",
            encoding="utf-8"
        )
        with pytest.raises(RulesConfigError, match="未授权键"):
            init_rules_config(force=True)

    def test_authorized_key_accepted(self, tmp_workspace, monkeypatch):
        """白名单内的 key 不报错"""
        (tmp_workspace / "overrides").mkdir(parents=True, exist_ok=True)
        (tmp_workspace / "overrides" / "rules.yaml").write_text(
            "matching:\n  dimension_weights:\n    D4: 0.22\n",
            encoding="utf-8"
        )
        init_rules_config(force=True)  # 不应抛
        cfg = get_rules_config()
        assert cfg.dimension_weights["D4"] == 0.22


# ─────────────────────────────────────────────────────────
# #2 base.md 不可被 workspace 覆盖
# ─────────────────────────────────────────────────────────

class TestBaseImmutability:
    """workspace 写 prompts/<id>/base.md 应被忽略"""

    def test_workspace_base_ignored(self, tmp_workspace, monkeypatch):
        """workspace 覆盖 base.md → 实际加载的还是 repo 的 base"""
        from tests.conftest import BACKEND_ROOT
        pid = "entity.resume.system"
        # workspace 写一个假的 base
        ws_prompts = tmp_workspace / "overrides" / "prompts" / pid
        ws_prompts.mkdir(parents=True, exist_ok=True)
        (ws_prompts / "base.md").write_text(
            "我是 WORKSPACE 覆盖的假 base，应该被忽略",
            encoding="utf-8"
        )
        # 读 repo 原 base 用于对比
        repo_base = BACKEND_ROOT / "config" / "prompts" / pid / "base.md"
        repo_base_text = repo_base.read_text(encoding="utf-8")

        init_prompt_registry(force=True)
        rendered = load_prompt(pid)
        # 输出应是 repo base 内容，不是 workspace 假 base
        assert "WORKSPACE 覆盖" not in rendered, (
            "workspace base.md 不应被加载"
        )
        # 至少含 repo base 的某个关键词
        assert any(kw in rendered for kw in repo_base_text.split()[:5]), (
            f"加载结果与 repo base 不符\n---\n{rendered[:300]}"
        )


# ─────────────────────────────────────────────────────────
# #3 tone.md 可被 workspace 整文件替换
# ─────────────────────────────────────────────────────────

class TestToneOverride:
    """workspace 写 prompts/<id>/tone.md → 实际加载它"""

    def test_workspace_tone_replaces_repo_tone(self, tmp_workspace, monkeypatch):
        """workspace 替换 tone.md 后，输出含 workspace 标志"""
        pid = "entity.resume.system"
        ws_prompts = tmp_workspace / "overrides" / "prompts" / pid
        ws_prompts.mkdir(parents=True, exist_ok=True)
        marker = "WORKSPACE_TONE_MARKER_42"
        (ws_prompts / "tone.md").write_text(marker, encoding="utf-8")

        init_prompt_registry(force=True)
        rendered = load_prompt(pid)
        assert marker in rendered, (
            f"workspace tone 应被加载,但未含 {marker}\n{rendered[:500]}"
        )


# ─────────────────────────────────────────────────────────
# #6 deep merge 不污染 defaults
# ─────────────────────────────────────────────────────────

class TestDeepMergePreservesDefaults:
    """workspace 改一个叶子，defaults 其他键仍在"""

    def test_override_does_not_clear_other_keys(self, tmp_workspace, monkeypatch):
        """只改 D4 权重，D1/D2/D3 应保持原值"""
        (tmp_workspace / "overrides").mkdir(parents=True, exist_ok=True)
        (tmp_workspace / "overrides" / "rules.yaml").write_text(
            "matching:\n  dimension_weights:\n    D4: 0.99\n",
            encoding="utf-8"
        )
        init_rules_config(force=True)
        cfg = get_rules_config()
        # D4 被覆盖
        assert cfg.dimension_weights["D4"] == 0.99
        # D1/D2/D3 仍为 defaults 值
        assert cfg.dimension_weights["D1"] == pytest.approx(0.25)
        assert cfg.dimension_weights["D2"] == pytest.approx(0.35)
        assert cfg.dimension_weights["D3"] == pytest.approx(0.20)

    def test_override_does_not_clear_sibling_sections(self, tmp_workspace, monkeypatch):
        """改 matching 不影响 thresholds / scoring_tables"""
        (tmp_workspace / "overrides").mkdir(parents=True, exist_ok=True)
        (tmp_workspace / "overrides" / "rules.yaml").write_text(
            "matching:\n  dimension_weights:\n    D4: 0.99\n",
            encoding="utf-8"
        )
        init_rules_config(force=True)
        cfg = get_rules_config()
        # thresholds 段完整
        assert "entity_normalizer" in cfg.thresholds
        assert "semantic" in cfg.thresholds
        assert cfg.semantic_cosine_threshold == 0.85
        # scoring_tables 段完整
        assert "degree_score" in cfg.scoring_tables
        assert cfg.scoring_tables["degree_score"]["本科"] == 18
