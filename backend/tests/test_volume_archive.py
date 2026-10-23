"""Cold-volume archive must restore exact files without traversal or links."""

import io
import os
from pathlib import Path
import tarfile

import pytest

from scripts.ops.volume_archive import archive_manifest, create_archive, restore_archive


def test_archive_round_trip_verifies_files_and_empty_directories(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "empty").mkdir()
    (source / "nested").mkdir()
    (source / "nested" / "payload.bin").write_bytes(b"\x00\x01payload")
    archive = tmp_path / "snapshot.tar.gz"
    files = tmp_path / "snapshot.files.json"
    target = tmp_path / "target"
    target.mkdir()

    create_archive(source, archive, files)
    restore_archive(archive, files, target)

    assert (target / "empty").is_dir()
    assert (target / "nested" / "payload.bin").read_bytes() == b"\x00\x01payload"
    assert archive_manifest(archive)["files"]["nested/payload.bin"]["size"] == 9


def test_restore_refuses_a_nonempty_target(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_text("unchanged", encoding="utf-8")
    archive = tmp_path / "snapshot.tar.gz"
    files = tmp_path / "snapshot.files.json"
    create_archive(source, archive, files)
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep").write_text("preserve", encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        restore_archive(archive, files, target)
    assert (target / "keep").read_text(encoding="utf-8") == "preserve"


@pytest.mark.parametrize("name,kind", [("../escape", "file"), ("link", "link")])
def test_unsafe_members_are_rejected_before_restore(tmp_path, name, kind):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        member = tarfile.TarInfo(name)
        if kind == "link":
            member.type = tarfile.SYMTYPE
            member.linkname = "../escape"
            output.addfile(member)
        else:
            member.size = 1
            output.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="Unsafe"):
        archive_manifest(archive)


def test_mysql_runtime_socket_requires_explicit_exclusion(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload").write_text("keep", encoding="utf-8")
    try:
        os.symlink("/var/run/mysqld/mysqld.sock", source / "mysql.sock")
    except OSError as exc:
        pytest.skip(f"Windows symlink privilege unavailable: {exc}")
    with pytest.raises(ValueError, match="Non-regular"):
        create_archive(source, tmp_path / "rejected.tar.gz", tmp_path / "rejected.json")

    archive = tmp_path / "accepted.tar.gz"
    files = tmp_path / "accepted.json"
    create_archive(source, archive, files, ignore_mysql_runtime_socket=True)
    assert set(archive_manifest(archive)["files"]) == {"payload"}


@pytest.mark.parametrize("name,target", [
    ("mysql.sock", "/tmp/not-the-mysql-runtime-socket"),
    ("other.sock", "/var/run/mysqld/mysqld.sock"),
])
def test_mysql_socket_exclusion_rejects_other_links(tmp_path, name, target):
    source = tmp_path / "source"
    source.mkdir()
    try:
        os.symlink(target, source / name)
    except OSError as exc:
        pytest.skip(f"Windows symlink privilege unavailable: {exc}")
    with pytest.raises(ValueError, match="Non-regular"):
        create_archive(source, tmp_path / "archive.tar.gz", tmp_path / "files.json",
                       ignore_mysql_runtime_socket=True)
