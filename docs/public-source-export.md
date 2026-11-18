# Public source export

Create a separate source tree with the explicit, file-by-file
`public-source-files.json` allowlist. Review changes to that list and the
contents of every added file before running the exporter.

## Create an export

Choose a new directory outside the checkout with an existing parent:

```bash
python3.11 scripts/export_public_source.py --output /path/to/new-source
```

The command refuses an existing output, overlapping trees, missing files, and
symlinked inputs. It copies only listed files, with no directory globs or Git
discovery. It never initializes Git or copies history. The original source and
runtime data remain intact. Ignore rules are defense in depth, not the export
boundary. Don't publish the private checkout or use a recursive directory copy.

The export includes core runtime, tests, native setup, license/provenance,
and the synthetic `examples/search-settings.json`. Import the example through
Profiles or `job-search profiles import examples/search-settings.json`.
It excludes developer resources, planning records, private configurations,
runtime data, dependency installations, and historical Docker files. This is
a native-source release; container deployment isn't supported.

## Verify the exact output

Install and run checks inside the exported tree, then inspect fresh wheel,
source distribution, and frontend assets. The source distribution is built
from an already allowlisted tree; its packaging patterns are not a substitute
for the exporter. Never build public artifacts from the private checkout.

The wheel contains the Python CLI package only. Use the complete source tree
or source distribution for the portal, frontend build, and bootstrap scripts.

Use the development commands in [README](../README.md). Ruff is pinned to
0.15.5 so a fresh development install reproduces the validated formatter.
Python dependencies otherwise resolve ranges; npm uses committed lockfiles.

Run a maintained secret scanner against the exact output and fresh artifacts.
For Gitleaks 8.30.1, the directory command is:

```bash
gitleaks dir /path/to/new-source --redact=100 --no-banner \
  --ignore-gitleaks-allow --report-format json --report-path /path/to/report.json
gitleaks dir /path/to/artifacts --redact=100 --no-banner \
  --ignore-gitleaks-allow --max-archive-depth 3 \
  --report-format json --report-path /path/to/artifact-report.json
```

Keep reports outside the export. Record scanner version, commands, exact
scope, exclusions, exit codes, findings, and artifact hashes. A scanner pass
doesn't establish privacy: separately inspect names, emails, owner paths,
real job IDs and links, company notes, preferences, CVs, fixtures, and metadata.
The maintainer's intentional copyright name is permitted; no license email
is needed. Inspect archive members and package metadata, not just filenames.

Review [third-party notices](../THIRD_PARTY_NOTICES.md), installed dependency
license texts, and transitive lockfile licenses, including CC-BY-4.0 data.
Rebuild and repeat the inspection whenever source or dependencies change.
Public history, repository creation, and publication require separate approval.
