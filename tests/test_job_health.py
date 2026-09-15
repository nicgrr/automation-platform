"""Reading whether the background jobs are actually running.

Every case here is one the real six-day outage either hit or would have
slipped past, so the fakes are shaped like real `systemctl show` output
rather than convenient dictionaries.
"""

from datetime import datetime, timedelta

import pytest

from automation_control import job_health
from automation_control.job_health import FAILED, OK, OVERDUE, RUNNING, UNKNOWN, Job, status_for

TIMED = Job("x.service", "x.timer", "Price backfill", "prices go stale")
ALWAYS_ON = Job("y.service", None, "Scan ingest", "sheets pile up")
NOW = datetime(2026, 9, 14, 16, 0, 0)


def _fake_show(units: dict[str, dict[str, str]]):
    def show(unit, properties):
        return units.get(unit, {})
    return show


def _install(monkeypatch, units):
    monkeypatch.setattr(job_health, "_show", _fake_show(units))


def test_a_healthy_scheduled_job_is_ok(monkeypatch):
    _install(monkeypatch, {
        "x.service": {"Result": "success", "ActiveState": "inactive", "ExecMainStatus": "0",
                      "ExecMainExitTimestamp": "Mon 2026-09-14 03:53:22 AEST"},
        "x.timer": {"NextElapseUSecRealtime": "Tue 2026-09-15 03:21:29 AEST"},
    })
    status = status_for(TIMED, NOW)
    assert status.state == OK
    assert status.last_run == datetime(2026, 9, 14, 3, 53, 22)
    assert status.next_run == datetime(2026, 9, 15, 3, 21, 29)
    assert status.needs_attention is False


def test_a_nonzero_exit_is_failed_even_when_result_says_success(monkeypatch):
    """`systemctl reset-failed` clears Result back to "success" while
    ExecMainStatus keeps the non-zero exit. Trusting Result alone puts a
    green tick over a job that failed hours ago -- which is the exact state
    this box was in when the module was written."""
    _install(monkeypatch, {
        "x.service": {"Result": "success", "ActiveState": "inactive", "ExecMainStatus": "1",
                      "ExecMainExitTimestamp": "Sun 2026-09-13 12:00:00 AEST"},
        "x.timer": {"NextElapseUSecRealtime": "Mon 2026-09-14 12:00:00 AEST"},
    })
    status = status_for(TIMED, NOW)
    assert status.state == FAILED
    assert status.exit_code == 1
    # ...and it must not print the contradiction back at the reader.
    assert status.detail == "last run exited 1"
    assert "success" not in status.detail


def test_a_failed_result_is_failed_even_with_a_zero_exit(monkeypatch):
    """A timeout or an OOM kill leaves Result set without a useful exit."""
    _install(monkeypatch, {
        "x.service": {"Result": "timeout", "ActiveState": "failed", "ExecMainStatus": "0",
                      "ExecMainExitTimestamp": "Sun 2026-09-13 12:00:00 AEST"},
        "x.timer": {"NextElapseUSecRealtime": "Mon 2026-09-14 12:00:00 AEST"},
    })
    assert status_for(TIMED, NOW).state == FAILED


def test_a_stopped_timer_is_overdue_not_ok(monkeypatch):
    """The quiet failure: nothing failed because nothing ran. Every other
    signal looks perfect."""
    _install(monkeypatch, {
        "x.service": {"Result": "success", "ActiveState": "inactive", "ExecMainStatus": "0",
                      "ExecMainExitTimestamp": "Sun 2026-08-30 12:00:00 AEST"},
        "x.timer": {},
    })
    status = status_for(TIMED, NOW)
    assert status.state == OVERDUE
    assert "stopped or disabled" in status.detail


def test_a_timer_whose_next_run_is_long_past_is_overdue(monkeypatch):
    _install(monkeypatch, {
        "x.service": {"Result": "success", "ActiveState": "inactive", "ExecMainStatus": "0",
                      "ExecMainExitTimestamp": "Sun 2026-09-06 12:00:00 AEST"},
        "x.timer": {"NextElapseUSecRealtime": "Sun 2026-09-07 12:00:00 AEST"},
    })
    status = status_for(TIMED, NOW)
    assert status.state == OVERDUE
    assert "7 days" in status.detail


def test_a_slightly_late_timer_is_still_ok(monkeypatch):
    """OnCalendar drifts with RandomizedDelaySec and Persistent catches up
    after a sleep -- being an hour late is not a fault."""
    _install(monkeypatch, {
        "x.service": {"Result": "success", "ActiveState": "inactive", "ExecMainStatus": "0",
                      "ExecMainExitTimestamp": "Mon 2026-09-14 03:00:00 AEST"},
        "x.timer": {"NextElapseUSecRealtime": "Mon 2026-09-14 15:00:00 AEST"},
    })
    assert status_for(TIMED, NOW).state == OK


def test_a_job_running_now_is_not_reported_as_yesterdays_failure(monkeypatch):
    """A oneshot mid-run still reports the *previous* exit code."""
    _install(monkeypatch, {
        "x.service": {"Result": "success", "ActiveState": "activating", "ExecMainStatus": "1",
                      "ExecMainExitTimestamp": "Sun 2026-09-13 12:00:00 AEST"},
        "x.timer": {"NextElapseUSecRealtime": "Tue 2026-09-15 12:00:00 AEST"},
    })
    status = status_for(TIMED, NOW)
    assert status.state == RUNNING
    assert status.needs_attention is False


def test_an_always_on_service_that_stopped_is_failed(monkeypatch):
    _install(monkeypatch, {"y.service": {"Result": "success", "ActiveState": "inactive",
                                         "ExecMainStatus": "0"}})
    status = status_for(ALWAYS_ON, NOW)
    assert status.state == FAILED
    assert "not running" in status.detail


def test_an_always_on_service_that_is_up_is_ok(monkeypatch):
    _install(monkeypatch, {"y.service": {"Result": "success", "ActiveState": "active",
                                         "ExecMainStatus": "0"}})
    assert status_for(ALWAYS_ON, NOW).state == OK


def test_an_unreadable_unit_is_unknown_not_a_crash(monkeypatch):
    """No systemd, a renamed unit or a hung query must not take the
    dashboard down with it."""
    _install(monkeypatch, {})
    status = status_for(TIMED, NOW)
    assert status.state == UNKNOWN
    assert status.needs_attention is False


def test_a_systemctl_that_will_not_run_is_survived(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("systemctl: not found")

    monkeypatch.setattr(job_health.subprocess, "run", boom)
    assert status_for(TIMED, NOW).state == UNKNOWN


@pytest.mark.parametrize("value", ["", "n/a", "0", "garbage", "Mon"])
def test_unparseable_timestamps_are_none(value):
    assert job_health._parse_timestamp(value) is None


def test_ebay_listings_cache_is_one_of_the_monitored_jobs():
    """Added alongside its timer, the same day: the dashboard widget and
    /settings' listing count were only ever as fresh as the last manual
    click before this timer existed."""
    from automation_control.job_health import JOBS

    labels = [job.label for job in JOBS]
    assert "eBay listings cache" in labels
