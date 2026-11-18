# Working on JobScout

JobScout is a local LinkedIn collection and review app. Read [README.md](README.md)
for the user workflow. Keep its main guide portal-first; put terminal workflows
in the CLI section.

## Find the relevant implementation

Follow the user action across these boundaries before changing behavior:

- `frontend/src/components/` contains portal screens and their UI labels;
  `frontend/src/api/jobs.js` connects them to the backend.
- `backend/server.js` owns portal endpoints, schema setup, and workflow launch;
  `backend/scheduler.js` owns schedules and queued work.
- `job_search_cli/cli.py` exposes terminal commands;
  `job_search_cli/linkedin_full_flow.py` orchestrates collection. The script in
  `scripts/linkedin_full_flow.py` is its checkout entry point.
- Python and Node share the SQLite database, profile settings, browser storage,
  and AI connection contract. Check both implementations for cross-cutting work.

## Preserve the shared contracts

Treat the following as behavior to verify when editing profiles, storage,
browser lifecycle, or AI:

- SQLite is the saved-profile authority. CLI `--profile-id` selects a record in
  the chosen database. JSON exchange creates a new profile; legacy `--profile`
  selects existing records rather than importing settings.
- Search profiles share the app-owned browser at `chrome/Default`. Sign-in
  happens in that browser. **Open Browser** is visible; workflow headless mode
  is a saved search preference. Preserve login across browser mode changes.
- Coordinate browser ownership across portal actions, CLI processes, and
  scheduled runs. Target cleanup to app-owned sessions and processes.
- Resolve private data outside the checkout through `runtime_paths.py` and
  `backend/runtime-paths.js`. Use an isolated external `JOB_SEARCH_DATA_DIR`
  for verification, with the same selection in child processes.
- Collection and review work with AI unconfigured. A successful explicit
  connection test gates generation. Automatic analysis and summaries require
  separate opt-ins; saving or testing a connection leaves them unchanged.
- Keep tokens out of frontend responses, browser storage, logs, and errors.
  Tokens persist unencrypted in the private database; document that accurately.
- AI analysis uses job details and `not_want`. Check the prompt and request
  builder before claiming other saved preferences or CV text affect matching.

## Verify the change

Use the development commands in [README.md](README.md#development). Run the
checks covering the changed behavior; a frontend build checks JSX and bundling.
Browser substitutes in automated tests don't prove real LinkedIn login or
desktop-browser support. Record exactly what you exercised.

For installation, runtime version, or storage changes, read
[native setup](docs/native-setup.md). For AI prompt or context changes, read
[AI instruction provenance](docs/ai-instruction-provenance.md) and check the
shared `job_search_cli/ai-prompts.json` plus both request builders.

## Update documentation and release contents

Write instructions using the current portal labels and concrete user actions.
Check behavior against code, including what happens without AI. Screenshots in
`docs/screenshots/` use fictional data from an isolated local instance. Capture
the real UI after loading finishes, and inspect images for private content.

When adding public files, read
[public source export](docs/public-source-export.md). Update both
`public-source-files.json` and `MANIFEST.in` so exported source and source
distributions include the documentation and image assets they reference.
