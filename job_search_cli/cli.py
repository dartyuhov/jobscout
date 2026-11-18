"""CLI entrypoints for the job search tool."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import click

from job_search_cli.ai_readiness import get_ai_readiness
from job_search_cli.browser_coordination import serialized_browser_operation
from job_search_cli.chrome import (
    ChromeRuntimeError,
    cleanup_chrome_processes_for_profile,
    close_tracked_chrome_session,
    get_job_ids_from_chrome_session,
    get_session_ws_endpoint,
    launch_google_chrome,
    open_url_in_chrome_session,
    parse_job_details_from_chrome_session,
)
from job_search_cli.linkedin_profile import ProfileLoadError, load_selected_profile
from job_search_cli.linkedin_url import (
    LINKEDIN_RESULTS_PER_PAGE,
    build_linkedin_jobs_page_urls,
    build_linkedin_jobs_url,
)
from job_search_cli.job_store import (
    JobStoreError,
    PendingLinkedInJob,
    PendingLinkedInSummaryJob,
    get_profile_preferences,
    get_profile,
    list_profiles,
    list_unsummarized_matched_linkedin_jobs,
    list_unanalyzed_linkedin_jobs,
    save_new_linkedin_job_ids,
    upsert_ai_analysis,
    upsert_ai_summary,
    upsert_linkedin_job_details,
)
from job_search_cli.sessions import (
    SessionRecord,
    SessionStoreError,
    load_session_records,
    save_session_record,
    write_session_records,
)
from job_search_cli.ai_client import (
    AiGenerationError,
    analyze_job,
    summarize_job,
    read_connection,
)
from job_search_cli.portal import (
    DEFAULT_PORTAL_HOST,
    DEFAULT_PORTAL_PORT,
    PortalServeError,
    serve_portal,
)
from job_search_cli.runtime_paths import (
    browser_profile_path,
    database_path,
    selected_database,
    child_environment,
    RuntimePathError,
)


DEFAULT_ANALYZE_CONCURRENCY = 4
MAX_ANALYZE_CONCURRENCY = 16


@click.group(
    name="job-search",
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Job search automation commands.",
)
@click.option(
    "--db-path",
    type=click.Path(path_type=Path, dir_okay=False),
    help="SQLite database for all commands (or DB_PATH).",
)
@click.pass_context
def main(ctx: click.Context, db_path: Path | None) -> None:
    """Root CLI command."""
    try:
        child_environment(db_path)
        token = selected_database.set(database_path(db_path))
    except RuntimePathError as exc:
        raise click.ClickException(str(exc)) from exc
    ctx.call_on_close(lambda: selected_database.reset(token))


@main.group(help="LinkedIn workflow commands.")
def linkedin() -> None:
    """LinkedIn commands."""


@linkedin.command(help="Prepare isolated Chrome storage; preserve any existing login.")
def prepare_browser() -> None:
    try:
        click.echo(json.dumps({"chromeProfilePath": str(browser_profile_path())}))
    except OSError as exc:
        raise click.ClickException(f"Could not prepare Chrome storage: {exc}") from exc


@linkedin.command(
    help="Open visible dedicated Chrome for one-time manual LinkedIn login."
)
@serialized_browser_operation
def login() -> None:
    try:
        profile_path = str(browser_profile_path())
        records, _ = _prune_to_healthy_sessions(load_session_records())
        record = next((r for r in records if r.chromeProfilePath == profile_path), None)
        if record is not None and record.headless:
            raise ChromeRuntimeError(
                "Close the dedicated headless session before manual login."
            )
        created = record is None
        if record is None:
            cleanup_chrome_processes_for_profile(profile_path)
            result = launch_google_chrome(
                {"chromeProfilePath": profile_path, "headless": False}
            )
            record = SessionRecord(
                sessionName="app-browser-login",
                wsEndpoint=result.ws_endpoint,
                cdpHttpEndpoint=result.cdp_http_endpoint,
                pid=result.pid,
                chromeProfilePath=profile_path,
                headless=False,
                createdAt=_utc_now(),
                lastUsedAt=_utc_now(),
            )
            # Persist before navigation so interrupted login is recoverable.
            save_session_record(record)
        try:
            open_url_in_chrome_session(
                record.cdpHttpEndpoint,
                "https://www.linkedin.com/login",
                manual_login=True,
            )
            click.echo("Sign in to LinkedIn manually in the dedicated Chrome window.")
            click.prompt(
                "When login and account challenges are complete, press Enter to close this window",
                default="",
                show_default=False,
            )
        finally:
            if created:
                close_tracked_chrome_session(record)
                write_session_records(records)
    except (ChromeRuntimeError, SessionStoreError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


@main.group(help="Inspect and exchange authoritative SQLite search profiles.")
def profiles() -> None:
    """Saved search profiles."""


@profiles.command(name="list", help="List saved profiles and their local IDs.")
def profiles_list() -> None:
    try:
        click.echo(json.dumps(list_profiles(), indent=2))
    except JobStoreError as exc:
        raise click.ClickException(str(exc)) from exc


@profiles.command(name="show", help="Show one saved profile by local ID.")
@click.argument("profile_id", type=click.IntRange(min=1))
def profiles_show(profile_id: int) -> None:
    try:
        click.echo(json.dumps(get_profile(profile_id), indent=2))
    except JobStoreError as exc:
        raise click.ClickException(str(exc)) from exc


@profiles.command(
    name="export",
    help="Export portable settings JSON. Review preference text for personal details.",
)
@click.argument("profile_id", type=click.IntRange(min=1))
def profiles_export(profile_id: int) -> None:
    from job_search_cli.profile_exchange import export_settings

    try:
        click.echo(json.dumps(export_settings(profile_id), indent=2))
    except JobStoreError as exc:
        raise click.ClickException(str(exc)) from exc


@profiles.command(
    name="import",
    help="Import settings JSON as a new local search (never restore runtime data).",
)
@click.argument(
    "settings_path", type=click.Path(path_type=Path, exists=True, dir_okay=False)
)
def profiles_import(settings_path: Path) -> None:
    from job_search_cli.profile_exchange import ProfileExchangeError, import_settings

    try:
        click.echo(json.dumps(import_settings(settings_path), indent=2))
    except (JobStoreError, ProfileExchangeError) as exc:
        raise click.ClickException(str(exc)) from exc


@main.group(help="Portal commands.")
def portal() -> None:
    """Portal commands."""


@portal.command(name="serve", help="Serve the local jobs portal.")
@click.option(
    "--host",
    type=str,
    default=DEFAULT_PORTAL_HOST,
    show_default=True,
    help="Host interface for the portal HTTP server.",
)
@click.option(
    "--port",
    type=click.IntRange(1, 65535),
    default=DEFAULT_PORTAL_PORT,
    show_default=True,
    help="Port for the portal HTTP server.",
)
@click.option(
    "--db-path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    show_default=True,
    help="Path to the SQLite jobs database.",
)
@click.option(
    "--no-browser",
    is_flag=True,
    help="Do not open the portal URL in the default browser.",
)
def portal_serve(host: str, port: int, db_path: Path | None, no_browser: bool) -> None:
    """Serve the local jobs portal."""
    try:
        serve_portal(
            host=host,
            port=port,
            db_path=db_path,
            open_browser=not no_browser,
        )
    except PortalServeError as exc:
        raise click.ClickException(str(exc)) from exc


@linkedin.command(help="Launch or reuse a LinkedIn Chrome session for a saved profile.")
@click.option(
    "--profile-id", type=click.IntRange(min=1), help="Saved SQLite profile ID."
)
@click.option(
    "--headed",
    is_flag=True,
    help="Show Chrome for this launch without saving a profile edit.",
)
@click.option(
    "--profile",
    "profile_path",
    type=click.Path(path_type=Path, dir_okay=False),
    metavar="PATH",
    help="Legacy JSON identity selector; settings are read from SQLite, never synced.",
)
@click.option(
    "--db-path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    show_default=True,
    help="Path to the SQLite database containing saved profiles.",
)
@serialized_browser_operation
def launch(
    profile_path: Path | None,
    profile_id: int | None,
    db_path: Path | None,
    headed: bool,
) -> None:
    """Launch or reuse a LinkedIn Chrome session."""
    try:
        profile = load_selected_profile(profile_path, profile_id, db_path=db_path)
        if headed:
            profile["headless"] = False
        app_browser = (
            Path(profile["chromeProfilePath"]).resolve() == browser_profile_path()
        )

        session_records = load_session_records()
        healthy_records, reusable_record, records_changed = _load_healthy_sessions(
            session_records,
            profile,
        )
        if records_changed:
            write_session_records(healthy_records)

        replaced_records: list[SessionRecord] = []
        if reusable_record is None and app_browser:
            reusable_record = next(
                (
                    record
                    for record in healthy_records
                    if Path(record.chromeProfilePath).resolve()
                    == browser_profile_path()
                ),
                None,
            )
            if (
                reusable_record is not None
                and reusable_record.headless
                and not profile["headless"]
            ):
                replaced_records = [
                    r for r in healthy_records if r.pid == reusable_record.pid
                ]
                close_tracked_chrome_session(reusable_record)
                healthy_records = [
                    r for r in healthy_records if r.pid != reusable_record.pid
                ]
                write_session_records(healthy_records)
                reusable_record = None

        linkedin_url = build_linkedin_jobs_url(profile)
        timestamp = _utc_now()

        if reusable_record is None:
            # Check if another session is already using the same Chrome profile
            conflicting_record = _find_conflicting_session_by_chrome_profile(
                healthy_records,
                profile["chromeProfilePath"],
                profile["sessionName"],
            )
            if conflicting_record is not None:
                raise ChromeRuntimeError(
                    f"Chrome profile '{profile['chromeProfilePath']}' is already in "
                    f"use by session '{conflicting_record.sessionName}'. "
                    "Close that session first or use a different Chrome profile."
                )

            cleanup_chrome_processes_for_profile(profile["chromeProfilePath"])
            launch_result = launch_google_chrome(profile)
            session_record = SessionRecord(
                sessionName=profile["sessionName"],
                wsEndpoint=launch_result.ws_endpoint,
                cdpHttpEndpoint=launch_result.cdp_http_endpoint,
                pid=launch_result.pid,
                chromeProfilePath=profile["chromeProfilePath"],
                headless=profile["headless"],
                createdAt=timestamp,
                lastUsedAt=timestamp,
            )
            action = "Created"
        else:
            session_record = replace(
                reusable_record,
                sessionName=profile["sessionName"],
                lastUsedAt=timestamp,
            )
            action = "Reused"

        try:
            # Track before navigation so a killed process can be recovered later.
            if replaced_records:
                healthy_records.extend(
                    replace(
                        session_record, sessionName=r.sessionName, createdAt=r.createdAt
                    )
                    for r in replaced_records
                )
                write_session_records(healthy_records)
            save_session_record(session_record)
            open_url_in_chrome_session(session_record.cdpHttpEndpoint, linkedin_url)
        except BaseException:
            if reusable_record is None:
                close_tracked_chrome_session(session_record)
            write_session_records(
                [
                    r
                    for r in healthy_records
                    if reusable_record is not None or r.pid != session_record.pid
                ]
            )
            raise
    except (
        ChromeRuntimeError,
        JobStoreError,
        ProfileLoadError,
        SessionStoreError,
    ) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"{action} Chrome session '{session_record.sessionName}'.")
    click.echo(f"PID: {session_record.pid}")
    click.echo(f"CDP HTTP endpoint: {session_record.cdpHttpEndpoint}")
    click.echo(f"CDP WebSocket endpoint: {session_record.wsEndpoint}")
    click.echo(f"Opened URL: {linkedin_url}")


@linkedin.command(help="Print unique LinkedIn job IDs from a tracked session.")
@click.option(
    "--profile-id", type=click.IntRange(min=1), help="Saved SQLite profile ID."
)
@click.option(
    "--profile",
    "profile_path",
    type=click.Path(path_type=Path, dir_okay=False),
    metavar="PATH",
    help="Legacy JSON identity selector; settings are read from SQLite, never synced.",
)
@serialized_browser_operation
def get_job_ids(profile_path: Path | None, profile_id: int | None) -> None:
    """Print all unique LinkedIn job IDs from the opened jobs page."""
    try:
        profile = load_selected_profile(profile_path, profile_id)
        profile_id = profile["id"]
        session_name = profile["sessionName"]
        if not isinstance(session_name, str):
            raise ProfileLoadError(
                f"Profile '{profile_path}': field 'sessionName' must be a string."
            )

        session_record = _find_session_by_name(
            load_session_records(),
            session_name,
        )
        if session_record is None:
            raise click.ClickException(
                f"Tracked LinkedIn session '{session_name}' was not found."
            )

        page_urls = build_linkedin_jobs_page_urls(
            profile,
            results_per_page=LINKEDIN_RESULTS_PER_PAGE,
        )
        job_ids = get_job_ids_from_chrome_session(
            session_record.cdpHttpEndpoint,
            page_urls=page_urls,
            results_per_page=LINKEDIN_RESULTS_PER_PAGE,
        )
        save_new_linkedin_job_ids(
            job_ids,
            profile_id=profile_id,
        )
    except (
        ChromeRuntimeError,
        JobStoreError,
        ProfileLoadError,
        SessionStoreError,
    ) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(_format_json_array(job_ids))


@linkedin.command(help="Open a LinkedIn job by id, parse details, and persist it.")
@click.argument("job_id", type=str, metavar="JOB_ID")
@click.option(
    "--profile-id", type=click.IntRange(min=1), help="Saved SQLite profile ID."
)
@click.option(
    "--profile",
    "profile_path",
    type=click.Path(path_type=Path, dir_okay=False),
    metavar="PATH",
    help="Legacy JSON identity selector; settings are read from SQLite, never synced.",
)
@serialized_browser_operation
def parse_job(job_id: str, profile_path: Path | None, profile_id: int | None) -> None:
    """Open one LinkedIn job page, parse details, and upsert into SQLite."""
    try:
        profile = load_selected_profile(profile_path, profile_id)
        profile_id = profile["id"]
        session_name = profile["sessionName"]
        if not isinstance(session_name, str):
            raise ProfileLoadError(
                f"Profile '{profile_path}': field 'sessionName' must be a string."
            )

        session_record = _find_session_by_name(
            load_session_records(),
            session_name,
        )
        if session_record is None:
            raise click.ClickException(
                f"Tracked LinkedIn session '{session_name}' was not found."
            )

        job_details = parse_job_details_from_chrome_session(
            session_record.cdpHttpEndpoint,
            job_id,
            parse_min_delay_ms=int(profile["parseMinDelayMs"]),
            parse_max_delay_ms=int(profile["parseMaxDelayMs"]),
            parse_retry_base_delay_ms=int(profile["parseRetryBaseDelayMs"]),
            parse_retry_max_delay_ms=int(profile["parseRetryMaxDelayMs"]),
            parse_reload_retries=int(profile["parseReloadRetries"]),
        )
        upsert_linkedin_job_details(job_details, parsed=True, profile_id=profile_id)
    except (
        ChromeRuntimeError,
        JobStoreError,
        ProfileLoadError,
        SessionStoreError,
    ) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Parsed LinkedIn job '{job_details.linkedin_job_id}'.")


@main.command(help="Analyze all non-analyzed jobs for one profile.")
@click.option(
    "--profile-id", type=click.IntRange(min=1), help="Saved SQLite profile ID."
)
@click.option(
    "--profile",
    "profile_path",
    type=click.Path(path_type=Path, dir_okay=False),
    metavar="PATH",
    help="Legacy JSON identity selector; settings are read from SQLite, never synced.",
)
@click.option(
    "--concurrency",
    type=click.IntRange(1, MAX_ANALYZE_CONCURRENCY),
    default=DEFAULT_ANALYZE_CONCURRENCY,
    show_default=True,
    help="Number of parallel job analyses to run.",
)
def analyze_all(
    profile_path: Path | None, profile_id: int | None, concurrency: int
) -> None:
    """Analyze pending jobs through the verified compatible connection."""
    ai = get_ai_readiness()
    if not ai.ready:
        raise click.ClickException(ai.guidance)
    analyzed_count = 0

    try:
        profile = load_selected_profile(profile_path, profile_id)
        profile_id = profile["id"]

        _, not_want = get_profile_preferences(profile_id)
        jobs = list_unanalyzed_linkedin_jobs(profile_id)
        if not jobs:
            click.echo("No pending jobs to analyze.")
            return

        server = read_connection()
        analysis_payloads = _analyze_jobs_in_parallel(
            server,
            jobs=jobs,
            not_want=not_want,
            concurrency=concurrency,
        )
        for job, analysis_payload in zip(jobs, analysis_payloads, strict=True):
            analyzed = cast(int, analysis_payload["analyzed"])
            desicion = cast(int, analysis_payload["desicion"])
            reasoning = cast(str, analysis_payload["reasoning"])
            upsert_ai_analysis(
                job_id=job.id,
                analyzed=analyzed,
                desicion=desicion,
                reasoning=reasoning,
            )
            analyzed_count += 1
            click.echo(
                f"Analyzed LinkedIn job '{job.linkedin_job_id}' (desicion={desicion})."
            )
    except (JobStoreError, AiGenerationError, ProfileLoadError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Analyzed {analyzed_count} pending job(s).")


@main.command(help="Summarize all matched jobs for one profile.")
@click.option(
    "--profile-id", type=click.IntRange(min=1), help="Saved SQLite profile ID."
)
@click.option(
    "--profile",
    "profile_path",
    type=click.Path(path_type=Path, dir_okay=False),
    metavar="PATH",
    help="Legacy JSON identity selector; settings are read from SQLite, never synced.",
)
@click.option(
    "--concurrency",
    type=click.IntRange(1, MAX_ANALYZE_CONCURRENCY),
    default=DEFAULT_ANALYZE_CONCURRENCY,
    show_default=True,
    help="Number of parallel job summaries to run.",
)
def summarize_all(
    profile_path: Path | None, profile_id: int | None, concurrency: int
) -> None:
    """Summarize all matched, reviewable, unsummarized jobs for one profile."""
    ai = get_ai_readiness()
    if not ai.ready:
        raise click.ClickException(ai.guidance)
    summarized_count = 0

    try:
        profile = load_selected_profile(profile_path, profile_id)
        profile_id = profile["id"]

        jobs = list_unsummarized_matched_linkedin_jobs(profile_id)
        if not jobs:
            click.echo("No matched jobs to summarize.")
            return

        server = read_connection()
        summary_payloads = _summarize_jobs_in_parallel(
            server,
            jobs=jobs,
            concurrency=concurrency,
        )
        for job, summary_payload in zip(jobs, summary_payloads, strict=True):
            upsert_ai_summary(job_id=job.id, summary_payload=summary_payload)
            summarized_count += 1
            click.echo(f"Summarized LinkedIn job '{job.linkedin_job_id}'.")
    except (JobStoreError, AiGenerationError, ProfileLoadError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Summarized {summarized_count} matched job(s).")


def _analyze_jobs_in_parallel(
    server: dict[str, object],
    *,
    jobs: list[PendingLinkedInJob],
    not_want: str,
    concurrency: int,
) -> list[dict[str, int | str]]:
    worker_count = min(max(concurrency, 1), len(jobs))

    def _analyze_one(job: PendingLinkedInJob) -> dict[str, int | str]:
        payload = analyze_job(
            server,
            not_want=not_want,
            job=job,
        )
        return cast(dict[str, int | str], payload)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return list(executor.map(_analyze_one, jobs))


def _summarize_jobs_in_parallel(
    server: dict[str, object],
    *,
    jobs: list[PendingLinkedInSummaryJob],
    concurrency: int,
) -> list[dict[str, object]]:
    worker_count = min(max(concurrency, 1), len(jobs))

    def _summarize_one(job: PendingLinkedInSummaryJob) -> dict[str, object]:
        payload = summarize_job(server, job=job)
        return cast(dict[str, object], payload)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return list(executor.map(_summarize_one, jobs))


@linkedin.command(
    help=(
        "Close tracked LinkedIn sessions. Requires exactly one selector: "
        "--all or --session-name TEXT."
    )
)
@click.option(
    "--all",
    "close_all",
    is_flag=True,
    help="Select all tracked sessions for closing.",
)
@click.option(
    "--session-name",
    type=str,
    metavar="TEXT",
    help="Select one tracked session by its saved session name.",
)
@serialized_browser_operation
def close(close_all: bool, session_name: str | None) -> None:
    """Close tracked LinkedIn sessions."""
    if close_all == (session_name is not None):
        raise click.UsageError(
            "Close requires exactly one selector: --all or --session-name TEXT."
        )

    try:
        session_records = load_session_records()
        healthy_records, records_changed = _prune_to_healthy_sessions(session_records)

        if close_all:
            closed_count = 0
            for record in healthy_records:
                close_tracked_chrome_session(record)
                closed_count += 1

            write_session_records([])
            click.echo(f"Closed {closed_count} tracked Chrome session(s).")
            return

        assert session_name is not None
        remaining_records: list[SessionRecord] = []
        target_record: SessionRecord | None = None

        for record in healthy_records:
            if record.sessionName == session_name and target_record is None:
                target_record = record
                continue
            remaining_records.append(record)

        if target_record is None:
            if records_changed:
                write_session_records(healthy_records)
            raise click.ClickException(
                f"Tracked LinkedIn session '{session_name}' was not found."
            )

        if not any(record.pid == target_record.pid for record in remaining_records):
            close_tracked_chrome_session(target_record)
        write_session_records(remaining_records)
    except (ChromeRuntimeError, SessionStoreError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Closed Chrome session '{target_record.sessionName}'.")
    click.echo(f"PID: {target_record.pid}")


@linkedin.command(name="cleanup-profile", help="Kill Chrome processes for one profile.")
@click.option(
    "--chrome-profile-path",
    type=str,
    metavar="PATH",
    required=True,
    help="Configured Chrome profile path to clean up.",
)
@serialized_browser_operation
def cleanup_profile(chrome_profile_path: str) -> None:
    """Terminate tracked sessions and leftover Chrome processes for one profile."""
    try:
        session_records = load_session_records()
        healthy_records, records_changed = _prune_to_healthy_sessions(session_records)
        normalized_profile_path = chrome_profile_path.strip()
        if not normalized_profile_path:
            raise click.ClickException("Chrome profile path must not be empty.")
        matching_records = [
            record
            for record in healthy_records
            if record.chromeProfilePath == normalized_profile_path
        ]

        for record in matching_records:
            close_tracked_chrome_session(record)

        if Path(normalized_profile_path).is_dir():
            cleanup_chrome_processes_for_profile(normalized_profile_path)

        remaining_records = [
            record
            for record in healthy_records
            if record.chromeProfilePath != normalized_profile_path
        ]
        removed_records = [
            record
            for record in session_records
            if record.chromeProfilePath == normalized_profile_path
        ]
        if records_changed or removed_records:
            write_session_records(remaining_records)
    except (ChromeRuntimeError, SessionStoreError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(
        json.dumps(
            {
                "chrome_profile_path": normalized_profile_path,
                "closed_session_names": [
                    record.sessionName for record in matching_records
                ],
                "closed_session_count": len(matching_records),
                "session_records_removed": len(removed_records),
            }
        )
    )


def _load_healthy_sessions(
    session_records: list[SessionRecord],
    profile: dict[str, object],
) -> tuple[list[SessionRecord], SessionRecord | None, bool]:
    healthy_records, records_changed = _prune_to_healthy_sessions(session_records)
    reusable_record: SessionRecord | None = None

    for record in healthy_records:
        if _matches_requested_session(record, profile):
            reusable_record = record
            break

    return healthy_records, reusable_record, records_changed


def _prune_to_healthy_sessions(
    session_records: list[SessionRecord],
) -> tuple[list[SessionRecord], bool]:
    healthy_records: list[SessionRecord] = []
    records_changed = False

    for record in session_records:
        ws_endpoint = get_session_ws_endpoint(record)
        if ws_endpoint is None:
            records_changed = True
            continue

        refreshed_record = record
        if ws_endpoint != record.wsEndpoint:
            refreshed_record = replace(record, wsEndpoint=ws_endpoint)
            records_changed = True

        healthy_records.append(refreshed_record)

    return healthy_records, records_changed


def _matches_requested_session(
    record: SessionRecord,
    profile: dict[str, object],
) -> bool:
    return (
        record.sessionName == profile["sessionName"]
        and record.chromeProfilePath == profile["chromeProfilePath"]
        and record.headless is profile["headless"]
    )


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _find_session_by_name(
    session_records: list[SessionRecord],
    session_name: str,
) -> SessionRecord | None:
    for record in session_records:
        if record.sessionName == session_name:
            return record
    return None


def _find_conflicting_session_by_chrome_profile(
    session_records: list[SessionRecord],
    chrome_profile_path: str,
    exclude_session_name: str,
) -> SessionRecord | None:
    """Find a session using the same Chrome profile path but with a different name.

    This detects when another session already holds the Chrome profile lock,
    which would cause Chrome to fail on launch.
    """
    for record in session_records:
        if (
            record.chromeProfilePath == chrome_profile_path
            and record.sessionName != exclude_session_name
        ):
            return record
    return None


def _format_json_array(values: list[str]) -> str:
    if not values:
        return "[ ]"

    serialized_values = ", ".join(json.dumps(value) for value in values)
    return f"[ {serialized_values} ]"


if __name__ == "__main__":
    main()
