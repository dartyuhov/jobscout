"""Copy only individually reviewed source files into a new, non-Git directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import shutil


def export_source(source: Path, output: Path) -> int:
    source = source.resolve(strict=True)
    if output.is_symlink():
        raise ValueError("Output must not be a symlink")
    output = output.resolve()
    if output == source or source in output.parents or output in source.parents:
        raise ValueError("Output must be separate from the source tree")
    if output.exists():
        raise ValueError("Output already exists; choose a new directory")
    if not output.parent.is_dir():
        raise ValueError("Output parent must already exist")
    manifest_path = source / "public-source-files.json"
    if manifest_path.is_symlink():
        raise ValueError("Manifest must not be a symlink")
    names = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(names, list)
        or not names
        or not all(isinstance(name, str) for name in names)
        or len(set(names)) != len(names)
    ):
        raise ValueError("Manifest must be a nonempty list of unique file paths")
    files = []
    for name in names:
        if not isinstance(name, str):
            raise ValueError("Manifest entries must be strings")
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or str(relative) != name:
            raise ValueError("Manifest entries must be canonical relative paths")
        path = source / name
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError(f"Allowlisted path must not use symlinks: {name}")
        if not path.is_file():
            raise ValueError(f"Allowlisted file is missing: {name}")
        files.append((path, name))
    # Validate everything before creating output; never overwrite or merge trees.
    output.mkdir()
    for path, name in files:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(0o644)
    return len(files)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        count = export_source(args.source, args.output)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, f"Export failed: {exc}\n")
    print(f"Exported {count} allowlisted files. No Git history was created or copied.")


if __name__ == "__main__":
    main()
