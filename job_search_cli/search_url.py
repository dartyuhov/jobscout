"""Normalize portable LinkedIn Jobs search URLs without network requests."""

from urllib.parse import parse_qsl, urlencode, urlsplit
import re


def normalize_search_url(value: str) -> str:
    """Retain search filters, excluding tracking and pagination state."""
    if not isinstance(value, str):
        raise ValueError("Search URL must be a string.")
    url = urlsplit(value.strip())
    if (
        url.scheme != "https"
        or not re.fullmatch(r"(?:[a-z]{2,3}\.)?linkedin\.com", url.hostname or "")
        or url.port
        or url.username
        or url.password
    ):
        raise ValueError("Use an HTTPS LinkedIn URL containing your search parameters.")
    params = [
        (key, val)
        for key, val in parse_qsl(url.query, keep_blank_values=True)
        if key in {"keywords", "geoId", "location", "distance", "sortBy"}
        or re.fullmatch(r"f_[A-Za-z0-9_]+", key)
    ]
    query = urlencode(params)
    return "https://www.linkedin.com/jobs/search/" + (f"?{query}" if query else "")
