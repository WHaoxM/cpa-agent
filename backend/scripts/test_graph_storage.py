"""测试图谱存储"""
import sys
sys.path.insert(0, '.')

from app.pipeline.entity_extractor.graph_storage import save_skills_to_graph, GraphStorage

test_skills = [
    {"name": "Python", "category": "编程语言", "confidence": 0.8, "source": "dict"},
    {"name": "Django", "category": "后端框架", "confidence": 0.75, "source": "dict"},
    {"name": "MySQL", "category": "数据库", "confidence": 0.7, "source": "dict"},
    {"name": "Redis", "category": "数据库", "confidence": 0.65, "source": "dict"},
    {"name": "Vue.js", "category": "前端框架", "confidence": 0.6, "source": "dict"},
]

test_relations = [
    {"source": "Django", "target": "Python", "type": "prerequisite", "confidence": 0.8},
    {"source": "MySQL", "target": "Redis", "type": "similar", "confidence": 0.6},
]

print("=" * 50)
print("图谱存储测试")
print("=" * 50)

result = save_skills_to_graph(
    entity_id="test_student_001",
    entity_type="student",
    skills=test_skills,
    relations=test_relations
)

print(f"\n保存结果:")
print(f"  实体ID: {result['entity_id']}")
print(f"  保存技能: {result['saved_skills']}")
print(f"  保存关系: {result['saved_relations']}")
print(f"  时间: {result['timestamp']}")

print("\n查询测试:")
storage = GraphStorage()
neighbors = storage.get_skill_neighbors("Django")
print(f"  Django 的邻居: {[n['name'] for n in neighbors]}")

entity_skills = storage.get_entity_skills("test_student_001", "student")
print(f"  test_student_001 的技能: {[s['name'] for s in entity_skills]}")
