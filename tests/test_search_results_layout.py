"""Real DOM checks for both LinkedIn search-result card layouts."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from job_search_cli.chrome import (
    _extract_visible_job_ids,
    _is_search_results_page_ready,
    _scroll_job_results,
)


def test_component_key_results_readiness_extraction_and_scroll():
    with sync_playwright() as playwright:
        executable = os.environ.get(
            "PLAYWRIGHT_CHROMIUM_EXECUTABLE", playwright.chromium.executable_path
        )
        if not Path(executable).exists():
            pytest.skip("Set PLAYWRIGHT_CHROMIUM_EXECUTABLE for real DOM regression")
        browser = playwright.chromium.launch(executable_path=executable)
        try:
            page = browser.new_page()
            page.set_content("<main>Loading</main>")
            assert not _is_search_results_page_ready(page)
            page.set_content("""
                <main style="height:100px;overflow-y:auto">
                  <div componentkey="job-card-component-ref-123">
                    <div componentkey="job-card-component-ref-123">Job one</div>
                  </div>
                  <div componentkey="job-card-component-ref-456">Job two</div>
                  <div componentkey="job-card-component-ref-invalid">Ignore</div>
                  <div style="height:1000px"></div>
                </main>
            """)
            assert _is_search_results_page_ready(page)
            assert _extract_visible_job_ids(page=page, cdp_http_endpoint="test") == [
                "123",
                "456",
            ]
            assert _scroll_job_results(page)
            page.evaluate(
                "document.body.insertAdjacentHTML('beforeend', '<div data-job-id=789></div>')"
            )
            assert _extract_visible_job_ids(page=page, cdp_http_endpoint="test") == [
                "123",
                "456",
                "789",
            ]
            page.set_content(
                "<main><h2>No results found</h2><p>Try shortening or rephrasing your search.</p></main>"
            )
            assert _is_search_results_page_ready(page)
            assert _extract_visible_job_ids(page=page, cdp_http_endpoint="test") == []
        finally:
            browser.close()
