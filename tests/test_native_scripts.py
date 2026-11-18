"""Exercise native entry points as a user would, with substitute system tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def installation(tmp_path: Path):
    source = tmp_path / "source with spaces"
    source.mkdir()
    shutil.copytree(REPO / "scripts", source / "scripts")
    shutil.copytree(
        REPO / "job_search_cli",
        source / "job_search_cli",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for package in ("backend", "frontend"):
        (source / package).mkdir()
        for name in ("package.json", "package-lock.json"):
            shutil.copyfile(REPO / package / name, source / package / name)
    (source / "backend" / "server.js").touch()
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "commands.jsonl"
    fake = tools / "fake"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "name = pathlib.Path(sys.argv[0]).name\n"
        "args = sys.argv[1:]\n"
        "if args and args[0].endswith('native.py'):\n"
        "    if os.environ.get('FAKE_PLATFORM'):\n"
        "        import platform, runpy\n"
        "        sys.platform = os.environ['FAKE_PLATFORM']\n"
        "        platform.freedesktop_os_release = lambda: {'ID': 'ubuntu', 'VERSION_ID': os.environ.get('FAKE_UBUNTU', '24.04')}\n"
        "        sys.argv = args\n"
        "        runpy.run_path(args[0], run_name='__main__')\n"
        "        sys.exit(0)\n"
        "    os.execv(sys.executable, [sys.executable, *args])\n"
        "with open(os.environ['COMMAND_LOG'], 'a') as f:\n"
        "    f.write(json.dumps([name, args, os.getcwd(), dict(os.environ)]) + '\\n')\n"
        "if name == 'node' and args == ['--version']:\n"
        "    print(os.environ.get('FAKE_NODE_VERSION', 'v22.23.3'))\n"
        "elif name == 'npm' and args == ['--version']:\n"
        "    print('10.9.9')\n"
        "elif args[:2] == ['-m', 'venv']:\n"
        "    target = pathlib.Path(args[2]) / 'bin'\n"
        "    target.mkdir(parents=True, exist_ok=True)\n"
        "    (target / 'python').symlink_to(sys.argv[0])\n"
        "elif name == 'npm' and args == ['run', 'build']:\n"
        "    pathlib.Path('dist').mkdir(exist_ok=True)\n"
        "    pathlib.Path('dist/index.html').write_text('<html>portal</html>')\n"
        "elif name in ('Google Chrome', 'google-chrome'):\n"
        "    if os.environ.get('FAKE_CHROME_MISSING'): sys.exit(1)\n"
        "    print(os.environ.get('FAKE_CHROME_VERSION', 'Google Chrome 142.0.7444.175'))\n"
        "elif args == ['--version']:\n"
        "    print('Python 3.11.6')\n"
        "elif name == 'node' and args[-1:] == ['server.js']:\n"
        "    print('SERVER_STARTED')\n"
        "if os.environ.get('FAIL_CONTAINS') and any(os.environ['FAIL_CONTAINS'] in a for a in args):\n"
        "    sys.exit(1)\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    for name in ("python3.11", "node", "npm"):
        (tools / name).symlink_to(fake)
    chrome = (
        tmp_path / "home/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    chrome.parent.mkdir(parents=True)
    chrome.symlink_to(fake)
    (tools / "google-chrome").symlink_to(fake)
    env = {
        **os.environ,
        "PATH": f"{tools}:{os.environ['PATH']}",
        "PYTHON": str(tools / "python3.11"),
        "HOME": str(tmp_path / "home"),
        "JOB_SEARCH_DATA_DIR": str(tmp_path / "private"),
        "COMMAND_LOG": str(log),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for key in ("DB_PATH", "SESSIONS_PATH", "FRONTEND_DIST", "PORT", "HOST"):
        env.pop(key, None)

    def run(command="bootstrap", **overrides):
        return subprocess.run(
            ["/bin/bash", str(source / "scripts" / f"{command}.sh")],
            cwd=tmp_path,
            env={**env, **overrides},
            capture_output=True,
            text=True,
        )

    return source, env, log, run


def test_bootstrap_rejects_incompatible_node_before_installing(installation):
    source, _, log, run = installation
    result = run(FAKE_NODE_VERSION="v18.20.8")
    assert result.returncode != 0
    assert "Node.js 22.23.3" in result.stderr
    assert not (source / ".venv").exists()
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    assert not any("ci" in entry[1] for entry in commands)


def test_bootstrap_installs_builds_and_prepares_browser_without_reset(installation):
    source, env, log, run = installation
    result = run()
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    assert sum(entry[1] == ["ci"] for entry in commands) == 2
    assert any(entry[1] == ["run", "build"] for entry in commands)
    assert any(
        entry[1] == ["-m", "job_search_cli.cli", "linkedin", "prepare-browser"]
        for entry in commands
    )
    assert any(entry[1][:3] == ["-m", "pip", "install"] for entry in commands)
    data = Path(env["JOB_SEARCH_DATA_DIR"])
    assert (data / "runtime").is_dir()
    assert (data / "logs").is_dir()
    assert (data.stat().st_mode & 0o777) == 0o700
    # Synthetic existing user state must survive the second installation.
    for relative in (
        "settings.json",
        "linkedin_jobs.sqlite3",
        "sessions.json",
        "chrome/Default/Cookies",
    ):
        target = data / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("PRESERVE-SYNTHETIC-DATA", encoding="utf-8")
    again = run()
    assert again.returncode == 0, again.stderr
    for target in data.rglob("*"):
        if target.is_file():
            assert target.read_text() == "PRESERVE-SYNTHETIC-DATA"
    assert (source / "frontend/dist/index.html").is_file()
    assert not (source / "sessions.json").exists()


def test_start_checks_readiness_and_forces_loopback(installation):
    source, env, log, run = installation
    result = run("start")
    assert result.returncode != 0
    assert "bootstrap.sh" in result.stderr
    assert run().returncode == 0
    (source / "frontend/dist/index.html").unlink()
    result = run("start")
    assert result.returncode != 0
    assert "Frontend build" in result.stderr
    (source / "frontend/dist/index.html").write_text("portal")
    result = run("start", HOST="0.0.0.0", PORT="34567")
    assert result.returncode == 0, result.stderr
    assert "SERVER_STARTED" in result.stdout
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    server = next(entry for entry in commands if entry[1][-1:] == ["server.js"])
    assert server[3]["HOST"] == "127.0.0.1"
    assert server[3]["PORT"] == "34567"
    assert server[3]["JOB_SEARCH_DATA_DIR"] == env["JOB_SEARCH_DATA_DIR"]
    assert server[2] == str(source / "backend")


@pytest.mark.parametrize("port", ["0", "65536", "abc", "3000oops", "-1"])
def test_start_rejects_invalid_port(installation, port):
    _, _, _, run = installation
    assert run().returncode == 0
    result = run("start", PORT=port)
    assert result.returncode != 0
    assert "PORT must be an integer" in result.stderr


def test_start_explains_occupied_port_and_native_abi(installation):
    _, _, _, run = installation
    assert run().returncode == 0
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        result = run("start", PORT=str(port))
        assert result.returncode != 0
        assert "Stop the process using that port" in result.stderr
    result = run("start", FAIL_CONTAINS="better-sqlite3")
    assert result.returncode != 0
    assert "native SQLite ABI" in result.stderr
    assert "bootstrap.sh" in result.stderr


def test_missing_chrome_is_actionable_without_launching_browser(installation):
    _, _, log, run = installation
    result = run(FAKE_CHROME_MISSING="1")
    assert result.returncode != 0
    assert "Google Chrome is required" in result.stderr
    assert "google.com/chrome" in result.stderr
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    assert not any(entry[1] == ["ci"] for entry in commands)


@pytest.mark.parametrize(
    "variable", ["JOB_SEARCH_DATA_DIR", "DB_PATH", "SESSIONS_PATH"]
)
def test_native_entry_points_reject_runtime_storage_in_checkout(installation, variable):
    source, _, _, run = installation
    result = run(**{variable: str(source / "private")})
    assert result.returncode != 0
    assert "outside the checkout" in result.stderr
    assert not (source / "private").exists()


def test_ubuntu_target_runs_same_workflow_and_rejects_other_releases(installation):
    _, _, _, run = installation
    result = run(FAKE_PLATFORM="linux", FAKE_UBUNTU="24.04")
    assert result.returncode == 0, result.stderr
    result = run(FAKE_PLATFORM="linux", FAKE_UBUNTU="22.04")
    assert result.returncode != 0
    assert "Ubuntu 24.04 desktop" in result.stderr


def test_missing_python_and_old_chrome_explain_remediation(installation):
    source, _, _, run = installation
    result = run(PYTHON="/missing/python3.11")
    assert result.returncode != 0
    assert "python.org" in result.stderr
    result = run(FAKE_CHROME_VERSION="Google Chrome 120.0.0.0")
    assert result.returncode != 0
    assert "Stable 142 or newer" in result.stderr
    assert not (source / ".venv").exists()


@pytest.mark.parametrize(
    "failure, expected",
    [
        ("ci", "Dependency installation failed"),
        ("build", "Frontend build failed"),
        ("pip", "Python dependency installation failed"),
    ],
)
def test_bootstrap_explains_dependency_and_build_failures(
    installation, failure, expected
):
    _, _, _, run = installation
    result = run(FAIL_CONTAINS=failure)
    assert result.returncode != 0
    assert expected in result.stderr
