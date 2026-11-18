from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from job_search_cli.chrome import ChromeRuntimeError
from job_search_cli.cli import main
from job_search_cli.job_store import JobStoreError, upsert_profile
from job_search_cli.sessions import SessionRecord, write_session_records


def _write_profile(
    tmp_path: Path,
    *,
    session_name: str = "linkedin-qa-bcn",
    max_page: int = 5,
    profile_id: int | None = 1,
) -> Path:
    payload = {
        "chromeProfilePath": "/tmp/chrome/Profile 10",
        "sessionName": session_name,
        "headless": False,
        "locationGeoId": "105088894",
        "f_TPR": "r604800",
        "maxPage": max_page,
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


def test_get_job_ids_command_prints_array_only_for_matching_session(
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
    saved: dict[str, object] = {}

    def fake_get_job_ids_from_chrome_session(
        cdp_http_endpoint: str,
        *,
        page_urls: list[str] | None = None,
        results_per_page: int = 25,
    ) -> list[str]:
        captured["cdp_http_endpoint"] = cdp_http_endpoint
        captured["page_urls"] = page_urls
        captured["results_per_page"] = results_per_page
        return ["123", "321", "123123"]

    monkeypatch.setattr(
        "job_search_cli.cli.get_job_ids_from_chrome_session",
        fake_get_job_ids_from_chrome_session,
    )
    monkeypatch.setattr(
        "job_search_cli.cli.save_new_linkedin_job_ids",
        lambda job_ids, **kwargs: (
            saved.update(
                {
                    "job_ids": job_ids,
                    "profile_id": kwargs.get("profile_id"),
                }
            )
            or len(job_ids)
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "get-job-ids", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert result.output.strip() == '[ "123", "321", "123123" ]'
    assert captured["cdp_http_endpoint"] == "http://127.0.0.1:9333"
    assert captured["results_per_page"] == 25
    assert captured["page_urls"] == [
        "https://www.linkedin.com/jobs/search/?currentJobId=1&f_TPR=r604800"
        "&geoId=105088894&start=0",
        "https://www.linkedin.com/jobs/search/?currentJobId=1&f_TPR=r604800"
        "&geoId=105088894&start=25",
        "https://www.linkedin.com/jobs/search/?currentJobId=1&f_TPR=r604800"
        "&geoId=105088894&start=50",
        "https://www.linkedin.com/jobs/search/?currentJobId=1&f_TPR=r604800"
        "&geoId=105088894&start=75",
        "https://www.linkedin.com/jobs/search/?currentJobId=1&f_TPR=r604800"
        "&geoId=105088894&start=100",
    ]
    assert saved == {
        "job_ids": ["123", "321", "123123"],
        "profile_id": 1,
    }


def test_get_job_ids_command_fails_when_session_is_missing(
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
        "job_search_cli.cli.get_job_ids_from_chrome_session",
        lambda cdp_http_endpoint, **kwargs: (_ for _ in ()).throw(
            AssertionError("get_job_ids_from_chrome_session should not be called")
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "get-job-ids", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code != 0
    assert "Tracked LinkedIn session 'linkedin-qa-bcn' was not found." in result.output


def test_get_job_ids_command_fails_when_database_profile_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    profile_path = _write_profile(tmp_path, profile_id=None)
    write_session_records([_make_record()], tmp_path / "sessions.json")

    monkeypatch.setattr(
        "job_search_cli.cli.get_job_ids_from_chrome_session",
        lambda cdp_http_endpoint, **kwargs: (_ for _ in ()).throw(
            AssertionError("get_job_ids_from_chrome_session should not be called")
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "get-job-ids", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code != 0
    assert "profile 'linkedin-qa-bcn' was not found" in result.output


def test_get_job_ids_command_surfaces_chrome_runtime_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    profile_path = _write_profile(tmp_path)
    write_session_records([_make_record()], tmp_path / "sessions.json")

    monkeypatch.setattr(
        "job_search_cli.cli.get_job_ids_from_chrome_session",
        lambda cdp_http_endpoint, **kwargs: (_ for _ in ()).throw(
            ChromeRuntimeError("Chrome session is unavailable.")
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "get-job-ids", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code != 0
    assert "Chrome session is unavailable." in result.output


def test_get_job_ids_command_surfaces_job_store_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    profile_path = _write_profile(tmp_path)
    write_session_records([_make_record()], tmp_path / "sessions.json")

    monkeypatch.setattr(
        "job_search_cli.cli.get_job_ids_from_chrome_session",
        lambda cdp_http_endpoint, **kwargs: ["123", "321"],
    )
    monkeypatch.setattr(
        "job_search_cli.cli.save_new_linkedin_job_ids",
        lambda job_ids, **kwargs: (_ for _ in ()).throw(
            JobStoreError("Job store '/tmp/jobs.sqlite3': could not be updated.")
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "get-job-ids", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code != 0
    assert "Job store '/tmp/jobs.sqlite3': could not be updated." in result.output
