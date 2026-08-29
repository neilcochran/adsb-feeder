# ADS-B Receiver Station — Odroid-XU4 / DietPi

A multi-network ADS-B receiving station built on an Odroid-XU4 running DietPi
(Debian 13 Trixie). A single FlightAware RTL-SDR dongle feeds decoded ADS-B
data to three networks simultaneously:

- **FlightAware** (via PiAware)
- **Flightradar24** (via fr24feed)
- **ADS-B Exchange** (via adsbexchange-feed)

All three feeders share a single dump1090-fa decoder instance. Each feeder
reads from dump1090-fa's output ports — no additional SDR hardware is needed.

## What's in this repo

| Path | What it is |
|---|---|
| [`docs/station-setup.md`](docs/station-setup.md) | Full station build/config runbook: hardware, service topology, from-source install steps for dump1090-fa/piaware/fr24feed/ADS-B Exchange, MLAT policy, and day-to-day useful commands |
| [`adsb_stats/`](adsb_stats/) | Statistics collector — a background service that maintains aggregate ADS-B statistics (message counts, unique aircraft/flights, altitude/reception-distance records) in a local SQLite database. See [`adsb_stats/README.md`](adsb_stats/README.md). |
| [`monitor/`](monitor/) | Real-time terminal dashboard for the station — uptime, CPU/memory/temperature, feeder service status, and live aircraft stats. See [`monitor/README.md`](monitor/README.md). |
| [`systemd/`](systemd/) | The `adsb-stats.service` unit this repo owns, its deployment script, and the full service topology (including the vendor services this repo doesn't own). See [`systemd/README.md`](systemd/README.md). |
| [`bin/`](bin/) | On-device convenience scripts, installed onto `PATH` by `systemd/install.sh`. |

To build a station like this one from scratch, start with
[`docs/station-setup.md`](docs/station-setup.md).
