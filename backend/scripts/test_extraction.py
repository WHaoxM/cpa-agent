"""测试技能抽取和关系抽取"""
import sys
sys.path.insert(0, '.')

from app.pipeline.entity_extractor.skill_extractor import extract_skills_with_context
from app.pipeline.entity_extractor.relation_extractor import extract_relations

text = "Python后端开发，熟练掌握Django、Flask、MySQL、Redis，了解Docker和Kubernetes"

print("=" * 50)
print("技能抽取测试")
print("=" * 50)

skills = extract_skills_with_context(text)
print(f"\n输入: {text}")
print(f"\n抽取到 {len(skills)} 个技能:")
for s in skills:
    print(f"  [{s['category']}] {s['name']} (置信度: {s['confidence']})")

print("\n" + "=" * 50)
print("关系抽取测试")
print("=" * 50)

skill_names = [s['name'] for s in skills]
relations = extract_relations(skill_names, text)
print(f"\n抽取到 {len(relations)} 个关系:")
for r in relations:
    print(f"  [{r['type']}] {r['source']} → {r['target']} (置信度: {r['confidence']})")
