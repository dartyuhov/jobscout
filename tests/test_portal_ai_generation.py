"""Generation-only Ask AI behavior at the portal UI boundary."""

import json
import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

import pytest
from playwright.sync_api import expect, sync_playwright


def test_ask_ai_explicit_context_conversation_and_errors():
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
        chats = []
        cv = ""
        ready = True
        fail = False
        job = {
            "id": 1,
            "position_name": "Selected Engineer",
            "company_name": "Example Co",
            "about_text": "Build software.",
        }

        def api(route):
            path = route.request.url.split("/api/", 1)[1].split("?", 1)[0]
            payload = {
                "health": {
                    "ai": {"ready": ready, "guidance": "Configure AI in Settings."}
                },
                "profiles": {"profiles": []},
                "jobs": {"jobs": [job], "total": 1},
                "jobs/1": {"job": job},
                "settings/cv": {"cv_text": cv},
            }.get(path, {})
            if path == "ai/chat":
                chats.append(route.request.post_data_json)
                payload = (
                    {"error": "AI generation failed (HTTP 429). Retry later."}
                    if fail
                    else {"reply": "Visible generated answer"}
                )
            route.fulfill(
                status=502 if path == "ai/chat" and fail else 200,
                content_type="application/json",
                body=json.dumps(payload),
            )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=executable)
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            documents = []
            page.on(
                "request",
                lambda request: (
                    documents.append(request.url)
                    if request.is_navigation_request()
                    and request.frame == page.main_frame
                    else None
                ),
            )
            page.route(f"{url}/api/**", api)
            page.goto(f"{url}/ask-ai")
            composer = page.get_by_role("textbox", name="Your message")
            expect(composer).to_be_enabled()
            composer.fill("What is an interview?")
            page.get_by_role("button", name="Ask AI", exact=True).last.click()
            try:
                expect(
                    page.get_by_text("Visible generated answer", exact=True)
                ).to_be_visible()
            except AssertionError:
                assert len(documents) == 1, (
                    f"Unexpected document reload cleared Ask AI: {documents}. "
                    "Use Playwright Chromium; Helium's bundled uBlock can reload "
                    "fresh-profile tabs during startup (imputnet/helium#2891)."
                )
                assert not errors, errors
                raise
            assert len(documents) == 1, documents
            assert len(chats) == 1
            assert chats[-1]["include_cv"] is False
            composer.fill("How should I prepare?")
            page.get_by_role("button", name="Ask AI", exact=True).last.click()
            expect(
                page.get_by_text("Visible generated answer", exact=True)
            ).to_have_count(2)
            assert chats[-1]["messages"] == [
                {"role": "user", "content": "What is an interview?"},
                {"role": "assistant", "content": "Visible generated answer"},
                {"role": "user", "content": "How should I prepare?"},
            ]
            cv = "Synthetic UI CV"
            page.reload()
            checkbox = page.get_by_role("checkbox", name="Include my CV")
            expect(checkbox).not_to_be_checked()
            expect(checkbox).to_be_enabled()
            checkbox.check()
            composer.fill("Compare my experience")
            page.get_by_role("button", name="Ask AI", exact=True).last.click()
            expect(
                page.get_by_text("Visible generated answer", exact=True)
            ).to_be_visible()
            assert chats[-1]["include_cv"] is True
            checkbox.uncheck()
            composer.fill("A general question without private history")
            page.get_by_role("button", name="Ask AI", exact=True).last.click()
            expect(
                page.get_by_text("Visible generated answer", exact=True)
            ).to_have_count(1)
            assert chats[-1]["include_cv"] is False
            assert chats[-1]["messages"] == [
                {
                    "role": "user",
                    "content": "A general question without private history",
                }
            ]
            page.get_by_role("button", name="Clear chat", exact=True).click()
            expect(checkbox).not_to_be_checked()
            page.get_by_role("button", name="Ask about my CV", exact=True).click()
            expect(checkbox).to_be_checked()
            page.get_by_role("button", name="Reset mode", exact=True).click()
            expect(checkbox).not_to_be_checked()
            # Job selection retains the existing conversation-reset confirmation.
            page.on("dialog", lambda dialog: dialog.accept())
            page.get_by_role(
                "button", name="Selected Engineer Example Co", exact=False
            ).click()
            page.get_by_role(
                "button", name="Ask about selected job", exact=True
            ).click()
            expect(checkbox).not_to_be_checked()
            page.get_by_role("button", name="Ask AI", exact=True).last.click()
            expect(
                page.get_by_text("Visible generated answer", exact=True)
            ).to_be_visible()
            assert chats[-1]["job_id"] == 1
            assert chats[-1]["mode"] == "job"
            assert chats[-1]["include_cv"] is False
            fail = True
            composer.fill("Another question")
            page.get_by_role("button", name="Ask AI", exact=True).last.click()
            expect(
                page.get_by_text("AI generation failed (HTTP 429). Retry later.")
            ).to_be_visible()
            expect(composer).to_be_enabled()
            ready = False
            page.reload()
            expect(
                page.get_by_text("Configure AI in Settings.", exact=True)
            ).to_be_visible()
            expect(composer).to_be_disabled()
            assert not errors, errors
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
