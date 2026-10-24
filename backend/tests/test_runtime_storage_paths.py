import os
import subprocess
import sys
import uuid
from pathlib import Path

from app.utils.logger import setup_logger


BACKEND = Path(__file__).resolve().parents[1]


def _load_upload_folder(tmp_path: Path, configured: str | None) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / "runtime.env"
    lines = ["SECRET_KEY=synthetic-test-only", "CPA_DEMO_MODE=true"]
    if configured is not None:
        lines.append(f"CPA_UPLOAD_FOLDER={configured}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
        if key in os.environ
    }
    env.update(
        {
            "CPA_ENV_FILE": str(env_file),
            "PYTHONPATH": str(BACKEND),
            "PYTHONUTF8": "1",
        }
    )
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "from app.config import Config; print(Config.UPLOAD_FOLDER)",
        ],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_upload_folder_uses_private_runtime_environment_path(tmp_path: Path) -> None:
    upload_dir = tmp_path / "private-upload-data"
    result = _load_upload_folder(tmp_path, str(upload_dir))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(upload_dir)


def test_upload_folder_keeps_development_default_without_override(tmp_path: Path) -> None:
    result = _load_upload_folder(tmp_path, None)

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == (BACKEND / "uploads").resolve()


def test_logger_uses_separate_runtime_log_directory(tmp_path: Path, monkeypatch) -> None:
    log_dir = tmp_path / "private-runtime-logs"
    monkeypatch.setenv("CPA_LOG_DIR", str(log_dir))
    logger_name = f"test-runtime-log-{uuid.uuid4().hex}"
    logger = setup_logger(logger_name)
    try:
        logger.info("runtime log path probe")
        for handler in logger.handlers:
            handler.flush()

        assert log_dir.is_dir()
        assert (log_dir / f"{logger_name}.log").is_file()
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
