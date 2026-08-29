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
"""

from typing import NamedTuple, Optional

FIELD_ICAO = 4
FIELD_CALLSIGN = 10
FIELD_ALTITUDE = 11
FIELD_LAT = 14
FIELD_LON = 15
MIN_FIELDS = 22


class SBSMessage(NamedTuple):
    """Fields extracted from one SBS "MSG" line."""
    icao_hex: str                 # lowercase ICAO hex address
    callsign: Optional[str] = None
    altitude_ft: Optional[int] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    is_ident: bool = False
    is_position: bool = False


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

    icao_hex = fields[FIELD_ICAO].strip().lower()
    if not icao_hex:
        return None

    callsign = fields[FIELD_CALLSIGN].strip() or None

    altitude_ft = None
    if fields[FIELD_ALTITUDE].strip():
        try:
            altitude_ft = int(float(fields[FIELD_ALTITUDE]))
        except ValueError:
            pass

    lat = lon = None
    if fields[FIELD_LAT].strip() and fields[FIELD_LON].strip():
        try:
            lat = float(fields[FIELD_LAT])
            lon = float(fields[FIELD_LON])
        except ValueError:
            lat = lon = None

    return SBSMessage(
        icao_hex=icao_hex,
        callsign=callsign,
        altitude_ft=altitude_ft,
        lat=lat,
        lon=lon,
        is_ident=callsign is not None,
        is_position=lat is not None,
    )
