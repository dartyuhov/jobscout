#!/usr/bin/env python3
"""Run the LinkedIn full flow orchestrator."""

from __future__ import annotations

from pathlib import Path

from job_search_cli.linkedin_full_flow import main


if __name__ == "__main__":
    repository_root = Path(__file__).resolve().parent.parent
    raise SystemExit(main(default_repo_root=repository_root))
