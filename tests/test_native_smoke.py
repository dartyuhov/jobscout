"""Opt-in clean-source installation smoke; Chrome detection alone is substituted."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen

import pytest

REPO = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_NATIVE_SMOKE") != "1",
    reason="Set RUN_NATIVE_SMOKE=1 to install real dependencies in isolated temporary source.",
)


def test_clean_source_bootstrap_start_and_repeat(tmp_path):
    source = tmp_path / "clean source"
    subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts/export_public_source.py"),
            "--output",
            str(source),
        ],
        check=True,
    )
    home = tmp_path / "home"
    tools = tmp_path / "tools"
    tools.mkdir()
    chrome = home / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    chrome.parent.mkdir(parents=True)
    chrome.write_text(
        "#!/bin/sh\n[ \"$1\" = --version ] || exit 99\nprintf 'Google Chrome 142.0.7444.175\\n'\n"
    )
    chrome.chmod(0o755)
    (tools / "google-chrome").symlink_to(chrome)
    data = tmp_path / "private"
    calls = []

    class UnexpectedAi(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(500)
            self.end_headers()

        do_POST = do_GET

    trap = ThreadingHTTPServer(("127.0.0.1", 0), UnexpectedAi)
    thread = threading.Thread(target=trap.serve_forever, daemon=True)
    thread.start()
    # Resolve before replacing HOME: user-managed node shims may inspect HOME.
    node = subprocess.check_output(
        ["node", "-p", "process.execPath"], text=True
    ).strip()
    env = {
        **os.environ,
        "HOME": str(home),
        "JOB_SEARCH_DATA_DIR": str(data),
        "PATH": f"{tools}:{Path(node).parent}:{os.environ['PATH']}",
        "PYTHON": sys.executable,
        "PYTHONDONTWRITEBYTECODE": "1",
        "OPENCODE_SERVER_URL": f"http://127.0.0.1:{trap.server_port}",
    }
    for key in ("DB_PATH", "SESSIONS_PATH", "PYTHONPATH", "FRONTEND_DIST"):
        env.pop(key, None)

    def bootstrap():
        result = subprocess.run(
            ["bash", str(source / "scripts/bootstrap.sh")],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def snapshot():
        ignored = {".venv", "node_modules", "dist", "build", "__pycache__"}
        return {
            str(p.relative_to(source)): p.read_bytes()
            for p in source.rglob("*")
            if p.is_file()
            and not any(
                part in ignored or part.endswith(".egg-info")
                for part in p.relative_to(source).parts
            )
        }

    try:
        bootstrap()
        rejected = subprocess.run(
            [
                str(source / ".venv/bin/job-search"),
                "--db-path",
                str(source / "private.sqlite3"),
                "profiles",
                "list",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert rejected.returncode == 1
        assert "outside the checkout" in rejected.stderr
        assert not (source / "private.sqlite3").exists()
        before = snapshot()
        for iteration in range(2):
            with socket.socket() as reserve:
                reserve.bind(("127.0.0.1", 0))
                port = reserve.getsockname()[1]
            process = subprocess.Popen(
                (
                    [
                        str(source / ".venv/bin/job-search"),
                        "portal",
                        "serve",
                        "--no-browser",
                        "--port",
                        str(port),
                    ]
                    if iteration == 1
                    else ["bash", str(source / "scripts/start.sh")]
                ),
                cwd=tmp_path,
                env={**env, "PORT": str(port), "HOST": "0.0.0.0"},
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            base = f"http://127.0.0.1:{port}"

            def request(route, body=None, base=base):
                payload = json.dumps(body).encode() if body is not None else None
                req = Request(
                    base + route,
                    data=payload,
                    method="PUT" if body else "GET",
                    headers={"Content-Type": "application/json"},
                )
                with urlopen(req, timeout=2) as response:
                    return json.load(response)

            try:
                for _ in range(200):
                    if process.poll() is not None:
                        pytest.fail(process.communicate()[0])
                    try:
                        health = request("/api/health")
                        break
                    except OSError:
                        time.sleep(0.1)
                else:
                    pytest.fail("Portal did not become ready")
                assert health["ai"]["ready"] is False
                assert health["ai"]["auto_analyze"] is False
                assert health["ai"]["auto_summarize"] is False
                assert request("/api/profiles") == {"profiles": []}
                assert request("/api/jobs")["jobs"] == []
                assert request("/api/settings/cv") == {"cv_text": None}
                with urlopen(base, timeout=2) as response:
                    html = response.read().decode()
                assert '<div id="root">' in html
                assert "/assets/" in html
                if iteration == 0:
                    request("/api/settings/theme", {"theme_preference": "dark"})
                else:
                    assert request("/api/settings/theme") == {
                        "theme_preference": "dark"
                    }
            finally:
                os.killpg(process.pid, signal.SIGINT)
                output, _ = process.communicate(timeout=15)
            assert f"http://127.0.0.1:{port}" in output
            cookies = data / "chrome/Default/Cookies"
            if iteration == 0:
                cookies.write_text("SYNTHETIC-LOGIN", encoding="utf-8")
                bootstrap()
            assert cookies.read_text() == "SYNTHETIC-LOGIN"
        assert snapshot() == before
        assert calls == []
        assert (data / "linkedin_jobs.sqlite3").stat().st_mode & 0o777 == 0o600
        assert (data / "chrome/Default").stat().st_mode & 0o777 == 0o700
        assert not (source / "sessions.json").exists()
    finally:
        trap.shutdown()
        trap.server_close()
        thread.join()
