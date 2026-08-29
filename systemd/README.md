# Systemd Service Topology

This folder contains the systemd units and documentation for the ADS-B feeder
station. The full service chain on a fully-configured stationary unit is:

```
                network-online.target
                        |
                    dump1090-fa
                (decoder, user: dump1090)
                       / | \
                      /  |  \
                     /   |   \
                 piaware |  fr24feed
                  (FA)   |    (FR24)
                         |
                    adsbexchange-feed
                         |    (ADSBX)
                         |
                    adsbexchange-mlat
                        /
                       /
                  adsb-stats
           (our service, user: adsbstats)
```

All services are enabled at boot via `systemctl enable`.

## Service Reference

| Service | User | Restart Policy | Source |
|---|---|---|---|
| `dump1090-fa` | `dump1090` | on-failure / 30s | [flightaware/dump1090](https://github.com/flightaware/dump1090) |
| `piaware` | `piaware` | on-failure / 30s, watchdog 120s | [flightaware/piaware_builder](https://github.com/flightaware/piaware_builder) |
| `fr24feed` | `fr24`/`fr24` | always | [FR24 installer](https://repo-feed.flightradar24.com/rpi/feeds/installer.sh) |
| `adsbexchange-feed` | `adsbexchange` | always / 30s | [ADSBX feed.sh](https://adsbexchange.com/feed.sh) |
| `adsbexchange-mlat` | `adsbexchange` | always / 30s | [ADSBX feed.sh](https://adsbexchange.com/feed.sh) |
| `generate-pirehose-cert` | `root` | oneshot, RemainAfterExit | shipped with piaware package |
| **`adsb-stats`** | `adsbstats` | on-failure / 10s | **this repo** |

## Upstream vs. Owned

- **Upstream** (installed by package scripts, do not edit):
  `dump1090-fa`, `piaware`, `fr24feed`, `adsbexchange-feed`, `adsbexchange-mlat`,
  `generate-pirehose-cert`.

- **Owned by this repo**:
  `adsb-stats.service` — install via `install.sh`.

## Dependency Chain

```
network-online.target
    └── dump1090-fa
            ├── piaware           (After=dump1090-fa)
            ├── fr24feed          (After=network-online.target; reads AVR port 30002)
            ├── adsbexchange-feed  (After=network.target; reads Beast port 30005)
            ├── adsbexchange-mlat  (After=network.target; reads Beast port 30005)
            └── adsb-stats        (After=dump1090-fa; reads SBS port 30003)
```

`adsb-stats` uses `Wants=dump1090-fa.service` (not `Requires=`) so that a
transient decoder restart doesn't cascade into a stats restart. The ingest
loop has its own reconnection logic with exponential backoff.

## Verifying the Full Set

```bash
# Quick status check
systemctl status dump1090-fa piaware fr24feed \
    adsbexchange-feed adsbexchange-mlat adsb-stats

# Confirm all are enabled at boot
systemctl is-enabled dump1090-fa piaware fr24feed \
    adsbexchange-feed adsbexchange-mlat adsb-stats
```