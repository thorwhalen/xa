"""Unit tests for ``xa.revive`` — no tmux server, no claude, no network.

The autouse ``no_tmux`` fixture makes that structural rather than a
promise: every tmux call in this package funnels through ``xa.tmux._run``,
and here it raises. A test that reaches a real tmux fails loudly instead of
passing on the author's machine and hanging on someone else's.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xa import revive as rv
from xa import store as st
from xa import tmux as tm


@pytest.fixture(autouse=True)
def no_tmux(monkeypatch):
    def _boom(*args, **kwargs):  # pragma: no cover - only runs on a bug
        raise AssertionError(f"test shelled out to tmux: {args!r}")

    monkeypatch.setattr(tm, "_run", _boom)


#: A live, remote-controlled pane. Tests that care about a *dropped* one say
#: so explicitly with ``bridge_session_id=None``, because that — plus the
#: missing footer pill — is what "dropped" now means.
def ref(target="w:@1.%1", **kw):
    return rv.PaneRef(
        target=target, **{"claude_pid": 42, "bridge_session_id": "s_1", **kw}
    )


def probe(text, **kw):
    return rv.Probe(text=text.lower(), ref=ref(**kw))


#: An empty TUI input line — what the typing gate requires before it will
#: send anything. Appended by :func:`panes_from` so each test says only what
#: it is actually about.
CLEAR_PROMPT = "\n\u276f "


def panes_from(tails, refs=None, **kw):
    """A SessionPanes over ``{target: tail}``, touching nothing real."""
    refs = refs or [ref(target=t, bridge_session_id="s_1") for t in tails]
    return rv.SessionPanes(
        panes=lambda: refs,
        is_relevant=lambda r: True,
        tail=lambda r: tails[r.target] + CLEAR_PROMPT,
        **kw,
    )


# --------------------------------------------------------------------------- #
# classify
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Remote Control disconnected", rv.RECONNECTABLE),
        ("Remote Control not started here", rv.RECONNECTABLE),
        ("Run /remote-control to retry", rv.RECONNECTABLE),
        ("/remote-control is no longer active. Run /remote-control", rv.RECONNECTABLE),
        ("/rc failed", rv.RECONNECTABLE),
        ("Remote control active", rv.CONNECTED),
        ("/rc reconnecting", rv.RECONNECTING),
        ("this session was ended or archived from another device", rv.HELD_ELSEWHERE),
        ("this connection is no longer the active worker for the session", rv.HELD_ELSEWHERE),
        ("another Claude Code already has Remote Control for this conversation", rv.HELD_ELSEWHERE),
        ("Please log in", rv.NEEDS_HUMAN),
        ("Do you trust the files in this folder?", rv.NEEDS_HUMAN),
        ("Do you want to proceed?", rv.NEEDS_HUMAN),
        ("API Error: Request was aborted.", rv.API_STALLED),
        ("Re-run `claude remote-control` to try again", rv.SERVER_MODE),
        ("ordinary conversation text", rv.UNKNOWN),  # bridge id present
    ],
)
def test_classify_each_verdict(text, expected):
    assert rv.classify(probe(text)) == expected


def test_no_claude_wins_over_every_text_signal():
    """A dead session's scrollback must never read as live state.

    The real pane this comes from still holds a permission dialog and a
    disconnect notice above a shell prompt — both true once, neither true now.
    """
    dead = rv.Probe(
        text="remote control disconnected\ndo you want to proceed?\nkilled".lower(),
        ref=rv.PaneRef(target="w:@1.%1", claude_pid=None),
    )
    assert rv.classify(dead) == rv.NO_CLAUDE


def test_held_elsewhere_beats_reconnectable():
    """FIXED: reviving a taken-over session steals it back from the phone."""
    text = "Remote Control disconnected\nended or archived from another device"
    assert rv.classify(probe(text)) == rv.HELD_ELSEWHERE


def test_needs_human_beats_reconnectable():
    """Keystrokes sent at an open dialog are answers, not commands."""
    text = "/rc failed\n Do you want to proceed?\n 1. Yes\n Esc to cancel"
    assert rv.classify(probe(text)) == rv.NEEDS_HUMAN


def test_api_stalled_beats_reconnectable():
    assert rv.classify(probe("/rc failed\nAPI Error: 400")) == rv.API_STALLED


def test_the_pill_means_connected_not_disconnected():
    """Verified against a live session: disconnecting makes the pill vanish.

    Getting this backwards is the expensive mistake — ``/remote-control``
    sent at a connected session opens a modal menu inside it.
    """
    footer = "  auto mode on\n                                        /rc\n"
    assert rv.classify(probe(footer)) == rv.CONNECTED
    assert rv.classify(probe(footer + "\n/rc active")) == rv.CONNECTED


def test_the_pill_outranks_a_stale_disconnect_notice():
    """A session that dropped and recovered keeps the notice in its scrollback."""
    tail = "Remote Control disconnected.\n...later...\n   /rc"
    assert rv.classify(probe(tail)) == rv.CONNECTED


def test_no_pill_and_no_bridge_id_is_a_drop():
    tail = "ordinary conversation\n\u276f "
    assert rv.classify(probe(tail, bridge_session_id=None)) == rv.RECONNECTABLE


def test_a_lingering_bridge_id_is_not_enough_to_act_on():
    """Conjunctive on purpose: one observable alone is not worth a keystroke."""
    tail = "ordinary conversation\n\u276f "
    assert rv.classify(probe(tail, bridge_session_id="s_1")) == rv.UNKNOWN


def test_pill_match_is_anchored_not_a_substring():
    """``"/rc" in text`` would fire on a file path; the pill ends its line."""
    assert rv.classify(probe("editing src/rc today\nmore text")) == rv.UNKNOWN


def test_unknown_is_the_floor_never_an_invitation():
    assert rv.classify(probe("")) == rv.UNKNOWN
    assert rv.UNKNOWN not in rv.ACTIONABLE
    assert rv.NO_CLAUDE not in rv.ACTIONABLE
    assert rv.HELD_ELSEWHERE not in rv.ACTIONABLE


def test_rules_are_injectable_and_verdicts_open():
    custom = ((("wedged"), lambda p: "kernel panic" in p.text),) + rv.DEFAULT_RULES
    assert rv.classify(probe("kernel panic"), rules=custom) == "wedged"
    # …and the default table is untouched by that call.
    assert rv.classify(probe("kernel panic")) == rv.UNKNOWN


def test_every_default_rule_declares_a_known_verdict():
    assert {verdict for verdict, _ in rv.DEFAULT_RULES} <= rv.VERDICTS


def test_evidence_names_the_phrase_that_decided_it():
    p = probe("Remote Control disconnected")
    assert rv.evidence_for(p, rv.RECONNECTABLE) == "remote control disconnected"


# --------------------------------------------------------------------------- #
# SessionPanes
# --------------------------------------------------------------------------- #


def test_session_panes_maps_target_to_state():
    panes = panes_from({"a:@1.%1": "/rc failed", "b:@1.%1": "/rc active"})
    assert sorted(panes) == ["a:@1.%1", "b:@1.%1"]
    assert panes["a:@1.%1"].verdict == rv.RECONNECTABLE
    assert panes["b:@1.%1"].verdict == rv.CONNECTED
    assert panes.by_verdict()[rv.RECONNECTABLE] == ["a:@1.%1"]


def test_session_panes_reads_each_pane_once_and_only_on_access():
    reads = []
    refs = [ref(target="a:@1.%1"), ref(target="b:@1.%1")]
    panes = rv.SessionPanes(
        panes=lambda: refs,
        is_relevant=lambda r: True,
        tail=lambda r: reads.append(r.target) or "quiet",
    )
    assert len(panes) == 2 and reads == []  # listing costs no captures
    panes["a:@1.%1"], panes["a:@1.%1"]
    assert reads == ["a:@1.%1"]


def test_session_panes_honours_is_relevant():
    refs = [ref(target="a:@1.%1"), ref(target="b:@1.%1")]
    panes = rv.SessionPanes(
        panes=lambda: refs,
        is_relevant=lambda r: r.target.startswith("a"),
        tail=lambda r: "quiet",
    )
    assert list(panes) == ["a:@1.%1"]


def test_refresh_drops_caches():
    tails = {"a:@1.%1": "/rc failed"}
    panes = panes_from(tails)
    assert panes["a:@1.%1"].verdict == rv.RECONNECTABLE
    tails["a:@1.%1"] = "/rc active"
    assert panes["a:@1.%1"].verdict == rv.RECONNECTABLE  # cached
    assert panes.refresh()["a:@1.%1"].verdict == rv.CONNECTED


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #


def _write_eph(home: Path, pid: int, **fields):
    d = home / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}.json").write_text(json.dumps({"pid": pid, **fields}))


def test_local_panes_reads_the_pane_ref_off_the_session_file(tmp_path):
    home = tmp_path / ".claude"
    _write_eph(
        home,
        11,
        tmux="work:@2.%3",
        cwd="/srv/app",
        sessionId="sid",
        bridgeSessionId="bid",
        name="worker",
    )
    (r,) = list(rv.local_panes(claude_home=home))
    assert (r.target, r.claude_pid, r.cwd) == ("work:@2.%3", 11, "/srv/app")
    assert (r.session_id, r.bridge_session_id, r.name) == ("sid", "bid", "worker")


def test_local_panes_skips_sessions_that_are_not_under_tmux(tmp_path):
    """A terminal-tab session has no pane to send keys to. Not an error."""
    home = tmp_path / ".claude"
    _write_eph(home, 11, tmux="work:@1.%1")
    _write_eph(home, 12, cwd="/home/me")  # no "tmux" key
    assert [r.claude_pid for r in rv.local_panes(claude_home=home)] == [11]


def test_local_panes_is_empty_without_a_sessions_dir(tmp_path):
    assert list(rv.local_panes(claude_home=tmp_path / "nope")) == []


def test_has_live_claude_demands_identity_not_just_a_pid(monkeypatch):
    monkeypatch.setattr(rv.ccli, "pid_is_claude", lambda pid: pid == 42)
    assert rv.has_live_claude(rv.PaneRef(target="t", claude_pid=42))
    assert not rv.has_live_claude(rv.PaneRef(target="t", claude_pid=43))
    assert not rv.has_live_claude(rv.PaneRef(target="t", claude_pid=None))


# --------------------------------------------------------------------------- #
# rate guard
# --------------------------------------------------------------------------- #


def test_rate_guard_blocks_then_releases():
    now = [0.0]
    guard = rv.RateGuard(store={}, min_interval_sec=60.0, clock=lambda: now[0])
    assert guard.allow("t")
    guard.record("t")
    now[0] = 30.0
    assert not guard.allow("t") and guard.wait_remaining("t") == 30.0
    now[0] = 61.0
    assert guard.allow("t")


def test_rate_guard_survives_the_process(tmp_path):
    """The point of the guard is a cron tick meeting a human's manual run."""
    store = st.FileStore(tmp_path / "revive", suffix=".stamp")
    rv.RateGuard(store=store, min_interval_sec=60.0, clock=lambda: 100.0).record("t")
    fresh = rv.RateGuard(store=store, min_interval_sec=60.0, clock=lambda: 130.0)
    assert not fresh.allow("t")


def test_rate_guard_tolerates_a_corrupt_stamp():
    guard = rv.RateGuard(store={rv._store_key("t"): b"not-a-float"}, clock=lambda: 0.0)
    assert guard.last_attempt("t") is None and guard.allow("t")


def test_store_keys_are_safe_and_distinct():
    a, b = rv._store_key("work:@1.%1"), rv._store_key("work:@1.%2")
    assert a != b
    assert all(st._KEY_RE.match(k) for k in (a, b))


# --------------------------------------------------------------------------- #
# revive
# --------------------------------------------------------------------------- #


def _revive(tails, refs=None, **kw):
    sent = []
    kw.setdefault("rate_guard", rv.RateGuard(store={}, clock=lambda: 0.0))
    actions = rv.revive(
        panes=panes_from(tails, refs=refs),
        send=lambda target, *keys: sent.append((target, keys)),
        **kw,
    )
    return actions, sent


def test_dry_run_is_the_default_and_sends_nothing():
    actions, sent = _revive({"a:@1.%1": "/rc failed"})
    assert sent == []
    (action,) = actions
    assert action.verdict == rv.RECONNECTABLE and action.would_send
    assert action.keys == rv.RECONNECT_KEYS


def test_apply_sends_the_slash_command():
    actions, sent = _revive({"a:@1.%1": "/rc failed"}, apply=True)
    assert sent == [("a:@1.%1", ("/remote-control", "Enter"))]
    assert actions[0].sent is True


@pytest.mark.parametrize(
    "tail",
    [
        "ended or archived from another device",
        "Do you want to proceed?",
        "API Error: 400",
        "/rc active",
        "/rc reconnecting",
    ],
)
def test_apply_never_touches_a_pane_it_must_not(tail):
    _, sent = _revive({"a:@1.%1": tail}, apply=True)
    assert sent == []


def test_held_elsewhere_is_reachable_only_by_opting_in():
    tails = {"a:@1.%1": "ended or archived from another device"}
    _, sent = _revive(tails, apply=True, include_held_elsewhere=True)
    assert sent == [("a:@1.%1", rv.RECONNECT_KEYS)]


def test_rate_limited_pane_is_reported_not_sent():
    guard = rv.RateGuard(store={}, min_interval_sec=600.0, clock=lambda: 0.0)
    guard.record("a:@1.%1")
    actions, sent = _revive({"a:@1.%1": "/rc failed"}, apply=True, rate_guard=guard)
    assert sent == []
    assert actions[0].skipped.startswith("rate-limited")


def test_a_second_pass_cannot_spam_the_same_pane():
    guard = rv.RateGuard(store={}, min_interval_sec=600.0, clock=lambda: 0.0)
    first, sent = _revive({"a:@1.%1": "/rc failed"}, apply=True, rate_guard=guard)
    second, sent2 = _revive({"a:@1.%1": "/rc failed"}, apply=True, rate_guard=guard)
    assert first[0].sent and not second[0].sent
    assert len(sent) == 1 and sent2 == []


def test_a_dry_run_does_not_consume_the_rate_budget():
    guard = rv.RateGuard(store={}, min_interval_sec=600.0, clock=lambda: 0.0)
    _revive({"a:@1.%1": "/rc failed"}, rate_guard=guard)
    assert guard.allow("a:@1.%1")


def test_every_pane_is_reported_including_the_untouched_ones():
    actions, _ = _revive({"a:@1.%1": "/rc failed", "b:@1.%1": "/rc active"})
    assert [(a.target, a.verdict) for a in actions] == [
        ("a:@1.%1", rv.RECONNECTABLE),
        ("b:@1.%1", rv.CONNECTED),
    ]
    assert actions[1].skipped == "not actionable"


def test_server_mode_pane_is_routed_not_reconnected():
    tails = {"s:@1.%1": "Re-run `claude remote-control` to try again"}
    actions, sent = _revive(tails, apply=True)
    assert sent == [] and "restart_server_mode" in actions[0].skipped


# --------------------------------------------------------------------------- #
# server mode
# --------------------------------------------------------------------------- #


def _state(verdict=rv.SERVER_MODE, **kw):
    return rv.PaneState(ref=rv.PaneRef(target="s:@1.%1", **kw), tail="", verdict=verdict)


def test_restart_server_mode_refuses_while_a_claude_owns_the_pane():
    action = rv.restart_server_mode(_state(claude_pid=9, cwd="/srv"), apply=True)
    assert not action.sent and "still owns this pane" in action.skipped


def test_restart_server_mode_uses_the_panes_recorded_cwd():
    sent = []
    action = rv.restart_server_mode(
        _state(cwd="/srv/app"),
        apply=True,
        rate_guard=rv.RateGuard(store={}, clock=lambda: 0.0),
        send=lambda t, *k: sent.append((t, k)),
    )
    assert action.sent and action.cwd == "/srv/app"
    assert sent == [("s:@1.%1", ("cd /srv/app && claude remote-control -c", "Enter"))]


def test_restart_server_mode_falls_back_to_asking_tmux():
    sent = []
    rv.restart_server_mode(
        _state(),
        apply=True,
        rate_guard=rv.RateGuard(store={}, clock=lambda: 0.0),
        send=lambda t, *k: sent.append((t, k)),
        resolve_cwd=lambda target: "/from/tmux",
    )
    assert sent[0][1][0] == "cd /from/tmux && claude remote-control -c"


def test_restart_server_mode_says_so_when_it_cannot_find_a_directory():
    action = rv.restart_server_mode(_state(), apply=True, resolve_cwd=lambda t: None)
    assert not action.sent and "no directory" in action.skipped


def test_restart_server_mode_is_a_dry_run_by_default():
    action = rv.restart_server_mode(
        _state(cwd="/srv"), resolve_cwd=lambda t: None,
        rate_guard=rv.RateGuard(store={}, clock=lambda: 0.0),
    )
    assert action.would_send and not action.sent


def test_server_mode_command_quotes_a_hostile_directory():
    keys = rv.server_mode_command("/tmp/a b; rm -rf /")
    assert keys[0] == "cd '/tmp/a b; rm -rf /' && claude remote-control -c"


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #


def test_format_actions_puts_the_actionable_first():
    actions = [
        rv.ReviveAction("b:@1.%1", rv.CONNECTED),
        rv.ReviveAction("a:@1.%1", rv.RECONNECTABLE, keys=rv.RECONNECT_KEYS,
                        skipped="dry-run", evidence="/rc failed"),
    ]
    lines = rv.format_actions(actions).splitlines()
    assert lines[0].startswith("reconnectable") and "/rc failed" in lines[0]
    assert lines[1].startswith("connected")


def test_format_actions_handles_nothing_to_report():
    assert rv.format_actions([]) == ""



# --------------------------------------------------------------------------- #
# typing safety
# --------------------------------------------------------------------------- #


def test_prompt_is_clear_reads_the_input_line():
    assert rv.prompt_is_clear("box\n\u276f \nbox")
    assert rv.prompt_is_clear("\u276f\u00a0")  # the TUI's real NBSP spelling
    assert not rv.prompt_is_clear("\u276f\u00a0yes, port it into the repo")


def test_prompt_is_clear_refuses_when_it_cannot_see_the_buffer():
    """'I could not find the prompt' must never read as 'the prompt is empty'."""
    assert not rv.prompt_is_clear("scrollback with no input line")
    assert not rv.prompt_is_clear("")


def test_prompt_is_clear_reads_the_last_prompt_not_an_earlier_one():
    """Scrollback holds old prompt lines; only the live one can be typed into."""
    tail = "\u276f old submitted line\n...\n\u276f\u00a0queued text"
    assert not rv.prompt_is_clear(tail)


def test_queued_text_is_never_appended_to():
    """The failure this gate exists for: send-keys appends to the buffer.

    Both live panes on the real server held an unsent instruction when this
    was written; sending /remote-control would have submitted
    "yes, port it into the repo/remote-control" and lost the instruction.
    """
    refs = [ref(target="a:@1.%1", bridge_session_id="s", status="idle")]
    panes = rv.SessionPanes(
        panes=lambda: refs,
        is_relevant=lambda r: True,
        tail=lambda r: "/rc failed\n\u276f\u00a0yes, port it into the repo",
    )
    sent = []
    actions = rv.revive(
        panes=panes, apply=True,
        rate_guard=rv.RateGuard(store={}, clock=lambda: 0.0),
        send=lambda t, *k: sent.append(t),
    )
    assert sent == []
    assert actions[0].verdict == rv.RECONNECTABLE  # still reported as dropped
    assert actions[0].skipped == "prompt holds unsent text"


@pytest.mark.parametrize("status", ["busy", "waiting"])
def test_a_session_mid_turn_is_not_typed_into(status):
    refs = [ref(target="a:@1.%1", bridge_session_id="s", status=status)]
    panes = rv.SessionPanes(
        panes=lambda: refs, is_relevant=lambda r: True,
        tail=lambda r: "/rc failed\n\u276f ",
    )
    sent = []
    actions = rv.revive(
        panes=panes, apply=True,
        rate_guard=rv.RateGuard(store={}, clock=lambda: 0.0),
        send=lambda t, *k: sent.append(t),
    )
    assert sent == [] and actions[0].skipped == f"session is {status}"


def test_a_refused_pane_does_not_consume_the_rate_budget():
    """Otherwise a pane you never touched sits in a cooldown for ten minutes."""
    guard = rv.RateGuard(store={}, min_interval_sec=600.0, clock=lambda: 0.0)
    refs = [ref(target="a:@1.%1", bridge_session_id="s", status="busy")]
    panes = rv.SessionPanes(
        panes=lambda: refs, is_relevant=lambda r: True,
        tail=lambda r: "/rc failed\n\u276f ",
    )
    rv.revive(panes=panes, apply=True, rate_guard=guard, send=lambda t, *k: None)
    assert guard.allow("a:@1.%1")


def test_status_is_read_off_the_session_file(tmp_path):
    home = tmp_path / ".claude"
    _write_eph(home, 11, tmux="w:@1.%1", status="busy")
    (r,) = list(rv.local_panes(claude_home=home))
    assert r.status == "busy"


# --------------------------------------------------------------------------- #
# doctests — testpaths scopes them out of collection, so run them here
# --------------------------------------------------------------------------- #


def test_module_doctests_pass():
    import doctest

    result = doctest.testmod(
        rv, optionflags=doctest.NORMALIZE_WHITESPACE | doctest.ELLIPSIS
    )
    assert result.attempted > 0
    assert result.failed == 0
