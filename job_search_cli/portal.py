"""Helpers for serving the local jobs portal."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from urllib import error, request
import webbrowser

from job_search_cli.runtime_paths import (
    child_environment,
    database_path,
    source_roots,
    RuntimePathError,
)

DEFAULT_PORTAL_HOST = "127.0.0.1"
DEFAULT_PORTAL_PORT = 3000
PORTAL_STARTUP_TIMEOUT_SECONDS = 20


class PortalServeError(ValueError):
    """Raised when the local portal cannot be started."""


def serve_portal(
    *,
    host: str,
    port: int,
    db_path: Path | None,
    open_browser: bool,
) -> None:
    """Start the Node backend that serves the jobs portal."""
    repo_root = _resolve_repo_root()
    backend_dir = repo_root / "backend"
    server_script = backend_dir / "server.js"
    frontend_dist = repo_root / "frontend" / "dist"

    if not server_script.exists():
        raise PortalServeError(
            f"Portal backend script was not found at '{server_script}'."
        )

    node_executable = shutil.which("node")
    if node_executable is None:
        raise PortalServeError(
            "Node.js was not found. Install Node.js to use 'job-search portal serve'."
        )

    try:
        env = child_environment(db_path)
    except RuntimePathError as exc:
        raise PortalServeError(str(exc)) from exc
    env["HOST"] = host
    env["PORT"] = str(port)
    env["FRONTEND_DIST"] = str(frontend_dist.resolve())

    try:
        process = subprocess.Popen(
            [node_executable, str(server_script)],
            cwd=backend_dir,
            env=env,
        )
    except OSError as exc:
        raise PortalServeError(f"Portal backend could not be started ({exc}).") from exc

    portal_url = f"http://{host}:{port}"
    try:
        _wait_for_healthcheck(
            process,
            url=f"{portal_url}/api/health",
            timeout_seconds=PORTAL_STARTUP_TIMEOUT_SECONDS,
        )
        if open_browser:
            webbrowser.open(portal_url)

        print(f"Portal backend is running at {portal_url}")
        if not frontend_dist.exists():
            print(
                "Frontend build not found. Build it with: "
                "'cd frontend && npm install && npm run build'"
            )
        print("Press Ctrl+C to stop.")

        return_code = process.wait()
        if return_code != 0:
            raise PortalServeError(
                f"Portal backend exited with status code {return_code}."
            )
    except KeyboardInterrupt:
        _stop_process(process)
    except PortalServeError:
        _stop_process(process)
        raise


def default_portal_db_path() -> Path:
    """Return the default SQLite path used by CLI commands."""
    return database_path()


def _resolve_repo_root() -> Path:
    """Find native assets for editable and ordinary source-native installs."""
    for candidate in source_roots():
        if (candidate / "backend/server.js").is_file() and (
            candidate / "frontend"
        ).is_dir():
            return candidate.resolve()
    raise PortalServeError(
        "Native portal assets were not found. Run bootstrap from the source checkout or set JOB_SEARCH_SOURCE_ROOT to that checkout."
    )


def _wait_for_healthcheck(
    process: subprocess.Popen[bytes],
    *,
    url: str,
    timeout_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise PortalServeError(
                f"Portal backend exited early with status code {return_code}."
            )

        try:
            with request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (error.HTTPError, error.URLError, TimeoutError, OSError):
            pass

        time.sleep(0.2)

    raise PortalServeError(
        "Portal backend did not become healthy in time. "
        "Check backend logs and ensure npm dependencies are installed."
    )


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
