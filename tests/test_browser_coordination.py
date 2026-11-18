"""Browser lifecycle behavior at the CLI and spawned workflow boundaries."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from job_search_cli.cli import main
from job_search_cli.chrome import ChromeLaunchResult
from job_search_cli.job_store import upsert_profile
from job_search_cli.sessions import load_session_records


@pytest.mark.parametrize("phase", ["create", "open", "flock"])
def test_lock_permission_failure_is_actionable_cli_error(monkeypatch, phase):
    def denied(*args, **kwargs):
        raise PermissionError("synthetic access denied")

    target = {"create": "private_file", "open": "os.open", "flock": "fcntl.flock"}[
        phase
    ]
    monkeypatch.setattr(f"job_search_cli.browser_coordination.{target}", denied)
    result = CliRunner().invoke(main, ["linkedin", "login"])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "browser.lock" in result.output
    assert "permission" in result.output.lower()


def test_prepare_browser_is_private_repeatable_and_preserves_login(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["linkedin", "prepare-browser"])
    assert result.exit_code == 0, result.output
    profile = Path(json.loads(result.output)["chromeProfilePath"])
    assert profile == tmp_path / "user-data/chrome/Default"
    assert profile.is_dir()
    assert profile.stat().st_mode & 0o777 == 0o700
    login = profile / "synthetic-login"
    login.write_text("persisted", encoding="utf-8")
    assert runner.invoke(main, ["linkedin", "prepare-browser"]).exit_code == 0
    assert login.read_text(encoding="utf-8") == "persisted"


def test_searches_share_healthy_app_browser_and_close_only_their_own_session(
    tmp_path, monkeypatch
):
    profile_path = tmp_path / "user-data/chrome/Default"
    launches = []
    closed = []
    monkeypatch.setattr(
        "job_search_cli.cli.launch_google_chrome",
        lambda profile: (
            launches.append(profile),
            ChromeLaunchResult(999999, "http://localhost:9333", "ws://test", ()),
        )[1],
    )
    monkeypatch.setattr(
        "job_search_cli.cli.cleanup_chrome_processes_for_profile", lambda *a: None
    )
    monkeypatch.setattr(
        "job_search_cli.cli.get_session_ws_endpoint", lambda *a: "ws://test"
    )
    monkeypatch.setattr(
        "job_search_cli.cli.open_url_in_chrome_session", lambda *a: None
    )
    monkeypatch.setattr(
        "job_search_cli.cli.close_tracked_chrome_session",
        lambda record: closed.append(record.sessionName),
    )
    runner = CliRunner()
    for name in ("search-one", "search-two"):
        profile_id = upsert_profile(
            {
                "sessionName": name,
                "chromeProfilePath": str(tmp_path / name),
                "headless": True,
                "locationGeoId": "123",
                "f_TPR": "r86400",
            }
        )
        result = runner.invoke(
            main, ["linkedin", "launch", "--profile-id", str(profile_id)]
        )
        assert result.exit_code == 0, result.output
    assert len(launches) == 1
    assert launches[0]["chromeProfilePath"] == str(profile_path)
    assert launches[0]["headless"] is False
    result = runner.invoke(main, ["linkedin", "close", "--session-name", "search-one"])
    assert result.exit_code == 0, result.output
    assert closed == []
    assert [r.sessionName for r in load_session_records()] == ["search-two"]
    result = runner.invoke(main, ["linkedin", "close", "--session-name", "search-two"])
    assert result.exit_code == 0, result.output
    assert closed == ["search-two"]


def _wait_for(path, processes):
    deadline = time.monotonic() + 10
    while not path.exists():
        assert time.monotonic() < deadline, f"Timed out waiting for {path}"
        for process in processes:
            if process.poll() is not None:
                pytest.fail(f"Substitute process exited early: {process.communicate()}")
        time.sleep(0.02)


def test_visible_manual_login_persists_storage_and_cleans_up_on_prompt_failure(
    tmp_path, monkeypatch
):
    launched = []
    closed = []
    monkeypatch.setattr(
        "job_search_cli.cli.launch_google_chrome",
        lambda profile: (
            launched.append(profile),
            ChromeLaunchResult(999999, "http://localhost:9333", "ws://test", ()),
        )[1],
    )
    monkeypatch.setattr(
        "job_search_cli.cli.cleanup_chrome_processes_for_profile", lambda *a: None
    )
    monkeypatch.setattr(
        "job_search_cli.cli.open_url_in_chrome_session", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "job_search_cli.cli.close_tracked_chrome_session",
        lambda r: closed.append(r.pid),
    )
    profile = tmp_path / "user-data/chrome/Default"
    runner = CliRunner()
    for input_text in ("\n", ""):
        result = runner.invoke(main, ["linkedin", "login"], input=input_text)
        assert result.exit_code == (0 if input_text == "\n" else 1), result.output
        assert "Sign in to LinkedIn" in result.output
        assert profile.is_dir()
    assert all(p["headless"] is False for p in launched)
    assert closed == [999999, 999999]
    assert load_session_records() == []


def test_failed_initial_navigation_closes_only_new_browser(tmp_path, monkeypatch):
    from job_search_cli.chrome import ChromeRuntimeError

    closed = []
    profile_id = upsert_profile(
        {
            "sessionName": "broken-navigation",
            "chromeProfilePath": str(tmp_path / "user-data/chrome/Default"),
            "headless": False,
            "locationGeoId": "123",
            "f_TPR": "r86400",
        }
    )
    monkeypatch.setattr(
        "job_search_cli.cli.cleanup_chrome_processes_for_profile", lambda *a: None
    )
    monkeypatch.setattr(
        "job_search_cli.cli.launch_google_chrome",
        lambda p: ChromeLaunchResult(999999, "http://localhost:9333", "ws://test", ()),
    )

    def failed(*args):
        raise ChromeRuntimeError("synthetic navigation failure")

    monkeypatch.setattr("job_search_cli.cli.open_url_in_chrome_session", failed)
    monkeypatch.setattr(
        "job_search_cli.cli.close_tracked_chrome_session",
        lambda r: closed.append(r.pid),
    )
    result = CliRunner().invoke(
        main, ["linkedin", "launch", "--profile-id", str(profile_id)]
    )
    assert result.exit_code == 1
    assert "synthetic navigation failure" in result.output
    assert closed == [999999]
    assert load_session_records() == []


def test_headed_launch_can_replace_app_headless_browser_without_profile_conflict(
    tmp_path, monkeypatch
):
    from job_search_cli.sessions import SessionRecord, write_session_records

    profile_path = str(tmp_path / "user-data/chrome/Default")
    profile_id = upsert_profile(
        {
            "sessionName": "visible-search",
            "chromeProfilePath": profile_path,
            "headless": False,
            "locationGeoId": "123",
            "f_TPR": "r86400",
        }
    )
    write_session_records(
        [
            SessionRecord(
                "previous-search",
                "ws://test",
                "http://localhost:9333",
                999998,
                profile_path,
                True,
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            )
        ]
    )
    closed = []
    monkeypatch.setattr(
        "job_search_cli.cli.get_session_ws_endpoint", lambda *a: "ws://test"
    )
    monkeypatch.setattr(
        "job_search_cli.cli.close_tracked_chrome_session",
        lambda r: closed.append(r.pid),
    )
    monkeypatch.setattr(
        "job_search_cli.cli.cleanup_chrome_processes_for_profile", lambda *a: None
    )
    monkeypatch.setattr(
        "job_search_cli.cli.launch_google_chrome",
        lambda p: ChromeLaunchResult(999999, "http://localhost:9334", "ws://new", ()),
    )
    monkeypatch.setattr(
        "job_search_cli.cli.open_url_in_chrome_session", lambda *a: None
    )
    result = CliRunner().invoke(
        main, ["linkedin", "launch", "--profile-id", str(profile_id)]
    )
    assert result.exit_code == 0, result.output
    assert closed == [999998]
    assert [(r.sessionName, r.pid, r.headless) for r in load_session_records()] == [
        ("previous-search", 999999, False),
        ("visible-search", 999999, False),
    ]


@pytest.mark.parametrize("interrupt", [False, True])
@pytest.mark.parametrize("entrypoint", ["workflow", "scheduler"])
@pytest.mark.parametrize("phase", ["collect", "parse"])
def test_workflow_and_interactive_cli_serialize_and_recover_with_real_children(
    tmp_path, interrupt, entrypoint, phase
):
    """The scheduler's workflow entrypoint competes with an independent CLI process."""
    repo = Path(__file__).resolve().parents[1]
    substitute = tmp_path / "sitecustomize.py"
    substitute.write_text(
        """
import os, time
from pathlib import Path
from job_search_cli import chrome
root = Path(os.environ["TEST_BROWSER_ROOT"])
def launch(profile):
    return chrome.ChromeLaunchResult(999999, "http://localhost:9333", "ws://test", ())
def collect(*a, **k):
    if os.environ["TEST_BROWSER_PHASE"] == "parse":
        return ["12345"]
    return blocked()
def blocked(*a, **k):
    (root / "collecting").touch()
    while not (root / "release").exists():
        time.sleep(0.02)
    raise chrome.ChromeRuntimeError("synthetic collection failure")
def opened(*a, **k):
    with (root / "opened").open("a") as f:
        f.write("opened\\n")
chrome.launch_google_chrome = launch
chrome.cleanup_chrome_processes_for_profile = lambda *a: None
chrome.get_session_ws_endpoint = lambda *a: "ws://test"
chrome.close_tracked_chrome_session = lambda r: (root / ("closed-" + r.sessionName)).touch()
chrome.open_url_in_chrome_session = opened
chrome.get_job_ids_from_chrome_session = collect
chrome.parse_job_details_from_chrome_session = blocked
""",
        encoding="utf-8",
    )
    profile_id = upsert_profile(
        {
            "sessionName": "scheduled",
            "chromeProfilePath": str(tmp_path / "user-data/chrome/Default"),
            "headless": False,
            "locationGeoId": "123",
            "f_TPR": "r86400",
        }
    )
    env = {
        **os.environ,
        "TEST_BROWSER_ROOT": str(tmp_path),
        "TEST_BROWSER_PHASE": phase,
        "PYTHONPATH": os.pathsep.join((str(tmp_path), str(repo))),
    }
    command = [
        sys.executable,
        str(repo / "scripts/linkedin_full_flow.py"),
        "--profile-id",
        str(profile_id),
        "--repo",
        str(repo),
    ]
    if entrypoint == "scheduler":
        if not (repo / "backend/node_modules/better-sqlite3").exists() and not env.get(
            "NODE_PATH"
        ):
            pytest.skip(
                "Scheduler checks require backend Node dependencies (or NODE_PATH)"
            )
        env["NODE_PATH"] = env.get("NODE_PATH", str(repo / "backend/node_modules"))
        env["TEST_WORKFLOW_COMMAND"] = json.dumps(command)
        command = [
            "node",
            "-e",
            """
const Database = require("better-sqlite3");
const { spawn } = require("node:child_process");
const scheduler = require(process.argv[1]);
const db = new Database(process.env.DB_PATH);
db.exec(`ALTER TABLE profile ADD COLUMN cron TEXT;
 ALTER TABLE profile ADD COLUMN archived_at TEXT;
 CREATE TABLE workflow_runs (
 id INTEGER PRIMARY KEY, profile_id INTEGER, batch_id TEXT, status TEXT,
 trigger_source TEXT, created_at TEXT, updated_at TEXT, started_at TEXT,
 finished_at TEXT, error_message TEXT);
 CREATE TABLE scheduled_runs (id INTEGER PRIMARY KEY, profile_id INTEGER,
 scheduled_for TEXT, trigger_source TEXT, status TEXT, created_at TEXT,
 triggered_at TEXT, run_id INTEGER, skip_reason TEXT);`);
scheduler.initScheduler(db, (id, profile) => {
 db.prepare("UPDATE workflow_runs SET status = 'running' WHERE id = ?").run(id);
 const [python, ...args] = JSON.parse(process.env.TEST_WORKFLOW_COMMAND);
 const child = spawn(python, args, {env: process.env, stdio: "inherit"});
 child.on("exit", code => { scheduler.stopAll(); db.close(); process.exit(code); });
}, () => new Date().toISOString());
db.prepare("UPDATE profile SET cron = '* * * * *'").run();
scheduler.__testOnly.triggerScheduledRun(1);
""",
            str(repo / "backend/scheduler.js"),
        ]
    workflow = subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    interactive = None
    try:
        _wait_for(tmp_path / "collecting", [workflow])
        interactive = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "job_search_cli.cli",
                "linkedin",
                "launch",
                "--profile-id",
                str(profile_id),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.3)
        assert interactive.poll() is None, interactive.communicate()
        assert (tmp_path / "opened").read_text().splitlines() == ["opened"]
        if interrupt:
            workflow.kill()
            workflow.wait(timeout=5)
            time.sleep(0.2)
            assert interactive.poll() is None
        (tmp_path / "release").touch()
        stdout, stderr = interactive.communicate(timeout=10)
        assert interactive.returncode == 0, (stdout, stderr)
        if not interrupt:
            stdout, stderr = workflow.communicate(timeout=10)
            assert workflow.returncode == (1 if phase == "collect" else 2), (
                stdout,
                stderr,
            )
            assert (tmp_path / "closed-scheduled").exists()
        assert (tmp_path / "opened").read_text().splitlines() == ["opened", "opened"]
    finally:
        (tmp_path / "release").touch()
        for process in (workflow, interactive):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
