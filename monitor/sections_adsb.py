"""Dashboard sections for dump1090-fa live stats and the adsb-stats
collector's persisted totals/health."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .config import ADSB_STATS_SERVICE
from .style import Style, age_since, format_age, section_header, service_color
from .systemctl import systemctl_is_active
from .trackers import MessageRateTracker


def render_adsb_live(msg_rate: MessageRateTracker, interval: int) -> list[str]:
    """
    Return lines showing a live snapshot of tracked aircraft from
    dump1090-fa's aircraft.json.

    Args:
        msg_rate: Tracks dump1090-fa's cumulative message counter across
            refresh cycles to compute a messages/sec rate.
        interval: Current refresh interval in seconds, used to compute the
            message rate.
    """
    lines = section_header("Live Aircraft")

    aircraft_path = Path("/run/dump1090-fa/aircraft.json")
    if not aircraft_path.exists():
        lines.append("  (dump1090-fa not running)")
        return lines

    try:
        with open(aircraft_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        lines.append("  (unable to read aircraft.json)")
        return lines

    aircraft_list = data.get("aircraft", [])

    total_tracked = len(aircraft_list)
    with_position = sum(1 for a in aircraft_list if "lat" in a)

    plane_color = Style.GREEN if total_tracked > 0 else Style.YELLOW
    lines.append(f"  {'Aircraft tracked:':<18}{plane_color}{total_tracked}{Style.RESET}")
    lines.append(f"  {'With position:':<18}{with_position}")

    # No coloring - a rate near 0 is normal when no aircraft are in range,
    # same reasoning as the network section's upload rate.
    msg_count = data.get("messages")
    rate_str = msg_rate.sample(msg_count, interval) if msg_count is not None else "N/A"
    lines.append(f"  {'Msg rate:':<18}{rate_str}")

    return lines


def _read_adsb_stats_row(db_path: Path) -> dict[str, Any] | None:
    """
    Read the single global_stats row from the adsb-stats database.

    Opens the database read-only so this process never creates, writes to,
    or locks it - adsb-stats owns all writes. A missing file, an
    unreadable file, and a not-yet-initialized database all collapse to
    the same None return (same handling as render_adsb_live's missing
    aircraft.json).

    Args:
        db_path: Filesystem path to adsb-stats' SQLite database.

    Returns:
        A dict of the columns this file displays, or None if the database
        couldn't be read.
    """
    columns = [
        "msg_total", "uaircraft_total", "uflights_total",
        "alt_max", "alt_max_icao", "alt_max_ts",
        "dist_max_nm", "dist_max_icao", "dist_max_ts",
        "last_msg_ts", "error_count", "last_error_ts", "last_error_msg",
    ]
    try:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            cursor = conn.execute(f"SELECT {', '.join(columns)} FROM global_stats WHERE id = 1")
            row = cursor.fetchone()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return None

    if row is None:
        return None
    return dict(zip(columns, row))


def render_adsb_global(db_path: Path) -> list[str]:
    """
    Return lines showing all-time adsb-stats collector totals.

    Reads adsb-stats' own SQLite database rather than dump1090-fa's
    aircraft.json - this is the collector's persisted, restart-tolerant
    totals, not a live snapshot (that's what the "adsb_live" section is for).

    Args:
        db_path: Filesystem path to adsb-stats' SQLite database.
    """
    lines = section_header("ADS-B Stats (All-Time)")

    stats = _read_adsb_stats_row(db_path)
    if stats is None:
        lines.append("  (adsb-stats db not found or unreadable)")
        return lines

    lines.append(f"  {'Messages:':<17}{stats['msg_total']:,}")
    lines.append(f"  {'Unique aircraft:':<17}{stats['uaircraft_total']:,}")
    lines.append(f"  {'Flights logged:':<17}{stats['uflights_total']:,}")

    if stats["alt_max"] is not None:
        age = age_since(stats["alt_max_ts"])
        detail = f" ({format_age(age)})" if age is not None else ""
        lines.append(f"  {'Max altitude:':<17}{stats['alt_max']:,.0f} ft{detail}")

    if stats["dist_max_nm"] is not None:
        age = age_since(stats["dist_max_ts"])
        detail = f" ({format_age(age)})" if age is not None else ""
        lines.append(f"  {'Max distance:':<17}{stats['dist_max_nm']:.1f} nm{detail}")

    return lines


def render_adsb_health(db_path: Path) -> list[str]:
    """
    Return lines showing adsb-stats service status, data freshness, and
    error counts.

    The service being "active" only means the process is running - it
    doesn't mean data is actually flowing. "Last data" and "Errors" catch
    the case where the service is up but the SBS connection has wedged or
    the ingest loop is hitting a real bug (see adsb_stats/README.md's
    Error Tracking section).

    Args:
        db_path: Filesystem path to adsb-stats' SQLite database.
    """
    lines = section_header("ADS-B Collector Health")

    status = systemctl_is_active(ADSB_STATS_SERVICE)
    color = service_color(status)
    lines.append(f"  {'Service:':<17}{color}● {status}{Style.RESET}")

    stats = _read_adsb_stats_row(db_path)
    if stats is None:
        lines.append("  (adsb-stats db not found or unreadable)")
        return lines

    age = age_since(stats["last_msg_ts"])
    if age is None:
        lines.append(f"  {'Last data:':<17}never")
    else:
        secs = age.total_seconds()
        # Capped at yellow, never red - a data gap alone isn't an error
        # (e.g. no aircraft in range overnight), just worth a light flag.
        age_color = Style.GREEN if secs < 600 else Style.YELLOW
        lines.append(f"  {'Last data:':<17}{age_color}{format_age(age)}{Style.RESET}")

    error_count = stats["error_count"] or 0
    if error_count == 0:
        lines.append(f"  {'Errors:':<17}{Style.GREEN}0{Style.RESET}")
    else:
        err_color = Style.RED if error_count >= 5 else Style.YELLOW
        lines.append(f"  {'Errors:':<17}{err_color}{error_count:,}{Style.RESET}")

        last_age = age_since(stats["last_error_ts"])
        age_str = f" ({format_age(last_age)})" if last_age is not None else ""
        msg = stats["last_error_msg"] or ""
        # "    Last (999d ago): " runs ~21 chars - budget the message to fit
        # an 80-column terminal's ~38-char 2-column width alongside it.
        if len(msg) > 14:
            msg = msg[:14] + "..."
        lines.append(f"    {err_color}Last{age_str}: {msg}{Style.RESET}")

    return lines
