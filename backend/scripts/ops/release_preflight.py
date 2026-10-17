"""Fail-closed static admission checks for the single-host production overlay.

This checks the *resolved* Compose model, not live dependencies, TLS, backups,
or a user journey. It deliberately never prints resolved configuration because
Compose includes credentials in service environments.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[3]
FILES = ("docker-compose.yml", "docker-compose.prod.yml")
IMAGE_DIGEST = re.compile(r"^[^\s@]+@sha256:[a-fA-F0-9]{64}$")
SERVICES = ("mysql", "neo4j", "redis", "backend", "worker", "beat")


def host_memory_bytes() -> int | None:
    """Linux physical memory; None on a non-Linux validation host."""
    try:
        line = next(line for line in Path("/proc/meminfo").read_text().splitlines()
                    if line.startswith("MemTotal:"))
        return int(line.split()[1]) * 1024
    except (OSError, StopIteration, ValueError, IndexError):
        return None


def _https_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        parsed.port  # Validate port syntax.
    except ValueError:
        return False
    return (parsed.scheme == "https" and bool(parsed.hostname)
            and not parsed.username and not parsed.password and not parsed.path
            and not parsed.query and not parsed.fragment
            and value == f"{parsed.scheme}://{parsed.netloc}")


def check_configuration(config: dict, *, available_memory: int | None = None) -> list[str]:
    """Return labels only; never return a credential or resolved config value."""
    errors: list[str] = []
    services = config.get("services", {})
    missing = set(SERVICES) - set(services)
    if missing:
        return ["missing required services: " + ", ".join(sorted(missing))]
    backend = services["backend"]
    backend_env = backend.get("environment", {})
    images = [services[name].get("image", "") for name in SERVICES]
    if not all(IMAGE_DIGEST.fullmatch(image) for image in images):
        errors.append("all production images must be registry digest references")
    if any("build" in services[name] for name in SERVICES):
        errors.append("production services must not rebuild images")
    if any(services[name].get("image") != backend.get("image")
           for name in ("worker", "beat")):
        errors.append("HTTP, worker, and Beat must use the same backend image")
    if any(services[name].get("environment") != backend_env
           for name in ("worker", "beat")):
        errors.append("HTTP, worker, and Beat must use the same backend environment")
    if backend_env.get("CPA_DEMO_MODE") != "false" or backend_env.get("FLASK_DEBUG") != "false":
        errors.append("demo/debug must be disabled")
    if backend_env.get("CPA_AUTH_COOKIE_SECURE") != "true":
        errors.append("account cookies must be HTTPS-only")
    origins = str(backend_env.get("CORS_ALLOWED_ORIGINS", "")).split(",")
    if not origins or not all(_https_origin(origin.strip()) for origin in origins):
        errors.append("production origins must be explicit HTTPS origins")
    secret = str(backend_env.get("SECRET_KEY", ""))
    if len(secret) < 32 or secret.lower().startswith("your-") or "insecure-default" in secret:
        errors.append("SECRET_KEY is unset or a template/default")
    if not backend_env.get("LLM_API_KEY") or str(backend_env["LLM_API_KEY"]).startswith("your-"):
        errors.append("LLM_API_KEY is unset or a template")
    mysql = services["mysql"].get("environment", {})
    if (backend_env.get("MYSQL_USER") in (None, "", "root")
            or not backend_env.get("MYSQL_PASSWORD")
            or backend_env.get("MYSQL_PASSWORD") == mysql.get("MYSQL_ROOT_PASSWORD")):
        errors.append("application MySQL account must be non-root with a distinct password")
    if (not mysql.get("MYSQL_ROOT_PASSWORD") or not mysql.get("MYSQL_PASSWORD")
            or mysql.get("MYSQL_ROOT_PASSWORD") == "!Rootmysql123"
            or mysql.get("MYSQL_PASSWORD") == "!Rootmysql123"):
        errors.append("MySQL credentials are missing")
    if (not services["neo4j"].get("environment", {}).get("NEO4J_AUTH")
            or not backend_env.get("NEO4J_PASSWORD")
            or backend_env.get("NEO4J_PASSWORD") == "neo4jpass"):
        errors.append("Neo4j credentials are missing")
    if any(services[name].get("ports") for name in ("mysql", "neo4j", "redis")):
        errors.append("databases must not publish host ports")
    for port in backend.get("ports", []):
        if port.get("host_ip") not in ("127.0.0.1", "::1"):
            errors.append("HTTP may only bind a loopback host-proxy port")
            break
    total_memory = 0
    for name in SERVICES:
        service = services[name]
        try:
            memory = int(service["mem_limit"])
            cpus = float(service["cpus"])
            if memory <= 0 or cpus <= 0:
                raise ValueError
            total_memory += memory
        except (KeyError, TypeError, ValueError):
            errors.append(f"{name} requires positive memory and CPU limits")
        logging = service.get("logging", {})
        if (logging.get("driver") != "local"
                or not logging.get("options", {}).get("max-size")
                or not logging.get("options", {}).get("max-file")):
            errors.append(f"{name} requires bounded local logging")
    if available_memory is not None and total_memory > available_memory * 0.8:
        errors.append("service memory limits exceed 80% of physical host memory")
    return errors


def resolved_compose(*, env_file: Path | None, host_proxy: bool) -> dict:
    command = ["docker", "compose"]
    if env_file is not None:
        command += ["--env-file", str(env_file.resolve())]
    for path in FILES:
        command += ["-f", path]
    if host_proxy:
        command += ["-f", "docker-compose.prod.host-proxy.yml"]
    command += ["config", "--format", "json"]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                            check=False)
    if result.returncode:
        raise RuntimeError("Compose configuration failed; inspect locally without sharing resolved secrets")
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--host-proxy", action="store_true")
    args = parser.parse_args()
    if os.name == "posix":
        candidate = args.env_file or ROOT / ".env"
        try:
            if candidate.stat().st_mode & 0o077:
                print("BLOCKED: production env file must be owner-only (mode 0600)")
                return 1
        except OSError:
            print("BLOCKED: production env file is not readable")
            return 1
    try:
        errors = check_configuration(
            resolved_compose(env_file=args.env_file, host_proxy=args.host_proxy),
            available_memory=host_memory_bytes(),
        )
    except (OSError, RuntimeError, json.JSONDecodeError):
        print("BLOCKED: could not resolve production Compose configuration")
        return 1
    if errors:
        for error in errors:
            print(f"BLOCKED: {error}")
        return 1
    print("PASS: static Compose admission only; TLS, image provenance, migration, "
          "backup restore, and business acceptance still require live verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
