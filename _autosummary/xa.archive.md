# xa.archive

Postmortem archive of Claude Code sessions spawned by `xa`.

Every session that `xa` spawns emits `created` + `url_acquired` events
into an append-only JSONL log and its tmux pane is tee’d to a per-session
file via `tmux pipe-pane`. When a session disappears from the live list,
`reconcile()` appends a `gone` event with an inferred death time and a
classified reason.

Death-reason taxonomy (most specific wins):

- `replaced`    — same tmux name, different `tmux_created_ts` (the
  original died and got taken over by a new session of the same name).
- `missing`     — no pane log exists (session predates our logging or
  the log was cleaned up).
- `interrupted` — transcript shows the user-interrupt marker AND the
  pane tail shows a clean exit. **Ambiguous**: the same marker fires on
  bridge-WebSocket resets (phone standby, reconnect), not only on human
  ESC. Don’t derive user intent from this alone.
- `oom_killed`  — last tool exited with 137 (SIGKILL) AND the pane log
  tail contains a kernel/shell OOM marker (`Killed`, `Out of memory`,
  `MemoryError`). Strongest single signal we can derive without root —
  the kernel’s “Killed” message rides through the same tty bash uses to
  print SIGKILL notices, so it ends up in the tee’d pane log.
- `tool_crash`  — pane tail shows clean exit AND the last tool use
  exited with a non-zero code (and we couldn’t promote to `oom_killed`).
- `clean_exit`  — pane tail contains `"Resume this session with:"`.
- `abrupt`      — killed / crashed / bridge-dropped with no clean-exit
  marker.

### Functions

| `append_created`(events, \*, id, name, cwd, ...)                                                    |                                                                        |
|-----------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|
| `append_gone`(events, \*, id, name, reason[, ...])                                                  |                                                                        |
| [`append_hidden`](#xa.archive.append_hidden)(events, \*, id, hidden)              | Mark an archived session as hidden (or un-hide it).                    |
| [`append_label`](#xa.archive.append_label)(events, \*, id, label)                | Set or clear a user-supplied display label for a session.              |
| `append_url_acquired`(events, \*, id, name[, ...])                                                  |                                                                        |
| [`classify_death`](#xa.archive.classify_death)(pane_kind, \*[, replaced, ...])     | Pick the most specific death reason from available signals.            |
| [`overlays`](#xa.archive.overlays)(events)                                   | Fold `labeled` / `hidden` events into `{id: {label, hidden}}`.         |
| [`reconcile`](#xa.archive.reconcile)(events, panes, live_sessions, \*[, ...]) | Emit `gone` events for archived sessions missing from `live_sessions`. |
| [`records`](#xa.archive.records)(events, panes)                             | Return per-session summaries, newest-first (by creation time).         |
| [`synthesize_diagnosis`](#xa.archive.synthesize_diagnosis)(\*, state[, reason, ...])     | One-paragraph plain-English hint about what likely happened.           |

### Classes

| [`ArchiveRecord`](#xa.archive.ArchiveRecord)(id, name, cwd, created, url, ...)   |                                                                 |
|----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------|
| [`PaneInspection`](#xa.archive.PaneInspection)(death_ts, kind, oom_markers, tail) | Read-once view of a pane log tail used by death classification. |

### *class* xa.archive.ArchiveRecord(id, name, cwd, created, url, gone, gone_detected, gone_reason, pane_log_bytes, claude_session_id, forensics, label=None, hidden=False)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

### *class* xa.archive.PaneInspection(death_ts, kind, oom_markers, tail)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Read-once view of a pane log tail used by death classification.

### xa.archive.append_hidden(events, , id, hidden)

Mark an archived session as hidden (or un-hide it).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.archive.append_label(events, , id, label)

Set or clear a user-supplied display label for a session.

`id` can be an archive id, a tmux session name, or a
`claude_session_id` — whatever key the caller will use to look it
up later. Empty-string or `None` label clears any prior label.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.archive.classify_death(pane_kind, , replaced=False, forensics=None, oom_markers=())

Pick the most specific death reason from available signals.

`oom_markers` is the tuple of OOM-shaped strings observed in the
pane tail (see `_OOM_PANE_MARKERS`). When the last tool exited
with 137 and at least one marker is present, we promote the verdict
to `oom_killed` — that pair is the strongest single signal we can
get without reading kernel logs (which would need root and is not
portable).

* **Return type:**
  [`Literal`](https://docs.python.org/3/library/typing.html#typing.Literal)[`'clean_exit'`, `'abrupt'`, `'interrupted'`, `'tool_crash'`, `'oom_killed'`, `'replaced'`, `'missing'`]

### xa.archive.overlays(events)

Fold `labeled` / `hidden` events into `{id: {label, hidden}}`.

Later events win. Useful when rendering live sessions too — callers
look up by whatever id they know (archive id, claude_session_id,
tmux name) and apply the overlay if present.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### xa.archive.reconcile(events, panes, live_sessions, , claude_home=PosixPath('/home/runner/.claude'))

Emit `gone` events for archived sessions missing from `live_sessions`.

Returns the list of freshly-emitted events (handy for tests). Idempotent:
calling twice with the same live list produces no new events the
second time.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### xa.archive.records(events, panes)

Return per-session summaries, newest-first (by creation time).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`ArchiveRecord`](#xa.archive.ArchiveRecord)]

### xa.archive.synthesize_diagnosis(, state, reason=None, forensics=None, oom_markers=())

One-paragraph plain-English hint about what likely happened.

Designed to be useful to both a human reading the UI and an LLM agent
deciding whether to retry or escalate. The hint surfaces the actionable
bit (e.g., “add swap”, “re-run with –resume”) rather than just naming
the verdict — the structured fields next to it already do that.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
