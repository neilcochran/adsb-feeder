-- ADS-B Statistics Collector schema.
-- Applied once by db.init_db() via executescript(); every statement must
-- be safe to re-run against an already-initialized database.

CREATE TABLE IF NOT EXISTS global_stats (
  id                INTEGER PRIMARY KEY DEFAULT 1,
  msg_total         INTEGER NOT NULL DEFAULT 0,
  uaircraft_total   INTEGER NOT NULL DEFAULT 0,
  uflights_total    INTEGER NOT NULL DEFAULT 0,
  alt_max           REAL,
  alt_max_icao      TEXT,
  alt_max_ts        TEXT,
  dist_max_km       REAL,
  dist_max_icao     TEXT,
  dist_max_ts       TEXT,
  first_msg_ts      TEXT,
  last_msg_ts       TEXT,
  -- Last raw value seen from dump1090-fa's own cumulative message counter
  -- (aircraft.json's top-level "messages" field). Used to compute deltas
  -- for msg_total; NULL means "never polled yet". See ingest.py.
  last_dump1090_msg_count INTEGER,
  -- All-time count of exceptions caught by ingest.py's process_message,
  -- plus the most recent one's timestamp/message. process_message logs
  -- and continues on any exception so one malformed message can't kill
  -- the collector, but that also means a real bug would otherwise be
  -- silently swallowed - these columns make that visible. NULL means
  -- no error has ever been recorded.
  error_count       INTEGER NOT NULL DEFAULT 0,
  last_error_ts     TEXT,
  last_error_msg    TEXT
);

-- Ensure exactly one row exists.
INSERT OR IGNORE INTO global_stats (id) VALUES (1);

CREATE TABLE IF NOT EXISTS daily_stats (
  date          TEXT PRIMARY KEY,  -- YYYY-MM-DD (UTC)
  msg_count     INTEGER NOT NULL DEFAULT 0,
  uaircraft     INTEGER NOT NULL DEFAULT 0,
  uflights      INTEGER NOT NULL DEFAULT 0,
  alt_max       REAL,
  alt_max_icao  TEXT,
  alt_max_ts    TEXT,
  dist_max_km   REAL,
  dist_max_icao TEXT,
  dist_max_ts   TEXT
);

CREATE TABLE IF NOT EXISTS hourly_stats (
  ts          TEXT PRIMARY KEY,  -- YYYY-MM-DD HH:00 (UTC)
  msg_count   INTEGER NOT NULL DEFAULT 0,
  uaircraft   INTEGER NOT NULL DEFAULT 0,
  alt_max     REAL,
  dist_max_km REAL
);

-- Global dedup table: every unique ICAO ever observed by this station.
CREATE TABLE IF NOT EXISTS seen_aircraft (
  icao        TEXT PRIMARY KEY,
  first_seen  TEXT NOT NULL,
  last_seen   TEXT NOT NULL
);

-- Daily dedup table: unique (icao, callsign) pairs seen today. Truncated
-- at UTC midnight rollover.
CREATE TABLE IF NOT EXISTS seen_today (
  icao       TEXT NOT NULL,
  callsign   TEXT NOT NULL,
  PRIMARY KEY (icao, callsign)
);
