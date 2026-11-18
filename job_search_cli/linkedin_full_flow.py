"""Sequential orchestrator for the LinkedIn full workflow."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from job_search_cli.ai_readiness import get_ai_readiness
from job_search_cli.browser_coordination import (
    inherited_browser_fds,
    serialized_browser_operation,
)
from job_search_cli.job_store import JobStoreError, filter_unparsed_linkedin_job_ids
from job_search_cli.linkedin_profile import ProfileLoadError, load_selected_profile
from job_search_cli.runtime_paths import (
    child_environment,
    data_dir,
    database_path,
    private_file,
)

DEFAULT_ANALYZE_CONCURRENCY = 4
MAX_ANALYZE_CONCURRENCY = 16

EXIT_SUCCESS = 0
EXIT_FATAL = 1
EXIT_PARTIAL = 2

MAX_RECORDED_OUTPUT_CHARS = 8_000


@dataclass(frozen=True, slots=True)
class CommandExecution:
    """Result for one executed CLI command."""

    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class FullFlowResult:
    """Full-flow run result with report payload and persisted path."""

    report: dict[str, Any]
    report_path: Path


CommandRunner = Callable[[list[str], Path], CommandExecution]


@serialized_browser_operation
def run_linkedin_full_flow(
    *,
    profile_path: Path | None = None,
    profile_id: int | None = None,
    repo_root: Path,
    analyze_concurrency: int = DEFAULT_ANALYZE_CONCURRENCY,
    max_jobs: int | None = None,
    report_path: Path | None = None,
    command_runner: CommandRunner | None = None,
    db_path: Path | None = None,
) -> FullFlowResult:
    """Run launch/get/parse/close/analyze/summarize sequentially and persist a JSON report."""
    started_at = _utc_now()
    ai = get_ai_readiness(db_path=database_path(db_path))
    environment = child_environment(db_path)
    effective_runner = command_runner or partial(
        _default_command_runner, env=environment
    )
    resolved_repo_root = repo_root.resolve()
    resolved_profile_path = (
        _resolve_profile_path(profile_path, resolved_repo_root)
        if profile_path
        else None
    )
    selection = (
        ["--profile-id", str(profile_id)]
        if profile_id is not None
        else ["--profile", str(resolved_profile_path)]
    )

    steps: dict[str, dict[str, Any]] = {}
    parse_jobs: list[dict[str, Any]] = []
    fatal_errors: list[str] = []
    failed_job_ids: list[str] = []
    job_ids: list[str] = []
    selected_job_ids: list[str] = []
    parsed_ok = 0
    parsed_failed = 0

    session_name: str | None = None
    launch_succeeded = False
    get_job_ids_succeeded = False

    try:
        profile = load_selected_profile(
            resolved_profile_path, profile_id, db_path=db_path
        )
        session_value = profile.get("sessionName")
        if not isinstance(session_value, str) or not session_value.strip():
            raise ProfileLoadError(
                f"Profile '{resolved_profile_path}': field 'sessionName' must be a non-empty string."
            )
        session_name = session_value
    except (ProfileLoadError, JobStoreError) as exc:
        fatal_errors.append(str(exc))

    if session_name is not None:
        launch_step = _run_cli_step(
            ["linkedin", "launch", *selection],
            repo_root=resolved_repo_root,
            command_runner=effective_runner,
        )
        steps["launch"] = launch_step["step"]
        launch_succeeded = launch_step["execution"].exit_code == 0
        if not launch_succeeded:
            fatal_errors.append(
                _build_failure_message("launch", launch_step["execution"])
            )

    if launch_succeeded and session_name is not None:
        get_step = _run_cli_step(
            ["linkedin", "get-job-ids", *selection],
            repo_root=resolved_repo_root,
            command_runner=effective_runner,
        )
        steps["get_job_ids"] = get_step["step"]
        get_job_ids_succeeded = get_step["execution"].exit_code == 0
        if get_job_ids_succeeded:
            try:
                job_ids = _parse_job_ids(get_step["execution"].stdout)
            except ValueError as exc:
                get_job_ids_succeeded = False
                fatal_errors.append(str(exc))
        else:
            fatal_errors.append(
                _build_failure_message("get-job-ids", get_step["execution"])
            )

    if launch_succeeded and get_job_ids_succeeded and session_name is not None:
        selected_job_ids = job_ids if max_jobs is None else job_ids[:max_jobs]
        try:
            new_job_ids = filter_unparsed_linkedin_job_ids(
                selected_job_ids, db_path=database_path(db_path)
            )
        except JobStoreError as exc:
            fatal_errors.append(str(exc))
        else:
            for job_id in new_job_ids:
                parse_step = _run_cli_step(
                    [
                        "linkedin",
                        "parse-job",
                        job_id,
                        *selection,
                    ],
                    repo_root=resolved_repo_root,
                    command_runner=effective_runner,
                )
                parse_payload = {"job_id": job_id, **parse_step["step"]}
                parse_jobs.append(parse_payload)
                if parse_step["execution"].exit_code == 0:
                    parsed_ok += 1
                else:
                    parsed_failed += 1
                    failed_job_ids.append(job_id)
            selected_job_ids = new_job_ids

    if launch_succeeded and session_name is not None:
        close_step = _run_cli_step(
            [
                "linkedin",
                "close",
                "--session-name",
                session_name,
            ],
            repo_root=resolved_repo_root,
            command_runner=effective_runner,
        )
        steps["close"] = close_step["step"]
        if close_step["execution"].exit_code != 0:
            fatal_errors.append(
                _build_failure_message("close", close_step["execution"])
            )
    else:
        steps["close"] = _skipped_step("Skipped because launch did not succeed.")

    ai = get_ai_readiness(db_path=database_path(db_path))
    if not ai.ready or not ai.auto_analyze:
        steps["analyze_all"] = _skipped_step(
            ai.guidance
            if not ai.ready
            else "Automatic AI analysis is disabled. Enable it separately in Settings."
        )
    elif launch_succeeded and get_job_ids_succeeded:
        analyze_step = _run_cli_step(
            [
                "analyze-all",
                *selection,
                "--concurrency",
                str(analyze_concurrency),
            ],
            repo_root=resolved_repo_root,
            command_runner=effective_runner,
        )
        steps["analyze_all"] = analyze_step["step"]
        if analyze_step["execution"].exit_code != 0:
            fatal_errors.append(
                _build_failure_message("analyze-all", analyze_step["execution"])
            )
    else:
        steps["analyze_all"] = _skipped_step(
            "Skipped because launch/get-job-ids did not succeed."
        )

    ai = get_ai_readiness(db_path=database_path(db_path))
    if not ai.ready or not ai.auto_summarize:
        steps["summarize_all"] = _skipped_step(
            ai.guidance
            if not ai.ready
            else "Automatic AI summaries are disabled. Enable them separately in Settings."
        )
    elif launch_succeeded and get_job_ids_succeeded and not fatal_errors:
        summarize_step = _run_cli_step(
            [
                "summarize-all",
                *selection,
                "--concurrency",
                str(analyze_concurrency),
            ],
            repo_root=resolved_repo_root,
            command_runner=effective_runner,
        )
        steps["summarize_all"] = summarize_step["step"]
        if summarize_step["execution"].exit_code != 0:
            fatal_errors.append(
                _build_failure_message("summarize-all", summarize_step["execution"])
            )
    else:
        steps["summarize_all"] = _skipped_step(
            "Skipped because analyze-all did not succeed."
        )

    if "launch" not in steps:
        steps["launch"] = _skipped_step("Skipped because profile validation failed.")
    if "get_job_ids" not in steps:
        steps["get_job_ids"] = _skipped_step("Skipped because launch did not succeed.")

    finished_at = _utc_now()
    duration_seconds = (
        datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        - datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    ).total_seconds()

    if fatal_errors:
        exit_code = EXIT_FATAL
    elif parsed_failed:
        exit_code = EXIT_PARTIAL
    else:
        exit_code = EXIT_SUCCESS

    report: dict[str, Any] = {
        "ai": {
            "ready": ai.ready,
            "auto_analyze": ai.auto_analyze,
            "auto_summarize": ai.auto_summarize,
        },
        "profile_path": str(resolved_profile_path) if resolved_profile_path else None,
        "profile_id": profile_id,
        "repo_root": str(resolved_repo_root),
        "session_name": session_name,
        "analyze_concurrency": analyze_concurrency,
        "max_jobs": max_jobs,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": duration_seconds,
        "job_ids_found": len(job_ids),
        "job_ids_selected": len(selected_job_ids),
        "parsed_ok": parsed_ok,
        "parsed_failed": parsed_failed,
        "failed_job_ids": failed_job_ids,
        "fatal_errors": fatal_errors,
        "steps": steps,
        "parse_jobs": parse_jobs,
        "exit_code": exit_code,
    }

    final_report_path = _resolve_report_path(
        report_path,
        repo_root=resolved_repo_root,
        session_name=session_name,
    )
    report["report_path"] = str(final_report_path)
    _write_report(report, final_report_path)
    return FullFlowResult(report=report, report_path=final_report_path)


def main(
    argv: list[str] | None = None,
    *,
    default_repo_root: Path | None = None,
) -> int:
    """CLI entrypoint for the orchestrator script."""
    parser = argparse.ArgumentParser(
        description=(
            "Run LinkedIn launch/get-job-ids/parse-job/close/analyze-all/summarize-all sequentially "
            "and write a JSON report."
        )
    )
    profile_selection = parser.add_mutually_exclusive_group(required=True)
    profile_selection.add_argument(
        "--profile",
        type=Path,
        help="Legacy JSON identity selector (absolute or repo-relative); no settings sync.",
    )
    profile_selection.add_argument(
        "--profile-id", type=int, help="Saved SQLite profile ID."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=default_repo_root or Path.cwd(),
        help="Repository root that contains job_search_cli/ and pyproject.toml.",
    )
    parser.add_argument(
        "--db-path", type=Path, default=None, help="SQLite database (or DB_PATH)."
    )
    parser.add_argument(
        "--analyze-concurrency",
        type=int,
        default=DEFAULT_ANALYZE_CONCURRENCY,
        help=f"Analyze-all concurrency (1..{MAX_ANALYZE_CONCURRENCY}).",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="Optional cap for parsed job IDs from get-job-ids output.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Optional output path for report JSON.",
    )

    args = parser.parse_args(argv)

    if (
        args.analyze_concurrency < 1
        or args.analyze_concurrency > MAX_ANALYZE_CONCURRENCY
    ):
        parser.error(
            f"--analyze-concurrency must be between 1 and {MAX_ANALYZE_CONCURRENCY}."
        )

    if args.max_jobs is not None and args.max_jobs < 1:
        parser.error("--max-jobs must be a positive integer.")

    result = run_linkedin_full_flow(
        profile_path=args.profile,
        profile_id=args.profile_id,
        repo_root=args.repo,
        analyze_concurrency=args.analyze_concurrency,
        max_jobs=args.max_jobs,
        report_path=args.report_path,
        db_path=args.db_path,
    )

    summary = {
        "ai": result.report["ai"],
        "ai_steps": {
            name: result.report["steps"][name]
            for name in ("analyze_all", "summarize_all")
        },
        "session_name": result.report["session_name"],
        "job_ids_found": result.report["job_ids_found"],
        "job_ids_selected": result.report["job_ids_selected"],
        "parsed_ok": result.report["parsed_ok"],
        "parsed_failed": result.report["parsed_failed"],
        "failed_job_ids": result.report["failed_job_ids"],
        "fatal_errors": result.report["fatal_errors"],
        "exit_code": result.report["exit_code"],
        "report_path": str(result.report_path),
    }
    print(json.dumps(summary, indent=2))
    return int(result.report["exit_code"])


def _run_cli_step(
    cli_args: list[str],
    *,
    repo_root: Path,
    command_runner: CommandRunner,
) -> dict[str, Any]:
    command = [sys.executable, "-m", "job_search_cli.cli", *cli_args]
    execution = command_runner(command, repo_root)
    return {
        "execution": execution,
        "step": {
            "command": _format_command(command),
            "ok": execution.exit_code == 0,
            "exit_code": execution.exit_code,
            "stdout": _trim_output(execution.stdout),
            "stderr": _trim_output(execution.stderr),
            "skipped": False,
        },
    }


def _default_command_runner(
    command: list[str], cwd: Path, *, env: dict[str, str] | None = None
) -> CommandExecution:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        pass_fds=inherited_browser_fds(),
    )
    return CommandExecution(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _build_failure_message(step_name: str, execution: CommandExecution) -> str:
    detail = execution.stderr.strip() or execution.stdout.strip()
    if not detail:
        detail = f"command exited with code {execution.exit_code}"
    detail_line = detail.splitlines()[-1].strip()
    return f"{step_name} failed: {detail_line}"


def _parse_job_ids(raw_output: str) -> list[str]:
    text = raw_output.strip()
    if not text:
        raise ValueError("get-job-ids returned empty output.")

    payload: Any
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end < start:
            raise ValueError("get-job-ids returned non-JSON output.") from None
        payload = json.loads(text[start : end + 1])

    if not isinstance(payload, list):
        raise ValueError("get-job-ids returned a non-array JSON payload.")

    normalized: list[str] = []
    seen: set[str] = set()
    for value in payload:
        if not isinstance(value, str):
            raise ValueError("get-job-ids returned non-string job ids.")
        job_id = value.strip()
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        normalized.append(job_id)

    return normalized


def _resolve_profile_path(profile_path: Path, repo_root: Path) -> Path:
    expanded = profile_path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (repo_root / expanded).resolve()


def _resolve_report_path(
    report_path: Path | None,
    *,
    repo_root: Path,
    session_name: str | None,
) -> Path:
    if report_path is not None:
        expanded = report_path.expanduser()
        if expanded.is_absolute():
            return expanded.resolve()
        return (repo_root / expanded).resolve()

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_session = _sanitize_name(session_name or "session")
    return (
        data_dir()
        / "logs"
        / f"linkedin-full-flow-{safe_session}-{timestamp}-{uuid4().hex}.json"
    )


def _write_report(report: dict[str, Any], report_path: Path) -> None:
    private_file(report_path)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _skipped_step(reason: str) -> dict[str, Any]:
    return {
        "command": None,
        "ok": False,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "skipped": True,
        "reason": reason,
    }


def _format_command(command: list[str]) -> str:
    return shlex.join(command)


def _trim_output(value: str) -> str:
    if len(value) <= MAX_RECORDED_OUTPUT_CHARS:
        return value
    return value[:MAX_RECORDED_OUTPUT_CHARS] + "\n...[truncated]"


def _sanitize_name(value: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in ("-", "_") else "-"
        for character in value
    )
    safe = safe.strip("-")
    return safe or "session"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
