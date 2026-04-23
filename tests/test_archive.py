"""Unit tests for ``xa.archive``."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from xa import archive as arch
from xa import claude_fs as cfs
from xa import store as st
from xa import tmux as tm


# --------------------------------------------------------------------------- #
# classify_death
# --------------------------------------------------------------------------- #


def _forensics(
    *,
    user_interrupted: bool = False,
    exit_code: Optional[int] = None,
) -> cfs.TranscriptForensics:
    return cfs.TranscriptForensics(
        transcript_path=None,
        line_count=0,
        last_tool_name="Bash",
        last_tool_command="ls",
        last_tool_exit_code=exit_code,
        last_tool_result_tail=None,
        final_assistant_text=None,
        user_interrupted=user_interrupted,
    )


def test_classify_replaced_wins() -> None:
    assert arch.classify_death("abrupt", replaced=True) == "replaced"


def test_classify_missing() -> None:
    assert arch.classify_death("missing") == "missing"


def test_classify_interrupted_requires_clean_exit() -> None:
    assert (
        arch.classify_death("clean_exit", forensics=_forensics(user_interrupted=True))
        == "interrupted"
    )
    # Abrupt + interrupt marker → still abrupt (marker alone is ambiguous).
    assert (
        arch.classify_death("abrupt", forensics=_forensics(user_interrupted=True))
        == "abrupt"
    )


def test_classify_tool_crash() -> None:
    assert (
        arch.classify_death("clean_exit", forensics=_forensics(exit_code=1))
        == "tool_crash"
    )


def test_classify_clean_exit_default() -> None:
    assert arch.classify_death("clean_exit", forensics=_forensics(exit_code=0)) == "clean_exit"
    assert arch.classify_death("clean_exit", forensics=None) == "clean_exit"


def test_classify_abrupt_default() -> None:
    assert arch.classify_death("abrupt") == "abrupt"


def test_classify_oom_killed_promoted() -> None:
    """Exit 137 + OOM marker → oom_killed regardless of pane_kind."""
    f = _forensics(exit_code=137)
    assert (
        arch.classify_death("abrupt", forensics=f, oom_markers=("Killed",))
        == "oom_killed"
    )
    assert (
        arch.classify_death("clean_exit", forensics=f, oom_markers=("Out of memory",))
        == "oom_killed"
    )
    # Without the pane marker we can't promote — fall back to tool_crash.
    assert (
        arch.classify_death("clean_exit", forensics=f, oom_markers=())
        == "tool_crash"
    )
    # Without exit 137 we don't promote — pane "Killed" alone is noisy
    # (a tool that prints "Killed" in normal output would otherwise misfire).
    assert (
        arch.classify_death(
            "abrupt", forensics=_forensics(exit_code=1), oom_markers=("Killed",),
        )
        == "abrupt"
    )


def test_synthesize_diagnosis_oom_mentions_swap() -> None:
    """The hint should be actionable, not just descriptive."""
    f = _forensics(exit_code=137)
    hint = arch.synthesize_diagnosis(
        state="archived",
        reason="oom_killed",
        forensics=f,
        oom_markers=("Killed",),
    )
    assert "OOM" in hint or "swap" in hint.lower()


def test_synthesize_diagnosis_live_says_so() -> None:
    hint = arch.synthesize_diagnosis(state="live")
    assert "live" in hint.lower()


def test_reconcile_records_oom_signals(tmp_path: Path) -> None:
    """Pane log containing 'Killed' should land oom_signals in the gone forensics."""
    events = st.JsonLinesStore(tmp_path / "events.jsonl")
    panes = st.FileStore(tmp_path / "panes", suffix=".log")
    arch.append_created(
        events, id="oomid", name="sess", cwd="/tmp", claude_bin="claude",
        tmux_created_ts=1000,
    )
    panes["oomid"] = b"Killed\n"  # OOM marker in pane tail
    arch.reconcile(events, panes, live_sessions=[])
    # Find the gone event and check its forensics.
    gone = next(ev for ev in events if ev.get("event") == "gone")
    assert gone["forensics"]["oom_signals"] == ["Killed"]


# --------------------------------------------------------------------------- #
# reconcile
# --------------------------------------------------------------------------- #


def test_reconcile_emits_gone_for_missing_session(tmp_path: Path) -> None:
    events = st.JsonLinesStore(tmp_path / "events.jsonl")
    panes = st.FileStore(tmp_path / "panes", suffix=".log")
    arch.append_created(
        events,
        id="abc",
        name="sess",
        cwd="/tmp",
        claude_bin="claude",
        tmux_created_ts=1000,
    )
    # Simulate a clean-exit pane log.
    panes["abc"] = b"... Resume this session with: blah\n"

    emitted = arch.reconcile(events, panes, live_sessions=[])
    assert len(emitted) == 1
    assert emitted[0]["reason"] == "clean_exit"
    # Idempotence: second call emits nothing.
    assert arch.reconcile(events, panes, live_sessions=[]) == []


def test_reconcile_detects_replaced(tmp_path: Path) -> None:
    events = st.JsonLinesStore(tmp_path / "events.jsonl")
    panes = st.FileStore(tmp_path / "panes", suffix=".log")
    arch.append_created(
        events, id="a", name="sess", cwd="/tmp", claude_bin="claude",
        tmux_created_ts=1000,
    )
    # Same name, but tmux-created 9999 — reconcile should see this as replaced.
    live = [tm.TmuxSession(name="sess", created=9999, activity=9999, attached=False)]
    emitted = arch.reconcile(events, panes, live_sessions=live)
    assert emitted[0]["reason"] == "replaced"


def test_reconcile_keeps_live_session_alive(tmp_path: Path) -> None:
    events = st.JsonLinesStore(tmp_path / "events.jsonl")
    panes = st.FileStore(tmp_path / "panes", suffix=".log")
    arch.append_created(
        events, id="a", name="sess", cwd="/tmp", claude_bin="claude",
        tmux_created_ts=1000,
    )
    live = [tm.TmuxSession(name="sess", created=1000, activity=1000, attached=False)]
    emitted = arch.reconcile(events, panes, live_sessions=live)
    assert emitted == []


def test_reconcile_missing_pane(tmp_path: Path) -> None:
    events = st.JsonLinesStore(tmp_path / "events.jsonl")
    panes = st.FileStore(tmp_path / "panes", suffix=".log")
    arch.append_created(
        events, id="no-log", name="sess", cwd="/tmp", claude_bin="claude",
        tmux_created_ts=1000,
    )
    emitted = arch.reconcile(events, panes, live_sessions=[])
    assert emitted[0]["reason"] == "missing"


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #


def test_records_reduces_events(tmp_path: Path) -> None:
    events = st.JsonLinesStore(tmp_path / "events.jsonl")
    panes = st.FileStore(tmp_path / "panes", suffix=".log")
    arch.append_created(
        events, id="a", name="sessA", cwd="/x", claude_bin="claude",
    )
    arch.append_url_acquired(
        events, id="a", name="sessA",
        url="https://claude.ai/code/session_aaa",
        claude_session_id="aaaaaaaa-1111-2222-3333-444444444444",
    )
    arch.append_created(
        events, id="b", name="sessB", cwd="/y", claude_bin="claude",
    )
    panes["a"] = b"hello"

    recs = arch.records(events, panes)
    assert len(recs) == 2
    by_id = {r.id: r for r in recs}
    assert by_id["a"].url == "https://claude.ai/code/session_aaa"
    assert by_id["a"].claude_session_id.startswith("aaaaaaaa")
    assert by_id["a"].pane_log_bytes == 5
    assert by_id["b"].pane_log_bytes == 0
    assert all(r.gone is None for r in recs)


def test_records_picks_up_gone_events(tmp_path: Path) -> None:
    events = st.JsonLinesStore(tmp_path / "events.jsonl")
    panes = st.FileStore(tmp_path / "panes", suffix=".log")
    arch.append_created(events, id="z", name="s", cwd="/x", claude_bin="claude")
    arch.append_gone(
        events, id="z", name="s", reason="clean_exit", death_ts=12345.0
    )
    recs = arch.records(events, panes)
    assert recs[0].gone == 12345.0
    assert recs[0].gone_reason == "clean_exit"
