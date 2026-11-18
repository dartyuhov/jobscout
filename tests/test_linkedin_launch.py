from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from job_search_cli.chrome import ChromeLaunchResult, ChromeRuntimeError
from job_search_cli.cli import main
from job_search_cli.job_store import PROFILE_TABLE, upsert_profile
from job_search_cli.sessions import (
    SessionRecord,
    load_session_records,
    write_session_records,
)


def _write_profile(
    tmp_path: Path,
    *,
    chrome_profile_path: str,
    headless: bool = False,
    job_title: str | None = None,
    db_path: Path | None = None,
) -> Path:
    payload: dict[str, object] = {
        "chromeProfilePath": chrome_profile_path,
        "sessionName": "linkedin-qa-bcn",
        "headless": headless,
        "locationGeoId": "105088894",
        "f_TPR": "r604800",
    }
    if job_title is not None:
        payload["jobTitle"] = job_title
    payload["id"] = upsert_profile(payload, db_path=db_path)

    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    return profile_path


def _make_record(**overrides: object) -> SessionRecord:
    values = {
        "sessionName": "linkedin-qa-bcn",
        "wsEndpoint": "ws://127.0.0.1:9222/devtools/browser/original",
        "cdpHttpEndpoint": "http://127.0.0.1:9222",
        "pid": 12345,
        "chromeProfilePath": "/tmp/chrome/Profile 10",
        "headless": False,
        "createdAt": "2026-03-07T10:20:30Z",
        "lastUsedAt": "2026-03-07T10:20:30Z",
    }
    values.update(overrides)
    return SessionRecord(**values)


def test_launch_command_creates_session_record_and_reports_endpoints(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    chrome_profile_dir = tmp_path / "Google" / "Chrome" / "Profile 10"
    chrome_profile_dir.mkdir(parents=True)
    profile_path = _write_profile(
        tmp_path,
        chrome_profile_path=str(chrome_profile_dir),
        headless=True,
    )
    events: list[str] = []

    monkeypatch.setattr(
        "job_search_cli.cli.cleanup_chrome_processes_for_profile",
        lambda chrome_profile_path: events.append(f"cleanup:{chrome_profile_path}"),
    )
    monkeypatch.setattr(
        "job_search_cli.cli.launch_google_chrome",
        lambda profile: (
            events.append("launch"),
            ChromeLaunchResult(
                pid=54321,
                cdp_http_endpoint="http://127.0.0.1:9333",
                ws_endpoint="ws://127.0.0.1:9333/devtools/browser/new",
                launch_args=(
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "--remote-debugging-port=9333",
                    "--remote-debugging-address=127.0.0.1",
                    f"--user-data-dir={chrome_profile_dir.parent}",
                    "--profile-directory=Profile 10",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--headless=new",
                ),
            ),
        )[1],
    )
    opened: dict[str, str] = {}
    monkeypatch.setattr(
        "job_search_cli.cli.open_url_in_chrome_session",
        lambda cdp_http_endpoint, url: opened.update(
            {
                "cdp_http_endpoint": cdp_http_endpoint,
                "url": url,
            }
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "launch", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "Created Chrome session 'linkedin-qa-bcn'." in result.output
    assert "PID: 54321" in result.output
    assert "CDP HTTP endpoint: http://127.0.0.1:9333" in result.output
    assert "CDP WebSocket endpoint: ws://127.0.0.1:9333/devtools/browser/new" in (
        result.output
    )
    assert (
        "Opened URL: https://www.linkedin.com/jobs/search/"
        "?currentJobId=1&f_TPR=r604800&geoId=105088894"
    ) in result.output
    assert opened == {
        "cdp_http_endpoint": "http://127.0.0.1:9333",
        "url": (
            "https://www.linkedin.com/jobs/search/"
            "?currentJobId=1&f_TPR=r604800&geoId=105088894"
        ),
    }

    records = load_session_records(tmp_path / "sessions.json")
    assert len(records) == 1
    assert records[0].pid == 54321
    app_profile = tmp_path / "user-data/chrome/Default"
    assert records[0].chromeProfilePath == str(app_profile)
    assert records[0].headless is False
    assert events == [f"cleanup:{app_profile}", "launch"]

    profile_payload = json.loads(profile_path.read_text(encoding="utf-8"))
    assert isinstance(profile_payload.get("id"), int)

    with sqlite3.connect(tmp_path / "linkedin_jobs.sqlite3") as connection:
        persisted_profile = connection.execute(
            f"""
            SELECT id, "sessionName", "chromeProfilePath"
            FROM {PROFILE_TABLE}
            """
        ).fetchone()

    assert persisted_profile == (
        profile_payload["id"],
        "linkedin-qa-bcn",
        str(chrome_profile_dir),
    )


def test_launch_command_selects_profile_from_custom_db_path(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    chrome_profile_dir = tmp_path / "Google" / "Chrome" / "Profile 10"
    chrome_profile_dir.mkdir(parents=True)
    custom_db_path = tmp_path / "data" / "jobs.sqlite3"
    profile_path = _write_profile(
        tmp_path,
        chrome_profile_path=str(chrome_profile_dir),
        db_path=custom_db_path,
    )

    monkeypatch.setattr(
        "job_search_cli.cli.cleanup_chrome_processes_for_profile",
        lambda chrome_profile_path: None,
    )
    monkeypatch.setattr(
        "job_search_cli.cli.launch_google_chrome",
        lambda profile: ChromeLaunchResult(
            pid=54321,
            cdp_http_endpoint="http://127.0.0.1:9333",
            ws_endpoint="ws://127.0.0.1:9333/devtools/browser/new",
            launch_args=(
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "--remote-debugging-port=9333",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={chrome_profile_dir.parent}",
                "--profile-directory=Profile 10",
                "--no-first-run",
                "--no-default-browser-check",
            ),
        ),
    )
    monkeypatch.setattr(
        "job_search_cli.cli.open_url_in_chrome_session",
        lambda cdp_http_endpoint, url: None,
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        [
            "linkedin",
            "launch",
            "--profile",
            str(profile_path),
            "--db-path",
            str(custom_db_path),
        ],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert custom_db_path.exists()
    assert not (tmp_path / "linkedin_jobs.sqlite3").exists()

    with sqlite3.connect(custom_db_path) as connection:
        persisted_profile = connection.execute(
            f"""
            SELECT id, "sessionName", "chromeProfilePath"
            FROM {PROFILE_TABLE}
            """
        ).fetchone()

    assert persisted_profile == (
        json.loads(profile_path.read_text(encoding="utf-8"))["id"],
        "linkedin-qa-bcn",
        str(chrome_profile_dir),
    )


def test_launch_command_includes_url_encoded_keywords_from_job_title(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    chrome_profile_dir = tmp_path / "Google" / "Chrome" / "Profile 10"
    chrome_profile_dir.mkdir(parents=True)
    profile_path = _write_profile(
        tmp_path,
        chrome_profile_path=str(chrome_profile_dir),
        job_title="QA engineer / tester",
    )

    monkeypatch.setattr(
        "job_search_cli.cli.launch_google_chrome",
        lambda profile: ChromeLaunchResult(
            pid=54321,
            cdp_http_endpoint="http://127.0.0.1:9333",
            ws_endpoint="ws://127.0.0.1:9333/devtools/browser/new",
            launch_args=(
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "--remote-debugging-port=9333",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={chrome_profile_dir.parent}",
                "--profile-directory=Profile 10",
                "--no-first-run",
                "--no-default-browser-check",
            ),
        ),
    )
    opened: dict[str, str] = {}
    monkeypatch.setattr(
        "job_search_cli.cli.open_url_in_chrome_session",
        lambda cdp_http_endpoint, url: opened.update(
            {
                "cdp_http_endpoint": cdp_http_endpoint,
                "url": url,
            }
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "launch", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    expected_url = (
        "https://www.linkedin.com/jobs/search/"
        "?currentJobId=1&f_TPR=r604800&geoId=105088894"
        "&keywords=QA+engineer+%2F+tester"
    )

    assert result.exit_code == 0
    assert f"Opened URL: {expected_url}" in result.output
    assert opened == {
        "cdp_http_endpoint": "http://127.0.0.1:9333",
        "url": expected_url,
    }


def test_launch_command_reuses_matching_healthy_session_and_prunes_stale_records(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    chrome_profile_dir = tmp_path / "Google" / "Chrome" / "Profile 10"
    chrome_profile_dir.mkdir(parents=True)
    profile_path = _write_profile(
        tmp_path,
        chrome_profile_path=str(chrome_profile_dir),
        headless=False,
    )
    healthy_record = _make_record(
        chromeProfilePath=str(tmp_path / "user-data/chrome/Default"), headless=False
    )
    stale_record = _make_record(
        sessionName="stale-session",
        cdpHttpEndpoint="http://127.0.0.1:9555",
        wsEndpoint="ws://127.0.0.1:9555/devtools/browser/stale",
        pid=99999,
        chromeProfilePath=str(tmp_path / "Google" / "Chrome" / "Profile 99"),
    )
    write_session_records(
        [healthy_record, stale_record],
        tmp_path / "sessions.json",
    )

    def fake_get_session_ws_endpoint(record: SessionRecord) -> str | None:
        if record.sessionName == healthy_record.sessionName:
            return "ws://127.0.0.1:9222/devtools/browser/refreshed"
        return None

    def fail_launch(profile: dict[str, object]) -> ChromeLaunchResult:
        raise AssertionError("launch_google_chrome should not be called for reuse")

    monkeypatch.setattr(
        "job_search_cli.cli.get_session_ws_endpoint",
        fake_get_session_ws_endpoint,
    )
    monkeypatch.setattr("job_search_cli.cli.launch_google_chrome", fail_launch)
    opened: dict[str, str] = {}
    monkeypatch.setattr(
        "job_search_cli.cli.open_url_in_chrome_session",
        lambda cdp_http_endpoint, url: opened.update(
            {
                "cdp_http_endpoint": cdp_http_endpoint,
                "url": url,
            }
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "launch", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "Reused Chrome session 'linkedin-qa-bcn'." in result.output
    assert "PID: 12345" in result.output
    assert "stale-session" not in result.output

    records = load_session_records(tmp_path / "sessions.json")
    assert len(records) == 1
    assert records[0].sessionName == "linkedin-qa-bcn"
    assert records[0].wsEndpoint == "ws://127.0.0.1:9222/devtools/browser/refreshed"
    assert records[0].createdAt == healthy_record.createdAt
    assert records[0].lastUsedAt != healthy_record.lastUsedAt
    assert opened == {
        "cdp_http_endpoint": "http://127.0.0.1:9222",
        "url": (
            "https://www.linkedin.com/jobs/search/"
            "?currentJobId=1&f_TPR=r604800&geoId=105088894"
        ),
    }


def test_launch_command_replaces_stale_matching_session_with_new_browser(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    chrome_profile_dir = tmp_path / "Google" / "Chrome" / "Profile 10"
    chrome_profile_dir.mkdir(parents=True)
    profile_path = _write_profile(
        tmp_path,
        chrome_profile_path=str(chrome_profile_dir),
        headless=False,
    )
    stale_record = _make_record(
        pid=11111,
        cdpHttpEndpoint="http://127.0.0.1:9444",
        wsEndpoint="ws://127.0.0.1:9444/devtools/browser/stale",
        chromeProfilePath=str(chrome_profile_dir),
    )
    write_session_records([stale_record], tmp_path / "sessions.json")

    monkeypatch.setattr(
        "job_search_cli.cli.get_session_ws_endpoint",
        lambda record: None,
    )
    monkeypatch.setattr(
        "job_search_cli.cli.launch_google_chrome",
        lambda profile: ChromeLaunchResult(
            pid=54321,
            cdp_http_endpoint="http://127.0.0.1:9333",
            ws_endpoint="ws://127.0.0.1:9333/devtools/browser/new",
            launch_args=(
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "--remote-debugging-port=9333",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={chrome_profile_dir.parent}",
                "--profile-directory=Profile 10",
                "--no-first-run",
                "--no-default-browser-check",
            ),
        ),
    )
    opened: dict[str, str] = {}
    monkeypatch.setattr(
        "job_search_cli.cli.open_url_in_chrome_session",
        lambda cdp_http_endpoint, url: opened.update(
            {
                "cdp_http_endpoint": cdp_http_endpoint,
                "url": url,
            }
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "launch", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "Created Chrome session 'linkedin-qa-bcn'." in result.output
    assert "PID: 54321" in result.output
    assert (
        "Opened URL: https://www.linkedin.com/jobs/search/"
        "?currentJobId=1&f_TPR=r604800&geoId=105088894"
    ) in result.output
    assert opened == {
        "cdp_http_endpoint": "http://127.0.0.1:9333",
        "url": (
            "https://www.linkedin.com/jobs/search/"
            "?currentJobId=1&f_TPR=r604800&geoId=105088894"
        ),
    }

    records = load_session_records(tmp_path / "sessions.json")
    assert len(records) == 1
    assert records[0].sessionName == "linkedin-qa-bcn"
    assert records[0].pid == 54321
    assert records[0].cdpHttpEndpoint == "http://127.0.0.1:9333"
    assert records[0].wsEndpoint == "ws://127.0.0.1:9333/devtools/browser/new"
    assert records[0].createdAt != stale_record.createdAt


def test_launch_command_ignores_external_profile_path(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    missing_profile_dir = tmp_path / "Google" / "Chrome" / "Profile 10"
    profile_path = _write_profile(
        tmp_path,
        chrome_profile_path=str(missing_profile_dir),
    )
    monkeypatch.chdir(tmp_path)
    launched = []
    monkeypatch.setattr(
        "job_search_cli.cli.cleanup_chrome_processes_for_profile", lambda *a: None
    )
    monkeypatch.setattr(
        "job_search_cli.cli.launch_google_chrome",
        lambda profile: (
            launched.append(profile),
            ChromeLaunchResult(999999, "http://localhost:9333", "ws://test", ()),
        )[1],
    )
    monkeypatch.setattr(
        "job_search_cli.cli.open_url_in_chrome_session", lambda *a: None
    )

    result = runner.invoke(
        main,
        ["linkedin", "launch", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code == 0, result.output
    assert launched[0]["chromeProfilePath"] == str(
        tmp_path / "user-data/chrome/Default"
    )
    assert not missing_profile_dir.exists()


def test_launch_command_surfaces_missing_chrome_error(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    chrome_profile_dir = tmp_path / "Google" / "Chrome" / "Profile 10"
    chrome_profile_dir.mkdir(parents=True)
    profile_path = _write_profile(
        tmp_path,
        chrome_profile_path=str(chrome_profile_dir),
    )
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        "job_search_cli.cli.launch_google_chrome",
        lambda profile: (_ for _ in ()).throw(
            ChromeRuntimeError("Google Chrome executable was not found.")
        ),
    )

    result = runner.invoke(
        main,
        ["linkedin", "launch", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code != 0
    assert "Google Chrome executable was not found" in result.output


def test_launch_command_surfaces_locked_profile_error(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    chrome_profile_dir = tmp_path / "Google" / "Chrome" / "Profile 10"
    chrome_profile_dir.mkdir(parents=True)
    profile_path = _write_profile(
        tmp_path,
        chrome_profile_path=str(chrome_profile_dir),
    )
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        "job_search_cli.cli.launch_google_chrome",
        lambda profile: (_ for _ in ()).throw(
            ChromeRuntimeError(
                "Google Chrome failed to start with profile "
                f"'{chrome_profile_dir}'. Ensure the profile exists and is not "
                "locked by another Chrome process."
            )
        ),
    )

    result = runner.invoke(
        main,
        ["linkedin", "launch", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code != 0
    assert "locked by another Chrome process" in result.output


def test_launch_command_surfaces_logged_out_linkedin_session(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    chrome_profile_dir = tmp_path / "Google" / "Chrome" / "Profile 10"
    chrome_profile_dir.mkdir(parents=True)
    profile_path = _write_profile(
        tmp_path,
        chrome_profile_path=str(chrome_profile_dir),
    )
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        "job_search_cli.cli.launch_google_chrome",
        lambda profile: ChromeLaunchResult(
            pid=54321,
            cdp_http_endpoint="http://127.0.0.1:9333",
            ws_endpoint="ws://127.0.0.1:9333/devtools/browser/new",
            launch_args=(
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "--remote-debugging-port=9333",
            ),
        ),
    )
    monkeypatch.setattr(
        "job_search_cli.cli.open_url_in_chrome_session",
        lambda cdp_http_endpoint, url: (_ for _ in ()).throw(
            ChromeRuntimeError(
                "LinkedIn session appears logged out ('login modal' at "
                "'https://www.linkedin.com/jobs/search/?start=0'). "
                "Log in again and rerun the workflow."
            )
        ),
    )

    result = runner.invoke(
        main,
        ["linkedin", "launch", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code != 0
    assert "LinkedIn session appears logged out" in result.output


def test_launch_command_leaves_unrelated_profile_session_untouched(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrelated browser record cannot redirect the app-owned session."""
    runner = CliRunner()
    chrome_profile_dir = tmp_path / "Google" / "Chrome" / "Profile 10"
    chrome_profile_dir.mkdir(parents=True)
    profile_path = _write_profile(
        tmp_path,
        chrome_profile_path=str(chrome_profile_dir),
    )

    # Simulate an existing healthy session using the same Chrome profile path
    # but with a different session name.
    existing_session = _make_record(
        sessionName="existing-session",
        chromeProfilePath=str(chrome_profile_dir),
        pid=77777,
        cdpHttpEndpoint="http://127.0.0.1:9777",
        wsEndpoint="ws://127.0.0.1:9777/devtools/browser/existing",
    )
    write_session_records([existing_session], tmp_path / "sessions.json")

    # The existing session is healthy (PID alive, DevTools responsive).
    monkeypatch.setattr(
        "job_search_cli.cli.get_session_ws_endpoint",
        lambda record: record.wsEndpoint,
    )

    launched = []

    def fail_launch(profile: dict[str, object]) -> ChromeLaunchResult:
        launched.append(profile)
        return ChromeLaunchResult(999999, "http://localhost:9333", "ws://new", ())

    monkeypatch.setattr("job_search_cli.cli.launch_google_chrome", fail_launch)
    monkeypatch.setattr(
        "job_search_cli.cli.cleanup_chrome_processes_for_profile", lambda *a: None
    )
    monkeypatch.setattr(
        "job_search_cli.cli.open_url_in_chrome_session", lambda *a: None
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "launch", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code == 0, result.output
    assert launched[0]["chromeProfilePath"] == str(
        tmp_path / "user-data/chrome/Default"
    )
    assert any(
        r == existing_session for r in load_session_records(tmp_path / "sessions.json")
    )


def test_launch_command_allows_same_chrome_profile_when_other_session_is_stale(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Launch should proceed when conflicting session is stale (not healthy)."""
    runner = CliRunner()
    chrome_profile_dir = tmp_path / "Google" / "Chrome" / "Profile 10"
    chrome_profile_dir.mkdir(parents=True)
    profile_path = _write_profile(
        tmp_path,
        chrome_profile_path=str(chrome_profile_dir),
    )

    # Simulate an existing stale session using the same Chrome profile path
    stale_session = _make_record(
        sessionName="stale-session",
        chromeProfilePath=str(chrome_profile_dir),
        pid=66666,
        cdpHttpEndpoint="http://127.0.0.1:9666",
        wsEndpoint="ws://127.0.0.1:9666/devtools/browser/stale",
    )
    write_session_records([stale_session], tmp_path / "sessions.json")

    # The existing session is NOT healthy (DevTools not responsive).
    monkeypatch.setattr(
        "job_search_cli.cli.get_session_ws_endpoint",
        lambda record: None,
    )
    monkeypatch.setattr(
        "job_search_cli.cli.launch_google_chrome",
        lambda profile: ChromeLaunchResult(
            pid=54321,
            cdp_http_endpoint="http://127.0.0.1:9333",
            ws_endpoint="ws://127.0.0.1:9333/devtools/browser/new",
            launch_args=(
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "--remote-debugging-port=9333",
            ),
        ),
    )
    monkeypatch.setattr(
        "job_search_cli.cli.open_url_in_chrome_session",
        lambda cdp_http_endpoint, url: None,
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "launch", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "Created Chrome session 'linkedin-qa-bcn'." in result.output
    assert "PID: 54321" in result.output
