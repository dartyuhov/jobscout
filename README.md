# JobScout

Local portal and CLI for LinkedIn collection, job review, and optional AI.

## Public preview

JobScout is available for early testing. Automated checks pass, but real
Chrome/LinkedIn workflows on macOS and Ubuntu desktop still need verification.
Frontend build dependencies have an unresolved Picomatch advisory,
[GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf).
This is a source preview, not a fully verified stable release.

## Quick start

Install the prerequisites listed below, then run:

```bash
git clone https://github.com/dartyuhov/jobscout.git
cd jobscout
bash scripts/bootstrap.sh
bash scripts/start.sh
```

Open `http://127.0.0.1:3000`. Your jobs, CV, settings, and dedicated Chrome
session stay in private application storage outside this checkout. AI is
optional and disabled until you configure and test a connection in Settings.

Project source is [MIT licensed](LICENSE), copyright Dzmitry Artsiukhou.
See [third-party notices](THIRD_PARTY_NOTICES.md) for dependency licenses and
[source export](docs/public-source-export.md) for the reviewed release boundary.

## What this tool does

`job-search` gives you a full workflow for one profile:

1. Launch (or reuse) a Chrome session for LinkedIn Jobs.
2. Collect LinkedIn job IDs from search result pages.
3. Parse detailed job data for selected IDs.
4. Close only the session you used.
5. Review stored jobs in the portal, with AI features optional.

Browser operations queue across processes, including scheduled and CLI runs.

## Requirements

- Python 3.11 (native setup; tested with 3.11.6)
- Node.js 22.23.3 and npm 10 (native SQLite requires a matching Node ABI)
- A Chromium-based browser with Chromium 142 or newer installed: Chrome,
  Helium, Chromium, Brave, or Edge. Standard locations are detected automatically.
  Set `JOBSCOUT_BROWSER_PATH` to an executable path to choose another installation;
  use the same setting during bootstrap, start, and CLI operations. Browser forks
  must support Chromium remote debugging; compatibility is checked at launch.

For native installation, run `bash scripts/bootstrap.sh`, then
`bash scripts/start.sh`. Open `http://127.0.0.1:3000` and create your first
search profile. See [native setup](docs/native-setup.md) for the exact target
matrix, prerequisite instructions, storage locations, and verification limits.

Collection, parsing, and review work without AI credentials. Configure and test
an optional AI connection in **Settings** to enable analysis, summaries, cover
letters, and Ask AI. The app calls your chosen compatible API directly.

## AI connection settings

You can verify a hosted or local OpenAI-compatible text-generation endpoint:

1. Open **Settings** and enter **AI base URL** and **AI model**.
2. Enter **AI token (optional)** if your provider requires authentication.
3. Select **Save AI connection**, then **Test saved connection**.

Enter the full API base URL, including its path prefix. For example,
`https://api.openai.com/v1` sends requests to
`https://api.openai.com/v1/chat/completions`. A local server might use
`http://localhost:1234/v1`. The app appends `/chat/completions` to the base URL;
it doesn't add `/v1` or accept a complete completions endpoint instead of a
base URL. Trailing slashes are ignored. URL credentials, query parameters,
and fragments aren't accepted. Use the token field for credentials.

The supported subset follows the official
[Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create):
HTTP POST with JSON `model`, text `messages` (`role` and `content`), and
`stream: false`. A successful reply must contain nonempty text in
`choices[0].message.content` with `finish_reason: "stop"`. Tool calls,
function calls, refusals, incomplete replies, and non-JSON responses don't
verify generation capability. No tools or provider-specific options are sent.
When a token is saved, requests use `Authorization: Bearer <token>`; without
one, the authorization header is omitted. Requests time out after 30 seconds
and don't follow redirects. Provider response bodies aren't included in errors.

Testing sends only a short synthetic prompt, not jobs or CV text, and can incur
provider charges. Feature requests send their required context to your chosen
provider. A liveness check or model listing doesn't establish readiness.
Changing the endpoint, model, or token requires another explicit successful
test. Saving or testing doesn't enable automatic analysis or summaries.

Analysis sends the selected job and exclusion preferences. Summaries send the
job description and company/position labels. Cover letters also send your saved
CV. Ask AI sends bounded conversation history and selected job context; it adds
CV text for CV, application-answer, or explicit personal-fit questions. These
features generate text without tools, shell access, or model-triggered actions.
See [AI instruction provenance](docs/ai-instruction-provenance.md) for the
application-owned prompt sources and retained behavior.

The token persists **unencrypted in your private backend SQLite database**.
Database backups contain the token. The frontend receives only token presence,
never the saved value. Leave the token input blank to keep it, enter a new
value to replace it, or select **Remove saved token** to remove it. Tokens
aren't saved in browser storage. Treat your database and its backups as private.

## Development setup

For normal installation, use the native bootstrap above. For Python development,
create an environment and install the development dependencies:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

## Verify install

With development dependencies installed, run the Python checks below. Run
`node --test` in `backend` and `npm run build` in `frontend` for portal checks.

```bash
job-search --help
ruff check .
ruff format --check .
python -m pytest
python -m build
```

## Saved search profiles

Paste a LinkedIn URL into **Create Profile → LinkedIn Jobs search URL**, review
the filter preview, enter a name, and save. The path is ignored: search
parameters determine the saved search. Tracking and pagination are removed.
Keywords, location, distance, sorting, and `f_*` filters are preserved. Editing
the individual search fields updates the URL without losing other filters.
Settings export/import preserves the normalized URL; review it before sharing.

Enable **Headless** to run workflows without a visible browser. **Open Browser**
always opens visibly for sign-in without changing the saved preference.
Switching mode restarts the dedicated browser while preserving login data.

Create and edit profiles in the portal. SQLite is the authoritative store for
the portal, CLI, and scheduled workflows. All searches use the app-owned
visible Chrome profile at `chrome/Default` in private application storage.

Inspect profiles and select their local IDs from the CLI:

```bash
job-search profiles list
job-search profiles show 1
job-search linkedin launch --profile-id 1
```

All profile-based commands and the full-flow script accept `--profile-id ID`.
Use the same `DB_PATH` or root `--db-path` selection as the portal. IDs belong
to that database. Missing profiles report the selected database and how to list
profiles; archived profiles must be restored in the portal before running.
Select exactly one of `--profile-id` and the legacy `--profile` option.

For compatibility, `--profile PATH` reads only an existing local `id`, or a
`sessionName` when the file has no ID. It loads current settings from SQLite;
it never imports settings, updates the database from JSON, or writes an ID back
to the file. Use explicit settings import to create a new profile from portable
JSON.

### Import and export search settings

In **Profiles**, select **Export settings** on a profile to download
`search-settings.json`. Select **Import settings JSON** to create a new local
profile from that file. You can also exchange settings through the CLI:

```bash
job-search --db-path /path/to/selected.sqlite3 profiles export 1 > search-settings.json
job-search --db-path /path/to/selected.sqlite3 profiles import search-settings.json
```

Exports contain only the search title, location, time and work-type filters,
page count, and `want` / `not_want` preference text. **Preference text can contain
personal details. Review it before sharing.** The exchange does not include CV
text, jobs, conversations, generated content, provider credentials, reporting
destinations, machine paths, browser/session identities, local database IDs,
schedules, or runtime tuning.

Each import creates a new database ID and session name, uses app-owned Chrome
storage, and starts with visible Chrome and no schedule. Importing the same file
again creates another independent profile; it doesn't update an existing one.
Unknown fields, including excluded runtime fields, are ignored. Invalid types,
missing required filters, and unsupported versions are rejected without creating
a profile. Existing runtime profile JSON isn't an exchange document.

The version 1 contract is a JSON object with `format: "job-search-settings"`,
`version: 1`, a personal-text `notice`, and a `settings` object. Its allowlist is
`jobTitle`, `locationGeoId`, `f_TPR`, `f_WT`, `want`, `not_want`, and `maxPage`.
All fields except `maxPage` are strings; `locationGeoId` and `f_TPR` must be
nonempty. `maxPage` is a positive 32-bit integer and defaults to 5; optional text
defaults to an empty string. Preference text is preserved verbatim.

The portal API serves downloads at `GET /api/profiles/:id/export` and accepts
the JSON document at `POST /api/profiles/import`. The file-upload endpoint,
`POST /api/profiles/upload` (multipart field `file`), uses the same contract.

Portal launch payloads live temporarily in private `runtime/` storage with mode
`0600` and are removed after execution. They select saved database records and
are not editable profile authorities. **Open Browser** and CLI browser
operations use the dedicated visible Chrome profile.

## Dedicated Chrome login

Prepare the shared browser location, then open a visible window for manual login:

```bash
job-search linkedin prepare-browser
job-search linkedin login
```

Sign in and complete account challenges yourself. Return to the terminal and
press Enter when finished. Closing this window preserves cookies and browser
data under `chrome/Default` in private application storage. New searches use
this same login. No everyday Chrome profile is registered, copied, or modified.
Saved legacy browser paths are not used; preparation does not copy or migrate
your everyday Chrome data.

Login holds browser ownership until you finish. Portal **Open Browser**, CLI
launch/get/parse/close/cleanup commands, and scheduled workflows take turns.
Use the full-flow script when you need launch through targeted close to run
without another request navigating between individual commands. All processes
must use the same `JOB_SEARCH_DATA_DIR` to share coordination. The lock file
remains in `runtime/browser.lock`; the OS releases ownership on process exit.
Don't delete it while the app is running.

Healthy app-owned sessions can be reused by several searches. Targeted close
removes only the selected session and keeps Chrome alive while another tracked
session references it. A failed new launch navigation closes only its new
browser. After an interrupted workflow, the next request can reuse a healthy
tracked browser or clean up processes belonging to the configured profile.

For manual desktop verification on macOS and Ubuntu, use an isolated
`JOB_SEARCH_DATA_DIR` and your own account. Run login, restart the app, create
two searches with the default browser path, and verify both retain login.
Run collection alongside a scheduled search and verify they take turns. Close
the target session and verify an unrelated Chrome window stays open. Automated
tests use browser substitutes; they don't establish real desktop login support.

## Full LinkedIn flow (multi-session safe)

Select a saved profile and its session name from `profiles list`:

```bash
export PROFILE_ID=1
export SESSION_NAME=your-saved-session-name
```

Run the full flow:

```bash
job-search linkedin launch --profile-id "$PROFILE_ID"
job-search linkedin get-job-ids --profile-id "$PROFILE_ID"
job-search linkedin parse-job <job-id> --profile-id "$PROFILE_ID"
job-search linkedin close --session-name "$SESSION_NAME"
```

Notes:

- Use `parse-job` repeatedly for each ID you want to enrich before analysis.
- `analyze-all` and `summarize-all` require a verified AI connection and explain
  setup requirements before attempting any AI connection.
- For parallel workflows, do not use `close --all` unless you intentionally want to terminate every tracked session.

## Automated full flow script (sequential parse)

Use the orchestrator script to collect and parse jobs automatically:

```bash
.venv/bin/python scripts/linkedin_full_flow.py \
  --profile-id "$PROFILE_ID" \
  --repo "$(pwd)"
```

Behavior:

- Parses jobs sequentially (no multi-tab parallel parsing).
- Continues on individual `parse-job` failures and reports failed IDs.
- Always closes by `--session-name` from the profile when launch succeeded.
- Skips AI analysis and summaries when AI is unconfigured. Both the terminal
  summary and saved report explain skipped stages; absent AI setup is not a
  collection failure.
- Automatic analysis and summaries default to off, with separate opt-ins. A
  verified connection alone must not enable background AI work.

Exit codes:

- `0`: collection success (including runs with AI skipped)
- `2`: partial success (`parse-job` failures happened)
- `1`: fatal failure in launch/get-job-ids/close/analyze step

## Useful outputs and files

Runtime data defaults to private per-user storage, independent of your working
directory:

- macOS: `~/Library/Application Support/job-search/`
- Ubuntu: `$XDG_DATA_HOME/job-search/`, or `~/.local/share/job-search/` when
  `XDG_DATA_HOME` is unset or relative.

The storage root contains these runtime locations:

- `sessions.json`: tracked Chrome sessions.
- `linkedin_jobs.sqlite3`: profiles, jobs, settings, CV text, generated content,
  and workflow output logs.
- `logs/`: full-flow JSON reports, including command output.
- `runtime/`: temporary workflow launch payloads, removed when work finishes.
- `chrome/`: persistent app-owned browser storage shared by searches.

Fresh storage starts without profiles, jobs, CV text, or imported sessions.
Checkout-local databases and sessions are never discovered, copied, or migrated
automatically. Keep your existing private installation and its data separately.
New runtime directories use mode `0700`; databases, session files, reports, and
launch payloads use `0600`. Existing parent directories retain their permissions.

Set `JOB_SEARCH_DATA_DIR` to override the storage root. Set `DB_PATH` and
`SESSIONS_PATH` to select individual files. Use absolute environment paths when
running separate commands from different directories. Relative overrides resolve
against the invoking process's working directory and are passed to children as
absolute paths. Portal actions, scheduled runs, and full-flow subprocesses use
the same resolved selection.

You can select a database for any CLI command with the root `--db-path` option:

```bash
job-search --db-path /absolute/path/jobs.sqlite3 linkedin get-job-ids \
  --profile-id "$PROFILE_ID"
job-search --db-path /absolute/path/jobs.sqlite3 portal serve --no-browser
```

The existing `linkedin launch --db-path` and `portal serve --db-path` options
remain available and override the root selection for that command. The full-flow
script also accepts `--db-path`; without `--report-path`, it writes to `logs/`
in the user storage root. A database override selects the database only; sessions
still use `SESSIONS_PATH` or the storage root. Backups of the database contain
your private settings and content.

Source and command outputs include:

- `frontend/`: React SPA for browsing jobs and applying
- `backend/`: Express API that reads/writes `linkedin_jobs.sqlite3`
- CLI output:
  - `launch` prints `Created` or `Reused` and CDP endpoints
  - `get-job-ids` prints a JSON array of IDs
  - `parse-job` confirms parsed job ID
  - `close --session-name` confirms only that session was closed
  - `analyze-all` prints per-job decision and final total

## Troubleshooting

- `Tracked LinkedIn session '<name>' was not found.`
  - Run `job-search linkedin launch --profile ...` first.
  - Confirm `sessionName` in profile matches the active tracked session.

- Missing or invalid profile ID
  - Create a profile in the portal and run `job-search profiles list`.
  - Use its local ID with `--profile-id`; launching doesn't rewrite JSON files.

- Chrome profile lock/start failure
  - Use a dedicated Chrome profile directory for automation.
  - Close other Chrome processes using the same profile path.

- `AI_NOT_READY` or `AI is not configured`
  - Continue collecting, parsing, and reviewing jobs without AI.
  - Open **Settings**, save an AI base URL and model, and select
    **Test saved connection**. Retest after changing the endpoint, model, or token.

## Portal (SPA)

Bootstrap installs Node dependencies and builds the frontend. To refresh them
manually, run `npm ci` in each Node package and build the frontend:

```bash
cd backend && npm ci
cd ../frontend && npm ci && npm run build
cd ..
```

Serve the portal locally from CLI:

```bash
job-search portal serve
```

The command starts the backend, serves the built frontend (if available), and
opens `http://127.0.0.1:3000` in your browser.

The portal starts without AI and makes no startup AI requests. AI generation
controls are disabled with setup guidance, while job descriptions, status
changes, and existing generated content remain available. Fresh filter defaults
show unanalyzed jobs instead of requiring an AI match.

Useful options:

```bash
job-search portal serve --port 3100
job-search portal serve --db-path /absolute/path/to/linkedin_jobs.sqlite3
job-search portal serve --no-browser
```

## Installation scope

Use the native macOS or Ubuntu desktop setup for the first release. See the
[verification matrix](docs/native-setup.md#runtime-and-desktop-matrix) for the
tested runtime versions and remaining desktop checks. The public export is
native source only; it omits obsolete container files.
