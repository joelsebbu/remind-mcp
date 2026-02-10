"""Main MCP server for the Linux remind calendar system."""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from remind_mcp.remind_manager import RemindManager
from remind_mcp.daemon_manager import (
    get_daemon_status as _get_daemon_status,
    start_daemon as _start_daemon,
    stop_daemon as _stop_daemon,
    restart_daemon as _restart_daemon,
)

# Initialize the MCP server
mcp = FastMCP(
    "remind-mcp",
    description="MCP server for the Linux remind calendar and reminder system",
)

# Shared manager instance (lazily configured via environment)
_manager: RemindManager | None = None


def _get_manager() -> RemindManager:
    global _manager
    if _manager is None:
        _manager = RemindManager(
            reminders_file=os.environ.get("REMIND_FILE")
        )
    return _manager


def _reminders_file_path() -> str:
    return str(_get_manager().reminders_file)


@mcp.tool()
def add_reminder(
    date: str,
    message: str,
    time: str | None = None,
    recurrence: str | None = None,
    advance_notice: int | None = None,
) -> str:
    """Add a new reminder to the reminders file.

    Args:
        date: When the reminder should trigger. Supports:
            - Specific dates: "2026-02-10", "Feb 10 2026"
            - Relative dates: "tomorrow", "today", "next monday"
            - Recurring: "every monday", "every day", "daily", "weekly", "monthly"
        message: The reminder message text.
        time: Optional time in 24h or 12h format (e.g. "14:30", "2:30pm").
        recurrence: Optional override for recurrence type: "daily", "weekly", or "monthly".
        advance_notice: Optional number of days to show the reminder in advance.

    Returns:
        The REM line that was added to the reminders file.
    """
    try:
        manager = _get_manager()
        rem_line = manager.add_reminder(
            date=date,
            message=message,
            time=time,
            recurrence=recurrence,
            advance_notice=advance_notice,
        )
        return f"Reminder added successfully:\n{rem_line}"
    except ValueError as e:
        return f"Error adding reminder: {e}"
    except OSError as e:
        return f"Error writing to reminders file: {e}"


@mcp.tool()
def list_reminders(date_range: str | None = None) -> str:
    """List upcoming reminders.

    Args:
        date_range: Time range to list. Options:
            - "today": Only today's reminders
            - "week": This week's reminders (default)
            - "month": This month's reminders
            - A specific date like "2026-02-10"

    Returns:
        Formatted list of upcoming reminders.
    """
    try:
        manager = _get_manager()
        reminders = manager.list_reminders(date_range=date_range)

        if not reminders:
            return f"No reminders found for range: {date_range or 'week'}"

        lines = [f"Upcoming reminders ({date_range or 'week'}):", ""]
        for r in reminders:
            time_part = f" at {r.time}" if r.time else ""
            lines.append(f"  {r.date}{time_part}: {r.message}")

        lines.append(f"\nTotal: {len(reminders)} reminder(s)")
        return "\n".join(lines)
    except RuntimeError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error listing reminders: {e}"


@mcp.tool()
def delete_reminder(identifier: str) -> str:
    """Remove a reminder from the reminders file.

    Args:
        identifier: Either a line number (e.g. "3") or a search pattern
            that matches the reminder text. If using a pattern, the first
            matching line will be deleted.

    Returns:
        Confirmation with the deleted reminder content.
    """
    try:
        manager = _get_manager()

        # Show available lines for context
        file_lines = manager.get_file_lines()
        deleted = manager.delete_reminder(identifier)

        return f"Deleted reminder:\n{deleted}"
    except ValueError as e:
        manager = _get_manager()
        file_lines = manager.get_file_lines()
        if file_lines:
            lines_display = "\n".join(
                f"  {fl.line_number}: {fl.content}" for fl in file_lines
            )
            return (
                f"Error: {e}\n\n"
                f"Available reminders:\n{lines_display}"
            )
        return f"Error: {e}"
    except OSError as e:
        return f"Error accessing reminders file: {e}"


@mcp.tool()
def update_reminder(
    identifier: str,
    date: str | None = None,
    message: str | None = None,
    time: str | None = None,
    recurrence: str | None = None,
    advance_notice: int | None = None,
) -> str:
    """Update an existing reminder in the reminders file.

    Finds the reminder by line number or search pattern, then replaces it
    with updated values.  Only the fields you provide will be changed;
    omitted fields keep their current values.

    Args:
        identifier: Either a line number (e.g. "3") or a search pattern
            that matches the reminder text.  If using a pattern, the first
            matching line will be updated.
        date: New date.  Supports the same formats as add_reminder.
        message: New reminder message text.
        time: New time in 24h or 12h format.  Pass empty string "" to remove
            the time from the reminder.
        recurrence: New recurrence type: "daily", "weekly", or "monthly".
        advance_notice: New advance-notice days.  Pass 0 to remove advance
            notice.

    Returns:
        Confirmation showing the old and new reminder lines.
    """
    try:
        manager = _get_manager()
        old_line, new_line = manager.update_reminder(
            identifier=identifier,
            date=date,
            message=message,
            time=time,
            recurrence=recurrence,
            advance_notice=advance_notice,
        )
        return (
            f"Reminder updated successfully:\n"
            f"  Old: {old_line}\n"
            f"  New: {new_line}"
        )
    except ValueError as e:
        manager = _get_manager()
        file_lines = manager.get_file_lines()
        if file_lines:
            lines_display = "\n".join(
                f"  {fl.line_number}: {fl.content}" for fl in file_lines
            )
            return (
                f"Error: {e}\n\n"
                f"Available reminders:\n{lines_display}"
            )
        return f"Error: {e}"
    except OSError as e:
        return f"Error accessing reminders file: {e}"


@mcp.tool()
def trigger_reminder(
    date: str,
    time: str,
    action_type: str,
    action_payload: str,
) -> str:
    """Set up a reminder with a custom action/webhook.

    This adds a reminder and ensures the daemon is running with the
    appropriate notification command.

    Args:
        date: When to trigger. Supports same formats as add_reminder.
        time: Time to trigger (e.g. "14:30", "2:30pm"). Required for triggers.
        action_type: Type of action: "curl", "script", or "command".
        action_payload: The action payload:
            - For "curl": the URL to POST to
            - For "script": the script path
            - For "command": the shell command

    Returns:
        Status of the trigger setup.
    """
    try:
        manager = _get_manager()

        # Build the notification command based on action type
        if action_type == "curl":
            # Validate URL-like payload
            if not action_payload.startswith(("http://", "https://")):
                return "Error: curl action_payload must be a URL starting with http:// or https://"
            notif_cmd = (
                f'curl -s -X POST {action_payload} '
                f'-d \'{{"msg":"%s"}}\' '
                f'-H "Content-Type: application/json"'
            )
        elif action_type == "script":
            if not os.path.isabs(action_payload):
                return "Error: script action_payload must be an absolute path"
            notif_cmd = f'{action_payload} "%s"'
        elif action_type == "command":
            notif_cmd = action_payload
        else:
            return f"Error: Unknown action_type '{action_type}'. Use 'curl', 'script', or 'command'."

        # Add the reminder
        rem_line = manager.add_reminder(date=date, message="Triggered action", time=time)

        # Ensure daemon is running with the notification command
        status = _get_daemon_status()
        daemon_msg = ""
        if not status.running:
            try:
                new_status = _start_daemon(
                    reminders_file=_reminders_file_path(),
                    notification_command=notif_cmd,
                )
                daemon_msg = f"\nDaemon started (PID {new_status.pid}) with action: {action_type}"
            except RuntimeError as e:
                daemon_msg = f"\nWarning: Could not start daemon: {e}"
        else:
            daemon_msg = (
                f"\nDaemon already running (PID {status.pid}). "
                f"Note: existing notification command will be used. "
                f"Restart daemon to change notification command."
            )

        return f"Trigger set up:\n{rem_line}{daemon_msg}"
    except ValueError as e:
        return f"Error setting up trigger: {e}"


@mcp.tool()
def get_daemon_status() -> str:
    """Check if the remind daemon is running.

    Returns:
        Status information including: running state, PID, uptime,
        and notification command.
    """
    status = _get_daemon_status()

    if not status.running:
        return "Remind daemon is NOT running."

    uptime_str = ""
    if status.uptime_seconds is not None:
        hours = int(status.uptime_seconds // 3600)
        minutes = int((status.uptime_seconds % 3600) // 60)
        seconds = int(status.uptime_seconds % 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"

    lines = [
        "Remind daemon is RUNNING",
        f"  PID: {status.pid}",
    ]
    if uptime_str:
        lines.append(f"  Uptime: {uptime_str}")
    if status.notification_command:
        lines.append(f"  Notification command: {status.notification_command}")

    return "\n".join(lines)


@mcp.tool()
def start_daemon(notification_command: str | None = None) -> str:
    """Start the remind daemon with a notification command.

    The daemon monitors the reminders file and executes the notification
    command when a timed reminder fires.

    Args:
        notification_command: Command to run for notifications. Use %s as
            placeholder for the reminder message. Examples:
            - 'notify-send "Reminder" "%s"' (desktop notification, default)
            - 'curl -X POST https://api.example.com/notify -d \'{"msg":"%s"}\'' (webhook)
            - '/path/to/script.sh "%s"' (custom script)

    Returns:
        Status of the started daemon.
    """
    try:
        status = _start_daemon(
            reminders_file=_reminders_file_path(),
            notification_command=notification_command,
        )
        return (
            f"Remind daemon started successfully\n"
            f"  PID: {status.pid}\n"
            f"  Notification: {status.notification_command}"
        )
    except RuntimeError as e:
        return f"Error: {e}"
    except ValueError as e:
        return f"Error (invalid notification command): {e}"


@mcp.tool()
def stop_daemon() -> str:
    """Stop the remind daemon.

    Finds all remind daemon processes and stops them gracefully
    (SIGTERM, then SIGKILL if needed).

    Returns:
        Confirmation with PIDs of stopped processes.
    """
    result = _stop_daemon()
    return result["message"]


@mcp.tool()
def restart_daemon(notification_command: str | None = None) -> str:
    """Restart the remind daemon with optional new configuration.

    Stops any existing daemon processes and starts a new one.

    Args:
        notification_command: Optional new notification command.
            If not provided, uses the default (notify-send).

    Returns:
        Status of the restarted daemon.
    """
    try:
        status = _restart_daemon(
            reminders_file=_reminders_file_path(),
            notification_command=notification_command,
        )
        return (
            f"Remind daemon restarted successfully\n"
            f"  PID: {status.pid}\n"
            f"  Notification: {status.notification_command}"
        )
    except RuntimeError as e:
        return f"Error: {e}"
    except ValueError as e:
        return f"Error (invalid notification command): {e}"


def main():
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
