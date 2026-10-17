"""Cold snapshot/restore for the essential single-host production Compose volumes.

This never stops or starts services and never overwrites a volume. Stop every
writer with ``docker compose stop`` first. Restore always targets a different,
fresh Compose project so the source remains available for rollback.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

if __package__:
    from .volume_archive import archive_manifest
else:
    from volume_archive import archive_manifest


ROOT = Path(__file__).resolve().parents[3]
VOLUME_KEYS = (
    "mysql_data",
    "neo4j_data",
    "redis_data",
    "backend_uploads",
    "backend_milvus_data",
)
PROJECT_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


def _run(args: list[str], *, cwd: Path = ROOT) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True,
                            check=False)
    if result.returncode:
        # Compose config may contain resolved secrets. Never echo stdout or
        # stderr, which may include resolved environment values.
        operation = args[1] if len(args) > 1 else "docker"
        raise RuntimeError(f"Docker {operation} failed with exit code {result.returncode}")
    return result.stdout.strip()


def _compose(project: str, env_file: Path | None) -> list[str]:
    if not PROJECT_NAME.fullmatch(project):
        raise ValueError("Project name must use lower-case letters, digits, _ or -")
    command = ["docker", "compose"]
    if env_file is not None:
        command += ["--env-file", str(env_file.resolve())]
    return command + ["-p", project, "-f", "docker-compose.yml",
                      "-f", "docker-compose.prod.yml"]


def _configuration(project: str, env_file: Path | None) -> tuple[str, dict[str, str]]:
    config = json.loads(_run(_compose(project, env_file) + ["config", "--format", "json"]))
    volumes = config.get("volumes", {})
    missing = set(VOLUME_KEYS) - set(volumes)
    if missing:
        raise ValueError(f"Production Compose is missing volume keys: {sorted(missing)}")
    names = {key: str(volumes[key]["name"]) for key in VOLUME_KEYS}
    if len(set(names.values())) != len(names):
        raise ValueError("Production Compose resolved duplicate volume names")
    return str(config["services"]["backend"]["image"]), names


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _existing_volumes() -> set[str]:
    output = _run(["docker", "volume", "ls", "--format", "{{.Name}}"])
    return set(output.splitlines())


def _require_owned_volumes(project: str, names: dict[str, str]) -> None:
    for key, name in names.items():
        labels = json.loads(_run(["docker", "volume", "inspect", name,
                                  "--format", "{{json .Labels}}"])) or {}
        if (labels.get("com.docker.compose.project") != project
                or labels.get("com.docker.compose.volume") != key):
            raise ValueError(f"Volume {key} is not owned by Compose project {project}")


def _require_stopped(names: dict[str, str]) -> None:
    for key, name in names.items():
        active = _run(["docker", "ps", "--filter", f"volume={name}",
                       "--format", "{{.ID}}"])
        if active:
            raise RuntimeError(f"Volume {key} is used by a running container; stop all writers")


def _helper(image: str, volume_name: str, backup_dir: Path,
            key: str, action: str) -> None:
    if action == "backup":
        volume_mount = f"type=volume,source={volume_name},target=/source,readonly"
        bind_mount = f"type=bind,source={backup_dir},target=/backup"
        arguments = ["backup", "/source", f"/backup/{key}.tar.gz",
                     f"/backup/{key}.files.json"]
        if key == "mysql_data":
            arguments.append("--ignore-mysql-runtime-socket")
    else:
        volume_mount = f"type=volume,source={volume_name},target=/target"
        bind_mount = f"type=bind,source={backup_dir},target=/backup,readonly"
        arguments = ["restore", f"/backup/{key}.tar.gz",
                     f"/backup/{key}.files.json", "/target"]
    # The application image runs as UID 10001. Offline cold-volume handling
    # requires root to read DB-owned files and preserve numeric ownership on
    # restore; never grant this identity to a long-running service.
    _run(["docker", "run", "--rm", "--network=none", "--no-healthcheck",
          "--user", "0:0",
          "--mount", volume_mount, "--mount", bind_mount,
          image, "python", "-B", "/app/scripts/ops/volume_archive.py"] + arguments)


def verify_snapshot(directory: Path) -> dict:
    manifest_path = directory / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or set(manifest.get("volumes", {})) != set(VOLUME_KEYS):
        raise ValueError("Snapshot manifest is incomplete or unsupported")
    for key in VOLUME_KEYS:
        item = manifest["volumes"][key]
        archive = directory / f"{key}.tar.gz"
        files = directory / f"{key}.files.json"
        if (_sha256(archive) != item["archive_sha256"]
                or _sha256(files) != item["files_sha256"]):
            raise ValueError(f"Snapshot checksum mismatch: {key}")
        if archive_manifest(archive) != json.loads(files.read_text(encoding="utf-8")):
            raise ValueError(f"Snapshot file inventory mismatch: {key}")
    return manifest


def backup(project: str, directory: Path, env_file: Path | None) -> None:
    image, names = _configuration(project, env_file)
    _run(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
    existing = _existing_volumes()
    missing = set(names.values()) - existing
    if missing:
        raise ValueError(f"Required source volumes do not exist: {sorted(missing)}")
    _require_owned_volumes(project, names)
    _require_stopped(names)
    directory = directory.resolve()
    if directory.exists():
        raise FileExistsError("Backup destination must not already exist")
    directory.mkdir(parents=True, mode=0o700)
    os.chmod(directory, 0o700)
    volumes: dict[str, dict[str, str]] = {}
    for key, name in names.items():
        _require_stopped(names)
        _helper(image, name, directory, key, "backup")
        volumes[key] = {
            "source_name": name,
            "archive_sha256": _sha256(directory / f"{key}.tar.gz"),
            "files_sha256": _sha256(directory / f"{key}.files.json"),
        }
    manifest = {
        "format": 1,
        "source_project": project,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "helper_image": image,
        "volumes": volumes,
    }
    manifest_path = directory / "snapshot.json"
    manifest_path.touch(mode=0o600, exist_ok=False)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    verify_snapshot(directory)


def restore(project: str, directory: Path, env_file: Path | None) -> None:
    directory = directory.resolve()
    manifest = verify_snapshot(directory)
    if project == manifest["source_project"]:
        raise ValueError("Restore must use a different project name from the source")
    image, names = _configuration(project, env_file)
    _run(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
    existing = _existing_volumes()
    collisions = set(names.values()) & existing
    if collisions:
        raise ValueError(f"Restore target volumes already exist: {sorted(collisions)}")
    for key, name in names.items():
        _run(["docker", "volume", "create",
              "--label", f"com.docker.compose.project={project}",
              "--label", f"com.docker.compose.volume={key}", name])
        _helper(image, name, directory, key, "restore")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "verify", "restore"))
    parser.add_argument("--project", help="Explicit Compose project name")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--env-file", type=Path,
                        help="Compose interpolation file; default is repo-root .env")
    args = parser.parse_args()
    if args.action == "verify":
        verify_snapshot(args.directory.resolve())
    elif not args.project:
        parser.error("--project is required for backup/restore")
    elif args.action == "backup":
        backup(args.project, args.directory, args.env_file)
    else:
        restore(args.project, args.directory, args.env_file)


if __name__ == "__main__":
    main()
