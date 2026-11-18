"""Release-content inspection through the standalone export command."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

import pytest


REPO = Path(__file__).resolve().parents[1]
EXPORTER = REPO / "scripts/export_public_source.py"


def test_export_excludes_sensitive_canaries_and_preserves_source(tmp_path):
    # This first invocation also verifies the documented command boundary.
    help_result = subprocess.run(
        [sys.executable, str(EXPORTER), "--help"], capture_output=True, text=True
    )
    assert help_result.returncode == 0, help_result.stderr
    manifest = json.loads((REPO / "public-source-files.json").read_text())
    source = tmp_path / "private source"
    source.mkdir()
    for name in manifest:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((REPO / name).read_bytes())
    canaries = (
        ".git/objects/private",
        ".env",
        ".agents/private.md",
        ".opencode/token",
        ".ralph/history",
        "docs/specs/private.md",
        "config.private.json",
        "backend/private.sqlite3-wal",
        "backend/node_modules/private/index.js",
        "frontend/src/private-notes.jsx",
        "job_search_cli/cv.py",
        "tests/private-fixture.json",
        "screenshots/login.png",
        "logs/private.log",
    )
    marker = ("SYNTHETIC_PRIVATE_" + tmp_path.name).encode()
    for name in canaries:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(marker)
    output = tmp_path / "public source"
    result = subprocess.run(
        [
            sys.executable,
            str(EXPORTER),
            "--source",
            str(source),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert {
        str(p.relative_to(output)) for p in output.rglob("*") if p.is_file()
    } == set(manifest)
    assert all(marker not in p.read_bytes() for p in output.rglob("*") if p.is_file())
    assert all((source / name).read_bytes() == marker for name in canaries)
    assert not (output / ".git").exists()


@pytest.mark.parametrize("hazard", ["existing", "nested", "symlink", "missing"])
def test_export_rejects_unsafe_inputs_without_modifying_destination(tmp_path, hazard):
    source = tmp_path / "source"
    source.mkdir()
    (source / "public-source-files.json").write_text('["README.md"]')
    (source / "README.md").write_text("synthetic public source")
    output = tmp_path / "output"
    if hazard == "existing":
        output.mkdir()
        (output / "keep").write_text("untouched")
    elif hazard == "nested":
        output = source / "nested"
    elif hazard == "symlink":
        (source / "README.md").unlink()
        (source / "README.md").symlink_to(REPO / "README.md")
    else:
        (source / "README.md").unlink()
    result = subprocess.run(
        [
            sys.executable,
            str(EXPORTER),
            "--source",
            str(source),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    if hazard == "existing":
        assert (output / "keep").read_text() == "untouched"
    else:
        assert not output.exists()


@pytest.mark.skipif(
    os.environ.get("RUN_EXPORT_BUILD") != "1",
    reason="Set RUN_EXPORT_BUILD=1 to build and inspect fresh release archives",
)
def test_fresh_export_archives_keep_the_release_boundary(tmp_path):
    source = tmp_path / "source"
    subprocess.run([sys.executable, str(EXPORTER), "--output", str(source)], check=True)
    # Private files introduced after export must not enter the sdist either.
    marker = ("PRIVATE_ARCHIVE_" + tmp_path.name).encode()
    for name in (".env", "backend/private.js", "tests/private.py", "docs/private.md"):
        (source / name).write_bytes(marker)
    subprocess.run([sys.executable, "-m", "build"], cwd=source, check=True)
    allowed = set(json.loads((source / "public-source-files.json").read_text()))
    with tarfile.open(next((source / "dist").glob("*.tar.gz"))) as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            name = member.name.split("/", 1)[1]
            assert (
                name in allowed
                or name == "PKG-INFO"
                or name == "setup.cfg"
                or name.startswith("job_search_cli.egg-info/")
            )
            assert marker not in archive.extractfile(member).read()
    with zipfile.ZipFile(next((source / "dist").glob("*.whl"))) as archive:
        for name in archive.namelist():
            assert name in allowed or name.startswith("job_search_cli-0.1.0.dist-info/")
            assert marker not in archive.read(name)
        metadata = archive.read("job_search_cli-0.1.0.dist-info/METADATA").decode()
        assert "License-Expression: MIT" in metadata
        assert "Author: Dzmitry Artsiukhou" in metadata
        assert "Author-email:" not in metadata
        assert "job_search_cli-0.1.0.dist-info/licenses/LICENSE" in archive.namelist()
