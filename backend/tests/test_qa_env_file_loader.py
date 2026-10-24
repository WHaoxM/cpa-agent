import os
import subprocess
import sys
import tempfile
from pathlib import Path


def test_config_loader_uses_explicit_qa_file_without_loading_root_db_target():
    with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as temp_dir:
        qa_env = Path(temp_dir) / "qa.env"
        qa_env.write_text(
            "MYSQL_HOST=127.0.0.1\n"
            "MYSQL_PORT=13306\n"
            "MYSQL_USER=cpa_qa_runtime_test\n"
            "MYSQL_PASSWORD=test-only\n"
            "MYSQL_DATABASE=cpa_agent_qa_loader_test\n"
            "REDIS_URL=redis://127.0.0.1:16381/0\n"
            f"NEO4J_AUTH_FILE={Path(temp_dir) / 'neo4j.env'}\n"
            "SECRET_KEY=test-only\n",
            encoding="utf-8",
        )
        (Path(temp_dir) / "neo4j.env").write_text(
            "NEO4J_AUTH=neo4j/synthetic-test-only\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        for key in list(env):
            if (
                key.startswith(("CPA_", "LLM_", "MYSQL_", "NEO4J_", "REDIS_", "CELERY_", "FLASK_", "LITELLM_"))
                or key.endswith("_API_KEY")
                or key in {"SECRET_KEY", "OPENAI_API_KEY"}
            ):
                env.pop(key, None)
        env["CPA_ENV_FILE"] = str(qa_env)

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from app.config import Config; print(Config.MYSQL_DATABASE + ';' + Config.NEO4J_USER + ';' + str(bool(Config.NEO4J_PASSWORD)))",
            ],
            cwd=os.path.dirname(__file__) + "/..",
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert "cpa_agent_qa_loader_test" in result.stdout
        assert "cpa_agent\n" not in result.stdout
        assert "neo4j;True" in result.stdout


def test_config_loader_fails_closed_for_missing_explicit_env_file():
    with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as temp_dir:
        env = os.environ.copy()
        env["CPA_ENV_FILE"] = str(Path(temp_dir) / "missing.env")

        result = subprocess.run(
            [sys.executable, "-c", "import app.config"],
            cwd=os.path.dirname(__file__) + "/..",
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        assert "CPA_ENV_FILE must point to an existing absolute private config file" in result.stderr
