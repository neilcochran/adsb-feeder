"""Dashboard section for feeder systemd service status."""

from __future__ import annotations

from .config import FEEDER_SERVICES
from .style import Style, format_age, retry_color, section_header, service_color
from .systemctl import get_restart_count, get_service_restart_age, systemctl_is_active


def render_feeder_services(
    retry_lookback_days: float, retry_thresholds_days: tuple[float, float]
) -> list[str]:
    """
    Return lines showing systemctl status and restart count for feeder services.

    Args:
        retry_lookback_days: A service's last restart is only reported if
            it happened within this many days; older ones are omitted.
        retry_thresholds_days: (red_cutoff, yellow_cutoff) in days,
            forwarded to retry_color to color a reported retry by how
            recently it happened.
    """
    lines = section_header("Feeder Services")

    for svc in FEEDER_SERVICES:
        status = systemctl_is_active(svc)
        color = service_color(status)
        lines.append(f"  {svc + ':':<19} {color}● {status}{Style.RESET}")

        restart_count = get_restart_count(svc)
        if restart_count is None or restart_count == 0:
            continue

        last_restart_age = get_service_restart_age(svc)
        if last_restart_age is None:
            lines.append(f"    {Style.YELLOW}Retries: {restart_count}{Style.RESET}")
            continue

        age_color = retry_color(last_restart_age, retry_lookback_days, retry_thresholds_days)
        if age_color is None:
            continue

        age_str = format_age(last_restart_age)
        lines.append(f"    {age_color}Retries: {restart_count} ({age_str}){Style.RESET}")

    return lines
