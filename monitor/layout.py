"""Section registry and layout engine: which sections render where, and
how their output is arranged into one or two terminal columns."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG
from .sections_adsb import render_adsb_global, render_adsb_health, render_adsb_live
from .sections_network import render_network
from .sections_services import render_feeder_services
from .sections_system import (
    render_cpu_frequencies,
    render_cpu_usage,
    render_fan_speed,
    render_memory,
    render_temperatures,
    render_uptime,
)
from .style import SECTION_GAP, pad_right
from .trackers import MessageRateTracker, UploadTracker, WifiState

# Map of section identifier → render function. Most take no arguments;
# "network", "adsb_live", "feeder_services", the two adsb_stats sections,
# and "temperatures" need runtime state instead (wifi/upload/interval,
# msg_rate/interval, retry_lookback_days/retry_thresholds_days, db_path,
# temp_simple respectively), so they're special-cased in render_sections()
# rather than changing every entry's signature.
SECTION_RENDERERS: dict[str, Callable[..., list[str]]] = {
    "uptime": render_uptime,
    "cpu_usage": render_cpu_usage,
    "memory": render_memory,
    "temperatures": render_temperatures,
    "cpu_freq": render_cpu_frequencies,
    "feeder_services": render_feeder_services,
    "network": render_network,  # special-cased: needs wifi/upload/interval
    "fan": render_fan_speed,
    "adsb_live": render_adsb_live,
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
        l_padded = pad_right(l_line, col_width)
        output_lines.append(f"{l_padded}{' ' * gap}{r_line}")

    return "\n".join(output_lines)


def render_sections(
    section_names: list[str],
    wifi: WifiState,
    upload: UploadTracker,
    msg_rate: MessageRateTracker,
    interval: int,
    db_path: Path,
    temp_simple: bool,
    retry_lookback_days: float,
    retry_thresholds_days: tuple[float, float],
) -> list[list[str]]:
    """
    Render a list of named sections into a list of line-lists.

    Args:
        section_names: Section ids to render, in order. Unknown ids are
            silently skipped.
        wifi: Passed through to the "network" section.
        upload: Passed through to the "network" section.
        msg_rate: Passed through to the "adsb_live" section.
        interval: Passed through to the "network" and "adsb_live" sections.
        db_path: Passed through to the "adsb_global"/"adsb_health" sections.
        temp_simple: Passed through to the "temperatures" section.
        retry_lookback_days: Passed through to the "feeder_services" section.
        retry_thresholds_days: Passed through to the "feeder_services" section.
    """
    rendered: list[list[str]] = []
    for name in section_names:
        renderer = SECTION_RENDERERS.get(name)
        if renderer is None:
            continue
        if name == "network":
            rendered.append(renderer(wifi, upload, interval))
        elif name == "adsb_live":
            rendered.append(renderer(msg_rate, interval))
        elif name in ("adsb_global", "adsb_health"):
            rendered.append(renderer(db_path))
        elif name == "temperatures":
            rendered.append(renderer(temp_simple))
        elif name == "feeder_services":
            rendered.append(renderer(retry_lookback_days, retry_thresholds_days))
        else:
            rendered.append(renderer())
    return rendered


def render_layout(
    config: dict[str, Any],
    wifi: WifiState,
    upload: UploadTracker,
    msg_rate: MessageRateTracker,
    interval: int,
) -> str:
    """
    Render the full dashboard according to the configured layout.

    Args:
        config: Loaded config (see DEFAULT_CONFIG for shape).
        wifi: Connection state tracked across loop iterations.
        upload: TX byte counter tracked across loop iterations.
        msg_rate: dump1090-fa message counter tracked across loop iterations.
        interval: Current refresh interval in seconds.
    """
    layout = config.get("layout", DEFAULT_CONFIG["layout"])
    columns = layout.get("columns", 1)
    left_names = layout.get("left", [])
    right_names = layout.get("right", [])
    db_path = Path(config.get("options", {}).get(
        "adsb_stats_db_path", DEFAULT_CONFIG["options"]["adsb_stats_db_path"]
    ))
    temp_simple = config.get("options", {}).get(
        "temp_simple", DEFAULT_CONFIG["options"]["temp_simple"]
    )
    retry_lookback_days = config.get("options", {}).get(
        "retry_lookback_days", DEFAULT_CONFIG["options"]["retry_lookback_days"]
    )
    retry_thresholds_days = config.get("options", {}).get(
        "retry_color_thresholds_days",
        DEFAULT_CONFIG["options"]["retry_color_thresholds_days"],
    )
    if retry_thresholds_days is None:
        retry_thresholds_days = (retry_lookback_days / 3, retry_lookback_days * 2 / 3)
    else:
        retry_thresholds_days = tuple(retry_thresholds_days)

    left = render_sections(
        left_names, wifi, upload, msg_rate, interval, db_path, temp_simple,
        retry_lookback_days, retry_thresholds_days,
    )
    right = render_sections(
        right_names, wifi, upload, msg_rate, interval, db_path, temp_simple,
        retry_lookback_days, retry_thresholds_days,
    )

    if columns == 1:
        return render_single_column(left + right)
    return render_two_column(left, right)
