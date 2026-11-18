from urllib.parse import parse_qs, urlsplit

import pytest

from job_search_cli.linkedin_url import build_linkedin_jobs_url
from job_search_cli.search_url import normalize_search_url


@pytest.mark.parametrize(
    "search_path", ["search", "search-results", "srch", "anything"]
)
def test_search_filters_preserved_and_tracking_removed(search_path):
    url = f"https://uk.linkedin.com/jobs/{search_path}/?keywords=QA%20engineer&f_E=2%2C3&f_AL=true&f_X=new&geoId=123&start=50&currentJobId=9&trackingId=secret#fragment"
    normalized = normalize_search_url(url)
    profile = {"searchUrl": normalized}
    params = parse_qs(urlsplit(build_linkedin_jobs_url(profile, start=25)).query)
    assert params == {
        "keywords": ["QA engineer"],
        "f_E": ["2,3"],
        "f_AL": ["true"],
        "f_X": ["new"],
        "geoId": ["123"],
        "start": ["25"],
    }
    assert "secret" not in normalized


@pytest.mark.parametrize(
    "url",
    [
        "https://linkedin.com.evil.test/jobs/search/",
        "https://evil@linkedin.com/jobs/search/",
        "http://linkedin.com/jobs/search/",
        "not a URL",
    ],
)
def test_reject_invalid_search_links(url):
    with pytest.raises(ValueError):
        normalize_search_url(url)
