"""Unit tests for ``xa.store``."""

from __future__ import annotations

from pathlib import Path

import pytest

from xa import store as st


def test_jsonlines_append_and_iter(tmp_path: Path) -> None:
    s = st.JsonLinesStore(tmp_path / "log.jsonl")
    assert list(s) == []
    s.append({"a": 1})
    s.append({"b": 2})
    assert list(s) == [{"a": 1}, {"b": 2}]
    assert len(s) == 2


def test_jsonlines_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    path.write_text('{"ok": 1}\n' + "not json\n" + '{"ok": 2}\n')
    s = st.JsonLinesStore(path)
    assert list(s) == [{"ok": 1}, {"ok": 2}]


def test_filestore_roundtrip(tmp_path: Path) -> None:
    fs = st.FileStore(tmp_path, suffix=".log")
    assert "abc" not in fs
    fs["abc"] = b"hello"
    assert "abc" in fs
    assert fs["abc"] == b"hello"
    assert fs.size("abc") == 5


def test_filestore_rejects_traversal(tmp_path: Path) -> None:
    fs = st.FileStore(tmp_path)
    with pytest.raises(KeyError):
        fs["../escape"] = b"nope"
    with pytest.raises(KeyError):
        _ = fs["../escape"]
    # A valid-looking but missing key raises KeyError on read.
    with pytest.raises(KeyError):
        _ = fs["valid_but_missing"]


def test_filestore_path_for_inside_root(tmp_path: Path) -> None:
    fs = st.FileStore(tmp_path, suffix=".log")
    p = fs.path_for("abc")
    assert p == tmp_path / "abc.log"
