import subprocess
from pathlib import Path

from scripts.check_release_permissions import tracked_paths, unreadable_release_files, unreadable_virtualenv_paths


def test_owner_only_file_is_rejected(tmp_path: Path):
    source = tmp_path / "private.py"
    source.write_text("pass\n")
    source.chmod(0o600)

    assert unreadable_release_files(tmp_path, ["private.py"]) == ["private.py"]


def test_group_or_world_readable_files_are_accepted(tmp_path: Path):
    group_readable = tmp_path / "group.py"
    group_readable.write_text("pass\n")
    group_readable.chmod(0o640)
    world_readable = tmp_path / "world.py"
    world_readable.write_text("pass\n")
    world_readable.chmod(0o604)

    assert unreadable_release_files(tmp_path, ["group.py", "world.py"]) == []


def test_untraversable_tracked_parent_directory_is_rejected(tmp_path: Path):
    package = tmp_path / "package"
    package.mkdir()
    package.chmod(0o2700)
    module = package / "module.py"
    module.write_text("pass\n")
    module.chmod(0o640)

    assert unreadable_release_files(tmp_path, ["package/module.py"]) == ["package/"]


def test_virtualenv_requires_group_read_and_directory_traversal(tmp_path: Path):
    package = tmp_path / "site-packages" / "example"
    package.mkdir(parents=True)
    package.chmod(0o2700)
    module = package / "module.py"
    module.write_text("pass\n")
    module.chmod(0o600)

    issues = unreadable_virtualenv_paths(tmp_path)

    assert "site-packages/example" in issues
    assert "site-packages/example/module.py" in issues


def test_group_readable_virtualenv_is_accepted(tmp_path: Path):
    package = tmp_path / "site-packages" / "example"
    package.mkdir(parents=True)
    (tmp_path / "site-packages").chmod(0o2750)
    package.chmod(0o2750)
    module = package / "module.py"
    module.write_text("pass\n")
    module.chmod(0o640)

    assert unreadable_virtualenv_paths(tmp_path) == []


def test_release_paths_include_untracked_files_but_not_ignored_runtime_data(tmp_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.py").write_text("pass\n")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)
    (tmp_path / "new_module.py").write_text("pass\n")
    (tmp_path / ".gitignore").write_text("runtime.db\n")
    (tmp_path / "runtime.db").write_text("ignored\n")

    paths = tracked_paths(tmp_path)

    assert "tracked.py" in paths
    assert "new_module.py" in paths
    assert ".gitignore" in paths
    assert "runtime.db" not in paths
