"""Hybrid retrieve + explain must not mutate deterministic match scores."""

from __future__ import annotations

from app.services.hybrid_retrieve import build_match_explain, rrf_fuse


def test_rrf_fuse_prefers_consensus():
    fused = rrf_fuse(
        [
            ["a", "b", "c"],
            ["b", "a", "d"],
        ]
    )
    ids = [x[0] for x in fused]
    assert ids[0] in ("a", "b")
    assert "d" in ids


def test_build_match_explain_passthrough_scores():
    score = 77.5
    coverage = 0.82
    expl = build_match_explain(
        student_id="student_001",
        job_portrait_id="job_001",
        matched_skills=["Python", "SQL"],
        missing_skills=["Kubernetes"],
        total_score=score,
        skill_coverage=coverage,
        position_name="后端开发",
        gaps=[{"skill": "Kubernetes", "priority": "critical"}],
        student_skill_names=["Python", "SQL"],
    )
    assert expl["total_score"] == score
    assert expl["skill_coverage"] == coverage
    assert expl["scores_authoritative"] is True
    assert "Kubernetes" in expl["keyword_coverage"]["highlight_missing"]
    assert any(e["status"] == "covered" for e in expl["evidence"])
    assert any(e["status"] == "gap" for e in expl["evidence"])


def test_explain_does_not_recompute_score_from_skills():
    """Even with empty overlaps, stored score is unchanged."""
    expl = build_match_explain(
        student_id="s",
        job_portrait_id="j",
        matched_skills=[],
        missing_skills=["X"],
        total_score=12.0,
        skill_coverage=0.1,
    )
    assert expl["total_score"] == 12.0
    assert expl["skill_coverage"] == 0.1
