"""Private runtime storage shared by the CLI and portal processes."""

from __future__ import annotations

import os
import sys
import json
from importlib.metadata import PackageNotFoundError, distribution
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import unquote, urlsplit

selected_database: ContextVar[Path | None] = ContextVar(
    "selected_database", default=None
)


class RuntimePathError(ValueError):
    """Private storage was directed into application source."""


def source_roots() -> list[Path]:
    """Resolve native source assets for editable and noneditable local installs."""
    roots = [Path(__file__).resolve().parents[1]]
    if os.environ.get("JOB_SEARCH_SOURCE_ROOT"):
        roots.insert(
            0, Path(os.environ["JOB_SEARCH_SOURCE_ROOT"]).expanduser().resolve()
        )
    try:
        origin = json.loads(
            distribution("job-search-cli").read_text("direct_url.json") or "{}"
        )
        url = urlsplit(origin.get("url", ""))
        if url.scheme == "file" and not url.netloc:
            roots.append(Path(unquote(url.path)).resolve())
    except (ValueError, TypeError, PackageNotFoundError):
        pass
    return roots


def external_path(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if any(resolved.is_relative_to(root) for root in source_roots()):
        raise RuntimePathError(
            f"Runtime storage '{resolved}' must be outside the checkout. Select a private external location."
        )
    return resolved


def data_dir() -> Path:
    """Resolve application storage without inspecting or migrating the checkout."""
    override = os.environ.get("JOB_SEARCH_DATA_DIR")
    if override:
        return external_path(override)
    if sys.platform == "darwin":
        return external_path(
            Path.home() / "Library" / "Application Support" / "job-search"
        )
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    if not base.is_absolute():
        base = Path.home() / ".local" / "share"
    return external_path(base / "job-search")


def database_path(path: str | Path | None = None) -> Path:
    return external_path(
        path
        or selected_database.get()
        or os.environ.get("DB_PATH")
        or data_dir() / "linkedin_jobs.sqlite3"
    )


def sessions_path(path: str | Path | None = None) -> Path:
    return external_path(
        path or os.environ.get("SESSIONS_PATH") or data_dir() / "sessions.json"
    )


def private_directory(path: Path) -> Path:
    """Create missing directories privately, leaving existing parents untouched."""
    if not path.exists():
        private_directory(path.parent)
        path.mkdir(mode=0o700, exist_ok=True)
    return path


def private_file(path: Path) -> Path:
    private_directory(path.parent)
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(descriptor)
    path.chmod(0o600)
    return path


def browser_profile_path() -> Path:
    """Prepare the shared app-owned profile without changing existing browser data."""
    return private_directory(data_dir() / "chrome" / "Default")


def child_environment(db_path: str | Path | None = None) -> dict[str, str]:
    """Freeze relative overrides before a child changes working directory."""
    return {
        **os.environ,
        "JOB_SEARCH_DATA_DIR": str(data_dir()),
        "DB_PATH": str(database_path(db_path)),
        "SESSIONS_PATH": str(sessions_path()),
    }
