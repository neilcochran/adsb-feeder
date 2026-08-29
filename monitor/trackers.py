"""Stateful trackers carried across dashboard refresh iterations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import TX_FILE, WIFI_IFACE
from .sysfs import read_int


@dataclass
class WifiState:
    """Tracks WiFi connection state across loop iterations."""

    iface: str = WIFI_IFACE
    last_state: str = ""
    down_since: str = ""
    up_since: str = ""


@dataclass
class UploadTracker:
    """Tracks cumulative TX bytes to compute upload rate between samples."""

    tx_file: Path = TX_FILE
    prev_tx: int | None = None

    def sample(self, interval: int) -> tuple[str, str]:
        """
        Sample TX byte counter and compute upload rate.

        Args:
            interval: Seconds elapsed since the previous sample, used to
                convert the byte delta into a rate.

        Returns:
            (rate_string, total_string) where rate is in KB/s and total is
            cumulative MB transmitted.
        """
        curr_tx = read_int(self.tx_file)
        if curr_tx is None:
            return "(tx stats unavailable)", ""

        total_mb = curr_tx / 1_048_576

        if self.prev_tx is not None and curr_tx >= self.prev_tx:
            delta = curr_tx - self.prev_tx
            kb_per_sec = delta / 1024 / interval
            rate_str = f"{kb_per_sec:.1f} KB/s"
        elif self.prev_tx is None:
            rate_str = "collecting..."
        else:
            rate_str = "0.0 KB/s"

        self.prev_tx = curr_tx
        return rate_str, f"{total_mb:.1f} MB"


@dataclass
class MessageRateTracker:
    """Tracks dump1090-fa's cumulative message counter to compute a rate."""

    prev_count: int | None = None

    def sample(self, curr_count: int, interval: int) -> str:
        """
        Compute a messages/sec rate from dump1090-fa's cumulative counter.

        Args:
            curr_count: Current value of aircraft.json's "messages" field.
            interval: Seconds elapsed since the previous sample, used to
                convert the count delta into a rate.

        Returns:
            Rate formatted as "N.N msg/s", "collecting..." on the first
            sample, or "0.0 msg/s" if the counter decreased (dump1090-fa
            restarted, so the previous count is no longer meaningful).
        """
        if self.prev_count is not None and curr_count >= self.prev_count:
            delta = curr_count - self.prev_count
            rate_str = f"{delta / interval:.1f} msg/s"
        elif self.prev_count is None:
            rate_str = "collecting..."
        else:
            rate_str = "0.0 msg/s"

        self.prev_count = curr_count
        return rate_str
