"""SQLite persistence for LinkedIn launch profiles and jobs."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from job_search_cli.linkedin_job import LinkedInJobDetails
from job_search_cli.runtime_paths import database_path, private_file

DEFAULT_JOBS_DB_FILENAME = "linkedin_jobs.sqlite3"
PROFILE_TABLE = "profile"
LINKEDIN_JOBS_TABLE = "linkedin_jobs"
AI_ANALYSIS_TABLE = "ai_analysis"
LEGACY_AI_REVIEW_TABLE = "ai_review"
SOURCE_TYPE_LINKEDIN = "linkedin"
DEFAULT_USER_STATUS = "to_be_reviewed"

PROFILE_SCHEMA_COLUMNS = {
    "jobTitle": "TEXT NOT NULL DEFAULT ''",
    "locationGeoId": "TEXT NOT NULL DEFAULT ''",
    "f_TPR": "TEXT NOT NULL DEFAULT ''",
    "f_WT": "TEXT NOT NULL DEFAULT ''",
    "want": "TEXT NOT NULL DEFAULT ''",
    "not_want": "TEXT NOT NULL DEFAULT ''",
    "headless": "INTEGER NOT NULL DEFAULT 0",
    "chromeProfilePath": "TEXT NOT NULL DEFAULT ''",
    "sessionName": "TEXT NOT NULL DEFAULT ''",
    "maxPage": "INTEGER NOT NULL DEFAULT 5",
    "parseMinDelayMs": "INTEGER NOT NULL DEFAULT 2000",
    "parseMaxDelayMs": "INTEGER NOT NULL DEFAULT 6000",
    "parseRetryBaseDelayMs": "INTEGER NOT NULL DEFAULT 1000",
    "parseRetryMaxDelayMs": "INTEGER NOT NULL DEFAULT 8000",
    "parseReloadRetries": "INTEGER NOT NULL DEFAULT 3",
    "statePath": "TEXT NOT NULL DEFAULT ''",
    "reporting": "TEXT NOT NULL DEFAULT '{}'",
    "created_at": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
}

LINKEDIN_JOBS_SCHEMA_COLUMNS = {
    "source_type": "TEXT NOT NULL DEFAULT 'linkedin'",
    "company_name": "TEXT",
    "position_name": "TEXT",
    "job_location": "TEXT",
    "posted_date": "TEXT",
    "easy_apply": "INTEGER NOT NULL DEFAULT 0",
    "apply_link": "TEXT",
    "about_text": "TEXT",
    "applied": "INTEGER NOT NULL DEFAULT 0",
    "user_status": "TEXT NOT NULL DEFAULT 'to_be_reviewed'",
    "created_at": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
    "profile_id": "INTEGER",
    "analyze_id": "INTEGER",
    "ai_summary": "TEXT",
    "parsed": "INTEGER NOT NULL DEFAULT 0",
}


class JobStoreError(ValueError):
    """Raised when the job ID store cannot be read or written safely."""


@dataclass(frozen=True, slots=True)
class PendingLinkedInJob:
    """LinkedIn job payload used for AI suitability analysis."""

    id: int
    linkedin_job_id: str
    company_name: str
    position_name: str
    job_location: str
    posted_date: str
    easy_apply: bool
    apply_link: str
    about_text: str


@dataclass(frozen=True, slots=True)
class PendingLinkedInSummaryJob:
    """LinkedIn job payload used for AI summary generation."""

    id: int
    linkedin_job_id: str
    company_name: str
    position_name: str
    about_text: str


def upsert_profile(
    profile_payload: Mapping[str, Any],
    db_path: str | Path | None = None,
) -> int:
    """Insert or update a launch profile row and return profile id."""
    path = database_path(db_path)
    timestamp = _utc_now()
    try:
        private_file(path)
        with sqlite3.connect(path) as connection:
            _ensure_schema(connection, timestamp=timestamp)
            profile_id = _upsert_profile_row(
                connection,
                profile_payload=profile_payload,
                timestamp=timestamp,
            )
            connection.execute(
                f'UPDATE {PROFILE_TABLE} SET "f_WT" = ? WHERE id = ?',
                (_coerce_profile_text(profile_payload.get("f_WT")), profile_id),
            )
            return profile_id
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise JobStoreError(
            f"Job store '{path}': could not be updated ({exc})."
        ) from exc


def list_profiles(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Read saved profiles without importing configuration from the checkout."""
    path = database_path(db_path)
    try:
        private_file(path)
        with sqlite3.connect(path) as connection:
            _ensure_schema(connection, timestamp=_utc_now())
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f'SELECT * FROM {PROFILE_TABLE} ORDER BY LOWER("sessionName"), id'
            ).fetchall()
            return [
                {key: row[key] for key in row.keys() if key != "reporting"}
                | {"headless": bool(row["headless"])}
                for row in rows
            ]
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise JobStoreError(f"Job store '{path}': could not be read ({exc}).") from exc


def get_profile(
    profile_id: int | None = None,
    *,
    session_name: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Select an existing profile by local id or legacy session name."""
    for profile in list_profiles(db_path):
        if (profile_id is not None and profile["id"] == profile_id) or (
            profile_id is None and profile["sessionName"] == session_name
        ):
            return profile
    selector = f"id '{profile_id}'" if profile_id is not None else f"'{session_name}'"
    raise JobStoreError(
        f"Job store '{database_path(db_path)}': profile {selector} was not found. "
        "Create a profile in the portal and use 'job-search profiles list'."
    )


def save_new_linkedin_job_ids(
    job_ids: list[str],
    db_path: str | Path | None = None,
    profile_id: int | None = None,
) -> int:
    """Insert only non-existing LinkedIn job IDs and return inserted count."""
    path = database_path(db_path)
    normalized_job_ids = _normalize_job_ids(job_ids)
    normalized_profile_id = _normalize_profile_id(profile_id)
    timestamp = _utc_now()

    try:
        private_file(path)
        with sqlite3.connect(path) as connection:
            _ensure_schema(connection, timestamp=timestamp)
            return _insert_new_job_rows(
                connection,
                job_ids=normalized_job_ids,
                profile_id=normalized_profile_id,
                timestamp=timestamp,
            )
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise JobStoreError(
            f"Job store '{path}': could not be updated ({exc})."
        ) from exc


def filter_new_linkedin_job_ids(
    job_ids: list[str],
    db_path: str | Path | None = None,
) -> list[str]:
    """Return only LinkedIn job IDs that do not yet exist in the database."""
    path = database_path(db_path)
    normalized_job_ids = _normalize_job_ids(job_ids)
    timestamp = _utc_now()

    try:
        private_file(path)
        with sqlite3.connect(path) as connection:
            _ensure_schema(connection, timestamp=timestamp)
            return _filter_missing_job_ids(connection, job_ids=normalized_job_ids)
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise JobStoreError(f"Job store '{path}': could not be read ({exc}).") from exc


def filter_unparsed_linkedin_job_ids(
    job_ids: list[str],
    db_path: str | Path | None = None,
) -> list[str]:
    """Return LinkedIn job IDs that do not already have parsed details."""
    path = database_path(db_path)
    normalized_job_ids = _normalize_job_ids(job_ids)
    timestamp = _utc_now()

    try:
        private_file(path)
        with sqlite3.connect(path) as connection:
            _ensure_schema(connection, timestamp=timestamp)
            return _filter_unparsed_job_ids(connection, job_ids=normalized_job_ids)
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise JobStoreError(f"Job store '{path}': could not be read ({exc}).") from exc


def upsert_linkedin_job_details(
    details: LinkedInJobDetails,
    db_path: str | Path | None = None,
    parsed: bool = True,
    profile_id: int | None = None,
) -> int:
    """Insert or update one LinkedIn job row and return changed row count."""
    path = database_path(db_path)
    normalized_profile_id = _normalize_profile_id(profile_id)
    timestamp = _utc_now()
    try:
        private_file(path)
        with sqlite3.connect(path) as connection:
            _ensure_schema(connection, timestamp=timestamp)
            before_changes = connection.total_changes
            connection.execute(
                f"""
                INSERT INTO {LINKEDIN_JOBS_TABLE} (
                    linkedin_job_id,
                    source_type,
                    company_name,
                    position_name,
                    job_location,
                    posted_date,
                    easy_apply,
                    apply_link,
                    about_text,
                    applied,
                    profile_id,
                    parsed,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(linkedin_job_id) DO UPDATE SET
                    source_type = excluded.source_type,
                    company_name = excluded.company_name,
                    position_name = excluded.position_name,
                    job_location = excluded.job_location,
                    posted_date = excluded.posted_date,
                    easy_apply = excluded.easy_apply,
                    apply_link = excluded.apply_link,
                    about_text = excluded.about_text,
                    applied = excluded.applied,
                    profile_id = COALESCE(excluded.profile_id, profile_id),
                    parsed = excluded.parsed,
                    updated_at = excluded.updated_at
                """,
                (
                    details.linkedin_job_id,
                    SOURCE_TYPE_LINKEDIN,
                    details.company_name,
                    details.position_name,
                    details.job_location,
                    details.posted_date,
                    int(details.easy_apply),
                    details.apply_link,
                    details.about_text,
                    int(details.applied),
                    normalized_profile_id,
                    int(parsed),
                    timestamp,
                    timestamp,
                ),
            )
            return connection.total_changes - before_changes
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise JobStoreError(
            f"Job store '{path}': could not be updated ({exc})."
        ) from exc


def get_profile_preferences(
    profile_id: int,
    db_path: str | Path | None = None,
) -> tuple[str, str]:
    """Return normalized want/not_want preferences for one profile id."""
    path = database_path(db_path)
    normalized_profile_id = _normalize_profile_id(profile_id)
    timestamp = _utc_now()

    try:
        private_file(path)
        with sqlite3.connect(path) as connection:
            _ensure_schema(connection, timestamp=timestamp)
            row = connection.execute(
                f'SELECT "want", "not_want" FROM {PROFILE_TABLE} WHERE id = ?',
                (normalized_profile_id,),
            ).fetchone()
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise JobStoreError(f"Job store '{path}': could not be read ({exc}).") from exc

    if row is None:
        raise JobStoreError(
            f"Job store '{path}': profile id '{normalized_profile_id}' was not found."
        )

    return (_coerce_database_text(row[0]), _coerce_database_text(row[1]))


def list_unanalyzed_linkedin_jobs(
    profile_id: int,
    db_path: str | Path | None = None,
) -> list[PendingLinkedInJob]:
    """Return jobs for one profile where analyze_id is NULL or zero."""
    path = database_path(db_path)
    normalized_profile_id = _normalize_profile_id(profile_id)
    timestamp = _utc_now()

    try:
        private_file(path)
        with sqlite3.connect(path) as connection:
            _ensure_schema(connection, timestamp=timestamp)
            rows = connection.execute(
                f"""
                SELECT
                    id,
                    linkedin_job_id,
                    company_name,
                    position_name,
                    job_location,
                    posted_date,
                    easy_apply,
                    apply_link,
                    about_text
                FROM {LINKEDIN_JOBS_TABLE}
                WHERE profile_id = ?
                  AND COALESCE(analyze_id, 0) = 0
                ORDER BY id
                """,
                (normalized_profile_id,),
            ).fetchall()
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise JobStoreError(f"Job store '{path}': could not be read ({exc}).") from exc

    jobs: list[PendingLinkedInJob] = []
    for row in rows:
        jobs.append(
            PendingLinkedInJob(
                id=int(row[0]),
                linkedin_job_id=_require_database_text(
                    row[1],
                    field_name="linkedin_job_id",
                ),
                company_name=_coerce_database_text(row[2]),
                position_name=_coerce_database_text(row[3]),
                job_location=_coerce_database_text(row[4]),
                posted_date=_coerce_database_text(row[5]),
                easy_apply=bool(row[6]),
                apply_link=_coerce_database_text(row[7]),
                about_text=_coerce_database_text(row[8]),
            )
        )

    return jobs


def upsert_ai_analysis(
    *,
    job_id: int,
    analyzed: int,
    desicion: int,
    reasoning: str,
    db_path: str | Path | None = None,
) -> int:
    """Insert or update AI analysis for a job and return analysis id."""
    path = database_path(db_path)
    normalized_job_id = _normalize_positive_int(job_id, field_name="job_id")
    normalized_analyzed = _normalize_binary_int(analyzed, field_name="analyzed")
    normalized_desicion = _normalize_binary_int(desicion, field_name="desicion")
    normalized_reasoning = _normalize_reasoning(reasoning)
    timestamp = _utc_now()

    try:
        private_file(path)
        with sqlite3.connect(path) as connection:
            _ensure_schema(connection, timestamp=timestamp)
            job_row = connection.execute(
                f"SELECT id FROM {LINKEDIN_JOBS_TABLE} WHERE id = ?",
                (normalized_job_id,),
            ).fetchone()
            if job_row is None:
                raise ValueError(f"job id '{normalized_job_id}' was not found")

            connection.execute(
                f"""
                INSERT INTO {AI_ANALYSIS_TABLE} (
                    job_id,
                    analyzed,
                    created_at,
                    updated_at,
                    desicion,
                    reasoning
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    analyzed = excluded.analyzed,
                    updated_at = excluded.updated_at,
                    desicion = excluded.desicion,
                    reasoning = excluded.reasoning
                """,
                (
                    normalized_job_id,
                    normalized_analyzed,
                    timestamp,
                    timestamp,
                    normalized_desicion,
                    normalized_reasoning,
                ),
            )
            analysis_row = connection.execute(
                f"SELECT id FROM {AI_ANALYSIS_TABLE} WHERE job_id = ?",
                (normalized_job_id,),
            ).fetchone()
            if analysis_row is None:
                raise ValueError(
                    f"analysis row for job id '{normalized_job_id}' was not found"
                )

            analysis_id = int(analysis_row[0])
            connection.execute(
                f"""
                UPDATE {LINKEDIN_JOBS_TABLE}
                SET analyze_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (analysis_id, timestamp, normalized_job_id),
            )
            return analysis_id
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise JobStoreError(
            f"Job store '{path}': could not be updated ({exc})."
        ) from exc


def list_unsummarized_matched_linkedin_jobs(
    profile_id: int,
    db_path: str | Path | None = None,
) -> list[PendingLinkedInSummaryJob]:
    """Return matched jobs for one profile that still need AI summaries."""
    path = database_path(db_path)
    normalized_profile_id = _normalize_profile_id(profile_id)
    timestamp = _utc_now()

    try:
        private_file(path)
        with sqlite3.connect(path) as connection:
            _ensure_schema(connection, timestamp=timestamp)
            rows = connection.execute(
                f"""
                SELECT
                    j.id,
                    j.linkedin_job_id,
                    j.company_name,
                    j.position_name,
                    j.about_text
                FROM {LINKEDIN_JOBS_TABLE} j
                LEFT JOIN {AI_ANALYSIS_TABLE} a ON a.job_id = j.id
                WHERE j.profile_id = ?
                  AND COALESCE(a.analyzed, 0) = 1
                  AND COALESCE(a.desicion, 0) = 1
                  AND j.user_status = ?
                  AND TRIM(COALESCE(j.about_text, '')) != ''
                  AND COALESCE(NULLIF(TRIM(j.ai_summary), ''), '') = ''
                ORDER BY j.id
                """,
                (normalized_profile_id, DEFAULT_USER_STATUS),
            ).fetchall()
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise JobStoreError(f"Job store '{path}': could not be read ({exc}).") from exc

    return [
        PendingLinkedInSummaryJob(
            id=int(row[0]),
            linkedin_job_id=_require_database_text(
                row[1], field_name="linkedin_job_id"
            ),
            company_name=_coerce_database_text(row[2]),
            position_name=_coerce_database_text(row[3]),
            about_text=_coerce_database_text(row[4]),
        )
        for row in rows
    ]


def upsert_ai_summary(
    *,
    job_id: int,
    summary_payload: Mapping[str, Any],
    db_path: str | Path | None = None,
) -> int:
    """Persist one job AI summary JSON payload and return changed row count."""
    path = database_path(db_path)
    normalized_job_id = _normalize_positive_int(job_id, field_name="job_id")
    timestamp = _utc_now()

    try:
        serialized_payload = json.dumps(summary_payload, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise JobStoreError(
            f"Job store '{path}': invalid summary payload ({exc})."
        ) from exc

    try:
        private_file(path)
        with sqlite3.connect(path) as connection:
            _ensure_schema(connection, timestamp=timestamp)
            cursor = connection.execute(
                f"""
                UPDATE {LINKEDIN_JOBS_TABLE}
                SET ai_summary = ?, updated_at = ?
                WHERE id = ?
                """,
                (serialized_payload, timestamp, normalized_job_id),
            )
            if cursor.rowcount < 1:
                raise ValueError(f"job id '{normalized_job_id}' was not found")
            return int(cursor.rowcount)
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise JobStoreError(
            f"Job store '{path}': could not be updated ({exc})."
        ) from exc


def _ensure_schema(
    connection: sqlite3.Connection,
    *,
    timestamp: str | None = None,
) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    effective_timestamp = timestamp or _utc_now()

    _ensure_profile_schema(connection, timestamp=effective_timestamp)
    _ensure_ai_analysis_schema(connection, timestamp=effective_timestamp)
    _ensure_jobs_schema(connection, timestamp=effective_timestamp)
    _migrate_legacy_ai_review(connection, timestamp=effective_timestamp)


def _ensure_profile_schema(
    connection: sqlite3.Connection,
    *,
    timestamp: str,
) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {PROFILE_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            "jobTitle" TEXT NOT NULL DEFAULT '',
            "locationGeoId" TEXT NOT NULL DEFAULT '',
            "f_TPR" TEXT NOT NULL DEFAULT '',
            "f_WT" TEXT NOT NULL DEFAULT '',
            "want" TEXT NOT NULL DEFAULT '',
            "not_want" TEXT NOT NULL DEFAULT '',
            "headless" INTEGER NOT NULL DEFAULT 0,
            "chromeProfilePath" TEXT NOT NULL DEFAULT '',
            "sessionName" TEXT NOT NULL UNIQUE,
            "maxPage" INTEGER NOT NULL DEFAULT 5,
            "parseMinDelayMs" INTEGER NOT NULL DEFAULT 2000,
            "parseMaxDelayMs" INTEGER NOT NULL DEFAULT 6000,
            "parseRetryBaseDelayMs" INTEGER NOT NULL DEFAULT 1000,
            "parseRetryMaxDelayMs" INTEGER NOT NULL DEFAULT 8000,
            "parseReloadRetries" INTEGER NOT NULL DEFAULT 3,
            "statePath" TEXT NOT NULL DEFAULT '',
            "reporting" TEXT NOT NULL DEFAULT '{{}}',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS profile_session_name_uq
        ON {PROFILE_TABLE}("sessionName")
        """
    )

    existing_columns = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({PROFILE_TABLE})").fetchall()
    }
    for column_name, column_ddl in PROFILE_SCHEMA_COLUMNS.items():
        if column_name in existing_columns:
            continue
        connection.execute(
            f'ALTER TABLE {PROFILE_TABLE} ADD COLUMN "{column_name}" {column_ddl}'
        )

    connection.execute(
        f"UPDATE {PROFILE_TABLE} "
        "SET created_at = ? "
        "WHERE created_at IS NULL OR created_at = ''",
        (timestamp,),
    )
    connection.execute(
        f"UPDATE {PROFILE_TABLE} "
        "SET updated_at = created_at "
        "WHERE updated_at IS NULL OR updated_at = ''",
    )
    connection.execute(
        f"UPDATE {PROFILE_TABLE} "
        "SET reporting = '{}' "
        "WHERE reporting IS NULL OR reporting = ''",
    )
    connection.execute(
        f'UPDATE {PROFILE_TABLE} SET "parseMinDelayMs" = 2000 '
        'WHERE "parseMinDelayMs" IS NULL OR "parseMinDelayMs" < 1'
    )
    connection.execute(
        f'UPDATE {PROFILE_TABLE} SET "parseMaxDelayMs" = 6000 '
        'WHERE "parseMaxDelayMs" IS NULL OR "parseMaxDelayMs" < 1'
    )
    connection.execute(
        f'UPDATE {PROFILE_TABLE} SET "parseRetryBaseDelayMs" = 1000 '
        'WHERE "parseRetryBaseDelayMs" IS NULL OR "parseRetryBaseDelayMs" < 1'
    )
    connection.execute(
        f'UPDATE {PROFILE_TABLE} SET "parseRetryMaxDelayMs" = 8000 '
        'WHERE "parseRetryMaxDelayMs" IS NULL OR "parseRetryMaxDelayMs" < 1'
    )
    connection.execute(
        f'UPDATE {PROFILE_TABLE} SET "parseReloadRetries" = 3 '
        'WHERE "parseReloadRetries" IS NULL OR "parseReloadRetries" < 1'
    )


def _ensure_jobs_schema(
    connection: sqlite3.Connection,
    *,
    timestamp: str,
) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {LINKEDIN_JOBS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            linkedin_job_id TEXT NOT NULL UNIQUE,
            source_type TEXT NOT NULL DEFAULT 'linkedin',
            company_name TEXT,
            position_name TEXT,
            job_location TEXT,
            posted_date TEXT,
            easy_apply INTEGER NOT NULL DEFAULT 0,
            apply_link TEXT,
            about_text TEXT,
            applied INTEGER NOT NULL DEFAULT 0,
            user_status TEXT NOT NULL DEFAULT 'to_be_reviewed',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            profile_id INTEGER,
            analyze_id INTEGER,
            parsed INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(profile_id) REFERENCES {PROFILE_TABLE}(id),
            FOREIGN KEY(analyze_id) REFERENCES {AI_ANALYSIS_TABLE}(id)
        )
        """
    )

    existing_columns = {
        row[1]
        for row in connection.execute(
            f"PRAGMA table_info({LINKEDIN_JOBS_TABLE})"
        ).fetchall()
    }
    for column_name, column_ddl in LINKEDIN_JOBS_SCHEMA_COLUMNS.items():
        if column_name in existing_columns:
            continue
        connection.execute(
            f"ALTER TABLE {LINKEDIN_JOBS_TABLE} ADD COLUMN {column_name} {column_ddl}"
        )

    if "profile_session_name" in existing_columns:
        connection.execute(
            f"""
            UPDATE {LINKEDIN_JOBS_TABLE}
            SET profile_id = (
                SELECT id
                FROM {PROFILE_TABLE}
                WHERE {PROFILE_TABLE}."sessionName" =
                    {LINKEDIN_JOBS_TABLE}.profile_session_name
                LIMIT 1
            )
            WHERE (profile_id IS NULL OR profile_id = 0)
              AND profile_session_name IS NOT NULL
              AND profile_session_name != ''
            """
        )
        connection.execute(
            f"ALTER TABLE {LINKEDIN_JOBS_TABLE} DROP COLUMN profile_session_name"
        )

    if "sessionName" in existing_columns:
        connection.execute(
            f"""
            UPDATE {LINKEDIN_JOBS_TABLE}
            SET profile_id = (
                SELECT id
                FROM {PROFILE_TABLE}
                WHERE {PROFILE_TABLE}."sessionName" =
                    {LINKEDIN_JOBS_TABLE}."sessionName"
                LIMIT 1
            )
            WHERE (profile_id IS NULL OR profile_id = 0)
              AND "sessionName" IS NOT NULL
              AND "sessionName" != ''
            """
        )
        connection.execute(
            f'ALTER TABLE {LINKEDIN_JOBS_TABLE} DROP COLUMN "sessionName"'
        )

    connection.execute(
        f"UPDATE {LINKEDIN_JOBS_TABLE} "
        "SET created_at = ? "
        "WHERE created_at IS NULL OR created_at = ''",
        (timestamp,),
    )
    connection.execute(
        f"UPDATE {LINKEDIN_JOBS_TABLE} "
        "SET updated_at = created_at "
        "WHERE updated_at IS NULL OR updated_at = ''",
    )
    connection.execute(
        f"UPDATE {LINKEDIN_JOBS_TABLE} SET parsed = 0 WHERE parsed IS NULL",
    )
    connection.execute(
        f"UPDATE {LINKEDIN_JOBS_TABLE} SET easy_apply = 0 WHERE easy_apply IS NULL",
    )
    connection.execute(
        f"UPDATE {LINKEDIN_JOBS_TABLE} SET applied = 0 WHERE applied IS NULL",
    )
    connection.execute(
        f"UPDATE {LINKEDIN_JOBS_TABLE} "
        "SET user_status = ? "
        "WHERE user_status IS NULL OR user_status = ''",
        (DEFAULT_USER_STATUS,),
    )
    connection.execute(
        f"UPDATE {LINKEDIN_JOBS_TABLE} "
        "SET user_status = 'not_suitable' "
        "WHERE user_status = 'dont_want'"
    )
    connection.execute(
        f"UPDATE {LINKEDIN_JOBS_TABLE} SET analyze_id = NULL WHERE analyze_id = 0",
    )
    connection.execute(
        f"UPDATE {LINKEDIN_JOBS_TABLE} "
        "SET source_type = ? "
        "WHERE source_type IS NULL OR source_type = ''",
        (SOURCE_TYPE_LINKEDIN,),
    )


def _ensure_ai_analysis_schema(
    connection: sqlite3.Connection,
    *,
    timestamp: str,
) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {AI_ANALYSIS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL UNIQUE,
            analyzed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            desicion INTEGER NOT NULL DEFAULT 0,
            reasoning TEXT DEFAULT NULL,
            FOREIGN KEY(job_id) REFERENCES {LINKEDIN_JOBS_TABLE}(id)
        )
        """
    )
    connection.execute(
        f"UPDATE {AI_ANALYSIS_TABLE} "
        "SET created_at = ? "
        "WHERE created_at IS NULL OR created_at = ''",
        (timestamp,),
    )
    connection.execute(
        f"UPDATE {AI_ANALYSIS_TABLE} "
        "SET updated_at = created_at "
        "WHERE updated_at IS NULL OR updated_at = ''",
    )
    connection.execute(
        f"UPDATE {AI_ANALYSIS_TABLE} SET analyzed = 0 WHERE analyzed IS NULL",
    )
    connection.execute(
        f"UPDATE {AI_ANALYSIS_TABLE} SET desicion = 0 WHERE desicion IS NULL",
    )


def _migrate_legacy_ai_review(
    connection: sqlite3.Connection,
    *,
    timestamp: str,
) -> None:
    has_ai_review = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (LEGACY_AI_REVIEW_TABLE,),
    ).fetchone()
    if has_ai_review is None:
        return

    connection.execute(
        f"""
        INSERT OR IGNORE INTO {AI_ANALYSIS_TABLE} (
            job_id,
            analyzed,
            created_at,
            updated_at,
            desicion,
            reasoning
        )
        SELECT
            job_id,
            CASE WHEN validated IS NULL THEN 0 ELSE validated END,
            ?,
            ?,
            CASE
                WHEN UPPER(COALESCE(desicion, '')) = 'MATCH' THEN 1
                ELSE 0
            END,
            reasoning
        FROM {LEGACY_AI_REVIEW_TABLE}
        WHERE job_id IS NOT NULL
        """,
        (timestamp, timestamp),
    )

    connection.execute(
        f"""
        UPDATE {LINKEDIN_JOBS_TABLE}
        SET analyze_id = (
            SELECT id
            FROM {AI_ANALYSIS_TABLE}
            WHERE {AI_ANALYSIS_TABLE}.job_id = {LINKEDIN_JOBS_TABLE}.id
            LIMIT 1
        )
        WHERE (analyze_id IS NULL OR analyze_id = 0)
          AND EXISTS (
              SELECT 1
              FROM {AI_ANALYSIS_TABLE}
              WHERE {AI_ANALYSIS_TABLE}.job_id = {LINKEDIN_JOBS_TABLE}.id
          )
        """
    )

    connection.execute(f"DROP TABLE {LEGACY_AI_REVIEW_TABLE}")


def _upsert_profile_row(
    connection: sqlite3.Connection,
    *,
    profile_payload: Mapping[str, Any],
    timestamp: str,
) -> int:
    session_name = _require_profile_text(profile_payload, "sessionName")
    row_values = (
        _coerce_profile_text(profile_payload.get("jobTitle")),
        _require_profile_stringish(profile_payload, "locationGeoId"),
        _require_profile_stringish(profile_payload, "f_TPR"),
        _coerce_profile_text(profile_payload.get("want")),
        _coerce_profile_text(profile_payload.get("not_want")),
        int(_require_profile_bool(profile_payload, "headless")),
        _require_profile_text(profile_payload, "chromeProfilePath"),
        session_name,
        _coerce_positive_int(profile_payload.get("maxPage", 5), field_name="maxPage"),
        _coerce_positive_int(
            profile_payload.get("parseMinDelayMs", 2000),
            field_name="parseMinDelayMs",
        ),
        _coerce_positive_int(
            profile_payload.get("parseMaxDelayMs", 6000),
            field_name="parseMaxDelayMs",
        ),
        _coerce_positive_int(
            profile_payload.get("parseRetryBaseDelayMs", 1000),
            field_name="parseRetryBaseDelayMs",
        ),
        _coerce_positive_int(
            profile_payload.get("parseRetryMaxDelayMs", 8000),
            field_name="parseRetryMaxDelayMs",
        ),
        _coerce_positive_int(
            profile_payload.get("parseReloadRetries", 3),
            field_name="parseReloadRetries",
        ),
        _coerce_profile_text(profile_payload.get("statePath")),
        "{}",  # Inert legacy column; destinations are no longer accepted.
        timestamp,
        timestamp,
    )

    profile_id = _normalize_profile_id(profile_payload.get("id"))
    if profile_id is not None:
        update_cursor = connection.execute(
            f"""
            UPDATE {PROFILE_TABLE}
            SET
                "jobTitle" = ?,
                "locationGeoId" = ?,
                "f_TPR" = ?,
                "want" = ?,
                "not_want" = ?,
                "headless" = ?,
                "chromeProfilePath" = ?,
                "sessionName" = ?,
                "maxPage" = ?,
                "parseMinDelayMs" = ?,
                "parseMaxDelayMs" = ?,
                "parseRetryBaseDelayMs" = ?,
                "parseRetryMaxDelayMs" = ?,
                "parseReloadRetries" = ?,
                "statePath" = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                row_values[0],
                row_values[1],
                row_values[2],
                row_values[3],
                row_values[4],
                row_values[5],
                row_values[6],
                row_values[7],
                row_values[8],
                row_values[9],
                row_values[10],
                row_values[11],
                row_values[12],
                row_values[13],
                row_values[14],
                row_values[17],
                profile_id,
            ),
        )
        if update_cursor.rowcount == 1:
            return profile_id

    connection.execute(
        f"""
        INSERT INTO {PROFILE_TABLE} (
            "jobTitle",
            "locationGeoId",
            "f_TPR",
            "want",
            "not_want",
            "headless",
            "chromeProfilePath",
            "sessionName",
            "maxPage",
            "parseMinDelayMs",
            "parseMaxDelayMs",
            "parseRetryBaseDelayMs",
            "parseRetryMaxDelayMs",
            "parseReloadRetries",
            "statePath",
            "reporting",
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("sessionName") DO UPDATE SET
            "jobTitle" = excluded."jobTitle",
            "locationGeoId" = excluded."locationGeoId",
            "f_TPR" = excluded."f_TPR",
            "want" = excluded."want",
            "not_want" = excluded."not_want",
            "headless" = excluded."headless",
            "chromeProfilePath" = excluded."chromeProfilePath",
            "maxPage" = excluded."maxPage",
            "parseMinDelayMs" = excluded."parseMinDelayMs",
            "parseMaxDelayMs" = excluded."parseMaxDelayMs",
            "parseRetryBaseDelayMs" = excluded."parseRetryBaseDelayMs",
            "parseRetryMaxDelayMs" = excluded."parseRetryMaxDelayMs",
            "parseReloadRetries" = excluded."parseReloadRetries",
            "statePath" = excluded."statePath",
            updated_at = excluded.updated_at
        """,
        row_values,
    )

    row = connection.execute(
        f'SELECT id FROM {PROFILE_TABLE} WHERE "sessionName" = ?',
        (session_name,),
    ).fetchone()
    if row is None:
        raise ValueError("profile upsert did not return an id")
    return int(row[0])


def _insert_new_job_rows(
    connection: sqlite3.Connection,
    *,
    job_ids: list[str],
    profile_id: int | None,
    timestamp: str,
) -> int:
    inserted_count = 0

    for job_id in job_ids:
        insert_cursor = connection.execute(
            (
                "INSERT OR IGNORE INTO "
                f"{LINKEDIN_JOBS_TABLE} "
                "(linkedin_job_id, source_type, profile_id, parsed, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)"
            ),
            (
                job_id,
                SOURCE_TYPE_LINKEDIN,
                profile_id,
                0,
                timestamp,
                timestamp,
            ),
        )
        if insert_cursor.rowcount != 1:
            continue

        inserted_count += 1

    return inserted_count


def _filter_missing_job_ids(
    connection: sqlite3.Connection,
    *,
    job_ids: list[str],
) -> list[str]:
    if not job_ids:
        return []

    placeholders = ", ".join("?" for _ in job_ids)
    rows = connection.execute(
        f"""
        SELECT linkedin_job_id
        FROM {LINKEDIN_JOBS_TABLE}
        WHERE linkedin_job_id IN ({placeholders})
        """,
        tuple(job_ids),
    ).fetchall()
    existing_job_ids = {str(row[0]).strip() for row in rows}
    return [job_id for job_id in job_ids if job_id not in existing_job_ids]


def _filter_unparsed_job_ids(
    connection: sqlite3.Connection,
    *,
    job_ids: list[str],
) -> list[str]:
    if not job_ids:
        return []

    placeholders = ", ".join("?" for _ in job_ids)
    rows = connection.execute(
        f"""
        SELECT linkedin_job_id
        FROM {LINKEDIN_JOBS_TABLE}
        WHERE linkedin_job_id IN ({placeholders})
          AND parsed = 1
        """,
        tuple(job_ids),
    ).fetchall()
    parsed_job_ids = {str(row[0]).strip() for row in rows}
    return [job_id for job_id in job_ids if job_id not in parsed_job_ids]


def _normalize_job_ids(job_ids: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for value in job_ids:
        job_id = value.strip()
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        normalized.append(job_id)

    return normalized


def _normalize_profile_id(profile_id: Any) -> int | None:
    if profile_id is None:
        return None
    if isinstance(profile_id, bool):
        raise ValueError("field 'id' must be a positive integer")
    try:
        parsed = int(profile_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("field 'id' must be a positive integer") from exc
    if parsed < 1:
        raise ValueError("field 'id' must be a positive integer")
    return parsed


def _normalize_positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"field '{field_name}' must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"field '{field_name}' must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"field '{field_name}' must be a positive integer")
    return parsed


def _normalize_binary_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"field '{field_name}' must be 0 or 1")
    if value not in (0, 1):
        raise ValueError(f"field '{field_name}' must be 0 or 1")
    return value


def _normalize_reasoning(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("field 'reasoning' must be a non-empty string")
    reasoning = value.strip()
    if not reasoning:
        raise ValueError("field 'reasoning' must be a non-empty string")
    return reasoning


def _coerce_database_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    raise ValueError("database text field must be a string")


def _require_database_text(value: Any, *, field_name: str) -> str:
    text_value = _coerce_database_text(value).strip()
    if not text_value:
        raise ValueError(f"database field '{field_name}' must be a non-empty string")
    return text_value


def _require_profile_text(
    profile_payload: Mapping[str, Any],
    field_name: str,
) -> str:
    value = profile_payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"profile field '{field_name}' must be a non-empty string")
    return value


def _require_profile_stringish(
    profile_payload: Mapping[str, Any],
    field_name: str,
) -> str:
    value = profile_payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(
            f"profile field '{field_name}' must be a non-empty string or integer"
        )
    parsed = str(value).strip()
    if not parsed:
        raise ValueError(
            f"profile field '{field_name}' must be a non-empty string or integer"
        )
    return parsed


def _require_profile_bool(
    profile_payload: Mapping[str, Any],
    field_name: str,
) -> bool:
    value = profile_payload.get(field_name)
    if not isinstance(value, bool):
        raise ValueError(f"profile field '{field_name}' must be a boolean")
    return value


def _coerce_positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"profile field '{field_name}' must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"profile field '{field_name}' must be a positive integer"
        ) from exc
    if parsed < 1:
        raise ValueError(f"profile field '{field_name}' must be a positive integer")
    return parsed


def _coerce_profile_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("profile text fields must be strings or integers")
    return str(value)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
