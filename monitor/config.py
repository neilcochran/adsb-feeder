"""Configuration loading for the ADS-B system monitor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .style import Style

DEFAULT_CONFIG = {
    "version": "1.0",
    "layout": {
        "columns": 2,
        "left": ["uptime", "cpu_usage", "memory", "temperatures"],
        "right": ["feeder_services", "network"]
    },
    "options": {
        "interval": 2,
        "adsb_stats_db_path": "/var/lib/adsb-stats/stats.db",
        "temp_simple": False,
        "retry_lookback_days": 7,
        "retry_color_thresholds_days": None
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
