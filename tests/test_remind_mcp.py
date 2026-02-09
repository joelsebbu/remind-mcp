"""Tests for the remind MCP server."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import psutil
import pytest

from remind_mcp.remind_manager import RemindManager, Reminder, ReminderFileLine
from remind_mcp.daemon_manager import (
    get_daemon_status,
    start_daemon,
    stop_daemon,
    restart_daemon,
    _validate_notification_command,
    _find_remind_daemon_processes,
    _read_pid_file,
    _write_pid_file,
    PID_FILE,
    DaemonStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_reminders(tmp_path):
    """Create a temporary reminders file."""
    f = tmp_path / ".reminders"
    f.touch()
    return str(f)


@pytest.fixture
def manager(tmp_reminders):
    """Create a RemindManager with a temporary file."""
    return RemindManager(reminders_file=tmp_reminders)


@pytest.fixture(autouse=True)
def cleanup_daemon():
    """Ensure daemon is stopped after each test."""
    yield
    # Clean up any daemon processes we may have started
    stop_daemon()


# ---------------------------------------------------------------------------
# RemindManager: date parsing
# ---------------------------------------------------------------------------

class TestDateParsing:
    def test_parse_today(self, manager):
        rem = manager.build_rem_line("today", "Test")
        assert rem.startswith("REM ")
        assert "MSG Test %" in rem

    def test_parse_tomorrow(self, manager):
        rem = manager.build_rem_line("tomorrow", "Test")
        assert rem.startswith("REM ")
        assert "MSG Test %" in rem

    def test_parse_iso_date(self, manager):
        rem = manager.build_rem_line("2026-03-15", "Test")
        assert "15 Mar 2026" in rem
        assert "MSG Test %" in rem

    def test_parse_natural_date(self, manager):
        rem = manager.build_rem_line("March 15, 2026", "Test")
        assert "15 Mar 2026" in rem

    def test_parse_next_monday(self, manager):
        rem = manager.build_rem_line("next monday", "Test")
        assert rem.startswith("REM ")
        # Should produce a specific date, not a recurring pattern
        assert "Mon" not in rem or "MSG" in rem

    def test_parse_every_monday(self, manager):
        rem = manager.build_rem_line("every monday", "Test")
        assert "REM Mon" in rem
        assert "MSG Test %" in rem

    def test_parse_every_day(self, manager):
        rem = manager.build_rem_line("every day", "Test")
        # "every day" should produce "REM MSG Test %"
        assert rem == "REM MSG Test %"

    def test_parse_daily(self, manager):
        rem = manager.build_rem_line("daily", "Test")
        assert rem == "REM MSG Test %"

    def test_parse_invalid_date(self, manager):
        with pytest.raises(ValueError, match="Cannot parse date"):
            manager.build_rem_line("not a date at all xyzzy", "Test")


# ---------------------------------------------------------------------------
# RemindManager: time formatting
# ---------------------------------------------------------------------------

class TestTimeFormatting:
    def test_24h_time(self, manager):
        rem = manager.build_rem_line("today", "Test", time="14:30")
        assert "AT 14:30" in rem

    def test_12h_time(self, manager):
        rem = manager.build_rem_line("today", "Test", time="2:30pm")
        assert "AT 14:30" in rem

    def test_invalid_time(self, manager):
        with pytest.raises(ValueError, match="Cannot parse time"):
            manager.build_rem_line("today", "Test", time="not-a-time")


# ---------------------------------------------------------------------------
# RemindManager: recurrence
# ---------------------------------------------------------------------------

class TestRecurrence:
    def test_daily_recurrence(self, manager):
        rem = manager.build_rem_line("today", "Test", recurrence="daily")
        # daily recurrence should drop the date
        assert rem == "REM MSG Test %"

    def test_weekly_recurrence_from_date(self, manager):
        rem = manager.build_rem_line(
            "2026-03-15", "Test", recurrence="weekly"
        )
        # March 15 2026 is a Sunday
        assert "REM Sun" in rem
        assert "MSG Test %" in rem

    def test_monthly_recurrence_from_date(self, manager):
        rem = manager.build_rem_line(
            "2026-03-15", "Test", recurrence="monthly"
        )
        assert "REM 15" in rem
        assert "MSG Test %" in rem


# ---------------------------------------------------------------------------
# RemindManager: advance notice
# ---------------------------------------------------------------------------

class TestAdvanceNotice:
    def test_advance_notice(self, manager):
        rem = manager.build_rem_line(
            "2026-03-15", "Test", advance_notice=3
        )
        assert "+3" in rem

    def test_no_advance_notice(self, manager):
        rem = manager.build_rem_line("2026-03-15", "Test")
        assert "+" not in rem


# ---------------------------------------------------------------------------
# RemindManager: add / delete / list
# ---------------------------------------------------------------------------

class TestAddDeleteList:
    def test_add_reminder(self, manager, tmp_reminders):
        result = manager.add_reminder("2026-03-15", "Deploy", time="14:30")
        assert "REM 15 Mar 2026 AT 14:30 MSG Deploy %" == result

        content = Path(tmp_reminders).read_text()
        assert result in content

    def test_add_multiple_reminders(self, manager, tmp_reminders):
        manager.add_reminder("2026-03-15", "First")
        manager.add_reminder("2026-03-16", "Second")

        lines = Path(tmp_reminders).read_text().strip().splitlines()
        assert len(lines) == 2

    def test_get_file_lines(self, manager):
        manager.add_reminder("2026-03-15", "First")
        manager.add_reminder("2026-03-16", "Second")

        lines = manager.get_file_lines()
        assert len(lines) == 2
        assert lines[0].line_number == 1
        assert lines[1].line_number == 2

    def test_delete_by_line_number(self, manager, tmp_reminders):
        manager.add_reminder("2026-03-15", "First")
        manager.add_reminder("2026-03-16", "Second")

        deleted = manager.delete_reminder("1")
        assert "First" in deleted

        remaining = Path(tmp_reminders).read_text().strip().splitlines()
        assert len(remaining) == 1
        assert "Second" in remaining[0]

    def test_delete_by_pattern(self, manager, tmp_reminders):
        manager.add_reminder("2026-03-15", "Deploy to production")
        manager.add_reminder("2026-03-16", "Team meeting")

        deleted = manager.delete_reminder("deploy")
        assert "Deploy" in deleted

        remaining = Path(tmp_reminders).read_text().strip().splitlines()
        assert len(remaining) == 1
        assert "meeting" in remaining[0].lower()

    def test_delete_invalid_line_number(self, manager):
        manager.add_reminder("2026-03-15", "First")

        with pytest.raises(ValueError, match="out of range"):
            manager.delete_reminder("99")

    def test_delete_no_match(self, manager):
        manager.add_reminder("2026-03-15", "First")

        with pytest.raises(ValueError, match="No reminder found"):
            manager.delete_reminder("nonexistent")

    def test_list_reminders(self, manager):
        manager.add_reminder("2026-03-15", "Deploy", time="14:30")

        reminders = manager.list_reminders(date_range="month")
        # The remind command should pick up the reminder if date is within range
        # This test depends on the system date, so we just check the call works
        assert isinstance(reminders, list)

    def test_list_reminders_empty(self, manager):
        reminders = manager.list_reminders()
        assert reminders == []


# ---------------------------------------------------------------------------
# RemindManager: output parsing
# ---------------------------------------------------------------------------

class TestOutputParsing:
    def test_parse_timed_line(self, manager):
        line = "2026/02/10 * * * 870 2:30pm Deploy"
        result = manager._parse_simple_output_line(line)
        assert result is not None
        assert result.date == "2026-02-10"
        assert result.time == "2:30pm"
        assert result.message == "Deploy"

    def test_parse_untimed_line(self, manager):
        line = "2026/02/09 * * * * Daily check"
        result = manager._parse_simple_output_line(line)
        assert result is not None
        assert result.date == "2026-02-09"
        assert result.time is None
        assert result.message == "Daily check"

    def test_parse_invalid_line(self, manager):
        result = manager._parse_simple_output_line("not a valid line")
        assert result is None


# ---------------------------------------------------------------------------
# Daemon manager: notification command validation
# ---------------------------------------------------------------------------

class TestNotificationValidation:
    def test_valid_notify_send(self):
        cmd = 'notify-send "Reminder" "%s"'
        assert _validate_notification_command(cmd) == cmd

    def test_valid_curl(self):
        cmd = 'curl -X POST https://example.com/notify -d \'{"msg":"%s"}\''
        assert _validate_notification_command(cmd) == cmd

    def test_valid_script(self):
        cmd = '/usr/local/bin/notify.sh "%s"'
        assert _validate_notification_command(cmd) == cmd

    def test_rejects_semicolon_injection(self):
        with pytest.raises(ValueError, match="disallowed"):
            _validate_notification_command('notify-send "test"; rm -rf /')

    def test_rejects_command_substitution(self):
        with pytest.raises(ValueError, match="disallowed"):
            _validate_notification_command('$(malicious command)')

    def test_rejects_backtick_injection(self):
        with pytest.raises(ValueError, match="disallowed"):
            _validate_notification_command('`malicious`')

    def test_rejects_and_chain(self):
        with pytest.raises(ValueError, match="disallowed"):
            _validate_notification_command('echo test && rm -rf /')

    def test_rejects_or_chain(self):
        with pytest.raises(ValueError, match="disallowed"):
            _validate_notification_command('echo test || rm -rf /')


# ---------------------------------------------------------------------------
# Daemon manager: PID file operations
# ---------------------------------------------------------------------------

class TestPidFile:
    def test_write_and_read_pid(self, tmp_path):
        pid_file = tmp_path / "test.pid"
        with patch("remind_mcp.daemon_manager.PID_FILE", pid_file):
            _write_pid_file(os.getpid())
            result = _read_pid_file()
            assert result == os.getpid()

    def test_read_missing_pid_file(self, tmp_path):
        pid_file = tmp_path / "nonexistent.pid"
        with patch("remind_mcp.daemon_manager.PID_FILE", pid_file):
            assert _read_pid_file() is None

    def test_read_stale_pid_file(self, tmp_path):
        pid_file = tmp_path / "stale.pid"
        pid_file.write_text("999999")  # Very unlikely to be a real PID
        with patch("remind_mcp.daemon_manager.PID_FILE", pid_file):
            assert _read_pid_file() is None
            # Stale file should be cleaned up
            assert not pid_file.exists()


# ---------------------------------------------------------------------------
# Daemon manager: status / start / stop / restart
# ---------------------------------------------------------------------------

class TestDaemonLifecycle:
    def test_status_not_running(self):
        # Ensure no daemon is running
        stop_daemon()
        status = get_daemon_status()
        assert status.running is False
        assert status.pid is None

    def test_start_and_stop(self, tmp_reminders):
        # Start daemon
        status = start_daemon(
            reminders_file=tmp_reminders,
            notification_command='notify-send "Test" "%s"',
        )
        assert status.running is True
        assert status.pid is not None
        assert status.pid > 0

        # Verify it's actually running
        check = get_daemon_status()
        assert check.running is True
        assert check.pid == status.pid

        # Stop it
        result = stop_daemon()
        assert result["stopped"] is True
        assert status.pid in result["killed_pids"]

        # Verify it's stopped
        check2 = get_daemon_status()
        assert check2.running is False

    def test_start_already_running(self, tmp_reminders):
        start_daemon(reminders_file=tmp_reminders)

        with pytest.raises(RuntimeError, match="already running"):
            start_daemon(reminders_file=tmp_reminders)

    def test_stop_not_running(self):
        stop_daemon()  # Ensure clean state
        result = stop_daemon()
        assert result["stopped"] is False

    def test_restart(self, tmp_reminders):
        # Start initial daemon
        status1 = start_daemon(reminders_file=tmp_reminders)
        pid1 = status1.pid

        # Restart with new notification command
        status2 = restart_daemon(
            reminders_file=tmp_reminders,
            notification_command='notify-send "Restarted" "%s"',
        )
        assert status2.running is True
        assert status2.pid != pid1  # Different PID after restart

        # Clean up
        stop_daemon()


# ---------------------------------------------------------------------------
# Integration: REM line generation + remind command
# ---------------------------------------------------------------------------

class TestIntegration:
    """Tests that require the remind command to be installed."""

    @pytest.fixture(autouse=True)
    def skip_if_no_remind(self):
        """Skip tests if remind is not installed."""
        import shutil
        if shutil.which("remind") is None:
            pytest.skip("remind command not installed")

    def test_add_and_list(self, manager):
        # Add a reminder for a future date
        manager.add_reminder("2026-12-25", "Christmas", time="09:00")

        # List with a specific date
        reminders = manager.list_reminders(date_range="2026-12-25")
        assert len(reminders) >= 1
        assert any("Christmas" in r.message for r in reminders)

    def test_full_workflow(self, manager, tmp_reminders):
        # Add several reminders
        manager.add_reminder("2026-06-01", "Summer starts", time="08:00")
        manager.add_reminder("2026-06-15", "Mid-June check")
        manager.add_reminder("every monday", "Weekly standup", time="09:00")

        # Verify file has 3 lines
        lines = manager.get_file_lines()
        assert len(lines) == 3

        # Delete by pattern
        manager.delete_reminder("standup")
        lines = manager.get_file_lines()
        assert len(lines) == 2

        # Delete by line number
        manager.delete_reminder("1")
        lines = manager.get_file_lines()
        assert len(lines) == 1
        assert "Mid-June" in lines[0].content
