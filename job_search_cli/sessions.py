"""Helpers for persisting tracked Chrome session metadata."""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from job_search_cli.runtime_paths import (
    private_directory,
    sessions_path as resolve_sessions_path,
)


class SessionStoreError(ValueError):
    """Raised when the session store cannot be read or written safely."""


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """Persisted browser session metadata."""

    sessionName: str
    wsEndpoint: str
    cdpHttpEndpoint: str
    pid: int
    chromeProfilePath: str
    headless: bool
    createdAt: str
    lastUsedAt: str

    @classmethod
    def from_payload(
        cls,
        payload: Any,
        *,
        path: Path,
        label: str,
    ) -> SessionRecord:
        """Validate and normalize a JSON payload into a session record."""
        if not isinstance(payload, dict):
            raise SessionStoreError(
                f"Session store '{path}': {label} must be a JSON object."
            )

        return cls(
            sessionName=_require_text(payload, path, label, "sessionName"),
            wsEndpoint=_require_text(payload, path, label, "wsEndpoint"),
            cdpHttpEndpoint=_require_text(payload, path, label, "cdpHttpEndpoint"),
            pid=_require_pid(payload, path, label, "pid"),
            chromeProfilePath=_require_text(
                payload,
                path,
                label,
                "chromeProfilePath",
            ),
            headless=_require_bool(payload, path, label, "headless"),
            createdAt=_require_text(payload, path, label, "createdAt"),
            lastUsedAt=_require_text(payload, path, label, "lastUsedAt"),
        )

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON-serializable representation of the record."""
        return asdict(self)


def load_session_records(
    sessions_path: str | Path | None = None,
) -> list[SessionRecord]:
    """Load the session store or return an empty list when it does not exist."""
    path = resolve_sessions_path(sessions_path)

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise SessionStoreError(
            f"Session store '{path}': could not be read ({_format_os_error(exc)})."
        ) from exc
    except json.JSONDecodeError as exc:
        detail = f"{exc.msg} at line {exc.lineno} column {exc.colno}"
        raise SessionStoreError(
            f"Session store '{path}': invalid JSON ({detail})."
        ) from exc

    if not isinstance(payload, list):
        raise SessionStoreError(
            f"Session store '{path}': expected a top-level JSON array."
        )

    return [
        SessionRecord.from_payload(record, path=path, label=f"entry {index}")
        for index, record in enumerate(payload)
    ]


def write_session_records(
    records: Iterable[SessionRecord | dict[str, Any]],
    sessions_path: str | Path | None = None,
) -> list[SessionRecord]:
    """Write session records atomically, keeping one record per session name."""
    path = resolve_sessions_path(sessions_path)
    normalized_records = _deduplicate_records(
        [
            _coerce_session_record(record, path=path, label=f"entry {index}")
            for index, record in enumerate(records)
        ]
    )
    temp_path: Path | None = None
    try:
        private_directory(path.parent)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(
                [record.to_payload() for record in normalized_records],
                handle,
                indent=2,
            )
            handle.write("\n")

        _replace_file(temp_path, path)
    except OSError as exc:
        raise SessionStoreError(
            f"Session store '{path}': could not be written ({_format_os_error(exc)})."
        ) from exc
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return normalized_records


def save_session_record(
    record: SessionRecord | dict[str, Any],
    sessions_path: str | Path | None = None,
) -> list[SessionRecord]:
    """Add or replace one session record by session name."""
    path = resolve_sessions_path(sessions_path)
    new_record = _coerce_session_record(record, path=path, label="entry")
    existing_records = load_session_records(path)
    return write_session_records([*existing_records, new_record], path)


def _coerce_session_record(
    record: SessionRecord | dict[str, Any],
    *,
    path: Path,
    label: str,
) -> SessionRecord:
    if isinstance(record, SessionRecord):
        return record
    return SessionRecord.from_payload(record, path=path, label=label)


def _deduplicate_records(records: list[SessionRecord]) -> list[SessionRecord]:
    seen_session_names: set[str] = set()
    deduplicated: list[SessionRecord] = []

    for record in reversed(records):
        if record.sessionName in seen_session_names:
            continue

        seen_session_names.add(record.sessionName)
        deduplicated.append(record)

    deduplicated.reverse()
    return deduplicated


def _replace_file(source_path: Path, destination_path: Path) -> None:
    source_path.replace(destination_path)


def _require_text(
    payload: dict[str, Any],
    path: Path,
    label: str,
    field_name: str,
) -> str:
    value = _require_field(payload, path, label, field_name)
    if not isinstance(value, str) or not value.strip():
        raise SessionStoreError(
            f"Session store '{path}': {label} field '{field_name}' "
            "must be a non-empty string."
        )
    return value


def _require_bool(
    payload: dict[str, Any],
    path: Path,
    label: str,
    field_name: str,
) -> bool:
    value = _require_field(payload, path, label, field_name)
    if not isinstance(value, bool):
        raise SessionStoreError(
            f"Session store '{path}': {label} field '{field_name}' must be a boolean."
        )
    return value


def _require_pid(
    payload: dict[str, Any],
    path: Path,
    label: str,
    field_name: str,
) -> int:
    value = _require_field(payload, path, label, field_name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SessionStoreError(
            f"Session store '{path}': {label} field '{field_name}' must be an integer."
        )
    return value


def _require_field(
    payload: dict[str, Any],
    path: Path,
    label: str,
    field_name: str,
) -> Any:
    if field_name not in payload:
        raise SessionStoreError(
            f"Session store '{path}': {label} missing field '{field_name}'."
        )
    return payload[field_name]


def _format_os_error(error: OSError) -> str:
    return error.strerror or str(error)
