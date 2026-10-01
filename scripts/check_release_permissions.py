"""Fail when a tracked release file is unreadable by the service account.

The repository directory is group-owned by ``ezbay``, but files created by some
editing tools can still land as 0600. Tests run as the owner and therefore miss
that failure; systemd then cannot import the application. This check needs no sudo:
it verifies that every tracked regular file grants read access to its group or to
other users. Gitlinks/symlinks are ignored.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys


def unreadable_release_files(root: Path, paths: list[str]) -> list[str]:
    unreadable: list[str] = []
    for relative in paths:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            continue
        mode = path.stat().st_mode
        if not mode & (stat.S_IRGRP | stat.S_IROTH):
            unreadable.append(relative)
    return unreadable


def tracked_paths(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True,
    )
    return [os.fsdecode(value) for value in result.stdout.split(b"\0") if value]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    unreadable = unreadable_release_files(root, tracked_paths(root))
    if unreadable:
        print("Tracked files unreadable by the ezbay service group:")
        for path in unreadable:
            print(f"  {path}")
        print("Set source/data-independent files to mode 0644 (or 0755 when executable).")
        return 1
    print("Release permissions: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
