"""The environment and flags a fresh ``claude`` process starts with: account and permissions.

No xa imports and no third-party ones: this is the lowest layer, so that any
launcher (xa's tmux panes, crowsnest's spawner, a future terminal-tab provider)
can import one copy of the rules instead of re-deriving them.

**Account.** Claude Code picks its account by ``CLAUDE_CONFIG_DIR`` (config,
credentials and transcripts all move with it), and reaches the *default* home
only by the variable's absence. A command line handed to tmux runs in a shell
whose environment is the tmux *server's*, which belongs to whoever started the
server: possibly another account, possibly a session long gone, whose identity
markers (``CLAUDECODE``, ``CLAUDE_CODE_SESSION_ID``, its messaging socket, its
effort) would make the new process register as that dead session's child.
:func:`env_prefix` therefore states every variable it controls either way round:
set when the child should have it, unset (``env -u``) when not. ``tmux -e`` can
only set, so it cannot express the default home.

**Permissions.** :func:`permission_args` turns a mode into flags,
:func:`bypass_refused` says where claude would refuse the bypass flags (root,
unless ``IS_SANDBOX=1``; the refusal is invisible behind tmux), and
:func:`resume_permission` picks the mode a resumed session comes back in.

Moved here from ``crowsnest.spawn`` (thorwhalen/xa#23), whose tests and
derivation notes it keeps; crowsnest is to import it from here.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Optional


#: Claude Code's account variable, and the home it means when unset.
HOME_ENV_VAR = "CLAUDE_CONFIG_DIR"
DFLT_HOME = "~/.claude"

#: The variables that select which account a session runs under. A shell profile
#: scheme commonly pairs ``CLAUDE_CONFIG_DIR`` with a ``CLAUDE_PROFILE`` label and
#: re-derives one from the other, so the two travel together or not at all.
ACCOUNT_VARS = (HOME_ENV_VAR, "CLAUDE_PROFILE")
_PROFILE_VAR = "CLAUDE_PROFILE"

#: Variables a new session must not inherit whatever they are set to: they would
#: override the account the home selects (an API key that bills elsewhere).
DROPPED_VARS = ("ANTHROPIC_API_KEY",)

#: The ``CLAUDE*`` variables that identify *one running session* -- who it is, what
#: started it, how to talk to it -- named so that :func:`env_prefix` can unset them
#: whether or not *this* process has them (a tmux server keeps the markers of the
#: session that started it and hands them to every pane it opens afterwards).
#:
#: *Identity, not configuration.* Claude Code reads hundreds of ``CLAUDE*``
#: variables, nearly all settings a person may deliberately export; those stay.
#: Derived by matching the identity families (session and background-job plumbing,
#: messaging and bridge sockets, job and project directories) against the name table
#: of the ``claude`` binary (``strings $(command -v claude) | grep -oE
#: 'CLAUDE[A-Z0-9_]*'``), not typed out. Re-derive it when Claude Code grows a new
#: family; ``test_session_vars_covers_every_marker_this_live_session_carries`` is the
#: tripwire. Account variables are deliberately absent: see :data:`ACCOUNT_VARS`.
SESSION_VARS = (
    "CLAUDECODE",
    "CLAUDE_AGENT_SDK_VERSION",
    "CLAUDE_BG_AUTH_SNAPSHOT_PATH",
    "CLAUDE_BG_BACKEND",
    "CLAUDE_BG_CLAIM_AUTH",
    "CLAUDE_BG_DISPATCHER_RATE_LIMIT_TIER",
    "CLAUDE_BG_DISPATCHER_SUBSCRIPTION_TYPE",
    "CLAUDE_BG_ISOLATION",
    "CLAUDE_BG_MEMORY_TOGGLED_OFF",
    "CLAUDE_BG_POST_CLEAR_RESPAWN",
    "CLAUDE_BG_PTY_AUTH",
    "CLAUDE_BG_RENDEZVOUS_SOCK",
    "CLAUDE_BG_RV_AUTH",
    "CLAUDE_BG_SESSION_PERMISSION_RULES",
    "CLAUDE_BG_SOCKET_TOKENS_PATH",
    "CLAUDE_BG_SOURCE",
    "CLAUDE_BG_STARTUP_WEDGE_MS",
    "CLAUDE_BG_TCC_DISCLAIMED",
    "CLAUDE_CODE_BRIDGE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_CLOUD_SESSION_ID",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_REMOTE_SESSION_ID",
    "CLAUDE_CODE_REMOTE_SESSION_ORIGIN",
    "CLAUDE_CODE_REMOTE_SESSION_UUID",
    "CLAUDE_CODE_SESSION_ACCESS_TOKEN",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_SESSION_KIND",
    "CLAUDE_CODE_SESSION_LOG",
    "CLAUDE_CODE_SESSION_NAME",
    "CLAUDE_CODE_SESSION_ORIGIN",
    "CLAUDE_CODE_SPAWN_TIMESTAMP_MS",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_TRIGGER_ID",
    "CLAUDE_CODE_WORKER_EPOCH",
    "CLAUDE_EFFORT",
    "CLAUDE_JOB_DIR",
    "CLAUDE_PID",
    "CLAUDE_PROJECT_DIR",
    "CLAUDE_PROJECT_UUID",
    "CLAUDE_SESSION_ID",
)

#: Under this variable (``"1"``) Claude Code accepts the bypass flag from root.
#: :func:`env_prefix` hands it on and never unsets it: a launcher may set it.
SANDBOX_VAR = "IS_SANDBOX"

#: Claude Code's name for "skip every permission check", and its aliases.
BYPASS_MODE = "bypassPermissions"
BYPASS_MODES = frozenset({"bypass", BYPASS_MODE, "dangerously-skip-permissions"})
BYPASS_FLAG = "--dangerously-skip-permissions"
ALLOW_BYPASS_FLAG = "--allow-dangerously-skip-permissions"
MODE_FLAG = "--permission-mode"

#: The modes ``claude --permission-mode`` accepts (``default`` is the older name of
#: ``manual``). A mode a newer claude adds goes here.
KNOWN_MODES = frozenset(
    {"acceptEdits", "auto", BYPASS_MODE, "default", "dontAsk", "manual", "plan"}
)


# --------------------------------------------------------------------------- #
# permissions
# --------------------------------------------------------------------------- #


def canonical_permission_mode(mode: Optional[str]) -> str:
    """``mode`` spelled as claude spells it; ``''`` stays ``''`` (claude's own default).

    Case-insensitive; every bypass alias is ``'bypassPermissions'``. An unknown name
    raises ``ValueError`` listing the known ones.

    >>> canonical_permission_mode('Bypass'), canonical_permission_mode('acceptedits')
    ('bypassPermissions', 'acceptEdits')
    """
    mode = (mode or "").strip()
    if not mode:
        return ""
    folded = mode.lower()
    if folded in {m.lower() for m in BYPASS_MODES}:
        return BYPASS_MODE
    for known in KNOWN_MODES:
        if known.lower() == folded:
            return known
    raise ValueError(
        f"not a permission mode: {mode!r}; use one of {sorted(KNOWN_MODES)} or 'bypass'"
    )


def permission_args(mode: Optional[str], *, allow_bypass: bool = False) -> list[str]:
    """The flags that start claude in permission ``mode`` (``''``/``None``: none).

    ``allow_bypass`` keeps bypass *reachable* (shift-tab) for a session that starts
    in another mode.

    >>> permission_args('bypass')
    ['--dangerously-skip-permissions']
    >>> permission_args('auto', allow_bypass=True)
    ['--allow-dangerously-skip-permissions', '--permission-mode', 'auto']
    >>> permission_args('')
    []
    """
    mode = canonical_permission_mode(mode)
    if mode == BYPASS_MODE:
        return [BYPASS_FLAG]
    args = [ALLOW_BYPASS_FLAG] if allow_bypass else []
    if mode:
        args += [MODE_FLAG, mode]
    return args


def bypass_refused(
    *,
    euid: Optional[int] = None,
    environ: Optional[dict] = None,
    root_bypass_ok: bool = False,
) -> bool:
    """Would claude refuse the bypass flags here? Root, unsandboxed, not vouched for.

    ``root_bypass_ok`` is for a launcher known to set ``IS_SANDBOX`` itself.

    >>> bypass_refused(euid=0, environ={}), bypass_refused(euid=0, environ={'IS_SANDBOX': '1'})
    (True, False)
    >>> bypass_refused(euid=501, environ={})
    False
    """
    if euid is None:
        euid = os.geteuid() if hasattr(os, "geteuid") else -1
    env = os.environ if environ is None else environ
    return euid == 0 and env.get(SANDBOX_VAR) != "1" and not root_bypass_ok


def resume_permission(
    mode: str,
    *,
    bypass_seen: bool,
    euid: Optional[int] = None,
    environ: Optional[dict] = None,
) -> tuple[str, bool, Optional[str]]:
    """``(mode, allow_bypass, warning)`` for resuming a session last seen in ``mode``.

    The session comes back in the mode it was last in. If it ever reached bypass
    (``bypass_seen``) it was started with the bypass flag, so bypass stays reachable
    even when it was last in another mode. Where claude would refuse the bypass flags
    (:func:`bypass_refused`) they are dropped with a warning: the machine's own
    settings then decide, as they would for a person there. A mode this module does
    not know (renamed in a newer claude) is dropped with a warning too, since claude
    would refuse it at startup, invisibly, behind tmux.

    >>> resume_permission('auto', bypass_seen=True, euid=501)
    ('auto', True, None)
    >>> resume_permission('bypassPermissions', bypass_seen=True, euid=0, environ={})[:2]
    ('', False)
    """
    try:
        mode = canonical_permission_mode(mode)
    except ValueError:
        return (
            "",
            False,
            f"unknown permission mode {mode!r} in the transcript; resuming in the account default",
        )
    allow = bypass_seen and mode != BYPASS_MODE
    if (mode == BYPASS_MODE or allow) and bypass_refused(euid=euid, environ=environ):
        kept = "" if mode == BYPASS_MODE else mode
        return (
            kept,
            False,
            (
                f"Claude Code refuses {BYPASS_FLAG} to root; resuming without it "
                f"(set {SANDBOX_VAR}=1 where the session starts to keep it)"
            ),
        )
    return mode, allow, None


# --------------------------------------------------------------------------- #
# account
# --------------------------------------------------------------------------- #


def _resolved(home) -> Path:
    """One spelling per home, so a relative path or a symlink still names the same account."""
    return Path(home).expanduser().resolve()


def child_env(
    environ: Optional[dict] = None,
    *,
    home=None,
    drop: Sequence[str] = DROPPED_VARS,
) -> dict:
    """``environ`` (default ``os.environ``) as a new session should inherit it.

    The spawning session's identity markers (:data:`SESSION_VARS`) go; the account
    and every ``CLAUDE*`` *setting* (``CLAUDE_CODE_OAUTH_TOKEN``,
    ``CLAUDE_CODE_USE_BEDROCK``, ...) stay. With
    ``home`` the child runs under that home instead: ``CLAUDE_CONFIG_DIR`` set to it,
    or *unset* when it is the default home. The ``CLAUDE_PROFILE`` label travels only
    with the account it labels. ``drop`` goes too (pass ``()`` to keep it).

    >>> child_env({'CLAUDECODE': '1', 'CLAUDE_CONFIG_DIR': '/h/b', 'PATH': '/bin'})
    {'CLAUDE_CONFIG_DIR': '/h/b', 'PATH': '/bin'}
    >>> child_env({'ANTHROPIC_API_KEY': 'sk-x', 'PATH': '/bin'})
    {'PATH': '/bin'}
    """
    environ = os.environ if environ is None else environ
    dropped = {*drop, *SESSION_VARS}
    env = {k: v for k, v in environ.items() if k not in dropped}
    if home is None:
        return env
    target = _resolved(home)
    if target != _resolved(environ.get(HOME_ENV_VAR) or DFLT_HOME):
        env.pop(_PROFILE_VAR, None)
    if target == _resolved(DFLT_HOME):
        env.pop(HOME_ENV_VAR, None)
    else:
        env[HOME_ENV_VAR] = str(target)
    return env


def env_prefix(
    env: dict,
    *,
    environ: Optional[dict] = None,
    drop: Sequence[str] = DROPPED_VARS,
    markers: Sequence[str] = SESSION_VARS,
) -> list[str]:
    """The ``env -u ... K=V ...`` tokens that make a fresh shell run a command under ``env``.

    Each name this controls -- ``markers``, the ``CLAUDE*`` names of ``environ``
    that ``env`` lacks, :data:`ACCOUNT_VARS`, ``drop`` and :data:`DROPPED_VARS` -- is
    stated either way round: set when ``env`` has it, unset when it does not, because
    the shell's environment is not this process's. Everything else (other settings,
    and so any token) is left to the shell rather than written on a command line,
    where ``ps`` and tmux would show it.

    >>> env_prefix({'CLAUDE_CONFIG_DIR': '/h/b'}, environ={}, markers=('CLAUDECODE',))
    ['env', '-u', 'CLAUDECODE', '-u', 'CLAUDE_PROFILE', '-u', 'ANTHROPIC_API_KEY', 'CLAUDE_CONFIG_DIR=/h/b']
    >>> env_prefix({}, environ={}, markers=('CLAUDECODE',), drop=())
    ['env', '-u', 'CLAUDECODE', '-u', 'CLAUDE_CONFIG_DIR', '-u', 'CLAUDE_PROFILE', '-u', 'ANTHROPIC_API_KEY']
    """
    environ = os.environ if environ is None else environ
    seen = [k for k in environ if k.startswith("CLAUDE") and k not in env]
    controlled: list[str] = []
    for k in (*markers, *seen, *ACCOUNT_VARS, *drop, *DROPPED_VARS):
        if k not in controlled:
            controlled.append(k)
    unset = [k for k in controlled if k not in env]
    stated = {*controlled, SANDBOX_VAR}
    assignments = [f"{k}={v}" for k, v in env.items() if k in stated]
    if not unset and not assignments:
        return []
    tokens = ["env"]
    for k in unset:
        tokens += ["-u", k]
    return tokens + assignments


def account_env_tokens(home, *, environ: Optional[dict] = None) -> list[str]:
    """The ``env`` prefix that runs a command under ``home``'s account, markers stripped.

    >>> toks = account_env_tokens('/h/b', environ={'CLAUDECODE': '1'})
    >>> toks[-1], ('-u', 'CLAUDECODE') in zip(toks, toks[1:])
    ('CLAUDE_CONFIG_DIR=/h/b', True)
    """
    return env_prefix(child_env(environ, home=home), environ=environ)
