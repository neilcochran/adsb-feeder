"""systemctl-querying helpers, shared by the feeder-services and
adsb-stats-health section renderers."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone


def systemctl_is_active(svc: str) -> str:
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


def get_restart_count(svc: str) -> int | None:
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


def get_service_restart_age(svc: str) -> timedelta | None:
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
