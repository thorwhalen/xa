# xa

xa — tools for managing remote Claude Code sessions.

### Functions

| [`encode_project_slug`](#xa.encode_project_slug)(cwd)                           | Encode a cwd into the `~/.claude/projects/` slug form.                     |
|-----------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------|
| [`history_iter`](#xa.history_iter)(\*[, claude_home])                    | Yield entries from `~/.claude/history.jsonl` in file order (oldest first). |
| [`iter_ephemeral_sessions`](#xa.iter_ephemeral_sessions)(\*[, claude_home])         | Yield all live ephemeral session dicts.                                    |
| [`iter_project_slugs`](#xa.iter_project_slugs)(\*[, claude_home])              | Yield slugs (directory names) under `~/.claude/projects/`.                 |
| [`iter_transcript_events`](#xa.iter_transcript_events)(path)                       | Yield every event from a transcript JSONL.                                 |
| [`iter_transcript_files`](#xa.iter_transcript_files)(\*[, claude_home, ...])      | Yield transcript JSONL paths, optionally restricted to one project.        |
| [`parse_project_slug`](#xa.parse_project_slug)(slug)                           | Decode a `~/.claude/projects/` slug back to a cwd.                         |
| [`read_ephemeral_session`](#xa.read_ephemeral_session)(pid, \*[, claude_home])     | Read the per-process session file for `pid` if present.                    |
| [`transcript_forensics`](#xa.transcript_forensics)(path)                         | Extract postmortem-relevant facts from the tail of a transcript.           |
| [`transcript_metadata`](#xa.transcript_metadata)(path)                          | Summarise a transcript without holding it all in memory.                   |
| [`transcript_path`](#xa.transcript_path)(cwd, session_id, \*[, ...])        | Return the transcript path for `(cwd, session_id)` if it exists.           |
| [`resolve_bridge_url`](#xa.resolve_bridge_url)(session_name, \*[, ...])        | Return `(url, source)` for a live tmux-hosted claude session.              |
| [`resume_session`](#xa.resume_session)(claude_session_id, \*, cwd[, ...])  | Launch `claude --resume <id>` in a new detached tmux session.              |
| [`spawn_session`](#xa.spawn_session)(name, \*, cwd[, claude_bin, ...])    | Create a detached tmux session running `claude` in `cwd`.                  |
| [`get_session`](#xa.get_session)(session_id, \*[, hosts, claude_home])  | Find one session by full ID or unique prefix.                              |
| [`iter_local_sessions`](#xa.iter_local_sessions)(\*[, claude_home, ...])        | Legacy local-only iterator.                                                |
| [`kill_session`](#xa.kill_session)(session, \*[, hosts])                 | Kill a live `Session`, scoped to what actually belongs to it.              |
| [`list_sessions`](#xa.list_sessions)(\*[, hosts, project, ...])           | Return sessions sorted by recency (live first, then newest-modified).      |
| [`resume`](#xa.resume)(session, \*[, cwd, name, hosts])            | Resume a transcript / archived session on its originating host.            |
| [`default_events_store`](#xa.default_events_store)([state_dir])                  | The event log at `<state_dir>/events.jsonl`.                               |
| [`default_pane_store`](#xa.default_pane_store)([state_dir])                    | Per-session pane logs at `<state_dir>/panes/<id>.log`.                     |
| `append_created`(events, \*, id, name, cwd, ...)                                                    |                                                                            |
| `append_gone`(events, \*, id, name, reason[, ...])                                                  |                                                                            |
| [`append_hidden`](#xa.append_hidden)(events, \*, id, hidden)              | Mark an archived session as hidden (or un-hide it).                        |
| [`append_label`](#xa.append_label)(events, \*, id, label)                | Set or clear a user-supplied display label for a session.                  |
| `append_url_acquired`(events, \*, id, name[, ...])                                                  |                                                                            |
| [`classify_death`](#xa.classify_death)(pane_kind, \*[, replaced, ...])     | Pick the most specific death reason from available signals.                |
| [`overlays`](#xa.overlays)(events)                                   | Fold `labeled` / `hidden` events into `{id: {label, hidden}}`.             |
| [`reconcile`](#xa.reconcile)(events, panes, live_sessions, \*[, ...]) | Emit `gone` events for archived sessions missing from `live_sessions`.     |
| [`records`](#xa.records)(events, panes)                             | Return per-session summaries, newest-first (by creation time).             |
| [`default_hosts`](#xa.default_hosts)()                                    | The out-of-the-box registry: one local host, nothing else.                 |
| `default_config_path`()                                                                             |                                                                            |
| [`load`](#xa.load)([path])                                       | Return `(settings, hosts_registry)` from a config file.                    |
| [`load_hosts`](#xa.load_hosts)([path])                                 | Shortcut: just the host registry.                                          |

### Classes

| [`HistoryEntry`](#xa.HistoryEntry)(cwd, project, display, ...)        | One line from `~/.claude/history.jsonl`.                             |
|--------------------------------------------------------------------------------------------------|----------------------------------------------------------------------|
| [`TranscriptForensics`](#xa.TranscriptForensics)(transcript_path, ...)       | Postmortem facts extracted by walking a transcript from the end.     |
| [`TranscriptMeta`](#xa.TranscriptMeta)(path, session_id, cwd, ...)      | Summary of a transcript JSONL, cheap enough to compute for listings. |
| [`SpawnResult`](#xa.SpawnResult)(name, cwd, claude_pid, ...[, ...])  | What `spawn_session` / `resume_session` return to the caller.        |
| [`Session`](#xa.Session)(id, claude_session_id, ...[, ...])      | Canonical session record — valid across hosts and states.            |
| [`FileStore`](#xa.FileStore)(root, \*[, suffix])                   | Directory of files, accessed by key.                                 |
| [`JsonLinesStore`](#xa.JsonLinesStore)(path)                            | Append-only JSONL file with an `Iterable` reader.                    |
| [`ArchiveRecord`](#xa.ArchiveRecord)(id, name, cwd, created, url, ...) |                                                                      |
| [`HTTPHost`](#xa.HTTPHost)(name, \*, base_url[, auth, ...])       | Client for a remote `xa serve` instance.                             |
| [`Host`](#xa.Host)(\*args, \*\*kwargs)                        | Duck-typed interface every transport satisfies.                      |
| [`LocalHost`](#xa.LocalHost)([name, claude_home, claude_bin, ...]) | The machine `xa` is running on.                                      |
| [`SSHHost`](#xa.SSHHost)(name, \*, host[, user, ...])            | Transcripts via rsync; actions via ssh exec.                         |
| [`Settings`](#xa.Settings)(cache_dir[, stale_threshold_sec, ...]) |                                                                      |

### *class* xa.ArchiveRecord(id, name, cwd, created, url, gone, gone_detected, gone_reason, pane_log_bytes, claude_session_id, forensics, label=None, hidden=False)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

### *class* xa.FileStore(root, , suffix='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Directory of files, accessed by key.

Values are `bytes`. Keys must match `[A-Za-z0-9_.-]+` — this
rejects path traversal attempts like `../etc/passwd`.

#### path_for(key)

Public path accessor (for callers that need to hand the path to tmux).

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### *class* xa.HTTPHost(name, , base_url, auth=None, username=None, password=None, token=None, timeout=30.0)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Client for a remote `xa serve` instance.

#### sync(, force=False)

HTTP sessions are fetched fresh on each listing — no separate sync.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### *class* xa.HistoryEntry(cwd, project, display, pasted_contents)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One line from `~/.claude/history.jsonl`.

### *class* xa.Host(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Duck-typed interface every transport satisfies.

### *class* xa.JsonLinesStore(path)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Append-only JSONL file with an `Iterable` reader.

```pycon
>>> import tempfile, pathlib
>>> with tempfile.TemporaryDirectory() as td:
...     s = JsonLinesStore(pathlib.Path(td) / 'log.jsonl')
...     s.append({'a': 1})
...     s.append({'b': 2})
...     list(s) == [{'a': 1}, {'b': 2}]
True
```

### *class* xa.LocalHost(name='local', , claude_home=PosixPath('/home/runner/.claude'), claude_bin='claude', tmux_bin='tmux', alive_predicate=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The machine `xa` is running on.

#### sync(, force=False)

No-op — local has nothing to sync.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### *class* xa.SSHHost(name, , host, user=None, remote_claude_home='~/.claude', cache_dir=PosixPath('/home/runner/.cache/xa/remotes'), stale_threshold_sec=3600, claude_bin='claude', tmux_bin='tmux', ssh_bin='ssh', rsync_bin='rsync')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Transcripts via rsync; actions via ssh exec.

* **Parameters:**
  * **name** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Logical name used in `Session.host` and elsewhere.
  * **host** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – SSH hostname / alias. Matches cc-sessions: either an
    `~/.ssh/config` alias (`devbox`) or a raw host (`1.2.3.4`).
  * **user** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Optional SSH user for raw hosts.
  * **remote_claude_home** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Remote path to `~/.claude/`. Defaults to `~/.claude` which
    resolves relative to the SSH user’s home on the remote side.
  * **cache_dir** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – Where to stage the rsync’d tree locally.
  * **stale_threshold_sec** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Re-sync when the cached copy is older than this. `0` means
    always sync.

#### spawn(name, , cwd, \*\*opts)

Spawn `tmux new-session -d 's <name>' 'cd <cwd> && exec claude'` over SSH.

URL detection is left to the caller’s next [`sync()`](#xa.SSHHost.sync) + listing —
we don’t round-trip waiting for the bridge URL here (that would
hold an SSH connection open for up to 2 minutes).

* **Return type:**
  [`SpawnResult`](xa.claude_cli.md#xa.claude_cli.SpawnResult)

#### sync(, force=False)

Pull `<remote_claude_home>/projects/` and `/sessions/` into the cache.

Non-existent remote directories are tolerated (rsync returns 23).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### *class* xa.Session(id, claude_session_id, bridge_session_id, host, cwd, project_slug, state, live_pid, tmux_name, name, summary, first_user_message, turn_count, forked_from, created, modified, url, url_source, transcript_path, pre_first_turn=False, attention=None, attention_hint=None, tmux_pane=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Canonical session record — valid across hosts and states.

Fields that don’t apply to a given state are `None`. Discovery
functions return fresh instances; callers should treat it as
immutable.

### *class* xa.Settings(cache_dir, stale_threshold_sec=3600, claude_bin='claude', tmux_bin='tmux')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

### *class* xa.SpawnResult(name, cwd, claude_pid, claude_session_id, bridge_session_id, url, url_source, warning, attention=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What `spawn_session` / `resume_session` return to the caller.

### *class* xa.TranscriptForensics(transcript_path, line_count, last_tool_name, last_tool_command, last_tool_exit_code, last_tool_result_tail, final_assistant_text, user_interrupted)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Postmortem facts extracted by walking a transcript from the end.

`user_interrupted` is the raw marker presence — it is **ambiguous**
(also fires on phone-standby bridge drops), so do not derive user intent
from it alone. Callers should corroborate against pane-log tails.

### *class* xa.TranscriptMeta(path, session_id, cwd, project_slug, summary, custom_title, first_user_message, turn_count, forked_from, created, modified, size_bytes)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Summary of a transcript JSONL, cheap enough to compute for listings.

### xa.append_hidden(events, , id, hidden)

Mark an archived session as hidden (or un-hide it).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.append_label(events, , id, label)

Set or clear a user-supplied display label for a session.

`id` can be an archive id, a tmux session name, or a
`claude_session_id` — whatever key the caller will use to look it
up later. Empty-string or `None` label clears any prior label.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.classify_death(pane_kind, , replaced=False, forensics=None, oom_markers=())

Pick the most specific death reason from available signals.

`oom_markers` is the tuple of OOM-shaped strings observed in the
pane tail (see `_OOM_PANE_MARKERS`). When the last tool exited
with 137 and at least one marker is present, we promote the verdict
to `oom_killed` — that pair is the strongest single signal we can
get without reading kernel logs (which would need root and is not
portable).

* **Return type:**
  [`Literal`](https://docs.python.org/3/library/typing.html#typing.Literal)[`'clean_exit'`, `'abrupt'`, `'interrupted'`, `'tool_crash'`, `'oom_killed'`, `'replaced'`, `'missing'`]

### xa.default_events_store(state_dir=PosixPath('/home/runner/.xa'))

The event log at `<state_dir>/events.jsonl`.

* **Return type:**
  [`JsonLinesStore`](xa.store.md#xa.store.JsonLinesStore)

### xa.default_hosts()

The out-of-the-box registry: one local host, nothing else.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Host`](xa.hosts.md#xa.hosts.Host)]

### xa.default_pane_store(state_dir=PosixPath('/home/runner/.xa'))

Per-session pane logs at `<state_dir>/panes/<id>.log`.

* **Return type:**
  [`FileStore`](xa.store.md#xa.store.FileStore)

### xa.encode_project_slug(cwd)

Encode a cwd into the `~/.claude/projects/` slug form.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> encode_project_slug('/root/py/proj/tt/glossa')
'-root-py-proj-tt-glossa'
>>> encode_project_slug('/')
'-'
```

### xa.get_session(session_id, , hosts=None, claude_home=None)

Find one session by full ID or unique prefix.

Raises `LookupError` if the prefix is ambiguous.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Session`](xa.sessions.md#xa.sessions.Session)]

### xa.history_iter(, claude_home=PosixPath('/home/runner/.claude'))

Yield entries from `~/.claude/history.jsonl` in file order (oldest first).

Useful for cross-project full-text prompt search without loading
every transcript.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`HistoryEntry`](xa.claude_fs.md#xa.claude_fs.HistoryEntry)]

### xa.iter_ephemeral_sessions(, claude_home=PosixPath('/home/runner/.claude'))

Yield all live ephemeral session dicts.

A session file disappears when claude exits, so this reflects
momentary state only. NB: claude deletes the file on *clean* exit
only — a crash / SIGKILL / reboot leaves a stale file behind, so
callers who care about actual liveness must validate each dict with
`ephemeral_session_alive()` (or an equivalent predicate).

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### xa.iter_local_sessions(, claude_home=PosixPath('/home/runner/.claude'), project_slug=None, include_live=True, tmux_bin=None)

Legacy local-only iterator.

Retained for backward compatibility and because some tests construct
sessions directly from a fake `~/.claude/`. Wraps a fresh
[`LocalHost`](#xa.LocalHost).

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`Session`](xa.sessions.md#xa.sessions.Session)]

### xa.iter_project_slugs(, claude_home=PosixPath('/home/runner/.claude'))

Yield slugs (directory names) under `~/.claude/projects/`.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### xa.iter_transcript_events(path)

Yield every event from a transcript JSONL.

Lines that fail to parse are silently skipped — transcripts are
append-only on a live process, so the tail can be partial.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### xa.iter_transcript_files(, claude_home=PosixPath('/home/runner/.claude'), project_slug=None)

Yield transcript JSONL paths, optionally restricted to one project.

Only files whose stem is a valid UUID are yielded — this filters out
the `memory/` subfolder and other non-session artefacts Claude Code
stores alongside transcripts.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]

### xa.kill_session(session, , hosts=None)

Kill a live `Session`, scoped to what actually belongs to it.

Kills the backing tmux session only when it is *dedicated* to this
claude (single pane, claude verified in it) — a claude living in one
window of a shared multi-window workspace must not take the whole
workspace down, so there we SIGTERM the identity-verified pid
instead. Remote hosts keep the name-kill contract (their server
applies its own scoping). Returns a short description of what was
killed (e.g. `"tmux:mysess"` / `"pid:1234"`).

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### xa.list_sessions(, hosts=None, project=None, include_forks=True, include_live=True, state=None, limit=None, claude_home=None, tmux_bin=None)

Return sessions sorted by recency (live first, then newest-modified).

When `hosts` is `None` we default to a single [`LocalHost`](#xa.LocalHost).
If `claude_home` or `tmux_bin` are given, they’re forwarded into
that default [`LocalHost`](#xa.LocalHost) — handy for fixture-based tests.

Filters:

- `project` — substring match against `cwd` (case-insensitive)
- `include_forks=False` — drop sessions with a `forked_from`
- `include_live=False` — skip live discovery (per host)
- `state` — one of `"live"` / `"archived"` / `"transcript_only"`

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Session`](xa.sessions.md#xa.sessions.Session)]

### xa.load(path=None)

Return `(settings, hosts_registry)` from a config file.

Missing file → `(defaults, {"local": LocalHost()})`.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Settings`](xa.config.md#xa.config.Settings), [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### xa.load_hosts(path=None)

Shortcut: just the host registry.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### xa.overlays(events)

Fold `labeled` / `hidden` events into `{id: {label, hidden}}`.

Later events win. Useful when rendering live sessions too — callers
look up by whatever id they know (archive id, claude_session_id,
tmux name) and apply the overlay if present.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### xa.parse_project_slug(slug)

Decode a `~/.claude/projects/` slug back to a cwd.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> parse_project_slug('-root-py-proj-tt-glossa')
'/root/py/proj/tt/glossa'
>>> parse_project_slug('-Users-thorwhalen-tw-server')
'/Users/thorwhalen/tw/server'
```

NB: This transform is lossy — a cwd with literal `-` in segment names
round-trips to a different slug. Claude Code itself has the same
limitation, so we accept it.

### xa.read_ephemeral_session(pid, , claude_home=PosixPath('/home/runner/.claude'))

Read the per-process session file for `pid` if present.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### xa.reconcile(events, panes, live_sessions, , claude_home=PosixPath('/home/runner/.claude'))

Emit `gone` events for archived sessions missing from `live_sessions`.

Returns the list of freshly-emitted events (handy for tests). Idempotent:
calling twice with the same live list produces no new events the
second time.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### xa.records(events, panes)

Return per-session summaries, newest-first (by creation time).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`ArchiveRecord`](xa.archive.md#xa.archive.ArchiveRecord)]

### xa.resolve_bridge_url(session_name, , claude_home=PosixPath('/home/runner/.claude'), tmux_bin='tmux', scrape_lines=400)

Return `(url, source)` for a live tmux-hosted claude session.

Primary path: find the claude descendant pid → read
`~/.claude/sessions/<pid>.json` → take `bridgeSessionId`.
Fallback: regex-scrape `capture-pane` output for the full URL.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Literal`](https://docs.python.org/3/library/typing.html#typing.Literal)[`'session_file'`, `'pane_capture'`]]]

### xa.resume(session, , cwd=None, name=None, hosts=None, \*\*opts)

Resume a transcript / archived session on its originating host.

* **Return type:**
  [`SpawnResult`](xa.claude_cli.md#xa.claude_cli.SpawnResult)

### xa.resume_session(claude_session_id, , cwd, name=None, claude_bin='claude', claude_home=PosixPath('/home/runner/.claude'), tmux_bin='tmux', url_timeout_sec=120.0, auto_remote_control=True, model=None, effort=None, pane_log_path=None, archive_store=None, pane_store=None, archive_id=None)

Launch `claude --resume <id>` in a new detached tmux session.

A user-supplied `name` is also passed to `claude --name` (renaming
the resumed session’s display name); the auto-generated fallback name
is tmux-only so a resume never clobbers the session’s existing name.

* **Return type:**
  [`SpawnResult`](xa.claude_cli.md#xa.claude_cli.SpawnResult)

### xa.spawn_session(name, , cwd, claude_bin='claude', claude_home=PosixPath('/home/runner/.claude'), tmux_bin='tmux', url_timeout_sec=120.0, auto_remote_control=True, model=None, effort=None, pane_log_path=None, archive_store=None, pane_store=None, archive_id=None)

Create a detached tmux session running `claude` in `cwd`.

Waits up to `url_timeout_sec` for the bridge URL to appear, dismissing
the “trust this folder” prompt and enabling remote control (via the
`--remote-control` flag on modern claude binaries, or the TUI
handshake on older ones). The session name is also passed to
`claude --name` so the Claude-side display name matches; `model`
and `effort` become per-session `--model` / `--effort` flags.

If `archive_store` (and optionally `pane_store`) are given, emits
`created` + `url_acquired` events to the archive and pipes the pane
output to a file under `pane_store`.

Services that want an immediate return should call
`prepare_spawn()` and run `complete_spawn()` in the background
instead — this function is simply the composition of the two.

* **Return type:**
  [`SpawnResult`](xa.claude_cli.md#xa.claude_cli.SpawnResult)

### xa.transcript_forensics(path)

Extract postmortem-relevant facts from the tail of a transcript.

Walks from the *end* of the file so we find the most recent
`tool_use` / `tool_result` / assistant `text` without reparsing
the whole transcript.

* **Return type:**
  [`TranscriptForensics`](xa.claude_fs.md#xa.claude_fs.TranscriptForensics)

### xa.transcript_metadata(path)

Summarise a transcript without holding it all in memory.

Walks forward, counting turns and capturing the first user message /
summary / custom title / fork pointer / cwd / sessionId. Stops reading
content fields once it has what it needs, but still counts turns to
the end. For very large transcripts, callers who only need counts
should use `iter_transcript_events` directly.

* **Return type:**
  [`TranscriptMeta`](xa.claude_fs.md#xa.claude_fs.TranscriptMeta)

### xa.transcript_path(cwd, session_id, , claude_home=PosixPath('/home/runner/.claude'))

Return the transcript path for `(cwd, session_id)` if it exists.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]

```pycon
>>> from pathlib import Path
>>> import tempfile
>>> with tempfile.TemporaryDirectory() as td:
...     home = Path(td)
...     (home / 'projects' / '-foo-bar').mkdir(parents=True)
...     f = home / 'projects' / '-foo-bar' / 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl'
...     _ = f.write_text('')
...     p = transcript_path('/foo/bar', 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', claude_home=home)
...     p == f
True
```

### Modules

| [`archive`](xa.archive.md#module-xa.archive)       | Postmortem archive of Claude Code sessions spawned by `xa`.              |
|----------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| [`claude_cli`](xa.claude_cli.md#module-xa.claude_cli) | Spawn, resume, and URL-resolve Claude Code sessions.                     |
| [`claude_fs`](xa.claude_fs.md#module-xa.claude_fs)   | Pure filesystem view of `~/.claude/`.                                    |
| [`cli`](xa.cli.md#module-xa.cli)               | Command-line interface for `xa`.                                         |
| [`config`](xa.config.md#module-xa.config)         | TOML config loader for `xa`.                                             |
| [`hosts`](xa.hosts.md#module-xa.hosts)           | Host abstraction — local, SSH, HTTP backends.                            |
| [`revive`](xa.revive.md#module-xa.revive)         | Detect — and reconnect — Claude Code panes whose Remote Control dropped. |
| [`service`](xa.service.md#module-xa.service)       | FastAPI service for `xa`.                                                |
| [`sessions`](xa.sessions.md#module-xa.sessions)     | The unified `Session` domain model and multi-host discovery.             |
| [`store`](xa.store.md#module-xa.store)           | Minimal key-value and append-log storage.                                |
| [`tmux`](xa.tmux.md#module-xa.tmux)             | Pure tmux wrappers.                                                      |
