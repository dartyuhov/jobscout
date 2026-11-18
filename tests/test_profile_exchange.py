"""Portable search settings exercised through the Click boundary."""

import json

import pytest
from click.testing import CliRunner

from job_search_cli.cli import main
from job_search_cli.job_store import upsert_profile


def test_cli_exports_only_portable_settings_and_imports_new_local_search(tmp_path):
    database = tmp_path / "selected.sqlite3"
    profile_id = upsert_profile(
        {
            "sessionName": "PRIVATE-SESSION",
            "chromeProfilePath": "/PRIVATE-MACHINE/chrome",
            "headless": True,
            "locationGeoId": "123",
            "f_TPR": "r86400",
            "f_WT": "2",
            "jobTitle": "Python engineer",
            "want": "Remote work",
            "not_want": "On-call",
            "maxPage": 3,
            "statePath": "/PRIVATE-STATE",
            "reporting": {"token": "PRIVATE-TOKEN", "chat": "PRIVATE-DESTINATION"},
        },
        db_path=database,
    )
    runner = CliRunner()
    args = ["--db-path", str(database), "profiles"]
    exported = runner.invoke(main, [*args, "export", str(profile_id)])
    assert exported.exit_code == 0, exported.output
    payload = json.loads(exported.stdout)
    assert payload["settings"] == {
        "jobTitle": "Python engineer",
        "locationGeoId": "123",
        "f_TPR": "r86400",
        "f_WT": "2",
        "want": "Remote work",
        "not_want": "On-call",
        "maxPage": 3,
    }
    assert "personal details" in payload["notice"]
    assert "PRIVATE-" not in exported.stdout
    source = tmp_path / "search.json"
    source.write_text(exported.stdout, encoding="utf-8")
    imported = runner.invoke(main, [*args, "import", str(source)])
    assert imported.exit_code == 0, imported.output
    local = json.loads(imported.stdout)
    assert local["id"] != profile_id
    assert local["sessionName"] != "PRIVATE-SESSION"
    assert local["chromeProfilePath"] == str(tmp_path / "user-data/chrome/Default")
    assert local["headless"] is False
    assert local["statePath"] == ""
    assert "reporting" not in local
    roundtrip = runner.invoke(main, [*args, "export", str(local["id"])])
    assert json.loads(roundtrip.stdout) == payload


def test_cli_import_ignores_foreign_runtime_fields_and_never_overwrites(tmp_path):
    runner = CliRunner()
    source = tmp_path / "search.json"
    source.write_text(
        json.dumps(
            {
                "format": "job-search-settings",
                "version": 1,
                "settings": {
                    "locationGeoId": "123",
                    "f_TPR": "r86400",
                    "want": "My personal preference",
                    "id": 800,
                    "sessionName": "PRIVATE-SESSION",
                    "chromeProfilePath": "/PRIVATE-PATH",
                    "headless": True,
                    "statePath": "/PRIVATE-STATE",
                    "reporting": {"token": "PRIVATE-SECRET"},
                    "cron": "* * * * *",
                    "cv_text": "PRIVATE-CV",
                    "jobs": ["PRIVATE-JOB"],
                    "conversations": ["PRIVATE-CHAT"],
                    "archived_at": "PRIVATE-DATE",
                },
                "token": "PRIVATE-PROVIDER",
            }
        ),
        encoding="utf-8",
    )
    first = runner.invoke(main, ["profiles", "import", str(source)])
    second = runner.invoke(main, ["profiles", "import", str(source)])
    assert first.exit_code == second.exit_code == 0
    a, b = json.loads(first.stdout), json.loads(second.stdout)
    assert a["id"] != b["id"]
    assert a["sessionName"] != b["sessionName"]
    assert a["id"] != 800
    exported = runner.invoke(main, ["profiles", "export", str(a["id"])])
    assert "PRIVATE-" not in exported.stdout
    assert "My personal preference" in exported.stdout
    assert "PRIVATE-" not in first.stdout


@pytest.mark.parametrize(
    "document, error",
    [
        ("{PRIVATE-SECRET", "JSON"),
        ("[]", "JSON object"),
        (json.dumps({"format": "job-search-settings", "version": True}), "version"),
        (json.dumps({"format": "job-search-settings", "version": 2}), "version"),
        (
            json.dumps({"format": "job-search-settings", "version": 1, "settings": []}),
            "settings",
        ),
        *[
            (
                json.dumps(
                    {
                        "format": "job-search-settings",
                        "version": 1,
                        "settings": {
                            "locationGeoId": "123",
                            "f_TPR": "r86400",
                            key: value,
                        },
                    }
                ),
                key,
            )
            for key, value in [
                ("locationGeoId", {}),
                ("locationGeoId", " "),
                ("f_TPR", None),
                ("want", ["PRIVATE-TEXT"]),
                ("not_want", False),
                ("jobTitle", 12),
                ("f_WT", {}),
                ("maxPage", True),
                ("maxPage", 1.5),
                ("maxPage", "3"),
                ("maxPage", 0),
                ("maxPage", 2147483648),
            ]
        ],
    ],
)
def test_cli_rejects_malformed_exchange_without_creating_profile(
    tmp_path, document, error
):
    source = tmp_path / "invalid.json"
    source.write_text(document, encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(main, ["profiles", "import", str(source)])
    assert result.exit_code == 1
    assert error in result.output
    assert "PRIVATE-" not in result.output
    assert json.loads(runner.invoke(main, ["profiles", "list"]).stdout) == []
