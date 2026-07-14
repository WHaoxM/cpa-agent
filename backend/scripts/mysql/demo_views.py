"""展示 20 个视图的查询结果, 模拟命令行 mysql 表格输出"""
import os
import sys
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_helper import get_mysql_cmd, get_mysql_env

# 20 个视图列表
views = [
    ("v_student_full_profile", 20),
    ("v_job_match_summary", 20),
    ("v_student_skill_gap_detail", 20),
    ("v_career_report_summary", 20),
    ("v_career_path_detail", 20),
    ("v_student_career_full", 20),
    ("v_student_class_info", 20),
    ("v_teacher_students", 20),
    ("v_user_activity_stats", 20),
    ("v_admin_operations", 10),
    ("v_knowledge_base_status", 20),
    ("v_dataset_status", 20),
    ("v_oov_pending_review", 20),
    ("v_oov_reviewed_history", 20),
    ("v_career_skill_map", 20),
    ("v_course_progress_detail", 20),
    ("v_student_grade_summary", 20),
    ("v_learning_path_full", 20),
    ("v_conversation_full", 20),
    ("v_pipeline_monitor", 20),
]

for view, limit in views:
    print(f"\n{'='*100}")
    print(f"mysql> SELECT * FROM {view} LIMIT {limit};")
    print(f"{'='*100}")
    cmd = get_mysql_cmd(database="cpa_agent", with_table=True) + [
        "-e", f"SELECT * FROM {view} LIMIT {limit};"
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
        env=get_mysql_env(),
    )
    if result.returncode == 0:
        print(result.stdout)
    else:
        print(f"错误: {result.stderr}")
    print()
