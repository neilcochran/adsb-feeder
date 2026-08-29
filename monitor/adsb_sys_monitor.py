#!/usr/bin/env python3
"""
Odroid-XU4 / DietPi — ADS-B Feeder Dashboard

Real-time system monitoring for ADS-B feeder stations.
Displays uptime, temperatures, CPU usage, fan PWM, memory, feeder service
status, ADS-B aircraft/message stats, adsb-stats collector totals and
health, and WiFi connectivity with upload throughput.

Configuration:
    Auto-creates ~/.config/adsb-monitor/config.json on first run.
    Customise which sections appear and in which column/order.
    Different config files can be used for different screen sizes/setups.

Usage:
    python3 adsb_sys_monitor.py                       # auto-detect config
    python3 adsb_sys_monitor.py --config mobile.json  # specific config
    python3 adsb_sys_monitor.py -i 1                  # 1s refresh override

Available sections:
    uptime, cpu_usage, memory, temperatures, cpu_freq, fan, services,
    adsb, adsb_global, adsb_health, network

Config search order:
    ~/.config/adsb-monitor/config.json
    ./config.json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ─── ANSI Styling ─────────────────────────────────────────────

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

class Style:
    """ANSI escape codes for terminal styling."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"
    RED = "\033[31m"
    YELLOW = "\033[33m"

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

def _pad_right(s: str, width: int) -> str:
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

# ─── Configuration ────────────────────────────────────────────

DEFAULT_CONFIG = {
    "version": "1.0",
    "layout": {
        "columns": 2,
        "left": ["uptime", "cpu_usage", "memory", "temperatures"],
        "right": ["services", "network"]
    },
    "options": {
        "interval": 2,
        "adsb_stats_db_path": "/var/lib/adsb-stats/stats.db"
    }
}

CONFIG_PATHS = [
    Path.home() / ".config" / "adsb-monitor" / "config.json",
    Path.cwd() / "config.json",
]

WIFI_IFACE = "wlan0"
TX_FILE = Path(f"/sys/class/net/{WIFI_IFACE}/statistics/tx_bytes")
FEEDER_SERVICES = [
    "dump1090-fa",
    "piaware",
    "fr24feed",
    "adsbexchange-feed",
    "adsbexchange-mlat",
]
ADSB_STATS_SERVICE = "adsb-stats"

SECTION_GAP = ""  # blank line inserted between sections

# ─── SysFS Helpers ────────────────────────────────────────────

def read_sysfs(path: Path) -> str | None:
    """
    Read a sysfs file and return stripped content, or None on failure.

    Args:
        path: Filesystem path to read.

    Returns:
        The file's stripped text content, or None if it couldn't be read.
    """
    try:
        return path.read_text().strip()
    except (OSError, FileNotFoundError, PermissionError):
        return None

def read_int(path: Path) -> int | None:
    """
    Read an integer from a sysfs file.

    Args:
        path: Filesystem path to read.

    Returns:
        The parsed integer, or None if the file couldn't be read or its
        content wasn't a valid integer.
    """
    raw = read_sysfs(path)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None

# ─── Data Structures ──────────────────────────────────────────

@dataclass
class WifiState:
    """Tracks WiFi connection state across loop iterations."""

    iface: str = WIFI_IFACE
    last_state: str = ""
    down_since: str = ""
    up_since: str = ""

@dataclass
class UploadTracker:
    """Tracks cumulative TX bytes to compute upload rate between samples."""

    tx_file: Path = TX_FILE
    prev_tx: int | None = None

    def sample(self, interval: int) -> tuple[str, str]:
        """
        Sample TX byte counter and compute upload rate.

        Args:
            interval: Seconds elapsed since the previous sample, used to
                convert the byte delta into a rate.

        Returns:
            (rate_string, total_string) where rate is in KB/s and total is
            cumulative MB transmitted.
        """
        curr_tx = read_int(self.tx_file)
        if curr_tx is None:
            return "(tx stats unavailable)", ""

        total_mb = curr_tx / 1_048_576

        if self.prev_tx is not None and curr_tx >= self.prev_tx:
            delta = curr_tx - self.prev_tx
            kb_per_sec = delta / 1024 / interval
            rate_str = f"{kb_per_sec:.1f} KB/s"
        elif self.prev_tx is None:
            rate_str = "collecting..."
        else:
            rate_str = "0.0 KB/s"

        self.prev_tx = curr_tx
        return rate_str, f"{total_mb:.1f} MB"

# ─── Section Renderers ───────────────────────────────────────
# Each returns a list[str] of formatted lines (no embedded \n).
# A blank line ("") is used as a section separator instead of "\n" prefix.

def _section_header(title: str) -> list[str]:
    """
    Return a blank separator followed by a styled section header.

    Args:
        title: Section title to display.

    Returns:
        The two lines to prepend to a section's body.
    """
    return [SECTION_GAP, f"{Style.CYAN}{Style.BOLD}[{title}]{Style.RESET}"]

def render_uptime() -> list[str]:
    """Return lines showing system uptime duration and boot time."""
    lines = _section_header("System Uptime")

    uptime_raw = read_sysfs(Path("/proc/uptime"))
    if uptime_raw:
        try:
            uptime_secs = int(float(uptime_raw.split()[0]))
            duration = _format_duration(uptime_secs)
            now = datetime.now()
            boot = now - timedelta(seconds=uptime_secs)
            lines.append(f"  Duration:  {duration}")
            lines.append(f"  Boot time: {boot.strftime('%Y-%m-%d %H:%M:%S')}")
        except (ValueError, IndexError):
            lines.append("  Duration:  N/A")
            lines.append("  Boot time: N/A")
    else:
        lines.append("  Duration:  N/A")
        lines.append("  Boot time: N/A")

    return lines

def render_temperatures() -> list[str]:
    """Return lines showing all thermal zone temperatures with color coding."""
    lines = _section_header("Temperatures")

    for zone_path in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
        if not zone_path.is_file():
            continue

        label_path = zone_path.parent / "type"
        label = read_sysfs(label_path) or "zone"
        temp_raw = read_sysfs(zone_path)

        if temp_raw is not None and temp_raw.lstrip("-").isdigit():
            temp_c = int(temp_raw) / 1000
            color = _temp_color(temp_c)
            lines.append(f"  {label + ':':<22} {color}{temp_c:>5.1f}°C{Style.RESET}")
        else:
            lines.append(f"  {label + ':':<22} {'N/A':>5}")

    return lines

def render_cpu_frequencies() -> list[str]:
    """Return lines showing per-core CPU frequencies with big.LITTLE labels."""
    lines = _section_header("CPU Frequencies")

    for i in range(8):
        freq_path = Path(f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq")
        if not freq_path.exists():
            continue

        freq_raw = read_sysfs(freq_path)
        if freq_raw is None:
            lines.append(f"  CPU{i:<2d}  offline")
            continue

        if freq_raw.isdigit():
            freq_mhz = int(freq_raw) // 1000
            cluster = "A7 (LITTLE)" if i < 4 else "A15 (big)"
            lines.append(f"  CPU{i:<2d} [{cluster:<11}]  {freq_mhz:>5} MHz")
        else:
            lines.append(f"  CPU{i:<2d}  offline")

    return lines

def render_cpu_usage() -> list[str]:
    """Return lines showing overall CPU utilization and load average."""
    lines = _section_header("CPU Usage")

    cpu_line = read_sysfs(Path("/proc/stat"))
    if cpu_line:
        fields = cpu_line.split()
        if len(fields) >= 8 and fields[0] == "cpu":
            values = [int(v) for v in fields[1:8]]
            active = sum(values[:3])
            total = sum(values)
            if total > 0:
                pct = int(100 * active / total)
                lines.append(f"  Overall:  {Style.GREEN}{pct:>5}%{Style.RESET}")

    loadavg = read_sysfs(Path("/proc/loadavg"))
    if loadavg:
        parts = loadavg.split()
        if len(parts) >= 3:
            lines.append(f"  Load avg: {' '.join(parts[:3])}")

    return lines

def render_memory() -> list[str]:
    """Return lines showing RAM and Swap usage with percentages."""
    lines = _section_header("Memory")

    mem = _parse_meminfo()
    mem_total = mem.get("MemTotal", 0)
    mem_avail = mem.get("MemAvailable", 0)

    if mem_total > 0:
        mem_used = mem_total - mem_avail
        used_mb = mem_used // 1024
        total_mb = mem_total // 1024
        pct = 100 * mem_used // mem_total
        color = _usage_color(pct)
        lines.append(f"  RAM:  {used_mb:>5} / {total_mb:>5} MB  ({color}{pct}%{Style.RESET})")
    else:
        lines.append("  RAM:  N/A")

    swap_total = mem.get("SwapTotal", 0)
    swap_free = mem.get("SwapFree", 0)

    if swap_total > 0:
        swap_used = swap_total - swap_free
        used_mb = swap_used // 1024
        total_mb = swap_total // 1024
        pct = 100 * swap_used // swap_total
        color = _usage_color(pct)
        lines.append(f"  Swap: {used_mb:>5} / {total_mb:>5} MB  ({color}{pct}%{Style.RESET})")

    return lines

def render_services() -> list[str]:
    """Return lines showing systemctl status and restart count for feeder services."""
    lines = _section_header("Feeder Services")

    for svc in FEEDER_SERVICES:
        status = _systemctl_is_active(svc)
        color = _service_color(status)
        lines.append(f"  {svc + ':':<19} {color}● {status}{Style.RESET}")

        # Check NRestarts counter from systemd
        restart_count = _get_restart_count(svc)
        if restart_count is not None and restart_count > 0:
            last_restart_age = _get_service_restart_age(svc)
            warn_color = Style.RED if restart_count >= 5 else Style.YELLOW

            if last_restart_age:
                age_str = _format_age(last_restart_age)
                if last_restart_age.total_seconds() > 86400:
                    # Dim old failures (>24hrs)
                    warn_color = Style.CYAN
                lines.append(
                    f"    {warn_color}Retries: {restart_count} ({age_str}){Style.RESET}"
                )
            else:
                lines.append(
                    f"    {warn_color}Retries: {restart_count}{Style.RESET}"
                )

    return lines

def render_network(wifi: WifiState, upload: UploadTracker, interval: int) -> list[str]:
    """
    Return lines showing WiFi connection status, signal, and upload.

    Args:
        wifi: Connection state tracked across loop iterations.
        upload: TX byte counter tracked across loop iterations.
        interval: Current refresh interval in seconds, used to compute the
            upload rate.
    """
    lines = _section_header("Network")

    iface_path = Path(f"/sys/class/net/{wifi.iface}")

    if not iface_path.exists():
        lines.append(f"  (interface not found)")
        wifi.last_state = ""
        return lines

    operstate = read_sysfs(iface_path / "operstate") or "unknown"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if operstate == "up":
        ssid = _get_ssid(wifi.iface)
        sig_dbm = _get_signal(wifi.iface)

        lines.append(f"  SSID: {ssid}")

        if sig_dbm is not None:
            color = _signal_color(sig_dbm)
            lines.append(f"  Signal: {color}{sig_dbm} dBm{Style.RESET}")
        else:
            lines.append(f"  Signal: N/A")

        if wifi.last_state != "up":
            wifi.up_since = now
        lines.append(f"  Connected: {Style.GREEN}{wifi.up_since}{Style.RESET}")
        wifi.down_since = ""

    elif operstate == "down":
        lines.append(f"  DOWN")
        if wifi.last_state == "up":
            wifi.down_since = now
        lines.append(f"  Disconnected: {Style.RED}{wifi.down_since}{Style.RESET}")
    else:
        lines.append(f"  (state: {operstate})")

    wifi.last_state = operstate

    # Upload rate — no coloring (low upload is normal during low traffic)
    rate_str, total_str = upload.sample(interval)
    if rate_str not in ("collecting...", "(tx stats unavailable)"):
        lines.append(f"  Upload: {rate_str} (TX: {total_str})")
    else:
        lines.append(f"  Upload: {rate_str}")

    return lines

def render_fan_speed() -> list[str]:
    """Return lines showing fan PWM duty cycle and control mode."""
    lines = _section_header("Fan")

    pwm = read_int(Path("/sys/class/hwmon/hwmon0/pwm1"))
    enable = read_int(Path("/sys/class/hwmon/hwmon0/pwm1_enable"))

    if pwm is not None:
        pct = int(pwm * 100 / 255)
        color = _usage_color(pct)
        lines.append(f"  PWM:  {pwm:>3} / 255  ({color}{pct}%{Style.RESET})")

        if enable is not None:
            modes = {0: "off", 1: "manual", 2: "auto"}
            mode = modes.get(enable, f"mode {enable}")
            lines.append(f"  Mode: {mode}")
    else:
        lines.append(f"  (fan not detected)")

    return lines

def render_adsb_stats() -> list[str]:
    """Return lines showing a live snapshot of tracked aircraft from dump1090-fa's aircraft.json."""
    lines = _section_header("Live Aircraft")

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
    with_callsign = sum(1 for a in aircraft_list if a.get("flight", "").strip())

    plane_color = Style.GREEN if total_tracked > 0 else Style.YELLOW
    lines.append(f"  {'Aircraft tracked:':<18}{plane_color}{total_tracked}{Style.RESET}")
    lines.append(f"  {'With position:':<18}{with_position}")
    lines.append(f"  {'With callsign:':<18}{with_callsign}")

    return lines

def render_adsb_global(db_path: Path) -> list[str]:
    """
    Return lines showing all-time adsb-stats collector totals.

    Reads adsb-stats' own SQLite database rather than dump1090-fa's
    aircraft.json - this is the collector's persisted, restart-tolerant
    totals, not a live snapshot (that's what the "adsb" section is for).

    Args:
        db_path: Filesystem path to adsb-stats' SQLite database.
    """
    lines = _section_header("ADS-B Stats (All-Time)")

    stats = _read_adsb_stats_row(db_path)
    if stats is None:
        lines.append("  (adsb-stats db not found or unreadable)")
        return lines

    lines.append(f"  {'Messages:':<17}{stats['msg_total']:,}")
    lines.append(f"  {'Unique aircraft:':<17}{stats['uaircraft_total']:,}")
    lines.append(f"  {'Flights logged:':<17}{stats['uflights_total']:,}")

    if stats["alt_max"] is not None:
        age = _age_since(stats["alt_max_ts"])
        detail = f" ({_format_age(age)})" if age is not None else ""
        lines.append(f"  {'Max altitude:':<17}{stats['alt_max']:,.0f} ft{detail}")

    if stats["dist_max_km"] is not None:
        age = _age_since(stats["dist_max_ts"])
        detail = f" ({_format_age(age)})" if age is not None else ""
        lines.append(f"  {'Max distance:':<17}{stats['dist_max_km']:.1f} km{detail}")

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
    lines = _section_header("ADS-B Collector Health")

    status = _systemctl_is_active(ADSB_STATS_SERVICE)
    color = _service_color(status)
    lines.append(f"  {'Service:':<17}{color}● {status}{Style.RESET}")

    stats = _read_adsb_stats_row(db_path)
    if stats is None:
        lines.append("  (adsb-stats db not found or unreadable)")
        return lines

    age = _age_since(stats["last_msg_ts"])
    if age is None:
        lines.append(f"  {'Last data:':<17}never")
    else:
        secs = age.total_seconds()
        if secs < 600:
            age_color = Style.GREEN
        elif secs < 1800:
            age_color = Style.YELLOW
        else:
            age_color = Style.RED
        lines.append(f"  {'Last data:':<17}{age_color}{_format_age(age)}{Style.RESET}")

    error_count = stats["error_count"] or 0
    if error_count == 0:
        lines.append(f"  {'Errors:':<17}{Style.GREEN}0{Style.RESET}")
    else:
        err_color = Style.RED if error_count >= 5 else Style.YELLOW
        lines.append(f"  {'Errors:':<17}{err_color}{error_count:,}{Style.RESET}")

        last_age = _age_since(stats["last_error_ts"])
        age_str = f" ({_format_age(last_age)})" if last_age is not None else ""
        msg = stats["last_error_msg"] or ""
        # "    Last (999d ago): " runs ~21 chars - budget the message to fit
        # an 80-column terminal's ~38-char 2-column width alongside it.
        if len(msg) > 14:
            msg = msg[:14] + "..."
        lines.append(f"    {err_color}Last{age_str}: {msg}{Style.RESET}")

    return lines

# ─── Helpers (used by section renderers) ──────────────────────

def _format_duration(secs: int) -> str:
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

def _format_age(td: timedelta) -> str:
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

def _age_since(ts_str: str | None) -> timedelta | None:
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

def _temp_color(temp_c: float) -> str:
    """Return a threshold-based ANSI color for a Celsius temperature."""
    if temp_c > 80:
        return Style.RED
    elif temp_c > 65:
        return Style.YELLOW
    return Style.GREEN

def _usage_color(pct: int) -> str:
    """Return ANSI color based on usage percentage threshold."""
    if pct > 85:
        return Style.RED
    elif pct > 70:
        return Style.YELLOW
    return Style.GREEN

def _service_color(status: str) -> str:
    """Return a systemd status string's ANSI color (active/inactive/other)."""
    if status == "active":
        return Style.GREEN
    elif status in ("inactive", "failed"):
        return Style.RED
    return Style.YELLOW

def _signal_color(dbm: int) -> str:
    """Return a threshold-based ANSI color for a WiFi signal in dBm."""
    if dbm > -50:
        return Style.GREEN
    elif dbm > -65:
        return Style.YELLOW
    return Style.RED

def _parse_meminfo() -> dict[str, int]:
    """Parse /proc/meminfo into a dictionary of key -> kilobytes."""
    info: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                key = parts[0].rstrip(":")
                try:
                    info[key] = int(parts[1])
                except ValueError:
                    pass
    except OSError:
        pass
    return info

def _get_ssid(iface: str) -> str:
    """
    Retrieve WiFi SSID using iwgetid, falling back to iw dev.

    Args:
        iface: Interface name to query (used only for the iw fallback -
            iwgetid always queries the system's current WiFi association).

    Returns:
        The associated SSID, or "N/A" if it couldn't be determined.
    """
    for cmd in (["iwgetid", "-r"], ["/usr/sbin/iwgetid", "-r"]):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            continue

    try:
        result = subprocess.run(
            ["iw", "dev", iface, "link"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if "SSID" in line.upper():
                return line.split("SSID:")[-1].strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    return "N/A"

def _get_signal(iface: str) -> int | None:
    """
    Read WiFi signal strength in dBm from /proc/net/wireless.

    Args:
        iface: Interface name to match exactly against each line's first
            (colon-suffixed) token.

    Returns:
        Signal strength in dBm, or None if iface's line wasn't found or
        couldn't be parsed.
    """
    try:
        for line in Path("/proc/net/wireless").read_text().splitlines():
            parts = line.split()
            if parts and parts[0].rstrip(":") == iface:
                if len(parts) >= 4:
                    return int(parts[3].rstrip("."))
    except (OSError, ValueError, IndexError):
        pass
    return None

def _systemctl_is_active(svc: str) -> str:
    """
    Query systemctl for a unit's active-state.

    Args:
        svc: systemd unit name (without the .service suffix).

    Returns:
        The status string systemctl reports ("active", "inactive",
        "failed", ...), or "unknown" if the query itself couldn't be run.
    """
    try:
        result = subprocess.run(
            ["systemctl", "is-active", svc],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except (subprocess.SubprocessError, FileNotFoundError):
        return "unknown"

def _get_restart_count(svc: str) -> int | None:
    """
    Get the NRestarts count for a systemd service.

    Args:
        svc: systemd unit name (without the .service suffix).

    Returns:
        The unit's NRestarts value, or None if it couldn't be read.
    """
    try:
        result = subprocess.run(
            ["systemctl", "show", svc, "-p", "NRestarts", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip().isdigit():
            return int(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError, FileNotFoundError):
        pass
    return None

def _get_service_restart_age(svc: str) -> timedelta | None:
    """
    Get the time elapsed since the service last restarted.

    Args:
        svc: systemd unit name (without the .service suffix).

    Returns:
        Elapsed time since ExecMainStartTimestamp, or None if it couldn't
        be read or parsed.
    """
    try:
        result = subprocess.run(
            ["systemctl", "show", svc, "-p", "ExecMainStartTimestamp", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            timestamp_str = result.stdout.strip()
            # Parse format like "Thu 2026-08-23 02:49:39 UTC"
            dt = datetime.strptime(timestamp_str, "%a %Y-%m-%d %H:%M:%S %Z")
            return datetime.now(timezone.utc).replace(tzinfo=None) - dt
    except (subprocess.SubprocessError, ValueError, FileNotFoundError):
        pass
    return None

def _read_adsb_stats_row(db_path: Path) -> dict[str, Any] | None:
    """
    Read the single global_stats row from the adsb-stats database.

    Opens the database read-only so this process never creates, writes to,
    or locks it - adsb-stats owns all writes. A missing file, an
    unreadable file, and a not-yet-initialized database all collapse to
    the same None return (same handling as render_adsb_stats' missing
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
        "dist_max_km", "dist_max_icao", "dist_max_ts",
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

# ─── Configuration Loading ────────────────────────────────────

class ConfigError(Exception):
    """Raised for a config-loading failure that main() should report and exit on."""

def find_config(config_file: str | None = None) -> Path | None:
    """
    Find configuration file, checking provided path then defaults.

    Args:
        config_file: Explicit path to check, or None to search
            CONFIG_PATHS in order.

    Returns:
        The resolved config file path, or None if none was found (only
        possible when config_file wasn't given explicitly).

    Raises:
        ConfigError: config_file was given explicitly but doesn't exist.
    """
    if config_file:
        path = Path(config_file)
        if path.exists():
            return path
        raise ConfigError(f"Config file '{config_file}' not found.")

    for path in CONFIG_PATHS:
        if path.exists():
            return path
    return None

def load_config(config_path: Path | None) -> dict[str, Any]:
    """
    Load configuration from file or return defaults.

    Args:
        config_path: Path to load, or None to create/return the default
            config.

    Returns:
        The loaded config, merged over DEFAULT_CONFIG so missing keys fall
        back to their default.

    Raises:
        ConfigError: config_path is set but contains invalid JSON, has the
            wrong shape, or couldn't be read.
    """
    if config_path is None:
        # Create default config directory and file if needed
        config_dir = Path.home() / ".config" / "adsb-monitor"
        config_dir.mkdir(parents=True, exist_ok=True)

        default_file = config_dir / "config.json"
        if not default_file.exists():
            with open(default_file, 'w') as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
            print(f"{Style.CYAN}Created default config at {default_file}{Style.RESET}")
        return DEFAULT_CONFIG.copy()

    try:
        with open(config_path) as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in config '{config_path}': {e}") from e
    except OSError as e:
        raise ConfigError(f"Error loading config '{config_path}': {e}") from e

    if not isinstance(config, dict):
        raise ConfigError(f"Config '{config_path}' must be a JSON object.")

    # Merge with defaults so missing keys fall back
    merged = DEFAULT_CONFIG.copy()
    if 'layout' in config:
        if not isinstance(config['layout'], dict):
            raise ConfigError(f"Config '{config_path}': 'layout' must be an object.")
        merged['layout'] = {**DEFAULT_CONFIG['layout'], **config['layout']}
    if 'options' in config:
        if not isinstance(config['options'], dict):
            raise ConfigError(f"Config '{config_path}': 'options' must be an object.")
        merged['options'] = {**DEFAULT_CONFIG['options'], **config['options']}
    return merged

# ─── Layout Engine ───────────────────────────────────────────

# Map of section identifier → render function. Most take no arguments;
# "network" and the two adsb_stats sections need runtime state instead
# (wifi/upload/interval, db_path respectively), so they're special-cased
# in render_sections() rather than changing every entry's signature.
SECTION_RENDERERS: dict[str, Callable[..., list[str]]] = {
    "uptime": render_uptime,
    "cpu_usage": render_cpu_usage,
    "memory": render_memory,
    "temperatures": render_temperatures,
    "cpu_freq": render_cpu_frequencies,
    "services": render_services,
    "network": render_network,  # special-cased: needs wifi/upload/interval
    "fan": render_fan_speed,
    "adsb": render_adsb_stats,
    "adsb_global": render_adsb_global,  # special-cased: needs db_path
    "adsb_health": render_adsb_health,  # special-cased: needs db_path
}

def _flatten_sections(sections: list[list[str]]) -> list[str]:
    """Flatten a list of section line-lists into one list, with blank separators between sections."""
    lines: list[str] = []
    for i, section in enumerate(sections):
        if i > 0:
            lines.append(SECTION_GAP)
        lines.extend(section)
    return lines

def render_single_column(sections: list[list[str]]) -> str:
    """Render all sections stacked vertically in a single column."""
    return "\n".join(_flatten_sections(sections))

def render_two_column(left: list[list[str]], right: list[list[str]]) -> str:
    """
    Render sections in two side-by-side columns.

    Each column's sections are flattened independently, the shorter
    column is padded with blank lines, then rows are zipped together
    with a fixed-width gap between columns.

    Args:
        left: Left column's rendered sections.
        right: Right column's rendered sections.
    """
    term_width = shutil.get_terminal_size((80, 24)).columns
    gap = 3
    col_width = (term_width - gap) // 2

    left_lines = _flatten_sections(left)
    right_lines = _flatten_sections(right)

    # Pad shorter side with blank lines
    max_lines = max(len(left_lines), len(right_lines))
    left_lines += [""] * (max_lines - len(left_lines))
    right_lines += [""] * (max_lines - len(right_lines))

    # Combine with padding
    output_lines: list[str] = []
    for l_line, r_line in zip(left_lines, right_lines):
        l_padded = _pad_right(l_line, col_width)
        output_lines.append(f"{l_padded}{' ' * gap}{r_line}")

    return "\n".join(output_lines)

def render_sections(
    section_names: list[str],
    wifi: WifiState,
    upload: UploadTracker,
    interval: int,
    db_path: Path,
) -> list[list[str]]:
    """
    Render a list of named sections into a list of line-lists.

    Args:
        section_names: Section ids to render, in order. Unknown ids are
            silently skipped.
        wifi: Passed through to the "network" section.
        upload: Passed through to the "network" section.
        interval: Passed through to the "network" section.
        db_path: Passed through to the "adsb_global"/"adsb_health" sections.
    """
    rendered: list[list[str]] = []
    for name in section_names:
        renderer = SECTION_RENDERERS.get(name)
        if renderer is None:
            continue
        if name == "network":
            rendered.append(renderer(wifi, upload, interval))
        elif name in ("adsb_global", "adsb_health"):
            rendered.append(renderer(db_path))
        else:
            rendered.append(renderer())
    return rendered

def render_layout(
    config: dict[str, Any],
    wifi: WifiState,
    upload: UploadTracker,
    interval: int,
) -> str:
    """
    Render the full dashboard according to the configured layout.

    Args:
        config: Loaded config (see DEFAULT_CONFIG for shape).
        wifi: Connection state tracked across loop iterations.
        upload: TX byte counter tracked across loop iterations.
        interval: Current refresh interval in seconds.
    """
    layout = config.get("layout", DEFAULT_CONFIG["layout"])
    columns = layout.get("columns", 1)
    left_names = layout.get("left", [])
    right_names = layout.get("right", [])
    db_path = Path(config.get("options", {}).get(
        "adsb_stats_db_path", DEFAULT_CONFIG["options"]["adsb_stats_db_path"]
    ))

    left = render_sections(left_names, wifi, upload, interval, db_path)
    right = render_sections(right_names, wifi, upload, interval, db_path)

    if columns == 1:
        return render_single_column(left + right)
    return render_two_column(left, right)

# ─── CLI ─────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Odroid-XU4 / DietPi — ADS-B Feeder Dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s                          Auto-detect config, 2s interval\n"
            "  %(prog)s --config mobile.json     Use specific config file\n"
            "  %(prog)s -i 1                     1-second refresh override\n"
            "\n"
            "Config files are searched in this order:\n"
            "  ~/.config/adsb-monitor/config.json\n"
            "  ./config.json\n"
            "\n"
            "Available sections: uptime, cpu_usage, memory, temperatures,\n"
            "                   cpu_freq, services, network, fan, adsb,\n"
            "                   adsb_global, adsb_health\n"
        ),
    )
    parser.add_argument(
        "-i", "--interval",
        type=int,
        default=None,
        metavar="SECS",
        help="override refresh interval from config (default: 2)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        metavar="FILE",
        help="path to configuration file (default: auto-detect)",
    )
    return parser.parse_args()

# ─── Main ────────────────────────────────────────────────────

def clear_screen() -> None:
    """Clear the terminal using ANSI escape codes."""
    print("\033[2J\033[H", end="")

def main() -> None:
    args = parse_args()

    try:
        config_path = find_config(args.config)
        config = load_config(config_path)

        # CLI interval overrides config
        if args.interval is not None:
            config.setdefault("options", {})["interval"] = args.interval

        interval = config.get("options", {}).get(
            "interval", DEFAULT_CONFIG["options"]["interval"]
        )
        if not isinstance(interval, int) or interval <= 0:
            raise ConfigError(
                f"options.interval must be a positive integer, got {interval!r}."
            )
    except ConfigError as e:
        print(f"{Style.RED}Error: {e}{Style.RESET}", file=sys.stderr)
        sys.exit(1)

    wifi = WifiState()
    upload = UploadTracker()

    try:
        while True:
            clear_screen()
            try:
                print(render_layout(config, wifi, upload, interval))
            except Exception as e:
                # One bad section (existing or future) shouldn't take down
                # the whole dashboard - report and retry next tick.
                print(f"{Style.RED}Render error: {e}{Style.RESET}", file=sys.stderr)
            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n\n{Style.CYAN}Dashboard stopped.{Style.RESET}")
        sys.exit(0)

if __name__ == "__main__":
    main()
