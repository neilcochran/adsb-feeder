"""Command-line interface for ADS-B Statistics Collector."""

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from . import __version__
from .config import load_config, save_config, DEFAULT_CONFIG_PATH
from .db import (
    init_db, get_connection, get_global_stats, get_table_rows,
    export_table_to_csv, export_table_to_json,
)
from .ingest import IngestLoop

TABLE_MAP = {
    "global": "global_stats",
    "daily": "daily_stats",
    "hourly": "hourly_stats",
}


def _configure_logging(config: dict[str, Any]) -> None:
    """
    Attach a handler to the root logger per config['log_level'].

    Without this, every logger.debug/info/warning/error call across the
    package (rollover events, reconnects, the dump1090-fa restart-detection
    log, etc.) is silently dropped - logging.basicConfig() is never called
    automatically, and Python's handler-of-last-resort only surfaces
    WARNING and above.
    """
    logging.basicConfig(
        level=config.get("log_level", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize the database, and the config file's receiver position if given."""
    config = load_config(args.config)
    _configure_logging(config)
    init_db(config["db_path"])
    print(f"Database initialized at {config['db_path']}")

    config_path = Path(args.config).expanduser() if args.config else Path(DEFAULT_CONFIG_PATH).expanduser()
    print(f"Using config: {config_path}")

    if args.lat:
        config["receiver_lat"] = float(args.lat)
        config["receiver_lon"] = float(args.lon) if args.lon else None
        save_config(config, config_path)
        print(f"Receiver position set: ({config['receiver_lat']}, {config['receiver_lon']})")


def cmd_run(args: argparse.Namespace) -> None:
    """Run the ingestion loop until interrupted."""
    config = load_config(args.config)
    _configure_logging(config)

    print(f"ADS-B Statistics Collector v{__version__}")
    print(f"Config: {args.config or 'auto'}")
    print(f"Database: {config['db_path']}")
    if config.get("receiver_lat"):
        print(f"Receiver: ({config['receiver_lat']}, {config['receiver_lon']})")
    print(f"Flush interval: {config['flush_interval_seconds']}s")
    print("\nPress Ctrl+C to stop...\n")

    collector = IngestLoop(config)
    collector.run()


def cmd_status(args: argparse.Namespace) -> None:
    """Print current global statistics to stdout."""
    config = load_config(args.config)
    _configure_logging(config)
    conn = get_connection(config["db_path"])
    try:
        stats = get_global_stats(conn)
    finally:
        conn.close()

    if not stats:
        print("No statistics available yet.")
        return

    print("=" * 50)
    print("ADS-B Statistics Summary")
    print("=" * 50)

    print("\nMessages Received:")
    print(f"  Total: {stats['msg_total']:,}")

    print("\nUnique Aircraft:")
    print(f"  All-time: {stats['uaircraft_total']:,}")

    print("\nUnique Flights (today):")
    print(f"  All-time: {stats['uflights_total']:,}")

    if stats["alt_max"] is not None:
        print("\nMaximum Altitude:")
        print(f"  Value: {stats['alt_max']:,.0f} ft")
        if stats["alt_max_icao"]:
            print(f"  Aircraft: {stats['alt_max_icao'].upper()}")
        if stats["alt_max_ts"]:
            print(f"  Time: {stats['alt_max_ts']}")

    if stats["dist_max_km"] is not None:
        print("\nMaximum Distance:")
        print(f"  Value: {stats['dist_max_km']:.2f} km")
        if stats["dist_max_icao"]:
            print(f"  Aircraft: {stats['dist_max_icao'].upper()}")
        if stats["dist_max_ts"]:
            print(f"  Time: {stats['dist_max_ts']}")

    print(f"\nFirst Message: {stats['first_msg_ts'] or 'N/A'}")
    print(f"Last Message: {stats['last_msg_ts'] or 'N/A'}")

    print("=" * 50)


def cmd_export(args: argparse.Namespace) -> None:
    """Export a stats table to CSV or JSON, to a file or stdout."""
    config = load_config(args.config)
    _configure_logging(config)
    conn = get_connection(config["db_path"])
    try:
        table_name = TABLE_MAP.get(args.table)
        if not table_name:
            print(f"Unknown table: {args.table}")
            print(f"Valid tables: {', '.join(TABLE_MAP.keys())}")
            return

        if args.format == "csv":
            if args.output:
                rows = export_table_to_csv(conn, table_name, args.output)
                print(f"Exported {len(rows)} rows to {args.output}")
            else:
                columns, rows = get_table_rows(conn, table_name)
                print(",".join(columns))
                for row in rows:
                    print(",".join(str(v) for v in row))

        elif args.format == "json":
            data = export_table_to_json(conn, table_name)
            if args.output:
                with open(args.output, "w") as f:
                    json.dump(data, f, indent=2)
                print(f"Exported {len(data)} rows to {args.output}")
            else:
                print(json.dumps(data, indent=2))
    finally:
        conn.close()


def main() -> None:
    """Entry point for `python -m adsb_stats.cli`."""
    parser = argparse.ArgumentParser(
        prog="adsb-stats",
        description="ADS-B Statistics Collection System",
    )
    parser.add_argument(
        "--version", action="version",
        version=f"ADS-B Statistics Collector v{__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    init_parser = subparsers.add_parser("init", help="Initialize database")
    init_parser.add_argument("--config", "-c", help="Config file path")
    init_parser.add_argument("--lat", help="Receiver latitude (optional)")
    init_parser.add_argument("--lon", help="Receiver longitude (optional)")
    init_parser.set_defaults(func=cmd_init)

    run_parser = subparsers.add_parser("run", help="Start ingestion loop")
    run_parser.add_argument("--config", "-c", help="Config file path")
    run_parser.set_defaults(func=cmd_run)

    status_parser = subparsers.add_parser("status", help="Show current statistics")
    status_parser.add_argument("--config", "-c", help="Config file path")
    status_parser.set_defaults(func=cmd_status)

    export_parser = subparsers.add_parser("export", help="Export statistics")
    export_parser.add_argument("--config", "-c", help="Config file path")
    export_parser.add_argument("--table", "-t", required=True,
                              choices=["global", "daily", "hourly"],
                              help="Table to export")
    export_parser.add_argument("--format", "-f", default="csv",
                              choices=["csv", "json"],
                              help="Output format")
    export_parser.add_argument("--output", "-o", help="Output file path")
    export_parser.set_defaults(func=cmd_export)

    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
