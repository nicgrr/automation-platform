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
    """Return tracked files and their in-repository parents the service cannot reach."""
    unreadable: list[str] = []
    checked_directories: set[Path] = set()
    for relative in paths:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            continue
        parent = path.parent
        while parent != root and parent not in checked_directories:
            checked_directories.add(parent)
            mode = parent.stat().st_mode
            if not mode & (stat.S_IXGRP | stat.S_IXOTH):
                unreadable.append(f"{parent.relative_to(root)}/")
            parent = parent.parent
        mode = path.stat().st_mode
        if not mode & (stat.S_IRGRP | stat.S_IROTH):
            unreadable.append(relative)
    return sorted(unreadable)


def unreadable_virtualenv_paths(venv: Path) -> list[str]:
    """Files need group-read; directories need group traversal.

    The production service runs as group ``ezbay``. A restrictive operator
    umask can make newly installed packages 0600 and their directories 2700,
    even when every tracked source file is readable.
    """
    unreadable: list[str] = []
    if not venv.exists():
        return [str(venv)]
    for path in venv.rglob("*"):
        if path.is_symlink():
            continue
        mode = path.stat().st_mode
        if path.is_dir() and not mode & stat.S_IXGRP:
            unreadable.append(str(path.relative_to(venv)))
        elif path.is_file() and not mode & stat.S_IRGRP:
            unreadable.append(str(path.relative_to(venv)))
    return unreadable


def tracked_paths(root: Path) -> list[str]:
    """Return tracked and not-ignored untracked release candidates.

    Including untracked files matters during a release prepared in-place: a new
    0600 module must be caught before the commit that makes it importable by the
    service. Ignored runtime data and secrets remain out of scope.
    """
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root, capture_output=True, check=True,
    )
    return [os.fsdecode(value) for value in result.stdout.split(b"\0") if value]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    unreadable = unreadable_release_files(root, tracked_paths(root))
    venv_unreadable = unreadable_virtualenv_paths(root / ".venv")
    if unreadable or venv_unreadable:
        if unreadable:
            print("Tracked release paths unreadable by the ezbay service group:")
        for path in unreadable:
            print(f"  {path}")
        if venv_unreadable:
            print("Virtualenv paths unreadable by the ezbay service group:")
            for path in venv_unreadable[:50]:
                print(f"  {path}")
            if len(venv_unreadable) > 50:
                print(f"  ...and {len(venv_unreadable) - 50} more")
        print("Normalize tracked files to Git modes, make their parent directories group-traversable, "
              "and run `chmod -R g+rX .venv` after installs.")
        return 1
    print("Release permissions: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
