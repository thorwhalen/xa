# xa.hosts.http

HTTP host — a remote `xa serve` instance.

The remote must be running `xa serve` (or an API with the same
`/sessions` + `/archive` surface) and be reachable over HTTPS.
All discovery and actions are performed through the API; no SSH keys,
no rsync. Auth is pluggable:

- `auth="basic"` → HTTP Basic with `username` / `password`
- `auth="bearer"` → `Authorization: Bearer <token>`
- `auth=None`   → no auth (host is already gated upstream)

`urllib` is used for transport so there’s no hard dep on `httpx`
or `requests`; `xa.hosts.http` works in any Python 3.10+ install.

### Classes

| [`HTTPHost`](#xa.hosts.http.HTTPHost)(name, \*, base_url[, auth, ...])   | Client for a remote `xa serve` instance.   |
|----------------------------------------------------------------------------------------------|--------------------------------------------|

### *class* xa.hosts.http.HTTPHost(name, , base_url, auth=None, username=None, password=None, token=None, timeout=30.0)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Client for a remote `xa serve` instance.

#### sync(, force=False)

HTTP sessions are fetched fresh on each listing — no separate sync.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)
