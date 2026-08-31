"""Thin wrapper around `scanimage` (SANE) for a flatbed physically attached
to the server -- the Canon CanoScan LiDE 300, in practice, via the
`genesys`/`pixma` backend. Writes straight into the watch folder `scan`
already polls, so capturing a sheet and processing it is two commands (or
one, if `run_session` is later extended to trigger captures itself) rather
than a manual scanimage invocation plus a file copy.
"""

import subprocess
from datetime import datetime
from pathlib import Path

DEFAULT_RESOLUTION = 600
DEFAULT_MODE = "Color"
SCAN_TIMEOUT_SECONDS = 120


class ScanError(Exception):
    """scanimage isn't installed, the device isn't reachable, or the scan
    itself failed. Never raised for "no scanner configured" -- omitting a
    device lets scanimage auto-pick the only one connected."""


def scan_sheet(
    output_dir: Path, device: str | None = None,
    resolution: int = DEFAULT_RESOLUTION, mode: str = DEFAULT_MODE,
    timeout: float = SCAN_TIMEOUT_SECONDS,
) -> Path:
    """Scan one sheet and save it into `output_dir` with a timestamped
    name (so concurrent/rapid captures never collide). Raises ScanError
    rather than returning a path to a partial/missing file -- callers can
    trust that a returned path is a real, complete image.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    # Local time, deliberately: this filename is the operator's handle on a
    # physical scan they just made, and every other timestamp they see (log
    # lines, file listings, their own clock) is local. Naming it in UTC made
    # a 05:27 scan land as "20260830-1927" -- the previous day -- so looking
    # for this morning's sheets found nothing. Sub-second precision still
    # carries the collision-avoidance, including across a DST repeat.
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output_path = output_dir / f"sheet-{timestamp}.png"

    command = ["scanimage", "--format=png", f"--resolution={resolution}", f"--mode={mode}", "-o", str(output_path)]
    if device:
        command[1:1] = ["-d", device]

    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise ScanError("scanimage not found -- install sane-utils") from None
    except subprocess.TimeoutExpired:
        raise ScanError(f"scan timed out after {timeout}s -- is the scanner powered on and idle?") from None

    if result.returncode != 0:
        raise ScanError(f"scanimage failed (exit {result.returncode}): {result.stderr.strip()}")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise ScanError("scanimage reported success but wrote no image")
    return output_path


def list_devices(timeout: float = 15.0) -> list[str]:
    """Device identifiers scanimage currently sees, e.g.
    ['pixma:04A91913_5E0102']. Empty if none are connected/detected."""
    try:
        result = subprocess.run(["scanimage", "-f", "%d%n"], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise ScanError("scanimage not found -- install sane-utils") from None
    except subprocess.TimeoutExpired:
        raise ScanError(f"device listing timed out after {timeout}s") from None
    return [line for line in result.stdout.splitlines() if line.strip()]
