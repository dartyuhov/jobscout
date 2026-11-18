"""Search-settings file exchange through the portal UI and real HTTP API."""

import json
import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

import pytest
from playwright.sync_api import expect, sync_playwright


def test_portal_downloads_settings_and_imports_independent_search(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    if not (repo / "frontend/dist/index.html").exists():
        pytest.skip("Build the frontend before running portal exchange browser checks")
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
        ["node", "server.js"],
        cwd=repo / "backend",
        env={**os.environ, "HOST": "127.0.0.1", "PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                with urlopen(f"{url}/api/health", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=executable)
            page = browser.new_page(accept_downloads=True)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            created = page.request.post(
                f"{url}/api/profiles",
                data={
                    "sessionName": "PRIVATE-SESSION",
                    "chromeProfilePath": "/PRIVATE-MACHINE/chrome",
                    "locationGeoId": "123",
                    "f_TPR": "r86400",
                    "f_WT": "2",
                    "headless": True,
                    "jobTitle": "Python engineer",
                    "want": "Remote work",
                    "reporting": {"token": "PRIVATE-TOKEN"},
                },
            )
            assert created.status == 201
            page.goto(f"{url}/profiles")
            expect(
                page.get_by_text(
                    "Preference text can contain personal details.", exact=False
                )
            ).to_be_visible()
            with page.expect_download() as downloaded:
                page.get_by_role("button", name="Export settings", exact=True).click()
            download = downloaded.value
            assert download.suggested_filename == "search-settings.json"
            file = tmp_path / "search-settings.json"
            download.save_as(file)
            text = file.read_text(encoding="utf-8")
            assert "PRIVATE-" not in text
            assert json.loads(text)["settings"]["want"] == "Remote work"
            with page.expect_file_chooser() as chooser:
                page.get_by_role(
                    "button", name="Import settings JSON", exact=True
                ).click()
            chooser.value.set_files(file)
            expect(page.get_by_role("status")).to_contain_text(
                "Imported settings as 'search-"
            )
            expect(
                page.get_by_role("button", name="Export settings", exact=True)
            ).to_have_count(2)
            profiles = page.request.get(f"{url}/api/profiles").json()["profiles"]
            local = next(
                profile
                for profile in profiles
                if profile["sessionName"] != "PRIVATE-SESSION"
            )
            assert local["id"] != created.json()["profile"]["id"]
            assert local["headless"] is False
            assert local["f_WT"] == "2"
            assert "reporting" not in local
            file.write_text('{"token": "PRIVATE-SECRET"', encoding="utf-8")
            page.locator('input[type="file"]').set_input_files(file)
            expect(page.get_by_role("alert")).to_have_text("Invalid JSON file.")
            page.get_by_role("button", name="Create Profile", exact=True).click()
            page.get_by_label("LinkedIn Jobs search URL").fill(
                "https://www.linkedin.com/jobs/search-results/?keywords=QA&f_E=2%2C3&f_AL=true&trackingId=discard&start=50"
            )
            expect(page.get_by_label("Search preview")).to_contain_text("f_E: 2,3")
            expect(page.get_by_label("Job Title", exact=True)).to_have_value("QA")
            expect(page.get_by_label("Location Geo ID", exact=False)).to_be_visible()
            page.get_by_label("Job Title", exact=True).fill("Backend engineer")
            page.get_by_label("Headless", exact=False).check()
            page.get_by_label("Profile name").fill("Pasted search")
            page.locator('button[type="submit"]').click()
            expect(page.get_by_role("heading", name="Create Profile")).to_have_count(0)
            saved = page.request.get(f"{url}/api/profiles").json()["profiles"]
            pasted = next(p for p in saved if p["sessionName"] == "Pasted search")
            assert "f_AL=true" in pasted["searchUrl"]
            assert "trackingId" not in pasted["searchUrl"]
            assert pasted["headless"] is True
            assert "keywords=Backend+engineer" in pasted["searchUrl"]
            assert not errors, errors
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
