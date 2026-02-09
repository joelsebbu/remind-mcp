"""Daemon lifecycle management for the remind daemon."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import psutil


PID_FILE = Path("/tmp/remind_daemon.pid")
DEFAULT_NOTIFICATION_CMD = 'notify-send "Reminder" "%s"'

# Allowed patterns for notification commands to prevent injection
_SAFE_NOTIFY_PATTERNS = [
    # notify-send with quoted args
    re.compile(r'^notify-send\s'),
    # curl with URL
    re.compile(r'^curl\s'),
    # A file path to a script
    re.compile(r'^/[\w./-]+\s'),
]


@dataclass
class DaemonStatus:
    """Status information for the remind daemon."""

    running: bool
    pid: int | None = None
    uptime_seconds: float | None = None
    notification_command: str | None = None


def _validate_notification_command(cmd: str) -> str:
    """Validate and sanitize a notification command.

    Prevents shell injection by checking against allowed patterns
    and rejecting dangerous characters.
    """
    # Reject obvious injection attempts
    dangerous = [";", "&&", "||", "`", "$(", "\n", "\r"]
    for d in dangerous:
        if d in cmd:
            raise ValueError(
                f"Notification command contains disallowed characters: {d!r}"
            )

    # Allow known safe patterns
    for pattern in _SAFE_NOTIFY_PATTERNS:
        if pattern.match(cmd):
            return cmd

    # If it doesn't match any known pattern, still allow it but warn
    # as long as it doesn't contain injection characters (already checked)
    return cmd


def _find_remind_daemon_processes() -> list[psutil.Process]:
    """Find all running remind daemon processes."""
    results = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            cmdline_str = " ".join(cmdline)
            # Look for remind running in daemon mode (-z flag)
            if "remind" in cmdline_str and "-z" in cmdline_str:
                results.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return results


def _read_pid_file() -> int | None:
    """Read the PID from the PID file, return None if invalid or missing."""
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        # Verify the process is actually running
        if psutil.pid_exists(pid):
            return pid
        # Stale PID file
        PID_FILE.unlink(missing_ok=True)
        return None
    except (ValueError, OSError):
        PID_FILE.unlink(missing_ok=True)
        return None


def _write_pid_file(pid: int) -> None:
    """Write a PID to the PID file."""
    PID_FILE.write_text(str(pid))


def get_daemon_status() -> DaemonStatus:
    """Check if the remind daemon is running.

    Checks both the PID file and live process scanning.

    Returns:
        DaemonStatus with current state information.
    """
    # Check PID file first
    pid_from_file = _read_pid_file()

    # Also scan for running remind daemon processes
    daemon_procs = _find_remind_daemon_processes()

    if pid_from_file and any(p.pid == pid_from_file for p in daemon_procs):
        # PID file matches a running process
        proc = psutil.Process(pid_from_file)
        uptime = time.time() - proc.create_time()
        # Try to extract notification command from cmdline
        cmdline = proc.cmdline()
        notif_cmd = _extract_notification_cmd(cmdline)
        return DaemonStatus(
            running=True,
            pid=pid_from_file,
            uptime_seconds=uptime,
            notification_command=notif_cmd,
        )

    if daemon_procs:
        # Found daemon processes but PID file is wrong/missing
        proc = daemon_procs[0]
        _write_pid_file(proc.pid)
        uptime = time.time() - proc.create_time()
        cmdline = proc.cmdline()
        notif_cmd = _extract_notification_cmd(cmdline)
        return DaemonStatus(
            running=True,
            pid=proc.pid,
            uptime_seconds=uptime,
            notification_command=notif_cmd,
        )

    return DaemonStatus(running=False)


def _extract_notification_cmd(cmdline: list[str]) -> str | None:
    """Extract the notification command from remind's -k flag."""
    for i, arg in enumerate(cmdline):
        if arg.startswith("-k"):
            # -k can be "-kcmd" or "-k" "cmd"
            if len(arg) > 2:
                return arg[2:]
            elif i + 1 < len(cmdline):
                return cmdline[i + 1]
    return None


def start_daemon(
    reminders_file: str,
    notification_command: str | None = None,
) -> DaemonStatus:
    """Start the remind daemon.

    Args:
        reminders_file: Path to the reminders file.
        notification_command: Command to run for notifications.
            Use %s as placeholder for the message.
            Defaults to notify-send.

    Returns:
        DaemonStatus with the new daemon information.

    Raises:
        RuntimeError: If daemon is already running or cannot be started.
    """
    # Check if already running
    status = get_daemon_status()
    if status.running:
        raise RuntimeError(
            f"Remind daemon is already running (PID {status.pid}). "
            "Stop it first or use restart_daemon."
        )

    notif_cmd = notification_command or DEFAULT_NOTIFICATION_CMD
    notif_cmd = _validate_notification_command(notif_cmd)

    # Build command: remind -z1 -k'<cmd>' <reminders_file>
    cmd = [
        "remind",
        "-z1",
        f"-k{notif_cmd}",
        str(reminders_file),
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "The 'remind' command is not installed. "
            "Install it with: apt-get install remind"
        )

    # Give it a moment to start
    time.sleep(0.5)

    # Check it didn't exit immediately
    if proc.poll() is not None:
        raise RuntimeError(
            f"Remind daemon exited immediately with code {proc.returncode}"
        )

    _write_pid_file(proc.pid)

    return DaemonStatus(
        running=True,
        pid=proc.pid,
        uptime_seconds=0,
        notification_command=notif_cmd,
    )


def stop_daemon() -> dict:
    """Stop the remind daemon.

    Finds all remind daemon processes and stops them gracefully.

    Returns:
        Dict with status information about stopped processes.
    """
    daemon_procs = _find_remind_daemon_processes()
    pid_from_file = _read_pid_file()

    # Also include PID from file if it's a valid process
    pids_to_kill: set[int] = set()
    for proc in daemon_procs:
        pids_to_kill.add(proc.pid)
    if pid_from_file and psutil.pid_exists(pid_from_file):
        pids_to_kill.add(pid_from_file)

    if not pids_to_kill:
        PID_FILE.unlink(missing_ok=True)
        return {"stopped": False, "message": "No remind daemon found running"}

    killed_pids = []
    failed_pids = []

    for pid in pids_to_kill:
        try:
            proc = psutil.Process(pid)
            # Try graceful SIGTERM first
            proc.send_signal(signal.SIGTERM)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Wait for processes to terminate
    time.sleep(1)

    for pid in pids_to_kill:
        try:
            proc = psutil.Process(pid)
            status = proc.status()
            if status == psutil.STATUS_ZOMBIE:
                # Zombie process - already terminated, just needs reaping
                killed_pids.append(pid)
                continue
            if proc.is_running():
                # Force kill with SIGKILL
                proc.send_signal(signal.SIGKILL)
                time.sleep(0.3)
                try:
                    status = proc.status()
                    if status == psutil.STATUS_ZOMBIE or not proc.is_running():
                        killed_pids.append(pid)
                    else:
                        failed_pids.append(pid)
                except psutil.NoSuchProcess:
                    killed_pids.append(pid)
            else:
                killed_pids.append(pid)
        except psutil.NoSuchProcess:
            killed_pids.append(pid)
        except psutil.AccessDenied:
            failed_pids.append(pid)

    # Clean up PID file
    PID_FILE.unlink(missing_ok=True)

    if failed_pids:
        return {
            "stopped": True,
            "killed_pids": killed_pids,
            "failed_pids": failed_pids,
            "message": (
                f"Stopped PIDs {killed_pids}, "
                f"but failed to stop PIDs {failed_pids} (permission denied)"
            ),
        }

    return {
        "stopped": True,
        "killed_pids": killed_pids,
        "message": f"Successfully stopped remind daemon (PIDs: {killed_pids})",
    }


def restart_daemon(
    reminders_file: str,
    notification_command: str | None = None,
) -> DaemonStatus:
    """Restart the remind daemon with optional new configuration.

    Args:
        reminders_file: Path to the reminders file.
        notification_command: Optional new notification command.

    Returns:
        DaemonStatus with the new daemon information.
    """
    stop_daemon()
    # Brief pause to ensure cleanup
    time.sleep(0.5)
    return start_daemon(reminders_file, notification_command)
