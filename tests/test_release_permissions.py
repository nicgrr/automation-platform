from pathlib import Path

from scripts.check_release_permissions import unreadable_release_files, unreadable_virtualenv_paths


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
