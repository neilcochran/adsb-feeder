"""SQLite database operations for ADS-B statistics."""

import json
import sqlite3
from pathlib import Path
from typing import Optional

VALID_EXPORT_TABLES = frozenset({"global_stats", "daily_stats", "hourly_stats"})

_READ_ONLY_PREFIXES = ("select", "with", "explain")


def init_db(db_path: str) -> None:
    """
    Initialize the database with schema, migrating databases created before
    a column existed.

    Args:
        db_path: Filesystem path to the SQLite database file. Created if
            it doesn't exist, along with any missing parent directories.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    try:
        schema_path = Path(__file__).parent / "sql" / "schema.sql"
        with open(schema_path) as f:
            schema = f.read()
        # executescript() commits any pending transaction before running,
        # but provides no further implicit transaction control of its own -
        # commit explicitly rather than relying on `with conn:` here.
        conn.executescript(schema)
        conn.commit()
        _migrate_schema(conn)
    finally:
        conn.close()


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Add columns to existing databases that predate them. Idempotent."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(global_stats)")
    columns = {row[1] for row in cursor.fetchall()}
    if "last_dump1090_msg_count" not in columns:
        with conn:
            conn.execute(
                "ALTER TABLE global_stats ADD COLUMN last_dump1090_msg_count INTEGER"
            )
    if "error_count" not in columns:
        with conn:
            conn.execute(
                "ALTER TABLE global_stats ADD COLUMN error_count INTEGER NOT NULL DEFAULT 0"
            )
            conn.execute("ALTER TABLE global_stats ADD COLUMN last_error_ts TEXT")
            conn.execute("ALTER TABLE global_stats ADD COLUMN last_error_msg TEXT")

    cursor.execute("PRAGMA table_info(hourly_stats)")
    hourly_columns = {row[1] for row in cursor.fetchall()}
    if "alt_max_icao" not in hourly_columns:
        with conn:
            conn.execute("ALTER TABLE hourly_stats ADD COLUMN alt_max_icao TEXT")
            conn.execute("ALTER TABLE hourly_stats ADD COLUMN alt_max_ts TEXT")


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open a database connection."""
    return sqlite3.connect(str(db_path))


def upsert_hourly(conn: sqlite3.Connection, ts: str, msg_count: int, uaircraft: int,
                  alt_max: Optional[float], alt_max_icao: Optional[str],
                  alt_max_ts: Optional[str], dist_max_nm: Optional[float]) -> None:
    """
    Upsert an hourly_stats row, accumulating msg_count and taking the max of
    the running totals (uaircraft/alt_max/dist_max_nm are expected to be
    cumulative-since-hour-start values, not deltas - see ingest.py).

    Args:
        conn: Open database connection.
        ts: Hour key, "YYYY-MM-DD HH:00" (UTC).
        msg_count: Delta message count since the last write for this hour.
        uaircraft: Cumulative unique-aircraft count so far this hour.
        alt_max: Cumulative max altitude (ft) so far this hour, or None.
        alt_max_icao: ICAO hex of the aircraft holding alt_max, or None.
        alt_max_ts: Timestamp alt_max was recorded, or None.
        dist_max_nm: Cumulative max distance (nm) so far this hour, or None.
    """
    with conn:
        conn.execute("""
            INSERT INTO hourly_stats (ts, msg_count, uaircraft, alt_max, alt_max_icao, alt_max_ts, dist_max_nm)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ts) DO UPDATE SET
                msg_count = msg_count + excluded.msg_count,
                uaircraft = MAX(uaircraft, excluded.uaircraft),
                alt_max = CASE WHEN excluded.alt_max > alt_max OR alt_max IS NULL THEN excluded.alt_max ELSE alt_max END,
                alt_max_icao = CASE WHEN excluded.alt_max > alt_max OR alt_max IS NULL THEN excluded.alt_max_icao ELSE alt_max_icao END,
                alt_max_ts = CASE WHEN excluded.alt_max > alt_max OR alt_max IS NULL THEN excluded.alt_max_ts ELSE alt_max_ts END,
                dist_max_nm = MAX(dist_max_nm, excluded.dist_max_nm)
        """, (ts, msg_count, uaircraft, alt_max, alt_max_icao, alt_max_ts, dist_max_nm))


def upsert_daily(conn: sqlite3.Connection, date: str, msg_count: int, uaircraft: int,
                 uflights: int, alt_max: Optional[float], alt_max_icao: Optional[str],
                 alt_max_ts: Optional[str], dist_max_nm: Optional[float],
                 dist_max_icao: Optional[str], dist_max_ts: Optional[str]) -> None:
    """
    Upsert a daily_stats row, accumulating msg_count and tracking maxima.

    Args:
        conn: Open database connection.
        date: Day key, "YYYY-MM-DD" (UTC).
        msg_count: Delta message count since the last write for this day.
        uaircraft: Cumulative unique-aircraft count so far today.
        uflights: Cumulative unique-flight count so far today.
        alt_max: Cumulative max altitude (ft) so far today, or None.
        alt_max_icao: ICAO hex of the aircraft holding alt_max, or None.
        alt_max_ts: Timestamp alt_max was recorded, or None.
        dist_max_nm: Cumulative max distance (nm) so far today, or None.
        dist_max_icao: ICAO hex of the aircraft holding dist_max_nm, or None.
        dist_max_ts: Timestamp dist_max_nm was recorded, or None.
    """
    with conn:
        conn.execute("""
            INSERT INTO daily_stats (date, msg_count, uaircraft, uflights,
                                    alt_max, alt_max_icao, alt_max_ts,
                                    dist_max_nm, dist_max_icao, dist_max_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                msg_count = msg_count + excluded.msg_count,
                uaircraft = MAX(uaircraft, excluded.uaircraft),
                uflights = MAX(uflights, excluded.uflights),
                alt_max = CASE WHEN excluded.alt_max > alt_max OR alt_max IS NULL THEN excluded.alt_max ELSE alt_max END,
                alt_max_icao = CASE WHEN excluded.alt_max > alt_max OR alt_max IS NULL THEN excluded.alt_max_icao ELSE alt_max_icao END,
                alt_max_ts = CASE WHEN excluded.alt_max > alt_max OR alt_max IS NULL THEN excluded.alt_max_ts ELSE alt_max_ts END,
                dist_max_nm = CASE WHEN excluded.dist_max_nm > dist_max_nm OR dist_max_nm IS NULL THEN excluded.dist_max_nm ELSE dist_max_nm END,
                dist_max_icao = CASE WHEN excluded.dist_max_nm > dist_max_nm OR dist_max_nm IS NULL THEN excluded.dist_max_icao ELSE dist_max_icao END,
                dist_max_ts = CASE WHEN excluded.dist_max_nm > dist_max_nm OR dist_max_nm IS NULL THEN excluded.dist_max_ts ELSE dist_max_ts END
        """, (date, msg_count, uaircraft, uflights, alt_max, alt_max_icao, alt_max_ts,
              dist_max_nm, dist_max_icao, dist_max_ts))


def update_global_incremental(conn: sqlite3.Connection, msg_delta: int, uaircraft_delta: int,
                              uflights_delta: int, alt_max: Optional[float],
                              alt_max_icao: Optional[str], alt_max_ts: Optional[str],
                              dist_max_nm: Optional[float], dist_max_icao: Optional[str],
                              dist_max_ts: Optional[str], first_msg_ts: Optional[str],
                              last_msg_ts: str, last_dump1090_msg_count: Optional[int]) -> None:
    """
    Update the single global_stats row incrementally.

    Args:
        conn: Open database connection.
        msg_delta: Messages to add to msg_total since the last write.
        uaircraft_delta: New unique aircraft to add to uaircraft_total.
        uflights_delta: New unique flights to add to uflights_total.
        alt_max: Best altitude (ft) seen since the last write, or None.
        alt_max_icao: ICAO hex of the aircraft holding alt_max, or None.
        alt_max_ts: Timestamp alt_max was recorded, or None.
        dist_max_nm: Best distance (nm) seen since the last write, or None.
        dist_max_icao: ICAO hex of the aircraft holding dist_max_nm, or None.
        dist_max_ts: Timestamp dist_max_nm was recorded, or None.
        first_msg_ts: Candidate first-message timestamp; only takes effect
            if first_msg_ts is still unset (COALESCE), since it's meant to
            be immutable once recorded.
        last_msg_ts: Timestamp of the most recent message.
        last_dump1090_msg_count: Latest raw value from dump1090-fa's own
            message counter, persisted so it survives our own restarts -
            see ingest.py's _poll_dump1090_message_delta.
    """
    with conn:
        conn.execute("""
            UPDATE global_stats SET
                msg_total = msg_total + ?,
                uaircraft_total = uaircraft_total + ?,
                uflights_total = uflights_total + ?,
                alt_max = CASE WHEN ? > alt_max OR alt_max IS NULL THEN ? ELSE alt_max END,
                alt_max_icao = CASE WHEN ? > alt_max OR alt_max IS NULL THEN ? ELSE alt_max_icao END,
                alt_max_ts = CASE WHEN ? > alt_max OR alt_max IS NULL THEN ? ELSE alt_max_ts END,
                dist_max_nm = CASE WHEN ? > dist_max_nm OR dist_max_nm IS NULL THEN ? ELSE dist_max_nm END,
                dist_max_icao = CASE WHEN ? > dist_max_nm OR dist_max_nm IS NULL THEN ? ELSE dist_max_icao END,
                dist_max_ts = CASE WHEN ? > dist_max_nm OR dist_max_nm IS NULL THEN ? ELSE dist_max_ts END,
                first_msg_ts = COALESCE(first_msg_ts, ?),
                last_msg_ts = ?,
                last_dump1090_msg_count = ?
            WHERE id = 1
        """, (msg_delta, uaircraft_delta, uflights_delta,
              alt_max, alt_max, alt_max, alt_max_icao, alt_max, alt_max_ts,
              dist_max_nm, dist_max_nm, dist_max_nm, dist_max_icao, dist_max_nm, dist_max_ts,
              first_msg_ts, last_msg_ts, last_dump1090_msg_count))


def update_error_stats(conn: sqlite3.Connection, error_delta: int,
                       last_error_ts: str, last_error_msg: str) -> None:
    """
    Add to the global error count and overwrite the last-error fields.

    Unlike alt_max/dist_max, last_error_ts/last_error_msg are always
    overwritten rather than MAX-compared - "most recent" is what matters
    here, the same as last_msg_ts.

    Args:
        conn: Open database connection.
        error_delta: Errors to add to error_count since the last write.
        last_error_ts: Timestamp of the most recent error in this batch.
        last_error_msg: Short description of the most recent error in this
            batch.
    """
    with conn:
        conn.execute("""
            UPDATE global_stats SET
                error_count = error_count + ?,
                last_error_ts = ?,
                last_error_msg = ?
            WHERE id = 1
        """, (error_delta, last_error_ts, last_error_msg))


def get_last_dump1090_msg_count(conn: sqlite3.Connection) -> Optional[int]:
    """Get the last-seen raw dump1090-fa message counter, or None if never polled."""
    cursor = conn.cursor()
    cursor.execute("SELECT last_dump1090_msg_count FROM global_stats WHERE id = 1")
    row = cursor.fetchone()
    return row[0] if row else None


def try_insert_aircraft(conn: sqlite3.Connection, icao_hex: str, timestamp: str) -> bool:
    """
    Try to insert an aircraft into seen_aircraft.

    Args:
        conn: Open database connection.
        icao_hex: Lowercase ICAO hex address.
        timestamp: ISO timestamp to record as first_seen and last_seen.

    Returns:
        True if this was a new aircraft (row inserted), False if it was
        already known.
    """
    with conn:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO seen_aircraft (icao, first_seen, last_seen)
            VALUES (?, ?, ?)
        """, (icao_hex, timestamp, timestamp))
        return cursor.rowcount > 0


def batch_update_aircraft_last_seen(conn: sqlite3.Connection,
                                    aircraft_timestamps: list[tuple[str, str]]) -> None:
    """
    Batch-update last_seen for multiple aircraft in one transaction.

    Args:
        conn: Open database connection.
        aircraft_timestamps: (icao_hex, timestamp) pairs to apply.
    """
    with conn:
        conn.executemany(
            "UPDATE seen_aircraft SET last_seen = ? WHERE icao = ?",
            [(timestamp, icao_hex) for icao_hex, timestamp in aircraft_timestamps]
        )


def try_insert_flight(conn: sqlite3.Connection, icao_hex: str, callsign: str) -> bool:
    """
    Try to insert a (icao, callsign) flight into seen_today.

    Args:
        conn: Open database connection.
        icao_hex: Lowercase ICAO hex address.
        callsign: Callsign, or the ICAO hex itself as a fallback when no
            callsign has been received yet - see ingest.py.

    Returns:
        True if this was a new (icao, callsign) pair (row inserted), False
        if it was already known.
    """
    with conn:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO seen_today (icao, callsign)
            VALUES (?, ?)
        """, (icao_hex, callsign))
        return cursor.rowcount > 0


def truncate_seen_today(conn: sqlite3.Connection) -> None:
    """Delete all rows from seen_today (called at UTC midnight rollover)."""
    with conn:
        conn.execute("DELETE FROM seen_today")


def get_global_stats(conn: sqlite3.Connection) -> Optional[dict]:
    """
    Fetch the single global_stats row.

    Builds the dict from cursor.description rather than positional indices,
    since ALTER TABLE ADD COLUMN/DROP COLUMN (see _migrate_schema) can leave
    an upgraded database's on-disk column order different from a fresh
    schema.sql CREATE TABLE's declared order.

    Returns:
        dict of column name -> value, or None if the row is somehow missing
        (shouldn't happen once init_db has run).
    """
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM global_stats WHERE id = 1")
    row = cursor.fetchone()
    if row is None:
        return None
    columns = [description[0] for description in cursor.description]
    return dict(zip(columns, row))


def get_table_rows(conn: sqlite3.Connection, table_name: str) -> tuple[list[str], list[tuple]]:
    """
    Fetch all rows and column names from one of the exportable stats tables.

    Centralizes the table-name allowlist so every export path (CSV file,
    JSON file, CSV to stdout) is safe against SQL injection via table_name,
    not just whichever caller happens to validate it first.

    Args:
        conn: Open database connection.
        table_name: One of VALID_EXPORT_TABLES.

    Returns:
        (column_names, rows) - rows is a list of sqlite3 row tuples.

    Raises:
        ValueError: If table_name isn't one of VALID_EXPORT_TABLES.
    """
    if table_name not in VALID_EXPORT_TABLES:
        raise ValueError(f"Unknown table: {table_name}")

    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM {table_name}")
    rows = cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    return columns, rows


def _strip_sql_comments(sql: str) -> str:
    """
    Strip `--` line comments, for checking a statement's leading keyword.

    Line-based only (doesn't understand string literals containing "--"),
    which is fine for its one caller, run_query()'s read-only check - the
    original sql, comments included, is still what actually gets executed;
    SQLite skips `--` comments on its own.
    """
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines()).strip()


def run_query(conn: sqlite3.Connection, sql: str) -> tuple[list[str], list[tuple]]:
    """
    Execute a saved read-only query and return its results.

    This is the enforcement point for the `query` CLI command's saved .sql
    files: the same allowlist-in-db.py pattern VALID_EXPORT_TABLES uses for
    table names, applied here to the statement's leading keyword instead.

    Args:
        conn: Open database connection.
        sql: SQL text to execute - must be a single SELECT/WITH/EXPLAIN
            statement (raw text, `--` comments included; SQLite skips
            those itself).

    Returns:
        (column_names, rows).

    Raises:
        ValueError: If sql isn't a SELECT/WITH/EXPLAIN statement.
    """
    if not _strip_sql_comments(sql).lower().startswith(_READ_ONLY_PREFIXES):
        raise ValueError("Saved queries must be a single SELECT/WITH/EXPLAIN statement")

    cursor = conn.cursor()
    cursor.execute(sql)
    rows = cursor.fetchall()
    columns = [description[0] for description in cursor.description] if cursor.description else []
    return columns, rows


def export_table_to_csv(conn: sqlite3.Connection, table_name: str, filepath: str) -> list[tuple]:
    """
    Write a stats table to a CSV file.

    Args:
        conn: Open database connection.
        table_name: One of VALID_EXPORT_TABLES.
        filepath: Destination file path.

    Returns:
        The exported rows (empty list if the table had none, in which case
        no file is written).
    """
    columns, rows = get_table_rows(conn, table_name)
    if not rows:
        return []

    with open(filepath, "w") as f:
        f.write(",".join(columns) + "\n")
        for row in rows:
            f.write(",".join(str(v) for v in row) + "\n")

    return rows


def export_table_to_json(conn: sqlite3.Connection, table_name: str) -> list[dict]:
    """
    Export a stats table as a list of row dicts.

    Args:
        conn: Open database connection.
        table_name: One of VALID_EXPORT_TABLES.

    Returns:
        One dict per row, keyed by column name.
    """
    columns, rows = get_table_rows(conn, table_name)
    return [dict(zip(columns, row)) for row in rows]
