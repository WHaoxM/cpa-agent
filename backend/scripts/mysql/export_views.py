"""从 MySQL 导出所有视图定义, 生成干净的视图 SQL 文件"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_helper import get_mysql_cmd, get_mysql_env
import subprocess

# 获取所有视图名
list_cmd = get_mysql_cmd(database="cpa_agent") + [
    "-N", "-B",
    "-e", "SELECT TABLE_NAME FROM information_schema.VIEWS WHERE TABLE_SCHEMA='cpa_agent' ORDER BY TABLE_NAME;"
]
result = subprocess.run(
    list_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
    env=get_mysql_env(),
)
views = [v.strip() for v in result.stdout.strip().split('\n') if v.strip()]

print(f"找到 {len(views)} 个视图")
for v in views:
    print(f"  - {v}")

# 获取每个视图的 CREATE 语句
output_lines = []
output_lines.append("-- ============================================================")
output_lines.append("-- CPA-Agent 数据库视图设计")
output_lines.append(f"-- 版本: v1.0")
output_lines.append(f"-- 日期: 2026-07-12")
output_lines.append(f"-- 视图总数: {len(views)} 个")
output_lines.append("-- 说明: 本文件包含所有业务视图定义, 可直接执行创建")
output_lines.append("-- ============================================================")
output_lines.append("")
output_lines.append("USE cpa_agent;")
output_lines.append("")

for view in views:
    # 获取视图定义
    cmd = get_mysql_cmd(database="cpa_agent") + [
        "-N", "-B",
        "-e", f"SHOW CREATE VIEW {view};"
    ]
    res = subprocess.run(
        cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
        env=get_mysql_env(),
    )
    if res.returncode == 0:
        # 输出格式: view_name \t create_statement \t charset \t collation
        parts = res.stdout.split('\t')
        if len(parts) >= 2:
            create_sql = parts[1].strip()
            # 替换 CREATE ALGORITHM... 为 DROP + CREATE
            output_lines.append(f"-- ------------------------------------------------------------")
            output_lines.append(f"-- 视图: {view}")
            output_lines.append(f"-- ------------------------------------------------------------")
            output_lines.append(f"DROP VIEW IF EXISTS {view};")
            output_lines.append(create_sql + ";")
            output_lines.append("")

# 写入文件 (基于脚本目录推导, 不写死绝对路径)
output_path = Path(__file__).resolve().parent / "views_design.sql"
with open(output_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(output_lines))

newline = chr(10)
total_size = len(newline.join(output_lines))
print(f"\n导出完成: {output_path}")
print(f"文件大小: {total_size} 字符")
