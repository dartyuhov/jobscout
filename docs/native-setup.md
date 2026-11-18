# Native setup

Use `bash scripts/bootstrap.sh` to prepare the application and
`bash scripts/start.sh` to serve the portal at `http://127.0.0.1:3000`.
Collection, parsing, and review don't require AI configuration.

## Runtime and desktop matrix

The first-release targets are native macOS and Ubuntu desktop. This table
distinguishes the installation contract from actual verification. It doesn't
claim full desktop or LinkedIn coverage on an untested machine.

| Component | Selected contract | Verification in this slice |
| --- | --- | --- |
| macOS | macOS 26, Apple Silicon | macOS 26.7.2 arm64: clean-source dependency installation, build, HTTP startup, and rerun passed |
| Ubuntu | Ubuntu 24.04 LTS desktop, x86_64 | Process tests simulate release detection; real desktop installation remains unverified |
| Python | Python 3.11 with `venv` and `ensurepip` | Python 3.11.6 |
| Node.js | Exactly 22.23.3 LTS, native architecture | Node.js 22.23.3 arm64 |
| npm | npm 10, bundled with the selected Node | npm 10.9.9 |
| Chrome | Google Chrome Stable 142 or newer, visible desktop | Version detection uses a synthetic 142.0.7444.175 executable; no real Chrome is installed on the test host |

Node 18 is incompatible with the locked native `better-sqlite3` dependency.
Although the dependency accepts other recent Node versions, this setup pins
22.23.3 in `.nvmrc` to keep installation and native ABI expectations explicit.
Don't copy `node_modules` or `.venv` between architectures or runtimes.
Other macOS versions, Intel Macs, other Ubuntu releases, Linux ARM, Windows,
and headless servers aren't verified release targets. Scripts reject non-macOS,
non-Ubuntu platforms and Ubuntu releases other than 24.04. A desktop session
is necessary for manual browser login, but isn't necessary for portal startup.

## Install prerequisites

Install the prerequisites yourself before running bootstrap. The scripts don't
install system packages, invoke `sudo`, install Playwright browsers, or start
Chrome during setup.

1. Install Python 3.11 with `venv` and `ensurepip` support. On macOS, use
   [Python.org downloads](https://www.python.org/downloads/macos/) or your
   existing runtime manager. On Ubuntu 24.04, use a user-managed Python 3.11
   distribution with venv support; the default system Python isn't 3.11.
   If your managed workstation lacks required shared libraries, ask its
   administrator to provision them separately.
2. Select Node.js 22.23.3 with your existing user-managed version manager.
   With nvm already installed, run `nvm install` and `nvm use` in the checkout.
   [Node.js downloads](https://nodejs.org/en/download) also provide native
   distributions. Keep the bundled npm 10 available on `PATH`.
3. Install [Google Chrome Stable](https://www.google.com/chrome/). On macOS,
   place it in `/Applications` or `~/Applications`. On Ubuntu, the existing
   browser discovery contract uses `google-chrome` or `google-chrome-stable`
   on `PATH`, `/usr/bin`, or `/opt/google/chrome/google-chrome`.
4. Ensure you have network access to the Python and npm package registries.
   Most supported native combinations have prebuilt SQLite binaries. If
   npm falls back to compilation, provide platform C/C++ build tools through
   your workstation's normal provisioning process, then retry. Bootstrap
   never installs those tools.

## Bootstrap and start

Run these commands from the source directory. Use the same environment
overrides for both commands and for later CLI work.

```bash
export PYTHON=python3.11
bash scripts/bootstrap.sh
bash scripts/start.sh
```

Bootstrap checks Python, Node, npm, and Chrome before installing project
dependencies. It creates `.venv`, installs the Python package, runs `npm ci`
in both Node packages using their committed lockfiles, builds `frontend/dist`,
and calls the existing `linkedin prepare-browser` CLI command. It probes
native SQLite with an in-memory database. Python dependencies follow the
ranges in `pyproject.toml`; only Node dependencies are lockfile-reproducible.

Rerunning bootstrap refreshes project dependencies and the frontend build.
It preserves your private database, settings, credentials, sessions, and
dedicated Chrome login. It doesn't create sample profiles or import data.
The scripts resolve source locations from their own path, so you can also
invoke them by absolute path from another working directory.

After bootstrap, `.venv/bin/job-search portal serve --no-browser` also serves
the native assets, including when invoked by absolute path from another
directory. A noneditable local install resolves its source checkout from pip's
installation metadata. If you move that checkout, rerun bootstrap or set
`JOB_SEARCH_SOURCE_ROOT` to its new location.

Start checks the Python environment, built frontend, backend dependencies,
native SQLite ABI, Chrome version, and port availability. It forces
`127.0.0.1` binding even if `HOST` is set elsewhere, then replaces itself
with the backend process. Press Ctrl+C to stop the portal. To change the port:

```bash
PORT=3001 bash scripts/start.sh
```

## Private storage

Runtime storage follows the shared CLI and portal path contract. New private
directories use mode `0700`; SQLite uses mode `0600`. Existing directories
and user data aren't reset by bootstrap.

- macOS: `~/Library/Application Support/job-search`.
- Ubuntu: `$XDG_DATA_HOME/job-search` when `XDG_DATA_HOME` is absolute;
  otherwise `~/.local/share/job-search`.
- `JOB_SEARCH_DATA_DIR` overrides the application data directory.
- `DB_PATH` and `SESSIONS_PATH` override the database and session files.
- `chrome/Default`, `logs`, and `runtime` remain under the data directory.

Native scripts, the CLI, and the backend reject runtime paths inside the
resolved checkout, including explicit overrides and
symlink aliases. Build dependencies, `.venv`, and frontend build outputs stay
in the checkout; private runtime data stays outside it. Treat database backups
as private: saved AI tokens are unencrypted. Use a new external location for
testing rather than your existing installation:

```bash
export JOB_SEARCH_DATA_DIR="$HOME/job-search-sandbox"
bash scripts/bootstrap.sh
bash scripts/start.sh
```

## Troubleshooting

Expected failures include a remediation command or prerequisite link.

- **Missing Python:** set `PYTHON` to an installed Python 3.11 executable.
  If `.venv` belongs to another Python version, move it aside and rerun setup.
- **Wrong Node/npm or native SQLite ABI:** select Node 22.23.3 and npm 10,
  then rerun bootstrap. `npm ci` replaces project dependencies for the current
  runtime and architecture. Run `npm ci` directly in the reported package
  directory to inspect installation details.
- **Missing Chrome or old version:** install or update Stable in a standard
  location. Detection only runs `--version`; it doesn't access your browser
  account or launch a login window.
- **Missing frontend:** rerun bootstrap. For detailed build diagnostics,
  run `npm run build` in `frontend`.
- **Port occupied:** stop the process using the selected port or set
  `PORT=3001`. A late port collision also produces a short actionable error.
- **Storage/database access:** check the selected paths, permissions, and free
  disk space. Don't delete the database to fix a dependency problem.

## Verification and next steps

The automated smoke installs from a fresh source copy without Git metadata,
uses an isolated home and data directory, builds real frontend assets, serves
the portal twice, and checks empty jobs/profiles/CV, disabled AI, preserved
settings and synthetic browser data, private permissions, and no new private
runtime files in the source. It traps unintended legacy AI service requests.
It substitutes Chrome version detection only; it doesn't validate a browser
session, external provider, or manual LinkedIn workflow. Run it with an
existing developer environment:

```bash
RUN_NATIVE_SMOKE=1 .venv/bin/python -m pytest tests/test_native_smoke.py -q
```

Real Chrome initialization, tester-owned LinkedIn login, collection, parsing,
and targeted close on both desktops remain release-verification work.
Ubuntu installation and its exact Chrome patch version must be
recorded before claiming verified desktop support. After starting your
portal, create a search profile and open the dedicated browser for manual
login. Configure optional AI in **Settings** only when you want AI features.
All searches use the same app-owned visible Chrome directory; search profiles
can't select arbitrary browser directories or enable headless operation.
