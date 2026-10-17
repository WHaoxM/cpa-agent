"""Cold Docker-volume archive helper using only the Python standard library.

Run inside the backend image with one stopped volume mounted at /source or
/target. Archives contain regular files and directories only; refusing links
keeps extraction confined to the newly created target volume.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile


def _hash_stream(stream) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _safe_name(member: tarfile.TarInfo) -> str:
    path = PurePosixPath(member.name)
    if (not member.name or path.is_absolute() or ".." in path.parts
            or not (member.isfile() or member.isdir())):
        raise ValueError(f"Unsafe archive member: {member.name!r}")
    return path.as_posix()


def archive_manifest(archive_path: Path) -> dict:
    """Hash file contents *inside* the archive, not a possibly changing source."""
    files: dict[str, dict[str, str | int]] = {}
    directories: list[str] = []
    seen: set[str] = set()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            name = _safe_name(member)
            if name in seen:
                raise ValueError(f"Duplicate archive member: {name}")
            seen.add(name)
            if member.isdir():
                directories.append(name)
            else:
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError(f"Unreadable archive member: {name}")
                with stream:
                    files[name] = {"size": member.size, "sha256": _hash_stream(stream)}
    return {"format": 1, "directories": sorted(directories), "files": files}


def create_archive(source: Path, archive_path: Path, files_path: Path, *,
                   ignore_mysql_runtime_socket: bool = False) -> None:
    if not source.is_dir() or source.is_symlink():
        raise ValueError("Source must be a real directory")
    if archive_path.exists() or files_path.exists():
        raise FileExistsError("Archive or file manifest already exists")
    with tarfile.open(archive_path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for base, dirs, files in os.walk(source, followlinks=False):
            dirs.sort()
            files.sort()
            for name in dirs + files:
                path = Path(base) / name
                mode = path.lstat().st_mode
                relative = path.relative_to(source).as_posix()
                # The official MySQL image leaves this dangling runtime-socket
                # link in /var/lib/mysql after shutdown. The server recreates
                # it; no other links are accepted into a cold data snapshot.
                if (ignore_mysql_runtime_socket and relative == "mysql.sock"
                        and stat.S_ISLNK(mode)
                        and os.readlink(path) == "/var/run/mysqld/mysqld.sock"):
                    continue
                if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                    raise ValueError(f"Non-regular source entry: {path}")
                archive.add(path, arcname=relative, recursive=False)
    files_path.write_text(
        json.dumps(archive_manifest(archive_path), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def restore_archive(archive_path: Path, files_path: Path, target: Path) -> None:
    expected = json.loads(files_path.read_text(encoding="utf-8"))
    if archive_manifest(archive_path) != expected:
        raise ValueError("Archive content does not match file manifest")
    if not target.is_dir() or target.is_symlink() or any(target.iterdir()):
        raise ValueError("Restore target must be an empty real directory")
    with tarfile.open(archive_path, "r:gz") as archive:
        # archive_manifest has already rejected traversal, links, special files,
        # and duplicate paths before extraction begins.
        archive.extractall(target, numeric_owner=True)
    actual_files: dict[str, dict[str, str | int]] = {}
    actual_dirs: list[str] = []
    for base, dirs, files in os.walk(target, followlinks=False):
        for name in dirs:
            path = Path(base) / name
            if path.is_symlink():
                raise ValueError("Restore produced a symbolic link")
            actual_dirs.append(path.relative_to(target).as_posix())
        for name in files:
            path = Path(base) / name
            if not path.is_file() or path.is_symlink():
                raise ValueError("Restore produced a non-regular file")
            with path.open("rb") as stream:
                actual_files[path.relative_to(target).as_posix()] = {
                    "size": path.stat().st_size,
                    "sha256": _hash_stream(stream),
                }
    if (sorted(actual_dirs) != expected["directories"]
            or actual_files != expected["files"]):
        raise ValueError("Restored file inventory differs from backup")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "restore"))
    parser.add_argument("source_or_archive", type=Path)
    parser.add_argument("archive_or_manifest", type=Path)
    parser.add_argument("manifest_or_target", type=Path)
    parser.add_argument("--ignore-mysql-runtime-socket", action="store_true",
                        help="Backup only: omit the exact root MySQL socket symlink")
    args = parser.parse_args()
    if args.action != "backup" and args.ignore_mysql_runtime_socket:
        parser.error("--ignore-mysql-runtime-socket is backup-only")
    # Archives contain private database rows and uploaded user documents.
    previous_umask = os.umask(0o077)
    try:
        if args.action == "backup":
            create_archive(args.source_or_archive, args.archive_or_manifest,
                           args.manifest_or_target,
                           ignore_mysql_runtime_socket=args.ignore_mysql_runtime_socket)
        else:
            restore_archive(args.source_or_archive, args.archive_or_manifest,
                            args.manifest_or_target)
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    main()
