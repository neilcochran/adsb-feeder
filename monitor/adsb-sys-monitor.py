#!/usr/bin/env python3
"""
Odroid-XU4 / DietPi — ADS-B Feeder Dashboard

Real-time system monitoring for ADS-B feeder stations.
Displays uptime, temperatures, CPU usage, fan PWM, memory, feeder service
status, ADS-B aircraft/message stats, and WiFi connectivity with upload
throughput.

Configuration:
    Auto-creates ~/.config/adsb-monitor/config.json on first run.
    Customise which sections appear and in which column/order.
    Different config files can be used for different screen sizes/setups.

Usage:
    python3 adsb-sys-monitor.py                       # auto-detect config
    python3 adsb-sys-monitor.py --config mobile.json  # specific config
    python3 adsb-sys-monitor.py -i 1                  # 1s refresh override

Available sections:
    uptime, cpu_usage, memory, temperatures, cpu_freq,
    fan, services, adsb, network

Config search order:
    ~/.config/adsb-monitor/config.json
    ./config.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
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
        """Wrap text in an ANSI color sequence."""
        return f"{color}{text}{Style.RESET}"

def _visible_len(s: str) -> int:
    """Return the visible length of a string, excluding ANSI escape codes."""
    return len(_ANSI_RE.sub("", s))

def _pad_right(s: str, width: int) -> str:
    """Right-pad string with spaces to reach visible width, accounting for ANSI."""
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
        "interval": 2
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

SECTION_GAP = ""  # blank line inserted between sections

# ─── SysFS Helpers ────────────────────────────────────────────

def read_sysfs(path: Path) -> str | None:
    """Read a sysfs file and return stripped content, or None on failure."""
    try:
        return path.read_text().strip()
    except (OSError, FileNotFoundError, PermissionError):
        return None

def read_int(path: Path) -> int | None:
    """Read an integer from a sysfs file."""
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

        Returns (rate_string, total_string) where rate is in KB/s
        and total is cumulative MB transmitted.
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
    """Return a blank separator followed by a styled section header."""
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
        try:
            result = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True,
                text=True,
                timeout=5,
            )
            status = result.stdout.strip() or "unknown"
        except (subprocess.SubprocessError, FileNotFoundError):
            status = "unknown"

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
    """Return lines showing WiFi connection status, signal, and upload."""
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
    """Return lines showing ADS-B stats from dump1090-fa JSON files."""
    lines = _section_header("ADS-B Stats")

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

    messages = data.get("messages", 0)
    aircraft_list = data.get("aircraft", [])

    total_tracked = len(aircraft_list)
    with_position = sum(1 for a in aircraft_list if "lat" in a)

    plane_color = Style.GREEN if total_tracked > 0 else Style.YELLOW
    lines.append(f"  Aircraft tracked: {plane_color}{total_tracked}{Style.RESET}")
    lines.append(f"  With position:    {with_position}")
    lines.append(f"  Messages:        {messages:,}")

    return lines

# ─── Helpers (used by section renderers) ──────────────────────

def _format_duration(secs: int) -> str:
    """Format seconds into a human-readable duration string."""
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
    """Format time duration with smart granularity."""
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

def _temp_color(temp_c: float) -> str:
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
    if status == "active":
        return Style.GREEN
    elif status in ("inactive", "failed"):
        return Style.RED
    return Style.YELLOW

def _signal_color(dbm: int) -> str:
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
    """Retrieve WiFi SSID using iwgetid, falling back to iw dev."""
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
    """Read WiFi signal strength in dBm from /proc/net/wireless."""
    try:
        for line in Path("/proc/net/wireless").read_text().splitlines():
            if iface in line:
                parts = line.split()
                if len(parts) >= 4:
                    return int(parts[3].rstrip("."))
    except (OSError, ValueError, IndexError):
        pass
    return None

def _get_restart_count(svc: str) -> int | None:
    """Get the NRestarts count for a systemd service."""
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
    """Get the time elapsed since the service last restarted."""
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

# ─── Configuration Loading ────────────────────────────────────

def find_config(config_file: str | None = None) -> Path | None:
    """Find configuration file, checking provided path then defaults."""
    if config_file:
        path = Path(config_file)
        if path.exists():
            return path
        print(f"{Style.RED}Error: Config file '{config_file}' not found.{Style.RESET}")
        sys.exit(1)

    for path in CONFIG_PATHS:
        if path.exists():
            return path
    return None

def load_config(config_path: Path | None) -> dict[str, Any]:
    """Load configuration from file or return defaults."""
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

        # Merge with defaults so missing keys fall back
        merged = DEFAULT_CONFIG.copy()
        if isinstance(config, dict):
            if 'layout' in config:
                merged['layout'] = {**DEFAULT_CONFIG['layout'], **config['layout']}
            if 'options' in config:
                merged['options'] = {**DEFAULT_CONFIG['options'], **config['options']}
        return merged
    except json.JSONDecodeError as e:
        print(f"{Style.RED}Error: Invalid JSON in config '{config_path}': {e}{Style.RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"{Style.RED}Error loading config '{config_path}': {e}{Style.RESET}")
        sys.exit(1)

# ─── Layout Engine ───────────────────────────────────────────

# Map of section identifier → render function.
# Sections that need runtime state (wifi, upload, interval) are
# handled specially in render_sections().
SECTION_RENDERERS: dict[str, Any] = {
    "uptime": render_uptime,
    "cpu_usage": render_cpu_usage,
    "memory": render_memory,
    "temperatures": render_temperatures,
    "cpu_freq": render_cpu_frequencies,
    "services": render_services,
    "network": render_network,  # special-cased: needs wifi/upload/interval
    "fan": render_fan_speed,
    "adsb": render_adsb_stats,
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
) -> list[list[str]]:
    """Render a list of named sections into a list of line-lists."""
    rendered: list[list[str]] = []
    for name in section_names:
        renderer = SECTION_RENDERERS.get(name)
        if renderer is None:
            continue
        if name in ("network",):
            rendered.append(renderer(wifi, upload, interval))
        else:
            rendered.append(renderer())
    return rendered

def render_layout(
    config: dict[str, Any],
    wifi: WifiState,
    upload: UploadTracker,
    interval: int,
) -> str:
    """Render the full dashboard according to the configured layout."""
    layout = config.get("layout", DEFAULT_CONFIG["layout"])
    columns = layout.get("columns", 1)
    left_names = layout.get("left", [])
    right_names = layout.get("right", [])

    left = render_sections(left_names, wifi, upload, interval)
    right = render_sections(right_names, wifi, upload, interval)

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
            "                   cpu_freq, services, network, fan, adsb\n"
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
    """Clear the terminal."""
    os.system("clear")

def main() -> None:
    args = parse_args()

    # Load configuration
    config_path = find_config(args.config)
    config = load_config(config_path)

    # CLI interval overrides config
    if args.interval is not None:
        config.setdefault("options", {})["interval"] = args.interval

    wifi = WifiState()
    upload = UploadTracker()
    interval = config.get("options", {}).get("interval", 2)

    try:
        while True:
            clear_screen()
            print(render_layout(config, wifi, upload, interval))
            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n\n{Style.CYAN}Dashboard stopped.{Style.RESET}")
        sys.exit(0)

if __name__ == "__main__":
    main()
