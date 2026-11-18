"""Google Chrome launch and health-check helpers."""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from job_search_cli.linkedin_job import LinkedInJobDetails
from job_search_cli.chrome_executable import (
    chrome_candidates as _google_chrome_candidates,
)
from job_search_cli.sessions import SessionRecord

DEFAULT_CDP_HOST = "127.0.0.1"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 1.0
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 2.0
DEFAULT_STARTUP_TIMEOUT_SECONDS = 10.0
DEVTOOLS_VERSION_PATH = "/json/version"
JOB_ID_SELECTOR = "[data-job-id]"
HUMAN_ACTION_MIN_WAIT_MILLISECONDS = 1_000
HUMAN_ACTION_MAX_WAIT_MILLISECONDS = 3_000
PAGE_READY_POLL_WAIT_MILLISECONDS = 250
PAGE_READY_TIMEOUT_MILLISECONDS = 10_000
JOB_NAVIGATION_TIMEOUT_MILLISECONDS = 60_000
JOB_NAVIGATION_MAX_RETRIES = 3
JOB_NAVIGATION_RETRY_WAIT_MILLISECONDS = 2_000
JOB_PARSE_WAIT_MILLISECONDS = 500
JOB_PARSE_MIN_DELAY_MILLISECONDS = 2_000
JOB_PARSE_MAX_DELAY_MILLISECONDS = 6_000
JOB_PARSE_RELOAD_RETRIES = 3
JOB_PARSE_RETRY_BASE_DELAY_MILLISECONDS = 1_000
JOB_PARSE_RETRY_MAX_DELAY_MILLISECONDS = 8_000
JOB_VIEW_URL_PREFIX = "https://www.linkedin.com/jobs/view/"
MAX_JOB_ID_COLLECTION_ROUNDS = 80
MAX_STAGNANT_JOB_ID_ROUNDS = 8
PLAYWRIGHT_BROWSER_CHANNEL = "chrome"
PLAYWRIGHT_WORKER_FLAG = "--playwright-worker"
PLAYWRIGHT_WORKER_MODULE = "job_search_cli.chrome"

JOB_VIEW_ID_PATTERN = re.compile(r"/jobs/view/(\d+)")
POSTED_DATE_UNIT_PATTERN = re.compile(
    r"(?P<count>\d+)\+?\s*"
    r"(?P<unit>"
    r"minutes?|mins?|min|hours?|hrs?|hr|days?|d|weeks?|w|months?|mos?|mo|years?|yrs?|yr"
    r")\s+ago"
)
POSTED_DATE_ABSOLUTE_FORMATS = (
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d, %Y, %I:%M %p",
    "%B %d, %Y, %I:%M %p",
    "%d %b %Y",
    "%d %B %Y",
)
POSTED_DATE_EMBEDDED_PATTERN = re.compile(
    r"([A-Za-z]+\s+\d{1,2},\s+\d{4}(?:,\s+\d{1,2}:\d{2}\s+[AP]M)?)"
)


class ChromeRuntimeError(ValueError):
    """Raised when Chrome cannot be launched or inspected safely."""


@dataclass(frozen=True, slots=True)
class ChromeProfilePaths:
    """Launch-ready Chrome profile arguments derived from the config path."""

    chrome_profile_path: str
    user_data_dir: str
    profile_directory: str


@dataclass(frozen=True, slots=True)
class ChromeLaunchResult:
    """Resolved Chrome process and DevTools endpoint details."""

    pid: int
    cdp_http_endpoint: str
    ws_endpoint: str
    launch_args: tuple[str, ...]


def resolve_chrome_profile_paths(
    chrome_profile_path: str | Path,
) -> ChromeProfilePaths:
    """Split a Chrome profile directory into the exact launch arguments."""
    raw_profile_path = str(chrome_profile_path)
    profile_path = Path(raw_profile_path)
    profile_directory = profile_path.name
    user_data_dir_path = profile_path.parent

    if not profile_directory or user_data_dir_path == profile_path:
        raise ChromeRuntimeError(
            f"Chrome profile path '{raw_profile_path}' must point to a profile "
            "directory inside the Chrome user data directory."
        )

    if not profile_path.is_dir():
        raise ChromeRuntimeError(
            f"Chrome profile path '{raw_profile_path}' does not exist or is not "
            "a directory."
        )

    return ChromeProfilePaths(
        chrome_profile_path=raw_profile_path,
        user_data_dir=str(user_data_dir_path),
        profile_directory=profile_directory,
    )


def build_google_chrome_launch_args(
    chrome_executable: str | Path,
    *,
    chrome_profile_path: str | Path,
    headless: bool,
    remote_debugging_port: int,
    remote_debugging_host: str = DEFAULT_CDP_HOST,
) -> tuple[str, ...]:
    """Build the exact Chrome command line for the configured profile."""
    profile_paths = resolve_chrome_profile_paths(chrome_profile_path)
    return _build_launch_args(
        chrome_executable,
        profile_paths=profile_paths,
        headless=headless,
        remote_debugging_port=remote_debugging_port,
        remote_debugging_host=remote_debugging_host,
    )


def _build_launch_args(
    chrome_executable: str | Path,
    *,
    profile_paths: ChromeProfilePaths,
    headless: bool,
    remote_debugging_port: int,
    remote_debugging_host: str,
) -> tuple[str, ...]:
    launch_args = [
        str(chrome_executable),
        f"--remote-debugging-port={remote_debugging_port}",
        f"--remote-debugging-address={remote_debugging_host}",
        f"--user-data-dir={profile_paths.user_data_dir}",
        f"--profile-directory={profile_paths.profile_directory}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        launch_args.append("--headless=new")
    return tuple(launch_args)


def _build_playwright_worker_command(
    *,
    profile_paths: ChromeProfilePaths,
    headless: bool,
    remote_debugging_port: int,
    remote_debugging_host: str,
) -> tuple[str, ...]:
    command = [
        sys.executable,
        "-m",
        PLAYWRIGHT_WORKER_MODULE,
        PLAYWRIGHT_WORKER_FLAG,
        f"--user-data-dir={profile_paths.user_data_dir}",
        f"--profile-directory={profile_paths.profile_directory}",
        f"--remote-debugging-port={remote_debugging_port}",
        f"--remote-debugging-host={remote_debugging_host}",
    ]
    if headless:
        command.append("--headless")
    return tuple(command)


def find_google_chrome_executable() -> str:
    """Locate a Google Chrome executable on the current workstation."""
    checked_paths: list[str] = []
    for candidate in _google_chrome_candidates():
        if candidate is None:
            continue
        path = Path(candidate)
        checked_paths.append(str(path))
        if path.is_file():
            return str(path)

    raise ChromeRuntimeError(
        "Google Chrome executable was not found. Checked: "
        f"{', '.join(checked_paths)}. Install Google Chrome in a standard "
        "location."
    )


def launch_google_chrome(
    profile: dict[str, Any],
    *,
    startup_timeout: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
) -> ChromeLaunchResult:
    """Launch regular Google Chrome for the configured profile."""
    profile_paths = resolve_chrome_profile_paths(profile["chromeProfilePath"])
    cdp_port = _reserve_cdp_port()
    cdp_http_endpoint = f"http://{DEFAULT_CDP_HOST}:{cdp_port}"
    chrome_executable = find_google_chrome_executable()
    launch_args = _build_launch_args(
        chrome_executable,
        profile_paths=profile_paths,
        headless=profile["headless"],
        remote_debugging_port=cdp_port,
        remote_debugging_host=DEFAULT_CDP_HOST,
    )

    with tempfile.TemporaryFile() as stderr_handle:
        try:
            process = subprocess.Popen(
                launch_args,
                stdout=subprocess.DEVNULL,
                stderr=stderr_handle,
                start_new_session=True,
            )
        except OSError as exc:
            raise ChromeRuntimeError(
                f"Google Chrome could not be started ({_format_os_error(exc)})."
            ) from exc

        try:
            version_payload = _wait_for_devtools_version(
                process,
                cdp_http_endpoint=cdp_http_endpoint,
                chrome_profile_path=profile["chromeProfilePath"],
                stderr_handle=stderr_handle,
                startup_timeout=startup_timeout,
            )
        except ChromeRuntimeError:
            _stop_process(process)
            raise

    return ChromeLaunchResult(
        pid=process.pid,
        cdp_http_endpoint=cdp_http_endpoint,
        ws_endpoint=_extract_ws_endpoint(
            cdp_http_endpoint=cdp_http_endpoint,
            version_payload=version_payload,
        ),
        launch_args=launch_args,
    )


def cleanup_chrome_processes_for_profile(
    chrome_profile_path: str | Path,
    *,
    shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
) -> None:
    """Stop leftover local Chrome processes for the configured profile path."""
    profile_paths = resolve_chrome_profile_paths(chrome_profile_path)
    matching_pids = _find_local_chrome_profile_pids(profile_paths)
    if not matching_pids:
        return

    for pid in matching_pids:
        _signal_profile_process(
            pid,
            signal.SIGTERM,
            chrome_profile_path=profile_paths.chrome_profile_path,
        )

    remaining_pids = _wait_for_pids_exit(matching_pids, timeout=shutdown_timeout)
    if not remaining_pids:
        return

    force_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
    for pid in remaining_pids:
        _signal_profile_process(
            pid,
            force_signal,
            chrome_profile_path=profile_paths.chrome_profile_path,
        )

    remaining_pids = _wait_for_pids_exit(remaining_pids, timeout=shutdown_timeout)
    if not remaining_pids:
        return

    pid_list = ", ".join(str(pid) for pid in remaining_pids)
    raise ChromeRuntimeError(
        f"Chrome processes for profile '{profile_paths.chrome_profile_path}' did not "
        f"stop after termination signals (pids: {pid_list})."
    )


def is_session_record_healthy(
    record: SessionRecord,
    *,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
) -> bool:
    """Return whether a stored session can be safely reused."""
    return get_session_ws_endpoint(record, request_timeout=request_timeout) is not None


def get_session_ws_endpoint(
    record: SessionRecord,
    *,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
) -> str | None:
    """Return a live browser WebSocket endpoint when the session is reusable."""
    if not is_process_alive(record.pid):
        return None

    try:
        version_payload = fetch_cdp_version(
            record.cdpHttpEndpoint,
            timeout=request_timeout,
        )
    except ChromeRuntimeError:
        return None

    try:
        return _extract_ws_endpoint(
            cdp_http_endpoint=record.cdpHttpEndpoint,
            version_payload=version_payload,
        )
    except ChromeRuntimeError:
        return None


def open_url_in_chrome_session(
    cdp_http_endpoint: str, url: str, *, manual_login: bool = False
) -> None:
    """Open a URL in a new tab of the tracked Chrome profile context."""
    _ensure_node_no_deprecation_option()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(cdp_http_endpoint)
            if not browser.contexts:
                raise ChromeRuntimeError(
                    f"Chrome DevTools endpoint '{cdp_http_endpoint}' did not expose "
                    "a default browser context."
                )

            page = browser.contexts[0].new_page()
            page.goto(url, wait_until="domcontentloaded")
            page.bring_to_front()
            if manual_login:
                return
            for attempt in range(2):
                try:
                    _raise_if_protection_page(page)
                    _wait_with_human_delay(page)
                    _raise_if_protection_page(page)
                    break
                except PlaywrightError as exc:
                    if not _is_transient_navigation_error(exc) or attempt >= 1:
                        raise
                    page.wait_for_load_state("domcontentloaded")
    except PlaywrightError as exc:
        raise ChromeRuntimeError(
            f"Could not open '{url}' in Chrome session at '{cdp_http_endpoint}' "
            f"({exc})."
        ) from exc


def get_job_ids_from_chrome_session(
    cdp_http_endpoint: str,
    *,
    page_urls: list[str] | None = None,
    results_per_page: int = 25,
) -> list[str]:
    """Return unique job IDs from one or more pages in a tracked session."""
    _ensure_node_no_deprecation_option()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(cdp_http_endpoint)
            if not browser.contexts:
                raise ChromeRuntimeError(
                    f"Chrome DevTools endpoint '{cdp_http_endpoint}' did not expose "
                    "a default browser context."
                )

            context = browser.contexts[0]
            if not context.pages:
                raise ChromeRuntimeError(
                    f"Chrome DevTools endpoint '{cdp_http_endpoint}' did not expose "
                    "any pages for job ID extraction."
                )

            page = context.pages[-1]

            if page_urls is None:
                _raise_if_protection_page(page)
                _wait_with_human_delay(page)
                _wait_for_search_results_page_ready(page)
                return _extract_visible_job_ids(
                    page=page,
                    cdp_http_endpoint=cdp_http_endpoint,
                )

            all_job_ids: list[str] = []
            seen_job_ids: set[str] = set()
            for page_url in page_urls:
                page.goto(page_url, wait_until="domcontentloaded")
                page.bring_to_front()
                _raise_if_protection_page(page)
                _wait_with_human_delay(page)
                _wait_for_search_results_page_ready(page)
                page_job_ids = _collect_page_job_ids(
                    page,
                    cdp_http_endpoint=cdp_http_endpoint,
                    results_per_page=results_per_page,
                )
                if not page_job_ids:
                    break
                _extend_unique_job_ids(page_job_ids, all_job_ids, seen_job_ids)

            return all_job_ids
    except PlaywrightError as exc:
        raise ChromeRuntimeError(
            "Could not collect job IDs from Chrome session at "
            f"'{cdp_http_endpoint}' ({exc})."
        ) from exc


def _extract_visible_job_ids(*, page: Any, cdp_http_endpoint: str) -> list[str]:
    raw_job_ids = page.eval_on_selector_all(
        JOB_ID_SELECTOR,
        "elements => elements.map((element) => element.getAttribute('data-job-id'))",
    )

    if not isinstance(raw_job_ids, list):
        raise ChromeRuntimeError(
            "Could not collect job IDs from Chrome session at "
            f"'{cdp_http_endpoint}' (unexpected page payload)."
        )

    job_ids: list[str] = []
    seen_job_ids: set[str] = set()
    for raw_job_id in raw_job_ids:
        if not isinstance(raw_job_id, str):
            continue

        job_id = raw_job_id.strip()
        if not job_id.isdigit() or job_id in seen_job_ids:
            continue

        seen_job_ids.add(job_id)
        job_ids.append(job_id)

    return job_ids


def _collect_page_job_ids(
    page: Any,
    *,
    cdp_http_endpoint: str,
    results_per_page: int,
) -> list[str]:
    page_job_ids: list[str] = []
    seen_page_job_ids: set[str] = set()
    stagnant_rounds = 0

    for _ in range(MAX_JOB_ID_COLLECTION_ROUNDS):
        _raise_if_protection_page(page)
        visible_job_ids = _extract_visible_job_ids(
            page=page,
            cdp_http_endpoint=cdp_http_endpoint,
        )
        previous_count = len(page_job_ids)
        _extend_unique_job_ids(visible_job_ids, page_job_ids, seen_page_job_ids)

        if len(page_job_ids) >= results_per_page:
            break

        moved = _scroll_job_results(page)
        if len(page_job_ids) > previous_count:
            stagnant_rounds = 0
        else:
            stagnant_rounds += 1

        if not moved:
            stagnant_rounds += 1

        if stagnant_rounds >= MAX_STAGNANT_JOB_ID_ROUNDS:
            break

        _wait_with_human_delay(page)

    return page_job_ids


def _scroll_job_results(page: Any) -> bool:
    payload = page.evaluate(
        """
        () => {
          const cards = Array.from(document.querySelectorAll('[data-job-id]'));
          let container = null;

          for (const card of cards) {
            let parent = card.parentElement;
            while (parent) {
              const style = window.getComputedStyle(parent);
              const canScroll = (style.overflowY === 'auto' || style.overflowY === 'scroll')
                && parent.scrollHeight > parent.clientHeight;
              if (canScroll) {
                if (!container || parent.scrollHeight > container.scrollHeight) {
                  container = parent;
                }
              }
              parent = parent.parentElement;
            }
          }

          if (!container) {
            container = document.scrollingElement || document.documentElement;
          }

          const before = container.scrollTop;
          const step = Math.max(Math.floor(container.clientHeight * 0.9), 300);
          container.scrollTop = Math.min(container.scrollTop + step, container.scrollHeight);
          return container.scrollTop > before;
        }
        """
    )

    return isinstance(payload, bool) and payload


def _extend_unique_job_ids(
    source_job_ids: list[str],
    destination_job_ids: list[str],
    seen_job_ids: set[str],
) -> None:
    for job_id in source_job_ids:
        if job_id in seen_job_ids:
            continue
        seen_job_ids.add(job_id)
        destination_job_ids.append(job_id)


def _navigate_to_job_with_retry(page: Any, job_url: str) -> None:
    """Navigate to a job URL with retry logic for timeout failures."""
    last_error: PlaywrightError | None = None

    for attempt in range(JOB_NAVIGATION_MAX_RETRIES + 1):
        try:
            page.goto(
                job_url,
                wait_until="domcontentloaded",
                timeout=JOB_NAVIGATION_TIMEOUT_MILLISECONDS,
            )
            return
        except PlaywrightError as exc:
            last_error = exc
            error_message = str(exc).lower()
            is_timeout = "timeout" in error_message

            if not is_timeout or attempt >= JOB_NAVIGATION_MAX_RETRIES:
                raise

            page.wait_for_timeout(JOB_NAVIGATION_RETRY_WAIT_MILLISECONDS)

    if last_error:
        raise last_error


def parse_job_details_from_chrome_session(
    cdp_http_endpoint: str,
    job_id: str,
    *,
    parse_min_delay_ms: int = JOB_PARSE_MIN_DELAY_MILLISECONDS,
    parse_max_delay_ms: int = JOB_PARSE_MAX_DELAY_MILLISECONDS,
    parse_retry_base_delay_ms: int = JOB_PARSE_RETRY_BASE_DELAY_MILLISECONDS,
    parse_retry_max_delay_ms: int = JOB_PARSE_RETRY_MAX_DELAY_MILLISECONDS,
    parse_reload_retries: int = JOB_PARSE_RELOAD_RETRIES,
) -> LinkedInJobDetails:
    """Open one LinkedIn job page and return normalized details."""
    normalized_job_id = job_id.strip()
    if not normalized_job_id.isdigit():
        raise ChromeRuntimeError(
            f"LinkedIn job id '{job_id}' must contain only digits."
        )

    _ensure_node_no_deprecation_option()
    job_url = f"{JOB_VIEW_URL_PREFIX}{normalized_job_id}"

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(cdp_http_endpoint)
            if not browser.contexts:
                raise ChromeRuntimeError(
                    f"Chrome DevTools endpoint '{cdp_http_endpoint}' did not expose "
                    "a default browser context."
                )

            context = browser.contexts[0]
            if not context.pages:
                raise ChromeRuntimeError(
                    f"Chrome DevTools endpoint '{cdp_http_endpoint}' did not expose "
                    "any pages for LinkedIn job parsing."
                )

            page = context.pages[-1]
            _navigate_to_job_with_retry(page, job_url)
            page.bring_to_front()
            _raise_if_protection_page(page)
            _wait_with_human_delay(page)
            _wait_for_job_details_page_ready_with_retry(page)
            _wait_before_job_parse(
                page,
                min_delay_ms=parse_min_delay_ms,
                max_delay_ms=parse_max_delay_ms,
            )
            page.wait_for_timeout(JOB_PARSE_WAIT_MILLISECONDS)
            payload = _extract_job_details_payload_with_retry(
                page,
                normalized_job_id,
                job_url,
                parse_retry_base_delay_ms=parse_retry_base_delay_ms,
                parse_retry_max_delay_ms=parse_retry_max_delay_ms,
                parse_reload_retries=parse_reload_retries,
            )
    except PlaywrightError as exc:
        raise ChromeRuntimeError(
            "Could not parse LinkedIn job details from Chrome session at "
            f"'{cdp_http_endpoint}' ({exc})."
        ) from exc

    return _normalize_job_details(payload, requested_job_id=normalized_job_id)


def _extract_job_details_payload(page: Any, job_id: str) -> dict[str, Any]:
    payload = page.evaluate(
        r"""
        (jobId) => {
          const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();

          const firstText = (selectors) => {
            for (const selector of selectors) {
              const element = document.querySelector(selector);
              const textValue = clean(element ? element.textContent : '');
              if (textValue) {
                return textValue;
              }
            }
            return '';
          };

          const firstHref = (selectors) => {
            for (const selector of selectors) {
              const element = document.querySelector(selector);
              if (!element || element.tagName !== 'A') {
                continue;
              }
              const href = clean(element.getAttribute('href') || '');
              if (href) {
                return href;
              }
            }
            return '';
          };

          const expandDescriptionElement = Array.from(
            document.querySelectorAll('button, a')
          ).find((element) => {
            const textValue = clean(element.textContent || '');
            const ariaValue = clean(element.getAttribute('aria-label') || '');
            return (
              /show more/i.test(textValue) &&
              /description|job/i.test(`${textValue} ${ariaValue}`)
            );
          });
          if (expandDescriptionElement instanceof HTMLElement) {
            expandDescriptionElement.click();
          }

          const mainElement = document.querySelector('main');
          const mainText = mainElement ? String(mainElement.innerText || '') : '';
          const unavailableMatch = mainText.match(
            /job id provided may not be valid or the job posting has been removed\.?/i
          );
          const lines = mainText
            .split('\n')
            .map((line) => clean(line))
            .filter(Boolean);

          const aboutComponent = document.querySelector(
            `[componentkey="JobDetails_AboutTheJob_${jobId}"]`
          );
          const aboutParagraphs = aboutComponent
            ? Array.from(aboutComponent.querySelectorAll('p'))
                .map((element) => clean(element.innerText || element.textContent || ''))
                .filter(Boolean)
            : [];
          const longestAboutParagraph = aboutParagraphs.reduce(
            (longest, value) => (value.length > longest.length ? value : longest),
            ''
          );
          const aboutTextFromComponent = aboutComponent
            ? longestAboutParagraph || clean(aboutComponent.innerText || aboutComponent.textContent || '')
            : '';

          const metadataFromSelectors = firstText([
            '.job-details-jobs-unified-top-card__primary-description-container',
            '.job-details-jobs-unified-top-card__primary-description',
            '.job-details-jobs-unified-top-card__tertiary-description-container',
          ]);
          const hasMetadataSignal = (value) =>
            /(\bago\b|\btoday\b|\byesterday\b|\breposted\b|\bjust now\b|\bposted on\b|\bapplicants?\b|\bclicked apply\b)/i.test(value);
          const metadataLine = metadataFromSelectors || lines.find(
            (line) =>
              line.includes('·') &&
              hasMetadataSignal(line)
          ) || '';
          const metadataSegments = metadataLine
            .split('·')
            .map((segment) => clean(segment))
            .filter(Boolean);
          const postedDateText = metadataSegments.find((segment) =>
            /(\bago\b|\btoday\b|\byesterday\b|\breposted\b|\bjust now\b|\bposted on\b)/i.test(segment)
          ) || '';

          const actionElement = Array.from(document.querySelectorAll('button, a')).find(
            (element) => {
              const textValue = clean(element.textContent || '');
              return textValue === 'Easy Apply' || textValue === 'Apply';
            }
          );
          const actionText = clean(actionElement ? actionElement.textContent : '');
          const easyApply = actionText.toLowerCase() === 'easy apply';
          const applied = /application submitted/i.test(mainText);

          let applyLink = '';
          if (!easyApply) {
            if (actionElement && actionElement.tagName === 'A') {
              applyLink = clean(actionElement.getAttribute('href') || '');
            }
            if (!applyLink) {
              applyLink = firstHref([
                'a.jobs-apply-button[href]',
                'a[data-control-name="jobdetails_topcard_inapply"]',
                'a[data-live-test-job-apply-link]',
                'a[href*="/redir/redirect/?url="]',
              ]);
            }
          }

          const aboutLineIndex = lines.findIndex((line) => /^about the job$/i.test(line));
          const aboutStopPattern = /^(set alert for similar jobs|see how you compare|exclusive job seeker insights|about the company|more jobs)$/i;
          const aboutLines = [];
          if (aboutLineIndex >= 0) {
            for (const line of lines.slice(aboutLineIndex + 1)) {
              if (aboutStopPattern.test(line)) {
                break;
              }
              aboutLines.push(line);
            }
          }
          const aboutTextFromLines = clean(aboutLines.join('\n'));
          const aboutTextFromSelectors = firstText([
            '.jobs-description-content__text',
            '#job-details',
            '.jobs-box__html-content',
            // Guest/logged-out page selectors
            '.show-more-less-html__markup',
            'section.description',
            '.core-section-container.description',
          ]);
          const aboutText =
            aboutTextFromComponent ||
            (aboutTextFromLines.length >= aboutTextFromSelectors.length
              ? aboutTextFromLines
              : aboutTextFromSelectors);

          return {
            currentUrl: window.location.href,
            companyName: firstText([
                '.job-details-jobs-unified-top-card__company-name a',
                '.job-details-jobs-unified-top-card__company-name',
              '.jobs-unified-top-card__company-name a',
              'a[href*="/company/"]',
            ]) || (lines.length > 0 ? lines[0] : ''),
            positionName: firstText([
              '.job-details-jobs-unified-top-card__job-title h1',
              '.job-details-jobs-unified-top-card__job-title',
              '.jobs-unified-top-card__job-title h1',
              'h1.t-24',
            ]) || (lines.length > 1 ? lines[1] : ''),
            jobLocation: metadataSegments.length > 0
              ? metadataSegments[0]
              : (lines.length > 2 ? lines[2].split('·')[0].trim() : ''),
            metadataSegments,
            lines,
            postedDateText,
            easyApply,
            applied,
            applyLink,
            aboutText,
            unavailableReason: unavailableMatch ? clean(unavailableMatch[0]) : '',
          };
        }
        """,
        job_id,
    )

    if not isinstance(payload, dict):
        raise ChromeRuntimeError(
            "Could not parse LinkedIn job details (unexpected page payload)."
        )
    return payload


def _extract_job_details_payload_with_retry(
    page: Any,
    job_id: str,
    job_url: str,
    *,
    parse_retry_base_delay_ms: int,
    parse_retry_max_delay_ms: int,
    parse_reload_retries: int,
) -> dict[str, Any]:
    payload = _extract_job_details_payload(page, job_id)
    if _payload_has_about_text(payload):
        return payload

    for attempt in range(parse_reload_retries):
        _raise_if_protection_page(page)
        page.reload(wait_until="domcontentloaded")
        _raise_if_protection_page(page)
        page.wait_for_timeout(
            _calculate_parse_retry_delay(
                attempt,
                base_delay_ms=parse_retry_base_delay_ms,
                max_delay_ms=parse_retry_max_delay_ms,
            )
        )
        _wait_for_job_details_page_ready(page)
        payload = _extract_job_details_payload(page, job_id)
        if _payload_has_about_text(payload):
            return payload

    return payload


def _normalize_job_details(
    payload: dict[str, Any],
    *,
    requested_job_id: str,
) -> LinkedInJobDetails:
    current_url = _payload_text(payload, "currentUrl")
    opened_job_id = _extract_job_id_from_url(current_url)
    if opened_job_id != requested_job_id:
        raise ChromeRuntimeError(
            f"Requested LinkedIn job '{requested_job_id}' but opened '{opened_job_id}'."
        )

    if _payload_is_unavailable(payload):
        return _build_unavailable_job_details(
            payload,
            requested_job_id=requested_job_id,
            current_url=current_url,
        )

    company_name = _payload_text(payload, "companyName")
    position_name = _payload_text(payload, "positionName")
    job_location = _payload_text(payload, "jobLocation")
    about_text = _normalize_about_text(_payload_text(payload, "aboutText"))
    if not about_text:
        raise ChromeRuntimeError(
            "Could not parse LinkedIn job details (missing aboutText)."
        )
    posted_date = _extract_posted_date(payload)

    easy_apply = payload.get("easyApply")
    if not isinstance(easy_apply, bool):
        raise ChromeRuntimeError(
            "Could not parse LinkedIn job details (missing easy-apply status)."
        )

    applied = payload.get("applied")
    normalized_applied = bool(applied) if isinstance(applied, bool) else False

    apply_link: str
    if easy_apply:
        apply_link = f"{JOB_VIEW_URL_PREFIX}{requested_job_id}"
    else:
        raw_apply_link = _payload_text(payload, "applyLink", required=False)
        if not raw_apply_link:
            if normalized_applied:
                apply_link = f"{JOB_VIEW_URL_PREFIX}{requested_job_id}"
            else:
                apply_link = f"{JOB_VIEW_URL_PREFIX}{requested_job_id}"
        else:
            apply_link = urljoin(current_url, raw_apply_link)

    return LinkedInJobDetails(
        linkedin_job_id=requested_job_id,
        company_name=company_name,
        position_name=position_name,
        job_location=job_location,
        posted_date=posted_date,
        easy_apply=easy_apply,
        apply_link=apply_link,
        about_text=about_text,
        applied=normalized_applied,
    )


def _payload_text(payload: dict[str, Any], key: str, *, required: bool = True) -> str:
    value = payload.get(key)
    if isinstance(value, str):
        normalized = " ".join(value.split())
    else:
        normalized = ""

    if required and not normalized:
        raise ChromeRuntimeError(
            f"Could not parse LinkedIn job details (missing {key})."
        )

    return normalized


def _payload_has_about_text(payload: dict[str, Any]) -> bool:
    value = payload.get("aboutText")
    return isinstance(value, str) and bool(value.strip())


def _payload_is_unavailable(payload: dict[str, Any]) -> bool:
    value = payload.get("unavailableReason")
    return isinstance(value, str) and bool(value.strip())


def _build_unavailable_job_details(
    payload: dict[str, Any],
    *,
    requested_job_id: str,
    current_url: str,
) -> LinkedInJobDetails:
    reason = _payload_text(payload, "unavailableReason")
    today = datetime.now(UTC).date().isoformat()
    return LinkedInJobDetails(
        linkedin_job_id=requested_job_id,
        company_name="Unknown",
        position_name="Unavailable LinkedIn job",
        job_location="Unknown",
        posted_date=today,
        easy_apply=False,
        apply_link=current_url,
        about_text=f"LinkedIn no longer exposes this posting: {reason}",
        applied=False,
    )


def _extract_posted_date(payload: dict[str, Any]) -> str:
    candidates = _collect_posted_date_candidates(payload)
    if not candidates:
        raise ChromeRuntimeError(
            "Could not parse LinkedIn job details (missing posted date)."
        )

    errors: list[str] = []
    for candidate in candidates:
        try:
            return _parse_posted_date(candidate)
        except ChromeRuntimeError as exc:
            errors.append(str(exc))

    raise ChromeRuntimeError(errors[-1])


def _collect_posted_date_candidates(payload: dict[str, Any]) -> list[str]:
    raw_candidates: list[str] = []
    raw_posted_date = payload.get("postedDateText")
    if isinstance(raw_posted_date, str):
        raw_candidates.append(raw_posted_date)

    for key in ("metadataSegments", "lines"):
        value = payload.get(key)
        if isinstance(value, list):
            raw_candidates.extend(item for item in value if isinstance(item, str))

    candidates: list[str] = []
    seen: set[str] = set()
    for value in raw_candidates:
        normalized = " ".join(value.split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
    return candidates


def _wait_with_human_delay(
    page: Any,
    *,
    min_delay_ms: int = HUMAN_ACTION_MIN_WAIT_MILLISECONDS,
    max_delay_ms: int = HUMAN_ACTION_MAX_WAIT_MILLISECONDS,
) -> None:
    page.wait_for_timeout(_choose_random_delay(min_delay_ms, max_delay_ms))


def _wait_for_search_results_page_ready(
    page: Any,
    *,
    timeout_ms: int = PAGE_READY_TIMEOUT_MILLISECONDS,
    poll_wait_ms: int = PAGE_READY_POLL_WAIT_MILLISECONDS,
) -> None:
    _wait_for_page_readiness(
        page,
        ready_check=_is_search_results_page_ready,
        timeout_ms=timeout_ms,
        poll_wait_ms=poll_wait_ms,
        description="LinkedIn jobs search page",
    )


def _wait_for_job_details_page_ready(
    page: Any,
    *,
    timeout_ms: int = PAGE_READY_TIMEOUT_MILLISECONDS,
    poll_wait_ms: int = PAGE_READY_POLL_WAIT_MILLISECONDS,
) -> None:
    _wait_for_page_readiness(
        page,
        ready_check=_is_job_details_page_ready,
        timeout_ms=timeout_ms,
        poll_wait_ms=poll_wait_ms,
        description="LinkedIn job page",
    )


def _wait_for_job_details_page_ready_with_retry(
    page: Any,
    *,
    timeout_ms: int = PAGE_READY_TIMEOUT_MILLISECONDS,
    poll_wait_ms: int = PAGE_READY_POLL_WAIT_MILLISECONDS,
    retries: int = 1,
) -> None:
    last_error: ChromeRuntimeError | None = None
    for attempt in range(retries + 1):
        try:
            _wait_for_job_details_page_ready(
                page,
                timeout_ms=timeout_ms,
                poll_wait_ms=poll_wait_ms,
            )
            return
        except ChromeRuntimeError as exc:
            last_error = exc
            if attempt >= retries:
                raise
            page.reload(wait_until="domcontentloaded")
    if last_error is not None:
        raise last_error


def _is_transient_navigation_error(exc: PlaywrightError) -> bool:
    return "Execution context was destroyed" in str(exc)


def _wait_for_page_readiness(
    page: Any,
    *,
    ready_check: Callable[[Any], bool],
    timeout_ms: int,
    poll_wait_ms: int,
    description: str,
) -> None:
    if timeout_ms < 1 or poll_wait_ms < 1:
        raise ValueError("page readiness waits must be positive integers")

    attempts = max(1, (timeout_ms + poll_wait_ms - 1) // poll_wait_ms)
    for attempt in range(attempts + 1):
        try:
            _raise_if_protection_page(page)
            if ready_check(page):
                return
        except PlaywrightError as exc:
            # LinkedIn sometimes redirects while Playwright is evaluating readiness.
            if not _is_transient_navigation_error(exc):
                raise
        if attempt >= attempts:
            break
        page.wait_for_timeout(poll_wait_ms)

    raise ChromeRuntimeError(
        f"{description} did not become ready within {timeout_ms} ms."
    )


def _is_search_results_page_ready(page: Any) -> bool:
    payload = page.evaluate(
        r"""
        () => {
          /* linkedin-results-ready */
          const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
          const bodyText = clean(document.body ? document.body.innerText || '' : '');
          const jobCount = Array.from(document.querySelectorAll('[data-job-id]'))
            .map((element) => clean(element.getAttribute('data-job-id')))
            .filter((value) => /^\d+$/.test(value)).length;

          return {
            readyState: document.readyState,
            jobCount,
            hasResultsScaffold: Boolean(
              document.querySelector('.jobs-search-results-list') ||
              document.querySelector('.jobs-search-results') ||
              document.querySelector('.scaffold-layout__list') ||
              document.querySelector('.jobs-search-two-pane__wrapper')
            ),
            hasEmptyState: /no matching jobs found|no jobs found|there are no jobs|try broadening your search/i.test(bodyText),
          };
        }
        """
    )
    if not isinstance(payload, dict):
        return False

    ready_state = str(payload.get("readyState") or "").lower()
    job_count = payload.get("jobCount")
    normalized_job_count = (
        job_count
        if isinstance(job_count, int) and not isinstance(job_count, bool)
        else 0
    )
    has_results_scaffold = bool(payload.get("hasResultsScaffold"))
    has_empty_state = bool(payload.get("hasEmptyState"))
    return (
        normalized_job_count > 0
        or has_empty_state
        or (has_results_scaffold and ready_state in {"interactive", "complete"})
    )


def _is_job_details_page_ready(page: Any) -> bool:
    payload = page.evaluate(
        r"""
        () => {
          /* linkedin-job-ready */
          const bodyText = String(document.body ? document.body.innerText || '' : '');
          const unavailableMatch = bodyText.match(
            /job id provided may not be valid or the job posting has been removed\.?/i
          );

          return {
            readyState: document.readyState,
            hasTitle: Boolean(
              document.querySelector('.job-details-jobs-unified-top-card__job-title h1') ||
              document.querySelector('.job-details-jobs-unified-top-card__job-title') ||
              document.querySelector('.jobs-unified-top-card__job-title h1') ||
              document.querySelector('h1.t-24')
            ),
            hasCompany: Boolean(
              document.querySelector('.job-details-jobs-unified-top-card__company-name a') ||
              document.querySelector('.job-details-jobs-unified-top-card__company-name') ||
              document.querySelector('.jobs-unified-top-card__company-name a') ||
              document.querySelector('a[href*="/company/"]')
            ),
            hasDescription: Boolean(
              document.querySelector('.jobs-description-content__text') ||
              document.querySelector('#job-details') ||
              document.querySelector('.jobs-box__html-content') ||
              document.querySelector('.show-more-less-html__markup') ||
              document.querySelector('section.description') ||
              document.querySelector('.core-section-container.description')
            ),
            hasApplyAction: /\bEasy Apply\b|\bApply\b/i.test(bodyText),
            hasUnavailableState: Boolean(unavailableMatch),
          };
        }
        """
    )
    if not isinstance(payload, dict):
        return False

    ready_state = str(payload.get("readyState") or "").lower()
    if ready_state not in {"interactive", "complete"}:
        return False
    if bool(payload.get("hasUnavailableState")):
        return True
    return any(
        bool(payload.get(key))
        for key in ("hasTitle", "hasCompany", "hasDescription", "hasApplyAction")
    )


def _wait_before_job_parse(page: Any, *, min_delay_ms: int, max_delay_ms: int) -> None:
    page.wait_for_timeout(_choose_parse_delay(min_delay_ms, max_delay_ms))


def _choose_parse_delay(min_delay_ms: int, max_delay_ms: int) -> int:
    return _choose_random_delay(min_delay_ms, max_delay_ms)


def _choose_random_delay(min_delay_ms: int, max_delay_ms: int) -> int:
    if min_delay_ms < 1 or max_delay_ms < 1:
        raise ValueError("delay bounds must be positive integers")
    if min_delay_ms > max_delay_ms:
        raise ValueError("minimum delay must be less than or equal to maximum")
    return random.randint(min_delay_ms, max_delay_ms)


def _calculate_parse_retry_delay(
    attempt: int,
    *,
    base_delay_ms: int,
    max_delay_ms: int,
) -> int:
    if attempt < 0:
        raise ValueError("retry attempt must be non-negative")
    if base_delay_ms < 1 or max_delay_ms < 1:
        raise ValueError("retry delay bounds must be positive integers")
    if base_delay_ms > max_delay_ms:
        raise ValueError("retry base delay must be less than or equal to maximum delay")
    return min(max_delay_ms, base_delay_ms * (2**attempt))


def _raise_if_protection_page(page: Any) -> None:
    inspection = _inspect_page_for_protection(page)
    if inspection is None:
        return

    current_url = inspection["current_url"]
    reason = inspection["reason"]
    state = inspection["state"]
    if state == "logged_out":
        raise ChromeRuntimeError(
            f"LinkedIn session appears logged out ('{reason}' at '{current_url}'). "
            "Log in again and rerun the workflow."
        )
    raise ChromeRuntimeError(
        "LinkedIn blocked automated access while parsing the job page "
        f"('{reason}' at '{current_url}')."
    )


def _inspect_page_for_protection(page: Any) -> dict[str, str] | None:
    payload = page.evaluate(
        r"""
        () => ({
          currentUrl: window.location.href,
          title: String(document.title || ''),
          bodyText: String(document.body ? document.body.innerText || '' : ''),
        })
        """
    )
    if not isinstance(payload, dict):
        return None

    current_url = str(payload.get("currentUrl") or "").strip()
    title = str(payload.get("title") or "").strip().lower()
    body_text = str(payload.get("bodyText") or "").strip().lower()
    combined_text = f"{title}\n{body_text}"

    url_indicators = (
        ("/checkpoint/", "protected", "checkpoint page"),
        ("/challenge/", "protected", "challenge page"),
        ("/uas/login", "logged_out", "login page"),
        ("/login", "logged_out", "login page"),
    )
    for needle, state, reason in url_indicators:
        if needle in current_url.lower():
            return {"current_url": current_url, "state": state, "reason": reason}

    text_indicators = (
        ("protected", "security verification", "security verification"),
        ("protected", "let's do a quick security check", "security check"),
        ("protected", "unusual activity", "unusual activity"),
        ("protected", "captcha", "captcha challenge"),
        ("logged_out", "sign in to linkedin", "login wall"),
        ("logged_out", "sign in to view more jobs", "login modal"),
        ("logged_out", "continue with google", "login modal"),
        ("logged_out", "sign in with email", "login modal"),
        ("logged_out", "join linkedin", "logged-out wall"),
    )
    for state, needle, reason in text_indicators:
        if needle in combined_text:
            return {
                "current_url": current_url or "unknown",
                "state": state,
                "reason": reason,
            }

    return None


def _normalize_about_text(text: str) -> str:
    value = text.strip()
    value = re.sub(r"^about the job\s*", "", value, flags=re.IGNORECASE)

    cutoff_markers = (
        "Can’t find top talent? Try Hiring Pro",
        "Can't find top talent? Try Hiring Pro",
        "About Accessibility Talent Solutions",
        "LinkedIn Corporation ©",
        "Questions? Visit our Help Center.",
        "Select language",
    )
    for marker in cutoff_markers:
        marker_index = value.find(marker)
        if marker_index == -1:
            continue
        value = value[:marker_index].strip()
        break

    value = re.sub(r"\s*…\s*more$", "", value, flags=re.IGNORECASE)
    return value.strip()


def _extract_job_id_from_url(url: str) -> str:
    match = JOB_VIEW_ID_PATTERN.search(url)
    if match is None:
        raise ChromeRuntimeError(f"LinkedIn job URL '{url}' does not contain a job id.")
    return match.group(1)


def _parse_posted_date(text: str) -> str:
    normalized = text.strip().lower()
    if not normalized:
        raise ChromeRuntimeError(
            "Could not parse LinkedIn job details (missing posted date)."
        )

    cleaned = normalized.replace("reposted", "").replace("posted", "").strip()
    today = datetime.now(UTC).date()

    if "today" in cleaned or "just now" in cleaned:
        return today.isoformat()
    if "yesterday" in cleaned:
        return (today - timedelta(days=1)).isoformat()

    unit_match = POSTED_DATE_UNIT_PATTERN.search(cleaned)
    if unit_match is not None:
        count = int(unit_match.group("count"))
        unit = unit_match.group("unit")
        days = _posted_unit_to_days(unit, count)
        return (today - timedelta(days=days)).isoformat()

    absolute_candidate = text.strip()
    absolute_candidate = re.sub(
        r"^posted\s+on\s+",
        "",
        absolute_candidate,
        flags=re.IGNORECASE,
    )
    embedded_match = POSTED_DATE_EMBEDDED_PATTERN.search(absolute_candidate)
    if embedded_match is not None:
        absolute_candidate = embedded_match.group(1)

    for absolute_format in POSTED_DATE_ABSOLUTE_FORMATS:
        try:
            parsed = datetime.strptime(absolute_candidate, absolute_format).date()
            return parsed.isoformat()
        except ValueError:
            continue

    raise ChromeRuntimeError(
        f"Could not parse LinkedIn posted date '{text}' into YYYY-MM-DD."
    )


def _posted_unit_to_days(unit: str, count: int) -> int:
    if unit.startswith(("min", "hr", "hour")):
        return 0
    if unit.startswith(("d", "day")):
        return count
    if unit.startswith(("w", "week")):
        return count * 7
    if unit.startswith(("mo", "month")):
        return count * 30
    if unit.startswith(("yr", "year")):
        return count * 365
    return count


def close_tracked_chrome_session(
    record: SessionRecord,
    *,
    shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
) -> None:
    """Terminate a tracked Chrome session process by PID."""
    if not is_process_alive(record.pid):
        return

    _signal_process(
        record.pid,
        signal.SIGTERM,
        session_name=record.sessionName,
    )
    if _wait_for_process_exit(record.pid, timeout=shutdown_timeout):
        return

    force_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
    _signal_process(
        record.pid,
        force_signal,
        session_name=record.sessionName,
    )
    if _wait_for_process_exit(record.pid, timeout=shutdown_timeout):
        return

    raise ChromeRuntimeError(
        f"Chrome session '{record.sessionName}' (pid {record.pid}) did not stop "
        "after termination signals."
    )


def is_process_alive(pid: int) -> bool:
    """Return whether the OS still reports the process as alive."""
    if pid <= 0:
        return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def fetch_cdp_version(
    cdp_http_endpoint: str,
    *,
    timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Load the Chrome DevTools version payload from the HTTP endpoint."""
    version_url = f"{cdp_http_endpoint.rstrip('/')}{DEVTOOLS_VERSION_PATH}"
    request = Request(version_url, headers={"Accept": "application/json"})

    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ChromeRuntimeError(
            f"Chrome DevTools endpoint '{version_url}' is unavailable "
            f"({_format_network_error(exc)})."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ChromeRuntimeError(
            f"Chrome DevTools endpoint '{version_url}' returned invalid JSON "
            f"({exc.msg})."
        ) from exc

    if not isinstance(payload, dict):
        raise ChromeRuntimeError(
            f"Chrome DevTools endpoint '{version_url}' returned an unexpected payload."
        )

    return payload


def _wait_for_devtools_version(
    process: subprocess.Popen[bytes],
    *,
    cdp_http_endpoint: str,
    chrome_profile_path: str,
    stderr_handle: Any,
    startup_timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + startup_timeout
    last_error: str | None = None

    while time.monotonic() < deadline:
        if process.poll() is not None:
            detail = _read_process_output(stderr_handle)
            detail_suffix = f" Chrome output: {detail}." if detail else ""
            raise ChromeRuntimeError(
                f"Google Chrome failed to start with profile "
                f"'{chrome_profile_path}'. Ensure the profile exists and is not "
                f"locked by another Chrome process.{detail_suffix}"
            )

        try:
            return fetch_cdp_version(cdp_http_endpoint)
        except ChromeRuntimeError as exc:
            last_error = str(exc)
            time.sleep(0.1)

    detail = _read_process_output(stderr_handle)
    detail_suffix = f" Chrome output: {detail}." if detail else ""
    error_suffix = f" Last probe error: {last_error}." if last_error else ""
    raise ChromeRuntimeError(
        f"Google Chrome did not expose DevTools at '{cdp_http_endpoint}' within "
        f"{startup_timeout:.1f}s for profile '{chrome_profile_path}'. Ensure the "
        "profile exists and is not locked by another Chrome process."
        f"{detail_suffix}{error_suffix}"
    )


def _playwright_worker_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"python -m {PLAYWRIGHT_WORKER_MODULE}",
        description="Internal Playwright worker for tracked Chrome sessions.",
    )
    parser.add_argument(PLAYWRIGHT_WORKER_FLAG, action="store_true")
    parser.add_argument("--user-data-dir", required=True)
    parser.add_argument("--profile-directory", required=True)
    parser.add_argument("--remote-debugging-port", type=int, required=True)
    parser.add_argument("--remote-debugging-host", required=True)
    parser.add_argument("--headless", action="store_true")
    return parser


def _run_playwright_worker_from_argv(argv: list[str] | None = None) -> int:
    parser = _playwright_worker_parser()
    args = parser.parse_args(argv)

    _ensure_node_no_deprecation_option()

    if not args.playwright_worker:
        parser.error(f"missing required flag: {PLAYWRIGHT_WORKER_FLAG}")

    shutdown_event = threading.Event()

    def _request_shutdown(_signum: int, _frame: Any) -> None:
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _request_shutdown)
        except (AttributeError, ValueError):
            continue

    launch_args = [
        f"--remote-debugging-port={args.remote_debugging_port}",
        f"--remote-debugging-address={args.remote_debugging_host}",
        f"--profile-directory={args.profile_directory}",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=args.user_data_dir,
                channel=PLAYWRIGHT_BROWSER_CHANNEL,
                headless=args.headless,
                args=launch_args,
            )
            try:
                while not shutdown_event.wait(0.2):
                    pass
            finally:
                context.close()
    except (OSError, PlaywrightError) as exc:
        lower_message = str(exc).lower()
        if "distribution 'chrome'" in lower_message and "not found" in lower_message:
            print(
                "Google Chrome executable was not found. Install Google Chrome in a standard location.",
                file=sys.stderr,
            )
            return 1
        print(
            f"Playwright failed to launch Google Chrome ({exc}).",
            file=sys.stderr,
        )
        return 1

    return 0


def _extract_ws_endpoint(
    *,
    cdp_http_endpoint: str,
    version_payload: dict[str, Any],
) -> str:
    ws_endpoint = version_payload.get("webSocketDebuggerUrl")
    if not isinstance(ws_endpoint, str) or not ws_endpoint.strip():
        raise ChromeRuntimeError(
            f"Chrome DevTools endpoint '{cdp_http_endpoint}' did not provide a "
            "browser WebSocket endpoint."
        )
    return ws_endpoint


def _reserve_cdp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((DEFAULT_CDP_HOST, 0))
        return int(sock.getsockname()[1])


def _wait_for_process_exit(pid: int, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_process_alive(pid):
            return True
        time.sleep(0.05)
    return not is_process_alive(pid)


def _wait_for_pids_exit(pids: list[int], *, timeout: float) -> list[int]:
    deadline = time.monotonic() + timeout
    remaining_pids = list(dict.fromkeys(pids))
    while remaining_pids and time.monotonic() < deadline:
        remaining_pids = [pid for pid in remaining_pids if is_process_alive(pid)]
        if not remaining_pids:
            return []
        time.sleep(0.05)
    return [pid for pid in remaining_pids if is_process_alive(pid)]


def _find_local_chrome_profile_pids(profile_paths: ChromeProfilePaths) -> list[int]:
    try:
        result = subprocess.run(
            ["ps", "ax", "-o", "pid=", "-o", "command="],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        error_detail = _format_os_error(exc) if isinstance(exc, OSError) else str(exc)
        raise ChromeRuntimeError(
            "Could not inspect local Chrome processes for profile "
            f"'{profile_paths.chrome_profile_path}' ({error_detail})."
        ) from exc

    current_pid = os.getpid()
    user_data_arg = f"--user-data-dir={profile_paths.user_data_dir}"
    profile_arg = f"--profile-directory={profile_paths.profile_directory}"
    matching_pids: list[int] = []
    for line in result.stdout.splitlines():
        stripped_line = line.strip()
        if not stripped_line:
            continue

        pid_text, _, command = stripped_line.partition(" ")
        if not command:
            continue

        try:
            pid = int(pid_text)
        except ValueError:
            continue

        if pid == current_pid:
            continue
        if user_data_arg not in command or profile_arg not in command:
            continue

        matching_pids.append(pid)

    return matching_pids


def _signal_process(pid: int, sig: int, *, session_name: str) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        raise ChromeRuntimeError(
            f"Chrome session '{session_name}' (pid {pid}) could not be stopped "
            f"({_format_os_error(exc)})."
        ) from exc
    except OSError as exc:
        raise ChromeRuntimeError(
            f"Chrome session '{session_name}' (pid {pid}) could not be stopped "
            f"({_format_os_error(exc)})."
        ) from exc


def _signal_profile_process(pid: int, sig: int, *, chrome_profile_path: str) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        raise ChromeRuntimeError(
            f"Chrome process for profile '{chrome_profile_path}' (pid {pid}) could "
            f"not be stopped ({_format_os_error(exc)})."
        ) from exc
    except OSError as exc:
        raise ChromeRuntimeError(
            f"Chrome process for profile '{chrome_profile_path}' (pid {pid}) could "
            f"not be stopped ({_format_os_error(exc)})."
        ) from exc


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _read_process_output(handle: Any) -> str:
    handle.seek(0)
    raw_output = handle.read()
    if isinstance(raw_output, bytes):
        decoded_output = raw_output.decode("utf-8", errors="replace")
    else:
        decoded_output = str(raw_output)
    return " ".join(decoded_output.split())


def _format_network_error(error: BaseException) -> str:
    reason = getattr(error, "reason", None)
    if reason:
        return str(reason)
    return str(error)


def _format_os_error(error: OSError) -> str:
    return error.strerror or str(error)


def _ensure_node_no_deprecation_option() -> None:
    current_options = os.environ.get("NODE_OPTIONS", "")
    if "--no-deprecation" in current_options:
        return

    if current_options:
        os.environ["NODE_OPTIONS"] = f"{current_options} --no-deprecation"
        return

    os.environ["NODE_OPTIONS"] = "--no-deprecation"


if __name__ == "__main__":
    raise SystemExit(_run_playwright_worker_from_argv())
