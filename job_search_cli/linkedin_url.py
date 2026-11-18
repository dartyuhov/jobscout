"""Helpers for building LinkedIn Jobs URLs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode, parse_qsl, urlsplit

from job_search_cli.search_url import normalize_search_url

LINKEDIN_JOBS_SEARCH_URL = "https://www.linkedin.com/jobs/search/"
LINKEDIN_RESULTS_PER_PAGE = 25


def build_linkedin_jobs_url(
    profile: Mapping[str, Any],
    *,
    start: int | None = None,
) -> str:
    """Build the reproducible LinkedIn Jobs URL from a normalized profile."""
    if profile.get("searchUrl"):
        url = normalize_search_url(profile["searchUrl"])
        params = parse_qsl(urlsplit(url).query, keep_blank_values=True)
        if start is not None:
            params.append(("start", str(start)))
        query = urlencode(params)
        return LINKEDIN_JOBS_SEARCH_URL + (f"?{query}" if query else "")
    query_params: list[tuple[str, str]] = [
        ("currentJobId", str(profile["currentJobId"])),
        ("f_TPR", str(profile["f_TPR"])),
    ]

    work_type = str(profile.get("f_WT", "")).strip()
    if work_type:
        query_params.append(("f_WT", work_type))

    query_params.append(("geoId", str(profile["locationGeoId"])))

    keywords = str(profile.get("jobTitle", "")).strip()
    if keywords:
        query_params.append(("keywords", keywords))

    if start is not None:
        query_params.append(("start", str(start)))

    return f"{LINKEDIN_JOBS_SEARCH_URL}?{urlencode(query_params)}"


def build_linkedin_jobs_page_urls(
    profile: Mapping[str, Any],
    *,
    results_per_page: int = LINKEDIN_RESULTS_PER_PAGE,
) -> list[str]:
    """Build all paginated LinkedIn Jobs URLs for configured crawl pages."""
    max_page = int(profile.get("maxPage", 5))
    return [
        build_linkedin_jobs_url(
            profile,
            start=(page_number - 1) * results_per_page,
        )
        for page_number in range(1, max_page + 1)
    ]
