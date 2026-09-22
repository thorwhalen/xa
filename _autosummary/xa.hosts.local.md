# xa.hosts.local

Local in-process host.

All discovery and actions hit the local filesystem / tmux / `claude`
binary directly. Most `xa` users never instantiate any other host.

### Classes

| [`LocalHost`](#xa.hosts.local.LocalHost)([name, claude_home, claude_bin, ...])   | The machine `xa` is running on.   |
|----------------------------------------------------------------------------------------------------|-----------------------------------|

### *class* xa.hosts.local.LocalHost(name='local', , claude_home=PosixPath('/home/runner/.claude'), claude_bin='claude', tmux_bin='tmux', alive_predicate=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The machine `xa` is running on.

#### sync(, force=False)

No-op — local has nothing to sync.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)
