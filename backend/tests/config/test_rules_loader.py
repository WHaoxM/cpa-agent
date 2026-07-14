"""
test_rules_loader.py — D1.1 Rules 配置加载器
- 4 段 (matching/thresholds/scoring_tables) 全部加载
- 7 个白名单键全部可访问
- competitiveness_level 档位映射
"""
import pytest
from config.loader import (
    RulesConfig,
    RulesConfigError,
    competitiveness_level,
    get_rules_config,
    init_rules_config,
    resolve_workspace_dir,
)


class TestRulesLoader:
    def test_config_loads(self):
        """RulesConfig 加载成功"""
        cfg = get_rules_config()
        assert isinstance(cfg, RulesConfig)
        assert isinstance(cfg.matching, dict)
        assert isinstance(cfg.thresholds, dict)
        assert isinstance(cfg.scoring_tables, dict)

    def test_3_sections_present(self):
        """3 个 section 都是 RulesConfig 的 dataclass 字段"""
        cfg = get_rules_config()
        # RulesConfig 有 3 个顶层 dataclass 字段
        assert isinstance(cfg.matching, dict) and cfg.matching
        assert isinstance(cfg.thresholds, dict) and cfg.thresholds
        assert isinstance(cfg.scoring_tables, dict) and cfg.scoring_tables
        # matching 段含核心子键
        assert "dimension_weights" in cfg.matching
        assert "match_gates" in cfg.matching
        # thresholds 段含核心子键
        assert "entity_normalizer" in cfg.thresholds
        assert "semantic" in cfg.thresholds
        assert "portrait" in cfg.thresholds
        assert "match" in cfg.thresholds
        # scoring_tables 段含核心子键
        assert "degree_score" in cfg.scoring_tables

    def test_7_whitelist_keys_accessible(self):
        """override_whitelist.yaml 中的 7 个键全部可访问"""
        cfg = get_rules_config()
        # matching 下
        assert isinstance(cfg.dimension_weights, dict)
        assert isinstance(cfg.match_gates, dict)
        assert isinstance(cfg.semantic_weight, float)
        # thresholds 下
        assert isinstance(cfg.alias_threshold, float)
        assert isinstance(cfg.related_threshold, float)
        assert isinstance(cfg.semantic_cosine_threshold, float)
        # portrait 下
        assert isinstance(cfg.competitiveness_levels, dict)

    def test_dimension_weights_sum(self):
        """D1-D4 权重和应为 1.0（冻结公式）"""
        cfg = get_rules_config()
        w = cfg.dimension_weights
        total = sum(w.values())
        assert abs(total - 1.0) < 1e-6, f"权重和 {total} ≠ 1.0"

    def test_competitiveness_level_mapping(self):
        """档位映射: ≥85 极强 / ≥70 强 / ≥55 中等 / ≥40 偏弱 / <40 弱"""
        assert competitiveness_level(90) == "极强"
        assert competitiveness_level(85) == "极强"
        assert competitiveness_level(70) == "强"
        assert competitiveness_level(55) == "中等"
        assert competitiveness_level(40) == "偏弱"
        assert competitiveness_level(39.9) == "弱"
        assert competitiveness_level(0) == "弱"

    def test_init_force_reload(self):
        """init_rules_config(force=True) 重新加载"""
        c1 = get_rules_config()
        c2 = init_rules_config(force=True)
        # 重新加载后值应一致
        assert c1.dimension_weights == c2.dimension_weights

    def test_resolve_workspace_dir_default(self, real_workspace_dir):
        """默认 WORKSPACE_ID=default 应解析到 workspace/default"""
        d = resolve_workspace_dir()
        assert d == real_workspace_dir
