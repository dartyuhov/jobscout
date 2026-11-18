"""Browser integration at the portal's existing HTTP/UI boundary."""

import json
import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

import pytest
from playwright.sync_api import expect, sync_playwright


@pytest.mark.parametrize("settings_flow", [False, True])
def test_unconfigured_portal_shows_unanalyzed_jobs_and_disables_ai(settings_flow):
    repo = Path(__file__).resolve().parents[1]
    if not (repo / "frontend/node_modules/vite/bin/vite.js").exists():
        pytest.skip("Portal browser checks require frontend npm dependencies")
    with sync_playwright() as playwright:
        executable = os.environ.get(
            "PLAYWRIGHT_CHROMIUM_EXECUTABLE", playwright.chromium.executable_path
        )
        if not Path(executable).exists():
            pytest.skip(
                "Install Playwright Chromium or set PLAYWRIGHT_CHROMIUM_EXECUTABLE"
            )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = subprocess.Popen(
        [
            "node",
            "node_modules/vite/bin/vite.js",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=repo / "frontend",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                with urlopen(url, timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        requests = []
        ai = {
            "base_url": "",
            "model": "",
            "token_present": False,
            "revision": None,
            "ready": False,
            "auto_analyze": False,
            "auto_summarize": False,
        }
        saved_bodies = []
        job = {
            "id": 1,
            "position_name": "Unanalyzed Engineer",
            "company_name": "Example Co",
            "about_text": "Build useful software.",
            "user_status": "to_be_reviewed",
            "linkedin_job_id": "100",
        }

        def api(route):
            request = route.request
            requests.append((request.method, request.url))
            path = request.url.split("/api/", 1)[1].split("?", 1)[0]
            if path == "settings/ai" and request.method == "PUT":
                body = request.post_data_json
                saved_bodies.append(body)
                ai.update(
                    base_url=body["base_url"],
                    model=body["model"],
                    revision="saved-revision",
                    ready=False,
                    token_present=bool(body.get("token", ai["token_present"])),
                )
            if path == "settings/ai/test":
                assert request.post_data_json == {"revision": "saved-revision"}
                ai["ready"] = True
            if path == "settings/ai/automation":
                ai.update(request.post_data_json)
            payload = {
                "health": {"status": "ok", "ai": {"ready": False}},
                "profiles": {"profiles": []},
                "jobs": {"jobs": [job], "total": 1, "total_pages": 1},
                "jobs/1": {"job": job},
                "settings/cv": {"cv_text": "Synthetic CV"},
                "settings/ai": dict(ai),
                "settings/ai/test": dict(ai),
                "settings/ai/automation": dict(ai),
            }.get(path, {})
            route.fulfill(content_type="application/json", body=json.dumps(payload))

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
            )
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route(f"{url}/api/**", api)
            if settings_flow:
                page.goto(f"{url}/settings")
                expect(page.get_by_label("AI base URL", exact=True)).to_be_visible()
                expect(
                    page.get_by_role("button", name="Test saved connection")
                ).to_be_disabled()
                page.get_by_label("AI base URL", exact=True).fill(
                    "http://localhost:1234/v1"
                )
                page.get_by_label("AI model", exact=True).fill("local-model")
                page.get_by_label("AI token (optional)", exact=True).fill(
                    "synthetic-ui-token"
                )
                page.get_by_role(
                    "button", name="Save AI connection", exact=True
                ).click()
                expect(page.get_by_text("Token saved", exact=True)).to_be_visible()
                expect(
                    page.get_by_label("AI token (optional)", exact=True)
                ).to_have_value("")
                assert saved_bodies[-1]["token"] == "synthetic-ui-token"
                page.get_by_role("button", name="Test saved connection").click()
                expect(
                    page.get_by_text("AI connection verified", exact=True)
                ).to_be_visible()
                analysis = page.get_by_label("Automatically analyze collected jobs")
                summaries = page.get_by_label(
                    "Automatically summarize matched jobs", exact=False
                )
                expect(analysis).not_to_be_checked()
                expect(summaries).not_to_be_checked()
                analysis.check()
                expect(analysis).to_be_checked()
                expect(summaries).not_to_be_checked()
                summaries.check()
                expect(summaries).to_be_checked()
                analysis.uncheck()
                expect(analysis).not_to_be_checked()
                expect(summaries).to_be_checked()
                page.get_by_role("button", name="Ask AI", exact=True).first.click()
                expect(
                    page.get_by_placeholder("Ask about your CV", exact=False)
                ).to_be_enabled()
                page.get_by_role("button", name="Settings", exact=True).click()
                expect(page.get_by_text("Token saved", exact=True)).to_be_visible()
                page.get_by_label("AI model", exact=True).fill("changed-model")
                expect(
                    page.get_by_role("button", name="Test saved connection")
                ).to_be_disabled()
                page.get_by_role(
                    "button", name="Save AI connection", exact=True
                ).click()
                expect(
                    page.get_by_text("Saved connection needs verification", exact=True)
                ).to_be_visible()
                assert "token" not in saved_bodies[-1]
                page.get_by_role(
                    "button", name="Remove saved token", exact=True
                ).click()
                expect(page.get_by_text("No token saved", exact=True)).to_be_visible()
                assert saved_bodies[-1]["token"] == ""
                assert "synthetic-ui-token" not in page.evaluate(
                    "JSON.stringify([localStorage, sessionStorage])"
                )
                assert not errors, errors
                browser.close()
                return
            page.goto(f"{url}/jobs")
            page.wait_for_load_state("networkidle")
            page.get_by_role(
                "button", name="Open job details for Unanalyzed Engineer"
            ).first.click()
            expect(
                page.get_by_role("button", name="Cover Letter", exact=True)
            ).to_be_visible()
            for name in ("Ask AI", "Cover Letter", "Summarize", "Analyze Job"):
                expect(
                    page.get_by_role("button", name=name, exact=True).last
                ).to_be_disabled()
            assert page.get_by_text(
                "Configure and verify an AI connection in Settings", exact=False
            ).is_visible()
            assert page.get_by_role("button", name="Applied", exact=True).is_enabled()
            assert all(
                "match_only=true" not in request_url for _, request_url in requests
            )
            page.goto(f"{url}/ask-ai")
            assert page.get_by_role(
                "button", name="Ask AI", exact=True
            ).last.is_disabled()
            assert page.get_by_text(
                "Configure and verify an AI connection in Settings", exact=False
            ).is_visible()
            assert page.get_by_role(
                "button", name="Open Settings", exact=True
            ).first.is_enabled()
            page.get_by_role("button", name="Open Settings", exact=True).first.click()
            assert page.url.endswith("/settings")
            expect(page.get_by_text("AI base URL", exact=True)).to_be_visible()
            assert not [
                (method, target) for method, target in requests if method == "POST"
            ]
            assert not errors, errors
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
