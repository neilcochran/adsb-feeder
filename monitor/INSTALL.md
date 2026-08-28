# ADS-B System Monitor — Installation & Configuration Guide

## Overview

`adsb-sys-monitor.py` is a real-time terminal dashboard for Odroid-XU4 / DietPi
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
ls monitor/adsb-sys-monitor.py
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
python3 monitor/adsb-sys-monitor.py
```

### Custom Configuration

Specify a configuration file to change layout, sections, or refresh interval:

```bash
python3 monitor/adsb-sys-monitor.py --config monitor/configs/stationary.json
```

### Refresh Interval Override

Override the configured refresh interval (in seconds) without editing the config:

```bash
python3 monitor/adsb-sys-monitor.py -i 1
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
    "right": ["services", "network"]
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

### Example Configs

The repo ships with two example configs in `monitor/configs/`:

**`stationary.json`** — Full 2-column layout for the 24/7 stationary box:

```json
{
  "version": "1.0",
  "layout": {
    "columns": 2,
    "left": ["uptime", "cpu_usage", "memory", "temperatures", "cpu_freq", "fan"],
    "right": ["services", "adsb", "network"]
  },
  "options": {
    "interval": 2
  }
}
```

**`mobile.json`** — Compact 1-column layout for the powerbank unit:

```json
{
  "version": "1.0",
  "layout": {
    "columns": 1,
    "left": ["uptime", "cpu_usage", "memory", "temperatures", "services", "adsb"],
    "right": []
  },
  "options": {
    "interval": 2
  }
}
```

### Creating a Custom Config

Either edit `~/.config/adsb-monitor/config.json` directly, or create a new
file and pass it with `--config`:

```bash
cp monitor/configs/stationary.json ~/.config/adsb-monitor/config.json
# Edit as needed, then run:
python3 monitor/adsb-sys-monitor.py
```

## Available Sections

Each section is identified by a string ID in the config's `left`/`right` arrays.
Unknown section IDs are silently skipped.

| Section ID | Description |
|---|---|
| `uptime` | System uptime duration and boot timestamp (from `/proc/uptime`) |
| `cpu_usage` | Overall CPU utilisation percentage and load average (from `/proc/stat` and `/proc/loadavg`) |
| `memory` | RAM and Swap usage with color-coded percentages (from `/proc/meminfo`) |
| `temperatures` | All thermal zone readings with threshold-based color coding (red > 80°C, yellow > 65°C, green otherwise) |
| `cpu_freq` | Per-core clock speeds with big.LITTLE cluster labels (A7 LITTLE cores 0–3, A15 big cores 4–7) |
| `fan` | Fan PWM duty cycle and control mode (from `/sys/class/hwmon/hwmon0/`) |
| `services` | Status of all six feeder services via `systemctl is-active`, including crash/retry detection via `NRestarts` and age since last restart |
| `adsb` | Live aircraft count, position count, and cumulative message total (parsed from `dump1090-fa`'s `/run/dump1090-fa/aircraft.json`) |
| `network` | WiFi connection state, SSID, signal strength in dBm, connection/disconnection timestamps, and upload throughput (computed from `/sys/class/net/wlan0/statistics/tx_bytes`) |

### Monitored Services

The `services` section tracks these six systemd units:

1. `dump1090-fa`
2. `piaware`
3. `fr24feed`
4. `adsbexchange-feed`
5. `adsbexchange-mlat`

Retry indicators appear when `NRestarts > 0`, color-coded:
- **Yellow**: 1–4 retries
- **Red**: 5+ retries
- **Cyan**: retries occurred > 24 hours ago (dimmed/historical)

## Troubleshooting

### ADS-B Stats Show "dump1090-fa not running"

The `adsb` section reads `/run/dump1090-fa/aircraft.json`. Ensure:

```bash
systemctl status dump1090-fa
ls -la /run/dump1090-fa/aircraft.json
```

If the file exists but shows stale data, restart `dump1090-fa`:

```bash
sudo systemctl restart dump1090-fa
```

### Network Section Shows "interface not found"

The script hardcodes the WiFi interface as `wlan0`. If your interface has a
different name (check with `ip link`), you'll need to modify the `WIFI_IFACE`
constant near the top of the script.

### Fan Section Shows "fan not detected"

The script reads from `/sys/class/hwmon/hwmon0/pwm1`. If your kernel or
device tree doesn't expose the PWM fan controller at `hwmon0`, the section
will display the fallback message. This is expected on boards without a
PWM-controlled fan or if the cooling device is mapped to a different hwmon index.

### Display Glitches

- Ensure your terminal supports UTF-8 and ANSI escape codes.
- For the 2-column layout, use a terminal width of at least 100 characters.
- If lines wrap or overlap, switch to a 1-column config or widen your terminal.