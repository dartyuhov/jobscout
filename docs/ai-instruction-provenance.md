# AI instruction provenance

The application supplies its own generation instructions. Running analysis,
summaries, cover letters, or Ask AI requires only a verified compatible API
connection in Settings. No agent installation, model alias, or developer
workspace configuration is loaded at runtime.

## Reviewed instruction sources

The application instructions were reviewed against the project's original
local generation definitions. The retained sources are:

| Feature | Application source | Behavior retained |
| --- | --- | --- |
| Analysis | `job_search_cli/ai-prompts.json`, `analysis` | Exclusion-only classification, evidence from supplied job text, strict `analyzed` / `desicion` / `reasoning` JSON |
| Summary | `job_search_cli/ai-prompts.json`, `summary` | Required and optional qualifications, company and application details, validated structured JSON |
| Cover letter | `backend/server.js`, `/api/cover-letter/generate` | Tailored 250–350-word letter based on the selected role and CV, no invented experience, placeholders, or extra commentary |
| Ask AI | `backend/server.js`, `buildAiChatPrompt` | Practical job, CV, and application answers using explicit context and bounded history, without invented personal facts |

The analysis, summary, and cover-letter behavior originated in this project's
local `job-interest-reviewer`, `job-summarizer`, and `cover-letter-helper`
definitions. The application wording is a concise rewrite, rather than a copy
of agent frontmatter or an external prompt pack. The summary now uses `Unknown`
for missing details rather than inferring an industry. Ask AI's instructions
live in the portal source; the former external `quick` agent is not bundled or
needed. All four features explicitly generate text without tools or actions.

## Attribution and release boundary

The reviewed local definitions and application prompt sources contain no
third-party copyright, license notice, or external prompt attribution to
carry forward. This finding covers these instruction sources only; it is not
a license clearance of dependencies or the complete release. See
[third-party notices](../THIRD_PARTY_NOTICES.md) for the broader source review.

Developer workspace resources and personal configuration aren't application
dependencies and aren't included in the public-source allowlist.

The legacy database `reporting` column remains inert for compatibility. Public
profile responses and launch payloads omit it, new profiles use its empty
default, and profile edits preserve existing stored values without using them.
