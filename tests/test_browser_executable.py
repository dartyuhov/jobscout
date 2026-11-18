from pathlib import Path

import pytest

from job_search_cli import chrome, chrome_executable


def test_explicit_browser_path_takes_precedence(tmp_path, monkeypatch):
    browser = tmp_path / "custom chromium"
    browser.touch()
    browser.chmod(0o700)
    monkeypatch.setenv("JOBSCOUT_BROWSER_PATH", str(browser))
    assert chrome.find_google_chrome_executable() == str(browser)


def test_bad_override_does_not_silently_select_another_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBSCOUT_BROWSER_PATH", str(tmp_path / "missing"))
    with pytest.raises(chrome.ChromeRuntimeError, match="JOBSCOUT_BROWSER_PATH"):
        chrome.find_google_chrome_executable()


def test_macos_candidates_include_helium(monkeypatch):
    monkeypatch.delenv("JOBSCOUT_BROWSER_PATH", raising=False)
    monkeypatch.setattr(chrome_executable.sys, "platform", "darwin")
    assert str(Path("/Applications/Helium.app/Contents/MacOS/Helium")) in (
        chrome_executable.chrome_candidates()
    )
