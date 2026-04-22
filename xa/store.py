"""Minimal key-value and append-log storage.

This is deliberately thin and stdlib-only — enough to back ``xa.archive``'s
event log and per-session pane captures without pulling in ``dol``. Two
types:

- ``JsonLinesStore`` — append-only JSONL. Iterable, multi-writer safe on
  POSIX (one line == one atomic ``write``), no random access.
- ``FileStore`` — dict-like view over a directory of flat files. Keys
  must match a strict allowlist regex so callers can't escape ``root``.

Later phases may swap these for ``dol`` equivalents without changing
the archive API.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterator, Optional


DEFAULT_STATE_DIR = Path(os.environ.get("XA_STATE_DIR") or Path.home() / ".xa")


_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


# --------------------------------------------------------------------------- #
# JsonLinesStore
# --------------------------------------------------------------------------- #


class JsonLinesStore:
    """Append-only JSONL file with an ``Iterable`` reader.

    >>> import tempfile, pathlib
    >>> with tempfile.TemporaryDirectory() as td:
    ...     s = JsonLinesStore(pathlib.Path(td) / 'log.jsonl')
    ...     s.append({'a': 1})
    ...     s.append({'b': 2})
    ...     list(s) == [{'a': 1}, {'b': 2}]
    True
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, event: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")

    def __iter__(self) -> Iterator[dict]:
        if not self.path.exists():
            return
        with self.path.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def __len__(self) -> int:
        return sum(1 for _ in self)


# --------------------------------------------------------------------------- #
# FileStore
# --------------------------------------------------------------------------- #


class FileStore:
    """Directory of files, accessed by key.

    Values are ``bytes``. Keys must match ``[A-Za-z0-9_.-]+`` — this
    rejects path traversal attempts like ``../etc/passwd``.
    """

    def __init__(self, root: Path, *, suffix: str = "") -> None:
        self.root = Path(root)
        self.suffix = suffix

    def _path_for(self, key: str) -> Path:
        if not _KEY_RE.match(key):
            raise KeyError(f"invalid key: {key!r}")
        return self.root / f"{key}{self.suffix}"

    def path_for(self, key: str) -> Path:
        """Public path accessor (for callers that need to hand the path to tmux)."""
        return self._path_for(key)

    def __contains__(self, key: str) -> bool:
        try:
            return self._path_for(key).is_file()
        except KeyError:
            return False

    def __getitem__(self, key: str) -> bytes:
        path = self._path_for(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise KeyError(key)

    def __setitem__(self, key: str, value: bytes) -> None:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)

    def size(self, key: str) -> int:
        try:
            return self._path_for(key).stat().st_size
        except (KeyError, FileNotFoundError):
            return 0

    def mtime(self, key: str) -> Optional[float]:
        try:
            return self._path_for(key).stat().st_mtime
        except (KeyError, FileNotFoundError, OSError):
            return None


# --------------------------------------------------------------------------- #
# conveniences
# --------------------------------------------------------------------------- #


def default_events_store(state_dir: Path = DEFAULT_STATE_DIR) -> JsonLinesStore:
    """The event log at ``<state_dir>/events.jsonl``."""
    return JsonLinesStore(state_dir / "events.jsonl")


def default_pane_store(state_dir: Path = DEFAULT_STATE_DIR) -> FileStore:
    """Per-session pane logs at ``<state_dir>/panes/<id>.log``."""
    return FileStore(state_dir / "panes", suffix=".log")
