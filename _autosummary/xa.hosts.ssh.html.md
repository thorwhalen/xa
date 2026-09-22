# xa.hosts.ssh

SSH-backed host.

Mirrors the cc-sessions transport pattern: pull the remote’s
`~/.claude/` tree into a local cache via `rsync -az --delete -e ssh`,
then read the cache with the same `claude_fs` code paths we use for
local discovery. Actions (`spawn` / `resume` / `kill` / tmux queries)
dispatch to the remote via `ssh <host> <cmd>`.

No Python SSH library is pulled in: we shell out to the system `ssh` /
`rsync` binaries, which means the user’s `~/.ssh/config`, agent, and
host keys Just Work. Install overhead: zero.

### Classes

| [`SSHHost`](#xa.hosts.ssh.SSHHost)(name, \*, host[, user, ...])   | Transcripts via rsync; actions via ssh exec.   |
|-----------------------------------------------------------------------------------------|------------------------------------------------|

### *class* xa.hosts.ssh.SSHHost(name, , host, user=None, remote_claude_home='~/.claude', cache_dir=PosixPath('/home/runner/.cache/xa/remotes'), stale_threshold_sec=3600, claude_bin='claude', tmux_bin='tmux', ssh_bin='ssh', rsync_bin='rsync')

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

URL detection is left to the caller’s next [`sync()`](#xa.hosts.ssh.SSHHost.sync) + listing —
we don’t round-trip waiting for the bridge URL here (that would
hold an SSH connection open for up to 2 minutes).

* **Return type:**
  [`SpawnResult`](xa.claude_cli.html.md#xa.claude_cli.SpawnResult)

#### sync(, force=False)

Pull `<remote_claude_home>/projects/` and `/sessions/` into the cache.

Non-existent remote directories are tolerated (rsync returns 23).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)
