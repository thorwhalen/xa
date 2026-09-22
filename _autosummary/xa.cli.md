# xa.cli

Command-line interface for `xa`.

Exposes subcommands via `cw`: `list`, `info`, `history`, `spawn`,
`resume`, `kill`, `serve`, `sync`, `pick`, `gen-secret`, `revive`,
and the `archive` group (`list`, `log`, `forensics`).

Entry point: `xa` (see `[project.scripts]` in `pyproject.toml`).

### Functions

| [`archive_forensics_cmd`](#xa.cli.archive_forensics_cmd)(archive_id)                 | Print rich forensics for an archived session as JSON.                                                                                            |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------|
| [`archive_list_cmd`](#xa.cli.archive_list_cmd)([limit, json_out])               | List archived sessions (`created` events, newest first).                                                                                         |
| [`archive_log_cmd`](#xa.cli.archive_log_cmd)(archive_id[, tail_kb])            | Print the pane log for an archived session.                                                                                                      |
| [`gen_secret_cmd`](#xa.cli.gen_secret_cmd)([length])                          | Print a cryptographically strong random hex secret.                                                                                              |
| [`history_cmd`](#xa.cli.history_cmd)([search, limit, json_out])            | Grep over `~/.claude/history.jsonl`.                                                                                                             |
| [`info_cmd`](#xa.cli.info_cmd)(session_id[, json_out])                  | Show full metadata + forensics for one session.                                                                                                  |
| [`kill_cmd`](#xa.cli.kill_cmd)(session_id)                              | Kill the tmux session backing a live Claude Code session.                                                                                        |
| [`list_cmd`](#xa.cli.list_cmd)([project, limit, include_forks, ...])    | List Claude Code sessions across configured hosts, newest first.                                                                                 |
| [`main`](#xa.cli.main)()                                            | Entry point referenced by `pyproject.toml`'s `[project.scripts]`.                                                                                |
| [`mk_parser`](#xa.cli.mk_parser)()                                       | The `xa` parser -- a plain [`argparse.ArgumentParser`](https://docs.python.org/3/library/argparse.html#argparse.ArgumentParser), built, not run. |
| [`pick_cmd`](#xa.cli.pick_cmd)([project, limit, host])                  | Interactive session picker — pick a row by number, then choose an action.                                                                        |
| [`resume_cmd`](#xa.cli.resume_cmd)(session_id[, name, cwd, timeout, ...]) | Resume a past session (`claude --resume`) in a new tmux pane.                                                                                    |
| [`revive_cmd`](#xa.cli.revive_cmd)([apply, include_held_elsewhere, ...])  | Report — and optionally reconnect — panes whose Remote Control dropped.                                                                          |
| [`serve_cmd`](#xa.cli.serve_cmd)([host, port, mount, username, ...])     | Run the xa HTTP service (requires the `xa[service]` extra).                                                                                      |
| [`spawn_cmd`](#xa.cli.spawn_cmd)(cwd[, name, timeout, ...])              | Spawn a detached claude-in-tmux session and print its remote URL.                                                                                |
| [`sync_cmd`](#xa.cli.sync_cmd)([host, force])                           | Refresh remote-host caches (SSH hosts; HTTP is always fresh).                                                                                    |

### xa.cli.archive_forensics_cmd(archive_id)

Print rich forensics for an archived session as JSON.

* **Parameters:**
  **archive_id** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Archive id from `xa archive list`.
* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.cli.archive_list_cmd(limit=30, json_out=False)

List archived sessions (`created` events, newest first).

* **Parameters:**
  * **limit** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Maximum rows (0 = unlimited).
  * **json_out** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – One JSON object per line.
* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.cli.archive_log_cmd(archive_id, tail_kb=64)

Print the pane log for an archived session.

* **Parameters:**
  * **archive_id** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – The archive id (first column of `xa archive list`).
  * **tail_kb** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Only print the last N KB (0 = full log).
* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.cli.gen_secret_cmd(length=32)

Print a cryptographically strong random hex secret.

Useful for `XA_PASSWORD`, `XA_CAPTCHA_KEY`, bearer tokens. Output
is to stdout with a trailing newline; pipe it into anything.

* **Parameters:**
  **length** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Byte length of the secret (hex output is 2x this).
* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.cli.history_cmd(search=None, limit=20, json_out=False)

Grep over `~/.claude/history.jsonl`.

* **Parameters:**
  * **search** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Case-insensitive substring to filter on (display field).
  * **limit** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Maximum rows to emit (0 = unlimited).
  * **json_out** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Emit one JSON object per line.
* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.cli.info_cmd(session_id, json_out=False)

Show full metadata + forensics for one session.

* **Parameters:**
  * **session_id** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Full UUID or unique prefix (e.g. first 8 chars).
  * **json_out** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Emit a single JSON object.
* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.cli.kill_cmd(session_id)

Kill the tmux session backing a live Claude Code session.

* **Parameters:**
  **session_id** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Full UUID or unique prefix, or a tmux session name.
* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.cli.list_cmd(project=None, limit=30, include_forks=True, no_live=False, state=None, host=None, json_out=False)

List Claude Code sessions across configured hosts, newest first.

* **Parameters:**
  * **project** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Substring match against session cwd (case-insensitive).
  * **limit** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Maximum rows to show (0 = unlimited).
  * **include_forks** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Include sessions spawned with `claude --resume`.
  * **no_live** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Skip the tmux/ephemeral scan (archive-only view).
  * **state** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Restrict to ‘live’, ‘archived’, or ‘transcript_only’.
  * **host** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Restrict to a single configured host by name.
  * **json_out** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Emit one JSON object per line instead of a table.
* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.cli.main()

Entry point referenced by `pyproject.toml`’s `[project.scripts]`.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.cli.mk_parser()

The `xa` parser – a plain [`argparse.ArgumentParser`](https://docs.python.org/3/library/argparse.html#argparse.ArgumentParser), built, not run.

Two calls rather than one `cw.dispatch(...)` because the `archive` group
carries `group_kwargs` (its one-line help in the top-level listing), and a
single mapping has nowhere to put those.

* **Return type:**
  [`ArgumentParser`](https://docs.python.org/3/library/argparse.html#argparse.ArgumentParser)

### xa.cli.pick_cmd(project=None, limit=30, host=None)

Interactive session picker — pick a row by number, then choose an action.

A pragmatic v1 picker without a TUI framework. Use **xa list**
for scripting; use **xa pick** when a human is at the keyboard.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.cli.resume_cmd(session_id, name=None, cwd=None, timeout=120.0, model=None, effort=None)

Resume a past session (`claude --resume`) in a new tmux pane.

* **Parameters:**
  * **session_id** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Full UUID or unique prefix.
  * **name** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Session name — tmux session and claude display name (auto if omitted).
  * **cwd** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Override cwd (defaults to the original session’s cwd).
  * **timeout** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Seconds to wait for the bridge URL.
  * **model** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Per-session `claude --model` (omitted → claude’s default).
  * **effort** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Per-session `claude --effort` (omitted → claude’s default).
* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.cli.revive_cmd(apply=False, include_held_elsewhere=False, server_mode=False, min_interval=0.0, json_out=False)

Report — and optionally reconnect — panes whose Remote Control dropped.

Prints one line per live claude pane with its verdict, so “nothing to do”
is legible next to how much was looked at. Sends nothing without
`--apply`.

* **Parameters:**
  * **apply** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Actually send `/remote-control`. Off by default.
  * **include_held_elsewhere** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Also reconnect sessions taken over from
    another device. This steals them back — deliberate keystroke only.
  * **server_mode** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Also restart died `claude remote-control` servers.
  * **min_interval** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Seconds before the same pane may be touched again
    (0 uses the default guard interval).
  * **json_out** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Emit JSON instead of the table.
* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.cli.serve_cmd(host='127.0.0.1', port=8010, mount='', username=None, password=None, password_env=None, captcha=False, webui=True, default_folder=None, i_know_its_insecure=False)

Run the xa HTTP service (requires the `xa[service]` extra).

**Secure by default.** Refuses to bind to a non-loopback interface
without HTTP Basic credentials. Override this guardrail with
`--i-know-its-insecure` only in locked-down deployments (e.g.
behind an mTLS-terminating proxy).

* **Parameters:**
  * **host** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Bind host. `127.0.0.1` by default — only this machine.
  * **port** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Bind port.
  * **mount** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Optional mount prefix (e.g. `/api/xa`).
  * **username** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – HTTP Basic username.
  * **password** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – HTTP Basic password. Prefer `--password-env`.
  * **password_env** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Name of env var to read the password from (recommended).
  * **captcha** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Enable captcha-gated deletes. Strongly recommended for
    any non-loopback deployment.
  * **webui** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Serve the bundled web UI at the mount root (default on).
    Pass `--no-webui` to run API-only.
  * **default_folder** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Default working directory the webui prefills into
    the “new session” dialog and the folder chooser opens at. Falls back
    to `$XA_DEFAULT_FOLDER` or `$HOME` if unset.
  * **i_know_its_insecure** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Skip the public-bind-without-auth safety check.
* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.cli.spawn_cmd(cwd, name=None, timeout=120.0, no_remote_control=False, model=None, effort=None)

Spawn a detached claude-in-tmux session and print its remote URL.

* **Parameters:**
  * **cwd** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Working directory to start claude in.
  * **name** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Session name — tmux session and claude display name (auto if omitted).
  * **timeout** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Seconds to wait for the bridge URL.
  * **no_remote_control** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Spawn without passing `--remote-control`.
  * **model** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Per-session `claude --model` (omitted → claude’s default).
  * **effort** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Per-session `claude --effort` (omitted → claude’s default).
* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.cli.sync_cmd(host=None, force=False)

Refresh remote-host caches (SSH hosts; HTTP is always fresh).

* **Parameters:**
  * **host** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Only sync this one host (default: all configured).
  * **force** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Sync even if the cache isn’t stale yet.
* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)
