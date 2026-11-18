from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from click.testing import CliRunner

from job_search_cli.cli import main


@pytest.mark.parametrize(
    "override", ["JOB_SEARCH_DATA_DIR", "DB_PATH", "SESSIONS_PATH", "flag"]
)
def test_direct_cli_rejects_checkout_runtime_storage(monkeypatch, override):
    location = Path(__file__).resolve().parents[1] / "forbidden-review-runtime"
    args = ["profiles", "list"]
    if override == "flag":
        args = ["--db-path", str(location), *args]
    else:
        monkeypatch.setenv(override, str(location))
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 1
    assert "outside the checkout" in result.output
    assert not location.exists()


def test_fresh_cli_ignores_checkout_sessions_and_uses_private_user_storage(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    legacy = checkout / "sessions.json"
    legacy.write_text("private legacy data", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "xdg"))
    for key in ("JOB_SEARCH_DATA_DIR", "DB_PATH", "SESSIONS_PATH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(checkout)

    result = CliRunner().invoke(main, ["linkedin", "close", "--all"])

    assert result.exit_code == 0, result.output
    root = (
        home / "Library" / "Application Support" / "job-search"
        if sys.platform == "darwin"
        else home / "xdg" / "job-search"
    )
    assert json.loads((root / "sessions.json").read_text()) == []
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "sessions.json").stat().st_mode & 0o777 == 0o600
    assert legacy.read_text() == "private legacy data"


def test_cli_database_override_applies_to_collection_from_another_directory(
    tmp_path, monkeypatch
):
    from job_search_cli.sessions import SessionRecord, write_session_records

    selected = tmp_path / "selected" / "jobs.sqlite3"
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "id": 1,
                "sessionName": "synthetic",
                "chromeProfilePath": str(tmp_path),
                "headless": False,
                "locationGeoId": "123",
                "f_TPR": "r86400",
                "maxPage": 1,
            }
        )
    )
    from job_search_cli.job_store import upsert_profile

    upsert_profile(json.loads(profile.read_text()), db_path=selected)
    write_session_records(
        [
            SessionRecord(
                sessionName="synthetic",
                wsEndpoint="ws://localhost/test",
                cdpHttpEndpoint="http://localhost:9222",
                pid=123,
                chromeProfilePath=str(tmp_path),
                headless=False,
                createdAt="2026-01-01T00:00:00Z",
                lastUsedAt="2026-01-01T00:00:00Z",
            )
        ]
    )
    monkeypatch.setattr(
        "job_search_cli.cli.get_job_ids_from_chrome_session", lambda *a, **k: ["12345"]
    )
    monkeypatch.setattr(
        "job_search_cli.cli.open_url_in_chrome_session", lambda *a: None
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        main,
        [
            "--db-path",
            str(selected),
            "linkedin",
            "get-job-ids",
            "--profile",
            str(profile),
        ],
    )

    assert result.exit_code == 0, result.output
    with sqlite3.connect(selected) as connection:
        assert connection.execute(
            "SELECT linkedin_job_id FROM linkedin_jobs"
        ).fetchall() == [("12345",)]
    assert selected.stat().st_mode & 0o777 == 0o600
    assert selected.parent.stat().st_mode & 0o777 == 0o700
    assert not (tmp_path / "linkedin_jobs.sqlite3").exists()


def test_portal_serve_freezes_relative_overrides_before_changing_directory(
    tmp_path, monkeypatch
):
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SESSIONS_PATH", "selected/sessions.json")
    captured = {}

    class Process:
        def wait(self):
            return 0

    def popen(command, **kwargs):
        captured.update(kwargs)
        return Process()

    monkeypatch.setattr("job_search_cli.portal.shutil.which", lambda name: "/bin/node")
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(
        "job_search_cli.portal._wait_for_healthcheck", lambda *a, **k: None
    )
    result = CliRunner().invoke(
        main,
        ["portal", "serve", "--db-path", "selected/jobs.sqlite3", "--no-browser"],
    )
    assert result.exit_code == 0, result.output
    assert captured["cwd"] != tmp_path
    assert captured["env"]["DB_PATH"] == str(tmp_path / "selected/jobs.sqlite3")
    assert captured["env"]["SESSIONS_PATH"] == str(tmp_path / "selected/sessions.json")


def test_full_flow_freezes_overrides_for_children_and_writes_private_report(
    tmp_path, monkeypatch
):
    import subprocess

    from job_search_cli.linkedin_full_flow import main as full_flow_main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SESSIONS_PATH", "selected/sessions.json")
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "sessionName": "synthetic",
                "chromeProfilePath": str(tmp_path),
                "headless": False,
                "locationGeoId": "123",
                "f_TPR": "r86400",
            }
        )
    )
    calls = []

    from job_search_cli.job_store import upsert_profile

    upsert_profile(json.loads(profile.read_text()), db_path="selected/jobs.sqlite3")

    def run(command, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(command, 1, "", "synthetic launch failure")

    monkeypatch.setattr("job_search_cli.linkedin_full_flow.subprocess.run", run)
    assert (
        full_flow_main(
            [
                "--profile",
                str(profile),
                "--repo",
                str(tmp_path),
                "--db-path",
                "selected/jobs.sqlite3",
            ]
        )
        == 1
    )
    assert calls[0]["env"]["DB_PATH"] == str(tmp_path / "selected/jobs.sqlite3")
    assert calls[0]["env"]["SESSIONS_PATH"] == str(tmp_path / "selected/sessions.json")
    reports = list((tmp_path / "user-data/logs").glob("*.json"))
    assert len(reports) == 1
    assert reports[0].stat().st_mode & 0o777 == 0o600
    assert reports[0].parent.stat().st_mode & 0o777 == 0o700
    assert not list(tmp_path.glob("*.sqlite3"))
