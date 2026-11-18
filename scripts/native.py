"""Native installation and launch entry points; use only the Python standard library."""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_search_cli.chrome_executable import chrome_candidates  # noqa: E402
from job_search_cli.runtime_paths import (  # noqa: E402
    child_environment,
    data_dir,
    database_path,
    private_directory,
    sessions_path,
    RuntimePathError,
)

ROOT = Path(__file__).resolve().parents[1]
NODE_VERSION = "v22.23.3"


class SetupError(Exception):
    """An actionable installation or startup failure."""


def run(args: list[str], *, cwd: Path = ROOT, remedy: str) -> str:
    try:
        result = subprocess.run(
            args, cwd=cwd, text=True, capture_output=True, check=False
        )
    except OSError as exc:
        raise SetupError(f"{remedy} ({exc})") from exc
    if result.returncode:
        raise SetupError(remedy)
    return result.stdout.strip()


def prerequisites() -> None:
    if sys.version_info[:2] != (3, 11):
        raise SetupError(
            "Python 3.11 is required. Set PYTHON to a Python 3.11 executable."
        )
    if sys.platform not in ("darwin", "linux"):
        raise SetupError("Use macOS or Ubuntu 24.04 desktop; see docs/native-setup.md.")
    if sys.platform == "linux":
        import platform

        release = platform.freedesktop_os_release()
        if release.get("ID") != "ubuntu" or release.get("VERSION_ID") != "24.04":
            raise SetupError("Use Ubuntu 24.04 desktop; see docs/native-setup.md.")
    node_help = (
        "Node.js 22.23.3 is required for native SQLite. Select it with your "
        "user-managed Node version manager (.nvmrc), then rerun bash scripts/bootstrap.sh."
    )
    if (
        not shutil.which("node")
        or run(["node", "--version"], remedy=node_help) != NODE_VERSION
    ):
        raise SetupError(node_help)
    if not shutil.which("npm"):
        raise SetupError(
            "npm 10 is required; use the npm bundled with Node.js 22.23.3."
        )
    npm = run(["npm", "--version"], remedy="Use npm 10 bundled with Node.js 22.23.3.")
    if npm.split(".")[0] != "10":
        raise SetupError(
            "npm 10 is required; use the npm bundled with Node.js 22.23.3."
        )


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("bootstrap", "start"):
        raise SetupError("Usage: bash scripts/bootstrap.sh | bash scripts/start.sh")
    prerequisites()
    check_chrome()
    os.umask(0o077)
    for location in (
        data_dir(),
        database_path(),
        sessions_path(),
        *(
            data_dir() / name
            for name in ("runtime", "logs", "chrome", "chrome/Default")
        ),
    ):
        if location.resolve().is_relative_to(ROOT):
            raise SetupError(
                "Runtime storage must be outside the checkout. Set JOB_SEARCH_DATA_DIR, "
                "DB_PATH, and SESSIONS_PATH to private external locations."
            )
    os.environ.update(child_environment())
    python = ROOT / ".venv" / "bin" / "python"
    if sys.argv[1] == "bootstrap":
        if not python.exists():
            run(
                [
                    os.environ.get("PYTHON", sys.executable),
                    "-m",
                    "venv",
                    str(ROOT / ".venv"),
                ],
                remedy="Could not create .venv. Use Python 3.11 with venv/ensurepip support; see docs/native-setup.md.",
            )
        check_python(python)
        run(
            [str(python), "-m", "pip", "install", "."],
            remedy="Python dependency installation failed. Check network access and Python 3.11 venv support; rerun bootstrap.",
        )
        for package in ("backend", "frontend"):
            run(
                ["npm", "ci"],
                cwd=ROOT / package,
                remedy=f"Dependency installation failed in {package}. Use Node.js 22.23.3 / npm 10 and check registry access. Run npm ci in {package} for details; native SQLite may need platform build tools. See docs/native-setup.md.",
            )
        check_backend()
        run(
            ["npm", "run", "build"],
            cwd=ROOT / "frontend",
            remedy="Frontend build failed. Run npm run build in frontend for details, then rerun bootstrap.",
        )
        for location in (data_dir(), data_dir() / "runtime", data_dir() / "logs"):
            private_directory(location)
        run(
            [str(python), "-m", "job_search_cli.cli", "linkedin", "prepare-browser"],
            remedy="Could not prepare private Chrome storage. Check JOB_SEARCH_DATA_DIR permissions.",
        )
        print("Bootstrap complete. Run bash scripts/start.sh; AI setup is optional.")
    else:
        if not python.is_file():
            raise SetupError(
                "Python environment is missing. Run bash scripts/bootstrap.sh first."
            )
        check_python(python)
        run(
            [str(python), "-m", "job_search_cli.cli", "--help"],
            remedy="Python project dependencies are missing or incompatible. Run bash scripts/bootstrap.sh.",
        )
        if not (ROOT / "frontend" / "dist" / "index.html").is_file():
            raise SetupError(
                "Frontend build is missing. Run bash scripts/bootstrap.sh."
            )
        check_backend()
        port_text = os.environ.get("PORT", "3000")
        if (
            not port_text.isascii()
            or not port_text.isdecimal()
            or not 1 <= int(port_text) <= 65535
        ):
            raise SetupError(
                "PORT must be an integer from 1 to 65535, for example PORT=3001 bash scripts/start.sh."
            )
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", int(port_text)))
        except OSError as exc:
            raise SetupError(
                f"Cannot listen on 127.0.0.1:{port_text}. Stop the process using that port or select PORT=3001 bash scripts/start.sh."
            ) from exc
        os.environ["HOST"] = "127.0.0.1"
        os.environ["PORT"] = port_text
        os.environ["FRONTEND_DIST"] = str(ROOT / "frontend" / "dist")
        os.chdir(ROOT / "backend")
        print(f"Starting portal at http://127.0.0.1:{port_text}", flush=True)
        os.execvp(
            "node",
            [
                "node",
                "--require",
                str(ROOT / "scripts" / "startup-errors.cjs"),
                "server.js",
            ],
        )


def check_python(python: Path) -> None:
    run(
        [str(python), "-c", "import sys; assert sys.version_info[:2] == (3, 11)"],
        remedy="The .venv Python version is incompatible. Move .venv aside, select Python 3.11, and rerun bash scripts/bootstrap.sh. Private user data is separate.",
    )


def check_backend() -> None:
    run(
        [
            "node",
            "-e",
            "for (const name of ['express', 'cors', 'multer', 'node-cron', 'cron-parser']) require(name); const Database = require('better-sqlite3'); const db = new Database(':memory:'); db.prepare('select 1').get(); db.close();",
        ],
        cwd=ROOT / "backend",
        remedy="Backend dependencies or native SQLite ABI are incompatible/missing. Select Node.js 22.23.3 and rerun bash scripts/bootstrap.sh (npm ci rebuilds dependencies for this runtime/architecture). See docs/native-setup.md.",
    )


def check_chrome() -> None:
    remedy = "Google Chrome is required (Stable 142 or newer). Install it from https://www.google.com/chrome/ in a standard location; see docs/native-setup.md. No browser was launched."
    executable = next((p for p in chrome_candidates() if p and Path(p).is_file()), None)
    if not executable:
        raise SetupError(remedy)
    version = run([executable, "--version"], remedy=remedy)
    match = re.search(r"Google Chrome (\d+)\.", version)
    if not match or int(match[1]) < 142:
        raise SetupError(remedy)
    print(f"Detected {version}")


if __name__ == "__main__":
    try:
        main()
    except (SetupError, OSError, RuntimePathError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
