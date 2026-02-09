"""Wrapper around the Linux remind command for managing reminders."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from dateutil import parser as dateutil_parser
from dateutil.relativedelta import relativedelta, MO, TU, WE, TH, FR, SA, SU

# Mapping for relative day names to remind day abbreviations
DAY_MAP = {
    "monday": "Mon",
    "tuesday": "Tue",
    "wednesday": "Wed",
    "thursday": "Thu",
    "friday": "Fri",
    "saturday": "Sat",
    "sunday": "Sun",
}

# Mapping from dateutil weekday constants
WEEKDAY_CONSTANTS = {
    "monday": MO,
    "tuesday": TU,
    "wednesday": WE,
    "thursday": TH,
    "friday": FR,
    "saturday": SA,
    "sunday": SU,
}

MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


@dataclass
class Reminder:
    """A parsed reminder from remind output."""

    date: str
    time: str | None
    message: str
    raw_line: str


@dataclass
class ReminderFileLine:
    """A line from the reminders file with its line number."""

    line_number: int
    content: str


class RemindManager:
    """Manages the remind command and reminders file."""

    def __init__(self, reminders_file: str | None = None):
        self.reminders_file = Path(
            reminders_file or os.environ.get("REMIND_FILE", "~/.reminders")
        ).expanduser()
        self._ensure_file_exists()

    def _ensure_file_exists(self) -> None:
        """Create the reminders file if it doesn't exist."""
        if not self.reminders_file.exists():
            self.reminders_file.touch()

    def _parse_date(self, date_str: str) -> tuple[str, bool]:
        """Parse a date string into remind format.

        Returns a tuple of (remind_date_part, is_recurring).

        Supports:
        - "tomorrow"
        - "today"
        - "next monday", "next tuesday", etc.
        - "every monday", "every tuesday", etc.
        - "every day" / "daily"
        - "every week" / "weekly"  (uses current weekday)
        - "every month" / "monthly" (uses current day-of-month)
        - ISO dates like "2026-02-10"
        - Natural dates like "Feb 10 2026", "February 10, 2026"
        """
        normalized = date_str.strip().lower()

        if normalized == "today":
            d = datetime.now()
            return f"{d.day} {MONTH_NAMES[d.month - 1]} {d.year}", False

        if normalized == "tomorrow":
            d = datetime.now() + timedelta(days=1)
            return f"{d.day} {MONTH_NAMES[d.month - 1]} {d.year}", False

        # "next <weekday>" - find the next occurrence of that weekday
        next_match = re.match(r"next\s+(\w+)", normalized)
        if next_match:
            day_name = next_match.group(1)
            if day_name in WEEKDAY_CONSTANTS:
                today = datetime.now()
                target = today + relativedelta(weekday=WEEKDAY_CONSTANTS[day_name](+1))
                if target.date() == today.date():
                    target += timedelta(weeks=1)
                return (
                    f"{target.day} {MONTH_NAMES[target.month - 1]} {target.year}",
                    False,
                )

        # "every <weekday>" - recurring weekly on that day
        every_match = re.match(r"every\s+(\w+)", normalized)
        if every_match:
            day_name = every_match.group(1)
            if day_name in DAY_MAP:
                return DAY_MAP[day_name], True
            if day_name == "day":
                return "", True  # REM MSG ... means every day
            if day_name in ("week", "weekly"):
                today = datetime.now()
                day_abbr = DAY_MAP[today.strftime("%A").lower()]
                return day_abbr, True
            if day_name in ("month", "monthly"):
                today = datetime.now()
                return str(today.day), True

        # "daily"
        if normalized == "daily":
            return "", True

        # "weekly"
        if normalized == "weekly":
            today = datetime.now()
            day_abbr = DAY_MAP[today.strftime("%A").lower()]
            return day_abbr, True

        # "monthly"
        if normalized == "monthly":
            today = datetime.now()
            return str(today.day), True

        # Try parsing as a date
        try:
            d = dateutil_parser.parse(date_str)
            return f"{d.day} {MONTH_NAMES[d.month - 1]} {d.year}", False
        except (ValueError, TypeError):
            pass

        raise ValueError(f"Cannot parse date: {date_str!r}")

    def _format_time(self, time_str: str) -> str:
        """Convert time string to remind AT format (HH:MM)."""
        # Try parsing with dateutil
        try:
            t = dateutil_parser.parse(time_str)
            return f"{t.hour:02d}:{t.minute:02d}"
        except (ValueError, TypeError):
            pass

        # Try HH:MM format directly
        match = re.match(r"(\d{1,2}):(\d{2})", time_str)
        if match:
            h, m = int(match.group(1)), int(match.group(2))
            if 0 <= h <= 23 and 0 <= m <= 59:
                return f"{h:02d}:{m:02d}"

        raise ValueError(f"Cannot parse time: {time_str!r}")

    def build_rem_line(
        self,
        date: str,
        message: str,
        time: str | None = None,
        recurrence: str | None = None,
        advance_notice: int | None = None,
    ) -> str:
        """Build a REM line for the reminders file.

        Args:
            date: Date string (natural language or specific date).
            message: Reminder message.
            time: Optional time string.
            recurrence: Optional recurrence type (daily/weekly/monthly).
                        Overrides recurrence detected from date parsing.
            advance_notice: Optional days of advance notice.

        Returns:
            A valid REM line string.
        """
        date_part, is_recurring = self._parse_date(date)

        # If explicit recurrence is given, override
        if recurrence:
            r = recurrence.lower()
            if r == "daily":
                date_part = ""
                is_recurring = True
            elif r == "weekly":
                # If date_part is already a weekday abbreviation keep it,
                # otherwise derive from the parsed date
                if date_part not in DAY_MAP.values():
                    try:
                        d = dateutil_parser.parse(date)
                        date_part = DAY_MAP[d.strftime("%A").lower()]
                    except (ValueError, TypeError):
                        today = datetime.now()
                        date_part = DAY_MAP[today.strftime("%A").lower()]
                is_recurring = True
            elif r == "monthly":
                if date_part not in [str(i) for i in range(1, 32)]:
                    try:
                        d = dateutil_parser.parse(date)
                        date_part = str(d.day)
                    except (ValueError, TypeError):
                        date_part = str(datetime.now().day)
                is_recurring = True

        parts = ["REM"]
        if date_part:
            parts.append(date_part)

        if advance_notice and advance_notice > 0:
            parts.append(f"+{advance_notice}")

        if time:
            formatted_time = self._format_time(time)
            parts.append(f"AT {formatted_time}")

        # Sanitize message: remove newlines, ensure it ends with %
        safe_message = message.replace("\n", " ").replace("\r", "")
        parts.append(f"MSG {safe_message} %")

        return " ".join(parts)

    def add_reminder(
        self,
        date: str,
        message: str,
        time: str | None = None,
        recurrence: str | None = None,
        advance_notice: int | None = None,
    ) -> str:
        """Add a reminder to the reminders file.

        Returns the REM line that was added.
        """
        rem_line = self.build_rem_line(date, message, time, recurrence, advance_notice)
        with open(self.reminders_file, "a") as f:
            f.write(rem_line + "\n")
        return rem_line

    def list_reminders(
        self, date_range: str | None = None
    ) -> list[Reminder]:
        """List upcoming reminders by running remind.

        Args:
            date_range: One of "today", "week", "month", or a specific date.
                        Defaults to "week".

        Returns:
            List of Reminder objects.
        """
        if not self.reminders_file.exists():
            return []

        date_range = (date_range or "week").lower().strip()

        # The -s flag and its argument must be a single token (e.g. "-s+1")
        if date_range == "today":
            cmd = ["remind", "-s+0"]
        elif date_range == "week":
            cmd = ["remind", "-s+1"]  # +1 week
        elif date_range == "month":
            cmd = ["remind", "-s1"]  # 1 month
        else:
            # Treat as a specific date
            cmd = ["remind", "-s+0"]

        cmd.append(str(self.reminders_file))

        # If a specific date was given, append it
        if date_range not in ("today", "week", "month"):
            try:
                d = dateutil_parser.parse(date_range)
                cmd.append(d.strftime("%Y-%m-%d"))
            except (ValueError, TypeError):
                pass

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10
            )
        except FileNotFoundError:
            raise RuntimeError(
                "The 'remind' command is not installed. "
                "Install it with: apt-get install remind"
            )

        reminders: list[Reminder] = []
        for line in result.stdout.strip().splitlines():
            parsed = self._parse_simple_output_line(line)
            if parsed:
                reminders.append(parsed)

        return reminders

    def _parse_simple_output_line(self, line: str) -> Reminder | None:
        """Parse a line from remind -s output.

        Format: YYYY/MM/DD * * * [minutes_past_midnight|*] [time_str] message
        Example: 2026/02/10 * * * 870 2:30pm Deploy
        Example: 2026/02/09 * * * * Daily check
        """
        match = re.match(
            r"(\d{4}/\d{2}/\d{2})\s+\*\s+\*\s+\*\s+(\d+|\*)\s*(.*)",
            line,
        )
        if not match:
            return None

        date_str = match.group(1).replace("/", "-")
        minutes_or_star = match.group(2)
        rest = match.group(3).strip()

        time_str = None
        message = rest

        if minutes_or_star != "*":
            # There's a time component - the time string is the first token
            time_match = re.match(r"(\d{1,2}:\d{2}[ap]m)\s+(.*)", rest)
            if time_match:
                time_str = time_match.group(1)
                message = time_match.group(2)
            else:
                # Convert minutes to HH:MM
                mins = int(minutes_or_star)
                h, m = divmod(mins, 60)
                time_str = f"{h:02d}:{m:02d}"
                message = rest

        return Reminder(
            date=date_str,
            time=time_str,
            message=message.strip(),
            raw_line=line,
        )

    def get_file_lines(self) -> list[ReminderFileLine]:
        """Read the reminders file and return numbered lines."""
        if not self.reminders_file.exists():
            return []
        lines = self.reminders_file.read_text().splitlines()
        return [
            ReminderFileLine(line_number=i + 1, content=line)
            for i, line in enumerate(lines)
            if line.strip()  # skip blank lines
        ]

    def delete_reminder(self, identifier: str) -> str:
        """Delete a reminder by line number or search pattern.

        Args:
            identifier: Either a line number (e.g. "3") or a search pattern.

        Returns:
            The deleted line content.
        """
        lines = self.reminders_file.read_text().splitlines()

        # Try as line number first
        try:
            line_num = int(identifier)
            if 1 <= line_num <= len(lines):
                deleted = lines.pop(line_num - 1)
                self.reminders_file.write_text("\n".join(lines) + "\n" if lines else "")
                return deleted
            else:
                raise ValueError(
                    f"Line number {line_num} out of range (1-{len(lines)})"
                )
        except ValueError as e:
            if "out of range" in str(e):
                raise

        # Search by pattern
        pattern = identifier.lower()
        for i, line in enumerate(lines):
            if pattern in line.lower():
                deleted = lines.pop(i)
                self.reminders_file.write_text("\n".join(lines) + "\n" if lines else "")
                return deleted

        raise ValueError(f"No reminder found matching: {identifier!r}")
