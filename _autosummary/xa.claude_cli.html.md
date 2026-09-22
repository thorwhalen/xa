# xa.claude_cli

Spawn, resume, and URL-resolve Claude Code sessions.

Ties `xa.tmux` (pane / process control), `xa.claude_fs` (read the
ephemeral `~/.claude/sessions/<pid>.json` file) and the `claude` CLI
binary together. Everything here targets the *local* machine; remote-host
dispatch lives in `xa.hosts` (Phase 6+).

Bridge URL format: `https://claude.ai/code/<bridgeSessionId>`. The
`bridgeSessionId` already starts with `session_` — do not prepend
anything.

### Functions

| [`complete_spawn`](#xa.claude_cli.complete_spawn)(pending)                           | Blocking half of the spawn: wait for the URL, then report.              |
|----------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| [`diagnose_bridgeless`](#xa.claude_cli.diagnose_bridgeless)(session_name, \*[, ...])      | Why has this session no bridge URL? Returns `(verdict, hint)`.          |
| [`find_claude_pid`](#xa.claude_cli.find_claude_pid)(session_name, \*[, tmux_bin])     | Return the PID of the claude process living in the tmux session.        |
| [`new_archive_id`](#xa.claude_cli.new_archive_id)()                                  | Fresh 12-char hex id used as the key for pane logs + archive events.    |
| [`pid_is_claude`](#xa.claude_cli.pid_is_claude)(pid)                                | Portable positive identity check — used before ever signaling a pid.    |
| [`prepare_spawn`](#xa.claude_cli.prepare_spawn)(name, \*, cwd[, resume_id, ...])    | Launch the tmux session and return without waiting for the URL.         |
| [`resolve_bridge_url`](#xa.claude_cli.resolve_bridge_url)(session_name, \*[, ...])       | Return `(url, source)` for a live tmux-hosted claude session.           |
| [`resume_session`](#xa.claude_cli.resume_session)(claude_session_id, \*, cwd[, ...]) | Launch `claude --resume <id>` in a new detached tmux session.           |
| [`spawn_session`](#xa.claude_cli.spawn_session)(name, \*, cwd[, claude_bin, ...])   | Create a detached tmux session running `claude` in `cwd`.               |
| [`supported_cli_flags`](#xa.claude_cli.supported_cli_flags)([claude_bin, ttl_sec])        | Long-form flags advertised by `<claude_bin> --help`, cached per binary. |
| [`tmux_session_dedicated_to`](#xa.claude_cli.tmux_session_dedicated_to)(session_name, ...)      | Is killing this whole tmux session equivalent to killing this claude?   |
| [`wait_for_bridge_url`](#xa.claude_cli.wait_for_bridge_url)(session_name, \*, ...[, ...]) | Poll the ephemeral session file until a bridge URL appears.             |

### Classes

| [`PendingSpawn`](#xa.claude_cli.PendingSpawn)(name, cwd, claude_home, ...)      | A launched tmux session whose bridge-URL handshake hasn't finished.   |
|-------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| [`SpawnResult`](#xa.claude_cli.SpawnResult)(name, cwd, claude_pid, ...[, ...]) | What `spawn_session` / `resume_session` return to the caller.         |

### *class* xa.claude_cli.PendingSpawn(name, cwd, claude_home, tmux_bin, deadline, warning, archive_ctx)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A launched tmux session whose bridge-URL handshake hasn’t finished.

Returned by [`prepare_spawn()`](#xa.claude_cli.prepare_spawn) — the fast, synchronous part (tmux
session exists, pane log piping, `created` archive event emitted).
Pass it to [`complete_spawn()`](#xa.claude_cli.complete_spawn) to run the slow part (URL wait,
prompt dismissal); services may do that from a background thread so
the caller gets an immediate acknowledgment.

### *class* xa.claude_cli.SpawnResult(name, cwd, claude_pid, claude_session_id, bridge_session_id, url, url_source, warning, attention=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What `spawn_session` / `resume_session` return to the caller.

### xa.claude_cli.complete_spawn(pending)

Blocking half of the spawn: wait for the URL, then report.

Safe to call from a background thread — all state it touches (tmux,
`~/.claude/sessions/`, the append-only events log) is shared
machine-wide, not per-process.

* **Return type:**
  [`SpawnResult`](#xa.claude_cli.SpawnResult)

### xa.claude_cli.diagnose_bridgeless(session_name, , claude_pid=None, tmux_bin='tmux')

Why has this session no bridge URL? Returns `(verdict, hint)`.

One pane capture, classified by [`xa.revive.classify()`](xa.revive.html.md#xa.revive.classify) — the single
rule engine for reading a claude pane. Only meaningful for a session
with no bridge id: a connected session’s pane renders conversation text,
which may legitimately quote `/login`.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

### xa.claude_cli.find_claude_pid(session_name, , tmux_bin='tmux')

Return the PID of the claude process living in the tmux session.

Walks **every** pane’s process tree (a claude can live in any window
of a multi-window session, not just the first pane). Recognizes both
the native binary (`comm == "claude"`) and npm/bun installs.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`int`](https://docs.python.org/3/builtins/functions.html#int)]

### xa.claude_cli.new_archive_id()

Fresh 12-char hex id used as the key for pane logs + archive events.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### xa.claude_cli.pid_is_claude(pid)

Portable positive identity check — used before ever signaling a pid.

Liveness ([`xa.claude_fs.ephemeral_session_alive()`](xa.claude_fs.html.md#xa.claude_fs.ephemeral_session_alive)) proves a
process exists; this proves it is actually a claude, so a stale
ephemeral file whose pid got recycled can never direct a kill at an
unrelated process. Uses `/proc` where available, `ps` otherwise.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### xa.claude_cli.prepare_spawn(name, , cwd, resume_id=None, claude_bin='claude', claude_home=PosixPath('/home/runner/.claude'), tmux_bin='tmux', url_timeout_sec=120.0, auto_remote_control=True, claude_name=None, model=None, effort=None, pane_log_path=None, archive_store=None, pane_store=None, archive_id=None)

Launch the tmux session and return without waiting for the URL.

Composes per-session claude flags (`--name` / `--model` /
`--effort` / `--remote-control`) when the installed binary
supports them; requested-but-unsupported flags are reported in
`PendingSpawn.warning` rather than failing the spawn.

`auto_remote_control` passes `--remote-control`, which is how xa
asks for Remote Control now — a documented CLI flag, not a keystroke
sent at a TUI. On a claude too old to advertise it, the host’s
`remoteControlAtStartup` setting is the remaining path, so the
warning says so rather than falling back to typing.

* **Return type:**
  [`PendingSpawn`](#xa.claude_cli.PendingSpawn)

### xa.claude_cli.resolve_bridge_url(session_name, , claude_home=PosixPath('/home/runner/.claude'), tmux_bin='tmux', scrape_lines=400)

Return `(url, source)` for a live tmux-hosted claude session.

Primary path: find the claude descendant pid → read
`~/.claude/sessions/<pid>.json` → take `bridgeSessionId`.
Fallback: regex-scrape `capture-pane` output for the full URL.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Literal`](https://docs.python.org/3/library/typing.html#typing.Literal)[`'session_file'`, `'pane_capture'`]]]

### xa.claude_cli.resume_session(claude_session_id, , cwd, name=None, claude_bin='claude', claude_home=PosixPath('/home/runner/.claude'), tmux_bin='tmux', url_timeout_sec=120.0, auto_remote_control=True, model=None, effort=None, pane_log_path=None, archive_store=None, pane_store=None, archive_id=None)

Launch `claude --resume <id>` in a new detached tmux session.

A user-supplied `name` is also passed to `claude --name` (renaming
the resumed session’s display name); the auto-generated fallback name
is tmux-only so a resume never clobbers the session’s existing name.

* **Return type:**
  [`SpawnResult`](#xa.claude_cli.SpawnResult)

### xa.claude_cli.spawn_session(name, , cwd, claude_bin='claude', claude_home=PosixPath('/home/runner/.claude'), tmux_bin='tmux', url_timeout_sec=120.0, auto_remote_control=True, model=None, effort=None, pane_log_path=None, archive_store=None, pane_store=None, archive_id=None)

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
[`prepare_spawn()`](#xa.claude_cli.prepare_spawn) and run [`complete_spawn()`](#xa.claude_cli.complete_spawn) in the background
instead — this function is simply the composition of the two.

* **Return type:**
  [`SpawnResult`](#xa.claude_cli.SpawnResult)

### xa.claude_cli.supported_cli_flags(claude_bin='claude', , ttl_sec=900.0)

Long-form flags advertised by `<claude_bin> --help`, cached per binary.

Lets spawn compose modern per-session flags (`--remote-control`,
`--name`, `--model`, `--effort`) while degrading gracefully on
older installs. Empty set when the binary can’t be executed — callers
then omit every optional flag, matching pre-flag behavior. Only
successful probes are cached (for `ttl_sec`); failures are retried.

* **Return type:**
  [`frozenset`](https://docs.python.org/3/builtins/stdtypes.html#frozenset)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### xa.claude_cli.tmux_session_dedicated_to(session_name, claude_pid, , tmux_bin='tmux')

Is killing this whole tmux session equivalent to killing this claude?

True iff the session consists of a single pane whose claude is
`claude_pid`. xa-spawned sessions always qualify; a claude living in
one window of a user’s multi-window workspace never does — killing
the session there would destroy unrelated panes, so callers must fall
back to signaling the pid instead.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### xa.claude_cli.wait_for_bridge_url(session_name, , claude_home, tmux_bin, deadline, poll_sec=0.5)

Poll the ephemeral session file until a bridge URL appears.

Nothing is typed into the pane. Claude Code connects Remote Control by
itself — from the `--remote-control` flag `_claude_argv()` passes,
or from the host’s `remoteControlAtStartup` setting — and records the
`bridgeSessionId` when it does. This function’s whole job is to wait
for that write.

Until 0.1.10 this drove the TUI instead: dismissing the workspace-trust
prompt with a blind Enter and sending `/remote-control` once a prompt
glyph appeared. That emulated, by scraping and typing, what the flag and
the setting do properly — and every upstream redraw was a chance for it
to type into the wrong thing. See [`xa.revive`](xa.revive.html.md#module-xa.revive) for the pane reading
that *is* still warranted: reconnecting a session whose Remote Control
dropped is a gap Claude Code genuinely leaves open.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Literal`](https://docs.python.org/3/library/typing.html#typing.Literal)[`'session_file'`, `'pane_capture'`]]]
