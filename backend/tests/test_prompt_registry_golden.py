"""CI-friendly golden check: registry paths exist (promptfoo companion)."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_registry_prompt_files_exist():
    root = Path(__file__).resolve().parents[1]
    registry = root / "config" / "prompts" / "registry.yaml"
    assert registry.is_file(), registry
    data = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
    prompts = data.get("prompts") or []
    assert isinstance(prompts, list) and prompts, "registry empty"

    missing = []
    for meta in prompts:
        if not isinstance(meta, dict):
            continue
        pid = meta.get("id") or "?"
        for key in ("base", "tone"):
            rel = meta.get(key)
            if not rel:
                continue
            candidates = [
                root / "config" / "prompts" / rel,
                root / rel,
            ]
            if not any(c.is_file() for c in candidates):
                missing.append(f"{pid}.{key}={rel}")
    assert not missing, missing[:30]


def test_topology_frozen_rejects_unknown():
    from app.services.agent_runtime.topology import assert_subagent_allowed, is_subagent_allowed

    assert is_subagent_allowed("match")
    assert is_subagent_allowed("match_subagent")
    try:
        assert_subagent_allowed("marketing_swarm_99")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
