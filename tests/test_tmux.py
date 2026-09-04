"""Integration tests for ``xa.tmux`` — require a real tmux binary.

Auto-skip if tmux isn't on PATH. Each test uses a uniquely-named session
prefixed with ``xa-test-`` and cleans up in a ``finally`` block so existing
tmux sessions on the host aren't disturbed.
"""

from __future__ import annotations

import shutil
import time
import uuid

import pytest

from xa import tmux as tm


pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="tmux not installed"
)


def _fresh_name() -> str:
    return f"xa-test-{uuid.uuid4().hex[:8]}"


def test_session_target() -> None:
    assert tm.session_target("foo") == "foo:"


def test_list_new_capture_kill_roundtrip() -> None:
    name = _fresh_name()
    assert name not in {s.name for s in tm.list_sessions()}
    tm.new_session(name, command="sh -c 'echo hello-xa; sleep 60'")
    try:
        time.sleep(0.3)  # let the echo hit the pane
        names = {s.name for s in tm.list_sessions()}
        assert name in names
        pane = tm.capture_pane(name, lines=50)
        assert "hello-xa" in pane
        pid = tm.pane_pid(name)
        assert isinstance(pid, int) and pid > 0
    finally:
        try:
            tm.kill_session(name)
        except RuntimeError:
            pass
    assert name not in {s.name for s in tm.list_sessions()}


def test_kill_session_raises_for_missing() -> None:
    with pytest.raises(RuntimeError):
        tm.kill_session(_fresh_name())


def test_descendants_walks_a_forked_child() -> None:
    """``descendants`` finds a genuinely forked grandchild of the pane.

    ``sleep 30 & wait`` forces the shell to fork rather than exec, so the
    pane's root really is ``sh`` and the sleep really is a descendant on
    every platform. (Plain ``sh -c 'sleep 30'`` does not test this: the
    shell exec's itself into ``sleep``, so the pane root *is* the sleep
    and the correct answer is an empty descendant list.)
    """
    name = _fresh_name()
    tm.new_session(name, command="sh -c 'sleep 30 & wait'")
    try:
        time.sleep(0.5)
        root = tm.pane_pid(name)
        assert root is not None
        kids = tm.descendants(root)
        comms = {tm.proc_comm(p) for p in kids}
        assert "sleep" in comms, f"expected 'sleep' in {comms}"
    finally:
        try:
            tm.kill_session(name)
        except RuntimeError:
            pass


def test_process_tree_contains_command_however_the_shell_runs_it() -> None:
    """The contract callers actually rely on: ``(root, *descendants(root))``.

    :func:`xa.claude_cli.find_claude_pid` searches the root *plus* its
    descendants precisely because whether the shell exec's or forks is
    not something xa gets to control — it differs by platform and by
    shell. This asserts the union, which is invariant to that choice.
    """
    name = _fresh_name()
    tm.new_session(name, command="sh -c 'sleep 30'")
    try:
        time.sleep(0.5)
        root = tm.pane_pid(name)
        assert root is not None
        tree = (root, *tm.descendants(root))
        comms = {tm.proc_comm(p) for p in tree}
        assert "sleep" in comms, f"expected 'sleep' in {comms}"
    finally:
        try:
            tm.kill_session(name)
        except RuntimeError:
            pass


def test_missing_tmux_binary_degrades_instead_of_raising() -> None:
    """A host with no tmux must yield empty reads, not a FileNotFoundError.

    ``capture_pane`` documents "'' on failure" and the listings document
    an empty list; before this was handled in ``_run`` those promises held
    for a non-zero exit but not for an absent binary, so a misconfigured
    ``tmux_bin`` crashed a whole listing.
    """
    missing = "/nonexistent/tmux"
    assert tm.list_sessions(binary=missing) == []
    assert tm.list_panes(binary=missing) == []
    assert tm.capture_pane("whatever", binary=missing) == ""
    # Callers that raise still do — but name the operation, not the plumbing.
    with pytest.raises(RuntimeError, match="send-keys"):
        tm.send_keys("whatever", "hi", "Enter", binary=missing)


def test_pipe_pane_writes_file(tmp_path) -> None:
    name = _fresh_name()
    log = tmp_path / "pane.log"
    tm.new_session(name, command="sh -c 'echo captured-output; sleep 30'")
    try:
        time.sleep(0.2)
        tm.pipe_pane_to_file(name, path=log)
        # Trigger more output to force a flush.
        tm.send_keys(name, "echo more-output", "Enter")
        time.sleep(0.5)
        assert log.exists()
        # Either the initial banner or the triggered echo should land; on
        # some tmux versions pipe-pane starts capturing only after it's
        # attached, so we accept either marker.
        content = log.read_text(errors="replace")
        assert "more-output" in content or "captured-output" in content
    finally:
        try:
            tm.kill_session(name)
        except RuntimeError:
            pass
