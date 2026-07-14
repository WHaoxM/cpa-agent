"""验证 Docker MySQL 中所有视图的数据行数"""
from _db_helper import get_mysql_conn

conn = get_mysql_conn()
cur = conn.cursor()

cur.execute(
    "SELECT table_name FROM information_schema.views "
    "WHERE table_schema='cpa_agent' ORDER BY table_name"
)
views = [r[0] for r in cur.fetchall()]

print(f"共 {len(views)} 个视图\n")
print(f"{'视图名':<35} {'行数':>8}")
print("-" * 50)

empty = []
for v in views:
    cur.execute(f"SELECT COUNT(*) FROM `{v}`")
    cnt = cur.fetchone()[0]
    print(f"{v:<35} {cnt:>8}")
    if cnt == 0:
        empty.append(v)

print("-" * 50)
if empty:
    print(f"空视图 ({len(empty)}): {', '.join(empty)}")
else:
    print("所有视图都有数据")

conn.close()
