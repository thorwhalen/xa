# xa.push

`xa push`: mirror this machine’s live sessions into another host’s xa cache.

`xa sync` pulls a remote home down, which needs this machine to reach the other one. A
laptop behind NAT can reach a server but not the reverse, so the server cannot pull it.
`push` is the other direction: it writes a *mirror* of the local home into
`<host>:<cache_dir>/<as_name>/`, the same place the server’s own `xa sync` would put it:

- `sessions/`: the registry records whose process is alive here, and nothing else
  (`--delete`, so a session that ended disappears from the mirror);
- `projects/<slug>/<session-id>.jsonl`: the transcript of each of those sessions;
- `.mirrored-at`: a stamp, written last, whose mtime says when the mirror was complete.

A reader on the hub (crowsnest, a home with `liveness = "mirror"`) counts every record in
it as live while the stamp is fresh, so an idle session still shows and a machine that
stops pushing drops out. Transcripts of ended sessions are pruned on the hub after
`keep_days`.

### Module Attributes

| [`MIRROR_STAMP`](#xa.push.MIRROR_STAMP)      | The stamp's name.                                                                           |
|--------------------------------------------------------------------|---------------------------------------------------------------------------------------------|
| [`DFLT_REMOTE_CACHE`](#xa.push.DFLT_REMOTE_CACHE) | ssh and rsync both start there, and a quoted `~` would not be expanded by the remote shell. |

### Functions

| [`live_records`](#xa.push.live_records)(home, \*[, is_alive])                 | The registry records under `home` whose pid is alive, with their transcripts.                                               |
|-----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------|
| [`push`](#xa.push.push)(host, \*, as_name[, home, remote_cache, ...]) | Mirror `home`'s live sessions to `host` (an [`SSHHost`](xa.hosts.ssh.html.md#xa.hosts.ssh.SSHHost)). |
| [`push_commands`](#xa.push.push_commands)(records, \*, home, target, ...)      | The commands one push runs, in order.                                                                                       |

### Classes

| [`LiveRecord`](#xa.push.LiveRecord)(record, transcript)   | One registry record whose process is alive here, and its transcript (if found).   |
|-----------------------------------------------------------------------------------|-----------------------------------------------------------------------------------|

### xa.push.DFLT_REMOTE_CACHE *= '.cache/xa/remotes'*

ssh and rsync both start there, and a quoted `~` would
not be expanded by the remote shell.

* **Type:**
  Relative to the remote home

### *class* xa.push.LiveRecord(record, transcript)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One registry record whose process is alive here, and its transcript (if found).

### xa.push.MIRROR_STAMP *= '.mirrored-at'*

The stamp’s name. crowsnest reads the same name (crowsnest.registry.MIRROR_STAMP).

### xa.push.live_records(home, \*, is_alive=<function \_pid_alive>)

The registry records under `home` whose pid is alive, with their transcripts.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`LiveRecord`](#xa.push.LiveRecord)]

### xa.push.push(host, \*, as_name, home='~/.claude', remote_cache='.cache/xa/remotes', keep_days=7, run=<function run>, timeout=300.0)

Mirror `home`’s live sessions to `host` (an [`SSHHost`](xa.hosts.ssh.html.md#xa.hosts.ssh.SSHHost)).

Returns how many live sessions were pushed. Raises `RuntimeError` naming the step
that failed.

* **Return type:**
  [`int`](https://docs.python.org/3/builtins/functions.html#int)

### xa.push.push_commands(records, , home, target, dest, staging, keep_days=7, rsync_bin='rsync', ssh_bin='ssh')

The commands one push runs, in order. Pure apart from writing into `staging`.

Transcripts go first and the registry plus stamp last, so a fresh stamp always comes
with the transcripts its records name.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]
