"""Keep every test away from the developer's runtime storage."""

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_SEARCH_DATA_DIR", str(tmp_path / "user-data"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "linkedin_jobs.sqlite3"))
    monkeypatch.setenv("SESSIONS_PATH", str(tmp_path / "sessions.json"))
