"""Snapshot orchestration refuses active and existing volume targets."""

import json

import pytest

from scripts.ops import volume_snapshot as snapshot
from scripts.ops.volume_archive import create_archive


def _fixture_snapshot(directory):
    directory.mkdir()
    source = directory.parent / "source"
    source.mkdir()
    (source / "payload").write_text("verified", encoding="utf-8")
    entries = {}
    for key in snapshot.VOLUME_KEYS:
        archive = directory / f"{key}.tar.gz"
        files = directory / f"{key}.files.json"
        create_archive(source, archive, files)
        entries[key] = {
            "source_name": f"old_{key}",
            "archive_sha256": snapshot._sha256(archive),
            "files_sha256": snapshot._sha256(files),
        }
    (directory / "snapshot.json").write_text(json.dumps({
        "format": 1, "source_project": "old", "volumes": entries,
    }), encoding="utf-8")


def test_snapshot_verify_detects_archive_tampering(tmp_path):
    directory = tmp_path / "backup"
    _fixture_snapshot(directory)
    assert snapshot.verify_snapshot(directory)["source_project"] == "old"
    with (directory / "mysql_data.tar.gz").open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(ValueError, match="checksum"):
        snapshot.verify_snapshot(directory)


def test_backup_refuses_running_writer_before_creating_output(monkeypatch, tmp_path):
    names = {key: f"prod_{key}" for key in snapshot.VOLUME_KEYS}
    monkeypatch.setattr(snapshot, "_configuration", lambda *_: ("backend:test", names))
    monkeypatch.setattr(snapshot, "_existing_volumes", lambda: set(names.values()))
    monkeypatch.setattr(snapshot, "_run", lambda *_args, **_kwargs: "image-id")
    monkeypatch.setattr(snapshot, "_require_owned_volumes", lambda *_: None)
    monkeypatch.setattr(snapshot, "_require_stopped",
                        lambda _names: (_ for _ in ()).throw(RuntimeError("running container")))
    output = tmp_path / "must-not-exist"
    with pytest.raises(RuntimeError, match="running"):
        snapshot.backup("prod", output, None)
    assert not output.exists()


def test_backup_rejects_volume_from_a_different_compose_project(monkeypatch, tmp_path):
    names = {key: f"prod_{key}" for key in snapshot.VOLUME_KEYS}
    monkeypatch.setattr(snapshot, "_configuration", lambda *_: ("backend:test", names))
    monkeypatch.setattr(snapshot, "_existing_volumes", lambda: set(names.values()))

    def run(args, **_):
        if args[1:3] == ["volume", "inspect"]:
            return json.dumps({"com.docker.compose.project": "other",
                               "com.docker.compose.volume": args[2][5:]})
        return "image-id"

    monkeypatch.setattr(snapshot, "_run", run)
    output = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="not owned"):
        snapshot.backup("prod", output, None)
    assert not output.exists()


def test_restore_refuses_source_project_or_existing_target_volumes(monkeypatch, tmp_path):
    directory = tmp_path / "backup"
    _fixture_snapshot(directory)
    names = {key: f"new_{key}" for key in snapshot.VOLUME_KEYS}
    monkeypatch.setattr(snapshot, "_configuration", lambda *_: ("backend:test", names))
    monkeypatch.setattr(snapshot, "_run", lambda *_args, **_kwargs: "image-id")
    monkeypatch.setattr(snapshot, "_existing_volumes", lambda: {names["mysql_data"]})
    with pytest.raises(ValueError, match="different project"):
        snapshot.restore("old", directory, None)
    with pytest.raises(ValueError, match="already exist"):
        snapshot.restore("new", directory, None)


def test_restore_creates_compose_owned_target_volumes(monkeypatch, tmp_path):
    directory = tmp_path / "backup"
    _fixture_snapshot(directory)
    names = {key: f"new_{key}" for key in snapshot.VOLUME_KEYS}
    monkeypatch.setattr(snapshot, "_configuration", lambda *_: ("backend:test", names))
    monkeypatch.setattr(snapshot, "_existing_volumes", lambda: set())
    commands = []
    monkeypatch.setattr(snapshot, "_run",
                        lambda args, **_: commands.append(args) or "image-id")
    monkeypatch.setattr(snapshot, "_helper", lambda *_: None)
    snapshot.restore("new", directory, None)
    creates = [args for args in commands if args[1:3] == ["volume", "create"]]
    assert len(creates) == len(snapshot.VOLUME_KEYS)
    for key, name in names.items():
        assert ["docker", "volume", "create", "--label",
                "com.docker.compose.project=new", "--label",
                f"com.docker.compose.volume={key}", name] in creates


def test_cold_archive_uses_ephemeral_root_for_numeric_db_ownership(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(snapshot, "_run", lambda args, **_: calls.append(args) or "")
    snapshot._helper("backend@sha256:" + "0" * 64, "owned_mysql", tmp_path,
                     "mysql_data", "backup")
    assert calls[0][:8] == ["docker", "run", "--rm", "--network=none",
                            "--no-healthcheck", "--user", "0:0", "--mount"]


def test_mysql_backup_explicitly_excludes_only_runtime_socket(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(snapshot, "_run", lambda args, **_: calls.append(args) or "")
    image = "backend@sha256:" + "0" * 64
    snapshot._helper(image, "owned_mysql", tmp_path, "mysql_data", "backup")
    snapshot._helper(image, "owned_redis", tmp_path, "redis_data", "backup")
    assert "--ignore-mysql-runtime-socket" in calls[0]
    assert "--ignore-mysql-runtime-socket" not in calls[1]


@pytest.mark.parametrize("project", ["Prod", "../prod", "", "-prod"])
def test_project_name_is_narrow(project):
    with pytest.raises(ValueError, match="Project name"):
        snapshot._compose(project, None)
