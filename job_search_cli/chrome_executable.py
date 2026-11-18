"""Dependency-free Chrome discovery shared by setup and browser workflows."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys


def chrome_candidates() -> tuple[str | None, ...]:
    override = os.environ.get("JOBSCOUT_BROWSER_PATH")
    if override:
        return (str(Path(override).expanduser()),)
    if sys.platform == "darwin":
        return tuple(
            str(root / app / "Contents/MacOS" / executable)
            for root in (Path("/Applications"), Path.home() / "Applications")
            for app, executable in (
                ("Google Chrome.app", "Google Chrome"),
                ("Helium.app", "Helium"),
                ("Chromium.app", "Chromium"),
                ("Brave Browser.app", "Brave Browser"),
                ("Microsoft Edge.app", "Microsoft Edge"),
            )
        )
    if os.name == "nt":
        return tuple(
            str(Path(root) / "Google/Chrome/Application/chrome.exe") if root else None
            for root in (
                os.environ.get("PROGRAMFILES"),
                os.environ.get("PROGRAMFILES(X86)"),
                os.environ.get("LOCALAPPDATA"),
            )
        )
    return (
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("helium"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("brave-browser"),
        shutil.which("microsoft-edge"),
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/opt/google/chrome/google-chrome",
    )
