"""Authoritative profiles exercised through the CLI boundary."""

import json

import pytest
from click.testing import CliRunner

from job_search_cli.cli import main
from job_search_cli.job_store import upsert_profile
from job_search_cli.chrome import ChromeLaunchResult


def test_cli_lists_and_shows_profiles_in_selected_database(tmp_path):
    database = tmp_path / "selected.sqlite3"
    profile_id = upsert_profile(
        {
            "sessionName": "backend-search",
            "chromeProfilePath": str(tmp_path / "chrome"),
            "headless": False,
            "locationGeoId": "123",
            "f_TPR": "r86400",
            "jobTitle": "Python engineer",
            "not_want": "On-call",
        },
        db_path=database,
    )
    runner = CliRunner()
    listed = runner.invoke(main, ["--db-path", str(database), "profiles", "list"])
    assert listed.exit_code == 0, listed.output
    assert [(p["id"], p["sessionName"]) for p in json.loads(listed.output)] == [
        (profile_id, "backend-search")
    ]
    shown = runner.invoke(
        main, ["--db-path", str(database), "profiles", "show", str(profile_id)]
    )
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["jobTitle"] == "Python engineer"
    assert json.loads(shown.output)["not_want"] == "On-call"


def test_launch_selects_latest_database_profile_without_rewriting_legacy_json(
    tmp_path, monkeypatch
):
    payload = {
        "sessionName": "backend-search",
        "chromeProfilePath": str(tmp_path / "chrome"),
        "headless": True,
        "locationGeoId": "123",
        "f_TPR": "r86400",
        "jobTitle": "Old title",
    }
    profile_id = upsert_profile(payload)
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(payload), encoding="utf-8")
    original = legacy.read_bytes()
    upsert_profile(payload | {"id": profile_id, "jobTitle": "Database title"})
    opened = []
    monkeypatch.setattr(
        "job_search_cli.cli.cleanup_chrome_processes_for_profile", lambda *a: None
    )
    monkeypatch.setattr(
        "job_search_cli.cli.launch_google_chrome",
        lambda profile: ChromeLaunchResult(
            pid=999999,
            cdp_http_endpoint="http://localhost:9333",
            ws_endpoint="ws://localhost/test",
            launch_args=(),
        ),
    )
    monkeypatch.setattr(
        "job_search_cli.cli.get_session_ws_endpoint", lambda *a: "ws://localhost/test"
    )
    monkeypatch.setattr(
        "job_search_cli.cli.open_url_in_chrome_session",
        lambda endpoint, url: opened.append(url),
    )
    runner = CliRunner()
    for selection in (["--profile-id", str(profile_id)], ["--profile", str(legacy)]):
        result = runner.invoke(main, ["linkedin", "launch", *selection])
        assert result.exit_code == 0, result.output
        assert "keywords=Database+title" in opened[-1]
    assert legacy.read_bytes() == original
    shown = runner.invoke(main, ["profiles", "show", str(profile_id)])
    assert json.loads(shown.output)["jobTitle"] == "Database title"
    legacy.write_text(
        json.dumps({"id": profile_id, "headless": "stale"}), encoding="utf-8"
    )
    result = runner.invoke(main, ["linkedin", "launch", "--profile", str(legacy)])
    assert result.exit_code == 0, result.output
    assert "keywords=Database+title" in opened[-1]


@pytest.mark.parametrize(
    "command",
    [
        ["linkedin", "launch"],
        ["linkedin", "get-job-ids"],
        ["linkedin", "parse-job", "12345"],
        ["analyze-all"],
        ["summarize-all"],
    ],
)
def test_workflows_report_missing_database_profile_without_importing_json(
    tmp_path, command, monkeypatch
):
    from job_search_cli.ai_readiness import AiReadiness

    monkeypatch.setattr(
        "job_search_cli.cli.get_ai_readiness", lambda: AiReadiness(ready=True)
    )
    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        json.dumps(
            {
                "id": 99,
                "sessionName": "missing",
                "chromeProfilePath": str(tmp_path),
                "headless": False,
                "locationGeoId": "123",
                "f_TPR": "r86400",
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    for selection in (["--profile-id", "99"], ["--profile", str(legacy)]):
        result = runner.invoke(main, [*command, *selection])
        assert result.exit_code == 1, result.output
        assert "profile id '99' was not found" in result.output
        assert "profiles list" in result.output
    assert json.loads(runner.invoke(main, ["profiles", "list"]).output) == []


def test_full_flow_selects_database_profile_and_propagates_identity(
    tmp_path, monkeypatch
):
    import subprocess

    from job_search_cli.linkedin_full_flow import main as full_flow_main

    database = tmp_path / "selected.sqlite3"
    profile_id = upsert_profile(
        {
            "sessionName": "database-flow",
            "chromeProfilePath": str(tmp_path),
            "headless": False,
            "locationGeoId": "123",
            "f_TPR": "r86400",
        },
        db_path=database,
    )
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        output = "[]" if "get-job-ids" in command else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr("job_search_cli.linkedin_full_flow.subprocess.run", run)
    assert (
        full_flow_main(
            [
                "--profile-id",
                str(profile_id),
                "--db-path",
                str(database),
                "--repo",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert len(calls) >= 3
    for command, kwargs in calls:
        assert kwargs["env"]["DB_PATH"] == str(database)
        if "close" in command:
            assert command[-2:] == ["--session-name", "database-flow"]
        else:
            assert command[command.index("--profile-id") + 1] == str(profile_id)
    assert not list((tmp_path / "user-data/runtime").glob("*.json"))
