from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from job_search_cli.chrome import ChromeRuntimeError
from job_search_cli.cli import main
from job_search_cli.job_store import JobStoreError, upsert_profile
from job_search_cli.linkedin_job import LinkedInJobDetails
from job_search_cli.sessions import SessionRecord, write_session_records


def _write_profile(
    tmp_path: Path,
    *,
    session_name: str = "linkedin-qa-bcn",
    profile_id: int | None = 1,
) -> Path:
    payload = {
        "chromeProfilePath": "/tmp/chrome/Profile 10",
        "sessionName": session_name,
        "headless": False,
        "locationGeoId": "105088894",
        "f_TPR": "r604800",
    }
    if profile_id is not None:
        payload["id"] = profile_id
        upsert_profile(payload)

    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
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


def _make_job_details(job_id: str) -> LinkedInJobDetails:
    return LinkedInJobDetails(
        linkedin_job_id=job_id,
        company_name="Example Company",
        position_name="QA Engineer",
        job_location="Seville, Andalusia, Spain",
        posted_date="2026-02-01",
        easy_apply=False,
        apply_link="https://example.com/apply",
        about_text="Full about text",
    )


def test_parse_job_command_parses_and_persists_job_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    profile_path = _write_profile(tmp_path)
    write_session_records(
        [_make_record(cdpHttpEndpoint="http://127.0.0.1:9333")],
        tmp_path / "sessions.json",
    )

    captured: dict[str, object] = {}

    def fake_parse_job_details_from_chrome_session(
        cdp_http_endpoint: str,
        job_id: str,
        **kwargs: object,
    ) -> LinkedInJobDetails:
        captured["cdp_http_endpoint"] = cdp_http_endpoint
        captured["job_id"] = job_id
        captured["kwargs"] = kwargs
        return _make_job_details(job_id)

    monkeypatch.setattr(
        "job_search_cli.cli.parse_job_details_from_chrome_session",
        fake_parse_job_details_from_chrome_session,
    )
    monkeypatch.setattr(
        "job_search_cli.cli.upsert_linkedin_job_details",
        lambda details, **kwargs: (
            captured.update(
                {
                    "details": details,
                    "parsed": kwargs.get("parsed"),
                    "profile_id": kwargs.get("profile_id"),
                }
            )
            or 1
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        [
            "linkedin",
            "parse-job",
            "101",
            "--profile",
            str(profile_path),
        ],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "Parsed LinkedIn job '101'." in result.output
    assert captured["cdp_http_endpoint"] == "http://127.0.0.1:9333"
    assert captured["job_id"] == "101"
    details = captured["details"]
    assert isinstance(details, LinkedInJobDetails)
    assert details.linkedin_job_id == "101"
    assert captured["parsed"] is True
    assert captured["profile_id"] == 1
    assert captured["kwargs"] == {
        "parse_min_delay_ms": 2000,
        "parse_max_delay_ms": 6000,
        "parse_retry_base_delay_ms": 1000,
        "parse_retry_max_delay_ms": 8000,
        "parse_reload_retries": 3,
    }


def test_parse_job_command_fails_when_session_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    profile_path = _write_profile(tmp_path)
    write_session_records(
        [_make_record(sessionName="linkedin-qa-remote")],
        tmp_path / "sessions.json",
    )

    monkeypatch.setattr(
        "job_search_cli.cli.parse_job_details_from_chrome_session",
        lambda cdp_http_endpoint, job_id, **kwargs: (_ for _ in ()).throw(
            AssertionError("parse_job_details_from_chrome_session should not be called")
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "parse-job", "101", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code != 0
    assert "Tracked LinkedIn session 'linkedin-qa-bcn' was not found." in result.output


def test_parse_job_command_fails_when_database_profile_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    profile_path = _write_profile(tmp_path, profile_id=None)
    write_session_records([_make_record()], tmp_path / "sessions.json")

    monkeypatch.setattr(
        "job_search_cli.cli.parse_job_details_from_chrome_session",
        lambda cdp_http_endpoint, job_id, **kwargs: (_ for _ in ()).throw(
            AssertionError("parse_job_details_from_chrome_session should not be called")
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "parse-job", "101", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code != 0
    assert "profile 'linkedin-qa-bcn' was not found" in result.output


def test_parse_job_command_surfaces_chrome_runtime_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    profile_path = _write_profile(tmp_path)
    write_session_records([_make_record()], tmp_path / "sessions.json")

    monkeypatch.setattr(
        "job_search_cli.cli.parse_job_details_from_chrome_session",
        lambda cdp_http_endpoint, job_id, **kwargs: (_ for _ in ()).throw(
            ChromeRuntimeError("LinkedIn job page is unavailable.")
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "parse-job", "101", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code != 0
    assert "LinkedIn job page is unavailable." in result.output


def test_parse_job_command_surfaces_job_store_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    profile_path = _write_profile(tmp_path)
    write_session_records([_make_record()], tmp_path / "sessions.json")

    monkeypatch.setattr(
        "job_search_cli.cli.parse_job_details_from_chrome_session",
        lambda cdp_http_endpoint, job_id, **kwargs: _make_job_details(job_id),
    )
    monkeypatch.setattr(
        "job_search_cli.cli.upsert_linkedin_job_details",
        lambda details, **kwargs: (_ for _ in ()).throw(
            JobStoreError("Job store '/tmp/jobs.sqlite3': could not be updated.")
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "parse-job", "101", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code != 0
    assert "Job store '/tmp/jobs.sqlite3': could not be updated." in result.output
