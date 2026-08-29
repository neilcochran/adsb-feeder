"""Loading and listing saved .sql query files for the `query` CLI command."""

import re
from pathlib import Path
from typing import Optional

QUERIES_DIR = Path(__file__).parent / "sql" / "queries"

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def list_saved_queries() -> list[tuple[str, str]]:
    """
    List saved queries available in QUERIES_DIR.

    Returns:
        (name, description) pairs, sorted by name. name is the filename
        without its .sql extension; description is the file's first line
        with its leading `--` stripped, or "" if the file doesn't start
        with a comment.
    """
    if not QUERIES_DIR.is_dir():
        return []

    return [(path.stem, _read_description(path)) for path in sorted(QUERIES_DIR.glob("*.sql"))]


def _read_description(path: Path) -> str:
    """Return a saved query file's description (its leading `--` comment line), or ''."""
    with open(path) as f:
        first_line = f.readline().strip()
    if first_line.startswith("--"):
        return first_line.lstrip("-").strip()
    return ""


def load_saved_query(name: str) -> Optional[str]:
    """
    Load a saved query's SQL text by name.

    Args:
        name: Query name (filename without .sql extension). Must match
            _NAME_RE - rejecting anything else (path separators, "..", etc.)
            keeps this from ever reading outside QUERIES_DIR.

    Returns:
        The file's full contents (including its leading description
        comment - SQLite skips `--` comments natively, see db.run_query),
        or None if name is invalid or no matching file exists.
    """
    if not _NAME_RE.match(name):
        return None

    path = QUERIES_DIR / f"{name}.sql"
    if not path.is_file():
        return None
    return path.read_text()
