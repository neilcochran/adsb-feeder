# ADS-B System Monitor — Installation & Configuration Guide

## Overview

`monitor` is a real-time terminal dashboard for Odroid-XU4 / DietPi
ADS-B feeder stations. It reads system metrics from sysfs and `/proc`,
queries `systemctl` for feeder service status, and parses `dump1090-fa`'s
live `aircraft.json` to display aircraft tracking statistics — all in a
configurable single or dual-column terminal layout.

## Requirements

| Requirement | Details |
|---|---|
| OS | DietPi (Debian 13 Trixie) or compatible Debian-based system |
| Python | 3.10+ (pre-installed on DietPi) |
| External tools | `iwgetid` or `iw` (for WiFi SSID), `systemctl` (for service status) |

The script has **zero third-party Python dependencies**. It relies entirely
on the standard library and Linux sysfs/procfs interfaces.

## Installation

No build step or global install is required. Simply ensure the script is
present in the repo:

```bash
cd ~/adsb-feeder
ls monitor/cli.py
```

### Verify External Tools

```bash
# iwgetid (preferred for SSID lookup)
which iwgetid

# iw (fallback for SSID lookup)
which iw

# systemctl (for feeder service status)
which systemctl
```

If `iwgetid` is missing:

```bash
sudo apt install -y wireless-tools
```

If `iw` is missing:

```bash
sudo apt install -y iw
```

## Usage

### Basic Run

Start the monitor with auto-detected config (checks `~/.config/adsb-monitor/config.json`
first, then `./config.json`, then falls back to defaults):

```bash
python3 -m monitor.cli
```

### Custom Configuration

Specify a configuration file to change layout, sections, or refresh interval:

```bash
python3 -m monitor.cli --config monitor/configs/stationary.json
```

### Refresh Interval Override

Override the configured refresh interval (in seconds) without editing the config:

```bash
python3 -m monitor.cli -i 1
```

### Headless / Remote Usage

The script works over SSH. Ensure your terminal supports ANSI colors and has
sufficient width (recommended ≥ 100 columns for the 2-column layout).

### Stopping

Press `Ctrl+C` to stop the dashboard cleanly.

## Configuration

### Config File Locations

The script searches for configuration files in this order:

1. Path passed via `--config FILE`
2. `~/.config/adsb-monitor/config.json`
3. `./config.json`

If none are found, a default config is auto-created at
`~/.config/adsb-monitor/config.json` on first run.

### Config Structure

```json
{
  "version": "1.0",
  "layout": {
    "columns": 2,
    "left": ["uptime", "cpu_usage", "memory", "temperatures"],
    "right": ["feeder_services", "network"]
  },
  "options": {
    "interval": 2
  }
}
```

| Field | Type | Description |
|---|---|---|
| `layout.columns` | int | `1` for single-column, `2` for dual-column |
| `layout.left` | string[] | Section names for the left column (or the single column if `columns` is `1`) |
| `layout.right` | string[] | Section names for the right column (ignored when `columns` is `1`) |
| `options.interval` | int | Refresh interval in seconds (can be overridden with `-i`) |
| `options.adsb_stats_db_path` | string | Path to adsb-stats' SQLite database, read by the `adsb_global`/`adsb_health` sections. Default `/var/lib/adsb-stats/stats.db` (matches `adsb_stats`' own default). See Troubleshooting below if those sections can't read it. |
| `options.temp_simple` | bool | If `true`, the `temperatures` section collapses all thermal zones into a single averaged line (with min/max noted alongside) instead of listing every zone. Default `false`. |
| `options.retry_lookback_days` | number | How many days back the `feeder_services` section reports a service's last restart. Restarts older than this aren't shown at all. Default `7`. |
| `options.retry_color_thresholds_days` | `[number, number]` | Optional `[red_cutoff, yellow_cutoff]` in days, both `<= retry_lookback_days`. A restart younger than `red_cutoff` is red, younger than `yellow_cutoff` is yellow, and anything older (up to `retry_lookback_days`) is grey. Default `null`, which splits `retry_lookback_days` into three even bands. |

### Example Configs

The repo ships with two example configs in `monitor/configs/`:

**`stationary.json`** — 2-column layout for the 24/7 stationary box, including
the adsb-stats collector sections (`adsb_global`/`adsb_health`):

```json
{
  "version": "1.0",
  "layout": {
    "columns": 2,
    "left": ["uptime", "cpu_usage", "memory", "temperatures", "network"],
    "right": ["adsb_live", "adsb_global", "adsb_health", "feeder_services"]
  },
  "options": {
    "interval": 5
  }
}
```

**`config.json`** — Second example config; 2-column layout with `fan` and
`cpu_freq`, also including the adsb-stats collector sections:

```json
{
  "version": "1.0",
  "layout": {
    "columns": 2,
    "left": ["uptime", "cpu_usage", "memory", "fan", "temperatures", "cpu_freq"],
    "right": ["adsb_live", "adsb_global", "adsb_health", "feeder_services", "network"]
  },
  "options": {
    "interval": 5
  }
}
```

### Creating a Custom Config

Either edit `~/.config/adsb-monitor/config.json` directly, or create a new
file and pass it with `--config`:

```bash
cp monitor/configs/stationary.json ~/.config/adsb-monitor/config.json
# Edit as needed, then run:
python3 -m monitor.cli
```

## Available Sections

Each section is identified by a string ID in the config's `left`/`right` arrays.
Unknown section IDs are silently skipped.

| Section ID | Description |
|---|---|
| `uptime` | System uptime duration and boot timestamp (from `/proc/uptime`) |
| `cpu_usage` | Overall CPU utilisation percentage and load average (from `/proc/stat` and `/proc/loadavg`) |
| `memory` | RAM and Swap usage with color-coded percentages (from `/proc/meminfo`) |
| `temperatures` | All thermal zone readings with threshold-based color coding (red > 80°C, yellow > 65°C, green otherwise). Set `options.temp_simple` to `true` for a single averaged line instead |
| `cpu_freq` | Per-core clock speeds with big.LITTLE cluster labels (A7 LITTLE cores 0–3, A15 big cores 4–7) |
| `fan` | Fan PWM duty cycle and control mode (from `/sys/class/hwmon/hwmon0/`) |
| `feeder_services` | Status of all five feeder services via `systemctl is-active`, including crash/retry detection via `NRestarts` and age since last restart. Does not include the `adsb-stats` collector service - see `adsb_health` |
| `adsb_live` | Live snapshot of tracked aircraft, position count, and messages/sec (parsed from `dump1090-fa`'s `/run/dump1090-fa/aircraft.json`) |
| `adsb_global` | All-time totals from the `adsb-stats` collector's database: message count, unique aircraft/flights, max altitude/distance (with age) |
| `adsb_health` | `adsb-stats` service status via `systemctl is-active`, data freshness (age of the last processed message), and error count with the most recent error's age and message |
| `network` | WiFi connection state, SSID, signal strength in dBm, connection/disconnection timestamps, and upload throughput (computed from `/sys/class/net/wlan0/statistics/tx_bytes`) |

### Monitored Services

The `feeder_services` section tracks these five systemd units:

1. `dump1090-fa`
2. `piaware`
3. `fr24feed`
4. `adsbexchange-feed`
5. `adsbexchange-mlat`

A retry indicator appears when `NRestarts > 0` and the service's last
restart falls within `options.retry_lookback_days` (default 7 days) - older
restarts aren't shown at all. Color is based purely on how recently the
restart happened, split into three even bands by default (or set
`options.retry_color_thresholds_days` for custom cutoffs):
- **Red**: most recent third of the lookback window
- **Yellow**: middle third
- **Grey**: oldest third (still within the lookback window)

## Troubleshooting

### "Live Aircraft" Section Shows "dump1090-fa not running"

The `adsb_live` section reads `/run/dump1090-fa/aircraft.json`. Ensure:

```bash
systemctl status dump1090-fa
ls -la /run/dump1090-fa/aircraft.json
```

If the file exists but shows stale data, restart `dump1090-fa`:

```bash
sudo systemctl restart dump1090-fa
```

### `adsb_global`/`adsb_health` Show "db not found or unreadable"

These sections open `options.adsb_stats_db_path` (default
`/var/lib/adsb-stats/stats.db`) directly with a read-only SQLite
connection - they don't go through `adsb_stats` at all. The most common
cause is permissions: the database is owned by the dedicated `adsbstats`
system user, and `systemd/install.sh` locks it to `750`/`640` (owner
read/write, group read-only) rather than leaving it world-readable. Add
whichever user runs the monitor to the `adsbstats` group, then log out and
back in:

```bash
sudo usermod -aG adsbstats <username>
```

If that's already done, confirm the path is right (`adsb_stats`'s own
`db_path` config field can differ from this file's default) and that
`adsb-stats` has run at least once to create the database - see
[`adsb_stats/README.md`](../adsb_stats/README.md)'s Troubleshooting
section for more.

### Network Section Shows "interface not found"

The script hardcodes the WiFi interface as `wlan0`. If your interface has a
different name (check with `ip link`), you'll need to modify the `WIFI_IFACE`
constant in `monitor/config.py`.

### Fan Section Shows "fan not detected"

The script reads from `/sys/class/hwmon/hwmon0/pwm1`. If your kernel or
device tree doesn't expose the PWM fan controller at `hwmon0`, the section
will display the fallback message. This is expected on boards without a
PWM-controlled fan or if the cooling device is mapped to a different hwmon index.

### Display Glitches

- Ensure your terminal supports UTF-8 and ANSI escape codes.
- For the 2-column layout, use a terminal width of at least 100 characters.
- If lines wrap or overlap, switch to a 1-column config or widen your terminal.