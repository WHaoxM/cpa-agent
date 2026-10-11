"""
test_workspace_loaders.py — D1.6 workspace seeds + branding 加载器
- 2 seeds (skills/jobs) 加载
- branding 加载（含 brand.yaml）
- 无 seeds 目录时返回空（不报错）
"""
import pytest
import yaml
from config.workspace_seeds_loader import (
    load_seeds_skills,
    load_seeds_jobs,
    has_seeds,
)
from config.workspace_branding_loader import load_branding, has_branding


class TestWorkspaceSeeds:
    def test_has_seeds_true_with_default_ws(self, real_workspace_dir):
        """workspace/default 含 seeds 目录"""
        assert has_seeds() is True

    def test_load_seeds_skills(self, real_workspace_dir):
        """skills.yaml 加载: 至少 1 个追加技能"""
        skills = load_seeds_skills()
        assert isinstance(skills, list)
        assert len(skills) >= 1
        # 字段结构
        s = skills[0]
        assert "name" in s
        assert "category" in s

    def test_load_seeds_jobs(self, real_workspace_dir):
        """jobs.yaml 加载: 至少 1 个追加岗位"""
        jobs = load_seeds_jobs()
        assert isinstance(jobs, list)
        assert len(jobs) >= 1
        j = jobs[0]
        assert "job_id" in j
        assert "position_name" in j

    def test_no_seeds_returns_empty(self, tmp_workspace):
        """无 seeds 目录时返回空列表,不报错"""
        assert has_seeds() is False
        assert load_seeds_skills() == []
        assert load_seeds_jobs() == []


class TestWorkspaceBranding:
    def test_has_branding_true_with_default_ws(self, real_workspace_dir):
        """workspace/default 含 branding 目录"""
        assert has_branding() is True

    def test_load_branding(self, real_workspace_dir):
        """branding loader reads the current sample, not a frozen brand name."""
        b = load_branding()
        assert isinstance(b, dict)
        brand_path = real_workspace_dir / "branding" / "brand.yaml"
        expected = yaml.safe_load(brand_path.read_text(encoding="utf-8"))
        for key in ("accent_color", "font_family", "company_name"):
            assert b.get(key) == expected.get(key)

    def test_no_branding_returns_empty(self, tmp_workspace):
        """无 branding 目录时返回空 dict"""
        assert has_branding() is False
        assert load_branding() == {}
