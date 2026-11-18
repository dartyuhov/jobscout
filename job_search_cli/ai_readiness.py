"""Read the same saved connection verification as the portal, without network probes."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from job_search_cli.runtime_paths import database_path

AI_SETUP_GUIDANCE = (
    "AI is not configured. Configure and verify an AI connection in Settings "
    "before using AI features. "
    "Collection, parsing, and review work without AI."
)


@dataclass(frozen=True, slots=True)
class AiReadiness:
    """Provider-independent readiness and separate background opt-ins."""

    ready: bool = False
    auto_analyze: bool = False
    auto_summarize: bool = False
    guidance: str = AI_SETUP_GUIDANCE


def valid_connection(value: object) -> bool:
    """Validate the persisted schema before readiness or credential use."""

    def text(item: object) -> bool:
        return (
            isinstance(item, str)
            and bool(item.strip())
            and not any(c in item for c in "\r\n")
        )

    if not isinstance(value, dict):
        return False
    if set(value) - {
        "base_url",
        "model",
        "token",
        "revision",
        "verified_revision",
        "test_attempt",
    }:
        return False
    if "verified_revision" not in value:
        return False
    if not all(text(value.get(key)) for key in ("base_url", "model", "revision")):
        return False
    if not isinstance(value.get("token"), str) or any(
        c in value["token"] for c in "\r\n"
    ):
        return False
    if value.get("verified_revision") is not None and not text(
        value["verified_revision"]
    ):
        return False
    if "test_attempt" in value and not text(value["test_attempt"]):
        return False
    if not re.match(r"^https?://", value["base_url"], re.IGNORECASE) or any(
        c.isspace() for c in value["base_url"]
    ):
        return False
    try:
        url = urlsplit(value["base_url"])
        url.port  # Validate malformed ports before passing credentials to transport.
        return bool(
            url.scheme in ("http", "https")
            and url.hostname
            and not (url.username or url.password or url.query or url.fragment)
        )
    except ValueError:
        return False


def get_ai_readiness(*, db_path: Path | None = None) -> AiReadiness:
    """Fail closed on absent or invalid settings; never create a database on read."""
    path = database_path(db_path)
    try:
        with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?", ("ai_connection",)
            ).fetchone()
        saved = json.loads(row[0]) if row else {}
        ready = bool(
            valid_connection(saved)
            and saved.get("verified_revision") == saved["revision"]
        )
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        return AiReadiness()
    automation = {}
    try:
        with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?", ("ai_automation",)
            ).fetchone()
        automation = json.loads(row[0]) if row else {}
        if not isinstance(automation, dict):
            automation = {}
    except (sqlite3.Error, ValueError, TypeError):
        pass
    return AiReadiness(
        ready=ready,
        auto_analyze=automation.get("auto_analyze") is True,
        auto_summarize=automation.get("auto_summarize") is True,
        guidance="" if ready else AI_SETUP_GUIDANCE,
    )
