"""SBS-1 (BaseStation) protocol TCP client for dump1090-fa connection.

Connects to dump1090-fa's SBS output stream (default port 30003) and yields
raw text lines. dump1090-fa has already fully decoded each message before
emitting it in this format (including CPR position decoding), so there is
no CRC/bit-level Mode S decoding to do on our end - see sbs_parser.py for
turning a line into a structured record.

Each line is a comma-separated BaseStation "MSG" record terminated by a
newline, e.g.:
    MSG,3,1,1,A1B2C3,1,2024/01/01,00:00:00.000,2024/01/01,00:00:00.000,,35000,,,42.1234,-70.5678,,,,,,0
"""

import socket
import time
import logging
from collections.abc import Iterator
from typing import Optional

logger = logging.getLogger(__name__)


class SBSClient:
    """TCP client for dump1090-fa's SBS/BaseStation output with automatic reconnection."""

    def __init__(self, host: str = "127.0.0.1", port: int = 30003) -> None:
        self.host = host
        self.port = port
        self.sock: Optional[socket.socket] = None
        self._buf = b""
        self._backoff = 5
        self._max_backoff = 60

    # -- Connection management ----------------------------------

    def connect(self) -> None:
        """Establish TCP connection and reset state."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(10.0)
        self.sock.connect((self.host, self.port))
        logger.info("Connected to SBS stream at %s:%d", self.host, self.port)
        self._backoff = 5
        self._buf = b""

    def disconnect(self) -> None:
        """Close TCP connection and clear buffer."""
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        self._buf = b""
        logger.info("Disconnected from SBS stream")

    def is_connected(self) -> bool:
        """Return True if socket is open."""
        return self.sock is not None

    def reconnect(self) -> bool:
        """Attempt reconnection with exponential backoff. Returns True on success."""
        self.disconnect()
        logger.warning("Connection lost, retrying in %ds...", self._backoff)
        time.sleep(self._backoff)
        self._backoff = min(self._backoff * 2, self._max_backoff)
        try:
            self.connect()
            return True
        except (socket.error, OSError) as exc:
            logger.error("Reconnection failed: %s", exc)
            return False

    # -- Line buffering -------------------------------------------

    def _recv_more(self) -> None:
        """
        Read more data from socket into internal buffer.
        Blocks until data arrives or socket timeout fires.
        """
        chunk = self.sock.recv(4096)
        if not chunk:
            raise ConnectionError("Connection closed by peer")
        self._buf += chunk

    def _next_line(self) -> Optional[str]:
        """
        Extract one complete line from the internal buffer.

        Returns:
            Decoded line (str, without trailing CR/LF) on success,
            None if a full line isn't buffered yet.
        """
        newline_pos = self._buf.find(b"\n")
        if newline_pos == -1:
            return None
        line = self._buf[:newline_pos]
        self._buf = self._buf[newline_pos + 1:]
        return line.rstrip(b"\r").decode("ascii", errors="replace")

    # -- Public stream interface -----------------------------------

    def get_message_stream(self) -> Iterator[str]:
        """
        Generator yielding decoded text lines from the SBS stream.

        Handles reconnection automatically. The generator runs indefinitely
        until the caller stops iterating (e.g. via KeyboardInterrupt). Blank
        lines are skipped rather than yielded.
        """
        while True:
            if not self.is_connected():
                while not self.reconnect():
                    pass  # Keep retrying with backoff

            try:
                while True:
                    line = self._next_line()
                    if line is not None:
                        if line:
                            yield line
                        continue
                    # Not enough data buffered - block until more arrives
                    self._recv_more()

            except socket.timeout:
                continue

            except (ConnectionError, OSError) as exc:
                logger.warning("Stream error: %s", exc)
                self.disconnect()
