from app.services.agent_runtime.context_epoch import ContextEpoch


def test_prompt_epoch_detects_newer_versions_and_effective_content_hashes():
    saved = ContextEpoch(
        prompt_versions={"agent.qa": 1},
        prompt_hashes={"agent.qa": "old"},
    )
    current = ContextEpoch(
        prompt_versions={"agent.qa": 1},
        prompt_hashes={"agent.qa": "new"},
    )
    assert current.needs_refresh(saved)
    assert current.changed_prompts(saved) == ["agent.qa"]


def test_prompt_epoch_serialization_keeps_hashes_and_reads_legacy_data():
    current = ContextEpoch(
        prompt_versions={"agent.qa": 2},
        prompt_hashes={"agent.qa": "abc"},
    )
    restored = ContextEpoch.from_dict(current.to_dict())
    assert restored.prompt_versions == current.prompt_versions
    assert restored.prompt_hashes == current.prompt_hashes
    legacy = ContextEpoch.from_dict({"prompt_versions": {"agent.qa": 1}})
    assert legacy.prompt_hashes == {}
