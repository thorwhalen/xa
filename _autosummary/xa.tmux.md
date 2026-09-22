# xa.tmux

Pure tmux wrappers.

No Claude Code knowledge lives here. Every public function takes a
`binary` keyword so callers can override the tmux executable (tests,
cross-platform installs, remote-host bridges).

Key gotchas encoded below:

- `session_target(name)` returns `f"{name}:"` — the trailing colon is
  essential. A bare session name can be silently resolved as a window or
  pane spec and mis-target a different session.
- `list_sessions` returns an empty list (not raises) when the tmux
  server isn’t running; tmux exits non-zero in that case and we absorb it.

### Functions

| [`capture_pane`](#xa.tmux.capture_pane)(name, \*[, lines, binary])          | Return the last `lines` of the targeted pane, or '' on failure.                |
|---------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| [`descendants`](#xa.tmux.descendants)(pid)                                 | All transitive descendant PIDs of `pid`.                                       |
| `kill_session`(name, \*[, binary])                                                                |                                                                                |
| [`list_panes`](#xa.tmux.list_panes)(\*[, binary])                         | Every pane on the server, or `[]` when no server is running.                   |
| [`list_sessions`](#xa.tmux.list_sessions)(\*[, binary])                      | Return all live tmux sessions; empty list if server isn't running.             |
| [`new_session`](#xa.tmux.new_session)(name, \*, command[, binary])         | Create a detached tmux session running `command` as its pane's program.        |
| [`pane_count`](#xa.tmux.pane_count)(name, \*[, binary])                   | Number of panes (across all windows) in the session; 0 if gone.                |
| [`pane_current_path`](#xa.tmux.pane_current_path)(name, \*[, binary])            | Working directory of the targeted pane's program, or None.                     |
| [`pane_pid`](#xa.tmux.pane_pid)(name, \*[, binary])                     | Return the pid of the first pane's program, or None if the session is gone.    |
| [`pane_pids`](#xa.tmux.pane_pids)(name, \*[, binary])                    | PIDs of every pane program in the session (all windows, all panes).            |
| [`pane_target`](#xa.tmux.pane_target)(ref)                                 | Target string for pane-scoped commands (capture, send-keys).                   |
| [`pipe_pane_to_file`](#xa.tmux.pipe_pane_to_file)(name, \*, path[, binary])      | Start streaming the pane's output to `path` (append mode).                     |
| [`proc_comm`](#xa.tmux.proc_comm)(pid)                                   | Return the `comm` name of a pid (kernel-level process name), '' if unreadable. |
| [`rename_session`](#xa.tmux.rename_session)(old_name, new_name, \*[, binary]) | Rename a live tmux session.                                                    |
| [`send_keys`](#xa.tmux.send_keys)(name, \*keys[, binary])                | Send one or more keys/strings to the targeted pane.                            |
| [`session_target`](#xa.tmux.session_target)(name)                             | Return the canonical target string for a session.                              |

### Classes

| [`TmuxPane`](#xa.tmux.TmuxPane)(target, pid, current_command, ...)    | Minimal view of one tmux pane, from `list-panes -a`.    |
|-------------------------------------------------------------------------------------------------|---------------------------------------------------------|
| [`TmuxSession`](#xa.tmux.TmuxSession)(name, created, activity, attached) | Minimal view of one tmux session, from `list-sessions`. |

### *class* xa.tmux.TmuxPane(target, pid, current_command, current_path)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Minimal view of one tmux pane, from `list-panes -a`.

### *class* xa.tmux.TmuxSession(name, created, activity, attached)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Minimal view of one tmux session, from `list-sessions`.

### xa.tmux.capture_pane(name, , lines=200, binary='tmux')

Return the last `lines` of the targeted pane, or ‘’ on failure.

`name` may be a session name (targets its active pane) or a full
pane ref (`session:@w.%p`) for an exact pane.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### xa.tmux.descendants(pid)

All transitive descendant PIDs of `pid`.

Uses `/proc/*/status` where available and `ps` elsewhere (macOS),
so the process-tree walk behaves the same on every platform xa runs
on. Silently tolerates races (processes dying mid-scan) and returns
`[]` when neither source can be read.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`int`](https://docs.python.org/3/builtins/functions.html#int)]

### xa.tmux.list_panes(, binary='tmux')

Every pane on the server, or `[]` when no server is running.

`target` is the full pane ref, the same shape claude records in its
ephemeral session file, so the two views join without parsing.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`TmuxPane`](#xa.tmux.TmuxPane)]

### xa.tmux.list_sessions(, binary='tmux')

Return all live tmux sessions; empty list if server isn’t running.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`TmuxSession`](#xa.tmux.TmuxSession)]

### xa.tmux.new_session(name, , command, binary='tmux')

Create a detached tmux session running `command` as its pane’s program.

`command` is passed to a shell: the caller is responsible for quoting.
Use `shlex.quote` for untrusted parts.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.tmux.pane_count(name, , binary='tmux')

Number of panes (across all windows) in the session; 0 if gone.

* **Return type:**
  [`int`](https://docs.python.org/3/builtins/functions.html#int)

### xa.tmux.pane_current_path(name, , binary='tmux')

Working directory of the targeted pane’s program, or None.

This is where a pane’s shell would run a command, which is what
`claude remote-control -c` keys its per-directory record on.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### xa.tmux.pane_pid(name, , binary='tmux')

Return the pid of the first pane’s program, or None if the session is gone.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`int`](https://docs.python.org/3/builtins/functions.html#int)]

### xa.tmux.pane_pids(name, , binary='tmux')

PIDs of every pane program in the session (all windows, all panes).

Empty list when the session is gone. `name` may be a session name
or a full pane ref — `-s` scopes to the containing session either way.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`int`](https://docs.python.org/3/builtins/functions.html#int)]

### xa.tmux.pane_target(ref)

Target string for pane-scoped commands (capture, send-keys).

Accepts either a bare session name or a full tmux pane ref
(`session:@window.%pane` — the shape claude records in its ephemeral
session file). A bare name targets the session’s *active* pane, which
may not be the claude pane in a multi-window session — pass the full
ref whenever you have one. Session names in xa match
`[A-Za-z0-9_.-]` so they can never contain a `:`.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> pane_target('foo')
'foo:'
>>> pane_target('foo:@1.%2')
'foo:@1.%2'
```

### xa.tmux.pipe_pane_to_file(name, , path, binary='tmux')

Start streaming the pane’s output to `path` (append mode).

Uses `-o` so a duplicate call toggles the pipe off, matching edualc’s
behavior. tmux stops piping automatically when the pane dies.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.tmux.proc_comm(pid)

Return the `comm` name of a pid (kernel-level process name), ‘’ if unreadable.

Linux reads `/proc/<pid>/comm`, which is a bare name (`claude`).
`ps -o comm=` on macOS reports a *path* (`/usr/local/bin/claude`),
so the fallback takes the basename — callers compare against bare
names (see `xa.claude_fs._looks_like_claude()`) and must not have
to care which platform answered.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### xa.tmux.rename_session(old_name, new_name, , binary='tmux')

Rename a live tmux session.

Both names must match the strict `[A-Za-z0-9_.-]{1,48}` pattern
used elsewhere in `xa`; callers should validate before calling.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.tmux.send_keys(name, \*keys, binary='tmux')

Send one or more keys/strings to the targeted pane.

Always pass `"Enter"` for newline. `name` may be a session name
(targets its active pane) or a full pane ref for an exact pane.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.tmux.session_target(name)

Return the canonical target string for a session.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> session_target('foo')
'foo:'
```
