"""Dashboard sections for local hardware/system stats: uptime, CPU, memory,
temperatures, and fan. Reads sysfs/procfs only - no subprocess or network
calls (see sections_services.py / sections_network.py for those)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .style import Style, format_duration, section_header, temp_color, usage_color
from .sysfs import read_int, read_sysfs


def render_uptime() -> list[str]:
    """Return lines showing system uptime duration and boot time."""
    lines = section_header("System Uptime")

    uptime_raw = read_sysfs(Path("/proc/uptime"))
    if uptime_raw:
        try:
            uptime_secs = int(float(uptime_raw.split()[0]))
            duration = format_duration(uptime_secs)
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


def _read_thermal_zones() -> list[tuple[str, float | None]]:
    """
    Read every thermal zone under /sys/class/thermal.

    Returns:
        One (label, temp_c) tuple per thermal_zone* directory, sorted by
        zone path. temp_c is None where the zone's temp file was missing
        or unparseable.
    """
    zones: list[tuple[str, float | None]] = []
    for zone_path in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
        if not zone_path.is_file():
            continue

        label_path = zone_path.parent / "type"
        label = read_sysfs(label_path) or "zone"
        temp_raw = read_sysfs(zone_path)

        if temp_raw is not None and temp_raw.lstrip("-").isdigit():
            zones.append((label, int(temp_raw) / 1000))
        else:
            zones.append((label, None))

    return zones


def _fmt_temp(temp_c: float) -> str:
    """
    Format a Celsius temperature, dropping an insignificant trailing .0.

    Args:
        temp_c: Temperature in Celsius.

    Returns:
        temp_c to one decimal place, e.g. "34.1", but "34" (not "34.0")
        when the value is a whole number.
    """
    text = f"{temp_c:.1f}"
    return text[:-2] if text.endswith(".0") else text


def render_temperatures(simple: bool = False) -> list[str]:
    """
    Return lines showing thermal zone temperatures with color coding.

    Args:
        simple: If True, collapse all zones into a single averaged line
            (with min/max noted alongside) instead of listing every zone.

    Returns:
        Formatted lines for the Temperatures section.
    """
    lines = section_header("Temperatures")
    zones = _read_thermal_zones()

    if simple:
        temps = [temp_c for _, temp_c in zones if temp_c is not None]
        if temps:
            avg_c = sum(temps) / len(temps)
            min_c = min(temps)
            max_c = max(temps)
            lines.append(
                f"  Avg Temp:  "
                f"{temp_color(avg_c)}{_fmt_temp(avg_c)}°C{Style.RESET}"
                f"  ({temp_color(min_c)}{_fmt_temp(min_c)}{Style.RESET}"
                f"-{temp_color(max_c)}{_fmt_temp(max_c)}{Style.RESET}°C)"
            )
        else:
            lines.append("  Avg Temp:  N/A")
        return lines

    for label, temp_c in zones:
        if temp_c is not None:
            color = temp_color(temp_c)
            lines.append(f"  {label + ':':<22} {color}{_fmt_temp(temp_c):>5}°C{Style.RESET}")
        else:
            lines.append(f"  {label + ':':<22} {'N/A':>5}")

    return lines


def render_cpu_frequencies() -> list[str]:
    """Return lines showing per-core CPU frequencies with big.LITTLE labels."""
    lines = section_header("CPU Frequencies")

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
    lines = section_header("CPU Usage")

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


def render_memory() -> list[str]:
    """Return lines showing RAM and Swap usage with percentages."""
    lines = section_header("Memory")

    mem = _parse_meminfo()
    mem_total = mem.get("MemTotal", 0)
    mem_avail = mem.get("MemAvailable", 0)

    if mem_total > 0:
        mem_used = mem_total - mem_avail
        used_mb = mem_used // 1024
        total_mb = mem_total // 1024
        pct = 100 * mem_used // mem_total
        color = usage_color(pct)
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
        color = usage_color(pct)
        lines.append(f"  Swap: {used_mb:>5} / {total_mb:>5} MB  ({color}{pct}%{Style.RESET})")

    return lines


def render_fan_speed() -> list[str]:
    """Return lines showing fan PWM duty cycle and control mode."""
    lines = section_header("Fan")

    pwm = read_int(Path("/sys/class/hwmon/hwmon0/pwm1"))
    enable = read_int(Path("/sys/class/hwmon/hwmon0/pwm1_enable"))

    if pwm is not None:
        pct = int(pwm * 100 / 255)
        color = usage_color(pct)
        lines.append(f"  PWM:  {pwm:>3} / 255  ({color}{pct}%{Style.RESET})")

        if enable is not None:
            modes = {0: "off", 1: "manual", 2: "auto"}
            mode = modes.get(enable, f"mode {enable}")
            lines.append(f"  Mode: {mode}")
    else:
        lines.append("  (fan not detected)")

    return lines
