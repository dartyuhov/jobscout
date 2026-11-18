"""Generation-only compatible HTTP client for application-owned job prompts."""

from __future__ import annotations

import json
import sqlite3
from importlib.resources import files
from typing import Any
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from job_search_cli.ai_readiness import (
    AI_SETUP_GUIDANCE,
    get_ai_readiness,
    valid_connection,
)
from job_search_cli.runtime_paths import database_path


class AiGenerationError(ValueError):
    """An actionable, credential-free generation failure."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_connection() -> dict[str, Any]:
    """Read only the verified private connection shared with the portal."""
    ai = get_ai_readiness()
    if not ai.ready:
        raise AiGenerationError(ai.guidance)
    try:
        with sqlite3.connect(f"{database_path().as_uri()}?mode=ro", uri=True) as db:
            connection = json.loads(
                db.execute(
                    "SELECT value FROM settings WHERE key='ai_connection'"
                ).fetchone()[0]
            )
    except (sqlite3.Error, ValueError, TypeError) as exc:
        raise AiGenerationError(
            "Cannot read AI connection. Check Settings and retry."
        ) from exc
    # Readiness and this credential read use separate SQLite snapshots. Validate
    # the record we will actually use, not a possibly older Settings revision.
    if not (
        valid_connection(connection)
        and connection.get("verified_revision") == connection["revision"]
    ):
        raise AiGenerationError(AI_SETUP_GUIDANCE)
    return connection


def generate_text(connection: dict[str, Any], messages: list[dict[str, str]]) -> str:
    """Use the same non-streaming Chat Completions subset as the backend."""
    token = connection.get("token", "")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        request = Request(
            f"{connection['base_url'].rstrip('/')}/chat/completions",
            data=json.dumps(
                {"model": connection["model"], "messages": messages, "stream": False}
            ).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with build_opener(_NoRedirect).open(request, timeout=30) as response:
            raw = response.read()
    except HTTPError as exc:
        raise AiGenerationError(
            f"AI generation failed (HTTP {exc.code}). Check the saved token, base URL, model, quota, and provider availability."
        ) from None
    except Exception:
        raise AiGenerationError(
            "Cannot reach AI endpoint. Check Settings, TLS, and network; requests time out after 30 seconds."
        ) from None
    try:
        payload = json.loads(raw)
        choice = payload["choices"][0]
        message = choice["message"]
        content = message.get("content")
        if (
            not isinstance(content, str)
            or not content.strip()
            or choice.get("finish_reason") != "stop"
            or message.get("tool_calls")
            or message.get("function_call")
            or message.get("refusal")
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        raise AiGenerationError(
            "AI endpoint did not return a complete text generation. Check compatible Chat Completions support."
        ) from None
    return content.replace(token, "[redacted]") if token else content


def generate_job_result(
    connection: dict[str, Any], kind: str, job: Any, not_want: str = ""
) -> dict[str, Any]:
    """Validate complete structured output before callers persist any result."""
    prompts = json.loads(
        files("job_search_cli").joinpath("ai-prompts.json").read_text(encoding="utf-8")
    )
    context = {
        key: getattr(job, key)
        for key in ("company_name", "position_name", "about_text")
    }
    if kind == "analysis":
        context.update(job_location=job.job_location, not_want=not_want)
    reply = generate_text(
        connection,
        [
            {"role": "system", "content": prompts[kind]},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
    )
    try:
        value = _redact_structured(json.loads(reply), connection.get("token", ""))
        if kind == "analysis":
            valid = (
                isinstance(value, dict)
                and set(value) == {"analyzed", "desicion", "reasoning"}
                and type(value["analyzed"]) is int
                and value["analyzed"] == 1
                and type(value["desicion"]) is int
                and value["desicion"] in (0, 1)
                and _text(value["reasoning"])
            )
        else:
            valid = (
                isinstance(value, dict)
                and set(value)
                == {"must_haves", "nice_to_haves", "company", "application_type"}
                and all(
                    isinstance(value[key], list)
                    and all(_text(item) for item in value[key])
                    for key in ("must_haves", "nice_to_haves")
                )
                and isinstance(value["company"], dict)
                and set(value["company"]) == {"name", "industry"}
                and all(_text(item) for item in value["company"].values())
                and _text(value["application_type"])
            )
        if not valid:
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise AiGenerationError(
            f"AI returned an invalid {kind} result. Retry or check the model's JSON support."
        ) from None
    return value


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _redact_structured(value: Any, token: str) -> Any:
    if not token:
        return value
    if isinstance(value, str):
        return value.replace(token, "[redacted]")
    if isinstance(value, list):
        return [_redact_structured(item, token) for item in value]
    if isinstance(value, dict):
        return {key: _redact_structured(item, token) for key, item in value.items()}
    return value


def analyze_job(
    connection: dict[str, Any], *, not_want: str, job: Any
) -> dict[str, Any]:
    return generate_job_result(connection, "analysis", job, not_want)


def summarize_job(connection: dict[str, Any], *, job: Any) -> dict[str, Any]:
    return generate_job_result(connection, "summary", job)
