# xa.revive

Detect — and reconnect — Claude Code panes whose Remote Control dropped.

Remote Control gives up on its own: after a few minutes of failed reconnects,
or roughly thirty minutes of failed presence heartbeats, it stops trying and
leaves the local session running but unreachable from claude.ai. The usual
cause is a network change (moving between networks, toggling a VPN) that
produces a transient HTTP 403 — the same change that clears it, usually
within a minute or two.

Two facts shape everything below, and both were verified rather than assumed:

- \*\*Two observables, and both were checked against a live session rather
  than assumed.\*\* The footer carries a `/rc` *pill* that is present while
  Remote Control is connected (`/rc`, or `/rc active` when verbose) and
  **disappears** when it is not — so the pill’s absence, not its presence,
  is what marks a dropped session. The ephemeral session file
  (`~/.claude/sessions/<pid>.json`) agrees from the other side: its
  `bridgeSessionId` is a real id while connected and `null` once it is
  not. The transcript JSONL never mentions Remote Control at all, so there
  is no third source.
- **\`\`tmux send-keys\`\` is the only way to act.** `/remote-control` is a
  built-in slash command; the CLI exposes no subcommand and no control
  request that reconnects a *running* session (checked against
  `claude --help` and `claude remote-control --help`, 2.1.251 — the only
  session-shaped verbs there are `agents`/`attach`/`logs`/`stop`/
  `rm`/`respawn`, all of which are about background agents).

The detection phrases in [`MARKERS`](#xa.revive.MARKERS) are transcribed from the strings
embedded in the `claude` 2.1.251 binary, not guessed from screenshots.
They are data, and [`DEFAULT_RULES`](#xa.revive.DEFAULT_RULES) is injectable, so a wording change
upstream is a one-line edit rather than a rewrite.

Four things this module refuses to do, each deliberate:

- **It never revives a session held elsewhere.** When a session was taken
  over or ended from another device, the TUI says so (WebSocket close code
  4090) and *omits* its “run /remote-control” advice. Sending the command
  > anyway would steal the session back from the user’s phone mid-sentence.

  [`HELD_ELSEWHERE`](#xa.revive.HELD_ELSEWHERE) is therefore tested before [`RECONNECTABLE`](#xa.revive.RECONNECTABLE)
  : and acting on it stays opt-in.
- **It never resends a prompt.** An API-stalled pane is reported and left
  alone: the fix there is a real model call that can duplicate work.
- **It never types into a pane waiting on a human.** A permission dialog or
  a `/login` prompt consumes keystrokes as *answers* — sending
  `/remote-control` into an open “Do you want to proceed?” selector would
  answer it. Those panes classify as [`NEEDS_HUMAN`](#xa.revive.NEEDS_HUMAN) and are skipped.
- **It does nothing at all unless asked.** `apply=True` is explicit;
  every entry point is a dry run by default.

Typical use:

```default
from xa.revive import SessionPanes, revive

for action in revive(panes=SessionPanes()):      # dry run
    print(action.target, action.verdict, action.skipped)
```

Discovery does **not** enumerate tmux and guess which pane holds a claude.
It reads the live ephemeral session files, each of which records its own
pane as `"tmux": "session:@window.%pane"`, so the pane refs are exact and
`pane_current_command` never enters the decision. See [`local_panes()`](#xa.revive.local_panes).

### Module Attributes

| [`NO_CLAUDE`](#xa.revive.NO_CLAUDE)             | No claude process behind the pane — a shell prompt, or a session that died.                                                                                    |
|------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`HELD_ELSEWHERE`](#xa.revive.HELD_ELSEWHERE)        | Taken over or ended from another device.                                                                                                                       |
| [`CONNECTED`](#xa.revive.CONNECTED)             | Remote Control is up.                                                                                                                                          |
| [`RECONNECTING`](#xa.revive.RECONNECTING)          | Remote Control is retrying right now.                                                                                                                          |
| [`NEEDS_HUMAN`](#xa.revive.NEEDS_HUMAN)           | Blocked on a human — an open permission dialog, or anything else that is eating keystrokes.                                                                    |
| [`NEEDS_LOGIN`](#xa.revive.NEEDS_LOGIN)           | claude on this host is logged out.                                                                                                                             |
| [`NEEDS_TRUST`](#xa.revive.NEEDS_TRUST)           | the workspace-trust prompt is open.                                                                                                                            |
| [`API_STALLED`](#xa.revive.API_STALLED)           | The API call is wedged.                                                                                                                                        |
| [`SERVER_MODE`](#xa.revive.SERVER_MODE)           | A `claude remote-control` *server* died.                                                                                                                       |
| [`RECONNECTABLE`](#xa.revive.RECONNECTABLE)         | Dropped and recoverable — the one verdict [`revive()`](#xa.revive.revive) acts on.                                                   |
| [`UNKNOWN`](#xa.revive.UNKNOWN)               | The pane says nothing either way.                                                                                                                              |
| [`VERDICTS`](#xa.revive.VERDICTS)              | Every verdict [`DEFAULT_RULES`](#xa.revive.DEFAULT_RULES) can produce.                                                                      |
| [`BLOCKED_ON_HUMAN`](#xa.revive.BLOCKED_ON_HUMAN)      | Verdicts that mean "a human must act on the host".                                                                                                             |
| [`ACTIONABLE`](#xa.revive.ACTIONABLE)            | Verdicts [`revive()`](#xa.revive.revive) will send `/remote-control` to.                                                             |
| [`MARKERS`](#xa.revive.MARKERS)               | Substring markers per verdict.                                                                                                                                 |
| [`RC_PILL`](#xa.revive.RC_PILL)               | The footer *pill*.                                                                                                                                             |
| [`DEFAULT_RULES`](#xa.revive.DEFAULT_RULES)         | Ordered `(verdict, predicate)` pairs; **first match wins**.                                                                                                    |
| [`PROMPT_LINE`](#xa.revive.PROMPT_LINE)           | the prompt glyph, then U+00A0, then whatever the user has typed. <br/><br/>```<br/>``<br/>```<br/><br/>> \`\` is the plain-ASCII rendering some terminals get. |
| [`TYPEABLE_STATUS`](#xa.revive.TYPEABLE_STATUS)       | Statuses in which a keystroke is a *command*.                                                                                                                  |
| [`DFLT_MIN_INTERVAL_SEC`](#xa.revive.DFLT_MIN_INTERVAL_SEC) | Long enough that a cron tick can never turn into a keystroke storm, short enough that a real network change is followed within one settling period.            |
| [`RECONNECT_KEYS`](#xa.revive.RECONNECT_KEYS)        | The keystrokes that reconnect a REPL session.                                                                                                                  |
| [`REPORT_ORDER`](#xa.revive.REPORT_ORDER)          | Verdicts worth a human's eye, in the order a report should present them.                                                                                       |

### Functions

| [`classify`](#xa.revive.classify)(probe, \*[, rules])                      | Return the verdict for `probe`.                                                                              |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------|
| [`evidence_for`](#xa.revive.evidence_for)(probe, verdict, \*[, rules])         | The marker that earned `verdict`, for a report a human has to trust.                                         |
| [`format_actions`](#xa.revive.format_actions)(actions)                           | One line per pane, most actionable first.                                                                    |
| [`has_live_claude`](#xa.revive.has_live_claude)(ref)                              | Default relevance test: the recorded pid is a *claude* that still exists.                                    |
| [`hint_for`](#xa.revive.hint_for)(verdict, \*[, tmux_name])                | Human fix-it line for a verdict, or `None` when there is nothing to say.                                     |
| [`local_panes`](#xa.revive.local_panes)(\*[, claude_home])                    | Yield one [`PaneRef`](#xa.revive.PaneRef) per live claude that records a tmux pane. |
| [`prompt_is_clear`](#xa.revive.prompt_is_clear)(tail)                             | True when the pane's input line is empty and safe to type into.                                              |
| [`refusal_to_type`](#xa.revive.refusal_to_type)(state)                            | Why typing into `state` would be unsafe, or `None` if it is fine.                                            |
| [`restart_server_mode`](#xa.revive.restart_server_mode)(state, \*[, cwd, apply, ...]) | Restart a died `claude remote-control` server in its own directory.                                          |
| [`revive`](#xa.revive.revive)(\*[, panes, apply, ...])                   | Reconnect every dropped pane.                                                                                |
| [`server_mode_command`](#xa.revive.server_mode_command)(cwd)                          | Keystrokes that restart a Remote Control *server* in `cwd`.                                                  |

### Classes

| [`PaneRef`](#xa.revive.PaneRef)(target[, host, claude_pid, cwd, ...])     | Identity of one candidate pane — everything known *without* reading it.   |
|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| [`PaneState`](#xa.revive.PaneState)(ref, tail, verdict[, evidence])         | A pane, as read and judged.                                               |
| [`Probe`](#xa.revive.Probe)(text[, ref])                                | The complete, pure input to a classification rule.                        |
| [`RateGuard`](#xa.revive.RateGuard)(\*[, store, min_interval_sec, clock])   | Refuses to touch the same pane twice inside `min_interval_sec`.           |
| [`ReviveAction`](#xa.revive.ReviveAction)(target, verdict[, keys, sent, ...])  | What was (or would be) done to one pane, and why.                         |
| [`SessionPanes`](#xa.revive.SessionPanes)(\*[, panes, is_relevant, tail, ...]) | Live view of the panes worth judging: `{tmux target: PaneState}`.         |

### xa.revive.ACTIONABLE *= frozenset({'reconnectable'})*

Verdicts [`revive()`](#xa.revive.revive) will send `/remote-control` to. `HELD_ELSEWHERE`
is deliberately absent — it is reachable only via `include_held_elsewhere`.

### xa.revive.API_STALLED *= 'api_stalled'*

The API call is wedged. Reported, never acted on (the fix is a new prompt).

### xa.revive.BLOCKED_ON_HUMAN *= frozenset({'needs_human', 'needs_login', 'needs_trust'})*

Verdicts that mean “a human must act on the host”. All are blocked on a
person, none are actionable by [`revive()`](#xa.revive.revive), and each has a fix-it line
in [`hint_for()`](#xa.revive.hint_for).

### xa.revive.CONNECTED *= 'connected'*

Remote Control is up. Nothing to do.

### xa.revive.DEFAULT_RULES *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Callable](https://docs.python.org/3/library/typing.html#typing.Callable)[[[Probe](#xa.revive.Probe)], [bool](https://docs.python.org/3/builtins/functions.html#bool)]], ...]* *= (('no_claude', <function \_no_claude>), ('held_elsewhere', <function \_any_of.<locals>._pred>), ('reconnecting', <function \_any_of.<locals>._pred>), ('connected', <function \_pill_present>), ('connected', <function \_any_of.<locals>._pred>), ('needs_login', <function \_any_of.<locals>._pred>), ('needs_trust', <function \_any_of.<locals>._pred>), ('needs_human', <function \_any_of.<locals>._pred>), ('api_stalled', <function \_any_of.<locals>._pred>), ('server_mode', <function \_any_of.<locals>._pred>), ('reconnectable', <function \_any_of.<locals>._pred>), ('reconnectable', <function \_bridge_down>), ('unknown', <function \_always>))*

Ordered `(verdict, predicate)` pairs; **first match wins**.

The order is the policy, not a style choice, and two things decide it.

*Safety*: `NO_CLAUDE` leads, so a dead session’s scrollback can never be
read as live state — the pane this was written against still showed a
permission dialog and a disconnect notice above a shell prompt, all true
once and none true now. `HELD_ELSEWHERE` precedes every reconnect
verdict, per the rule that a session picked up on another device is never
taken back without asking.

*Freshness*: the pill rules are **live** state and the phrase rules are
**history**. A session that dropped and recovered still carries
“Remote Control disconnected” in its scrollback for as long as it stays on
screen, so a text rule placed above the pill would report a healthy
session as broken until the line scrolled away.

### xa.revive.DFLT_MIN_INTERVAL_SEC *= 600.0*

Long enough that a cron tick can never turn into a keystroke storm, short
enough that a real network change is followed within one settling period.

### xa.revive.HELD_ELSEWHERE *= 'held_elsewhere'*

Taken over or ended from another device. Never reconnect without opt-in.

### xa.revive.MARKERS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), ...]]* *= {'api_stalled': ('api error', 'retrying in ', 'overloaded'), 'connected': ('remote control active',), 'held_elsewhere': ('ended or archived from another device', 'no longer the active worker for the session', 'already has remote control for this conversation', 'run /remote-control to move it to this terminal'), 'needs_human': ('do you want to proceed?', 'esc to cancel'), 'needs_login': ('run /login', 'please log in', 'select login method', 'oauth token has expired', 'invalid api key', 'unknown command: /remote-control'), 'needs_trust': ('trust this folder', 'do you trust the files'), 'reconnectable': ('remote control disconnected', 'remote control not started here', 'run /remote-control to retry', '/remote-control is no longer active', '/rc failed'), 'reconnecting': ('/rc reconnecting',), 'server_mode': ('re-run \`claude remote-control\` to try again',)}*

Substring markers per verdict. Lowercase; matched against a lowered tail.

### xa.revive.NEEDS_HUMAN *= 'needs_human'*

Blocked on a human — an open permission dialog, or anything else that is
eating keystrokes. See [`NEEDS_LOGIN`](#xa.revive.NEEDS_LOGIN) / [`NEEDS_TRUST`](#xa.revive.NEEDS_TRUST) for the
two causes specific enough to tell the user how to fix.

### xa.revive.NEEDS_LOGIN *= 'needs_login'*

claude on this host is logged out.
Login state is per-machine, so one `/login` fixes every session on it.

* **Type:**
  Blocked on a human, specifically

### xa.revive.NEEDS_TRUST *= 'needs_trust'*

the workspace-trust prompt is open.

* **Type:**
  Blocked on a human, specifically

### xa.revive.NO_CLAUDE *= 'no_claude'*

No claude process behind the pane — a shell prompt, or a session that died.

### xa.revive.PROMPT_LINE *= re.compile('(?m)^(?:❯|>)[\\xa0 ](.\*)$', re.MULTILINE)*

the prompt glyph, then U+00A0, then whatever the user
has typed. 

```
``
```

> \`\` is the plain-ASCII rendering some terminals get.

* **Type:**
  The TUI’s input line

### *class* xa.revive.PaneRef(target, host='local', claude_pid=None, cwd=None, session_id=None, bridge_session_id=None, name=None, status=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Identity of one candidate pane — everything known *without* reading it.

`target` is a tmux target: a full pane ref (`session:@window.%pane`,
which is what the ephemeral session file records) or a bare session name.

#### status *: [str](https://docs.python.org/3/builtins/stdtypes.html#str) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

`idle` / `busy` / `waiting` as the session last reported it.
`None` on a claude too old to record one.

### *class* xa.revive.PaneState(ref, tail, verdict, evidence=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A pane, as read and judged. The value type of [`SessionPanes`](#xa.revive.SessionPanes).

### *class* xa.revive.Probe(text, ref=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The complete, pure input to a classification rule.

`text` is the pane tail, lowercased once so every rule can use a plain
substring test.

### xa.revive.RC_PILL *= re.compile('(?m)^.\*(?<![\\\\w/])/rc(?: active)?\\\\s\*$', re.MULTILINE)*

The footer *pill*. Present while Remote Control is connected — `/rc` on
its own right-aligned line, or `/rc active` in verbose mode — and gone
the moment it is not. Anchored to a line end because a bare `"/rc" in
text` would also match a file path ending in `src/rc`.

The direction matters and is easy to get backwards: the pill is evidence
of *health*. Verified by disconnecting a live session and watching the
line vanish (claude 2.1.251).

### xa.revive.RECONNECTABLE *= 'reconnectable'*

Dropped and recoverable — the one verdict [`revive()`](#xa.revive.revive) acts on.

### xa.revive.RECONNECTING *= 'reconnecting'*

Remote Control is retrying right now. Leave it alone; it may well succeed.

### xa.revive.RECONNECT_KEYS *= ('/remote-control', 'Enter')*

The keystrokes that reconnect a REPL session. `Enter` submits.

### xa.revive.REPORT_ORDER *= ('reconnectable', 'server_mode', 'held_elsewhere', 'needs_human', 'api_stalled', 'reconnecting', 'no_claude', 'unknown', 'connected')*

Verdicts worth a human’s eye, in the order a report should present them.

### *class* xa.revive.RateGuard(\*, store=None, min_interval_sec=600.0, clock=<built-in function time>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Refuses to touch the same pane twice inside `min_interval_sec`.

Without this a cron tick, a hook and an impatient human can each send
`/remote-control` into the same pane seconds apart. Backed by any
`{key: bytes}` mapping — the default is xa’s on-disk
[`FileStore`](xa.store.html.md#xa.store.FileStore), so the interval survives process exit,
which is the entire point.

```pycon
>>> clock = iter([100.0, 200.0, 800.0]).__next__
>>> guard = RateGuard(store={}, min_interval_sec=600.0, clock=clock)
>>> guard.allow('work:@1.%1')     # never attempted
True
>>> guard.record('work:@1.%1')    # stamped at t=100
>>> guard.allow('work:@1.%1')     # t=200 — 100s later, still held off
False
>>> guard.allow('work:@1.%1')     # t=800 — 700s later, released
True
```

#### wait_remaining(target)

Seconds left before `target` may be touched again (0.0 if now).

* **Return type:**
  [`float`](https://docs.python.org/3/builtins/functions.html#float)

### *class* xa.revive.ReviveAction(target, verdict, keys=(), sent=False, skipped=None, evidence=None, cwd=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What was (or would be) done to one pane, and why.

#### *property* would_send *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

True when only `apply=False` stood in the way.

### xa.revive.SERVER_MODE *= 'server_mode'*

A `claude remote-control` *server* died. See [`restart_server_mode()`](#xa.revive.restart_server_mode).

### *class* xa.revive.SessionPanes(\*, panes=<function local_panes>, is_relevant=<function has_live_claude>, tail=<function \_capture>, rules=(('no_claude', <function \_no_claude>), ('held_elsewhere', <function \_any_of.<locals>._pred>), ('reconnecting', <function \_any_of.<locals>._pred>), ('connected', <function \_pill_present>), ('connected', <function \_any_of.<locals>._pred>), ('needs_login', <function \_any_of.<locals>._pred>), ('needs_trust', <function \_any_of.<locals>._pred>), ('needs_human', <function \_any_of.<locals>._pred>), ('api_stalled', <function \_any_of.<locals>._pred>), ('server_mode', <function \_any_of.<locals>._pred>), ('reconnectable', <function \_any_of.<locals>._pred>), ('reconnectable', <function \_bridge_down>), ('unknown', <function \_always>)))

Bases: [`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)

Live view of the panes worth judging: `{tmux target: PaneState}`.

Every source of truth is injected, which is what lets the whole
classification path be tested without a tmux server:

- `panes` — callable returning [`PaneRef`](#xa.revive.PaneRef) objects (default:
  [`local_panes()`](#xa.revive.local_panes));
- `is_relevant` — per-ref filter (default: [`has_live_claude()`](#xa.revive.has_live_claude));
- `tail` — callable reading a ref’s pane text (default: tmux capture);
- `rules` — the classification table (default: [`DEFAULT_RULES`](#xa.revive.DEFAULT_RULES)).

Pane text is read lazily and cached, so building the mapping costs
nothing and iterating it costs one capture per pane.

```pycon
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
```

#### by_verdict()

`{verdict: [target, ...]}` — reads every pane.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

#### refresh()

Drop every cached ref and pane read. Returns self, for chaining.

* **Return type:**
  [`SessionPanes`](#xa.revive.SessionPanes)

### xa.revive.TYPEABLE_STATUS *= frozenset({'idle', None})*

Statuses in which a keystroke is a *command*. Anything else — `busy`,
`waiting` — means the session is mid-turn or holding a dialog, and typed
text would be queued into it. `None` is a claude too old to say.

### xa.revive.UNKNOWN *= 'unknown'*

The pane says nothing either way. Never acted on.

### xa.revive.VERDICTS *= frozenset({'api_stalled', 'connected', 'held_elsewhere', 'needs_human', 'needs_login', 'needs_trust', 'no_claude', 'reconnectable', 'reconnecting', 'server_mode', 'unknown'})*

Every verdict [`DEFAULT_RULES`](#xa.revive.DEFAULT_RULES) can produce. Custom rules may add more.

### xa.revive.classify(probe, \*, rules=(('no_claude', <function \_no_claude>), ('held_elsewhere', <function \_any_of.<locals>._pred>), ('reconnecting', <function \_any_of.<locals>._pred>), ('connected', <function \_pill_present>), ('connected', <function \_any_of.<locals>._pred>), ('needs_login', <function \_any_of.<locals>._pred>), ('needs_trust', <function \_any_of.<locals>._pred>), ('needs_human', <function \_any_of.<locals>._pred>), ('api_stalled', <function \_any_of.<locals>._pred>), ('server_mode', <function \_any_of.<locals>._pred>), ('reconnectable', <function \_any_of.<locals>._pred>), ('reconnectable', <function \_bridge_down>), ('unknown', <function \_always>)))

Return the verdict for `probe`. Pure — no I/O, no tmux, no clock.

A session whose footer pill is gone and whose session file records no
bridge id has dropped:

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> dropped = PaneRef(target='w:@1.%1', claude_pid=42)
>>> classify(Probe(text='conversation text\n❯ ', ref=dropped))
'reconnectable'
```

One still carrying the pill has not, whatever its scrollback remembers:

```pycon
>>> live = PaneRef(target='w:@1.%1', claude_pid=42, bridge_session_id='s')
>>> classify(Probe(text='remote control disconnected\n     /rc', ref=live))
'connected'
```

A session someone picked up on their phone is never reconnectable, even
though the pane also says it disconnected:

```pycon
>>> taken = 'remote control disconnected\nended or archived from another device'
>>> classify(Probe(text=taken, ref=dropped))
'held_elsewhere'
```

Neither is one sitting on a permission dialog:

```pycon
>>> classify(Probe(text='/rc failed\ndo you want to proceed?', ref=live))
'needs_human'
```

And a healthy, quiet, connected pane is simply left alone:

```pycon
>>> classify(Probe(text='just some conversation\n  /rc', ref=live))
'connected'
```

### xa.revive.evidence_for(probe, verdict, \*, rules=(('no_claude', <function \_no_claude>), ('held_elsewhere', <function \_any_of.<locals>._pred>), ('reconnecting', <function \_any_of.<locals>._pred>), ('connected', <function \_pill_present>), ('connected', <function \_any_of.<locals>._pred>), ('needs_login', <function \_any_of.<locals>._pred>), ('needs_trust', <function \_any_of.<locals>._pred>), ('needs_human', <function \_any_of.<locals>._pred>), ('api_stalled', <function \_any_of.<locals>._pred>), ('server_mode', <function \_any_of.<locals>._pred>), ('reconnectable', <function \_any_of.<locals>._pred>), ('reconnectable', <function \_bridge_down>), ('unknown', <function \_always>)))

The marker that earned `verdict`, for a report a human has to trust.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

```pycon
>>> ref = PaneRef(target='w:@1.%1', claude_pid=1)
>>> evidence_for(Probe(text='/rc failed', ref=ref), 'reconnectable')
'/rc failed'
```

### xa.revive.format_actions(actions)

One line per pane, most actionable first.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> print(format_actions([
...     ReviveAction('a:@1.%1', RECONNECTABLE, keys=RECONNECT_KEYS,
...                  skipped='dry-run', evidence='/rc failed'),
...     ReviveAction('b:@1.%1', CONNECTED),
... ]))
reconnectable  a:@1.%1  would send /remote-control  [/rc failed]
connected      b:@1.%1
```

### xa.revive.has_live_claude(ref)

Default relevance test: the recorded pid is a *claude* that still exists.

Identity, not just liveness — a stale ephemeral file whose pid got
recycled must never direct keystrokes at an unrelated process.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### xa.revive.hint_for(verdict, , tmux_name=None)

Human fix-it line for a verdict, or `None` when there is nothing to say.

SSOT for the wording shown by the CLI, the web UI and the HTTP API, so
the three can’t drift. Only the verdicts a person can actually clear get
a line; `connected`/`unknown`/`reconnectable` return `None`
(nothing to do, nothing known, or xa’s own job respectively).

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

```pycon
>>> hint_for(CONNECTED) is None
True
>>> hint_for(NEEDS_TRUST, tmux_name='sess').startswith('Claude is waiting')
True
```

### xa.revive.local_panes(, claude_home=PosixPath('/home/runner/.claude'))

Yield one [`PaneRef`](#xa.revive.PaneRef) per live claude that records a tmux pane.

Derived from the ephemeral session files rather than from
`tmux list-panes`: each file names its own pane, so no heuristic has
to decide whether a pane holds a claude. A session not under tmux (every
session on a machine that runs claude in terminal tabs) simply has no
`tmux` key and is skipped — there is no pane to send keys to.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`PaneRef`](#xa.revive.PaneRef)]

### xa.revive.prompt_is_clear(tail)

True when the pane’s input line is empty and safe to type into.

This is the difference between reconnecting a session and destroying the
instruction its owner left half-typed: `send-keys` appends to whatever
is already in the buffer, so `/remote-control` sent at a pane whose
prompt reads `yes, port it into the repo` submits
`yes, port it into the repo/remote-control`.

Conservative by construction — a pane whose prompt line cannot be found
is reported as not clear, because “I could not see the buffer” and “the
buffer is empty” must never collapse into the same answer.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

```pycon
>>> prompt_is_clear('---\n❯ \n---')
True
>>> prompt_is_clear('❯ yes, port it into the repo')
False
>>> prompt_is_clear('no prompt line here at all')
False
```

### xa.revive.refusal_to_type(state)

Why typing into `state` would be unsafe, or `None` if it is fine.

Two independent gates, because each catches what the other misses: the
reported status catches a session mid-turn whose buffer happens to be
empty, and the buffer check catches an idle session holding text the
status knows nothing about.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

```pycon
>>> ref = PaneRef(target='t', claude_pid=1, status='idle')
>>> refusal_to_type(PaneState(ref=ref, tail='❯ ', verdict=RECONNECTABLE))
>>> busy = replace(ref, status='busy')
>>> refusal_to_type(PaneState(ref=busy, tail='❯ ', verdict=RECONNECTABLE))
'session is busy'
```

### xa.revive.restart_server_mode(state, , cwd=None, apply=False, rate_guard=None, send=None, resolve_cwd=None)

Restart a died `claude remote-control` server in its own directory.

The directory is **not** recorded by this package. It is read back from
what already knows it — the pane’s own working directory via tmux, or
the ephemeral session file when a claude is still alive — and the
session identity is left to `claude remote-control -c`, which keeps
its own per-directory record. Adding a fourth place to write it down
would only create something to drift.

Refuses whenever a live claude still owns the pane: this sends a *shell*
command, and a running TUI would take it as prompt text.

* **Return type:**
  [`ReviveAction`](#xa.revive.ReviveAction)

```pycon
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
```

```pycon
>>> live = PaneState(ref=replace(ref, claude_pid=9), tail='', verdict=SERVER_MODE)
>>> restart_server_mode(live, apply=True).skipped
'a claude still owns this pane — keystrokes would land in its prompt'
```

### xa.revive.revive(, panes=None, apply=False, include_held_elsewhere=False, rate_guard=None, send=None, actionable=frozenset({'reconnectable'}))

Reconnect every dropped pane. \*\*Dry run unless `apply=True`.\*\*

Returns one [`ReviveAction`](#xa.revive.ReviveAction) per pane examined — including the ones
left alone, each carrying the reason, because “nothing happened” is only
useful next to what was looked at.

`include_held_elsewhere=True` opts into reconnecting sessions that were
taken over from another device. That steals the session back from
whatever device holds it; it is off by default and should stay a
deliberate keystroke.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`ReviveAction`](#xa.revive.ReviveAction)]

```pycon
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
```

### xa.revive.server_mode_command(cwd)

Keystrokes that restart a Remote Control *server* in `cwd`.

`claude remote-control -c` reattaches the session the command last
recorded **for that directory** (within ~4h), which is why the directory
— and nothing else — is what a restart needs to know.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]

```pycon
>>> server_mode_command('/root/py/proj')
('cd /root/py/proj && claude remote-control -c', 'Enter')
>>> server_mode_command(None)
('claude remote-control -c', 'Enter')
```
