"""
test_prompt_loader.py — D1.2 Prompt 注册表
- 28 个 prompt 全部能加载
- 每个跑一次真实变量替换（c 深度）
- 未注册 id 报错
"""
import pytest
import yaml
from config.prompt_loader import (
    PromptRegistry,
    PromptRegistryError,
    get_prompt_registry,
    init_prompt_registry,
    load_prompt,
)


REGISTRY_PATH = "config/prompts/registry.yaml"


def _load_registry_yaml() -> dict:
    """直接读 registry.yaml 拿到 prompt 元数据"""
    import os
    # 找仓库根
    here = os.path.dirname(os.path.abspath(__file__))
    backend = os.path.dirname(os.path.dirname(here))
    repo = os.path.dirname(backend)
    path = os.path.join(backend, REGISTRY_PATH)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _fake_variables(prompt_id: str, declared_vars: list) -> dict:
    """
    给每个 prompt 生成"够用"的假变量值。
    - 字符串变量: 短占位
    - 数字变量: 0
    - 列表变量: []
    """
    fake = {}
    for v in declared_vars:
        # 全部用非空字符串，避免某些模板有 if 判断
        fake[v] = f"<{v}>"
    return fake


class TestPromptRegistryCount:
    def test_total_prompts_count(self):
        """当前应有 28 个 prompt（阶段五后续收尾）"""
        r = get_prompt_registry()
        ids = r.list_prompt_ids()
        assert len(ids) == 28, f"期望 28, 实际 {len(ids)}: {ids}"

    def test_registry_yaml_consistent(self):
        """registry.yaml 列出的 prompt 与 list_prompt_ids 一致"""
        data = _load_registry_yaml()
        declared_ids = {e["id"] for e in data["prompts"]}
        loaded_ids = set(get_prompt_registry().list_prompt_ids())
        assert declared_ids == loaded_ids, (
            f"yaml 声明 {declared_ids - loaded_ids} 与加载器 {loaded_ids - declared_ids} 不一致"
        )


class TestPromptLoadAll:
    def test_all_loadable(self):
        """28 个 prompt 全部能加载（不带变量）"""
        r = get_prompt_registry()
        data = _load_registry_yaml()
        for entry in data["prompts"]:
            pid = entry["id"]
            # 无 variables 的可直接 load，有 variables 的给空 dict
            declared = entry.get("variables") or []
            r.load(pid, **_fake_variables(pid, declared))


class TestPromptVariableSubstitution:
    """
    c 深度: 每个 prompt 跑一次真实变量替换。
    抓 4A 类 f-string 残留 / JSON 模板花括号转义漏改。
    """

    @pytest.mark.parametrize("prompt_id,variables,expected_substr", [
        # entity_extractor - resume
        ("entity.resume.user.basic_info", {"indexed_text": "[1] 张三"}, "张三"),
        ("entity.resume.user.education", {"indexed_text": "[2] 清华大学", "confirmed": ""}, "清华大学"),
        ("entity.resume.user.experience", {"indexed_text": "[3] 项目经历", "confirmed": ""}, "项目经历"),
        ("entity.resume.user.skills", {"indexed_text": "[4] Python", "constraint": ""}, "Python"),
        # entity_extractor - jd
        ("entity.jd.user.job_info", {"jd_text": "招聘 Python 工程师"}, "Python 工程师"),
        ("entity.jd.user.requirements", {"jd_text": "本科起步", "constraints": ""}, "本科"),
        ("entity.jd.user.description", {"jd_text": "负责前后端开发"}, "前后端开发"),
        # entity_normalizer
        ("entity.normalize.user.award", {"name": "ACM金牌", "ctx": "全国赛"}, "ACM金牌"),
        ("entity.normalize.user.generic", {"label": "skill", "name": "Vue", "ctx": "前端"}, "Vue"),
        # cypher
        ("cypher.intent.user", {"query": "查询Python岗位"}, "Python岗位"),
        ("cypher.generate.user", {"query": "列出所有学生"}, "所有学生"),
        ("cypher.rewrite.user", {"original_query": "查薪资", "result_json": "{}"}, "查薪资"),
        # report
        ("report.summary.user", {
            "match_score": 80.5, "match_level": "强",
            "skill_coverage_pct": 75, "matched_skills": "Python",
            "missing_skills": "Go", "gap_lines": "补Go"
        }, "80.5"),
        # llm_skill_extractor
        ("entity.skill_extractor.user", {"text": "熟练 Python 和 FastAPI"}, "Python 和 FastAPI"),
        # nsle_pipeline
        ("pipeline.nsle.extract.skills.user", {"text": "熟练 Docker 和 Kubernetes"}, "Docker 和 Kubernetes"),
        ("pipeline.nsle.extract.experience.user", {"text": "在腾讯担任后端开发"}, "腾讯"),
        ("pipeline.nsle.extract.education.user", {"text": "北京大学计算机本科"}, "北京大学"),
        ("pipeline.nsle.extract.certificates.user", {"text": "通过软考高级"}, "软考高级"),
        # admin_kb
        ("admin.kb.extract_entities.user", {"source_type": "jd", "content": "招聘算法工程师"}, "算法工程师"),
        # scripts
        ("script.vector.skill_enrich.user", {
            "name": "Vue 3", "cat": "前端", "sub": "框架", "tier": 2
        }, "Vue 3"),
        ("script.vector.jd_chunk.user", {
            "position": "前端工程师", "company": "字节", "category": "互联网"
        }, "前端工程师"),
        ("script.vector.skill_seed_enrich.user", {
            "name": "CFA", "cat": "金融", "sub": "证书", "tier": 3, "orig": "特许金融分析师"
        }, "CFA"),
    ])
    def test_variable_substitution(self, prompt_id, variables, expected_substr):
        """真实变量替换: 输出应含 expected_substr"""
        r = get_prompt_registry()
        rendered = r.load(prompt_id, **variables)
        assert expected_substr in rendered, (
            f"{prompt_id} 替换后未含 {expected_substr!r}\n---\n{rendered[:500]}"
        )

    def test_json_template_braces_preserved(self):
        """JSON 模板中的 {{ 转义为 { 后保留"""
        r = get_prompt_registry()
        rendered = r.load("entity.resume.user.basic_info", indexed_text="x")
        # {{"name": ""}} 渲染后应为 {"name": ""}
        assert '{"name":' in rendered, f"JSON 模板花括号转义丢失: {rendered[:300]}"


class TestPromptLoadErrors:
    def test_unknown_prompt_id(self):
        """未知 prompt_id 报错"""
        r = get_prompt_registry()
        with pytest.raises(PromptRegistryError, match="未知 prompt_id"):
            r.load("not.exists.user")

    def test_missing_variable(self):
        """模板里有变量但传错 key → 报错"""
        r = get_prompt_registry()
        with pytest.raises(PromptRegistryError, match="缺少变量"):
            # entity.resume.user.basic_info 需要 indexed_text, 故意传错
            r.load("entity.resume.user.basic_info", wrong_key="x")
