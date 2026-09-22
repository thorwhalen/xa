# xa.sessions

The unified `Session` domain model and multi-host discovery.

This layer is the single thing CLI / HTTP / UI renderers iterate over.
Discovery and actions are delegated to [`xa.hosts`](xa.hosts.md#module-xa.hosts); this module
composes them into a host-agnostic API.

### Functions

| [`get_session`](#xa.sessions.get_session)(session_id, \*[, hosts, claude_home])   | Find one session by full ID or unique prefix.                         |
|------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| [`iter_local_sessions`](#xa.sessions.iter_local_sessions)(\*[, claude_home, ...])         | Legacy local-only iterator.                                           |
| [`kill_session`](#xa.sessions.kill_session)(session, \*[, hosts])                  | Kill a live `Session`, scoped to what actually belongs to it.         |
| [`list_sessions`](#xa.sessions.list_sessions)(\*[, hosts, project, ...])            | Return sessions sorted by recency (live first, then newest-modified). |
| [`resume`](#xa.sessions.resume)(session, \*[, cwd, name, hosts])             | Resume a transcript / archived session on its originating host.       |

### Classes

| [`Session`](#xa.sessions.Session)(id, claude_session_id, ...[, ...])   | Canonical session record — valid across hosts and states.   |
|-----------------------------------------------------------------------------------------------|-------------------------------------------------------------|

### *class* xa.sessions.Session(id, claude_session_id, bridge_session_id, host, cwd, project_slug, state, live_pid, tmux_name, name, summary, first_user_message, turn_count, forked_from, created, modified, url, url_source, transcript_path, pre_first_turn=False, attention=None, attention_hint=None, tmux_pane=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Canonical session record — valid across hosts and states.

Fields that don’t apply to a given state are `None`. Discovery
functions return fresh instances; callers should treat it as
immutable.

### xa.sessions.get_session(session_id, , hosts=None, claude_home=None)

Find one session by full ID or unique prefix.

Raises `LookupError` if the prefix is ambiguous.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Session`](#xa.sessions.Session)]

### xa.sessions.iter_local_sessions(, claude_home=PosixPath('/home/runner/.claude'), project_slug=None, include_live=True, tmux_bin=None)

Legacy local-only iterator.

Retained for backward compatibility and because some tests construct
sessions directly from a fake `~/.claude/`. Wraps a fresh
`LocalHost`.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`Session`](#xa.sessions.Session)]

### xa.sessions.kill_session(session, , hosts=None)

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

### xa.sessions.list_sessions(, hosts=None, project=None, include_forks=True, include_live=True, state=None, limit=None, claude_home=None, tmux_bin=None)

Return sessions sorted by recency (live first, then newest-modified).

When `hosts` is `None` we default to a single `LocalHost`.
If `claude_home` or `tmux_bin` are given, they’re forwarded into
that default `LocalHost` — handy for fixture-based tests.

Filters:

- `project` — substring match against `cwd` (case-insensitive)
- `include_forks=False` — drop sessions with a `forked_from`
- `include_live=False` — skip live discovery (per host)
- `state` — one of `"live"` / `"archived"` / `"transcript_only"`

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Session`](#xa.sessions.Session)]

### xa.sessions.resume(session, , cwd=None, name=None, hosts=None, \*\*opts)

Resume a transcript / archived session on its originating host.

* **Return type:**
  [`SpawnResult`](xa.claude_cli.md#xa.claude_cli.SpawnResult)
