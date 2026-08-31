"""Command-line interface for ADS-B Statistics Collector."""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from . import __version__
from .config import load_config, save_config, DEFAULT_CONFIG_PATH
from .db import (
    init_db, get_connection, get_global_stats, get_table_rows,
    export_table_to_csv, export_table_to_json, run_query, run_maintenance_sql,
)
from .ingest import IngestLoop
from .queries import QUERIES_DIR, list_saved_queries, load_saved_query

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

    if stats["dist_max_nm"] is not None:
        print("\nMaximum Distance:")
        print(f"  Value: {stats['dist_max_nm']:.2f} nm")
        if stats["dist_max_icao"]:
            print(f"  Aircraft: {stats['dist_max_icao'].upper()}")
        if stats["dist_max_ts"]:
            print(f"  Time: {stats['dist_max_ts']}")

    print(f"\nFirst Message: {stats['first_msg_ts'] or 'N/A'}")
    print(f"Last Message: {stats['last_msg_ts'] or 'N/A'}")

    print(f"\nErrors: {stats['error_count']:,}")
    if stats["error_count"]:
        print(f"  Last: {stats['last_error_ts']} - {stats['last_error_msg']}")

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


def cmd_query(args: argparse.Namespace) -> None:
    """Run a saved .sql query and print its results, or list available ones."""
    if args.list:
        queries = list_saved_queries()
        if not queries:
            print(f"No saved queries in {QUERIES_DIR}")
            return
        for name, description in queries:
            print(f"{name:<24} {description}")
        return

    if not args.name:
        print("Specify a query name, or --list to see available queries.")
        return

    sql = load_saved_query(args.name)
    if sql is None:
        print(f"No saved query named '{args.name}' in {QUERIES_DIR}")
        return

    config = load_config(args.config)
    _configure_logging(config)
    conn = get_connection(config["db_path"])
    try:
        columns, rows = run_query(conn, sql)
    except ValueError as e:
        print(f"Error: {e}")
        return
    finally:
        conn.close()

    _print_query_results(columns, rows, args.format, args.output)


def cmd_exec_sql(args: argparse.Namespace) -> None:
    """
    Run a one-shot, non-read-only SQL script against the database, for
    manual maintenance/cleanup - not the `query` command's saved, enforced-
    read-only .sql files. Exits non-zero on failure so a calling script
    (see bin/adsb-stats's `redeploy` subcommand) can tell success from
    failure without parsing output.
    """
    sql = Path(args.sql_file).read_text() if args.sql_file else args.sql

    config = load_config(args.config)
    _configure_logging(config)
    conn = get_connection(config["db_path"])
    try:
        run_maintenance_sql(conn, sql)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()

    print("SQL executed successfully.")


def _format_table(columns: list[str], rows: list[tuple]) -> str:
    """Render columns/rows as a simple fixed-width text table."""
    if not rows:
        return "(no rows)"

    str_rows = [[str(v) if v is not None else "" for v in row] for row in rows]
    widths = [len(c) for c in columns]
    for row in str_rows:
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len(v))

    def format_row(values: list[str]) -> str:
        return "  ".join(v.ljust(widths[i]) for i, v in enumerate(values))

    lines = [format_row(columns), "  ".join("-" * w for w in widths)]
    lines.extend(format_row(row) for row in str_rows)
    return "\n".join(lines)


def _print_query_results(columns: list[str], rows: list[tuple], fmt: str, output: Optional[str]) -> None:
    """Render query results in the requested format, to a file or stdout."""
    if fmt == "json":
        text = json.dumps([dict(zip(columns, row)) for row in rows], indent=2)
    elif fmt == "csv":
        lines = [",".join(columns)]
        lines.extend(",".join(str(v) for v in row) for row in rows)
        text = "\n".join(lines)
    else:
        text = _format_table(columns, rows)

    if output:
        with open(output, "w") as f:
            f.write(text + "\n")
        print(f"Wrote {len(rows)} row(s) to {output}")
        return

    print(text)
    if fmt == "table":
        print(f"\n({len(rows)} row(s))")


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

    query_parser = subparsers.add_parser("query", help="Run a saved SQL query")
    query_parser.add_argument("name", nargs="?", help="Saved query name (see --list)")
    query_parser.add_argument("--list", "-l", action="store_true", help="List available saved queries")
    query_parser.add_argument("--config", "-c", help="Config file path")
    query_parser.add_argument("--format", "-f", default="table",
                             choices=["table", "csv", "json"],
                             help="Output format (default: table)")
    query_parser.add_argument("--output", "-o", help="Output file path")
    query_parser.set_defaults(func=cmd_query)

    exec_parser = subparsers.add_parser(
        "exec-sql", help="Run a one-shot, non-read-only SQL script (maintenance/cleanup)"
    )
    exec_group = exec_parser.add_mutually_exclusive_group(required=True)
    exec_group.add_argument("--sql", help="Inline SQL to execute")
    exec_group.add_argument("--sql-file", help="Path to a .sql file to execute")
    exec_parser.add_argument("--config", "-c", help="Config file path")
    exec_parser.set_defaults(func=cmd_exec_sql)

    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
