import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from click.testing import CliRunner

from job_search_cli.cli import main
from job_search_cli.job_store import upsert_profile
from job_search_cli.linkedin_full_flow import CommandExecution, run_linkedin_full_flow


@pytest.mark.parametrize(
    "token", ["", "private-token", 'quote"secret', "unicode-é-secret"]
)
def test_cli_generates_validated_analysis_and_summary_without_opencode(
    tmp_path, token, monkeypatch
):
    profile_id = upsert_profile(
        {
            "sessionName": "test",
            "not_want": "On call",
            "locationGeoId": "1",
            "f_TPR": "r86400",
            "headless": False,
            "chromeProfilePath": str(tmp_path / "chrome"),
        }
    )
    requests = []
    decision = {"analyzed": 1, "desicion": 1, "reasoning": token or "No on call"}
    summary = {
        "must_haves": ["Python"],
        "nice_to_haves": [],
        "company": {"name": "Acme", "industry": "Unknown"},
        "application_type": "Unknown",
    }
    output = decision
    status = 200

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            assert self.headers.get("Authorization") == (
                f"Bearer {token}" if token else None
            )
            requests.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            self.send_response(status)
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "choices": [
                            {
                                "message": {"content": json.dumps(output)},
                                "finish_reason": "stop",
                            }
                        ]
                    }
                ).encode()
            )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        with sqlite3.connect(tmp_path / "linkedin_jobs.sqlite3") as db:
            db.execute(
                "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"
            )
            db.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES ('ai_connection', ?, 'now')",
                (
                    json.dumps(
                        {
                            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                            "model": "local",
                            "token": token,
                            "revision": "1",
                            "verified_revision": "1",
                        }
                    ),
                ),
            )
            db.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES ('cv_text', 'PRIVATE-CV', 'now')"
            )
            db.execute(
                "INSERT INTO linkedin_jobs (linkedin_job_id, profile_id, company_name, about_text, parsed, created_at, updated_at) VALUES ('123', ?, 'Acme', 'Python role', 1, 'now', 'now')",
                (profile_id,),
            )
        runner = CliRunner()
        result = runner.invoke(main, ["analyze-all", "--profile-id", str(profile_id)])
        assert result.exit_code == 0, result.output
        output = summary
        result = runner.invoke(main, ["summarize-all", "--profile-id", str(profile_id)])
        assert result.exit_code == 0, result.output
        with sqlite3.connect(tmp_path / "linkedin_jobs.sqlite3") as db:
            assert db.execute("SELECT desicion FROM ai_analysis").fetchone()[0] == 1
            assert db.execute("SELECT reasoning FROM ai_analysis").fetchone()[0] == (
                "[redacted]" if token else "No on call"
            )
            assert (
                json.loads(
                    db.execute("SELECT ai_summary FROM linkedin_jobs").fetchone()[0]
                )
                == summary
            )
            db.execute("DELETE FROM ai_analysis")
            db.execute("UPDATE linkedin_jobs SET analyze_id = NULL")
        output = {**decision, "desicion": True, "reasoning": "private-token"}
        result = runner.invoke(main, ["analyze-all", "--profile-id", str(profile_id)])
        assert result.exit_code == 1
        assert "invalid analysis" in result.output
        assert "private-token" not in result.output
        with sqlite3.connect(tmp_path / "linkedin_jobs.sqlite3") as db:
            assert db.execute("SELECT count(*) FROM ai_analysis").fetchone()[0] == 0
        assert len(requests) == 3
        for request in requests:
            assert request["model"] == "local"
            assert "tools" not in request
            assert "PRIVATE-CV" not in json.dumps(request)
            assert "private-token" not in json.dumps(request)
        assert (
            json.loads(requests[0]["messages"][1]["content"])["not_want"] == "On call"
        )
        assert "not_want" not in requests[1]["messages"][1]["content"]
        with sqlite3.connect(tmp_path / "linkedin_jobs.sqlite3") as db:
            db.execute(
                "INSERT INTO ai_analysis (job_id, analyzed, desicion) SELECT id, 1, 1 FROM linkedin_jobs"
            )
            db.execute("UPDATE linkedin_jobs SET ai_summary = NULL")
        output = {**summary, "must_haves": "private-token"}
        result = runner.invoke(main, ["summarize-all", "--profile-id", str(profile_id)])
        assert result.exit_code == 1
        assert "invalid summary" in result.output
        assert "private-token" not in result.output
        status = 401
        for command in ["analyze-all", "summarize-all"]:
            with sqlite3.connect(tmp_path / "linkedin_jobs.sqlite3") as db:
                if command == "analyze-all":
                    db.execute("DELETE FROM ai_analysis")
                    db.execute("UPDATE linkedin_jobs SET analyze_id=NULL")
                else:
                    db.execute(
                        "INSERT INTO ai_analysis (job_id, analyzed, desicion) SELECT id, 1, 1 FROM linkedin_jobs"
                    )
            result = runner.invoke(main, [command, "--profile-id", str(profile_id)])
            assert result.exit_code == 1
            assert "HTTP 401" in result.output
            assert "private-token" not in result.output
        status = 200
        for auto_analyze, auto_summarize in [
            (False, False),
            (True, False),
            (False, True),
            (True, True),
        ]:
            with sqlite3.connect(tmp_path / "linkedin_jobs.sqlite3") as db:
                db.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('ai_automation', ?, 'now')",
                    (
                        json.dumps(
                            {
                                "auto_analyze": auto_analyze,
                                "auto_summarize": auto_summarize,
                            }
                        ),
                    ),
                )
                db.execute("DELETE FROM ai_analysis")
                db.execute("UPDATE linkedin_jobs SET analyze_id=NULL, ai_summary=NULL")
                if not auto_analyze:
                    db.execute(
                        "INSERT INTO ai_analysis (job_id, analyzed, desicion) SELECT id, 1, 1 FROM linkedin_jobs"
                    )
            before = len(requests)

            def workflow_command(command, cwd):
                nonlocal output
                args = command[3:]
                if args[0] == "linkedin":
                    return CommandExecution(
                        exit_code=0,
                        stdout='["123"]' if args[1] == "get-job-ids" else "ok",
                        stderr="",
                    )
                output = decision if args[0] == "analyze-all" else summary
                invoked = runner.invoke(main, args)
                return CommandExecution(
                    exit_code=invoked.exit_code, stdout=invoked.output, stderr=""
                )

            flow = run_linkedin_full_flow(
                profile_id=profile_id,
                repo_root=tmp_path,
                command_runner=workflow_command,
                report_path=tmp_path / "flow.json",
            )
            assert flow.report["exit_code"] == 0, flow.report
            assert len(requests) - before == int(auto_analyze) + int(auto_summarize)

        # A concurrent Settings edit after the readiness read must not let CLI
        # send private job context through the newly unverified connection.
        from job_search_cli import ai_client

        original_readiness = ai_client.get_ai_readiness

        def change_connection_after_readiness():
            readiness = original_readiness()
            with sqlite3.connect(tmp_path / "linkedin_jobs.sqlite3") as db:
                saved = json.loads(
                    db.execute(
                        "SELECT value FROM settings WHERE key='ai_connection'"
                    ).fetchone()[0]
                )
                saved.update(model="unverified-model", revision="changed")
                db.execute(
                    "UPDATE settings SET value=? WHERE key='ai_connection'",
                    (json.dumps(saved),),
                )
            return readiness

        with sqlite3.connect(tmp_path / "linkedin_jobs.sqlite3") as db:
            db.execute("DELETE FROM ai_analysis")
            db.execute("UPDATE linkedin_jobs SET analyze_id=NULL")
        before = len(requests)
        monkeypatch.setattr(
            ai_client, "get_ai_readiness", change_connection_after_readiness
        )
        result = runner.invoke(main, ["analyze-all", "--profile-id", str(profile_id)])
        assert result.exit_code == 1, result.output
        assert "verify" in result.output
        assert len(requests) == before
    finally:
        server.shutdown()
        worker.join()
        server.server_close()
