"""Allowlisted, versioned search-settings exchange; never runtime records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from job_search_cli.job_store import get_profile, upsert_profile
from job_search_cli.runtime_paths import data_dir

FORMAT = "job-search-settings"
NOTICE = "Preference text can contain personal details. Review it before sharing."
DEFAULTS = {
    "jobTitle": "",
    "locationGeoId": "",
    "f_TPR": "",
    "f_WT": "",
    "want": "",
    "not_want": "",
    "maxPage": 5,
}


class ProfileExchangeError(ValueError):
    """An exchange document is malformed or unsupported."""


def export_settings(profile_id: int) -> dict[str, Any]:
    profile = get_profile(profile_id)
    return {
        "format": FORMAT,
        "version": 1,
        "notice": NOTICE,
        "settings": {
            key: profile.get(key, default) for key, default in DEFAULTS.items()
        },
    }


def import_settings(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProfileExchangeError("Could not read search settings JSON.") from exc
    settings = validate_settings(document)
    profile_id = upsert_profile(
        settings
        | {
            "sessionName": f"search-{uuid4()}",
            "chromeProfilePath": str(data_dir() / "chrome" / "Default"),
            "headless": False,
        }
    )
    return get_profile(profile_id)


def validate_settings(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise ProfileExchangeError("Expected a job-search-settings JSON object.")
    if type(document.get("version")) is not int or document["version"] != 1:
        raise ProfileExchangeError("Unsupported search settings version; expected 1.")
    source = document.get("settings")
    if not isinstance(source, dict):
        raise ProfileExchangeError("settings must be a JSON object.")
    settings = {key: source.get(key, default) for key, default in DEFAULTS.items()}
    for key, value in settings.items():
        if key == "maxPage":
            if type(value) is not int or not 1 <= value <= 2147483647:
                raise ProfileExchangeError("maxPage must be a positive 32-bit integer.")
        elif not isinstance(value, str):
            raise ProfileExchangeError(f"{key} must be a string.")
    for key in ("locationGeoId", "f_TPR"):
        if not settings[key].strip():
            raise ProfileExchangeError(f"{key} must be a non-empty string.")
    return settings
