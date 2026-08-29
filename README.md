# ADS-B Receiver Station — Odroid-XU4 / DietPi

A multi-network ADS-B receiving station built on an Odroid-XU4 running DietPi
(Debian 13 Trixie). A single FlightAware RTL-SDR dongle feeds decoded ADS-B
data to three networks simultaneously:

- **FlightAware** (via PiAware)
- **Flightradar24** (via fr24feed)
- **ADS-B Exchange** (via adsbexchange-feed)

All three feeders share a single dump1090-fa decoder instance. Each feeder
reads from dump1090-fa's output ports — no additional SDR hardware is needed.

## Station Identification

These public identifiers are specific to your registered feed station. Your values
will differ from this example.

| Network | Identifier | Location |
|---|---|---|
| FlightAware Site Number | `280723` | Public on FlightAware feed map |
| Flightradar24 Radar Code | `T-KPWM29` | Public on FR24 maps |
| ADS-B Exchange Feed UUID | `5c42d0c7-8f54-4266-848d-5fd61e1f35af` | Public on ADSBX sync map |

## Hardware

| Component | Details |
|---|---|
| Board | Odroid-XU4 (armhf, 32-bit) |
| OS | DietPi (Debian 13 Trixie) |
| SDR Dongle | FlightAware Pro Stick (RTL2832U/R820T2) |
| Antenna | FlightAware 1090 MHz ADS-B antenna |
| Network | WiFi |

## Architecture

```
RTL-SDR Dongle → dump1090-fa (port 30005 Beast, 30002 AVR, 8080 web)
                       |
        +--------------+------------------+
        |              |                  |
   piaware        fr24feed         adsbexchange-*
   (FA)           (FR24)            (ADS-B Exchange)
```

### Port Map

| Port | Protocol | Used By |
|---|---|---|
| 30002 | AVR (raw) | fr24feed |
| 30003 | SBS (BaseStation) | adsb-stats |
| 30005 | Beast | piaware, adsbexchange-feed |
| 8080 | HTTP | dump1090-fa web map (SkyAware) |
| 8754 | HTTP | fr24feed web UI |

## Services

All services are enabled at boot via `systemctl enable`.

### FlightAware

| Service | Purpose |
|---|---|
| `dump1090-fa` | Decodes ADS-B messages from RTL-SDR dongle |
| `piaware` | Feeds decoded data to FlightAware via TLS |
| `generate-pirehose-cert` | Generates TLS certificate for FA connection |

### Flightradar24

| Service | Purpose |
|---|---|
| `fr24feed` | Feeds decoded data to Flightradar24 |

### ADS-B Exchange

| Service | Purpose |
|---|---|
| `adsbexchange-feed` | Feeds decoded data to ADS-B Exchange |
| `adsbexchange-mlat` | MLAT client for ADS-B Exchange |

### ADS-B Statistics Collector

| Service | Purpose |
|---|---|
| `adsb-stats` | Collects aggregate ADS-B statistics from dump1090-fa (this repo's own tooling, not a feeder) |

### Check All Services

```bash
systemctl status dump1090-fa piaware generate-pirehose-cert fr24feed adsbexchange-feed adsbexchange-mlat adsb-stats
```

### Check If Enabled At Boot

```bash
systemctl is-enabled dump1090-fa piaware generate-pirehose-cert fr24feed adsbexchange-feed adsbexchange-mlat adsb-stats
```

## MLAT Configuration

MLAT (multilateration) is enabled for FlightAware and disabled for
Flightradar24 and ADS-B Exchange. This is intentional:

- **FlightAware**: MLAT enabled — FA accepts MLAT data from multi-feeders
- **Flightradar24**: MLAT disabled — FR24 requires MLAT off when feeding
  multiple networks (ToS requirement)
- **ADS-B Exchange**: MLAT disabled — recommended for multi-network setups

Do not enable MLAT for fr24feed. Doing so risks account suspension per
FR24's contributor agreement.

## Configuration Files

### dump1090-fa

**File:** `/etc/default/dump1090-fa`

Key settings:

- `RECEIVER_OPTIONS="--device-type rtlsdr --gain 40"`
- Gain can be adjusted. Use `--gain -10` for auto-gain.

### PiAware

**File:** `/etc/piaware.conf` (also managed via `piaware-config`)

Key settings:

- Latitude/longitude configured via `piaware-config`
- MLAT enabled by default (do not disable)

### fr24feed

**File:** `/etc/fr24feed.ini`

```ini
receiver="avr-tcp"
fr24key="YOUR_SHARING_KEY"
host="127.0.0.1:30002"
bs="no"
raw="no"
mlat="no"
mlat-without-gps="no"
```

### ADS-B Exchange

**Files:**

- `/usr/local/share/adsbexchange/` — installation directory
- `/usr/local/share/adsbexchange/git/` — git repo for updates
- `/usr/local/share/adsbexchange/venv/` — Python venv for mlat-client

Feed UUID stored at: `/usr/local/share/adsbexchange/uuid`

## Setup Guide

This station was built from source. The Odroid-XU4 runs DietPi
(Debian Trixie, armhf). All components were compiled or installed from
source/repositories.

### Step 1: Blacklist RTL-SDR DVB Driver

The kernel's DVB-TV driver claims the RTL-SDR dongle by default. This
must be blacklisted before dump1090-fa can access the device.

**File:** `/etc/modprobe.d/blacklist-rtlsdr.conf`

```
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
```

After creating the file, reboot.

### Step 2: Install Build Dependencies

```bash
sudo apt update
sudo apt install -y \
    git build-essential debhelper \
    libusb-1.0-0-dev libncurses-dev \
    pkg-config librtlsdr-dev \
    lighttpd adduser lsb-release
```

> **Note:** `libncurses5-dev` does not exist on Trixie — use `libncurses-dev`.
> `dh-systemd` is deprecated and merged into `debhelper` — omit it entirely.

### Step 3: Build and Install dump1090-fa

```bash
cd ~
git clone https://github.com/flightaware/dump1090.git
cd dump1090
./prepare-build.sh $(lsb_release -cs)
cd package-$(lsb_release -cs)
dpkg-buildpackage -b --no-sign
cd ..
sudo dpkg -i dump1090-fa_*.deb
sudo systemctl enable --now dump1090-fa
```

> **Note:** If `dpkg-buildpackage` complains about missing optional SDR
> frontend libraries (`libbladerf-dev`, `libhackrf-dev`, `liblimesuite-dev`,
> `libsoapysdr-dev`), install them if available or bypass with the `-d` flag:
> `dpkg-buildpackage -b --no-sign -d`

### Step 4: Install tcl-tls (PiAware Dependency)

```bash
sudo apt install -y tcl-tls
```

### Step 5: Build and Install PiAware

PiAware must be built using the `piaware_builder` repository (not the
standalone `piaware` repo). The builder assembles all components into a
single Debian package.

```bash
cd ~
git clone https://github.com/flightaware/piaware_builder.git
cd piaware_builder
./sensible-build.sh $(lsb_release -cs)
cd package-$(lsb_release -cs)
dpkg-buildpackage -b --no-sign
cd ..
sudo dpkg -i piaware_*.deb
```

Installing this package also installs and enables `generate-pirehose-cert`
automatically - it's set up as a systemd dependency (`Wants=`) of
`piaware.service`, so it runs on its own to generate PiAware's TLS
certificate. No separate install or manual invocation needed.

> **Note:** If there are runtime dependency issues with `rsyslog`, remove
> piaware, install deps separately, then reinstall:
>
> ```bash
> sudo dpkg -r piaware
> sudo apt install -y net-tools tclx8.4 tcllib itcl3 rsyslog
> sudo dpkg -i piaware_*.deb
> ```
>
> If `rsyslog` will not install, force-configure piaware:
>
> ```bash
> sudo dpkg --force-depends --configure piaware
> ```

### Step 6: Claim FlightAware Station

1. Visit https://flightaware.com/adsb/piaware/claim
2. Click claim on your station
3. Configure coordinates via web UI (gear icon on stats page) or:
   ```bash
   sudo piaware-config latitude <LAT>
   sudo piaware-config longitude <LON>
   sudo systemctl restart piaware
   ```
4. Stats appear within ~30 minutes at https://flightaware.com/adsb/stats/

### Step 7: Install and Configure fr24feed (Flightradar24)

```bash
wget -qO- https://repo-feed.flightradar24.com/rpi/feeds/installer.sh | sudo sh -s
```

The install script auto-detects dump1090-fa and autoconfigures fr24feed
to use AVR-tcp on port 30002. Config file at `/etc/fr24feed.ini`.

If manual signup is needed:

```bash
sudo fr24feed --signup
```

Answer: MLAT = No, MLAT-without-GPS = No, receiver = existing dump1090.

> **Alternative APT repo method:**
>
> ```bash
> sudo bash -c 'echo "deb [trusted=yes] https://repo-feed.flightradar24.com/flightradar24 raspberrypi-stable main" > /etc/apt/sources.list.d/fr24.list'
> sudo apt update
> sudo apt install -y fr24feed
> ```

### Step 8: Install ADS-B Exchange Feeder

```bash
curl -L -o /tmp/axfeed.sh https://adsbexchange.com/feed.sh
sudo bash /tmp/axfeed.sh
```

The script clones the feed client, creates a Python venv, builds
mlat-client and readsb-based feed client, generates a UUID, and enables
the `adsbexchange-feed` and `adsbexchange-mlat` services.

> **Note:** Python 3.13 (Trixie) removed `asyncore` from the standard
> library (PEP 594). The `pyasyncore` package provides a drop-in
> replacement. If errors recur:
>
> ```bash
> sudo /usr/local/share/adsbexchange/venv/bin/pip install pyasyncore
> ```

Verify feed status:

- https://adsbexchange.com/myip/
- https://map.adsbexchange.com/sync/

## Useful Commands

### FlightAware

```bash
# Status
sudo piaware-status

# Logs
sudo journalctl -u piaware -f
sudo journalctl -u dump1090-fa -f

# Configuration
piaware-config -showall
sudo piaware-config latitude 
sudo piaware-config longitude 

# Restart
sudo systemctl restart dump1090-fa piaware
```

### Flightradar24

```bash
# Status
fr24feed-status

# Logs
tail -30 /var/log/fr24feed.log

# Configuration
cat /etc/fr24feed.ini

# Restart
sudo systemctl restart fr24feed
```

### ADS-B Exchange

```bash
# Status
systemctl status adsbexchange-feed adsbexchange-mlat

# Logs
sudo journalctl -u adsbexchange-feed -n 50
sudo journalctl -u adsbexchange-mlat -n 50

# Restart
sudo systemctl restart adsbexchange-feed adsbexchange-mlat
```

### ADS-B Statistics Collector

```bash
# Status (numbers, not systemd state - see below for that)
cd /opt/adsb-feeder
sudo -u adsbstats python3 -m adsb_stats.cli status --config /etc/adsb-stats/config.json

# Logs
sudo journalctl -u adsb-stats -f

# Configuration
cat /etc/adsb-stats/config.json

# Restart
sudo systemctl restart adsb-stats
```

See [`adsb_stats/README.md`](adsb_stats/README.md) for full setup, configuration, and troubleshooting.

### All Services

```bash
# Status
systemctl status dump1090-fa piaware generate-pirehose-cert fr24feed adsbexchange-feed adsbexchange-mlat adsb-stats

# Restart everything
sudo systemctl restart dump1090-fa piaware fr24feed adsbexchange-feed adsbexchange-mlat adsb-stats

# Verify dongle is visible
lsusb

# Verify DVB driver is NOT loaded
lsmod | grep dvb_usb
```

## Web Interfaces

| Interface | URL |
|---|---|
| dump1090-fa SkyAware map | `http://<XU4-IP>:8080/` |
| fr24feed web UI | `http://<XU4-IP>:8754/` |
| FlightAware stats | https://flightaware.com/adsb/stats/ |
| Flightradar24 data sharing | https://www.flightradar24.com/account/data-sharing |
| ADS-B Exchange myIP | https://adsbexchange.com/myip/ |
| ADS-B Exchange sync map | https://map.adsbexchange.com/sync/ |

## Statistics Collector

This repo includes `adsb_stats/`, a background collector that connects to
dump1090-fa's SBS output and maintains aggregate statistics - message
counts, unique aircraft/flights, and altitude/reception-distance records -
in a local SQLite database, deployed as its own systemd service
(`adsb-stats`).

For installation, configuration, usage, and troubleshooting, see
[`adsb_stats/README.md`](adsb_stats/README.md).

## System Monitor

This repo includes `monitor/adsb-sys-monitor.py`, a real-time terminal
dashboard for monitoring the feeder station. It displays system uptime,
CPU usage and frequencies, temperatures, fan speed, memory, feeder service
status with crash/retry detection, ADS-B aircraft tracking stats, and WiFi
connectivity with upload throughput.

For installation, configuration, usage, and troubleshooting, see
[`monitor/README.md`](monitor/README.md).

## Useful Links

- [FlightAware PiAware](https://github.com/flightaware/piaware)
- [FlightAware dump1090](https://github.com/flightaware/dump1090)
- [FlightAware piaware_builder](https://github.com/flightaware/piaware_builder)
- [Flightradar24 share data](https://www.flightradar24.com/share-your-data)
- [ADS-B Exchange feed](https://adsbexchange.com/feed.sh)
