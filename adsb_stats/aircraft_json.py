"""Reader for dump1090-fa's aircraft.json output.

Used for two independent things: pulling dump1090-fa's own cumulative
message counter (see ingest.py's global msg_total tracking), and, in the
test harness, spot-checking our SBS-parsed positions against dump1090-fa's
own aircraft list.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def load_aircraft_json(path: str) -> Optional[dict]:
    """
    Load and parse dump1090-fa's aircraft.json.

    Args:
        path: Filesystem path to aircraft.json.

    Returns:
        The parsed JSON dict (with at least "messages" and "aircraft" keys
        on a healthy dump1090-fa), or None if the file couldn't be read or
        parsed.
    """
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read aircraft.json at %s: %s", path, exc)
        return None


def get_message_count(data: Optional[dict]) -> Optional[int]:
    """
    Extract dump1090-fa's cumulative message counter from parsed aircraft.json.

    This counter resets to 0 whenever dump1090-fa itself restarts - callers
    that persist it across polls must detect and handle a decrease.

    Args:
        data: Return value of load_aircraft_json (may be None).

    Returns:
        The counter as an int, or None if unavailable.
    """
    if data is None:
        return None
    return data.get("messages")


def index_by_hex(data: Optional[dict]) -> dict[str, dict]:
    """
    Index aircraft.json's aircraft list by lowercase ICAO hex.

    Args:
        data: Return value of load_aircraft_json (may be None).

    Returns:
        dict mapping lowercase ICAO hex -> that aircraft's JSON object.
        Empty dict if data is None or has no aircraft list.
    """
    if data is None:
        return {}
    return {ac.get("hex", "").lstrip("~").lower(): ac
            for ac in data.get("aircraft", []) if ac.get("hex")}
