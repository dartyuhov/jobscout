from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from click.testing import CliRunner

from job_search_cli.chrome import ChromeLaunchResult
from job_search_cli.cli import main
from job_search_cli.linkedin_profile import ProfileLoadError, load_launch_profile
from job_search_cli.linkedin_url import (
    build_linkedin_jobs_page_urls,
    build_linkedin_jobs_url,
)


def test_load_launch_profile_normalizes_defaults_and_extra_keys(tmp_path) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "chromeProfilePath": "/tmp/chrome/Profile 1",
                "sessionName": "linkedin-qa-bcn",
                "headless": False,
                "locationGeoId": 105088894,
                "f_TPR": "r604800",
                "jobTitle": "QA Engineer",
            }
        ),
        encoding="utf-8",
    )

    profile = load_launch_profile(profile_path)

    assert profile["chromeProfilePath"] == "/tmp/chrome/Profile 1"
    assert profile["sessionName"] == "linkedin-qa-bcn"
    assert profile["headless"] is False
    assert profile["locationGeoId"] == "105088894"
    assert profile["f_TPR"] == "r604800"
    assert "f_WT" not in profile
    assert profile["currentJobId"] == "1"
    assert profile["maxPage"] == 5
    assert profile["parseMinDelayMs"] == 2000
    assert profile["parseMaxDelayMs"] == 6000
    assert profile["parseRetryBaseDelayMs"] == 1000
    assert profile["parseRetryMaxDelayMs"] == 8000
    assert profile["parseReloadRetries"] == 3
    assert profile["jobTitle"] == "QA Engineer"


def test_load_launch_profile_coerces_max_page_to_positive_integer(tmp_path) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "chromeProfilePath": "/tmp/chrome/Profile 1",
                "sessionName": "linkedin-qa-bcn",
                "headless": False,
                "locationGeoId": 105088894,
                "f_TPR": "r604800",
                "maxPage": "7",
            }
        ),
        encoding="utf-8",
    )

    profile = load_launch_profile(profile_path)

    assert profile["maxPage"] == 7


def test_load_launch_profile_rejects_non_positive_max_page(tmp_path) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "chromeProfilePath": "/tmp/chrome/Profile 1",
                "sessionName": "linkedin-qa-bcn",
                "headless": False,
                "locationGeoId": "105088894",
                "f_TPR": "r604800",
                "maxPage": 0,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProfileLoadError) as exc_info:
        load_launch_profile(profile_path)

    assert "maxPage" in str(exc_info.value)
    assert "positive integer" in str(exc_info.value)


def test_load_launch_profile_rejects_parse_delay_range_inversion(tmp_path) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "chromeProfilePath": "/tmp/chrome/Profile 1",
                "sessionName": "linkedin-qa-bcn",
                "headless": False,
                "locationGeoId": "105088894",
                "f_TPR": "r604800",
                "parseMinDelayMs": 7000,
                "parseMaxDelayMs": 2000,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProfileLoadError) as exc_info:
        load_launch_profile(profile_path)

    assert "parseMinDelayMs" in str(exc_info.value)


def test_load_launch_profile_rejects_parse_retry_delay_range_inversion(
    tmp_path,
) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "chromeProfilePath": "/tmp/chrome/Profile 1",
                "sessionName": "linkedin-qa-bcn",
                "headless": False,
                "locationGeoId": "105088894",
                "f_TPR": "r604800",
                "parseRetryBaseDelayMs": 9000,
                "parseRetryMaxDelayMs": 4000,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProfileLoadError) as exc_info:
        load_launch_profile(profile_path)

    assert "parseRetryBaseDelayMs" in str(exc_info.value)


def test_build_linkedin_jobs_url_from_sample_profile_includes_keywords() -> None:
    profile_path = Path(__file__).parent / "fixtures" / "launch-profile.json"

    profile = load_launch_profile(profile_path)
    url = build_linkedin_jobs_url(profile)
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.linkedin.com"
    assert parsed.path == "/jobs/search/"
    expected_query_keys = {
        "currentJobId",
        "f_TPR",
        "geoId",
        "keywords",
    }
    if "f_WT" in profile:
        expected_query_keys.add("f_WT")

    assert set(parse_qs(parsed.query)) == expected_query_keys
    assert parse_qs(parsed.query)["keywords"] == ["Software Engineer"]


def test_build_linkedin_jobs_page_urls_uses_25_result_page_offsets() -> None:
    profile = {
        "currentJobId": "1",
        "f_TPR": "r604800",
        "locationGeoId": "105088894",
        "maxPage": 5,
    }

    urls = build_linkedin_jobs_page_urls(profile)

    assert urls == [
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


def test_load_launch_profile_missing_required_field_names_file(tmp_path) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "sessionName": "linkedin-qa-bcn",
                "headless": False,
                "locationGeoId": "105088894",
                "f_TPR": "r604800",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProfileLoadError) as exc_info:
        load_launch_profile(profile_path)

    assert str(profile_path) in str(exc_info.value)
    assert "chromeProfilePath" in str(exc_info.value)


def test_load_launch_profile_invalid_json_names_file(tmp_path) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ProfileLoadError) as exc_info:
        load_launch_profile(profile_path)

    assert str(profile_path) in str(exc_info.value)
    assert "invalid JSON" in str(exc_info.value)


@pytest.mark.parametrize(
    ("file_contents", "expected_message"),
    [
        (
            json.dumps(
                {
                    "headless": False,
                    "locationGeoId": "105088894",
                    "f_TPR": "r604800",
                }
            ),
            "missing required field 'sessionName'",
        ),
        ("{not-json", "invalid JSON"),
    ],
)
def test_launch_command_reports_profile_loading_errors(
    tmp_path,
    file_contents: str,
    expected_message: str,
) -> None:
    runner = CliRunner()
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(file_contents, encoding="utf-8")

    result = runner.invoke(main, ["linkedin", "launch", "--profile", str(profile_path)])

    assert result.exit_code != 0
    assert str(profile_path) in result.output
    assert expected_message in result.output


def test_launch_command_fails_for_missing_profile_path(tmp_path) -> None:
    runner = CliRunner()
    missing_path = tmp_path / "missing.json"

    result = runner.invoke(main, ["linkedin", "launch", "--profile", str(missing_path)])

    assert result.exit_code != 0
    assert str(missing_path) in result.output
    assert "file does not exist" in result.output


def test_launch_command_succeeds_for_valid_profile(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    chrome_user_data_dir = tmp_path / "chrome"
    chrome_profile_dir = chrome_user_data_dir / "Profile 1"
    chrome_profile_dir.mkdir(parents=True)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "chromeProfilePath": str(chrome_profile_dir),
                "sessionName": "linkedin-qa-bcn",
                "headless": True,
                "locationGeoId": "105088894",
                "f_TPR": "r604800",
                "f_WT": "1",
                "currentJobId": 987654,
            }
        ),
        encoding="utf-8",
    )
    from job_search_cli.job_store import upsert_profile

    upsert_profile(json.loads(profile_path.read_text(encoding="utf-8")))
    monkeypatch.setattr(
        "job_search_cli.cli.launch_google_chrome",
        lambda profile: ChromeLaunchResult(
            pid=54321,
            cdp_http_endpoint="http://127.0.0.1:9333",
            ws_endpoint="ws://127.0.0.1:9333/devtools/browser/test",
            launch_args=(
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "--remote-debugging-port=9333",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={chrome_user_data_dir}",
                "--profile-directory=Profile 1",
                "--no-first-run",
                "--no-default-browser-check",
                "--headless=new",
            ),
        ),
    )
    monkeypatch.setattr(
        "job_search_cli.cli.open_url_in_chrome_session",
        lambda cdp_http_endpoint, url: None,
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["linkedin", "launch", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "Created Chrome session 'linkedin-qa-bcn'." in result.output
    assert "PID: 54321" in result.output
    assert "CDP HTTP endpoint: http://127.0.0.1:9333" in result.output
    assert (
        "Opened URL: https://www.linkedin.com/jobs/search/"
        "?currentJobId=1&f_TPR=r604800&f_WT=1&geoId=105088894"
    ) in result.output
    assert "jobTitle" not in result.output
