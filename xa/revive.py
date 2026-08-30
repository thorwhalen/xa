"""Detect — and reconnect — Claude Code panes whose Remote Control dropped.

Remote Control gives up on its own: after a few minutes of failed reconnects,
or roughly thirty minutes of failed presence heartbeats, it stops trying and
leaves the local session running but unreachable from claude.ai. The usual
cause is a network change (moving between networks, toggling a VPN) that
produces a transient HTTP 403 — the same change that clears it, usually
within a minute or two.

Two facts shape everything below, and both were verified rather than assumed:

- **Two observables, and both were checked against a live session rather
  than assumed.** The footer carries a ``/rc`` *pill* that is present while
  Remote Control is connected (``/rc``, or ``/rc active`` when verbose) and
  **disappears** when it is not — so the pill's absence, not its presence,
  is what marks a dropped session. The ephemeral session file
  (``~/.claude/sessions/<pid>.json``) agrees from the other side: its
  ``bridgeSessionId`` is a real id while connected and ``null`` once it is
  not. The transcript JSONL never mentions Remote Control at all, so there
  is no third source.
- **``tmux send-keys`` is the only way to act.** ``/remote-control`` is a
  built-in slash command; the CLI exposes no subcommand and no control
  request that reconnects a *running* session (checked against
  ``claude --help`` and ``claude remote-control --help``, 2.1.251 — the only
  session-shaped verbs there are ``agents``/``attach``/``logs``/``stop``/
  ``rm``/``respawn``, all of which are about background agents).

The detection phrases in :data:`MARKERS` are transcribed from the strings
embedded in the ``claude`` 2.1.251 binary, not guessed from screenshots.
They are data, and :data:`DEFAULT_RULES` is injectable, so a wording change
upstream is a one-line edit rather than a rewrite.

Four things this module refuses to do, each deliberate:

- **It never revives a session held elsewhere.** When a session was taken
  over or ended from another device, the TUI says so (WebSocket close code
  4090) and *omits* its "run /remote-control" advice. Sending the command
  anyway would steal the session back from the user's phone mid-sentence.
  :data:`HELD_ELSEWHERE` is therefore tested before :data:`RECONNECTABLE`
  and acting on it stays opt-in.
- **It never resends a prompt.** An API-stalled pane is reported and left
  alone: the fix there is a real model call that can duplicate work.
- **It never types into a pane waiting on a human.** A permission dialog or
  a ``/login`` prompt consumes keystrokes as *answers* — sending
  ``/remote-control`` into an open "Do you want to proceed?" selector would
  answer it. Those panes classify as :data:`NEEDS_HUMAN` and are skipped.
- **It does nothing at all unless asked.** ``apply=True`` is explicit;
  every entry point is a dry run by default.

Typical use::

    from xa.revive import SessionPanes, revive

    for action in revive(panes=SessionPanes()):      # dry run
        print(action.target, action.verdict, action.skipped)

Discovery does **not** enumerate tmux and guess which pane holds a claude.
It reads the live ephemeral session files, each of which records its own
pane as ``"tmux": "session:@window.%pane"``, so the pane refs are exact and
``pane_current_command`` never enters the decision. See :func:`local_panes`.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Sequence

from xa import claude_cli as ccli
from xa import claude_fs as cfs
from xa import store as st
from xa import tmux as tm


# --------------------------------------------------------------------------- #
# verdicts
# --------------------------------------------------------------------------- #

#: No claude process behind the pane — a shell prompt, or a session that died.
NO_CLAUDE = "no_claude"
#: Taken over or ended from another device. Never reconnect without opt-in.
HELD_ELSEWHERE = "held_elsewhere"
#: Remote Control is up. Nothing to do.
CONNECTED = "connected"
#: Remote Control is retrying right now. Leave it alone; it may well succeed.
RECONNECTING = "reconnecting"
#: Blocked on a human — login, workspace trust, or an open permission dialog.
NEEDS_HUMAN = "needs_human"
#: The API call is wedged. Reported, never acted on (the fix is a new prompt).
API_STALLED = "api_stalled"
#: A ``claude remote-control`` *server* died. See :func:`restart_server_mode`.
SERVER_MODE = "server_mode"
#: Dropped and recoverable — the one verdict :func:`revive` acts on.
RECONNECTABLE = "reconnectable"
#: The pane says nothing either way. Never acted on.
UNKNOWN = "unknown"

#: Every verdict :data:`DEFAULT_RULES` can produce. Custom rules may add more.
VERDICTS = frozenset(
    {
        NO_CLAUDE,
        HELD_ELSEWHERE,
        CONNECTED,
        RECONNECTING,
        NEEDS_HUMAN,
        API_STALLED,
        SERVER_MODE,
        RECONNECTABLE,
        UNKNOWN,
    }
)

#: Verdicts :func:`revive` will send ``/remote-control`` to. ``HELD_ELSEWHERE``
#: is deliberately absent — it is reachable only via ``include_held_elsewhere``.
ACTIONABLE = frozenset({RECONNECTABLE})


# --------------------------------------------------------------------------- #
# pane data (pure)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PaneRef:
    """Identity of one candidate pane — everything known *without* reading it.

    ``target`` is a tmux target: a full pane ref (``session:@window.%pane``,
    which is what the ephemeral session file records) or a bare session name.
    """

    target: str
    host: str = "local"
    claude_pid: Optional[int] = None
    cwd: Optional[str] = None
    session_id: Optional[str] = None
    bridge_session_id: Optional[str] = None
    name: Optional[str] = None
    #: ``idle`` / ``busy`` / ``waiting`` as the session last reported it.
    #: ``None`` on a claude too old to record one.
    status: Optional[str] = None


@dataclass(frozen=True)
class Probe:
    """The complete, pure input to a classification rule.

    ``text`` is the pane tail, lowercased once so every rule can use a plain
    substring test.
    """

    text: str
    ref: PaneRef = field(default_factory=lambda: PaneRef(target="?"))


@dataclass(frozen=True)
class PaneState:
    """A pane, as read and judged. The value type of :class:`SessionPanes`."""

    ref: PaneRef
    tail: str
    verdict: str
    evidence: Optional[str] = None

    @property
    def target(self) -> str:
        return self.ref.target


# --------------------------------------------------------------------------- #
# detection phrases (data, transcribed from the claude 2.1.251 binary)
# --------------------------------------------------------------------------- #

#: Substring markers per verdict. Lowercase; matched against a lowered tail.
MARKERS: dict[str, tuple[str, ...]] = {
    # WebSocket close code 4090 — someone else now owns the session, plus the
    # same-machine variant where another Claude Code holds this conversation.
    HELD_ELSEWHERE: (
        "ended or archived from another device",
        "no longer the active worker for the session",
        "already has remote control for this conversation",
        "run /remote-control to move it to this terminal",
    ),
    # The pill itself is matched by RC_PILL; this is the prose form.
    CONNECTED: ("remote control active",),
    RECONNECTING: ("/rc reconnecting",),
    # Reuses claude_cli's login/trust vocabulary, plus the dialog chrome that
    # proves a prompt is open and eating keystrokes.
    NEEDS_HUMAN: ccli._LOGIN_PANE_MARKERS
    + ccli._TRUST_PANE_MARKERS
    + (
        "do you want to proceed?",
        "esc to cancel",
    ),
    API_STALLED: ("api error", "retrying in ", "overloaded"),
    # Server mode prints its own retry advice; the REPL prints a slash command.
    SERVER_MODE: ("re-run `claude remote-control` to try again",),
    RECONNECTABLE: (
        "remote control disconnected",
        "remote control not started here",
        "run /remote-control to retry",
        "/remote-control is no longer active",
        "/rc failed",
    ),
}

#: The footer *pill*. Present while Remote Control is connected — ``/rc`` on
#: its own right-aligned line, or ``/rc active`` in verbose mode — and gone
#: the moment it is not. Anchored to a line end because a bare ``"/rc" in
#: text`` would also match a file path ending in ``src/rc``.
#:
#: The direction matters and is easy to get backwards: the pill is evidence
#: of *health*. Verified by disconnecting a live session and watching the
#: line vanish (claude 2.1.251).
RC_PILL = re.compile(r"(?m)^.*(?<![\w/])/rc(?: active)?\s*$")


# --------------------------------------------------------------------------- #
# rules
# --------------------------------------------------------------------------- #

Predicate = Callable[[Probe], bool]
Rule = tuple[str, Predicate]


def _any_of(*markers: str) -> Predicate:
    """A predicate matching any of ``markers`` (case-insensitive substrings).

    The markers ride along on the returned function so a match can report
    *which* phrase it saw, without a second table to keep in step.

    >>> pred = _any_of('Hello', 'Goodbye')
    >>> pred(Probe(text='well hello there'))
    True
    >>> pred(Probe(text='nothing to see'))
    False
    >>> pred.markers
    ('hello', 'goodbye')
    """
    lowered = tuple(m.lower() for m in markers)

    def _pred(probe: Probe) -> bool:
        return any(m in probe.text for m in lowered)

    _pred.markers = lowered  # type: ignore[attr-defined]
    return _pred


def _pill_present(probe: Probe) -> bool:
    """True when the footer still carries its Remote Control pill."""
    return bool(RC_PILL.search(probe.text))


_pill_present.markers = ("/rc",)  # type: ignore[attr-defined]


def _bridge_down(probe: Probe) -> bool:
    """Both observables agree that Remote Control is not connected.

    Deliberately conjunctive. Either half alone has a failure direction that
    costs the user something: the pill can be missed (a redraw, an unusual
    width) and reconnecting a healthy session opens a modal menu in it,
    while the session file is written by the session itself and cannot be
    read at all for a claude too old to write one.
    """
    return probe.ref.bridge_session_id is None and not _pill_present(probe)


def _no_claude(probe: Probe) -> bool:
    return probe.ref.claude_pid is None


def _always(probe: Probe) -> bool:
    return True


#: Ordered ``(verdict, predicate)`` pairs; **first match wins**.
#:
#: The order is the policy, not a style choice, and two things decide it.
#:
#: *Safety*: ``NO_CLAUDE`` leads, so a dead session's scrollback can never be
#: read as live state — the pane this was written against still showed a
#: permission dialog and a disconnect notice above a shell prompt, all true
#: once and none true now. ``HELD_ELSEWHERE`` precedes every reconnect
#: verdict, per the rule that a session picked up on another device is never
#: taken back without asking.
#:
#: *Freshness*: the pill rules are **live** state and the phrase rules are
#: **history**. A session that dropped and recovered still carries
#: "Remote Control disconnected" in its scrollback for as long as it stays on
#: screen, so a text rule placed above the pill would report a healthy
#: session as broken until the line scrolled away.
DEFAULT_RULES: tuple[Rule, ...] = (
    (NO_CLAUDE, _no_claude),
    (HELD_ELSEWHERE, _any_of(*MARKERS[HELD_ELSEWHERE])),
    (RECONNECTING, _any_of(*MARKERS[RECONNECTING])),
    (CONNECTED, _pill_present),
    (CONNECTED, _any_of(*MARKERS[CONNECTED])),
    (NEEDS_HUMAN, _any_of(*MARKERS[NEEDS_HUMAN])),
    (API_STALLED, _any_of(*MARKERS[API_STALLED])),
    (SERVER_MODE, _any_of(*MARKERS[SERVER_MODE])),
    (RECONNECTABLE, _any_of(*MARKERS[RECONNECTABLE])),
    (RECONNECTABLE, _bridge_down),
    (UNKNOWN, _always),
)


def classify(probe: Probe, *, rules: Sequence[Rule] = DEFAULT_RULES) -> str:
    r"""Return the verdict for ``probe``. Pure — no I/O, no tmux, no clock.

    A session whose footer pill is gone and whose session file records no
    bridge id has dropped:

    >>> dropped = PaneRef(target='w:@1.%1', claude_pid=42)
    >>> classify(Probe(text='conversation text\n❯ ', ref=dropped))
    'reconnectable'

    One still carrying the pill has not, whatever its scrollback remembers:

    >>> live = PaneRef(target='w:@1.%1', claude_pid=42, bridge_session_id='s')
    >>> classify(Probe(text='remote control disconnected\n     /rc', ref=live))
    'connected'

    A session someone picked up on their phone is never reconnectable, even
    though the pane also says it disconnected:

    >>> taken = 'remote control disconnected\nended or archived from another device'
    >>> classify(Probe(text=taken, ref=dropped))
    'held_elsewhere'

    Neither is one sitting on a permission dialog:

    >>> classify(Probe(text='/rc failed\ndo you want to proceed?', ref=live))
    'needs_human'

    And a healthy, quiet, connected pane is simply left alone:

    >>> classify(Probe(text='just some conversation\n  /rc', ref=live))
    'connected'
    """
    for verdict, predicate in rules:
        if predicate(probe):
            return verdict
    return UNKNOWN


def evidence_for(probe: Probe, verdict: str, *, rules: Sequence[Rule] = DEFAULT_RULES) -> Optional[str]:
    """The marker that earned ``verdict``, for a report a human has to trust.

    >>> ref = PaneRef(target='w:@1.%1', claude_pid=1)
    >>> evidence_for(Probe(text='/rc failed', ref=ref), 'reconnectable')
    '/rc failed'
    """
    for rule_verdict, predicate in rules:
        if rule_verdict != verdict or not predicate(probe):
            continue
        for marker in getattr(predicate, "markers", ()):
            if marker in probe.text:
                return marker
        return getattr(predicate, "markers", (None,))[0]
    return None


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #


def local_panes(*, claude_home: Path = cfs.DEFAULT_CLAUDE_HOME) -> Iterator[PaneRef]:
    """Yield one :class:`PaneRef` per live claude that records a tmux pane.

    Derived from the ephemeral session files rather than from
    ``tmux list-panes``: each file names its own pane, so no heuristic has
    to decide whether a pane holds a claude. A session not under tmux (every
    session on a machine that runs claude in terminal tabs) simply has no
    ``tmux`` key and is skipped — there is no pane to send keys to.
    """
    for eph in cfs.iter_ephemeral_sessions(claude_home=claude_home):
        target = eph.get("tmux")
        if not target:
            continue
        yield PaneRef(
            target=target,
            claude_pid=eph.get("pid"),
            cwd=eph.get("cwd"),
            session_id=eph.get("sessionId"),
            bridge_session_id=eph.get("bridgeSessionId"),
            name=eph.get("name"),
            status=eph.get("status"),
        )


def has_live_claude(ref: PaneRef) -> bool:
    """Default relevance test: the recorded pid is a *claude* that still exists.

    Identity, not just liveness — a stale ephemeral file whose pid got
    recycled must never direct keystrokes at an unrelated process.
    """
    return ref.claude_pid is not None and ccli.pid_is_claude(ref.claude_pid)


def _capture(ref: PaneRef, *, lines: int = 200) -> str:
    return tm.capture_pane(ref.target, lines=lines)


class SessionPanes(Mapping):
    """Live view of the panes worth judging: ``{tmux target: PaneState}``.

    Every source of truth is injected, which is what lets the whole
    classification path be tested without a tmux server:

    - ``panes`` — callable returning :class:`PaneRef` objects (default:
      :func:`local_panes`);
    - ``is_relevant`` — per-ref filter (default: :func:`has_live_claude`);
    - ``tail`` — callable reading a ref's pane text (default: tmux capture);
    - ``rules`` — the classification table (default: :data:`DEFAULT_RULES`).

    Pane text is read lazily and cached, so building the mapping costs
    nothing and iterating it costs one capture per pane.

    >>> refs = [PaneRef(target='w:@1.%1', claude_pid=7, bridge_session_id='s_1')]
    >>> panes = SessionPanes(
    ...     panes=lambda: refs,
    ...     is_relevant=lambda ref: True,
    ...     tail=lambda ref: 'Remote Control disconnected',
    ... )
    >>> list(panes)
    ['w:@1.%1']
    >>> panes['w:@1.%1'].verdict
    'reconnectable'
    >>> panes.by_verdict()['reconnectable']
    ['w:@1.%1']
    """

    def __init__(
        self,
        *,
        panes: Callable[[], Iterable[PaneRef]] = local_panes,
        is_relevant: Callable[[PaneRef], bool] = has_live_claude,
        tail: Callable[[PaneRef], str] = _capture,
        rules: Sequence[Rule] = DEFAULT_RULES,
    ) -> None:
        self._panes = panes
        self._is_relevant = is_relevant
        self._tail = tail
        self._rules = rules
        self._refs: Optional[dict[str, PaneRef]] = None
        self._states: dict[str, PaneState] = {}

    def refresh(self) -> "SessionPanes":
        """Drop every cached ref and pane read. Returns self, for chaining."""
        self._refs = None
        self._states = {}
        return self

    @property
    def _ref_map(self) -> dict[str, PaneRef]:
        if self._refs is None:
            self._refs = {
                ref.target: ref for ref in self._panes() if self._is_relevant(ref)
            }
        return self._refs

    def __iter__(self) -> Iterator[str]:
        return iter(self._ref_map)

    def __len__(self) -> int:
        return len(self._ref_map)

    def __getitem__(self, target: str) -> PaneState:
        ref = self._ref_map[target]
        if target not in self._states:
            tail = self._tail(ref)
            probe = Probe(text=tail.lower(), ref=ref)
            verdict = classify(probe, rules=self._rules)
            self._states[target] = PaneState(
                ref=ref,
                tail=tail,
                verdict=verdict,
                evidence=evidence_for(probe, verdict, rules=self._rules),
            )
        return self._states[target]

    def by_verdict(self) -> dict[str, list[str]]:
        """``{verdict: [target, ...]}`` — reads every pane."""
        out: dict[str, list[str]] = {}
        for target in self:
            out.setdefault(self[target].verdict, []).append(target)
        return out


# --------------------------------------------------------------------------- #
# typing safety
# --------------------------------------------------------------------------- #

#: The TUI's input line: the prompt glyph, then U+00A0, then whatever the user
#: has typed. ``> `` is the plain-ASCII rendering some terminals get.
PROMPT_LINE = re.compile("(?m)^(?:\u276f|>)[\u00a0 ](.*)$")

#: Statuses in which a keystroke is a *command*. Anything else — ``busy``,
#: ``waiting`` — means the session is mid-turn or holding a dialog, and typed
#: text would be queued into it. ``None`` is a claude too old to say.
TYPEABLE_STATUS = frozenset({"idle", None})


def prompt_is_clear(tail: str) -> bool:
    r"""True when the pane's input line is empty and safe to type into.

    This is the difference between reconnecting a session and destroying the
    instruction its owner left half-typed: ``send-keys`` appends to whatever
    is already in the buffer, so ``/remote-control`` sent at a pane whose
    prompt reads ``yes, port it into the repo`` submits
    ``yes, port it into the repo/remote-control``.

    Conservative by construction — a pane whose prompt line cannot be found
    is reported as not clear, because "I could not see the buffer" and "the
    buffer is empty" must never collapse into the same answer.

    >>> prompt_is_clear('---\n❯ \n---')
    True
    >>> prompt_is_clear('❯ yes, port it into the repo')
    False
    >>> prompt_is_clear('no prompt line here at all')
    False
    """
    matches = PROMPT_LINE.findall(tail)
    if not matches:
        return False
    return not matches[-1].strip()


def refusal_to_type(state: "PaneState") -> Optional[str]:
    r"""Why typing into ``state`` would be unsafe, or ``None`` if it is fine.

    Two independent gates, because each catches what the other misses: the
    reported status catches a session mid-turn whose buffer happens to be
    empty, and the buffer check catches an idle session holding text the
    status knows nothing about.

    >>> ref = PaneRef(target='t', claude_pid=1, status='idle')
    >>> refusal_to_type(PaneState(ref=ref, tail='❯ ', verdict=RECONNECTABLE))
    >>> busy = replace(ref, status='busy')
    >>> refusal_to_type(PaneState(ref=busy, tail='❯ ', verdict=RECONNECTABLE))
    'session is busy'
    """
    if state.ref.status not in TYPEABLE_STATUS:
        return f"session is {state.ref.status}"
    if not prompt_is_clear(state.tail):
        return "prompt holds unsent text"
    return None


# --------------------------------------------------------------------------- #
# rate guard
# --------------------------------------------------------------------------- #

#: Long enough that a cron tick can never turn into a keystroke storm, short
#: enough that a real network change is followed within one settling period.
DFLT_MIN_INTERVAL_SEC = 600.0


def _store_key(target: str) -> str:
    """A ``FileStore``-safe key for a tmux target, collision-free.

    tmux targets carry ``:``, ``@`` and ``%``, none of which the store's key
    allowlist accepts, and squashing them would merge two panes into one
    record — so the readable part is a hint and the digest carries identity.

    >>> _store_key('work:@1.%2')
    'work..1..2.fa6511fa'
    """
    safe = re.sub(r"[^A-Za-z0-9_.-]", ".", target)[:48]
    digest = hashlib.sha256(target.encode()).hexdigest()[:8]
    return f"{safe}.{digest}"


class RateGuard:
    """Refuses to touch the same pane twice inside ``min_interval_sec``.

    Without this a cron tick, a hook and an impatient human can each send
    ``/remote-control`` into the same pane seconds apart. Backed by any
    ``{key: bytes}`` mapping — the default is xa's on-disk
    :class:`~xa.store.FileStore`, so the interval survives process exit,
    which is the entire point.

    >>> clock = iter([100.0, 200.0, 800.0]).__next__
    >>> guard = RateGuard(store={}, min_interval_sec=600.0, clock=clock)
    >>> guard.allow('work:@1.%1')     # never attempted
    True
    >>> guard.record('work:@1.%1')    # stamped at t=100
    >>> guard.allow('work:@1.%1')     # t=200 — 100s later, still held off
    False
    >>> guard.allow('work:@1.%1')     # t=800 — 700s later, released
    True
    """

    def __init__(
        self,
        *,
        store=None,
        min_interval_sec: float = DFLT_MIN_INTERVAL_SEC,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = st.default_revive_store() if store is None else store
        self.min_interval_sec = min_interval_sec
        self.clock = clock

    def last_attempt(self, target: str) -> Optional[float]:
        key = _store_key(target)
        try:
            raw = self.store[key]
        except KeyError:
            return None
        try:
            return float(raw.decode() if isinstance(raw, bytes) else raw)
        except (ValueError, AttributeError):
            return None

    def wait_remaining(self, target: str) -> float:
        """Seconds left before ``target`` may be touched again (0.0 if now)."""
        last = self.last_attempt(target)
        if last is None:
            return 0.0
        return max(0.0, self.min_interval_sec - (self.clock() - last))

    def allow(self, target: str) -> bool:
        return self.wait_remaining(target) <= 0.0

    def record(self, target: str) -> None:
        self.store[_store_key(target)] = str(self.clock()).encode()


# --------------------------------------------------------------------------- #
# acting
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReviveAction:
    """What was (or would be) done to one pane, and why."""

    target: str
    verdict: str
    keys: tuple[str, ...] = ()
    sent: bool = False
    skipped: Optional[str] = None
    evidence: Optional[str] = None
    cwd: Optional[str] = None

    @property
    def would_send(self) -> bool:
        """True when only ``apply=False`` stood in the way."""
        return bool(self.keys) and not self.sent and self.skipped == "dry-run"


#: The keystrokes that reconnect a REPL session. ``Enter`` submits.
RECONNECT_KEYS = ("/remote-control", "Enter")


def revive(
    *,
    panes: Optional[Mapping] = None,
    apply: bool = False,
    include_held_elsewhere: bool = False,
    rate_guard: Optional[RateGuard] = None,
    send: Optional[Callable[..., None]] = None,
    actionable: Iterable[str] = ACTIONABLE,
) -> list[ReviveAction]:
    r"""Reconnect every dropped pane. **Dry run unless ``apply=True``.**

    Returns one :class:`ReviveAction` per pane examined — including the ones
    left alone, each carrying the reason, because "nothing happened" is only
    useful next to what was looked at.

    ``include_held_elsewhere=True`` opts into reconnecting sessions that were
    taken over from another device. That steals the session back from
    whatever device holds it; it is off by default and should stay a
    deliberate keystroke.

    >>> refs = [
    ...     PaneRef(target='a:@1.%1', claude_pid=1),
    ...     PaneRef(target='b:@1.%1', claude_pid=2, bridge_session_id='s'),
    ... ]
    >>> tails = {'a:@1.%1': '/rc failed\n❯ ', 'b:@1.%1': 'all quiet\n❯ \n   /rc'}
    >>> panes = SessionPanes(
    ...     panes=lambda: refs,
    ...     is_relevant=lambda ref: True,
    ...     tail=lambda ref: tails[ref.target],
    ... )
    >>> sent = []
    >>> actions = revive(
    ...     panes=panes,
    ...     apply=True,
    ...     rate_guard=RateGuard(store={}, clock=lambda: 0.0),
    ...     send=lambda target, *keys: sent.append((target, keys)),
    ... )
    >>> [(a.target, a.verdict, a.sent) for a in actions]
    [('a:@1.%1', 'reconnectable', True), ('b:@1.%1', 'connected', False)]
    >>> sent
    [('a:@1.%1', ('/remote-control', 'Enter'))]
    """
    panes = SessionPanes() if panes is None else panes
    rate_guard = RateGuard() if rate_guard is None else rate_guard
    send = tm.send_keys if send is None else send
    act_on = set(actionable) | ({HELD_ELSEWHERE} if include_held_elsewhere else set())

    actions: list[ReviveAction] = []
    for target in sorted(panes):
        state = panes[target]
        common = dict(
            target=target,
            verdict=state.verdict,
            evidence=state.evidence,
            cwd=state.ref.cwd,
        )
        if state.verdict == SERVER_MODE:
            actions.append(
                ReviveAction(
                    skipped="server mode — use restart_server_mode()", **common
                )
            )
            continue
        if state.verdict not in act_on:
            actions.append(ReviveAction(skipped="not actionable", **common))
            continue
        refusal = refusal_to_type(state)
        if refusal is not None:
            actions.append(ReviveAction(skipped=refusal, **common))
            continue
        remaining = rate_guard.wait_remaining(target)
        if remaining > 0:
            actions.append(
                ReviveAction(skipped=f"rate-limited ({remaining:.0f}s)", **common)
            )
            continue
        if not apply:
            actions.append(
                ReviveAction(keys=RECONNECT_KEYS, skipped="dry-run", **common)
            )
            continue
        rate_guard.record(target)
        send(target, *RECONNECT_KEYS)
        actions.append(ReviveAction(keys=RECONNECT_KEYS, sent=True, **common))
    return actions


def server_mode_command(cwd: Optional[str]) -> tuple[str, ...]:
    """Keystrokes that restart a Remote Control *server* in ``cwd``.

    ``claude remote-control -c`` reattaches the session the command last
    recorded **for that directory** (within ~4h), which is why the directory
    — and nothing else — is what a restart needs to know.

    >>> server_mode_command('/root/py/proj')
    ('cd /root/py/proj && claude remote-control -c', 'Enter')
    >>> server_mode_command(None)
    ('claude remote-control -c', 'Enter')
    """
    base = "claude remote-control -c"
    if not cwd:
        return (base, "Enter")
    return (f"cd {shlex.quote(cwd)} && {base}", "Enter")


def restart_server_mode(
    state: PaneState,
    *,
    cwd: Optional[str] = None,
    apply: bool = False,
    rate_guard: Optional[RateGuard] = None,
    send: Optional[Callable[..., None]] = None,
    resolve_cwd: Optional[Callable[[str], Optional[str]]] = None,
) -> ReviveAction:
    """Restart a died ``claude remote-control`` server in its own directory.

    The directory is **not** recorded by this package. It is read back from
    what already knows it — the pane's own working directory via tmux, or
    the ephemeral session file when a claude is still alive — and the
    session identity is left to ``claude remote-control -c``, which keeps
    its own per-directory record. Adding a fourth place to write it down
    would only create something to drift.

    Refuses whenever a live claude still owns the pane: this sends a *shell*
    command, and a running TUI would take it as prompt text.

    >>> ref = PaneRef(target='srv:@1.%1', cwd='/srv/app')
    >>> state = PaneState(ref=ref, tail='', verdict=SERVER_MODE)
    >>> keyed = []
    >>> action = restart_server_mode(
    ...     state,
    ...     apply=True,
    ...     rate_guard=RateGuard(store={}, clock=lambda: 0.0),
    ...     send=lambda target, *keys: keyed.append((target, keys)),
    ... )
    >>> action.sent, keyed
    (True, [('srv:@1.%1', ('cd /srv/app && claude remote-control -c', 'Enter'))])

    >>> live = PaneState(ref=replace(ref, claude_pid=9), tail='', verdict=SERVER_MODE)
    >>> restart_server_mode(live, apply=True).skipped
    'a claude still owns this pane — keystrokes would land in its prompt'
    """
    rate_guard = RateGuard() if rate_guard is None else rate_guard
    send = tm.send_keys if send is None else send
    resolve_cwd = tm.pane_current_path if resolve_cwd is None else resolve_cwd
    target = state.ref.target
    common = dict(target=target, verdict=state.verdict, evidence=state.evidence)

    if state.ref.claude_pid is not None:
        return ReviveAction(
            skipped="a claude still owns this pane — keystrokes would land in its prompt",
            cwd=state.ref.cwd,
            **common,
        )
    where = cwd or state.ref.cwd or resolve_cwd(target)
    if not where:
        return ReviveAction(
            skipped="no directory to restart in (pass cwd=)", cwd=None, **common
        )
    keys = server_mode_command(where)
    remaining = rate_guard.wait_remaining(target)
    if remaining > 0:
        return ReviveAction(
            skipped=f"rate-limited ({remaining:.0f}s)", cwd=where, **common
        )
    if not apply:
        return ReviveAction(keys=keys, skipped="dry-run", cwd=where, **common)
    rate_guard.record(target)
    send(target, *keys)
    return ReviveAction(keys=keys, sent=True, cwd=where, **common)


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #

#: Verdicts worth a human's eye, in the order a report should present them.
REPORT_ORDER = (
    RECONNECTABLE,
    SERVER_MODE,
    HELD_ELSEWHERE,
    NEEDS_HUMAN,
    API_STALLED,
    RECONNECTING,
    NO_CLAUDE,
    UNKNOWN,
    CONNECTED,
)


def format_actions(actions: Sequence[ReviveAction]) -> str:
    """One line per pane, most actionable first.

    >>> print(format_actions([
    ...     ReviveAction('a:@1.%1', RECONNECTABLE, keys=RECONNECT_KEYS,
    ...                  skipped='dry-run', evidence='/rc failed'),
    ...     ReviveAction('b:@1.%1', CONNECTED),
    ... ]))
    reconnectable  a:@1.%1  would send /remote-control  [/rc failed]
    connected      b:@1.%1
    """
    order = {verdict: i for i, verdict in enumerate(REPORT_ORDER)}
    width = max((len(a.verdict) for a in actions), default=0)
    lines = []
    for action in sorted(actions, key=lambda a: (order.get(a.verdict, 99), a.target)):
        bits = [f"{action.verdict:<{width}}", action.target]
        if action.sent:
            bits.append(f"sent {action.keys[0]}")
        elif action.would_send:
            bits.append(f"would send {action.keys[0]}")
        elif action.skipped and action.skipped != "not actionable":
            bits.append(action.skipped)
        if action.evidence:
            bits.append(f"[{action.evidence}]")
        lines.append("  ".join(bits))
    return "\n".join(lines)
