import os
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from job_search_cli.cli import main


def test_core_cli_runs_without_legacy_integration_modules(tmp_path) -> None:
    script = """
import importlib.abc
import sys
class NoLegacyIntegrations(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'job_search_cli.google_sheets_jobs', 'job_search_cli.opencode_server'}:
            raise ImportError('Legacy integration unavailable: ' + fullname)
sys.meta_path.insert(0, NoLegacyIntegrations())
from click.testing import CliRunner
from job_search_cli.cli import main
for args in (['--help'], ['linkedin', '--help'], ['portal', 'serve', '--help'],
             ['analyze-all', '--help'], ['summarize-all', '--help'], ['profiles', 'list']):
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output or repr(result.exception)
    assert not any(word in result.output.lower() for word in ('opencode', 'telegram', 'google sheets'))
    if args == ['profiles', 'list']:
        assert result.output.strip() == '[]'
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "JOB_SEARCH_DATA_DIR": str(tmp_path / "data"),
            "DB_PATH": str(tmp_path / "data" / "jobs.sqlite3"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_root_help_exits_zero() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["--help"], prog_name="job-search")

    assert result.exit_code == 0
    assert "Usage: job-search" in result.output
    assert "Job search automation commands." in result.output
    assert "linkedin" in result.output
    assert "portal" in result.output
    assert "analyze-all" in result.output
    assert "summarize-all" in result.output


def test_profile_cli_omits_retired_reporting_and_preserves_stored_value(tmp_path):
    import json
    import sqlite3

    from job_search_cli.job_store import upsert_profile

    db = tmp_path / "jobs.sqlite3"
    payload = {
        "sessionName": "core-search",
        "locationGeoId": "123",
        "f_TPR": "r86400",
        "chromeProfilePath": "/tmp/synthetic-chrome",
        "headless": False,
    }
    profile_id = upsert_profile(payload, db_path=db)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE profile SET reporting = ? WHERE id = ?",
            ('{"token":"PRIVATE-LEGACY-DESTINATION"}', profile_id),
        )
    for args in (["profiles", "list"], ["profiles", "show", str(profile_id)]):
        result = CliRunner().invoke(main, ["--db-path", str(db), *args])
        assert result.exit_code == 0, result.output
        assert "reporting" not in result.output
        assert "PRIVATE-LEGACY-DESTINATION" not in result.output
        json.loads(result.output)
    upsert_profile({**payload, "id": profile_id}, db_path=db)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT reporting FROM profile").fetchone()[0] == (
            '{"token":"PRIVATE-LEGACY-DESTINATION"}'
        )


def test_linkedin_help_lists_launch_close_get_job_ids_and_parse_job_commands() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["linkedin", "--help"], prog_name="job-search")

    assert result.exit_code == 0
    assert "Usage: job-search linkedin" in result.output
    assert "launch" in result.output
    assert "close" in result.output
    assert "get-job-ids" in result.output
    assert "parse-job" in result.output


def test_portal_help_lists_serve_command() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["portal", "--help"], prog_name="job-search")

    assert result.exit_code == 0
    assert "Usage: job-search portal" in result.output
    assert "serve" in result.output


def test_portal_serve_help_exposes_host_port_and_db_path_options() -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["portal", "serve", "--help"],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "Serve the local jobs portal." in result.output
    assert "--host TEXT" in result.output
    assert "--port INTEGER RANGE" in result.output
    assert "--db-path FILE" in result.output
    assert "--no-browser" in result.output


def test_linkedin_launch_help_exposes_profile_and_db_path_options() -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["linkedin", "launch", "--help"],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert (
        "Launch or reuse a LinkedIn Chrome session for a saved profile."
    ) in result.output
    assert "--profile PATH" in result.output
    assert "--db-path FILE" in result.output
    assert "--all" not in result.output
    assert "--session-name" not in result.output


def test_linkedin_close_help_describes_exactly_one_selector() -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["linkedin", "close", "--help"],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "Requires exactly one selector" in result.output
    assert "--all" in result.output
    assert "--session-name TEXT" in result.output
    assert "--profile" not in result.output


def test_linkedin_get_job_ids_help_exposes_only_profile_option() -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["linkedin", "get-job-ids", "--help"],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "Print unique LinkedIn job IDs" in result.output
    assert "--profile PATH" in result.output
    assert "--all" not in result.output
    assert "--session-name" not in result.output


def test_linkedin_parse_job_help_exposes_job_id_and_profile_option() -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["linkedin", "parse-job", "--help"],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "Open a LinkedIn job by id" in result.output
    assert "JOB_ID" in result.output
    assert "--profile PATH" in result.output
    assert "--all" not in result.output
    assert "--session-name" not in result.output


def test_analyze_all_help_exposes_only_profile_option() -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["analyze-all", "--help"],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "Analyze all non-analyzed jobs for one profile." in result.output
    assert "--profile PATH" in result.output
    assert "--concurrency INTEGER RANGE" in result.output
    assert "--all" not in result.output
    assert "--session-name" not in result.output


def test_summarize_all_help_exposes_only_profile_option() -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["summarize-all", "--help"],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "Summarize all matched jobs for one profile." in result.output
    assert "--profile PATH" in result.output
    assert "--concurrency INTEGER RANGE" in result.output
    assert "--all" not in result.output
    assert "--session-name" not in result.output


def test_linkedin_missing_command_returns_usage_error() -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["linkedin", "missing-command"],
        prog_name="job-search",
    )

    assert result.exit_code != 0
    assert "No such command 'missing-command'" in result.output


def test_linkedin_close_requires_exactly_one_selector() -> None:
    runner = CliRunner()

    missing_selector = runner.invoke(
        main,
        ["linkedin", "close"],
        prog_name="job-search",
    )
    both_selectors = runner.invoke(
        main,
        ["linkedin", "close", "--all", "--session-name", "linkedin-qa-bcn"],
        prog_name="job-search",
    )

    assert missing_selector.exit_code != 0
    assert "exactly one selector" in missing_selector.output
    assert both_selectors.exit_code != 0
    assert "exactly one selector" in both_selectors.output
