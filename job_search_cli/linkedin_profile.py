"""Helpers for loading LinkedIn launch profiles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OPTIONAL_DEFAULTS = {
    "currentJobId": "1",
}

OPTIONAL_STRING_FIELDS = ("f_WT",)
OPTIONAL_POSITIVE_INT_DEFAULTS = {
    "maxPage": 5,
    "parseMinDelayMs": 2000,
    "parseMaxDelayMs": 6000,
    "parseRetryBaseDelayMs": 1000,
    "parseRetryMaxDelayMs": 8000,
    "parseReloadRetries": 3,
}


class ProfileLoadError(ValueError):
    """Raised when a profile file cannot be loaded safely."""


def load_launch_profile(profile_path: str | Path) -> dict[str, Any]:
    """Load, validate, and normalize a LinkedIn launch profile."""
    path = Path(profile_path)
    return normalize_launch_profile(_read_profile_object(path), path)


def _read_profile_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProfileLoadError(f"Profile '{path}': file does not exist.") from exc
    except OSError as exc:
        raise ProfileLoadError(
            f"Profile '{path}': could not be read ({exc.strerror})."
        ) from exc
    except json.JSONDecodeError as exc:
        detail = f"{exc.msg} at line {exc.lineno} column {exc.colno}"
        raise ProfileLoadError(f"Profile '{path}': invalid JSON ({detail}).") from exc

    if not isinstance(payload, dict):
        raise ProfileLoadError(f"Profile '{path}': expected a JSON object.")
    return payload


def load_selected_profile(
    profile_path: Path | None = None,
    profile_id: int | None = None,
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Resolve a database record; legacy JSON is only an identity selector."""
    from job_search_cli.job_store import get_profile

    if (profile_path is None) == (profile_id is None):
        raise ProfileLoadError(
            "Select exactly one of --profile-id ID or --profile PATH."
        )
    session_name = None
    if profile_path is not None:
        legacy = _read_profile_object(profile_path)
        value = legacy.get("id")
        if value is not None:
            if isinstance(value, bool) or not str(value).isdigit() or int(value) < 1:
                raise ProfileLoadError(
                    f"Profile '{profile_path}': field 'id' must be a positive integer."
                )
            profile_id = int(value)
        else:
            session_name = _require_text(legacy, profile_path, "sessionName")
    profile = get_profile(profile_id, session_name=session_name, db_path=db_path)
    if profile.get("archived_at"):
        raise ProfileLoadError(
            f"Profile id '{profile['id']}' is archived. Restore it in the portal first."
        )
    from job_search_cli.runtime_paths import browser_profile_path

    profile.update(chromeProfilePath=str(browser_profile_path()), statePath="")
    return normalize_launch_profile(profile, Path(f"database profile {profile['id']}"))


def normalize_launch_profile(payload: Any, path: Path) -> dict[str, Any]:
    """Validate file and database launch settings consistently."""
    if not isinstance(payload, dict):
        raise ProfileLoadError(f"Profile '{path}': expected a JSON object.")

    normalized = dict(payload)
    normalized["chromeProfilePath"] = _require_text(payload, path, "chromeProfilePath")
    normalized["sessionName"] = _require_text(payload, path, "sessionName")
    normalized["headless"] = _require_bool(payload, path, "headless")

    if payload.get("searchUrl"):
        from job_search_cli.search_url import normalize_search_url

        try:
            normalized["searchUrl"] = normalize_search_url(payload["searchUrl"])
        except ValueError as exc:
            raise ProfileLoadError(str(exc)) from exc

    for field_name in ("locationGeoId", "f_TPR"):
        if normalized.get("searchUrl"):
            normalized[field_name] = str(payload.get(field_name, ""))
            continue
        normalized[field_name] = _require_string_value(payload, path, field_name)

    for field_name, default_value in OPTIONAL_DEFAULTS.items():
        normalized[field_name] = _optional_string_value(
            payload,
            path,
            field_name,
            default_value,
        )

    for field_name in OPTIONAL_STRING_FIELDS:
        if field_name in payload:
            normalized[field_name] = _normalize_string_value(
                path,
                field_name,
                payload[field_name],
            )

    for field_name, default_value in OPTIONAL_POSITIVE_INT_DEFAULTS.items():
        normalized[field_name] = _optional_positive_int_value(
            payload,
            path,
            field_name,
            default_value,
        )

    if normalized["parseMinDelayMs"] > normalized["parseMaxDelayMs"]:
        raise ProfileLoadError(
            f"Profile '{path}': field 'parseMinDelayMs' must be less than or equal to 'parseMaxDelayMs'."
        )

    if normalized["parseRetryBaseDelayMs"] > normalized["parseRetryMaxDelayMs"]:
        raise ProfileLoadError(
            f"Profile '{path}': field 'parseRetryBaseDelayMs' must be less than or equal to 'parseRetryMaxDelayMs'."
        )

    return normalized


def _require_text(payload: dict[str, Any], path: Path, field_name: str) -> str:
    value = _require_field(payload, path, field_name)
    if not isinstance(value, str) or not value.strip():
        raise ProfileLoadError(
            f"Profile '{path}': field '{field_name}' must be a non-empty string."
        )
    return value


def _require_bool(payload: dict[str, Any], path: Path, field_name: str) -> bool:
    value = _require_field(payload, path, field_name)
    if not isinstance(value, bool):
        raise ProfileLoadError(
            f"Profile '{path}': field '{field_name}' must be a boolean."
        )
    return value


def _require_string_value(payload: dict[str, Any], path: Path, field_name: str) -> str:
    value = _require_field(payload, path, field_name)
    return _normalize_string_value(path, field_name, value)


def _optional_string_value(
    payload: dict[str, Any],
    path: Path,
    field_name: str,
    default_value: str,
) -> str:
    value = payload.get(field_name, default_value)
    return _normalize_string_value(path, field_name, value)


def _normalize_string_value(path: Path, field_name: str, value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ProfileLoadError(
            f"Profile '{path}': field '{field_name}' must be a string or integer."
        )
    return str(value)


def _optional_positive_int_value(
    payload: dict[str, Any],
    path: Path,
    field_name: str,
    default_value: int,
) -> int:
    value = payload.get(field_name, default_value)
    if isinstance(value, bool):
        raise ProfileLoadError(
            f"Profile '{path}': field '{field_name}' must be a positive integer."
        )

    try:
        parsed_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ProfileLoadError(
            f"Profile '{path}': field '{field_name}' must be a positive integer."
        ) from exc

    if parsed_value < 1:
        raise ProfileLoadError(
            f"Profile '{path}': field '{field_name}' must be a positive integer."
        )

    return parsed_value


def _require_field(payload: dict[str, Any], path: Path, field_name: str) -> Any:
    if field_name not in payload:
        raise ProfileLoadError(
            f"Profile '{path}': missing required field '{field_name}'."
        )
    return payload[field_name]
