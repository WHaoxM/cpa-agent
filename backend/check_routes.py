"""检查 Flask 路由"""
import sys
sys.path.insert(0, '.')
from app import create_app

app = create_app()
print("Pipeline 路由:")
for r in app.url_map.iter_rules():
    if 'pipeline' in r.rule:
        print(f"  {r.rule} -> {r.endpoint}")
