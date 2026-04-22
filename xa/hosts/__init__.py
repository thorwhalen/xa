"""Host abstraction — local, SSH, HTTP backends.

A ``Host`` is anything that can enumerate Claude Code sessions and
perform lifecycle actions on them (spawn / resume / kill). All transports
return the same :class:`xa.sessions.Session` shape so higher layers
don't care whether sessions live on this machine, on another machine
over SSH, or behind another ``xa`` server over HTTPS.

The protocol is duck-typed (``runtime_checkable``), so user extensions
only need to implement the methods they actually use — unused methods
can raise ``NotImplementedError``.
"""

from __future__ import annotations

from typing import Iterator, Optional, Protocol, runtime_checkable

from xa.claude_cli import SpawnResult


@runtime_checkable
class Host(Protocol):
    """Duck-typed interface every transport satisfies."""

    name: str
    kind: str  # 'local' / 'ssh' / 'http'

    # Discovery ----------------------------------------------------------- #

    def iter_sessions(
        self,
        *,
        project_slug: Optional[str] = None,
        include_live: bool = True,
    ) -> Iterator["Session"]:  # noqa: F821 — forward-referenced
        ...

    # Actions ------------------------------------------------------------- #

    def spawn(self, name: str, *, cwd: str, **opts) -> SpawnResult: ...

    def resume(
        self, claude_session_id: str, *, cwd: str, **opts
    ) -> SpawnResult: ...

    def kill(self, name: str) -> None: ...

    def capture_pane(self, name: str, *, lines: int = 200) -> str: ...

    # Cache management (no-op for local; rsync for SSH; HTTP fetch for http)
    def sync(self, *, force: bool = False) -> None: ...


# Concrete hosts. Imports are guarded so that installing xa without
# e.g. fastapi doesn't break the basic Host protocol import.
from xa.hosts.local import LocalHost  # noqa: E402
from xa.hosts.ssh import SSHHost  # noqa: E402
from xa.hosts.http import HTTPHost  # noqa: E402


def default_hosts() -> dict[str, Host]:
    """The out-of-the-box registry: one local host, nothing else."""
    return {"local": LocalHost()}


__all__ = ["Host", "LocalHost", "SSHHost", "HTTPHost", "default_hosts"]
