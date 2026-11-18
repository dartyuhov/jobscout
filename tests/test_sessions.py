from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import job_search_cli.sessions as sessions
from job_search_cli.cli import main
from job_search_cli.sessions import (
    SessionRecord,
    SessionStoreError,
    load_session_records,
    save_session_record,
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
        "createdAt": "2026-03-07T10:20:30.000Z",
        "lastUsedAt": "2026-03-07T10:20:30.000Z",
    }
    values.update(overrides)
    return SessionRecord(**values)


def test_load_session_records_missing_file_returns_empty_list(tmp_path) -> None:
    sessions_path = tmp_path / "sessions.json"

    assert load_session_records(sessions_path) == []


def test_write_session_records_round_trips_expected_schema(tmp_path) -> None:
    sessions_path = tmp_path / "sessions.json"
    record = _make_record()

    written_records = write_session_records([record], sessions_path)

    assert written_records == [record]
    assert json.loads(sessions_path.read_text(encoding="utf-8")) == [
        record.to_payload()
    ]
    assert load_session_records(sessions_path) == [record]


def test_save_session_record_replaces_existing_session_name(tmp_path) -> None:
    sessions_path = tmp_path / "sessions.json"
    first_record = _make_record()
    updated_record = _make_record(
        wsEndpoint="ws://127.0.0.1:9333/devtools/browser/updated",
        cdpHttpEndpoint="http://127.0.0.1:9333",
        pid=54321,
        lastUsedAt="2026-03-07T10:25:10.000Z",
    )

    save_session_record(first_record, sessions_path)
    updated_records = save_session_record(updated_record, sessions_path)

    assert updated_records == [updated_record]
    stored_records = load_session_records(sessions_path)
    assert stored_records == [updated_record]
    assert [record.sessionName for record in stored_records] == ["linkedin-qa-bcn"]


def test_load_session_records_rejects_invalid_json(tmp_path) -> None:
    sessions_path = tmp_path / "sessions.json"
    sessions_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(SessionStoreError) as exc_info:
        load_session_records(sessions_path)

    assert str(sessions_path) in str(exc_info.value)
    assert "invalid JSON" in str(exc_info.value)


def test_write_session_records_preserves_existing_file_on_replace_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions_path = tmp_path / "sessions.json"
    existing_record = _make_record()
    sessions_path.write_text(
        json.dumps([existing_record.to_payload()], indent=2),
        encoding="utf-8",
    )
    replace_calls: dict[str, Path] = {}

    def fail_replace(source_path: Path, destination_path: Path) -> None:
        replace_calls["source"] = source_path
        replace_calls["destination"] = destination_path
        raise OSError("replace failed")

    monkeypatch.setattr(sessions, "_replace_file", fail_replace)

    with pytest.raises(SessionStoreError) as exc_info:
        write_session_records([_make_record(pid=99999)], sessions_path)

    assert str(sessions_path) in str(exc_info.value)
    assert replace_calls["destination"] == sessions_path
    assert replace_calls["source"] != sessions_path
    assert load_session_records(sessions_path) == [existing_record]
    assert not list(tmp_path.glob(".sessions.json.*.tmp"))


def test_launch_command_fails_for_corrupt_sessions_json(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "chromeProfilePath": "/tmp/chrome/Profile 10",
                "sessionName": "linkedin-qa-bcn",
                "headless": False,
                "locationGeoId": "105088894",
                "f_TPR": "r604800",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "sessions.json").write_text("{not-json", encoding="utf-8")
    from job_search_cli.job_store import upsert_profile

    upsert_profile(json.loads(profile_path.read_text(encoding="utf-8")))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "launch", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code != 0
    assert "sessions.json" in result.output
    assert "invalid JSON" in result.output
