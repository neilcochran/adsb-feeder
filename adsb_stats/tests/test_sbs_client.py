#!/usr/bin/env python3
"""
Manual test harness for the SBS client/parser - connects to dump1090-fa's
SBS stream, parses messages, and reports basic stats plus a light spot
check against aircraft.json.

This is a sanity check on our parsing, not a decode-correctness test: SBS
values already come from dump1090-fa's own fully-decoded output (including
position), so there is no independent decoding on our end left to validate
the way there was with the old Beast/CPR approach.

Usage:
    cd ~/adsb-feeder
    python3 adsb_stats/tests/test_sbs_client.py [--seconds 30] [--host 127.0.0.1]
        [--port 30003] [--aircraft-json /run/dump1090-fa/aircraft.json]

Press Ctrl+C to stop early.
"""

import argparse
import signal
import sys
import time

sys.path.insert(0, ".")

from adsb_stats.sbs_client import SBSClient
from adsb_stats.sbs_parser import parse_sbs_line
from adsb_stats.aircraft_json import load_aircraft_json, index_by_hex


def main():
    parser = argparse.ArgumentParser(description="Test SBS client/parser against dump1090-fa")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=30003)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--aircraft-json", default="/run/dump1090-fa/aircraft.json")
    args = parser.parse_args()

    client = SBSClient(args.host, args.port)

    total_lines = 0
    parsed = 0
    ident_count = 0
    position_count = 0
    altitude_only_count = 0
    seen_icaos = set()
    last_position = {}  # icao_hex -> (lat, lon, altitude_ft)

    stop = False

    def handle_sigint(sig, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_sigint)

    print(f"Connecting to {args.host}:{args.port} ...")
    try:
        client.connect()
    except (ConnectionError, OSError) as exc:
        print(f"Connection failed: {exc}")
        print("Is dump1090-fa running with SBS output enabled? Check: systemctl status dump1090-fa")
        sys.exit(1)

    print(f"Connected! Collecting for {args.seconds}s (Ctrl+C to stop early)...\n")

    stream = client.get_message_stream()
    deadline = time.time() + args.seconds
    last_report = 0

    try:
        for line in stream:
            if stop or time.time() > deadline:
                break

            total_lines += 1
            msg = parse_sbs_line(line)
            if msg is not None:
                parsed += 1
                seen_icaos.add(msg.icao_hex)
                if msg.is_ident:
                    ident_count += 1
                if msg.is_position:
                    position_count += 1
                    last_position[msg.icao_hex] = (msg.lat, msg.lon, msg.altitude_ft)
                elif msg.altitude_ft is not None:
                    altitude_only_count += 1

            now = time.time()
            if now - last_report >= 5:
                last_report = now
                print(f"  ... {total_lines} lines, {parsed} parsed, "
                      f"{len(seen_icaos)} ICAOs, {position_count} position msgs")

    except Exception as exc:
        print(f"\nError during stream: {exc}")
        import traceback
        traceback.print_exc()
    finally:
        client.disconnect()

    print(f"\n{'=' * 60}")
    print("SBS Client/Parser Test Report")
    print(f"{'=' * 60}")
    print(f"  Total lines:              {total_lines:,}")
    print(f"  Parsed as MSG records:    {parsed:,}")
    print(f"  Identification messages:  {ident_count:,}")
    print(f"  Position messages:        {position_count:,}")
    print(f"  Altitude-only messages:   {altitude_only_count:,}")
    print(f"  Unique ICAOs seen:        {len(seen_icaos)}")

    if not last_position:
        print(f"{'=' * 60}")
        return

    print(f"\n  Spot-checking {len(last_position)} last-known positions against "
          f"{args.aircraft_json} (sanity check only - SBS values already come "
          f"from dump1090-fa's own decode, so these should match closely) ...\n")
    reference = index_by_hex(load_aircraft_json(args.aircraft_json))

    for icao_hex, (lat, lon, alt) in last_position.items():
        ref = reference.get(icao_hex)
        if ref is None or "lat" not in ref:
            continue
        ref_alt = ref.get("alt_baro", ref.get("altitude"))
        print(f"    {icao_hex}  ours=({lat:.5f},{lon:.5f}) alt={alt}  "
              f"aircraft.json=({ref['lat']:.5f},{ref['lon']:.5f}) alt={ref_alt}")

    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
