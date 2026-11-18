"""Shared LinkedIn job domain models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LinkedInJobDetails:
    """Normalized LinkedIn job details extracted from the job view page."""

    linkedin_job_id: str
    company_name: str
    position_name: str
    job_location: str
    posted_date: str
    easy_apply: bool
    apply_link: str
    about_text: str
    applied: bool = False
