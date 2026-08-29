"""ANSI terminal styling: colors, padding, and section headers/formatting
helpers shared across the dashboard's section renderers."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

SECTION_GAP = ""  # blank line inserted between sections

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


class Style:
    """ANSI escape codes for terminal styling."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    GREY = "\033[90m"

    @staticmethod
    def colored(text: str, color: str) -> str:
        """
        Wrap text in an ANSI color sequence.

        Args:
            text: Text to wrap.
            color: One of this class's ANSI color constants.

        Returns:
            text wrapped in color, followed by RESET.
        """
        return f"{color}{text}{Style.RESET}"


def _visible_len(s: str) -> int:
    """
    Return the visible length of a string, excluding ANSI escape codes.

    Args:
        s: String to measure, possibly containing ANSI escape sequences.

    Returns:
        Length of s as it would appear on screen.
    """
    return len(_ANSI_RE.sub("", s))


def pad_right(s: str, width: int) -> str:
    """
    Right-pad string with spaces to reach visible width, accounting for ANSI.

    Args:
        s: String to pad, possibly containing ANSI escape sequences.
        width: Target visible width.

    Returns:
        s padded with trailing spaces so its visible length is width (or s
        unchanged if it's already at or past that width).
    """
    visible = _visible_len(s)
    if visible < width:
        return s + " " * (width - visible)
    return s


def section_header(title: str) -> list[str]:
    """
    Return a blank separator followed by a styled section header.

    Args:
        title: Section title to display.

    Returns:
        The two lines to prepend to a section's body.
    """
    return [SECTION_GAP, f"{Style.CYAN}{Style.BOLD}[{title}]{Style.RESET}"]


def format_duration(secs: int) -> str:
    """
    Format seconds into a human-readable duration string.

    Args:
        secs: Duration in seconds.

    Returns:
        A compact duration string, e.g. "2d 3h 14m 5s" or "45s".
    """
    if secs >= 86400:
        days = secs // 86400
        rem = secs % 86400
        hrs, rem = divmod(rem, 3600)
        mins, s = divmod(rem, 60)
        return f"{days}d {hrs}h {mins}m {s}s"
    hrs, rem = divmod(secs, 3600)
    mins, s = divmod(rem, 60)
    if hrs > 0:
        return f"{hrs}h {mins}m {s}s"
    elif mins > 0:
        return f"{mins}m {s}s"
    return f"{s}s"


def format_age(td: timedelta) -> str:
    """
    Format time duration with smart granularity.

    Args:
        td: Elapsed time to format.

    Returns:
        A short "N<unit> ago" string at whichever granularity (seconds,
        minutes, hours, days) best fits td.
    """
    total_secs = int(td.total_seconds())
    if total_secs < 60:
        return f"{total_secs}s ago"
    elif total_secs < 3600:
        mins = total_secs // 60
        return f"{mins}m ago"
    elif total_secs < 86400:
        hrs = total_secs // 3600
        return f"{hrs}h ago"
    else:
        days = total_secs // 86400
        return f"{days}d ago"


def age_since(ts_str: str | None) -> timedelta | None:
    """
    Parse an adsb-stats UTC timestamp and return how long ago it was.

    Args:
        ts_str: Timestamp in adsb_stats.ingest's "%Y-%m-%dT%H:%M:%SZ"
            format, or None.

    Returns:
        Elapsed time since ts_str, or None if ts_str is None or malformed.
    """
    if not ts_str:
        return None
    try:
        dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return datetime.now(timezone.utc) - dt


def temp_color(temp_c: float) -> str:
    """Return a threshold-based ANSI color for a Celsius temperature."""
    if temp_c > 80:
        return Style.RED
    elif temp_c > 65:
        return Style.YELLOW
    return Style.GREEN


def usage_color(pct: int) -> str:
    """Return ANSI color based on usage percentage threshold."""
    if pct > 85:
        return Style.RED
    elif pct > 70:
        return Style.YELLOW
    return Style.GREEN


def service_color(status: str) -> str:
    """Return a systemd status string's ANSI color (active/inactive/other)."""
    if status == "active":
        return Style.GREEN
    elif status in ("inactive", "failed"):
        return Style.RED
    return Style.YELLOW


def retry_color(
    restart_age: timedelta, lookback_days: float, thresholds_days: tuple[float, float]
) -> str | None:
    """
    Return a recency-based color for a service retry, or None to hide it.

    Args:
        restart_age: Time elapsed since the service's last restart.
        lookback_days: How far back retries are reported at all; a restart
            at least this old returns None.
        thresholds_days: (red_cutoff, yellow_cutoff) in days from now - an
            age below red_cutoff is RED, below yellow_cutoff is YELLOW,
            and below lookback_days is GREY. Both must be <= lookback_days.

    Returns:
        An ANSI color constant, or None if restart_age falls outside the
        lookback window.
    """
    age_days = restart_age.total_seconds() / 86400
    if age_days >= lookback_days:
        return None

    red_cutoff, yellow_cutoff = thresholds_days
    if age_days < red_cutoff:
        return Style.RED
    if age_days < yellow_cutoff:
        return Style.YELLOW
    return Style.GREY


def signal_color(dbm: int) -> str:
    """Return a threshold-based ANSI color for a WiFi signal in dBm."""
    if dbm > -50:
        return Style.GREEN
    elif dbm > -65:
        return Style.YELLOW
    return Style.RED
