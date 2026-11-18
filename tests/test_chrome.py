from __future__ import annotations

import signal
from subprocess import CompletedProcess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

import job_search_cli.chrome as chrome
from job_search_cli.chrome import (
    ChromeLaunchResult,
    ChromeRuntimeError,
    build_google_chrome_launch_args,
    cleanup_chrome_processes_for_profile,
    close_tracked_chrome_session,
    get_job_ids_from_chrome_session,
    is_session_record_healthy,
    launch_google_chrome,
    open_url_in_chrome_session,
    parse_job_details_from_chrome_session,
    resolve_chrome_profile_paths,
)
from job_search_cli.sessions import SessionRecord


def _make_profile_dir(tmp_path: Path) -> Path:
    chrome_user_data_dir = (
        tmp_path / "Library" / "Application Support" / "Google" / "Chrome"
    )
    chrome_profile_dir = chrome_user_data_dir / "Profile 10"
    chrome_profile_dir.mkdir(parents=True)
    return chrome_profile_dir


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


def test_resolve_chrome_profile_paths_uses_parent_and_directory_name(
    tmp_path,
) -> None:
    chrome_profile_dir = _make_profile_dir(tmp_path)

    resolved = resolve_chrome_profile_paths(chrome_profile_dir)

    assert resolved.chrome_profile_path == str(chrome_profile_dir)
    assert resolved.user_data_dir == str(chrome_profile_dir.parent)
    assert resolved.profile_directory == "Profile 10"


def test_build_google_chrome_launch_args_only_adds_headless_flag_when_requested(
    tmp_path,
) -> None:
    chrome_profile_dir = _make_profile_dir(tmp_path)

    headless_args = build_google_chrome_launch_args(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        chrome_profile_path=chrome_profile_dir,
        headless=True,
        remote_debugging_port=9333,
    )
    headed_args = build_google_chrome_launch_args(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        chrome_profile_path=chrome_profile_dir,
        headless=False,
        remote_debugging_port=9444,
    )

    assert f"--user-data-dir={chrome_profile_dir.parent}" in headless_args
    assert "--profile-directory=Profile 10" in headless_args
    assert "--headless=new" in headless_args
    assert "--headless=new" not in headed_args


def test_is_session_record_healthy_short_circuits_dead_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _make_record()
    fetch_calls = {"count": 0}

    monkeypatch.setattr(chrome, "is_process_alive", lambda pid: False)

    def fake_fetch(*args: object, **kwargs: object) -> dict[str, str]:
        fetch_calls["count"] += 1
        return {"webSocketDebuggerUrl": record.wsEndpoint}

    monkeypatch.setattr(chrome, "fetch_cdp_version", fake_fetch)

    assert is_session_record_healthy(record) is False
    assert fetch_calls["count"] == 0


def test_is_session_record_healthy_requires_cdp_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _make_record()

    monkeypatch.setattr(chrome, "is_process_alive", lambda pid: True)
    monkeypatch.setattr(
        chrome,
        "fetch_cdp_version",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ChromeRuntimeError("endpoint unavailable")
        ),
    )

    assert is_session_record_healthy(record) is False


def test_is_session_record_healthy_accepts_live_process_and_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _make_record()

    monkeypatch.setattr(chrome, "is_process_alive", lambda pid: True)
    monkeypatch.setattr(
        chrome,
        "fetch_cdp_version",
        lambda *args, **kwargs: {"webSocketDebuggerUrl": record.wsEndpoint},
    )

    assert is_session_record_healthy(record) is True


def test_launch_google_chrome_returns_pid_and_endpoints(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chrome_profile_dir = _make_profile_dir(tmp_path)
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 43210

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            captured["terminated"] = True

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            captured["killed"] = True

    def fake_popen(
        args: tuple[str, ...],
        *,
        stdout: object,
        stderr: object,
        start_new_session: bool,
    ) -> FakeProcess:
        captured["args"] = args
        captured["stdout"] = stdout
        captured["stderr"] = stderr
        captured["start_new_session"] = start_new_session
        return FakeProcess()

    monkeypatch.setattr(chrome, "_reserve_cdp_port", lambda: 9333)
    monkeypatch.setattr(
        chrome, "find_google_chrome_executable", lambda: "/synthetic/google-chrome"
    )
    monkeypatch.setattr(chrome.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        chrome,
        "_wait_for_devtools_version",
        lambda process, **kwargs: {
            "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/browser/test"
        },
    )

    result = launch_google_chrome(
        {
            "chromeProfilePath": str(chrome_profile_dir),
            "headless": True,
        }
    )

    assert isinstance(result, ChromeLaunchResult)
    assert result.pid == 43210
    assert result.cdp_http_endpoint == "http://127.0.0.1:9333"
    assert result.ws_endpoint == "ws://127.0.0.1:9333/devtools/browser/test"
    assert "--headless=new" in result.launch_args
    assert (f"--user-data-dir={chrome_profile_dir.parent}") in result.launch_args
    assert "--profile-directory=Profile 10" in result.launch_args
    assert captured["args"] == result.launch_args
    assert result.launch_args[0] == "/synthetic/google-chrome"
    assert captured["start_new_session"] is True


def test_cleanup_chrome_processes_for_profile_stops_only_matching_pids(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chrome_profile_dir = _make_profile_dir(tmp_path)
    ps_output = "\n".join(
        [
            "111 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/tmp/other --profile-directory=Profile 10",
            (
                "222 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                f"--user-data-dir={chrome_profile_dir.parent} --profile-directory=Profile 10"
            ),
            (
                "333 /Library/Frameworks/Python.framework/Versions/3.11/Resources/Python.app/Contents/MacOS/Python "
                f"-m job_search_cli.chrome --playwright-worker --user-data-dir={chrome_profile_dir.parent} --profile-directory=Profile 10"
            ),
        ]
    )
    monkeypatch.setattr(
        chrome.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0, stdout=ps_output),
    )
    monkeypatch.setattr(chrome.os, "getpid", lambda: 999)

    signaled: list[tuple[int, int]] = []
    alive_state = {222: True, 333: True}

    def fake_signal_profile_process(
        pid: int,
        sig: int,
        *,
        chrome_profile_path: str,
    ) -> None:
        signaled.append((pid, sig))
        alive_state[pid] = False

    monkeypatch.setattr(chrome, "_signal_profile_process", fake_signal_profile_process)
    monkeypatch.setattr(
        chrome,
        "is_process_alive",
        lambda pid: alive_state.get(pid, False),
    )

    cleanup_chrome_processes_for_profile(chrome_profile_dir)

    assert signaled == [
        (222, signal.SIGTERM),
        (333, signal.SIGTERM),
    ]


def test_playwright_worker_uses_selected_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeEvent:
        def set(self) -> None:
            captured["set_called"] = True

        def wait(self, timeout: float) -> bool:
            captured["wait_timeout"] = timeout
            return True

    class FakeContext:
        def close(self) -> None:
            captured["context_closed"] = True

    class FakeChromium:
        def launch_persistent_context(self, **kwargs: object) -> FakeContext:
            captured["launch_kwargs"] = kwargs
            return FakeContext()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(chrome.threading, "Event", lambda: FakeEvent())
    monkeypatch.setattr(chrome.signal, "signal", lambda sig, handler: None)
    monkeypatch.setattr(chrome, "sync_playwright", lambda: FakeManager())
    monkeypatch.setattr(chrome, "find_google_chrome_executable", lambda: "/test/helium")

    exit_code = chrome._run_playwright_worker_from_argv(
        [
            chrome.PLAYWRIGHT_WORKER_FLAG,
            "--user-data-dir=/tmp/chrome-user-data",
            "--profile-directory=Profile 10",
            "--remote-debugging-port=9333",
            "--remote-debugging-host=127.0.0.1",
            "--headless",
        ]
    )

    assert exit_code == 0
    assert captured["wait_timeout"] == 0.2
    assert captured["context_closed"] is True
    launch_kwargs = captured["launch_kwargs"]
    assert isinstance(launch_kwargs, dict)
    assert launch_kwargs["user_data_dir"] == "/tmp/chrome-user-data"
    assert launch_kwargs["executable_path"] == "/test/helium"
    assert "channel" not in launch_kwargs
    assert launch_kwargs["headless"] is True
    args = launch_kwargs["args"]
    assert isinstance(args, list)
    assert "--profile-directory=Profile 10" in args
    assert "--remote-debugging-port=9333" in args
    assert "--remote-debugging-address=127.0.0.1" in args


@pytest.mark.parametrize("manual_login", [False, True])
def test_open_url_in_chrome_session_opens_new_tab_in_default_context(
    monkeypatch: pytest.MonkeyPatch,
    manual_login: bool,
) -> None:
    captured: dict[str, object] = {}

    class FakePage:
        def goto(
            self, url: str, *, wait_until: str, timeout: int | None = None
        ) -> None:
            captured["url"] = url
            captured["wait_until"] = wait_until

        def bring_to_front(self) -> None:
            captured["brought_to_front"] = True

        def wait_for_timeout(self, timeout_ms: int) -> None:
            captured["wait_timeout"] = timeout_ms

        def evaluate(
            self,
            expression: str,
            argument: object | None = None,
        ) -> dict[str, str]:
            assert argument is None
            return {
                "currentUrl": "https://www.linkedin.com/jobs/search/?currentJobId=1",
                "title": "LinkedIn Jobs",
                "bodyText": "Job results",
            }

    class FakeContext:
        def new_page(self) -> FakePage:
            captured["new_page_called"] = True
            return FakePage()

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, *, no_defaults: bool) -> FakeBrowser:
            assert no_defaults is True
            captured["endpoint"] = endpoint
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            captured["entered"] = True
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> None:
            captured["exited"] = True

    monkeypatch.setattr(chrome, "sync_playwright", lambda: FakeManager())
    if manual_login:
        monkeypatch.setattr(
            chrome,
            "_raise_if_protection_page",
            lambda page: pytest.fail("Manual browser opening must allow login"),
        )

    open_url_in_chrome_session(
        "http://127.0.0.1:9222",
        "https://www.linkedin.com/jobs/search/?currentJobId=1",
        manual_login=manual_login,
    )

    assert captured["endpoint"] == "http://127.0.0.1:9222"
    assert captured["new_page_called"] is True
    assert captured["url"] == "https://www.linkedin.com/jobs/search/?currentJobId=1"
    assert captured["wait_until"] == "domcontentloaded"
    assert captured["brought_to_front"] is True
    assert captured["entered"] is True
    assert captured["exited"] is True


def test_exited_child_is_not_reported_alive():
    import subprocess
    import time

    process = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
    try:
        deadline = time.monotonic() + 2
        while chrome.is_process_alive(process.pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not chrome.is_process_alive(process.pid)
    finally:
        process.wait()


def test_open_url_in_chrome_session_fails_when_linkedin_session_is_logged_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePage:
        def goto(
            self, url: str, *, wait_until: str, timeout: int | None = None
        ) -> None:
            return None

        def bring_to_front(self) -> None:
            return None

        def wait_for_timeout(self, timeout_ms: int) -> None:
            return None

        def evaluate(
            self,
            expression: str,
            argument: object | None = None,
        ) -> dict[str, str]:
            assert argument is None
            return {
                "currentUrl": "https://www.linkedin.com/jobs/search/?start=0",
                "title": "LinkedIn Jobs, Employment",
                "bodyText": (
                    "Sign in to view more jobs Continue with Google Sign in with Email"
                ),
            }

    class FakeContext:
        def new_page(self) -> FakePage:
            return FakePage()

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, *, no_defaults: bool) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(chrome, "sync_playwright", lambda: FakeManager())

    with pytest.raises(ChromeRuntimeError) as exc_info:
        open_url_in_chrome_session(
            "http://127.0.0.1:9222",
            "https://www.linkedin.com/jobs/search/?currentJobId=1",
        )

    assert "LinkedIn session appears logged out" in str(exc_info.value)


def test_get_job_ids_from_chrome_session_returns_unique_trimmed_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakePage:
        def wait_for_timeout(self, timeout_ms: int) -> None:
            timeouts = captured.get("timeouts")
            if timeouts is None:
                timeouts = []
                captured["timeouts"] = timeouts
            cast(list[int], timeouts).append(timeout_ms)

        def evaluate(
            self,
            expression: str,
            argument: object | None = None,
        ) -> dict[str, str] | dict[str, object]:
            assert argument is None
            if "linkedin-results-ready" in expression:
                return {
                    "readyState": "complete",
                    "jobCount": 3,
                    "hasResultsScaffold": True,
                    "hasEmptyState": False,
                }
            return {
                "currentUrl": "https://www.linkedin.com/jobs/search/?currentJobId=1",
                "title": "LinkedIn Jobs",
                "bodyText": "Job results",
            }

        def eval_on_selector_all(self, selector: str, expression: str) -> list[object]:
            captured["selector"] = selector
            captured["expression"] = expression
            return [
                "101",
                "101",
                " 102 ",
                None,
                "",
                "search",
                "103",
                "102",
            ]

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, *, no_defaults: bool) -> FakeBrowser:
            captured["endpoint"] = endpoint
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(chrome, "sync_playwright", lambda: FakeManager())

    job_ids = get_job_ids_from_chrome_session("http://127.0.0.1:9222")

    assert job_ids == ["101", "102", "103"]
    assert captured["endpoint"] == "http://127.0.0.1:9222"
    assert captured["selector"] == chrome.JOB_ID_SELECTOR
    assert "data-job-id" in str(captured["expression"])


def test_get_job_ids_from_chrome_session_waits_for_results_page_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeouts: list[int] = []

    class FakePage:
        def __init__(self) -> None:
            self._ready_checks = 0

        def wait_for_timeout(self, timeout_ms: int) -> None:
            timeouts.append(timeout_ms)

        def evaluate(
            self,
            expression: str,
            argument: object | None = None,
        ) -> dict[str, object] | dict[str, str]:
            assert argument is None
            if "linkedin-results-ready" in expression:
                self._ready_checks += 1
                if self._ready_checks == 1:
                    return {
                        "readyState": "loading",
                        "jobCount": 0,
                        "hasResultsScaffold": False,
                        "hasEmptyState": False,
                    }
                return {
                    "readyState": "complete",
                    "jobCount": 2,
                    "hasResultsScaffold": True,
                    "hasEmptyState": False,
                }
            return {
                "currentUrl": "https://www.linkedin.com/jobs/search/?currentJobId=1",
                "title": "LinkedIn Jobs",
                "bodyText": "Job results",
            }

        def eval_on_selector_all(self, selector: str, expression: str) -> list[str]:
            return ["101", "102"]

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, *, no_defaults: bool) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(chrome.random, "randint", lambda start, end: start)
    monkeypatch.setattr(chrome, "sync_playwright", lambda: FakeManager())

    job_ids = get_job_ids_from_chrome_session("http://127.0.0.1:9222")

    assert job_ids == ["101", "102"]
    assert timeouts == [
        chrome.HUMAN_ACTION_MIN_WAIT_MILLISECONDS,
        chrome.PAGE_READY_POLL_WAIT_MILLISECONDS,
    ]


def test_get_job_ids_from_chrome_session_collects_across_paginated_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    page_urls = [
        "https://www.linkedin.com/jobs/search/?start=0",
        "https://www.linkedin.com/jobs/search/?start=25",
        "https://www.linkedin.com/jobs/search/?start=50",
    ]
    page_snapshots = {
        page_urls[0]: [["1", "2", "2"], ["1", "2", "3"]],
        page_urls[1]: [["3", "4"], ["3", "4", "5"]],
        page_urls[2]: [["5", "6", " 7 "]],
    }

    class FakePage:
        def __init__(self) -> None:
            self._url = page_urls[0]
            self._snapshot_index = 0

        def goto(
            self, url: str, *, wait_until: str, timeout: int | None = None
        ) -> None:
            captured.setdefault("visited", []).append(url)
            captured.setdefault("wait_until", []).append(wait_until)
            self._url = url
            self._snapshot_index = 0

        def bring_to_front(self) -> None:
            captured["brought_to_front"] = True

        def wait_for_timeout(self, timeout_ms: int) -> None:
            timeouts = captured.setdefault("timeouts", [])
            assert isinstance(timeouts, list)
            timeouts.append(timeout_ms)

        def eval_on_selector_all(self, selector: str, expression: str) -> list[str]:
            captured["selector"] = selector
            captured["expression"] = expression
            snapshots = page_snapshots[self._url]
            return snapshots[self._snapshot_index]

        def evaluate(self, expression: str) -> dict[str, object] | bool:
            if "linkedin-results-ready" in expression:
                snapshots = page_snapshots[self._url]
                return {
                    "readyState": "complete",
                    "jobCount": len(snapshots[self._snapshot_index]),
                    "hasResultsScaffold": True,
                    "hasEmptyState": False,
                }
            if "scrollTop" in expression:
                captured["scroll_expression"] = expression
                snapshots = page_snapshots[self._url]
                if self._snapshot_index < len(snapshots) - 1:
                    self._snapshot_index += 1
                    return True
                return False
            return {
                "currentUrl": self._url,
                "title": "LinkedIn Jobs",
                "bodyText": "Job results",
            }

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, *, no_defaults: bool) -> FakeBrowser:
            captured["endpoint"] = endpoint
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(chrome, "sync_playwright", lambda: FakeManager())

    job_ids = get_job_ids_from_chrome_session(
        "http://127.0.0.1:9222",
        page_urls=page_urls,
        results_per_page=25,
    )

    assert job_ids == ["1", "2", "3", "4", "5", "6", "7"]
    assert captured["endpoint"] == "http://127.0.0.1:9222"
    assert captured["visited"] == page_urls
    assert captured["selector"] == chrome.JOB_ID_SELECTOR
    assert "data-job-id" in str(captured["expression"])


def test_get_job_ids_from_chrome_session_stops_when_page_has_no_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    page_urls = [
        "https://www.linkedin.com/jobs/search/?start=0",
        "https://www.linkedin.com/jobs/search/?start=25",
        "https://www.linkedin.com/jobs/search/?start=50",
    ]
    page_snapshots = {
        page_urls[0]: [["1", "2"]],
        page_urls[1]: [[]],
        page_urls[2]: [["99"]],
    }

    class FakePage:
        def __init__(self) -> None:
            self._url = page_urls[0]

        def goto(
            self, url: str, *, wait_until: str, timeout: int | None = None
        ) -> None:
            captured.setdefault("visited", []).append(url)
            captured.setdefault("wait_until", []).append(wait_until)
            self._url = url

        def bring_to_front(self) -> None:
            captured["brought_to_front"] = True

        def wait_for_timeout(self, timeout_ms: int) -> None:
            existing_timeouts = captured.get("timeouts")
            if existing_timeouts is None:
                existing_timeouts = []
                captured["timeouts"] = existing_timeouts
            cast(list[int], existing_timeouts).append(timeout_ms)

        def eval_on_selector_all(self, selector: str, expression: str) -> list[str]:
            captured["selector"] = selector
            captured["expression"] = expression
            return page_snapshots[self._url][0]

        def evaluate(self, expression: str) -> dict[str, object] | bool:
            if "linkedin-results-ready" in expression:
                current_snapshot = page_snapshots[self._url][0]
                return {
                    "readyState": "complete",
                    "jobCount": len(current_snapshot),
                    "hasResultsScaffold": True,
                    "hasEmptyState": not bool(current_snapshot),
                }
            if "scrollTop" in expression:
                captured["scroll_expression"] = expression
                return False
            return {
                "currentUrl": self._url,
                "title": "LinkedIn Jobs",
                "bodyText": "Job results",
            }

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, *, no_defaults: bool) -> FakeBrowser:
            captured["endpoint"] = endpoint
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(chrome, "sync_playwright", lambda: FakeManager())

    job_ids = get_job_ids_from_chrome_session(
        "http://127.0.0.1:9222",
        page_urls=page_urls,
        results_per_page=25,
    )

    assert job_ids == ["1", "2"]
    assert captured["endpoint"] == "http://127.0.0.1:9222"
    assert captured["visited"] == [
        "https://www.linkedin.com/jobs/search/?start=0",
        "https://www.linkedin.com/jobs/search/?start=25",
    ]


def test_get_job_ids_from_chrome_session_fails_when_login_modal_is_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page_urls = ["https://www.linkedin.com/jobs/search/?start=0"]

    class FakePage:
        def goto(
            self, url: str, *, wait_until: str, timeout: int | None = None
        ) -> None:
            return None

        def bring_to_front(self) -> None:
            return None

        def wait_for_timeout(self, timeout_ms: int) -> None:
            return None

        def evaluate(
            self,
            expression: str,
            argument: object | None = None,
        ) -> dict[str, str]:
            assert argument is None
            return {
                "currentUrl": "https://www.linkedin.com/jobs/search/?start=0",
                "title": "LinkedIn Jobs, Employment",
                "bodyText": "Sign in to view more jobs Continue with Google",
            }

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, *, no_defaults: bool) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(chrome, "sync_playwright", lambda: FakeManager())

    with pytest.raises(ChromeRuntimeError) as exc_info:
        get_job_ids_from_chrome_session(
            "http://127.0.0.1:9222",
            page_urls=page_urls,
            results_per_page=25,
        )

    assert "LinkedIn session appears logged out" in str(exc_info.value)


def test_parse_job_details_from_chrome_session_extracts_and_normalizes_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "FixedDateTime":
            return cls(2026, 3, 7, tzinfo=tz)

    class FakePage:
        def goto(
            self, url: str, *, wait_until: str, timeout: int | None = None
        ) -> None:
            captured["url"] = url
            captured["wait_until"] = wait_until

        def bring_to_front(self) -> None:
            captured["brought_to_front"] = True

        def wait_for_timeout(self, timeout_ms: int) -> None:
            existing_timeouts = captured.get("timeouts")
            if existing_timeouts is None:
                existing_timeouts = []
                captured["timeouts"] = existing_timeouts
            cast(list[int], existing_timeouts).append(timeout_ms)

        def evaluate(
            self,
            expression: str,
            argument: object | None = None,
        ) -> dict[str, object]:
            if argument is None and "linkedin-job-ready" in expression:
                return {
                    "readyState": "complete",
                    "hasTitle": True,
                    "hasCompany": True,
                    "hasDescription": True,
                    "hasApplyAction": True,
                }
            if argument is None:
                return {
                    "currentUrl": "https://www.linkedin.com/jobs/view/101/?foo=1",
                    "title": "LinkedIn",
                    "bodyText": "QA Engineer - AI & Advanced Testing",
                }
            captured["expression"] = expression
            captured["argument"] = argument
            return {
                "currentUrl": "https://www.linkedin.com/jobs/view/101/?foo=1",
                "companyName": "Example Company",
                "positionName": "QA Engineer - AI & Advanced Testing",
                "jobLocation": "Seville, Andalusia, Spain",
                "postedDateText": "1 month ago",
                "easyApply": False,
                "applyLink": "https://example.com/apply/101",
                "aboutText": "About the job full text",
            }

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, *, no_defaults: bool) -> FakeBrowser:
            captured["endpoint"] = endpoint
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(chrome, "datetime", FixedDateTime)
    monkeypatch.setattr(chrome.random, "randint", lambda start, end: end)
    monkeypatch.setattr(chrome, "sync_playwright", lambda: FakeManager())

    details = parse_job_details_from_chrome_session(
        "http://127.0.0.1:9222",
        "101",
    )

    assert details.linkedin_job_id == "101"
    assert details.company_name == "Example Company"
    assert details.position_name == "QA Engineer - AI & Advanced Testing"
    assert details.job_location == "Seville, Andalusia, Spain"
    assert (
        details.posted_date
        == (datetime(2026, 3, 7, tzinfo=UTC) - timedelta(days=30)).date().isoformat()
    )
    assert details.easy_apply is False
    assert details.apply_link == "https://example.com/apply/101"
    assert details.about_text == "full text"
    assert captured["endpoint"] == "http://127.0.0.1:9222"
    assert captured["url"] == "https://www.linkedin.com/jobs/view/101"
    assert captured["wait_until"] == "domcontentloaded"
    assert captured["argument"] == "101"
    assert captured["timeouts"] == [
        chrome.HUMAN_ACTION_MAX_WAIT_MILLISECONDS,
        chrome.JOB_PARSE_MAX_DELAY_MILLISECONDS,
        chrome.JOB_PARSE_WAIT_MILLISECONDS,
    ]


def test_parse_job_details_from_chrome_session_waits_for_job_page_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeouts: list[int] = []

    class FakePage:
        def __init__(self) -> None:
            self._ready_checks = 0

        def goto(
            self, url: str, *, wait_until: str, timeout: int | None = None
        ) -> None:
            return None

        def bring_to_front(self) -> None:
            return None

        def wait_for_timeout(self, timeout_ms: int) -> None:
            timeouts.append(timeout_ms)

        def evaluate(
            self,
            expression: str,
            argument: object | None = None,
        ) -> dict[str, object]:
            if argument is None and "linkedin-job-ready" in expression:
                self._ready_checks += 1
                if self._ready_checks == 1:
                    return {
                        "readyState": "loading",
                        "hasTitle": False,
                        "hasCompany": False,
                        "hasDescription": False,
                        "hasApplyAction": False,
                    }
                return {
                    "readyState": "complete",
                    "hasTitle": True,
                    "hasCompany": True,
                    "hasDescription": True,
                    "hasApplyAction": True,
                }
            if argument is None:
                return {
                    "currentUrl": "https://www.linkedin.com/jobs/view/101/",
                    "title": "LinkedIn",
                    "bodyText": "QA Engineer",
                }
            return {
                "currentUrl": "https://www.linkedin.com/jobs/view/101/",
                "companyName": "Example Company",
                "positionName": "QA Engineer",
                "jobLocation": "Barcelona, Catalonia, Spain",
                "postedDateText": "1 day ago",
                "easyApply": False,
                "applyLink": "https://example.com/apply/101",
                "aboutText": "About text",
            }

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, *, no_defaults: bool) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(chrome.random, "randint", lambda start, end: start)
    monkeypatch.setattr(chrome, "sync_playwright", lambda: FakeManager())

    details = parse_job_details_from_chrome_session(
        "http://127.0.0.1:9222",
        "101",
    )

    assert details.linkedin_job_id == "101"
    assert timeouts == [
        chrome.HUMAN_ACTION_MIN_WAIT_MILLISECONDS,
        chrome.PAGE_READY_POLL_WAIT_MILLISECONDS,
        chrome.JOB_PARSE_MIN_DELAY_MILLISECONDS,
        chrome.JOB_PARSE_WAIT_MILLISECONDS,
    ]


def test_parse_job_details_from_chrome_session_retries_page_ready_after_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reloaded: list[str] = []

    class FakePage:
        def __init__(self) -> None:
            self._ready_checks = 0

        def goto(
            self, url: str, *, wait_until: str, timeout: int | None = None
        ) -> None:
            return None

        def bring_to_front(self) -> None:
            return None

        def wait_for_timeout(self, timeout_ms: int) -> None:
            return None

        def reload(self, *, wait_until: str) -> None:
            reloaded.append(wait_until)

        def evaluate(
            self,
            expression: str,
            argument: object | None = None,
        ) -> dict[str, object]:
            if argument is None and "linkedin-job-ready" in expression:
                self._ready_checks += 1
                if self._ready_checks == 1:
                    return {
                        "readyState": "loading",
                        "hasTitle": False,
                        "hasCompany": False,
                        "hasDescription": False,
                        "hasApplyAction": False,
                    }
                return {
                    "readyState": "complete",
                    "hasTitle": True,
                    "hasCompany": True,
                    "hasDescription": True,
                    "hasApplyAction": True,
                }
            if argument is None:
                return {
                    "currentUrl": "https://www.linkedin.com/jobs/view/101/",
                    "title": "LinkedIn",
                    "bodyText": "QA Engineer",
                }
            return {
                "currentUrl": "https://www.linkedin.com/jobs/view/101/",
                "companyName": "Example Company",
                "positionName": "QA Engineer",
                "jobLocation": "Barcelona, Catalonia, Spain",
                "postedDateText": "1 day ago",
                "easyApply": False,
                "applyLink": "https://example.com/apply/101",
                "aboutText": "About text",
            }

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, *, no_defaults: bool) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(chrome.random, "randint", lambda start, end: start)
    monkeypatch.setattr(chrome, "sync_playwright", lambda: FakeManager())
    monkeypatch.setattr(
        chrome,
        "_wait_for_job_details_page_ready",
        lambda page, timeout_ms=chrome.PAGE_READY_TIMEOUT_MILLISECONDS, poll_wait_ms=chrome.PAGE_READY_POLL_WAIT_MILLISECONDS: (
            (_ for _ in ()).throw(
                chrome.ChromeRuntimeError(
                    "LinkedIn job page did not become ready within 10000 ms."
                )
            )
            if not reloaded
            else None
        ),
    )

    details = parse_job_details_from_chrome_session(
        "http://127.0.0.1:9222",
        "101",
    )

    assert details.linkedin_job_id == "101"
    assert reloaded == ["domcontentloaded"]


def test_parse_job_details_from_chrome_session_sets_job_url_for_easy_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chrome.random, "randint", lambda start, end: start)

    class FakePage:
        def goto(
            self, url: str, *, wait_until: str, timeout: int | None = None
        ) -> None:
            return None

        def bring_to_front(self) -> None:
            return None

        def wait_for_timeout(self, timeout_ms: int) -> None:
            return None

        def evaluate(
            self,
            expression: str,
            argument: object | None = None,
        ) -> dict[str, object]:
            if argument is None and "linkedin-job-ready" in expression:
                return {
                    "readyState": "complete",
                    "hasTitle": True,
                    "hasCompany": True,
                    "hasDescription": True,
                    "hasApplyAction": True,
                }
            if argument is None:
                return {
                    "currentUrl": "https://www.linkedin.com/jobs/view/102/",
                    "title": "LinkedIn",
                    "bodyText": "QA Tester",
                }
            return {
                "currentUrl": "https://www.linkedin.com/jobs/view/102/",
                "companyName": "Example Company",
                "positionName": "QA Tester",
                "jobLocation": "Spain",
                "postedDateText": "1 day ago",
                "easyApply": True,
                "applyLink": "",
                "aboutText": "About text",
            }

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, *, no_defaults: bool) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(chrome, "sync_playwright", lambda: FakeManager())

    details = parse_job_details_from_chrome_session(
        "http://127.0.0.1:9222",
        "102",
    )

    assert details.easy_apply is True
    assert details.apply_link == "https://www.linkedin.com/jobs/view/102"


def test_parse_job_details_from_chrome_session_retries_until_about_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeouts: list[int] = []
    monkeypatch.setattr(chrome.random, "randint", lambda start, end: start)

    class FakePage:
        def __init__(self) -> None:
            self._evaluate_calls = 0

        def goto(
            self, url: str, *, wait_until: str, timeout: int | None = None
        ) -> None:
            return None

        def bring_to_front(self) -> None:
            return None

        def wait_for_timeout(self, timeout_ms: int) -> None:
            timeouts.append(timeout_ms)

        def reload(self, *, wait_until: str) -> None:
            return None

        def evaluate(
            self,
            expression: str,
            argument: object | None = None,
        ) -> dict[str, object]:
            if argument is None and "linkedin-job-ready" in expression:
                return {
                    "readyState": "complete",
                    "hasTitle": True,
                    "hasCompany": True,
                    "hasDescription": True,
                    "hasApplyAction": True,
                }
            if argument is None:
                return {
                    "currentUrl": "https://www.linkedin.com/jobs/view/102/",
                    "title": "LinkedIn",
                    "bodyText": "QA Tester",
                }
            self._evaluate_calls += 1
            about_text = "" if self._evaluate_calls == 1 else "About text"
            return {
                "currentUrl": "https://www.linkedin.com/jobs/view/102/",
                "companyName": "Example Company",
                "positionName": "QA Tester",
                "jobLocation": "Spain",
                "postedDateText": "1 day ago",
                "easyApply": True,
                "applyLink": "",
                "aboutText": about_text,
            }

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, *, no_defaults: bool) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(chrome, "sync_playwright", lambda: FakeManager())

    details = parse_job_details_from_chrome_session(
        "http://127.0.0.1:9222",
        "102",
    )

    assert details.about_text == "About text"
    assert timeouts == [
        chrome.HUMAN_ACTION_MIN_WAIT_MILLISECONDS,
        chrome.JOB_PARSE_MIN_DELAY_MILLISECONDS,
        chrome.JOB_PARSE_WAIT_MILLISECONDS,
        chrome.JOB_PARSE_RETRY_BASE_DELAY_MILLISECONDS,
    ]


def test_parse_job_details_marks_applied_when_submitted_without_apply_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chrome.random, "randint", lambda start, end: start)

    class FakePage:
        def goto(
            self, url: str, *, wait_until: str, timeout: int | None = None
        ) -> None:
            return None

        def bring_to_front(self) -> None:
            return None

        def wait_for_timeout(self, timeout_ms: int) -> None:
            return None

        def evaluate(
            self,
            expression: str,
            argument: object | None = None,
        ) -> dict[str, object]:
            if argument is None and "linkedin-job-ready" in expression:
                return {
                    "readyState": "complete",
                    "hasTitle": True,
                    "hasCompany": True,
                    "hasDescription": True,
                    "hasApplyAction": True,
                }
            if argument is None:
                return {
                    "currentUrl": "https://www.linkedin.com/jobs/view/103/",
                    "title": "LinkedIn",
                    "bodyText": "Software Tester",
                }
            return {
                "currentUrl": "https://www.linkedin.com/jobs/view/103/",
                "companyName": "Example Company",
                "positionName": "Software Tester",
                "jobLocation": "European Economic Area",
                "postedDateText": "2 days ago",
                "easyApply": False,
                "applied": True,
                "applyLink": "",
                "aboutText": "About text",
            }

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, *, no_defaults: bool) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(chrome, "sync_playwright", lambda: FakeManager())

    details = parse_job_details_from_chrome_session(
        "http://127.0.0.1:9222",
        "103",
    )

    assert details.applied is True
    assert details.apply_link == "https://www.linkedin.com/jobs/view/103"


def test_parse_job_details_from_chrome_session_fails_when_opened_job_mismatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chrome.random, "randint", lambda start, end: start)

    class FakePage:
        def goto(
            self, url: str, *, wait_until: str, timeout: int | None = None
        ) -> None:
            return None

        def bring_to_front(self) -> None:
            return None

        def wait_for_timeout(self, timeout_ms: int) -> None:
            return None

        def evaluate(
            self,
            expression: str,
            argument: object | None = None,
        ) -> dict[str, object]:
            if argument is None and "linkedin-job-ready" in expression:
                return {
                    "readyState": "complete",
                    "hasTitle": True,
                    "hasCompany": True,
                    "hasDescription": True,
                    "hasApplyAction": True,
                }
            if argument is None:
                return {
                    "currentUrl": "https://www.linkedin.com/jobs/view/9999999999/",
                    "title": "LinkedIn",
                    "bodyText": "Position",
                }
            return {
                "currentUrl": "https://www.linkedin.com/jobs/view/9999999999/",
                "companyName": "Company",
                "positionName": "Position",
                "jobLocation": "Barcelona",
                "postedDateText": "1 day ago",
                "easyApply": True,
                "applyLink": "",
                "aboutText": "About",
            }

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, *, no_defaults: bool) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(chrome, "sync_playwright", lambda: FakeManager())

    with pytest.raises(ChromeRuntimeError) as exc_info:
        parse_job_details_from_chrome_session("http://127.0.0.1:9222", "101")

    assert "Requested LinkedIn job '101' but opened '9999999999'." in str(
        exc_info.value
    )


def test_parse_job_details_from_chrome_session_stops_on_protection_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeouts: list[int] = []
    monkeypatch.setattr(chrome.random, "randint", lambda start, end: start)

    class FakePage:
        def goto(
            self, url: str, *, wait_until: str, timeout: int | None = None
        ) -> None:
            return None

        def bring_to_front(self) -> None:
            return None

        def wait_for_timeout(self, timeout_ms: int) -> None:
            timeouts.append(timeout_ms)

        def evaluate(
            self,
            expression: str,
            argument: object | None = None,
        ) -> dict[str, object]:
            assert argument is None
            return {
                "currentUrl": "https://www.linkedin.com/checkpoint/challenge/abc",
                "title": "Security Verification",
                "bodyText": "Let's do a quick security check before you continue.",
            }

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, *, no_defaults: bool) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(chrome, "sync_playwright", lambda: FakeManager())

    with pytest.raises(ChromeRuntimeError) as exc_info:
        parse_job_details_from_chrome_session(
            "http://127.0.0.1:9222",
            "102",
        )

    assert "blocked automated access" in str(exc_info.value)
    assert timeouts == []


def test_parse_job_details_from_chrome_session_reports_logged_out_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePage:
        def goto(
            self, url: str, *, wait_until: str, timeout: int | None = None
        ) -> None:
            return None

        def bring_to_front(self) -> None:
            return None

        def wait_for_timeout(self, timeout_ms: int) -> None:
            return None

        def evaluate(
            self,
            expression: str,
            argument: object | None = None,
        ) -> dict[str, object]:
            assert argument is None
            return {
                "currentUrl": "https://www.linkedin.com/jobs/search/?start=0",
                "title": "LinkedIn Jobs, Employment",
                "bodyText": (
                    "Sign in to view more jobs Continue with Google Sign in with Email"
                ),
            }

    class FakeContext:
        pages = [FakePage()]

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, *, no_defaults: bool) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(chrome, "sync_playwright", lambda: FakeManager())

    with pytest.raises(ChromeRuntimeError) as exc_info:
        parse_job_details_from_chrome_session(
            "http://127.0.0.1:9222",
            "102",
        )

    assert "LinkedIn session appears logged out" in str(exc_info.value)


def test_parse_posted_date_supports_posted_on_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "FixedDateTime":
            return cls(2026, 3, 7, tzinfo=tz)

    monkeypatch.setattr(chrome, "datetime", FixedDateTime)

    parsed = chrome._parse_posted_date("Posted on March 5, 2026, 12:07 PM")

    assert parsed == "2026-03-05"


def test_extract_posted_date_falls_back_to_metadata_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "FixedDateTime":
            return cls(2026, 3, 7, tzinfo=tz)

    monkeypatch.setattr(chrome, "datetime", FixedDateTime)

    parsed = chrome._extract_posted_date(
        {
            "postedDateText": "Chicago, IL",
            "metadataSegments": ["Chicago, IL", "2 weeks ago", "17 applicants"],
        }
    )

    assert parsed == "2026-02-21"


def test_extract_posted_date_falls_back_to_page_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "FixedDateTime":
            return cls(2026, 3, 7, tzinfo=tz)

    monkeypatch.setattr(chrome, "datetime", FixedDateTime)

    parsed = chrome._extract_posted_date(
        {
            "postedDateText": "Santiago Metropolitan Region, Chile",
            "lines": [
                "Company",
                "Title",
                "Santiago Metropolitan Region, Chile",
                "Posted on March 5, 2026",
            ],
        }
    )

    assert parsed == "2026-03-05"


def test_normalize_job_details_uses_job_url_when_apply_link_missing() -> None:
    details = chrome._normalize_job_details(
        {
            "currentUrl": "https://www.linkedin.com/jobs/view/105/",
            "companyName": "Acme",
            "positionName": "QA Engineer",
            "jobLocation": "Remote",
            "postedDateText": "1 day ago",
            "easyApply": False,
            "applyLink": "",
            "aboutText": "About text",
        },
        requested_job_id="105",
    )

    assert details.apply_link == "https://www.linkedin.com/jobs/view/105"


def test_normalize_job_details_builds_placeholder_for_unavailable_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "FixedDateTime":
            return cls(2026, 3, 7, tzinfo=tz)

    monkeypatch.setattr(chrome, "datetime", FixedDateTime)

    details = chrome._normalize_job_details(
        {
            "currentUrl": "https://www.linkedin.com/jobs/view/104/",
            "unavailableReason": "Job id provided may not be valid or the job posting has been removed.",
        },
        requested_job_id="104",
    )

    assert details.company_name == "Unknown"
    assert details.position_name == "Unavailable LinkedIn job"
    assert details.job_location == "Unknown"
    assert details.posted_date == "2026-03-07"
    assert details.apply_link == "https://www.linkedin.com/jobs/view/104/"
    assert "job posting has been removed" in details.about_text


def test_normalize_about_text_removes_header_footer_and_more_suffix() -> None:
    raw_about = (
        "About the job We are hiring QA engineers for a growing team. "
        "Responsibilities include manual and automation testing. "
        "… more Can’t find top talent? Try Hiring Pro About Accessibility Talent Solutions"
    )

    normalized = chrome._normalize_about_text(raw_about)

    assert normalized == (
        "We are hiring QA engineers for a growing team. "
        "Responsibilities include manual and automation testing."
    )


def test_close_tracked_chrome_session_returns_when_process_is_already_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _make_record()

    monkeypatch.setattr(chrome, "is_process_alive", lambda pid: False)
    monkeypatch.setattr(
        chrome.os,
        "kill",
        lambda pid, sig: (_ for _ in ()).throw(
            AssertionError("os.kill should not be called")
        ),
    )

    close_tracked_chrome_session(record)


def test_close_tracked_chrome_session_escalates_after_term_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _make_record(pid=54321)
    sent_signals: list[tuple[int, int]] = []
    wait_results = iter([False, True])

    monkeypatch.setattr(chrome, "is_process_alive", lambda pid: True)
    monkeypatch.setattr(
        chrome.os,
        "kill",
        lambda pid, sig: sent_signals.append((pid, sig)),
    )
    monkeypatch.setattr(
        chrome,
        "_wait_for_process_exit",
        lambda pid, *, timeout: next(wait_results),
    )

    close_tracked_chrome_session(record)

    assert sent_signals == [
        (54321, signal.SIGTERM),
        (54321, getattr(signal, "SIGKILL", signal.SIGTERM)),
    ]


def test_resolve_chrome_profile_paths_rejects_missing_directory(tmp_path) -> None:
    missing_path = tmp_path / "Google" / "Chrome" / "Profile 99"

    with pytest.raises(ChromeRuntimeError) as exc_info:
        resolve_chrome_profile_paths(missing_path)

    assert str(missing_path) in str(exc_info.value)
    assert "does not exist or is not a directory" in str(exc_info.value)
