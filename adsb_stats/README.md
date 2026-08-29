# ADS-B Statistics Collector - Installation & Configuration Guide

## Overview

`adsb_stats` is a lightweight background collector that connects to
dump1090-fa's SBS/BaseStation output and maintains aggregate ADS-B
statistics (message counts, unique aircraft/flights, altitude and
reception-distance records) in a local SQLite database. It consumes
dump1090-fa's own already-decoded output rather than raw Beast/Mode S -
there is no independent message decoding here, only parsing and
aggregation. This is deliberate: dump1090-fa's own decoder already handles
CRC validation and CPR position decoding correctly, so re-implementing that
from scratch would only reintroduce the same class of bugs.

## Requirements

| Requirement | Details |
|---|---|
| OS | DietPi (Debian 13 Trixie) or compatible Debian-based system |
| Python | 3.10+ (pre-installed on DietPi) |
| dump1090-fa | Running with SBS output enabled (port 30003 by default) |

Zero third-party Python dependencies - standard library only.

## Installation

### Quick Test (manual, no systemd)

Useful for confirming SBS connectivity and checking the numbers look right
before setting up the always-on service:

```bash
cd ~/adsb-feeder
python3 -m adsb_stats.cli init --lat <YOUR_LAT> --lon <YOUR_LON>
python3 -m adsb_stats.cli run
```

`--lat`/`--lon` are optional. Without them, `dist_max_nm` and related
fields stay null; message/aircraft/altitude tracking works regardless.
Ctrl+C stops it cleanly (a final flush happens on exit).

### Systemd Deployment (production, always-on)

```bash
cd ~/adsb-feeder
sudo bash systemd/install.sh
```

This creates a dedicated `adsbstats` system user, deploys the code to
`/opt/adsb-feeder/`, and creates `/etc/adsb-stats/config.json` with a
default (unpositioned) config **only if one doesn't already exist**.

If you already have a config from the manual test above, or just want
distance tracking to actually work, copy it over the auto-created default
and fix its ownership/permissions:

```bash
sudo cp ~/.config/adsb-stats/config.json /etc/adsb-stats/config.json
sudo chown adsbstats:adsbstats /etc/adsb-stats/config.json
sudo chmod 640 /etc/adsb-stats/config.json
```

Then start it:

```bash
sudo systemctl start adsb-stats
sudo systemctl status adsb-stats
```

`install.sh` is safe to re-run any time you update the code - it skips
recreating the user, skips an existing config, skips an existing database,
and just re-syncs `/opt/adsb-feeder/` and reinstalls the systemd unit.
After re-running it, migrate the database *before* restarting the service:

```bash
cd /opt/adsb-feeder
sudo -u adsbstats python3 -m adsb_stats.cli init --config /etc/adsb-stats/config.json
sudo systemctl restart adsb-stats
```

`init` is safe to re-run against an existing database - it only adds
columns a schema change introduced, never touches existing rows (see
`db._migrate_schema()`). Migrating before restarting matters whenever an
update includes a schema change: newly-deployed code can start writing to
a new column immediately, and restarting first would have it do that
against a database that doesn't have that column yet, crashing on the
next write until the migration catches up. Running `init` first (harmless
even when there's nothing to migrate) means the still-running old process
keeps working fine on the old schema while the new column gets added
underneath it, so the new code never finds it missing.

## Usage

Every subcommand accepts `--config`/`-c PATH` to use a specific config file
instead of the auto-detected one (see Configuration below).

### `init`

Create the database (schema and migrations are idempotent - safe to
re-run) and, optionally, set the receiver position:

```bash
python3 -m adsb_stats.cli init [--lat LAT] [--lon LON]
```

`--lat`/`--lon` are only applied if given; omitting them leaves an existing
config's receiver position untouched.

### `run`

Connect to dump1090-fa's SBS stream and collect statistics until
interrupted (Ctrl+C, or SIGTERM under systemd):

```bash
python3 -m adsb_stats.cli run
```

### `status`

Print current all-time totals:

```bash
python3 -m adsb_stats.cli status
```

### `export`

Export a stats table to CSV or JSON, to a file or stdout:

```bash
python3 -m adsb_stats.cli export --table {global,daily,hourly} [--format {csv,json}] [--output PATH]
```

```bash
python3 -m adsb_stats.cli export --table daily --format json
python3 -m adsb_stats.cli export --table hourly --format csv --output hourly.csv
```

### `query`

Run a saved `.sql` query from `adsb_stats/sql/queries/` and print its
results, or list what's available:

```bash
python3 -m adsb_stats.cli query --list
python3 -m adsb_stats.cli query <name> [--format {table,csv,json}] [--output PATH]
```

```bash
python3 -m adsb_stats.cli query busiest_hours
python3 -m adsb_stats.cli query daily_summary --format json --output daily.json
```

Saved queries are plain `.sql` files named `<name>.sql`; a leading `--`
comment line becomes the query's one-line description in `--list` output.
Add your own by dropping a file in `adsb_stats/sql/queries/` - no code
change needed. Only a single `SELECT`/`WITH`/`EXPLAIN` statement is
allowed; anything else (`INSERT`/`UPDATE`/`DELETE`/etc.) is rejected before
it reaches the database.

### `--version`

```bash
python3 -m adsb_stats.cli --version
```

## Configuration

### Config File Locations

Checked in this order:

1. Path passed via `--config FILE`
2. `~/.config/adsb-stats/config.json`
3. `./config.json`

If none are found, a default config is auto-created at
`~/.config/adsb-stats/config.json`. The systemd deployment always passes
`--config /etc/adsb-stats/config.json` explicitly (see the unit file's
`ExecStart`), so the running service never falls back to this search - if
you edit the "wrong" config file, the service won't see the change.

### Config Fields

| Field | Type | Description |
|---|---|---|
| `sbs_host` | string | dump1090-fa's SBS host. Default `127.0.0.1`. |
| `sbs_port` | int | dump1090-fa's SBS port. Default `30003`. |
| `aircraft_json_path` | string | Path to dump1090-fa's `aircraft.json`, used to derive `msg_total` (see Data Model below). Default `/run/dump1090-fa/aircraft.json`. |
| `db_path` | string | SQLite database path. Default `/var/lib/adsb-stats/stats.db`. |
| `receiver_lat` / `receiver_lon` | float or null | Receiver position for distance tracking. `null` disables `dist_max_nm` and related fields entirely - no error, they just stay null. |
| `flush_interval_seconds` | int | How often in-memory counters are written to the database. Default `300` (5 minutes). |
| `log_level` | string | Python logging level (`DEBUG`/`INFO`/`WARNING`/`ERROR`). Default `INFO`. |

### Example

```json
{
  "sbs_host": "127.0.0.1",
  "sbs_port": 30003,
  "aircraft_json_path": "/run/dump1090-fa/aircraft.json",
  "db_path": "/var/lib/adsb-stats/stats.db",
  "receiver_lat": 43.6666,
  "receiver_lon": -70.36367,
  "flush_interval_seconds": 300,
  "log_level": "INFO"
}
```

## Data Model

Three tables track statistics at different granularities:

- **`global_stats`** - a single row of all-time totals: message count,
  unique aircraft/flights, max altitude/distance (with which aircraft and
  when), first/last message timestamps, and an error count with the most
  recent error's time and message (see Error Tracking below).
- **`daily_stats`** - one row per UTC day, same shape as global but scoped
  to that day. The dedup table behind unique-flight counting resets at UTC
  midnight.
- **`hourly_stats`** - one row per UTC hour; a lighter subset (message
  count, unique aircraft, altitude/distance maxima) for trend charts.
  Altitude maxima carry which aircraft and when, same as `daily_stats`;
  distance maxima don't have per-hour attribution.

Unique aircraft are deduplicated by ICAO hex address for the lifetime of
the database. Unique flights are deduplicated by (ICAO, callsign) per day;
if an aircraft's callsign isn't known yet, its ICAO hex is used as a
fallback callsign, so it still counts as at least one flight - if a real
callsign arrives later, that's counted as a second flight, matching how
FlightAware/FR24/ADSBX distinguish separate legs.

### Where `msg_total` comes from

`global_stats.msg_total` is **not** a count of SBS lines received. It's
derived from dump1090-fa's own cumulative message counter (the top-level
`messages` field in `aircraft.json`), which covers more of dump1090-fa's
raw Mode S traffic than what actually shows up as SBS lines. Every flush,
adsb-stats polls that counter and adds the delta since the last poll to
`msg_total`. If the counter goes down (dump1090-fa restarted), the new
value is treated as the count since that restart rather than producing a
negative delta - this is why `msg_total` never resets or goes backward,
even across a dump1090-fa restart, an adsb-stats restart, or both at once.

`daily_stats`/`hourly_stats.msg_count` are still counted from SBS lines
directly, not the aircraft.json counter - the two are not expected to add
up to exactly the same number, since they're measuring at different layers
of the pipeline.

### Error tracking

`ingest.py`'s `process_message` wraps its whole body in a broad
`except Exception`, deliberately - one malformed SBS line or transient DB
hiccup shouldn't take down the whole collector. That resilience comes at a
cost: without something to surface it, a genuine bug in this code (a typo,
an `AttributeError`) would be silently logged and otherwise invisible.
`global_stats.error_count`/`last_error_ts`/`last_error_msg` exist to make
that visible - every exception `process_message` catches increments
`error_count` and overwrites the last-error fields, batched into the
existing flush cycle rather than written per-message. `error_count` never
resets; a climbing count (check it with `status` or the terminal monitor's
`adsb_health` section) is worth investigating even though the collector
itself keeps running through it.

### Granting read access to other tools

`stats.db` is owned by the dedicated `adsbstats` user, so another tool
running as a different user (e.g. `monitor`'s `adsb_global`/`adsb_health`
sections) can't read it by default.
`systemd/install.sh` sets the data directory and database file to `750`/
`640` (owner read/write, group read-only) rather than leaving them at
whatever the ambient umask produces. To grant a user read access, add them
to the `adsbstats` group and have them log out and back in:

```bash
sudo usermod -aG adsbstats <username>
```

SQLite handles concurrent readers safely on its own - this is purely a
file-permission step, not a locking concern.

## Troubleshooting

### `status` fails with a permission error under `sudo -u adsbstats`

`sudo -u adsbstats` doesn't change your working directory. Run it from
`/opt/adsb-feeder` (world-readable) rather than `~/adsb-feeder` (usually
only readable by your own user):

```bash
cd /opt/adsb-feeder
sudo -u adsbstats python3 -m adsb_stats.cli status --config /etc/adsb-stats/config.json
```

### `ModuleNotFoundError: No module named 'adsb_stats'`

`cli.py` uses relative imports and must be run as a module, not as a
script file:

```bash
python3 -m adsb_stats.cli status    # correct
python3 adsb_stats/cli.py status    # fails
```

### `dist_max_nm` stays null / distance tracking isn't working

`receiver_lat`/`receiver_lon` are `null` in the config the running service
is actually using. Check which config that is (`systemctl cat adsb-stats`,
look at `ExecStart`), and confirm that specific file has real coordinates -
see the config-precedence note above, since editing the "wrong" copy is
easy to do by mistake.

### No log output in `journalctl`

Confirm `PYTHONUNBUFFERED=1` is set in the systemd unit
(`systemctl cat adsb-stats`) - without it, stdout is block-buffered under
systemd, so `cli.py run`'s startup banner won't appear until the process
exits. Log lines from Python's `logging` module (connection status,
rollovers, reconnects) go to stderr, which is line-buffered by default, so
they aren't affected by this and should already appear promptly.

### Service won't connect / "Connection failed"

Confirm dump1090-fa is running and has SBS output enabled on the configured
port:

```bash
systemctl status dump1090-fa
ss -tlnp | grep 30003
```

`adsb_stats/tests/test_sbs_client.py` is a standalone connectivity/parsing
check independent of the full service - useful for isolating whether the
problem is the SBS connection itself or something in the ingest loop:

```bash
python3 adsb_stats/tests/test_sbs_client.py --seconds 30
```
