# Learning report completion count evidence

On 2026-10-13, a live Flask test-client request against Docker MySQL reproduced `/api/learning/learning/report/<owner>` returning `completed_courses: 1` for one course with a 50% chapter progress row whose `completed=0`. This contradicted the persisted completion flag. The aggregate now counts distinct courses only from rows with `completed=1`; study-hour and average-progress aggregates remain unchanged.

Replay from the repository root in PowerShell: `$env:CPA_TEST_LIVE_MYSQL='1'; & 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest backend/tests/test_learning_report_contract.py -q`. The opt-in test creates uniquely tagged rows and deletes only its own progress, chapter, and course rows in `finally`. It verifies the HTTP response; the Docker MySQL tables are the authoritative state used by the endpoint. Scope is this report count only, not a whole-platform completion audit.
