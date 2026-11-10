# xa.recovery

Bring back the sessions a shutdown killed, each as it was launched.

Two questions, answered from the transcripts alone (the processes are gone):

1. **Which sessions were alive at the last shutdown?** When a machine shuts
   down, every running claude writes a final `cost-state` entry on its way
   out, so its transcript’s last write lands within seconds of the shutdown.
   [`sessions_alive_at_shutdown()`](#xa.recovery.sessions_alive_at_shutdown) returns the transcripts whose last write
   > falls in `[shutdown - window, boot)`. The shutdown time comes from
   > `last` ([`last_shutdown_time()`](#xa.recovery.last_shutdown_time)), the boot time from the kernel
   > ([`boot_time()`](#xa.recovery.boot_time)), and the record counts only when it belongs to this
   > boot. Sessions resumed since then, running now, or already sitting in a tmux
   > pane started with `--resume <id>` are never candidates, so applying twice
   > resumes nothing twice. Headless runs (`claude -p`, the SDK) are skipped.
2. **How was each one launched?** [`xa.claude_fs.launch_facts()`](xa.claude_fs.html.md#xa.claude_fs.launch_facts) reads one transcript
   for what `claude --resume` needs in order to come back the same:
   - the **cwd**: the *first* `cwd` whose slug is the transcript’s project
     folder. A session that `cd`-ed elsewhere records later cwds too, and
     resuming in the last one puts it in the wrong folder (thorwhalen/xa#23);
   - the **permission mode**: the last one recorded, plus whether bypass was
     ever reached (only a session started with the bypass flag can reach it);
   - the **model** of the last assistant turn, pinned only when its family
     differs from the account default’s ([`xa.claude_fs.resume_model()`](xa.claude_fs.html.md#xa.claude_fs.resume_model)).

What a shutdown with no clean exit (a power cut, a kernel panic) leaves is not
covered: no process wrote anything at the moment it died, so there is nothing
to time it by.

### Module Attributes

| [`DFLT_WINDOW_SEC`](#xa.recovery.DFLT_WINDOW_SEC)   | How long before the shutdown record a session's last write may land and still count as "alive at shutdown".   |
|--------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------|

### Functions

| [`boot_time`](#xa.recovery.boot_time)(\*[, run])                               | Unix time the machine last booted, or `None` when it cannot be read.        |
|-----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| [`last_shutdown_time`](#xa.recovery.last_shutdown_time)(\*[, before, run])              | Unix time of the shutdown that preceded the current boot, or `None`.        |
| [`live_session_ids`](#xa.recovery.live_session_ids)(\*, claude_home[, tmux_bin])      | Session ids running right now: a live claude, or a tmux pane resuming them. |
| [`parse_last_stamp`](#xa.recovery.parse_last_stamp)(text, \*, before)                 | The first `Tue Nov 10 09:50`-style stamp in `text`, as unix time.           |
| [`resume_all`](#xa.recovery.resume_all)(candidates, \*[, claude_home, ...])     | Resume every candidate in its own tmux session, each as it was launched.    |
| [`sessions_alive_at_shutdown`](#xa.recovery.sessions_alive_at_shutdown)(\*[, claude_home, ...]) | The sessions of `claude_home` whose last write fell at the last shutdown.   |
| [`tmux_name_for`](#xa.recovery.tmux_name_for)(facts, \*[, taken])                  | A tmux session name for `facts`: its own name, sanitised, made unique.      |

### Classes

| [`Resumed`](#xa.recovery.Resumed)(facts, result[, skipped, warning])    | One candidate's outcome: a [`SpawnResult`](xa.claude_cli.html.md#xa.claude_cli.SpawnResult), or why not.   |
|------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|
| [`ShutdownScan`](#xa.recovery.ShutdownScan)(candidates, shutdown, boot, ...) | The candidates, and the times they were picked by (for the dry run).                                                             |

### xa.recovery.DFLT_WINDOW_SEC *= 120.0*

How long before the shutdown record a session’s last write may land and
still count as “alive at shutdown”. `last` has minute resolution, so this
has to cover at least the minute it rounds down.

### *class* xa.recovery.Resumed(facts, result, skipped=None, warning=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One candidate’s outcome: a [`SpawnResult`](xa.claude_cli.html.md#xa.claude_cli.SpawnResult), or why not.

### *class* xa.recovery.ShutdownScan(candidates, shutdown, boot, anchor_source, lo, hi)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The candidates, and the times they were picked by (for the dry run).

### xa.recovery.boot_time(\*, run=<function \_run>)

Unix time the machine last booted, or `None` when it cannot be read.

`/proc/stat`’s `btime` on Linux, `sysctl kern.boottime` elsewhere.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]

### xa.recovery.last_shutdown_time(\*, before=None, run=<function \_run>)

Unix time of the shutdown that preceded the current boot, or `None`.

Read from `last` (minute resolution), newest record first: the current
boot’s `reboot` line, then its shutdown. A second `reboot` line before
any shutdown means this boot followed a crash (no clean shutdown), and an
older record must not stand in for it. `last -x reboot shutdown` on
Linux; macOS prints both kinds under `last reboot`.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]

### xa.recovery.live_session_ids(, claude_home, tmux_bin=None)

Session ids running right now: a live claude, or a tmux pane resuming them.

The pane scan covers a resume that has not written anything yet (it is
still at a trust or login prompt), which the session files cannot show.

* **Return type:**
  [`set`](https://docs.python.org/3/builtins/stdtypes.html#set)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### xa.recovery.parse_last_stamp(text, , before)

The first `Tue Nov 10 09:50`-style stamp in `text`, as unix time.

`last` prints no year, so the year is the latest one that puts the stamp
at or before `before` (local time).

```pycon
>>> import time as t
>>> before = t.mktime((2026, 11, 10, 10, 0, 0, 0, 0, -1))
>>> ts = parse_last_stamp('shutdown time   Tue Nov 10 09:50', before=before)
>>> t.localtime(ts)[:5]
(2026, 11, 10, 9, 50)
```

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]

### xa.recovery.resume_all(candidates, , claude_home=PosixPath('/home/runner/.claude'), claude_bin=None, tmux_bin=None, url_timeout_sec=120.0, auto_remote_control=True, permission_mode=None, euid=None)

Resume every candidate in its own tmux session, each as it was launched.

All panes are launched first and only then waited on, so the whole batch
takes about one `url_timeout_sec`, not one per session. A pane that comes
up without a Remote Control URL gets the [`xa.revive`](xa.revive.html.md#module-xa.revive) verdict for what
it is showing (`needs_trust` for a folder the account has not trusted,
`needs_login`, …) in `result.attention`, rather than a hang.
`permission_mode` overrides every candidate’s recorded mode (`''`:
claude’s default).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Resumed`](#xa.recovery.Resumed)]

### xa.recovery.sessions_alive_at_shutdown(, claude_home=PosixPath('/home/runner/.claude'), window_sec=120.0, shutdown=None, boot=None, live_ids=None, tmux_bin=None)

The sessions of `claude_home` whose last write fell at the last shutdown.

The window is `[shutdown - window_sec, boot)`. With no shutdown record
for this boot (a crash, or `last` unreadable), the anchor is the latest
transcript write before boot instead, and the window is
`[anchor - window_sec, anchor]`: the dry run says which was used.
`shutdown` / `boot` / `live_ids` default to what this machine reports.
Sessions in `live_ids` (running now) and headless runs are never candidates.

* **Return type:**
  [`ShutdownScan`](#xa.recovery.ShutdownScan)

### xa.recovery.tmux_name_for(facts, , taken=())

A tmux session name for `facts`: its own name, sanitised, made unique.

```pycon
>>> f = LaunchFacts('0123456789', Path('t'), None, '', False, None, None, 'my session', None, 0.0)
>>> tmux_name_for(f), tmux_name_for(f, taken={'my-session'})
('my-session', 'my-session-2')
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
