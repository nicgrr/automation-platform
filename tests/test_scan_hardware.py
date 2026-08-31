import subprocess
from unittest.mock import patch

import pytest

from automation_control.scan_ingest.hardware import ScanError, list_devices, scan_sheet


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["scanimage"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_scan_sheet_returns_the_written_path(tmp_path):
    def fake_run(command, **kwargs):
        output_path = command[command.index("-o") + 1]
        with open(output_path, "wb") as handle:
            handle.write(b"fake-png-bytes")
        return _completed()

    with patch("subprocess.run", side_effect=fake_run):
        path = scan_sheet(tmp_path)

    assert path.exists()
    assert path.read_bytes() == b"fake-png-bytes"
    assert path.parent == tmp_path


def test_scan_sheet_names_are_unique_and_timestamped(tmp_path):
    def fake_run(command, **kwargs):
        output_path = command[command.index("-o") + 1]
        with open(output_path, "wb") as handle:
            handle.write(b"x")
        return _completed()

    with patch("subprocess.run", side_effect=fake_run):
        first = scan_sheet(tmp_path)
        second = scan_sheet(tmp_path)

    assert first != second
    assert first.exists() and second.exists()  # neither overwrote the other


def test_scan_sheet_names_the_file_in_local_time_not_utc(tmp_path):
    """A sheet's filename is how the operator finds the scan they just
    made, so it has to agree with their clock. Naming it in UTC put a
    05:27 AEST scan under '20260830-1927' -- the previous day -- so
    looking for that morning's sheets turned up nothing.

    Asserted against the local wall clock rather than a fixed timezone, so
    this holds wherever the host runs.
    """
    from datetime import datetime

    def fake_run(command, **kwargs):
        output_path = command[command.index("-o") + 1]
        with open(output_path, "wb") as handle:
            handle.write(b"x")
        return _completed()

    before = datetime.now()
    with patch("subprocess.run", side_effect=fake_run):
        path = scan_sheet(tmp_path)
    after = datetime.now()

    stamped = datetime.strptime(path.stem.removeprefix("sheet-"), "%Y%m%d-%H%M%S-%f")
    assert before.replace(microsecond=0) <= stamped <= after, (
        f"{path.name} is not stamped with local time ({before}..{after})"
    )


def test_scan_sheet_passes_resolution_and_mode(tmp_path):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        output_path = command[command.index("-o") + 1]
        open(output_path, "wb").write(b"x")
        return _completed()

    with patch("subprocess.run", side_effect=fake_run):
        scan_sheet(tmp_path, resolution=300, mode="Gray")

    assert "--resolution=300" in captured["command"]
    assert "--mode=Gray" in captured["command"]


def test_scan_sheet_includes_device_flag_only_when_given(tmp_path):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        output_path = command[command.index("-o") + 1]
        open(output_path, "wb").write(b"x")
        return _completed()

    with patch("subprocess.run", side_effect=fake_run):
        scan_sheet(tmp_path)  # no device
    assert "-d" not in captured["command"]

    with patch("subprocess.run", side_effect=fake_run):
        scan_sheet(tmp_path, device="pixma:04A91913")
    assert "-d" in captured["command"]
    assert "pixma:04A91913" in captured["command"]


def test_scan_sheet_raises_when_scanimage_is_not_installed(tmp_path):
    with patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(ScanError, match="not found"):
            scan_sheet(tmp_path)


def test_scan_sheet_raises_on_nonzero_exit(tmp_path):
    with patch("subprocess.run", return_value=_completed(returncode=1, stderr="no scanner found")):
        with pytest.raises(ScanError, match="no scanner found"):
            scan_sheet(tmp_path)


def test_scan_sheet_raises_on_timeout(tmp_path):
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="scanimage", timeout=120)):
        with pytest.raises(ScanError, match="timed out"):
            scan_sheet(tmp_path)


def test_scan_sheet_raises_when_output_file_is_missing_despite_success(tmp_path):
    """A 0 exit code with no actual file written must not be treated as a
    real scan -- better to fail loudly than hand back a phantom path."""
    with patch("subprocess.run", return_value=_completed(returncode=0)):
        with pytest.raises(ScanError, match="wrote no image"):
            scan_sheet(tmp_path)


def test_scan_sheet_raises_when_output_file_is_empty(tmp_path):
    def fake_run(command, **kwargs):
        output_path = command[command.index("-o") + 1]
        open(output_path, "wb").close()  # zero bytes
        return _completed()

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(ScanError, match="wrote no image"):
            scan_sheet(tmp_path)


def test_scan_sheet_creates_output_dir_if_missing(tmp_path):
    target = tmp_path / "nested" / "watch"

    def fake_run(command, **kwargs):
        output_path = command[command.index("-o") + 1]
        open(output_path, "wb").write(b"x")
        return _completed()

    with patch("subprocess.run", side_effect=fake_run):
        path = scan_sheet(target)
    assert path.exists()


# --- list_devices ---

def test_list_devices_parses_output():
    with patch("subprocess.run", return_value=_completed(stdout="pixma:04A91913_5E0102\n")):
        assert list_devices() == ["pixma:04A91913_5E0102"]


def test_list_devices_returns_empty_list_when_none_found():
    with patch("subprocess.run", return_value=_completed(stdout="")):
        assert list_devices() == []


def test_list_devices_raises_when_scanimage_is_not_installed():
    with patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(ScanError, match="not found"):
            list_devices()
