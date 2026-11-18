from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import job_search_cli.job_store as job_store
from job_search_cli.job_store import (
    AI_ANALYSIS_TABLE,
    filter_new_linkedin_job_ids,
    filter_unparsed_linkedin_job_ids,
    LEGACY_AI_REVIEW_TABLE,
    LINKEDIN_JOBS_TABLE,
    PendingLinkedInJob,
    PROFILE_TABLE,
    get_profile_preferences,
    list_unanalyzed_linkedin_jobs,
    save_new_linkedin_job_ids,
    upsert_ai_analysis,
    upsert_linkedin_job_details,
    upsert_profile,
)
from job_search_cli.linkedin_job import LinkedInJobDetails


def _make_profile(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "jobTitle": "Software Engineer",
        "locationGeoId": "105088894",
        "f_TPR": "r604800",
        "want": "Synthetic preference",
        "not_want": "On-site only",
        "headless": False,
        "chromeProfilePath": "/tmp/chrome/Profile 10",
        "sessionName": "linkedin-qa-bcn",
        "maxPage": 5,
        "statePath": "state/synthetic.json",
    }
    payload.update(overrides)
    return payload


def _make_job_details(job_id: str) -> LinkedInJobDetails:
    return LinkedInJobDetails(
        linkedin_job_id=job_id,
        company_name="Company A",
        position_name="QA Engineer",
        job_location="Barcelona",
        posted_date="2026-03-01",
        easy_apply=False,
        apply_link="https://example.com/apply",
        about_text="About text",
        applied=False,
    )


def test_upsert_profile_creates_row_and_returns_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "linkedin_jobs.sqlite3"
    monkeypatch.setattr(job_store, "_utc_now", lambda: "2026-03-07T10:20:30Z")

    profile_id = upsert_profile(_make_profile(), db_path=db_path)

    assert profile_id == 1

    with sqlite3.connect(db_path) as connection:
        columns = [
            row[1]
            for row in connection.execute(
                f"PRAGMA table_info({PROFILE_TABLE})"
            ).fetchall()
        ]
        row = connection.execute(
            f"""
            SELECT
                id,
                "jobTitle",
                "locationGeoId",
                "f_TPR",
                "want",
                "not_want",
                "headless",
                "chromeProfilePath",
                "sessionName",
                "maxPage",
                "statePath",
                "reporting",
                created_at,
                updated_at
            FROM {PROFILE_TABLE}
            """
        ).fetchone()

    assert columns == [
        "id",
        "jobTitle",
        "locationGeoId",
        "f_TPR",
        "f_WT",
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
        "created_at",
        "updated_at",
        "searchUrl",
    ]
    assert row is not None
    assert row[0] == 1
    assert row[1] == "Software Engineer"
    assert row[2] == "105088894"
    assert row[3] == "r604800"
    assert row[4] == "Synthetic preference"
    assert row[5] == "On-site only"
    assert row[6] == 0
    assert row[7] == "/tmp/chrome/Profile 10"
    assert row[8] == "linkedin-qa-bcn"
    assert row[9] == 5
    assert row[10] == "state/synthetic.json"
    assert json.loads(row[11]) == {}
    assert row[12] == "2026-03-07T10:20:30Z"
    assert row[13] == "2026-03-07T10:20:30Z"


def test_upsert_profile_updates_existing_row_by_session_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "linkedin_jobs.sqlite3"
    timestamps = iter(["2026-03-07T10:20:30Z", "2026-03-07T10:30:30Z"])
    monkeypatch.setattr(job_store, "_utc_now", lambda: next(timestamps))

    first_id = upsert_profile(_make_profile(), db_path=db_path)
    second_id = upsert_profile(
        _make_profile(
            want="Updated synthetic preference",
            maxPage=7,
        ),
        db_path=db_path,
    )

    assert first_id == 1
    assert second_id == 1

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            f"""
            SELECT
                id,
                "want",
                "maxPage",
                created_at,
                updated_at
            FROM {PROFILE_TABLE}
            """
        ).fetchone()

    assert row == (
        1,
        "Updated synthetic preference",
        7,
        "2026-03-07T10:20:30Z",
        "2026-03-07T10:30:30Z",
    )


def test_save_new_linkedin_job_ids_creates_schema_and_null_analyze_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "linkedin_jobs.sqlite3"
    monkeypatch.setattr(job_store, "_utc_now", lambda: "2026-03-07T10:20:30Z")
    profile_id = upsert_profile(_make_profile(), db_path=db_path)

    inserted = save_new_linkedin_job_ids(
        ["123", "321", "123"],
        db_path=db_path,
        profile_id=profile_id,
    )

    assert inserted == 2

    with sqlite3.connect(db_path) as connection:
        job_columns = [
            row[1]
            for row in connection.execute(
                f"PRAGMA table_info({LINKEDIN_JOBS_TABLE})"
            ).fetchall()
        ]
        analysis_columns = [
            row[1]
            for row in connection.execute(
                f"PRAGMA table_info({AI_ANALYSIS_TABLE})"
            ).fetchall()
        ]
        job_rows = connection.execute(
            f"""
            SELECT
                linkedin_job_id,
                source_type,
                profile_id,
                analyze_id,
                parsed,
                applied,
                user_status,
                created_at,
                updated_at
            FROM {LINKEDIN_JOBS_TABLE}
            ORDER BY id
            """
        ).fetchall()
        analysis_rows = connection.execute(
            f"SELECT id, job_id, analyzed, desicion, reasoning FROM {AI_ANALYSIS_TABLE}"
        ).fetchall()

    assert job_columns == [
        "id",
        "linkedin_job_id",
        "source_type",
        "company_name",
        "position_name",
        "job_location",
        "posted_date",
        "easy_apply",
        "apply_link",
        "about_text",
        "applied",
        "user_status",
        "created_at",
        "updated_at",
        "profile_id",
        "analyze_id",
        "parsed",
        "ai_summary",
    ]
    assert analysis_columns == [
        "id",
        "job_id",
        "analyzed",
        "created_at",
        "updated_at",
        "desicion",
        "reasoning",
    ]
    assert job_rows == [
        (
            "123",
            "linkedin",
            profile_id,
            None,
            0,
            0,
            "to_be_reviewed",
            "2026-03-07T10:20:30Z",
            "2026-03-07T10:20:30Z",
        ),
        (
            "321",
            "linkedin",
            profile_id,
            None,
            0,
            0,
            "to_be_reviewed",
            "2026-03-07T10:20:30Z",
            "2026-03-07T10:20:30Z",
        ),
    ]
    assert analysis_rows == []


def test_filter_new_linkedin_job_ids_returns_only_missing_ids(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "linkedin_jobs.sqlite3"
    monkeypatch.setattr(job_store, "_utc_now", lambda: "2026-03-07T10:20:30Z")
    profile_id = upsert_profile(_make_profile(), db_path=db_path)
    save_new_linkedin_job_ids(["123", "321"], db_path=db_path, profile_id=profile_id)

    new_job_ids = filter_new_linkedin_job_ids(
        ["123", "555", "321", "777", "555"],
        db_path=db_path,
    )

    assert new_job_ids == ["555", "777"]


def test_filter_unparsed_linkedin_job_ids_skips_only_already_parsed_ids(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "linkedin_jobs.sqlite3"
    timestamps = iter(
        [
            "2026-03-07T10:20:30Z",
            "2026-03-07T10:21:30Z",
            "2026-03-07T10:22:30Z",
            "2026-03-07T10:23:30Z",
        ]
    )
    monkeypatch.setattr(job_store, "_utc_now", lambda: next(timestamps))
    profile_id = upsert_profile(_make_profile(), db_path=db_path)
    save_new_linkedin_job_ids(["123", "321"], db_path=db_path, profile_id=profile_id)
    upsert_linkedin_job_details(
        _make_job_details("123"),
        db_path=db_path,
        profile_id=profile_id,
    )

    unparsed_job_ids = filter_unparsed_linkedin_job_ids(
        ["123", "555", "321", "777", "555"],
        db_path=db_path,
    )

    assert unparsed_job_ids == ["555", "321", "777"]


def test_upsert_linkedin_job_details_keeps_existing_profile_id_when_none_passed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "linkedin_jobs.sqlite3"
    timestamps = iter(
        [
            "2026-03-07T10:20:30Z",
            "2026-03-07T10:21:30Z",
            "2026-03-07T10:22:30Z",
            "2026-03-07T10:23:30Z",
        ]
    )
    monkeypatch.setattr(job_store, "_utc_now", lambda: next(timestamps))
    profile_id = upsert_profile(_make_profile(), db_path=db_path)
    save_new_linkedin_job_ids(["102"], db_path=db_path, profile_id=profile_id)

    first_changes = upsert_linkedin_job_details(
        _make_job_details("102"),
        db_path=db_path,
        profile_id=profile_id,
    )
    second_changes = upsert_linkedin_job_details(
        _make_job_details("102"),
        db_path=db_path,
    )

    assert first_changes == 1
    assert second_changes == 1

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            f"""
            SELECT
                linkedin_job_id,
                profile_id,
                analyze_id,
                parsed,
                applied,
                user_status,
                created_at,
                updated_at
            FROM {LINKEDIN_JOBS_TABLE}
            """
        ).fetchone()

    assert row == (
        "102",
        profile_id,
        None,
        1,
        0,
        "to_be_reviewed",
        "2026-03-07T10:21:30Z",
        "2026-03-07T10:23:30Z",
    )


def test_upsert_linkedin_job_details_migrates_profile_session_name_to_profile_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "linkedin_jobs.sqlite3"
    monkeypatch.setattr(job_store, "_utc_now", lambda: "2026-03-07T10:20:30Z")

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"""
            CREATE TABLE {PROFILE_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                "sessionName" TEXT NOT NULL UNIQUE
            )
            """
        )
        connection.execute(
            f'INSERT INTO {PROFILE_TABLE} ("sessionName") VALUES (?)',
            ("linkedin-qa-bcn",),
        )
        profile_id = connection.execute(
            f'SELECT id FROM {PROFILE_TABLE} WHERE "sessionName" = ?',
            ("linkedin-qa-bcn",),
        ).fetchone()[0]
        connection.execute(
            f"""
            CREATE TABLE {LINKEDIN_JOBS_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                linkedin_job_id TEXT NOT NULL UNIQUE,
                profile_session_name TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            f"""
            INSERT INTO {LINKEDIN_JOBS_TABLE} (
                linkedin_job_id,
                profile_session_name
            )
            VALUES (?, ?)
            """,
            ("101", "linkedin-qa-bcn"),
        )

    changes = upsert_linkedin_job_details(
        _make_job_details("101"),
        db_path=db_path,
    )

    assert changes == 1

    with sqlite3.connect(db_path) as connection:
        columns = [
            row[1]
            for row in connection.execute(
                f"PRAGMA table_info({LINKEDIN_JOBS_TABLE})"
            ).fetchall()
        ]
        row = connection.execute(
            f"""
            SELECT
                linkedin_job_id,
                source_type,
                profile_id,
                analyze_id,
                parsed,
                applied,
                user_status,
                created_at,
                updated_at
            FROM {LINKEDIN_JOBS_TABLE}
            """
        ).fetchone()

    assert "profile_session_name" not in columns
    assert "profile_id" in columns
    assert "analyze_id" in columns
    assert row == (
        "101",
        "linkedin",
        profile_id,
        None,
        1,
        0,
        "to_be_reviewed",
        "2026-03-07T10:20:30Z",
        "2026-03-07T10:20:30Z",
    )


def test_upsert_linkedin_job_details_keeps_existing_user_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "linkedin_jobs.sqlite3"
    timestamps = iter(
        [
            "2026-03-07T10:20:30Z",
            "2026-03-07T10:21:30Z",
            "2026-03-07T10:22:30Z",
        ]
    )
    monkeypatch.setattr(job_store, "_utc_now", lambda: next(timestamps))
    profile_id = upsert_profile(_make_profile(), db_path=db_path)
    save_new_linkedin_job_ids(["102"], db_path=db_path, profile_id=profile_id)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE {LINKEDIN_JOBS_TABLE} SET user_status = ? WHERE linkedin_job_id = ?",
            ("want", "102"),
        )

    changes = upsert_linkedin_job_details(
        _make_job_details("102"),
        db_path=db_path,
        profile_id=profile_id,
    )

    assert changes == 1

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            f"SELECT user_status FROM {LINKEDIN_JOBS_TABLE} WHERE linkedin_job_id = ?",
            ("102",),
        ).fetchone()

    assert row == ("want",)


def test_upsert_linkedin_job_details_persists_applied_flag(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "linkedin_jobs.sqlite3"
    monkeypatch.setattr(job_store, "_utc_now", lambda: "2026-03-07T10:20:30Z")
    profile_id = upsert_profile(_make_profile(), db_path=db_path)

    changes = upsert_linkedin_job_details(
        LinkedInJobDetails(
            linkedin_job_id="103",
            company_name="Example Company",
            position_name="Software Tester",
            job_location="European Economic Area",
            posted_date="2026-03-06",
            easy_apply=False,
            apply_link="https://www.linkedin.com/jobs/view/103",
            about_text="About text",
            applied=True,
        ),
        db_path=db_path,
        profile_id=profile_id,
    )

    assert changes == 1

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            f"SELECT linkedin_job_id, applied, profile_id FROM {LINKEDIN_JOBS_TABLE}"
        ).fetchone()

    assert row == ("103", 1, profile_id)


def test_save_new_linkedin_job_ids_migrates_legacy_ai_review_data(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "linkedin_jobs.sqlite3"
    monkeypatch.setattr(job_store, "_utc_now", lambda: "2026-03-07T10:20:30Z")

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"""
            CREATE TABLE {LINKEDIN_JOBS_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                linkedin_job_id TEXT NOT NULL UNIQUE
            )
            """
        )
        connection.execute(
            f"INSERT INTO {LINKEDIN_JOBS_TABLE} (linkedin_job_id) VALUES (?)",
            ("999",),
        )
        job_id = connection.execute(
            f"SELECT id FROM {LINKEDIN_JOBS_TABLE} WHERE linkedin_job_id = ?",
            ("999",),
        ).fetchone()[0]
        connection.execute(
            f"""
            CREATE TABLE {LEGACY_AI_REVIEW_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL UNIQUE,
                validated INTEGER NOT NULL DEFAULT 0,
                desicion TEXT,
                reasoning TEXT
            )
            """
        )
        connection.execute(
            f"""
            INSERT INTO {LEGACY_AI_REVIEW_TABLE} (job_id, validated, desicion, reasoning)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, 1, "MATCH", "Good fit"),
        )

    inserted = save_new_linkedin_job_ids([], db_path=db_path)

    assert inserted == 0

    with sqlite3.connect(db_path) as connection:
        legacy_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (LEGACY_AI_REVIEW_TABLE,),
        ).fetchone()
        analysis_row = connection.execute(
            f"""
            SELECT id, job_id, analyzed, desicion, reasoning, created_at, updated_at
            FROM {AI_ANALYSIS_TABLE}
            """
        ).fetchone()
        job_row = connection.execute(
            f"SELECT id, analyze_id FROM {LINKEDIN_JOBS_TABLE} WHERE id = ?",
            (job_id,),
        ).fetchone()

    assert legacy_exists is None
    assert analysis_row is not None
    assert analysis_row[1] == job_id
    assert analysis_row[2] == 1
    assert analysis_row[3] == 1
    assert analysis_row[4] == "Good fit"
    assert analysis_row[5] == "2026-03-07T10:20:30Z"
    assert analysis_row[6] == "2026-03-07T10:20:30Z"
    assert job_row == (job_id, analysis_row[0])


def test_get_profile_preferences_returns_want_and_not_want(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "linkedin_jobs.sqlite3"
    monkeypatch.setattr(job_store, "_utc_now", lambda: "2026-03-07T10:20:30Z")

    profile_id = upsert_profile(
        _make_profile(
            want="Synthetic preference",
            not_want="On-site only",
        ),
        db_path=db_path,
    )

    preferences = get_profile_preferences(profile_id, db_path=db_path)

    assert preferences == ("Synthetic preference", "On-site only")


def test_save_new_linkedin_job_ids_migrates_dont_want_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "linkedin_jobs.sqlite3"
    monkeypatch.setattr(job_store, "_utc_now", lambda: "2026-03-07T10:20:30Z")

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"""
            CREATE TABLE {LINKEDIN_JOBS_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                linkedin_job_id TEXT NOT NULL UNIQUE,
                user_status TEXT NOT NULL DEFAULT 'to_be_reviewed',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            f"""
            INSERT INTO {LINKEDIN_JOBS_TABLE} (
                linkedin_job_id,
                user_status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?)
            """,
            ("legacy-123", "dont_want", "2026-03-07T10:00:00Z", "2026-03-07T10:00:00Z"),
        )

    inserted = save_new_linkedin_job_ids([], db_path=db_path)

    assert inserted == 0

    with sqlite3.connect(db_path) as connection:
        user_status = connection.execute(
            f"SELECT user_status FROM {LINKEDIN_JOBS_TABLE} WHERE linkedin_job_id = ?",
            ("legacy-123",),
        ).fetchone()[0]

    assert user_status == "not_suitable"


def test_list_unanalyzed_jobs_and_upsert_ai_analysis_link_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "linkedin_jobs.sqlite3"
    timestamps = iter(
        [
            "2026-03-07T10:20:30Z",
            "2026-03-07T10:21:30Z",
            "2026-03-07T10:22:30Z",
            "2026-03-07T10:23:30Z",
            "2026-03-07T10:24:30Z",
            "2026-03-07T10:25:30Z",
        ]
    )
    monkeypatch.setattr(job_store, "_utc_now", lambda: next(timestamps))

    profile_id = upsert_profile(_make_profile(), db_path=db_path)
    upsert_linkedin_job_details(
        LinkedInJobDetails(
            linkedin_job_id="4381111111",
            company_name="Acme QA",
            position_name="QA Engineer",
            job_location="Barcelona",
            posted_date="2026-03-08",
            easy_apply=True,
            apply_link="https://www.linkedin.com/jobs/view/4381111111/",
            about_text="Full description",
        ),
        db_path=db_path,
        profile_id=profile_id,
    )
    upsert_linkedin_job_details(
        LinkedInJobDetails(
            linkedin_job_id="4382222222",
            company_name="Beta QA",
            position_name="Manual QA Engineer",
            job_location="Remote",
            posted_date="2026-03-07",
            easy_apply=False,
            apply_link="https://www.linkedin.com/jobs/view/4382222222/",
            about_text="Second description",
        ),
        db_path=db_path,
        profile_id=profile_id,
    )

    pending_before = list_unanalyzed_linkedin_jobs(profile_id, db_path=db_path)

    assert len(pending_before) == 2
    assert all(isinstance(job, PendingLinkedInJob) for job in pending_before)
    assert pending_before[0].linkedin_job_id == "4381111111"
    assert pending_before[0].company_name == "Acme QA"
    assert pending_before[0].position_name == "QA Engineer"
    assert pending_before[0].job_location == "Barcelona"
    assert pending_before[0].posted_date == "2026-03-08"
    assert pending_before[0].easy_apply is True
    assert (
        pending_before[0].apply_link == "https://www.linkedin.com/jobs/view/4381111111/"
    )
    assert pending_before[0].about_text == "Full description"

    first_job_id = pending_before[0].id
    analysis_id = upsert_ai_analysis(
        job_id=first_job_id,
        analyzed=1,
        desicion=1,
        reasoning="Matches want criteria.",
        db_path=db_path,
    )

    assert analysis_id >= 1

    pending_after = list_unanalyzed_linkedin_jobs(profile_id, db_path=db_path)
    assert [job.linkedin_job_id for job in pending_after] == ["4382222222"]

    with sqlite3.connect(db_path) as connection:
        analysis_row = connection.execute(
            f"""
            SELECT job_id, analyzed, desicion, reasoning
            FROM {AI_ANALYSIS_TABLE}
            WHERE id = ?
            """,
            (analysis_id,),
        ).fetchone()
        analyzed_job_row = connection.execute(
            f"SELECT analyze_id FROM {LINKEDIN_JOBS_TABLE} WHERE id = ?",
            (first_job_id,),
        ).fetchone()

    assert analysis_row == (first_job_id, 1, 1, "Matches want criteria.")
    assert analyzed_job_row == (analysis_id,)
