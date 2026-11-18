"""Dependency-free Chrome discovery shared by setup and browser workflows."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys


def chrome_candidates() -> tuple[str | None, ...]:
    if sys.platform == "darwin":
        return (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            str(
                Path.home()
                / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            ),
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
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/opt/google/chrome/google-chrome",
    )
