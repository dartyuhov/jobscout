from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from job_search_cli.cli import main
from job_search_cli.sessions import (
    SessionRecord,
    load_session_records,
    write_session_records,
)


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


def test_close_command_all_closes_live_sessions_prunes_stale_and_empties_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    sessions_path = tmp_path / "sessions.json"
    target_record = _make_record()
    other_record = _make_record(
        sessionName="linkedin-qa-remote",
        wsEndpoint="ws://127.0.0.1:9333/devtools/browser/other",
        cdpHttpEndpoint="http://127.0.0.1:9333",
        pid=54321,
        chromeProfilePath="/tmp/chrome/Profile 11",
    )
    stale_record = _make_record(
        sessionName="stale-session",
        wsEndpoint="ws://127.0.0.1:9444/devtools/browser/stale",
        cdpHttpEndpoint="http://127.0.0.1:9444",
        pid=99999,
        chromeProfilePath="/tmp/chrome/Profile 99",
    )
    write_session_records([target_record, other_record, stale_record], sessions_path)

    def fake_get_session_ws_endpoint(record: SessionRecord) -> str | None:
        if record.sessionName == stale_record.sessionName:
            return None
        return record.wsEndpoint

    closed_sessions: list[str] = []
    monkeypatch.setattr(
        "job_search_cli.cli.get_session_ws_endpoint",
        fake_get_session_ws_endpoint,
    )
    monkeypatch.setattr(
        "job_search_cli.cli.close_tracked_chrome_session",
        lambda record: closed_sessions.append(record.sessionName),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "close", "--all"],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "Closed 2 tracked Chrome session(s)." in result.output
    assert closed_sessions == ["linkedin-qa-bcn", "linkedin-qa-remote"]
    assert load_session_records(sessions_path) == []


def test_close_command_all_succeeds_when_session_store_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "close", "--all"],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "Closed 0 tracked Chrome session(s)." in result.output
    assert (tmp_path / "sessions.json").read_text(encoding="utf-8").strip() == "[]"


def test_close_command_session_name_removes_only_target_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    sessions_path = tmp_path / "sessions.json"
    target_record = _make_record()
    other_record = _make_record(
        sessionName="linkedin-qa-remote",
        wsEndpoint="ws://127.0.0.1:9333/devtools/browser/other",
        cdpHttpEndpoint="http://127.0.0.1:9333",
        pid=54321,
        chromeProfilePath="/tmp/chrome/Profile 11",
    )
    write_session_records([target_record, other_record], sessions_path)

    closed_sessions: list[str] = []
    monkeypatch.setattr(
        "job_search_cli.cli.get_session_ws_endpoint",
        lambda record: record.wsEndpoint,
    )
    monkeypatch.setattr(
        "job_search_cli.cli.close_tracked_chrome_session",
        lambda record: closed_sessions.append(record.sessionName),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "close", "--session-name", "linkedin-qa-bcn"],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "Closed Chrome session 'linkedin-qa-bcn'." in result.output
    assert "PID: 12345" in result.output
    assert closed_sessions == ["linkedin-qa-bcn"]
    assert load_session_records(sessions_path) == [other_record]


def test_close_command_session_name_errors_when_session_is_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    sessions_path = tmp_path / "sessions.json"
    other_record = _make_record(
        sessionName="linkedin-qa-remote",
        wsEndpoint="ws://127.0.0.1:9333/devtools/browser/other",
        cdpHttpEndpoint="http://127.0.0.1:9333",
        pid=54321,
        chromeProfilePath="/tmp/chrome/Profile 11",
    )
    stale_record = _make_record(
        sessionName="stale-session",
        wsEndpoint="ws://127.0.0.1:9444/devtools/browser/stale",
        cdpHttpEndpoint="http://127.0.0.1:9444",
        pid=99999,
        chromeProfilePath="/tmp/chrome/Profile 99",
    )
    write_session_records([other_record, stale_record], sessions_path)

    def fake_get_session_ws_endpoint(record: SessionRecord) -> str | None:
        if record.sessionName == stale_record.sessionName:
            return None
        return record.wsEndpoint

    monkeypatch.setattr(
        "job_search_cli.cli.get_session_ws_endpoint",
        fake_get_session_ws_endpoint,
    )
    monkeypatch.setattr(
        "job_search_cli.cli.close_tracked_chrome_session",
        lambda record: (_ for _ in ()).throw(
            AssertionError("close_tracked_chrome_session should not be called")
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "close", "--session-name", "linkedin-qa-bcn"],
        prog_name="job-search",
    )

    assert result.exit_code != 0
    assert "Tracked LinkedIn session 'linkedin-qa-bcn' was not found." in (
        result.output
    )
    assert load_session_records(sessions_path) == [other_record]
