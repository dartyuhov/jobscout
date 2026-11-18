from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from job_search_cli.cli import main
from job_search_cli.job_store import (
    PendingLinkedInJob,
    PendingLinkedInSummaryJob,
    upsert_profile,
)
from job_search_cli.ai_client import AiGenerationError


@pytest.fixture(autouse=True)
def verified_ai(monkeypatch):
    from job_search_cli.ai_readiness import AiReadiness

    monkeypatch.setattr(
        "job_search_cli.cli.get_ai_readiness", lambda: AiReadiness(ready=True)
    )


@pytest.mark.parametrize("command", ["analyze-all", "summarize-all"])
@pytest.mark.parametrize("use_profile_id", [False, True])
def test_unconfigured_manual_ai_explains_setup_without_connections(
    command, use_profile_id, tmp_path, monkeypatch
):
    from job_search_cli.ai_readiness import get_ai_readiness

    monkeypatch.setattr("job_search_cli.cli.get_ai_readiness", get_ai_readiness)
    monkeypatch.setenv("OPENCODE_SERVER_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(
        "job_search_cli.cli.read_connection",
        lambda: pytest.fail("Unconfigured CLI must not read a private connection"),
    )
    profile = tmp_path / "selector.json"
    profile.write_text(json.dumps({"id": 99}), encoding="utf-8")
    before = profile.read_bytes()
    selection = (
        ["--profile-id", "99"] if use_profile_id else ["--profile", str(profile)]
    )
    result = CliRunner().invoke(main, [command, *selection])
    assert result.exit_code == 1
    assert "Configure and verify an AI connection in Settings" in result.output
    assert profile.read_bytes() == before
    assert not (tmp_path / "linkedin_jobs.sqlite3").exists()


def _write_profile(tmp_path: Path, *, profile_id: int | None = None) -> Path:
    payload: dict[str, object] = {
        "chromeProfilePath": "/tmp/chrome/Profile 10",
        "sessionName": "linkedin-qa-bcn",
        "headless": False,
        "locationGeoId": "105088894",
        "f_TPR": "r604800",
    }
    if profile_id is not None:
        payload["id"] = profile_id

    profile_path = tmp_path / "profile.json"
    upsert_profile(payload)
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    return profile_path


def _make_pending_job(
    *,
    job_id: int,
    linkedin_job_id: str,
    about_text: str = "About text",
) -> PendingLinkedInJob:
    return PendingLinkedInJob(
        id=job_id,
        linkedin_job_id=linkedin_job_id,
        company_name="Acme QA",
        position_name="QA Engineer",
        job_location="Barcelona, Catalonia, Spain",
        posted_date="2026-03-08",
        easy_apply=True,
        apply_link=f"https://www.linkedin.com/jobs/view/{linkedin_job_id}/",
        about_text=about_text,
    )


def _make_server_handle() -> dict[str, str]:
    return {"base_url": "http://127.0.0.1:1/v1", "model": "local"}


def _make_summary_job(
    *, job_id: int, linkedin_job_id: str
) -> PendingLinkedInSummaryJob:
    return PendingLinkedInSummaryJob(
        id=job_id,
        linkedin_job_id=linkedin_job_id,
        company_name="Acme QA",
        position_name="QA Engineer",
        about_text="About text",
    )


def test_analyze_all_exits_successfully_when_no_pending_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    profile_path = _write_profile(tmp_path)

    monkeypatch.setattr(
        "job_search_cli.cli.get_profile_preferences",
        lambda profile_id: ("remote", "onsite only"),
    )
    monkeypatch.setattr(
        "job_search_cli.cli.list_unanalyzed_linkedin_jobs",
        lambda profile_id: [],
    )
    monkeypatch.setattr(
        "job_search_cli.cli.read_connection",
        lambda: (_ for _ in ()).throw(
            AssertionError("read_connection should not be called")
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["analyze-all", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "No pending jobs to analyze." in result.output


def test_analyze_all_uses_connection_and_persists_all_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    profile_path = _write_profile(tmp_path)
    pending_jobs = [
        _make_pending_job(job_id=11, linkedin_job_id="4380000001", about_text="First"),
        _make_pending_job(job_id=12, linkedin_job_id="4380000002", about_text="Second"),
    ]
    server = _make_server_handle()

    captured_jobs: list[PendingLinkedInJob] = []
    persisted_rows: list[tuple[int, int, int, str]] = []

    monkeypatch.setattr(
        "job_search_cli.cli.get_profile_preferences",
        lambda profile_id: (
            "Hybrid role in Barcelona, remote",
            "On-site only",
        ),
    )
    monkeypatch.setattr(
        "job_search_cli.cli.list_unanalyzed_linkedin_jobs",
        lambda profile_id: pending_jobs,
    )
    monkeypatch.setattr("job_search_cli.cli.read_connection", lambda: server)

    def fake_analyze_job(open_server, *, not_want, job):
        assert open_server is server
        assert not_want == "On-site only"
        captured_jobs.append(job)
        return {
            "analyzed": 1,
            "desicion": 1 if job.linkedin_job_id.endswith("1") else 0,
            "reasoning": f"analysis for {job.linkedin_job_id}",
        }

    monkeypatch.setattr(
        "job_search_cli.cli.analyze_job",
        fake_analyze_job,
    )
    monkeypatch.setattr(
        "job_search_cli.cli.upsert_ai_analysis",
        lambda **kwargs: (
            persisted_rows.append(
                (
                    kwargs["job_id"],
                    kwargs["analyzed"],
                    kwargs["desicion"],
                    kwargs["reasoning"],
                )
            )
            or kwargs["job_id"]
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["analyze-all", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "Analyzed LinkedIn job '4380000001' (desicion=1)." in result.output
    assert "Analyzed LinkedIn job '4380000002' (desicion=0)." in result.output
    assert "Analyzed 2 pending job(s)." in result.output
    captured_by_id = {job.linkedin_job_id: job for job in captured_jobs}
    assert set(captured_by_id) == {"4380000001", "4380000002"}
    assert captured_by_id["4380000001"].company_name == "Acme QA"
    assert captured_by_id["4380000001"].position_name == "QA Engineer"
    assert captured_by_id["4380000001"].job_location == "Barcelona, Catalonia, Spain"
    assert captured_by_id["4380000001"].posted_date == "2026-03-08"
    assert captured_by_id["4380000001"].easy_apply is True
    assert (
        captured_by_id["4380000001"].apply_link
        == "https://www.linkedin.com/jobs/view/4380000001/"
    )
    assert captured_by_id["4380000001"].about_text == "First"
    assert persisted_rows == [
        (11, 1, 1, "analysis for 4380000001"),
        (12, 1, 0, "analysis for 4380000002"),
    ]


def test_analyze_all_surfaces_analysis_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    profile_path = _write_profile(tmp_path)
    pending_jobs = [_make_pending_job(job_id=11, linkedin_job_id="4380000001")]
    server = _make_server_handle()

    monkeypatch.setattr(
        "job_search_cli.cli.get_profile_preferences",
        lambda profile_id: ("remote", "onsite only"),
    )
    monkeypatch.setattr(
        "job_search_cli.cli.list_unanalyzed_linkedin_jobs",
        lambda profile_id: pending_jobs,
    )
    monkeypatch.setattr("job_search_cli.cli.read_connection", lambda: server)
    monkeypatch.setattr(
        "job_search_cli.cli.analyze_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AiGenerationError("AI returned invalid payload.")
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["analyze-all", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code != 0
    assert "AI returned invalid payload." in result.output


def test_summarize_all_exits_successfully_when_no_pending_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    profile_path = _write_profile(tmp_path)

    monkeypatch.setattr(
        "job_search_cli.cli.list_unsummarized_matched_linkedin_jobs",
        lambda profile_id: [],
    )
    monkeypatch.setattr(
        "job_search_cli.cli.read_connection",
        lambda: (_ for _ in ()).throw(
            AssertionError("read_connection should not be called")
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["summarize-all", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "No matched jobs to summarize." in result.output


def test_summarize_all_persists_all_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    profile_path = _write_profile(tmp_path)
    jobs = [
        _make_summary_job(job_id=11, linkedin_job_id="4380000001"),
        _make_summary_job(job_id=12, linkedin_job_id="4380000002"),
    ]
    server = _make_server_handle()
    persisted_rows: list[tuple[int, dict[str, object]]] = []

    monkeypatch.setattr(
        "job_search_cli.cli.list_unsummarized_matched_linkedin_jobs",
        lambda profile_id: jobs,
    )
    monkeypatch.setattr("job_search_cli.cli.read_connection", lambda: server)
    monkeypatch.setattr(
        "job_search_cli.cli.summarize_job",
        lambda open_server, *, job: {
            "must_haves": [job.linkedin_job_id],
            "nice_to_haves": [],
            "company": {"name": job.company_name, "industry": "Technology"},
            "application_type": "backend",
        },
    )
    monkeypatch.setattr(
        "job_search_cli.cli.upsert_ai_summary",
        lambda *, job_id, summary_payload: (
            persisted_rows.append((job_id, summary_payload)) or 1
        ),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main,
        ["summarize-all", "--profile", str(profile_path)],
        prog_name="job-search",
    )

    assert result.exit_code == 0
    assert "Summarized LinkedIn job '4380000001'." in result.output
    assert "Summarized LinkedIn job '4380000002'." in result.output
    assert "Summarized 2 matched job(s)." in result.output
    assert persisted_rows == [
        (
            11,
            {
                "must_haves": ["4380000001"],
                "nice_to_haves": [],
                "company": {"name": "Acme QA", "industry": "Technology"},
                "application_type": "backend",
            },
        ),
        (
            12,
            {
                "must_haves": ["4380000002"],
                "nice_to_haves": [],
                "company": {"name": "Acme QA", "industry": "Technology"},
                "application_type": "backend",
            },
        ),
    ]
