"""测试 LLM 增强的技能抽取"""
import sys
sys.path.insert(0, '.')

from app.pipeline.entity_extractor.llm_skill_extractor import extract_skills_with_relations

test_text = """
Python后端开发工程师

熟练掌握：
- Python, Django, Flask
- MySQL, Redis
- Docker 容器化部署

了解/接触过：
- Vue.js 前端开发
- Kubernetes 集群管理
- 机器学习基础

加分项：
- 有 TensorFlow 或 PyTorch 项目经验
- 熟悉 AWS 或阿里云
"""

print("=" * 60)
print("LLM 增强技能抽取测试")
print("=" * 60)

result = extract_skills_with_relations(test_text, use_llm=True)

print(f"\n抽取到 {len(result['skills'])} 个技能:")
for s in result["skills"]:
    src = f"[{s.get('source', 'unknown')}]"
    print(f"  {src:8} {s['name']:15} ({s['category']})")

print(f"\n抽取到 {len(result['relations'])} 个关系:")
for r in result["relations"]:
    print(f"  {r['source']} --({r['type']})--> {r['target']}")
