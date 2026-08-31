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
    update_error_stats, get_last_altitudes, batch_update_aircraft_last_altitude
)
from .geo import haversine_distance
from .aircraft_json import load_aircraft_json, get_message_count, index_by_hex

logger = logging.getLogger(__name__)

# Radio-horizon line-of-sight range tops out well under this even for a
# receiver/aircraft both at generous altitude, and exceptional atmospheric
# ducting DX reception rarely exceeds ~325nm - this ceiling is deliberately
# generous so it only rejects clearly-corrupted position data (e.g. a bad
# CPR decode on dump1090-fa's side), never a genuine reading.
MAX_PLAUSIBLE_DISTANCE_NM = 540

# A corrupted-but-CRC-passing altitude message can land on a value that's
# perfectly valid by format alone (see sbs_parser.py's MIN/MAX_ALTITUDE_FT
# bounds), so a fixed range can't catch every bad reading - it has to be
# checked against the same aircraft's own last accepted reading instead.
# MAX_CLIMB_RATE_FT_PER_MIN is the fallback bound used only when this
# aircraft has no recent reported vertical rate to calibrate against (see
# VERTICAL_RATE_TOLERANCE_FT_PER_MIN below for the normal case) - it's set
# far above genuine performance (well beyond even a fighter jet's
# sustained rate) purely to reject corruption, never a real reading, since
# without a reported rate to narrow against there's no better information
# to calibrate on. MIN_PLAUSIBLE_ALTITUDE_DELTA_FT is a floor under both
# bounds so two messages arriving a fraction of a second apart aren't held
# to a near-zero allowance and false-reject ordinary encoding-quantization
# jitter.
MAX_CLIMB_RATE_FT_PER_MIN = 10000
MIN_PLAUSIBLE_ALTITUDE_DELTA_FT = 250

# When this aircraft has a recent reported vertical rate (SBS field 16,
# "set for type 4" - see sbs_parser.py), the plausibility check centers on
# *that* rate instead of the flat MAX_CLIMB_RATE_FT_PER_MIN bound, mirroring
# readsb's own internal altitude-tracking check (track.c's updateAltitude):
# expected change = reported_rate * elapsed time, allowed to be off by
# VERTICAL_RATE_TOLERANCE_FT_PER_MIN per minute elapsed. This is far
# tighter than the flat bound when an aircraft is reporting a normal climb/
# descent/level rate, which is the common case. VERTICAL_RATE_MAX_AGE_MINUTES
# bounds how long a reported rate stays trusted for this purpose - an
# aircraft's true rate can change substantially within a couple minutes
# (e.g. leveling off), so a stale rate falls back to the flat bound instead
# of being trusted to narrow the window.
VERTICAL_RATE_TOLERANCE_FT_PER_MIN = 1500
VERTICAL_RATE_MAX_AGE_MINUTES = 2

# If the SBS-only check above still finds a reading implausible, fall back
# to comparing against dump1090-fa's own tracked alt_baro for that aircraft
# (via aircraft.json) before rejecting outright - see _confirm_via_aircraft_json.
# This tolerance is deliberately looser than MIN_PLAUSIBLE_ALTITUDE_DELTA_FT
# since the aircraft.json poll happens a moment after the SBS line arrived,
# not at the exact same instant, so some genuine climb/descent in that gap
# is expected; it's still tight enough to clearly separate a genuine match
# from the thousands-of-feet-off readings this whole check exists to catch.
AIRCRAFT_JSON_CONFIRM_TOLERANCE_FT = 1000


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

    last_altitude/last_vertical_rate are separate from all of the above:
    per-ICAO maps of each aircraft's last accepted altitude and last
    reported vertical rate, kept for the lifetime of the process (see
    _check_altitude/_within_plausible_change) rather than reset per
    hour/day/flush, since they exist to catch a corrupted reading before
    it ever reaches the counters above, not to feed the database directly.
    last_altitude is also persisted to seen_aircraft (batched at flush
    time, loaded back in run()) so a restart doesn't erase every
    aircraft's baseline at once; last_vertical_rate is deliberately not
    persisted - it's only ever used to narrow the plausibility window for
    a reading that arrives soon after, so losing it to a restart just
    means the next implausible-looking reading briefly falls back to the
    flatter MAX_CLIMB_RATE_FT_PER_MIN bound until a fresh rate arrives,
    which is harmless.
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

        # icao_hex -> (altitude_ft, time.monotonic() reference, ISO timestamp).
        # The ISO timestamp is unused for last_altitude's own in-process
        # comparisons (time.monotonic() is used for those) - it's carried
        # only so flush() can persist a wall-clock value, and run() can
        # convert it back into a correctly-backdated monotonic reference
        # after a restart.
        self.last_altitude: dict[str, tuple[int, float, str]] = {}
        # icao_hex -> (vertical_rate_fpm, time.monotonic() when recorded).
        # Never persisted - see the class docstring for why that's fine.
        self.last_vertical_rate: dict[str, tuple[int, float]] = {}

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

    def _get_recent_vertical_rate(self, icao_hex: str) -> Optional[int]:
        """
        Look up this aircraft's last reported vertical rate, if recent
        enough to still be trusted (see VERTICAL_RATE_MAX_AGE_MINUTES).

        Args:
            icao_hex: Lowercase ICAO hex address.

        Returns:
            The vertical rate in fpm, or None if unknown or stale.
        """
        entry = self.last_vertical_rate.get(icao_hex)
        if entry is None:
            return None
        vertical_rate_fpm, recorded_monotonic = entry
        age_minutes = (time.monotonic() - recorded_monotonic) / 60
        if age_minutes > VERTICAL_RATE_MAX_AGE_MINUTES:
            return None
        return vertical_rate_fpm

    def _within_plausible_change(self, icao_hex: str, reference: tuple[int, float, str], altitude_ft: int) -> bool:
        """
        Check whether altitude_ft is reachable from a reference reading in
        the time elapsed since it.

        Mirrors readsb's own internal altitude-tracking check
        (track.c's updateAltitude): if this aircraft has a recent reported
        vertical rate, the expected change is that rate times the elapsed
        time, allowed to be off by VERTICAL_RATE_TOLERANCE_FT_PER_MIN per
        minute elapsed. Without a recent rate to calibrate against, falls
        back to the flatter MAX_CLIMB_RATE_FT_PER_MIN bound.

        Args:
            icao_hex: Lowercase ICAO hex address.
            reference: (altitude_ft, time.monotonic() reference, ISO
                timestamp) to compare against - the timestamp is unused
                here (see reset_counters).
            altitude_ft: Newly-reported altitude, feet.

        Returns:
            True if the change from reference is plausible for the
            elapsed time (with a MIN_PLAUSIBLE_ALTITUDE_DELTA_FT floor
            for very small elapsed times).
        """
        reference_altitude_ft, reference_monotonic, _ = reference
        elapsed_minutes = (time.monotonic() - reference_monotonic) / 60
        actual_delta = altitude_ft - reference_altitude_ft

        vertical_rate = self._get_recent_vertical_rate(icao_hex)
        if vertical_rate is not None:
            expected_delta = vertical_rate * elapsed_minutes
            tolerance = max(
                MIN_PLAUSIBLE_ALTITUDE_DELTA_FT,
                VERTICAL_RATE_TOLERANCE_FT_PER_MIN * elapsed_minutes
            )
            return abs(actual_delta - expected_delta) <= tolerance

        max_change = max(
            MIN_PLAUSIBLE_ALTITUDE_DELTA_FT,
            MAX_CLIMB_RATE_FT_PER_MIN * elapsed_minutes
        )
        return abs(actual_delta) <= max_change

    def _confirm_via_aircraft_json(self, icao_hex: str, altitude_ft: int) -> bool:
        """
        Fall back to dump1090-fa's own tracked alt_baro for this aircraft
        when the SBS-only check finds a reading implausible.

        This is deliberately a last resort, not the primary check: it
        means reading and parsing aircraft.json, which the primary
        per-message check avoids doing on every reading. alt_baro reflects
        dump1090-fa's own internally-validated altitude for this aircraft
        (see CONTEXT.md's note on this), which is more authoritative than
        anything derivable from a single SBS line alone.

        Args:
            icao_hex: Lowercase ICAO hex address.
            altitude_ft: The SBS-reported altitude awaiting confirmation, feet.

        Returns:
            True if aircraft.json's alt_baro agrees (within
            AIRCRAFT_JSON_CONFIRM_TOLERANCE_FT), False if it disagrees or
            can't be checked (no current entry, or no numeric alt_baro -
            e.g. the aircraft is on the ground).
        """
        aircraft = index_by_hex(load_aircraft_json(self.aircraft_json_path)).get(icao_hex)
        if aircraft is None:
            logger.warning(
                "Cannot confirm altitude %d ft for %s: no current aircraft.json entry",
                altitude_ft, icao_hex
            )
            return False

        alt_baro = aircraft.get("alt_baro")
        if not isinstance(alt_baro, (int, float)):
            logger.warning(
                "Cannot confirm altitude %d ft for %s: aircraft.json alt_baro is %r",
                altitude_ft, icao_hex, alt_baro
            )
            return False

        agrees = abs(altitude_ft - alt_baro) <= AIRCRAFT_JSON_CONFIRM_TOLERANCE_FT
        if agrees:
            logger.warning(
                "Confirmed altitude %d ft for %s via aircraft.json (alt_baro=%s ft)",
                altitude_ft, icao_hex, alt_baro
            )
        else:
            logger.warning(
                "Rejected altitude %d ft for %s: aircraft.json alt_baro=%s ft disagrees (diff %d ft)",
                altitude_ft, icao_hex, alt_baro, abs(altitude_ft - alt_baro)
            )
        return agrees

    def _check_altitude(self, icao_hex: str, altitude_ft: int, now_str: str) -> bool:
        """
        Validate a new altitude reading against this aircraft's history,
        falling back to aircraft.json if the fast per-message check alone
        finds it implausible.

        A reading consistent with the aircraft's last accepted one (per
        _within_plausible_change) is accepted immediately, with no I/O
        beyond the SBS stream itself - this covers the overwhelming
        majority of readings, including an aircraft legitimately holding a
        steady high altitude for a long stretch. Only a reading that fails
        that check triggers an aircraft.json lookup
        (_confirm_via_aircraft_json) to resolve the ambiguity immediately,
        rather than waiting to see if a later reading happens to agree
        with it.

        Updates self.last_altitude as a side effect - this is not a pure
        predicate.

        Args:
            icao_hex: Lowercase ICAO hex address.
            altitude_ft: Newly-reported altitude, feet.
            now_str: Current message timestamp (ISO UTC string).

        Returns:
            True if this reading should be applied to the alt_max
            counters, False if it was rejected by both checks.
        """
        now_monotonic = time.monotonic()
        last = self.last_altitude.get(icao_hex)

        if last is None or self._within_plausible_change(icao_hex, last, altitude_ft):
            self.last_altitude[icao_hex] = (altitude_ft, now_monotonic, now_str)
            return True

        known_rate = self._get_recent_vertical_rate(icao_hex)
        rate_desc = f"known vertical rate {known_rate} fpm" if known_rate is not None else "no known vertical rate (flat bound)"
        logger.warning(
            "SBS altitude check failed for %s: %d ft vs last accepted %d ft (%s) - checking aircraft.json",
            icao_hex, altitude_ft, last[0], rate_desc
        )

        if self._confirm_via_aircraft_json(icao_hex, altitude_ft):
            self.last_altitude[icao_hex] = (altitude_ft, now_monotonic, now_str)
            return True

        return False

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

            if msg.vertical_rate_fpm is not None:
                self.last_vertical_rate[msg.icao_hex] = (msg.vertical_rate_fpm, time.monotonic())

            if msg.altitude_ft is not None:
                if self._check_altitude(msg.icao_hex, msg.altitude_ft, now_str):
                    self._apply_altitude(msg.altitude_ft, msg.icao_hex, now_str)
                else:
                    last_altitude_ft, _, _ = self.last_altitude[msg.icao_hex]
                    logger.warning(
                        "Rejected implausible altitude for %s: %d ft (last accepted: %d ft)",
                        msg.icao_hex, msg.altitude_ft, last_altitude_ft
                    )

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

        if self.last_altitude:
            batch_update_aircraft_last_altitude(
                self.conn,
                [(icao, altitude_ft, ts) for icao, (altitude_ft, _, ts) in self.last_altitude.items()]
            )

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

    def _load_persisted_altitudes(self) -> None:
        """
        Seed self.last_altitude from seen_aircraft so the climb-rate check
        has a baseline immediately after a restart, instead of treating
        every aircraft's next reading as first contact.

        Converts each persisted wall-clock timestamp into a correctly
        backdated time.monotonic() reference - a monotonic value from a
        previous process run has no meaning after a restart, so without
        this conversion a reloaded reading would look like it just
        happened, giving it an artificially tight allowance instead of one
        reflecting how long it's actually been since it was recorded.
        """
        now_monotonic = time.monotonic()
        now_wall = datetime.now(timezone.utc)
        for icao_hex, (altitude_ft, ts_str) in get_last_altitudes(self.conn).items():
            try:
                recorded_at = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            elapsed_seconds = (now_wall - recorded_at).total_seconds()
            self.last_altitude[icao_hex] = (altitude_ft, now_monotonic - elapsed_seconds, ts_str)

    def run(self) -> None:
        """Connect to the SBS stream and process messages until interrupted."""
        self.running = True
        signal.signal(signal.SIGTERM, self._raise_keyboard_interrupt)
        self.reset_counters()
        self.aircraft_updates = []
        self.conn = get_connection(self.db_path)
        self.last_dump1090_msg_count = get_last_dump1090_msg_count(self.conn)
        self._load_persisted_altitudes()

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
