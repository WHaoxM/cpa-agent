"""执行 SQL 文件并显示结果"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_helper import run_mysql_file

# SQL 文件路径基于脚本目录推导, 不再写死 Windows 绝对路径
sql_file = Path(__file__).resolve().parent / "seed_more_data.sql"

result = run_mysql_file(str(sql_file), database="cpa_agent", with_table=True)
print("STDOUT:")
print(result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout)
print("STDERR:")
print(result.stderr[-1000:] if result.stderr else "(empty)")
print(f"Return code: {result.returncode}")
