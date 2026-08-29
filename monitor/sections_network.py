"""Dashboard section for WiFi connectivity and upload throughput."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from .style import Style, section_header, signal_color
from .sysfs import read_sysfs
from .trackers import UploadTracker, WifiState


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


def render_network(wifi: WifiState, upload: UploadTracker, interval: int) -> list[str]:
    """
    Return lines showing WiFi connection status, signal, and upload.

    Args:
        wifi: Connection state tracked across loop iterations.
        upload: TX byte counter tracked across loop iterations.
        interval: Current refresh interval in seconds, used to compute the
            upload rate.
    """
    lines = section_header("Network")

    iface_path = Path(f"/sys/class/net/{wifi.iface}")

    if not iface_path.exists():
        lines.append("  (interface not found)")
        wifi.last_state = ""
        return lines

    operstate = read_sysfs(iface_path / "operstate") or "unknown"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if operstate == "up":
        ssid = _get_ssid(wifi.iface)
        sig_dbm = _get_signal(wifi.iface)

        lines.append(f"  SSID: {ssid}")

        if sig_dbm is not None:
            color = signal_color(sig_dbm)
            lines.append(f"  Signal: {color}{sig_dbm} dBm{Style.RESET}")
        else:
            lines.append("  Signal: N/A")

        if wifi.last_state != "up":
            wifi.up_since = now
        lines.append(f"  Connected: {Style.GREEN}{wifi.up_since}{Style.RESET}")
        wifi.down_since = ""

    elif operstate == "down":
        lines.append("  DOWN")
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
