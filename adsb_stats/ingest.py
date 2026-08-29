"""Main ingestion loop for ADS-B statistics collection."""

import logging
import signal
import time
from datetime import datetime, timezone
from typing import Any, Optional

from .sbs_client import SBSClient
from .sbs_parser import parse_sbs_line
from .db import (
    get_connection, upsert_hourly, upsert_daily, update_global_incremental,
    try_insert_aircraft, try_insert_flight, truncate_seen_today,
    batch_update_aircraft_last_seen, get_last_dump1090_msg_count,
    update_error_stats
)
from .geo import haversine_distance
from .aircraft_json import load_aircraft_json, get_message_count

logger = logging.getLogger(__name__)

# Radio-horizon line-of-sight range tops out well under this even for a
# receiver/aircraft both at generous altitude, and exceptional atmospheric
# ducting DX reception rarely exceeds ~325nm - this ceiling is deliberately
# generous so it only rejects clearly-corrupted position data (e.g. a bad
# CPR decode on dump1090-fa's side), never a genuine reading.
MAX_PLAUSIBLE_DISTANCE_NM = 540


class IngestLoop:
    """Main ingestion loop coordinating the SBS client, parser, and database.

    dump1090-fa's SBS output arrives already decoded (including position),
    so this loop has no message decoding of its own to do - it only parses
    the CSV line, deduplicates, and aggregates.

    Two families of in-memory counters feed the database, and they are
    reset on different schedules because the SQL that consumes them uses
    different upsert semantics:

    - msg_count_hour/msg_count_day are *deltas since the last write*. They
      are reset to 0 immediately after every upsert because the database
      side does `column = column + excluded.column`
      (see db.upsert_hourly/upsert_daily).
    - uaircraft_*/uflights_*/alt_max_*/dist_max_* (hour/day) are
      *cumulative since the start of the current period*. They are reset
      only on hour/day rollover because the database side does
      `MAX(column, excluded.column)` - re-sending the same running total
      on every flush is intentional and idempotent-safe.

    The *_flush variants of uaircraft/uflights/alt_max/dist_max are a third
    kind: true deltas-since-last-flush, tracked separately from the hourly
    ones so an hour rollover landing between two flushes can't corrupt the
    global counter. error_count_flush follows this same flush-delta family.

    Global msg_total is *not* derived from counting SBS lines at all - it's
    computed from dump1090-fa's own cumulative message counter in
    aircraft.json (see _poll_dump1090_message_delta), which covers more of
    dump1090-fa's raw Mode S traffic than what shows up as SBS lines.
    hourly_stats/daily_stats.msg_count remain SBS-line-counted; the two are
    not expected to reconcile exactly.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.sbs_client = SBSClient(
            config.get("sbs_host", "127.0.0.1"),
            config.get("sbs_port", 30003)
        )

        self.db_path = config.get("db_path", "/var/lib/adsb-stats/stats.db")
        self.flush_interval = config.get("flush_interval_seconds", 300)
        self.receiver_lat = config.get("receiver_lat")
        self.receiver_lon = config.get("receiver_lon")
        self.aircraft_json_path = config.get("aircraft_json_path", "/run/dump1090-fa/aircraft.json")

        self.conn = None
        # Loaded from the database in run() so it survives our own restarts,
        # not just reset here - see _poll_dump1090_message_delta.
        self.last_dump1090_msg_count: Optional[int] = None
        self.reset_counters()
        self.aircraft_updates: list[tuple[str, str]] = []
        self.current_utc_date: Optional[str] = None
        self.current_utc_hour: Optional[str] = None
        self.running = False

    def reset_counters(self) -> None:
        """Reset all in-memory counters (only safe to call at startup)."""
        self.msg_count_hour = 0
        self.msg_count_day = 0

        self.uaircraft_hour = 0
        self.uaircraft_day = 0
        self.uflights_day = 0

        self.uaircraft_flush_delta = 0
        self.uflights_flush_delta = 0

        self.error_count_flush = 0
        self.last_error_ts: Optional[str] = None
        self.last_error_msg: Optional[str] = None

        self.alt_max_hour: Optional[float] = None
        self.alt_max_hour_icao: Optional[str] = None
        self.alt_max_hour_ts: Optional[str] = None

        self.alt_max_day: Optional[float] = None
        self.alt_max_day_icao: Optional[str] = None
        self.alt_max_day_ts: Optional[str] = None

        self.alt_max_flush: Optional[float] = None
        self.alt_max_flush_icao: Optional[str] = None
        self.alt_max_flush_ts: Optional[str] = None

        self.dist_max_hour: Optional[float] = None

        self.dist_max_day: Optional[float] = None
        self.dist_max_day_icao: Optional[str] = None
        self.dist_max_day_ts: Optional[str] = None

        self.dist_max_flush: Optional[float] = None
        self.dist_max_flush_icao: Optional[str] = None
        self.dist_max_flush_ts: Optional[str] = None

    def get_current_timestamp(self) -> str:
        """Get current UTC timestamp as ISO string."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def get_current_hour_key(self) -> str:
        """Get current hour key for hourly_stats (YYYY-MM-DD HH:00)."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:00")

    def get_current_day_key(self) -> str:
        """Get current day key for daily_stats (YYYY-MM-DD)."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def check_rollovers(self) -> None:
        """Check for hour/day rollovers and finalize the outgoing period."""
        now = datetime.now(timezone.utc)
        current_date = now.strftime("%Y-%m-%d")
        current_hour = now.strftime("%Y-%m-%d %H:00")

        if self.current_utc_hour and self.current_utc_hour != current_hour:
            logger.info("Hour rollover: %s -> %s", self.current_utc_hour, current_hour)

            upsert_hourly(self.conn, self.current_utc_hour,
                         self.msg_count_hour, self.uaircraft_hour,
                         self.alt_max_hour, self.alt_max_hour_icao, self.alt_max_hour_ts,
                         self.dist_max_hour)

            self.msg_count_hour = 0
            self.uaircraft_hour = 0
            self.alt_max_hour = None
            self.alt_max_hour_icao = None
            self.alt_max_hour_ts = None
            self.dist_max_hour = None

        if self.current_utc_date and self.current_utc_date != current_date:
            logger.info("UTC midnight reached, processing day rollover")

            upsert_daily(self.conn, self.current_utc_date,
                        self.msg_count_day, self.uaircraft_day, self.uflights_day,
                        self.alt_max_day, self.alt_max_day_icao, self.alt_max_day_ts,
                        self.dist_max_day, self.dist_max_day_icao, self.dist_max_day_ts)

            truncate_seen_today(self.conn)

            self.msg_count_day = 0
            self.uaircraft_day = 0
            self.uflights_day = 0
            self.alt_max_day = None
            self.alt_max_day_icao = None
            self.alt_max_day_ts = None
            self.dist_max_day = None
            self.dist_max_day_icao = None
            self.dist_max_day_ts = None

        self.current_utc_date = current_date
        self.current_utc_hour = current_hour

    def _apply_altitude(self, altitude_ft: int, icao_hex: str, now_str: str) -> None:
        """Fold a new altitude reading into the hour/day/flush maxima."""
        if self.alt_max_hour is None or altitude_ft > self.alt_max_hour:
            self.alt_max_hour = altitude_ft
            self.alt_max_hour_icao = icao_hex
            self.alt_max_hour_ts = now_str

        if self.alt_max_day is None or altitude_ft > self.alt_max_day:
            self.alt_max_day = altitude_ft
            self.alt_max_day_icao = icao_hex
            self.alt_max_day_ts = now_str

        if self.alt_max_flush is None or altitude_ft > self.alt_max_flush:
            self.alt_max_flush = altitude_ft
            self.alt_max_flush_icao = icao_hex
            self.alt_max_flush_ts = now_str

    def _apply_distance(self, distance_nm: float, icao_hex: str, now_str: str) -> None:
        """Fold a new distance reading into the hour/day/flush maxima."""
        if self.dist_max_hour is None or distance_nm > self.dist_max_hour:
            self.dist_max_hour = distance_nm

        if self.dist_max_day is None or distance_nm > self.dist_max_day:
            self.dist_max_day = distance_nm
            self.dist_max_day_icao = icao_hex
            self.dist_max_day_ts = now_str

        if self.dist_max_flush is None or distance_nm > self.dist_max_flush:
            self.dist_max_flush = distance_nm
            self.dist_max_flush_icao = icao_hex
            self.dist_max_flush_ts = now_str

    def process_message(self, line: str) -> None:
        """
        Parse and process a single SBS line: dedup, and fold into the
        in-memory counters. Broadly catches and logs errors rather than
        raising, so one malformed message or transient DB hiccup can't take
        down the whole ingest loop.

        Args:
            line: One line of text from the SBS stream.
        """
        try:
            msg = parse_sbs_line(line)
            if msg is None:
                return

            now_str = self.get_current_timestamp()

            self.msg_count_hour += 1
            self.msg_count_day += 1

            if try_insert_aircraft(self.conn, msg.icao_hex, now_str):
                self.uaircraft_hour += 1
                self.uaircraft_day += 1
                self.uaircraft_flush_delta += 1
            self.aircraft_updates.append((msg.icao_hex, now_str))

            callsign = msg.callsign if msg.callsign else msg.icao_hex
            if try_insert_flight(self.conn, msg.icao_hex, callsign):
                self.uflights_day += 1
                self.uflights_flush_delta += 1

            if msg.altitude_ft is not None:
                self._apply_altitude(msg.altitude_ft, msg.icao_hex, now_str)

            if msg.is_position and self.receiver_lat and self.receiver_lon:
                distance = haversine_distance(
                    self.receiver_lat, self.receiver_lon, msg.lat, msg.lon
                )
                if distance <= MAX_PLAUSIBLE_DISTANCE_NM:
                    self._apply_distance(distance, msg.icao_hex, now_str)

        except Exception as e:
            self.error_count_flush += 1
            self.last_error_ts = self.get_current_timestamp()
            self.last_error_msg = f"{type(e).__name__}: {e}"[:200]
            logger.error("Error processing message: %s", e)

    def _poll_dump1090_message_delta(self) -> int:
        """
        Poll dump1090-fa's aircraft.json for its cumulative message counter
        and return how many new messages it has seen since the last poll.

        Detects a counter decrease (dump1090-fa restarted) and treats the
        new value as the count since that restart, rather than computing a
        negative delta. Returns 0 if aircraft.json couldn't be read this
        cycle, or on the very first poll ever (which only establishes the
        baseline - we don't know how many messages happened before we
        started watching).

        Returns:
            Message count delta to add to msg_total this flush.
        """
        current = get_message_count(load_aircraft_json(self.aircraft_json_path))
        if current is None:
            return 0

        if self.last_dump1090_msg_count is None:
            delta = 0
        elif current < self.last_dump1090_msg_count:
            logger.info("dump1090-fa message counter decreased (%d -> %d); "
                       "treating as a dump1090-fa restart",
                       self.last_dump1090_msg_count, current)
            delta = current
        else:
            delta = current - self.last_dump1090_msg_count

        self.last_dump1090_msg_count = current
        return delta

    def flush(self) -> None:
        """Flush in-memory counters to database."""
        if self.msg_count_hour == 0 and not self.aircraft_updates:
            return  # Nothing to flush

        batch_update_aircraft_last_seen(self.conn, self.aircraft_updates)
        self.aircraft_updates.clear()

        hour_key = self.get_current_hour_key()
        upsert_hourly(self.conn, hour_key,
                     self.msg_count_hour, self.uaircraft_hour,
                     self.alt_max_hour, self.alt_max_hour_icao, self.alt_max_hour_ts,
                     self.dist_max_hour)
        self.msg_count_hour = 0

        day_key = self.get_current_day_key()
        upsert_daily(self.conn, day_key,
                    self.msg_count_day, self.uaircraft_day, self.uflights_day,
                    self.alt_max_day, self.alt_max_day_icao, self.alt_max_day_ts,
                    self.dist_max_day, self.dist_max_day_icao, self.dist_max_day_ts)
        self.msg_count_day = 0

        msg_delta = self._poll_dump1090_message_delta()
        now_str = self.get_current_timestamp()
        update_global_incremental(self.conn,
                                 msg_delta,
                                 self.uaircraft_flush_delta,
                                 self.uflights_flush_delta,
                                 self.alt_max_flush, self.alt_max_flush_icao, self.alt_max_flush_ts,
                                 self.dist_max_flush, self.dist_max_flush_icao, self.dist_max_flush_ts,
                                 now_str,
                                 now_str,
                                 self.last_dump1090_msg_count)

        if self.error_count_flush > 0:
            update_error_stats(self.conn, self.error_count_flush,
                              self.last_error_ts, self.last_error_msg)
            self.error_count_flush = 0

        self.uaircraft_flush_delta = 0
        self.uflights_flush_delta = 0
        self.alt_max_flush = None
        self.alt_max_flush_icao = None
        self.alt_max_flush_ts = None
        self.dist_max_flush = None
        self.dist_max_flush_icao = None
        self.dist_max_flush_ts = None

    def _raise_keyboard_interrupt(self, signum: int, frame) -> None:
        """
        SIGTERM handler that reuses the existing KeyboardInterrupt shutdown
        path (final flush, clean disconnect).

        Python's default SIGTERM disposition just terminates the process
        without running finally blocks - unlike SIGINT, which Python already
        turns into a KeyboardInterrupt by default. `systemctl stop`/`restart`
        send SIGTERM, so without this, every service restart would silently
        drop up to flush_interval_seconds of unflushed counters. Raising
        (rather than only setting a flag the loop polls) matters here
        specifically because the loop is usually blocked inside a generator
        waiting on a socket read - a flag can't interrupt that; an exception
        propagates through it immediately, the same way Ctrl+C already does.
        """
        raise KeyboardInterrupt()

    def run(self) -> None:
        """Connect to the SBS stream and process messages until interrupted."""
        self.running = True
        signal.signal(signal.SIGTERM, self._raise_keyboard_interrupt)
        self.reset_counters()
        self.aircraft_updates = []
        self.conn = get_connection(self.db_path)
        self.last_dump1090_msg_count = get_last_dump1090_msg_count(self.conn)

        now = datetime.now(timezone.utc)
        self.current_utc_date = now.strftime("%Y-%m-%d")
        self.current_utc_hour = now.strftime("%Y-%m-%d %H:00")

        self.sbs_client.connect()

        logger.info("Starting ADS-B statistics ingestion...")

        message_generator = self.sbs_client.get_message_stream()

        try:
            start_time = time.monotonic()

            for line in message_generator:
                if not self.running:
                    break

                self.process_message(line)
                self.check_rollovers()

                elapsed = time.monotonic() - start_time
                if elapsed >= self.flush_interval:
                    logger.debug("Flushing after %.0fs (threshold: %ds)", elapsed, self.flush_interval)
                    self.flush()
                    start_time = time.monotonic()

        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        except Exception as e:
            logger.error("Ingestion error: %s", e)
            raise
        finally:
            self.flush()
            self.sbs_client.disconnect()
            if self.conn is not None:
                self.conn.close()
                self.conn = None
            self.running = False
