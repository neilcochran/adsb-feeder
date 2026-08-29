"""Low-level sysfs/procfs file reading helpers."""

from __future__ import annotations

from pathlib import Path


def read_sysfs(path: Path) -> str | None:
    """
    Read a sysfs file and return stripped content, or None on failure.

    Args:
        path: Filesystem path to read.

    Returns:
        The file's stripped text content, or None if it couldn't be read.
    """
    try:
        return path.read_text().strip()
    except (OSError, FileNotFoundError, PermissionError):
        return None


def read_int(path: Path) -> int | None:
    """
    Read an integer from a sysfs file.

    Args:
        path: Filesystem path to read.

    Returns:
        The parsed integer, or None if the file couldn't be read or its
        content wasn't a valid integer.
    """
    raw = read_sysfs(path)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None
