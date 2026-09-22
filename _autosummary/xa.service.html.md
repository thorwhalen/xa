# xa.service

FastAPI service for `xa`.

`build_api(...)` returns a mountable `FastAPI` app that reimplements
the `edualc` route surface on top of the `xa` primitives. The service
is deliberately a function (not a module that imports-and-runs) so it can
be embedded into larger hosts (enlace / tw_platform) or launched
standalone via `xa serve`.

FastAPI is an optional dependency. Import `xa.service` only when you
have installed `xa[service]` (pulls in `fastapi` + `uvicorn`).

### Module Attributes

| [`RC_ENABLE_TIMEOUT_SEC`](#xa.service.RC_ENABLE_TIMEOUT_SEC)   | How long POST /sessions/{id}/remote-control waits for claude to write its bridge id after the command is sent.   |
|--------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------|

### Functions

| [`allow_all`](#xa.service.allow_all)()                              | No-op auth dependency.                                            |
|-------------------------------------------------------------------------------------------|-------------------------------------------------------------------|
| [`build_api`](#xa.service.build_api)(\*[, auth, events_store, ...]) | Return a `FastAPI` app exposing `xa`'s session + archive surface. |
| [`make_basic_auth`](#xa.service.make_basic_auth)(username, password)      | Build a FastAPI dependency that enforces HTTP Basic auth.         |

### Classes

| [`Captcha`](#xa.service.Captcha)(\*, key[, ttl_sec])   | Stateless 4-letter captcha with an HMAC-signed token.   |
|--------------------------------------------------------------------------------|---------------------------------------------------------|

### *class* xa.service.Captcha(, key, ttl_sec=120)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Stateless 4-letter captcha with an HMAC-signed token.

The signing key never leaves the server. Tokens are
`b64(CHALLENGE.EXPIRY).SIG` where SIG = HMAC-SHA256(key, payload).
Multi-worker safe; no server-side state. Pass one instance of this
class to [`build_api()`](#xa.service.build_api) to enable captcha-gated deletes.

#### issue()

Return `(token, challenge, ttl_sec)`.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`int`](https://docs.python.org/3/builtins/functions.html#int)]

### xa.service.RC_ENABLE_TIMEOUT_SEC *= 15.0*

How long POST /sessions/{id}/remote-control waits for claude to write
its bridge id after the command is sent. The TUI acks well before the
session file is rewritten, so a bare read would always miss.

### xa.service.allow_all()

No-op auth dependency. Useful when the service is behind an external
auth layer (enlace, reverse-proxy mTLS) that already gated the request.

FastAPI inspects the signature to decide what to inject; keep this
nullary so it isn’t treated as query-parameter binding.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### xa.service.build_api(\*, auth=<function allow_all>, events_store=None, pane_store=None, captcha=None, claude_home=PosixPath('/home/runner/.claude'), claude_bin='claude', session_prefix='xa-', title='xa', version='0.1', include_webui=False, default_folder=None)

Return a `FastAPI` app exposing `xa`’s session + archive surface.

The caller composes this app into whatever process they have: mount
under `/api/xa/` inside enlace, or run standalone via `uvicorn`.

### xa.service.make_basic_auth(username, password)

Build a FastAPI dependency that enforces HTTP Basic auth.

Returns a callable suitable for `Depends(...)`. Uses
`secrets.compare_digest` to avoid timing leaks.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]
