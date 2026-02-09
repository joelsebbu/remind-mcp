# remind-mcp

An MCP (Model Context Protocol) server for the Linux `remind` command-line calendar and reminder system.

## Overview

This server exposes the powerful `remind` utility as a set of MCP tools, allowing AI assistants to manage reminders, schedule notifications, and control the remind daemon — all without needing to know remind's native syntax.

## Prerequisites

- Python 3.10+
- Linux `remind` package (`apt-get install remind`)
- Optional: `notify-send` for desktop notifications (usually from `libnotify-bin`)

## Installation

```bash
# Clone the repository
git clone <repo-url>
cd remind-mcp

# Install the package
pip install .
```

Or install in development mode:

```bash
pip install -e .
```

## Configuration

### Claude Desktop

Add this to your Claude Desktop config file (`~/.config/claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "remind": {
      "command": "remind-mcp",
      "env": {
        "REMIND_FILE": "~/.reminders"
      }
    }
  }
}
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REMIND_FILE` | Path to the reminders file | `~/.reminders` |

## Tools

### `add_reminder`

Add a new reminder to the reminders file.

**Parameters:**
- `date` (required): When the reminder should trigger. Supports:
  - Specific dates: `"2026-02-10"`, `"Feb 10 2026"`
  - Relative dates: `"tomorrow"`, `"today"`, `"next monday"`
  - Recurring: `"every monday"`, `"every day"`, `"daily"`, `"weekly"`, `"monthly"`
- `message` (required): The reminder message text
- `time` (optional): Time in 24h or 12h format (`"14:30"`, `"2:30pm"`)
- `recurrence` (optional): Override recurrence: `"daily"`, `"weekly"`, `"monthly"`
- `advance_notice` (optional): Days to show the reminder in advance

**Examples:**
```
add_reminder(date="2026-02-10", time="14:30", message="Deploy to production")
add_reminder(date="every monday", time="09:00", message="Weekly standup")
add_reminder(date="tomorrow", message="Buy groceries", advance_notice=1)
```

### `list_reminders`

List upcoming reminders.

**Parameters:**
- `date_range` (optional): `"today"`, `"week"` (default), `"month"`, or a specific date

**Example:**
```
list_reminders(date_range="week")
```

### `delete_reminder`

Remove a reminder by line number or search pattern.

**Parameters:**
- `identifier` (required): Line number (e.g. `"3"`) or text pattern (e.g. `"standup"`)

**Example:**
```
delete_reminder(identifier="standup")
delete_reminder(identifier="2")
```

### `trigger_reminder`

Set up a reminder with a custom action/webhook. Starts the daemon if needed.

**Parameters:**
- `date` (required): When to trigger
- `time` (required): Time to trigger
- `action_type` (required): `"curl"`, `"script"`, or `"command"`
- `action_payload` (required): URL, script path, or command

**Examples:**
```
trigger_reminder(
    date="2026-02-10",
    time="14:30",
    action_type="curl",
    action_payload="https://api.example.com/notify"
)

trigger_reminder(
    date="tomorrow",
    time="09:00",
    action_type="script",
    action_payload="/usr/local/bin/my_notify.sh"
)
```

### `get_daemon_status`

Check if the remind daemon is running.

**Returns:** Running state, PID, uptime, and notification command.

### `start_daemon`

Start the remind daemon with a notification command.

**Parameters:**
- `notification_command` (optional): Command with `%s` placeholder for the message. Defaults to `notify-send "Reminder" "%s"`.

**Examples:**
```
start_daemon()
start_daemon(notification_command='curl -X POST https://api.example.com/notify -d \'{"msg":"%s"}\'')
start_daemon(notification_command='/path/to/script.sh "%s"')
```

### `stop_daemon`

Stop all running remind daemon processes (SIGTERM, then SIGKILL fallback).

### `restart_daemon`

Restart the daemon with optional new configuration.

**Parameters:**
- `notification_command` (optional): New notification command for the restarted daemon.

## How It Works

This server translates natural-language-style parameters into remind's native `REM` syntax:

| Input | Generated REM line |
|-------|-------------------|
| `date="2026-02-10", time="14:30", message="Deploy"` | `REM 10 Feb 2026 AT 14:30 MSG Deploy %` |
| `date="every monday", time="09:00", message="Standup"` | `REM Mon AT 09:00 MSG Standup %` |
| `date="daily", message="Check email"` | `REM MSG Check email %` |
| `date="2026-03-01", message="Report", advance_notice=3` | `REM 1 Mar 2026 +3 MSG Report %` |

## Security

- Notification commands are validated against shell injection patterns
- Characters like `;`, `&&`, `||`, `` ` ``, and `$(` are rejected
- User inputs are sanitized before being written to the reminders file
- Daemon processes are properly tracked via PID files

## Running Tests

```bash
pip install pytest
pytest tests/
```

## License

MIT
