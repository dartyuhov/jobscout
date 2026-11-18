from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_search_cli.job_store import upsert_profile
from job_search_cli.linkedin_full_flow import (
    EXIT_FATAL,
    EXIT_PARTIAL,
    EXIT_SUCCESS,
    CommandExecution,
    run_linkedin_full_flow,
)


def _write_profile(tmp_path: Path, *, session_name: str = "linkedin-qa-bcn") -> Path:
    payload = {
        "chromeProfilePath": "/tmp/chrome/Profile 10",
        "sessionName": session_name,
        "headless": False,
        "locationGeoId": "105088894",
        "f_TPR": "r604800",
    }
    profile_path = tmp_path / "profile.json"
    upsert_profile(payload)
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    return profile_path


def _execution(
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> CommandExecution:
    return CommandExecution(exit_code=exit_code, stdout=stdout, stderr=stderr)


@pytest.mark.parametrize(
    "analyze,summarize", [(False, False), (True, False), (False, True), (True, True)]
)
def test_full_flow_reads_independent_persisted_opt_ins(
    tmp_path, monkeypatch, analyze, summarize
):
    import sqlite3

    from job_search_cli.ai_readiness import get_ai_readiness

    profile_path = _write_profile(tmp_path)
    with sqlite3.connect(tmp_path / "linkedin_jobs.sqlite3") as db:
        db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        db.executemany(
            "INSERT INTO settings VALUES (?, ?)",
            [
                (
                    "ai_connection",
                    json.dumps(
                        {
                            "base_url": "http://127.0.0.1:1/v1",
                            "model": "local",
                            "token": "",
                            "revision": "1",
                            "verified_revision": "1",
                        }
                    ),
                ),
                (
                    "ai_automation",
                    json.dumps({"auto_analyze": analyze, "auto_summarize": summarize}),
                ),
            ],
        )
    monkeypatch.setattr(
        "job_search_cli.linkedin_full_flow.get_ai_readiness", get_ai_readiness
    )
    calls = []

    def run(command, cwd):
        args = command[3:]
        calls.append(args[0])
        return _execution(stdout='["100"]' if args[1] == "get-job-ids" else "ok")

    result = run_linkedin_full_flow(
        profile_path=profile_path,
        repo_root=tmp_path,
        command_runner=run,
        report_path=tmp_path / "report.json",
    )
    assert ("analyze-all" in calls) is analyze
    assert ("summarize-all" in calls) is summarize
    assert result.report["exit_code"] == EXIT_SUCCESS


class _FakeRunner:
    def __init__(self, responses: dict[tuple[str, ...], CommandExecution]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command: list[str], cwd: Path) -> CommandExecution:
        assert command[1:3] == ["-m", "job_search_cli.cli"]
        assert isinstance(cwd, Path)

        cli_args = tuple(command[3:])
        self.calls.append(cli_args)

        if cli_args not in self._responses:
            raise AssertionError(f"Missing fake response for command: {cli_args}")
        return self._responses[cli_args]


@pytest.fixture(autouse=True)
def verified_ai(monkeypatch):
    # Existing orchestration cases exercise the explicitly ready/opted-in path.
    from job_search_cli.ai_readiness import AiReadiness

    monkeypatch.setattr(
        "job_search_cli.linkedin_full_flow.get_ai_readiness",
        lambda **kwargs: AiReadiness(
            ready=True, auto_analyze=True, auto_summarize=True
        ),
    )


@pytest.mark.parametrize("use_profile_id", [False, True])
def test_full_flow_without_ai_collects_parses_closes_and_reports_skips(
    tmp_path: Path, monkeypatch, use_profile_id
) -> None:
    from job_search_cli.ai_readiness import get_ai_readiness

    monkeypatch.setattr(
        "job_search_cli.linkedin_full_flow.get_ai_readiness", get_ai_readiness
    )
    profile_path = _write_profile(tmp_path)
    selection = {"profile_id": 1} if use_profile_id else {"profile_path": profile_path}
    calls = []

    def collect_only(command, cwd):
        args = command[3:]
        calls.append(args)
        assert args[0] == "linkedin", "Unconfigured full-flow must not invoke AI"
        return _execution(stdout='["100"]' if args[1] == "get-job-ids" else "ok")

    result = run_linkedin_full_flow(
        **selection,
        repo_root=tmp_path,
        command_runner=collect_only,
        report_path=tmp_path / "report.json",
    )

    assert result.report["exit_code"] == EXIT_SUCCESS
    assert result.report["parsed_ok"] == 1
    assert result.report["fatal_errors"] == []
    assert [args[1] for args in calls] == [
        "launch",
        "get-job-ids",
        "parse-job",
        "close",
    ]
    for name in ("analyze_all", "summarize_all"):
        step = result.report["steps"][name]
        assert step["skipped"] is True
        assert "AI" in step["reason"]
        assert "Settings" in step["reason"]
    assert result.report["ai"]["ready"] is False
    assert result.report["profile_id"] == (1 if use_profile_id else None)
    assert result.report["profile_path"] == (
        None if use_profile_id else str(profile_path.resolve())
    )
    for args in calls[:-1]:
        assert args[-2:] == (
            ["--profile-id", "1"]
            if use_profile_id
            else ["--profile", str(profile_path.resolve())]
        )


def test_full_flow_success_runs_all_steps_in_order(tmp_path: Path) -> None:
    profile_path = _write_profile(tmp_path)
    report_path = tmp_path / "report-success.json"
    runner = _FakeRunner(
        {
            (
                "linkedin",
                "launch",
                "--profile",
                str(profile_path),
            ): _execution(stdout="launch ok"),
            (
                "linkedin",
                "get-job-ids",
                "--profile",
                str(profile_path),
            ): _execution(stdout='[ "100", "200" ]'),
            (
                "linkedin",
                "parse-job",
                "100",
                "--profile",
                str(profile_path),
            ): _execution(stdout="parsed 100"),
            (
                "linkedin",
                "parse-job",
                "200",
                "--profile",
                str(profile_path),
            ): _execution(stdout="parsed 200"),
            (
                "linkedin",
                "close",
                "--session-name",
                "linkedin-qa-bcn",
            ): _execution(stdout="closed"),
            (
                "analyze-all",
                "--profile",
                str(profile_path),
                "--concurrency",
                "3",
            ): _execution(stdout="analyzed"),
            (
                "summarize-all",
                "--profile",
                str(profile_path),
                "--concurrency",
                "3",
            ): _execution(stdout="summarized"),
        }
    )

    result = run_linkedin_full_flow(
        profile_path=profile_path,
        repo_root=tmp_path,
        analyze_concurrency=3,
        report_path=report_path,
        command_runner=runner,
    )

    assert result.report["exit_code"] == EXIT_SUCCESS
    assert result.report["job_ids_found"] == 2
    assert result.report["job_ids_selected"] == 2
    assert result.report["parsed_ok"] == 2
    assert result.report["parsed_failed"] == 0
    assert result.report["failed_job_ids"] == []
    assert result.report["fatal_errors"] == []
    assert result.report["steps"]["close"]["ok"] is True
    assert result.report["steps"]["analyze_all"]["ok"] is True
    assert result.report["steps"]["summarize_all"]["ok"] is True
    assert runner.calls == [
        ("linkedin", "launch", "--profile", str(profile_path)),
        ("linkedin", "get-job-ids", "--profile", str(profile_path)),
        ("linkedin", "parse-job", "100", "--profile", str(profile_path)),
        ("linkedin", "parse-job", "200", "--profile", str(profile_path)),
        ("linkedin", "close", "--session-name", "linkedin-qa-bcn"),
        ("analyze-all", "--profile", str(profile_path), "--concurrency", "3"),
        ("summarize-all", "--profile", str(profile_path), "--concurrency", "3"),
    ]

    persisted = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert persisted["exit_code"] == EXIT_SUCCESS
    assert persisted["report_path"] == str(report_path)


@pytest.mark.parametrize("auto_summarize", [False, True])
def test_ready_connection_does_not_opt_in_to_background_analysis(
    tmp_path, monkeypatch, auto_summarize
):
    from job_search_cli.ai_readiness import AiReadiness

    monkeypatch.setattr(
        "job_search_cli.linkedin_full_flow.get_ai_readiness",
        lambda **kwargs: AiReadiness(ready=True, auto_summarize=auto_summarize),
    )
    calls = []

    def run(command, cwd):
        args = command[3:]
        calls.append(args[0])
        assert args[0] != "analyze-all"
        return _execution(stdout="[]" if args[1] == "get-job-ids" else "ok")

    result = run_linkedin_full_flow(
        profile_path=_write_profile(tmp_path),
        repo_root=tmp_path,
        command_runner=run,
        report_path=tmp_path / "report.json",
    )
    assert result.report["exit_code"] == EXIT_SUCCESS
    assert result.report["steps"]["analyze_all"]["skipped"] is True
    assert result.report["steps"]["summarize_all"]["skipped"] is not auto_summarize
    assert ("summarize-all" in calls) is auto_summarize


def test_full_flow_parses_only_unparsed_job_ids(
    tmp_path: Path,
    monkeypatch,
) -> None:
    profile_path = _write_profile(tmp_path)
    report_path = tmp_path / "report-new-only.json"
    runner = _FakeRunner(
        {
            (
                "linkedin",
                "launch",
                "--profile",
                str(profile_path),
            ): _execution(stdout="launch ok"),
            (
                "linkedin",
                "get-job-ids",
                "--profile",
                str(profile_path),
            ): _execution(stdout='[ "100", "200", "300" ]'),
            (
                "linkedin",
                "parse-job",
                "200",
                "--profile",
                str(profile_path),
            ): _execution(stdout="parsed 200"),
            (
                "linkedin",
                "close",
                "--session-name",
                "linkedin-qa-bcn",
            ): _execution(stdout="closed"),
            (
                "analyze-all",
                "--profile",
                str(profile_path),
                "--concurrency",
                "4",
            ): _execution(stdout="analyzed"),
            (
                "summarize-all",
                "--profile",
                str(profile_path),
                "--concurrency",
                "4",
            ): _execution(stdout="summarized"),
        }
    )
    monkeypatch.setattr(
        "job_search_cli.linkedin_full_flow.filter_unparsed_linkedin_job_ids",
        lambda job_ids, **kwargs: ["200"],
    )

    result = run_linkedin_full_flow(
        profile_path=profile_path,
        repo_root=tmp_path,
        report_path=report_path,
        command_runner=runner,
    )

    assert result.report["exit_code"] == EXIT_SUCCESS
    assert result.report["job_ids_found"] == 3
    assert result.report["job_ids_selected"] == 1
    assert result.report["parsed_ok"] == 1
    assert result.report["parsed_failed"] == 0
    assert [job["job_id"] for job in result.report["parse_jobs"]] == ["200"]
    assert runner.calls == [
        ("linkedin", "launch", "--profile", str(profile_path)),
        ("linkedin", "get-job-ids", "--profile", str(profile_path)),
        ("linkedin", "parse-job", "200", "--profile", str(profile_path)),
        ("linkedin", "close", "--session-name", "linkedin-qa-bcn"),
        ("analyze-all", "--profile", str(profile_path), "--concurrency", "4"),
        ("summarize-all", "--profile", str(profile_path), "--concurrency", "4"),
    ]


def test_full_flow_continues_after_parse_failures_and_returns_partial_exit(
    tmp_path: Path,
) -> None:
    profile_path = _write_profile(tmp_path)
    report_path = tmp_path / "report-partial.json"
    runner = _FakeRunner(
        {
            (
                "linkedin",
                "launch",
                "--profile",
                str(profile_path),
            ): _execution(stdout="launch ok"),
            (
                "linkedin",
                "get-job-ids",
                "--profile",
                str(profile_path),
            ): _execution(stdout='[ "100", "200" ]'),
            (
                "linkedin",
                "parse-job",
                "100",
                "--profile",
                str(profile_path),
            ): _execution(exit_code=1, stderr="job 100 failed"),
            (
                "linkedin",
                "parse-job",
                "200",
                "--profile",
                str(profile_path),
            ): _execution(stdout="parsed 200"),
            (
                "linkedin",
                "close",
                "--session-name",
                "linkedin-qa-bcn",
            ): _execution(stdout="closed"),
            (
                "analyze-all",
                "--profile",
                str(profile_path),
                "--concurrency",
                "4",
            ): _execution(stdout="analyzed"),
            (
                "summarize-all",
                "--profile",
                str(profile_path),
                "--concurrency",
                "4",
            ): _execution(stdout="summarized"),
        }
    )

    result = run_linkedin_full_flow(
        profile_path=profile_path,
        repo_root=tmp_path,
        report_path=report_path,
        command_runner=runner,
    )

    assert result.report["exit_code"] == EXIT_PARTIAL
    assert result.report["parsed_ok"] == 1
    assert result.report["parsed_failed"] == 1
    assert result.report["failed_job_ids"] == ["100"]
    assert result.report["fatal_errors"] == []
    assert result.report["steps"]["close"]["ok"] is True
    assert result.report["steps"]["analyze_all"]["ok"] is True
    assert result.report["steps"]["summarize_all"]["ok"] is True


def test_full_flow_closes_session_when_get_job_ids_fails(tmp_path: Path) -> None:
    profile_path = _write_profile(tmp_path)
    report_path = tmp_path / "report-fatal.json"
    runner = _FakeRunner(
        {
            (
                "linkedin",
                "launch",
                "--profile",
                str(profile_path),
            ): _execution(stdout="launch ok"),
            (
                "linkedin",
                "get-job-ids",
                "--profile",
                str(profile_path),
            ): _execution(exit_code=1, stderr="get ids failed"),
            (
                "linkedin",
                "close",
                "--session-name",
                "linkedin-qa-bcn",
            ): _execution(stdout="closed"),
        }
    )

    result = run_linkedin_full_flow(
        profile_path=profile_path,
        repo_root=tmp_path,
        report_path=report_path,
        command_runner=runner,
    )

    assert result.report["exit_code"] == EXIT_FATAL
    assert result.report["steps"]["close"]["ok"] is True
    assert result.report["steps"]["analyze_all"]["skipped"] is True
    assert result.report["steps"]["summarize_all"]["skipped"] is True
    assert "get-job-ids failed:" in result.report["fatal_errors"][0]
    assert runner.calls == [
        ("linkedin", "launch", "--profile", str(profile_path)),
        ("linkedin", "get-job-ids", "--profile", str(profile_path)),
        ("linkedin", "close", "--session-name", "linkedin-qa-bcn"),
    ]


def test_full_flow_stops_with_clear_message_when_linkedin_session_is_logged_out(
    tmp_path: Path,
) -> None:
    profile_path = _write_profile(tmp_path)
    report_path = tmp_path / "report-logged-out.json"
    runner = _FakeRunner(
        {
            (
                "linkedin",
                "launch",
                "--profile",
                str(profile_path),
            ): _execution(
                exit_code=1,
                stderr=(
                    "LinkedIn session appears logged out ('login modal' at "
                    "'https://www.linkedin.com/jobs/search/?start=0'). "
                    "Log in again and rerun the workflow."
                ),
            ),
        }
    )

    result = run_linkedin_full_flow(
        profile_path=profile_path,
        repo_root=tmp_path,
        report_path=report_path,
        command_runner=runner,
    )

    assert result.report["exit_code"] == EXIT_FATAL
    assert result.report["steps"]["get_job_ids"]["skipped"] is True
    assert result.report["steps"]["close"]["skipped"] is True
    assert result.report["steps"]["analyze_all"]["skipped"] is True
    assert result.report["steps"]["summarize_all"]["skipped"] is True
    assert "LinkedIn session appears logged out" in result.report["fatal_errors"][0]
    assert runner.calls == [
        ("linkedin", "launch", "--profile", str(profile_path)),
    ]
