# Station Setup - Odroid-XU4 / DietPi

Full build, configuration, and operations runbook for the physical station.
See [../README.md](../README.md) for a repo overview.

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
RTL-SDR Dongle -> dump1090-fa (port 30005 Beast, 30002 AVR, 8080 web)
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
| 30005 | Beast | piaware, adsbexchange-feed, adsbscope |
| 8080 | HTTP | dump1090-fa web map (SkyAware) |
| 8090 | HTTP | adsbscope live traffic view |
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
| `adsbexchange-stats` | Reports feed statistics to adsbexchange.com |

### ADS-B Statistics Collector

| Service | Purpose |
|---|---|
| `adsb-stats` | Collects aggregate ADS-B statistics from dump1090-fa (this repo's own tooling, not a feeder) |

### Live Traffic View

| Service | Purpose |
|---|---|
| `adsbscope` | Serves a browser view of the aircraft dump1090-fa is tracking (separate npm package; this repo owns only its unit file) |

### Check All Services

```bash
systemctl status dump1090-fa piaware generate-pirehose-cert fr24feed adsbexchange-feed adsbexchange-mlat adsbexchange-stats adsb-stats adsbscope
```

### Check If Enabled At Boot

```bash
systemctl is-enabled dump1090-fa piaware generate-pirehose-cert fr24feed adsbexchange-feed adsbexchange-mlat adsbexchange-stats adsb-stats adsbscope
```

## MLAT Configuration

MLAT (multilateration) is enabled for FlightAware and ADS-B Exchange and
disabled for Flightradar24. This is intentional:

- **FlightAware**: MLAT enabled - FA accepts MLAT data from multi-feeders
- **ADS-B Exchange**: MLAT enabled - ADSBX accepts MLAT data from
  multi-feeders
- **Flightradar24**: MLAT disabled - FR24 requires MLAT off when feeding
  multiple networks (ToS requirement)

Do not enable MLAT for fr24feed. Doing so risks account suspension per
FR24's contributor agreement.

## Configuration Files

### dump1090-fa

**File:** `/etc/default/dump1090-fa`

Two config formats exist, distinguished by the `CONFIG_STYLE` value at the
bottom of the file. Check which one the device has before editing.

**Older style (no `CONFIG_STYLE` line)** - a single options string:

- `RECEIVER_OPTIONS="--device-type rtlsdr --gain 40"`
- Gain can be adjusted. Use `--gain -10` for auto-gain.

**`CONFIG_STYLE=6` (dump1090-fa 11.x)** - discrete variables, no options
string:

- `RECEIVER=rtlsdr` selects the SDR type; `RECEIVER_GAIN` sets gain in dB.
  A value above the tuner's ceiling (58.6 dB on the R820T) clamps to
  maximum.
- `ADAPTIVE_DYNAMIC_RANGE=yes` enables adaptive gain control, which tracks
  a target dynamic range rather than holding gain fixed. This is the
  preferred setting for the mobile unit, which sees a different RF
  environment on every use. Startup logs confirm it with `adaptive: enabled
  adaptive gain control with gain limits ...`.
- `RECEIVER_LAT` / `RECEIVER_LON` set the receiver position. On the
  stationary unit leave them empty: piaware receives the site position from
  FlightAware and passes it to dump1090-fa. On a receive-only unit with no
  piaware, this file is the only place to set it. Left empty with no piaware,
  distance and bearing are unavailable, positions decode via global CPR
  only, and `MAX_RANGE` has no effect - the range check is gated on a valid
  receiver position.

### PiAware

**File:** `/etc/piaware.conf` (also managed via `piaware-config`)

Key settings:

- `feeder-id` identifies the site; set it to reuse an existing site (see
  Step 5)
- Receiver position is set on the FlightAware stats page, not here
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

- `/etc/default/adsbexchange` - feed configuration (station name,
  position, MLAT)
- `/usr/local/share/adsbexchange/adsbx-uuid` - feed UUID
- `/usr/local/share/adsbexchange/git/` - git repo for updates
- `/usr/local/share/adsbexchange/venv/` - Python venv for mlat-client

## Setup Guide

This station was built from source. The Odroid-XU4 runs DietPi
(Debian Trixie, armhf). All components were compiled or installed from
source/repositories.

### Step 1: Install DietPi

Download the Odroid-XU4 image from https://dietpi.com/#download, flash it
to the SD card with any imaging tool, and boot. The first-run setup walks
through network and password configuration. The rest of this guide assumes
an SSH session as the default `dietpi` user.

### Step 2: Blacklist the DVB Driver and Install Dependencies

The kernel's DVB-T driver claims the RTL-SDR dongle by default. Blacklist
it so dump1090-fa can access the device.

**File:** `/etc/modprobe.d/blacklist-rtlsdr.conf`

```
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
blacklist rtl2832_sdr
```

Install everything the builds and this repo's own tooling need:

```bash
sudo apt update
sudo apt install -y \
    git build-essential debhelper pkg-config lsb-release \
    libusb-1.0-0-dev libncurses-dev librtlsdr-dev \
    libbladerf-dev libhackrf-dev liblimesuite-dev libsoapysdr-dev \
    lighttpd adduser \
    tcl8.6-dev python3-dev python3-venv python3-setuptools python3-wheel \
    python3-build python3-pip python3-filelock python3-pyasyncore \
    libz-dev libboost-system-dev libboost-program-options-dev \
    libboost-regex-dev libboost-filesystem-dev patchelf \
    rsync wireless-tools
```

The first four lines cover dump1090-fa, the next three cover piaware, and
the last line covers this repo (`rsync` for `systemd/install.sh`,
`wireless-tools` for the monitor's SSID lookup).

> **Note:** `libncurses5-dev` does not exist on Trixie - use `libncurses-dev`.
> `dh-systemd` is deprecated and merged into `debhelper` - omit it entirely.

Reboot so the dongle enumerates with the DVB driver blacklisted and
librtlsdr's udev rules already in place:

```bash
sudo reboot
```

Then clone this repo:

```bash
git clone https://github.com/neilcochran/adsb-feeder.git ~/adsb-feeder
```

### Step 3: Build and Install dump1090-fa

```bash
cd ~
git clone https://github.com/flightaware/dump1090.git
cd dump1090
./prepare-build.sh $(lsb_release -cs)
cd package-$(lsb_release -cs)
dpkg-buildpackage -b --no-sign
cd ..
sudo apt install -y ./dump1090-fa_*.deb
sudo systemctl enable --now dump1090-fa
```

The package ships with SBS (30003), AVR (30002), and Beast (30005) output
enabled, which is everything the feeders and the statistics collector
need. Confirm aircraft appear on the SkyAware map at `http://<XU4-IP>:8080/`.

> **Note:** The four SDR frontend libraries (`libbladerf-dev`,
> `libhackrf-dev`, `liblimesuite-dev`, `libsoapysdr-dev`) are declared build
> dependencies, not optional ones - `dpkg-buildpackage` aborts without them.
> They are included in Step 2. The `-d` flag skips the dependency check but
> does not change what gets compiled, so it only moves the failure later.

> **Note:** If the service fails on startup with `rtlsdr: error querying
> device #0: Permission denied`, udev never applied its rules to the dongle.
> The rules at `/lib/udev/rules.d/60-librtlsdr0.rules` set the device node to
> group `plugdev`, but udev applies rules only on a device add event - so a
> dongle that enumerated before librtlsdr was installed keeps default
> `root:root` permissions. Step 2's reboot avoids this; if it was skipped,
> replay the rules:
>
> ```bash
> sudo udevadm control --reload-rules
> sudo udevadm trigger --attr-match=idVendor=0bda --action=add
> sudo systemctl restart dump1090-fa
> ```
>
> The symptom is misleading: the SkyAware map on port 8080 loads normally,
> because lighttpd serves it independently of the decoder, but it never
> shows aircraft.

### Step 4: Build and Install PiAware

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
sudo apt install -y ./piaware_*.deb
```

Installing through `apt` rather than `dpkg -i` pulls in the package's
runtime dependencies (`rsyslog`, `tcl-tls`, `tclx8.4`, and others) in the
same step.

Installing this package also installs and enables `generate-pirehose-cert`
automatically - it's set up as a systemd dependency (`Wants=`) of
`piaware.service`, so it runs on its own to generate PiAware's TLS
certificate. No separate install or manual invocation needed.

### Step 5: Claim or Reattach the FlightAware Station

**New station:**

1. Visit https://flightaware.com/adsb/piaware/claim
2. Click claim on your station
3. Set the receiver position via the gear icon on the stats page
4. Stats appear within ~30 minutes at https://flightaware.com/adsb/stats/

**Existing station (rebuild):** do not claim the new receiver that piaware
generated. Set the existing site's feeder ID (shown on its stats page) and
restart:

```bash
sudo piaware-config feeder-id <FEEDER_ID>
sudo systemctl restart piaware
```

In both cases piaware receives the site position from FlightAware and
passes it to dump1090-fa, which only reads it at startup. Once piaware
reports connected, restart the decoder so range checks and distance
display use the position:

```bash
sudo systemctl restart dump1090-fa
```

### Step 6: Install fr24feed (Flightradar24)

```bash
sudo bash -c "$(wget -O - https://fr24.com/install.sh)"
```

The installer adds Flightradar24's APT repository, installs `fr24feed`,
and runs a signup wizard. It asks for an email address, an existing
sharing key (enter it to reattach an existing station, or leave it blank
for a new one), whether to enable MLAT (no - see MLAT Configuration), and
whether to autoconfigure against the running dump1090-fa (yes). The wizard
does not start the service:

```bash
sudo systemctl enable --now fr24feed
fr24feed-status
```

### Step 7: Install the ADS-B Exchange Feeder

To reattach an existing feed, seed its UUID first. The installer reuses a
valid UUID at this path and only generates a new one when the file is
absent:

```bash
sudo mkdir -p /usr/local/share/adsbexchange
echo "<FEED_UUID>" | sudo tee /usr/local/share/adsbexchange/adsbx-uuid
```

Then run the installer:

```bash
curl -L -o /tmp/axfeed.sh https://adsbexchange.com/feed.sh
sudo bash /tmp/axfeed.sh
```

It asks whether to enable MLAT (yes), a station name, and the receiver
latitude and longitude, then builds the feed client and mlat-client and
enables the `adsbexchange-feed` and `adsbexchange-mlat` services. Import
errors for `setuptools` and `asyncore` in the middle of the output are the
installer probing for them before installing both into its venv.

For feed statistics on adsbexchange.com, install the stats package the
same way. It reuses the UUID above and prints your feed's stats URL:

```bash
curl -L -o /tmp/axstats.sh https://adsbexchange.com/stats.sh
sudo bash /tmp/axstats.sh
```

Verify feed status:

- https://adsbexchange.com/myip/
- https://map.adsbexchange.com/sync/

### Step 8: Install the Statistics Collector and Monitor

```bash
cd ~/adsb-feeder
sudo bash systemd/install.sh
```

Set `receiver_lat` and `receiver_lon` in `/etc/adsb-stats/config.json`,
then start the service and grant your user read access to its database
so the monitor can show collector statistics:

```bash
sudo systemctl start adsb-stats
sudo usermod -aG adsbstats dietpi
```

Log out and back in for the group change to apply, then run the monitor:

```bash
cd ~/adsb-feeder
python3 -m monitor.cli --config monitor/configs/stationary.json
```

See [`../adsb_stats/README.md`](../adsb_stats/README.md) and
[`../monitor/README.md`](../monitor/README.md) for configuration and
troubleshooting.

### Step 9: Install adsbtop

[`@squawk/adsbtop`](https://www.npmjs.com/package/@squawk/adsbtop) is a
terminal dashboard of the aircraft dump1090-fa is currently tracking. It
needs Node.js 22 or newer. Debian's `nodejs` package is too old and
`dietpi-software` picks whichever release has the highest version number
rather than an LTS one, so install a pinned version with the
[nodejs-linux-installer](https://github.com/MichaIng/nodejs-linux-installer)
script instead. Node 22 is the last line with official armv7l builds.

```bash
cd ~
wget -qO node-install.sh https://raw.githubusercontent.com/MichaIng/nodejs-linux-installer/master/node-install.sh
sudo bash node-install.sh -v v22.23.2
sudo npm install -g @squawk/adsbtop
```

Its defaults connect to dump1090-fa's SBS output on this machine, so no
flags are needed:

```bash
adsbtop
```

### Step 10: Install adsbscope

[`@squawk/adsbscope`](https://www.npmjs.com/package/@squawk/adsbscope)
serves the same traffic as a map in the browser instead of the terminal. It
reads dump1090-fa's Beast output and needs the Node.js installed in Step 9.

```bash
sudo npm install -g @squawk/adsbscope
```

Binding to `0.0.0.0` makes the view reachable from the LAN rather than only
from the board itself:

```bash
adsbscope --host localhost --bind 0.0.0.0 --lat <latitude> --lon <longitude>
```

Anyone who can reach that address can see the traffic and the receiver
position, so only bind beyond loopback on a network you control.

That command dies with the shell that started it. To keep the view running
across logouts and reboots, install `adsbscope.service` instead - see
[`../systemd/README.md`](../systemd/README.md), which also covers the
dedicated service user and the environment file holding the receiver
position.

### Optional: Continuous Clock Sync with chrony

Only do this if the clock is actually drifting. DietPi's default time sync
(`CONFIG_NTP_MODE=2` in `/boot/dietpi.txt`) runs `systemd-timesyncd` once
at boot and once a day and leaves the clock free-running in between. Most
boards keep good enough time for that to be fine. The catch is that the
brief daily run can leave a large frequency correction behind in the
kernel, up to the kernel's 500 ppm limit, with nothing left running to
revise it. The clock then runs at that rate for the next 24 hours,
accumulating tens of seconds of error, and the next daily sync applies
all of it as one large step.

The usual symptom is piaware being killed by its systemd watchdog shortly
after the daily sync: piaware pings the watchdog every 96 seconds on a
wall-clock timer while systemd enforces its 120 second limit on the
monotonic clock, so a backward step of more than about 24 seconds delays
the ping past the limit. The journal shows `Watchdog timeout (limit
2min)!` followed by a scheduled restart. mlat-client reporting `clock
unstable` is another sign.

Confirm before changing anything:

```bash
grep CONFIG_NTP_MODE /boot/dietpi.txt
sudo journalctl -u systemd-timesyncd --no-pager | tail -20
sudo journalctl -u piaware --since "-2d" --no-pager | grep -iE "clock|reconnect|restart"
```

A step shows up in the timesyncd log as a `Contacted time server` line
whose timestamp is out of order with the `Started` line just before it.
If those corrections line up with the piaware events, replace timesyncd
with chrony, which stays running and slews the clock continuously
instead of stepping it once a day. Switch DietPi's time sync mode to
custom (0) first so its boot and daily jobs stop expecting
`systemd-timesyncd`, then install chrony. Installing chrony removes
`systemd-timesyncd`; the two packages conflict because both provide
`time-daemon`.

```bash
sudo /boot/dietpi/func/dietpi-set_software ntpd-mode 0
sudo apt install -y chrony
```

Debian's default `/etc/chrony/chrony.conf` needs no changes. Verify it is
tracking a source:

```bash
chronyc tracking
chronyc sources
```

The `Frequency` line in the first run of `chronyc tracking`, before
chrony has synchronised, is the correction it inherited from the kernel.
A value at or near 500 ppm confirms the problem above. Within a few
minutes it is replaced by chrony's own estimate of the board's real
drift, typically a few ppm, and `Leap status` reads `Normal`.

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
systemctl status adsbexchange-feed adsbexchange-mlat adsbexchange-stats

# Logs
sudo journalctl -u adsbexchange-feed -n 50
sudo journalctl -u adsbexchange-mlat -n 50

# Configuration
cat /etc/default/adsbexchange

# Restart
sudo systemctl restart adsbexchange-feed adsbexchange-mlat adsbexchange-stats
```

### ADS-B Statistics Collector

```bash
# Status (numbers, not systemd state - see below for that)
adsb-stats status

# Logs
sudo journalctl -u adsb-stats -f

# Configuration
cat /etc/adsb-stats/config.json

# Restart
sudo systemctl restart adsb-stats
```

`adsb-stats` is a wrapper installed onto `PATH` by `systemd/install.sh` -
see [`../bin/adsb-stats`](../bin/adsb-stats). See
[`../adsb_stats/README.md`](../adsb_stats/README.md) for full setup,
configuration, and troubleshooting.

### All Services

```bash
# Status
systemctl status dump1090-fa piaware generate-pirehose-cert fr24feed adsbexchange-feed adsbexchange-mlat adsbexchange-stats adsb-stats adsbscope

# Restart everything
sudo systemctl restart dump1090-fa piaware fr24feed adsbexchange-feed adsbexchange-mlat adsbexchange-stats adsb-stats adsbscope

# Verify dongle is visible
lsusb

# Verify DVB driver is NOT loaded
lsmod | grep dvb_usb
```

## Web Interfaces

| Interface | URL |
|---|---|
| dump1090-fa SkyAware map | `http://<XU4-IP>:8080/` |
| adsbscope live traffic view | `http://<XU4-IP>:8090/` |
| fr24feed web UI | `http://<XU4-IP>:8754/` |
| FlightAware stats | https://flightaware.com/adsb/stats/ |
| Flightradar24 data sharing | https://www.flightradar24.com/account/data-sharing |
| ADS-B Exchange myIP | https://adsbexchange.com/myip/ |
| ADS-B Exchange sync map | https://map.adsbexchange.com/sync/ |

## Useful Links

- [FlightAware PiAware](https://github.com/flightaware/piaware)
- [FlightAware dump1090](https://github.com/flightaware/dump1090)
- [FlightAware piaware_builder](https://github.com/flightaware/piaware_builder)
- [Flightradar24 share data](https://www.flightradar24.com/share-your-data)
- [ADS-B Exchange feed](https://adsbexchange.com/feed.sh)
