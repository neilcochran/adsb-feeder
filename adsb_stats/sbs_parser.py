"""Parser for dump1090-fa's SBS-1 (BaseStation) text protocol.

dump1090-fa has already decoded each Mode S/ADS-B message (including CRC
validation and CPR position decoding) before emitting it in this format, so
there is nothing left for us to decode - only to parse the CSV fields we
care about out of each line.

Field layout (comma-separated, 0-indexed), verified against live
dump1090-fa output across transmission types 3, 4, 5, 7, and 8:

    0  message type       always "MSG" for the records dump1090-fa emits
    1  transmission type  1-8, selects which of the fields below are set
    2  session id         unused
    3  aircraft id        unused
    4  hex ident          ICAO address, hex string
    5  flight id          unused
    6  date generated     unused
    7  time generated     unused
    8  date logged        unused
    9  time logged        unused
    10 callsign           set for transmission type 1
    11 altitude           feet, set for types 2/3/5/6/7
    12 ground speed       set for types 2/4
    13 track              set for types 2/4
    14 latitude           set for types 2/3
    15 longitude          set for types 2/3
    16 vertical rate      set for type 4
    17 squawk             set for type 6
    18 alert flag         set for type 6
    19 emergency flag     set for type 6
    20 SPI flag           set for type 6
    21 is-on-ground flag  set for types 2/3/5/6/7

Rather than branch on transmission type, fields are treated as present
whenever they parse to a non-empty value - simpler and more robust than a
type-number lookup table, since SBS already self-describes which fields
apply to a given line via which ones are empty.

Field 17's format and field 19's meaning come from dump1090-fa's own SBS
writer (net_io.c, modesSendSBSOutput - note that its comments number
fields from 1, so what it calls "Field 18" is field 17 here). The squawk
is written as the code's four octal digits, zero-padded to four
characters. The emergency flag is not an independent signal: dump1090-fa
sets it to -1 exactly when that same squawk is 7500, 7600, or 7700, and
to 0 otherwise. That is why only the squawk itself is parsed here - the
flag cannot say anything the squawk value does not already say. Type 6
lines have not yet been observed live from this station's dump1090-fa;
the squawk handling is based on the writer's source rather than captured
output.
"""

import re
from typing import NamedTuple, Optional

FIELD_ICAO = 4
FIELD_CALLSIGN = 10
FIELD_ALTITUDE = 11
FIELD_LAT = 14
FIELD_LON = 15
FIELD_VERTICAL_RATE = 16
FIELD_SQUAWK = 17
MIN_FIELDS = 22

# Physically-plausible bounds for a genuine barometric altitude reading.
# The standard ADS-B altitude encoding (n * 25 - 1000 over an 11-bit field)
# has a hard maximum of 50,175 ft - no validly-encoded Q=1 message can ever
# exceed that, for any aircraft. Unlike MIN_ALTITUDE_FT's floor, this is a
# hard bit-width limit rather than an approximate physical one, so
# MAX_ALTITUDE_FT is set exactly at it rather than padded - anything above
# is provably not a valid decode, most likely a bit error (e.g. in the
# Q-bit itself) that still happened to pass dump1090-fa's CRC check.
# MIN_ALTITUDE_FT matches the format's own natural floor (n=0 gives
# -1000 ft) with a small margin - small negative readings are real
# (aircraft near sea level under low-pressure conditions).
MIN_ALTITUDE_FT = -1500
MAX_ALTITUDE_FT = 50175

# Generous sanity bounds for vertical rate, well beyond even a fighter
# jet's sustained rate - this field isn't tracked as a stat itself, it's
# used by ingest.py to calibrate how much altitude change is plausible
# for a given aircraft, so a wildly out-of-range value here is rejected
# for the same reason a wildly out-of-range altitude is: better to fall
# back to "unknown" than let a corrupted reading skew that calibration.
MIN_VERTICAL_RATE_FPM = -12000
MAX_VERTICAL_RATE_FPM = 12000

# A Mode A squawk is four octal digits, and dump1090-fa always emits it
# zero-padded to four characters. Anything else in the field (a garbled
# decode that still passed CRC, or an unexpected format) is treated as
# absent rather than passed through: a squawk only matters here for
# spotting the emergency codes below, and a mangled one is worthless for
# that.
SQUAWK_RE = re.compile(r"^[0-7]{4}$")

# The three Mode A codes reserved for emergencies: 7500 (unlawful
# interference), 7600 (radio failure), 7700 (general emergency). This is
# the same set dump1090-fa itself tests to set SBS field 19.
EMERGENCY_SQUAWKS = frozenset({"7500", "7600", "7700"})


class SBSMessage(NamedTuple):
    """Fields extracted from one SBS "MSG" line."""
    icao_hex: str                 # lowercase ICAO hex address
    callsign: Optional[str] = None
    altitude_ft: Optional[int] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    vertical_rate_fpm: Optional[int] = None
    squawk: Optional[str] = None  # four octal digits, e.g. "1200"
    is_ident: bool = False
    is_position: bool = False
    is_emergency: bool = False    # squawk is one of EMERGENCY_SQUAWKS


def parse_sbs_line(line: str) -> Optional[SBSMessage]:
    """
    Parse one line of dump1090-fa's SBS output.

    Args:
        line: A single line of text (no trailing newline required).

    Returns:
        SBSMessage, or None if the line isn't a usable MSG record.
    """
    fields = line.split(",")
    if len(fields) < MIN_FIELDS or fields[0] != "MSG":
        return None

    # dump1090-fa prefixes a non-ICAO address (TIS-B, ADS-R, ground vehicle,
    # anonymous address - see readsb's MODES_NON_ICAO_ADDRESS) with "~" in
    # this field; stripped for the same reason aircraft_json.py's
    # index_by_hex strips it - both need to key on the same bare hex string
    # for _confirm_via_aircraft_json's cross-reference to ever find a match.
    icao_hex = fields[FIELD_ICAO].strip().lstrip("~").lower()
    if not icao_hex:
        return None

    callsign = fields[FIELD_CALLSIGN].strip() or None

    altitude_ft = None
    if fields[FIELD_ALTITUDE].strip():
        try:
            parsed_altitude = int(float(fields[FIELD_ALTITUDE]))
            if MIN_ALTITUDE_FT <= parsed_altitude <= MAX_ALTITUDE_FT:
                altitude_ft = parsed_altitude
        except ValueError:
            pass

    lat = lon = None
    if fields[FIELD_LAT].strip() and fields[FIELD_LON].strip():
        try:
            parsed_lat = float(fields[FIELD_LAT])
            parsed_lon = float(fields[FIELD_LON])
            if -90.0 <= parsed_lat <= 90.0 and -180.0 <= parsed_lon <= 180.0:
                lat, lon = parsed_lat, parsed_lon
        except ValueError:
            pass

    vertical_rate_fpm = None
    if fields[FIELD_VERTICAL_RATE].strip():
        try:
            parsed_rate = int(float(fields[FIELD_VERTICAL_RATE]))
            if MIN_VERTICAL_RATE_FPM <= parsed_rate <= MAX_VERTICAL_RATE_FPM:
                vertical_rate_fpm = parsed_rate
        except ValueError:
            pass

    squawk = None
    raw_squawk = fields[FIELD_SQUAWK].strip()
    if SQUAWK_RE.match(raw_squawk):
        squawk = raw_squawk

    return SBSMessage(
        icao_hex=icao_hex,
        callsign=callsign,
        altitude_ft=altitude_ft,
        lat=lat,
        lon=lon,
        vertical_rate_fpm=vertical_rate_fpm,
        squawk=squawk,
        is_ident=callsign is not None,
        is_position=lat is not None,
        is_emergency=squawk in EMERGENCY_SQUAWKS,
    )
