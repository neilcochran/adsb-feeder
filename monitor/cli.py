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
    python3 -m monitor.cli                           # auto-detect config
    python3 -m monitor.cli --config stationary.json  # specific config
    python3 -m monitor.cli -i 1                      # 1s refresh override

Available sections:
    uptime, cpu_usage, memory, temperatures, cpu_freq, fan, feeder_services,
    adsb_live, adsb_global, adsb_health, network

Config search order:
    ~/.config/adsb-monitor/config.json
    ./config.json
"""

from __future__ import annotations

import argparse
import sys
import time

from .config import DEFAULT_CONFIG, ConfigError, find_config, load_config
from .layout import render_layout
from .style import Style
from .trackers import MessageRateTracker, UploadTracker, WifiState


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="adsb-monitor",
        description="Odroid-XU4 / DietPi — ADS-B Feeder Dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s                              Auto-detect config, 2s interval\n"
            "  %(prog)s --config stationary.json     Use specific config file\n"
            "  %(prog)s -i 1                         1-second refresh override\n"
            "\n"
            "Config files are searched in this order:\n"
            "  ~/.config/adsb-monitor/config.json\n"
            "  ./config.json\n"
            "\n"
            "Available sections: uptime, cpu_usage, memory, temperatures,\n"
            "                   cpu_freq, feeder_services, network, fan,\n"
            "                   adsb_live, adsb_global, adsb_health\n"
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


def clear_screen() -> None:
    """Clear the terminal using ANSI escape codes."""
    print("\033[2J\033[H", end="")


def main() -> None:
    """Entry point for `python3 -m monitor.cli`."""
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
    msg_rate = MessageRateTracker()

    try:
        while True:
            clear_screen()
            try:
                print(render_layout(config, wifi, upload, msg_rate, interval))
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
