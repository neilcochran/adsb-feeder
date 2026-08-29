"""Configuration management for ADS-B Statistics Collector."""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "sbs_host": "127.0.0.1",
    "sbs_port": 30003,
    "aircraft_json_path": "/run/dump1090-fa/aircraft.json",
    "db_path": "/var/lib/adsb-stats/stats.db",
    "receiver_lat": None,
    "receiver_lon": None,
    "flush_interval_seconds": 300,
    "log_level": "INFO",
}

DEFAULT_CONFIG_PATH = "~/.config/adsb-stats/config.json"

CONFIG_PATHS = [
    DEFAULT_CONFIG_PATH,
    "./config.json",
]


def load_config(config_path: Optional[str] = None) -> dict:
    """
    Load config from a specific file, or search CONFIG_PATHS and create a
    default config if none is found.

    Args:
        config_path: Explicit path to a config file. If given, this is the
            only path checked. If omitted, CONFIG_PATHS is searched in
            order.

    Returns:
        The loaded config, merged over DEFAULT_CONFIG so missing fields
        (e.g. from a config file written before a field existed) fall back
        to their default rather than being absent.
    """
    if config_path:
        paths = [Path(config_path)]
    else:
        paths = [Path(p).expanduser() for p in CONFIG_PATHS]

    for path in paths:
        if path.exists():
            logger.debug("Loading config from %s", path)
            with open(path) as f:
                config = json.load(f)
            merged = DEFAULT_CONFIG.copy()
            merged.update(config)
            return merged

    default_path = Path(DEFAULT_CONFIG_PATH).expanduser()
    default_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Creating default config at %s", default_path)
    with open(default_path, "w") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2)

    return DEFAULT_CONFIG.copy()


def save_config(config: dict, config_path: Optional[str] = None) -> None:
    """
    Save config to a file.

    Args:
        config: Config dict to write.
        config_path: Destination path. Defaults to DEFAULT_CONFIG_PATH if
            omitted.
    """
    path = Path(config_path).expanduser() if config_path else Path(DEFAULT_CONFIG_PATH).expanduser()

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(config, f, indent=2)

    logger.info("Config saved to %s", path)
