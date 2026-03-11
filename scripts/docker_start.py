from __future__ import annotations

import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


def _project_root() -> Path:
    start = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    for candidate in [start, *start.parents]:
        if (candidate / "docker-compose.yml").exists():
            return candidate
    raise FileNotFoundError("docker-compose.yml not found")


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _ensure_file(target: Path, example: Path) -> None:
    if target.exists() or not example.exists():
        return
    target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[bootstrap] created {target.name} from {example.name}", flush=True)


def _docker_ready() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == 0


def _desktop_candidates() -> list[Path]:
    return [
        Path(r"C:\Program Files\Rancher Desktop\Rancher Desktop.exe"),
        Path(r"C:\Users\wlflq\AppData\Local\Programs\Rancher Desktop\Rancher Desktop.exe"),
        Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe"),
    ]


def _start_desktop_if_needed() -> None:
    if _docker_ready():
        return
    for candidate in _desktop_candidates():
        if not candidate.exists():
            continue
        print(f"[docker] starting desktop app: {candidate}", flush=True)
        subprocess.Popen([str(candidate)], cwd=str(candidate.parent))
        break
    else:
        raise RuntimeError("docker daemon is not ready and no desktop app was found")

    deadline = time.time() + 240
    while time.time() < deadline:
        if _docker_ready():
            print("[docker] daemon is ready", flush=True)
            return
        time.sleep(3)
    raise RuntimeError("docker daemon did not become ready in time")


def _run_compose(project_root: Path) -> None:
    print("[compose] docker compose up -d --build", flush=True)
    result = subprocess.run(
        ["docker", "compose", "up", "-d", "--build"],
        cwd=str(project_root),
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("docker compose up failed")


def _wait_for_health(url: str, timeout_seconds: int = 240) -> None:
    import urllib.request

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if int(getattr(resp, "status", 0)) == 200:
                    print(f"[health] ready: {url}", flush=True)
                    return
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError(f"health check failed: {url}")


def main() -> int:
    try:
        root = _project_root()
        env_path = root / ".env"
        env_example_path = root / ".env.example"
        _ensure_file(env_path, env_example_path)

        env_values = _read_env_file(env_path)
        runtime_name = env_values.get("RUNTIME_SETTINGS_FILE", "runtime_settings.local.json") or "runtime_settings.local.json"
        app_port = int(env_values.get("APP_PORT", "8099") or "8099")
        runtime_path = root / runtime_name
        runtime_example_path = root / "runtime_settings.example.json"
        _ensure_file(runtime_path, runtime_example_path)

        _start_desktop_if_needed()
        _run_compose(root)

        url = f"http://127.0.0.1:{app_port}/health"
        _wait_for_health(url)
        webbrowser.open(f"http://127.0.0.1:{app_port}")
        print("[done] browser opened", flush=True)
        return 0
    except Exception as exc:
        print(f"[error] {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
