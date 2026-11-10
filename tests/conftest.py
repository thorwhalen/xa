"""Shared test setup: every tmux call in the suite talks to a private tmux server.

tmux picks its server from ``$TMUX`` (inside a pane) or from the socket under
``$TMUX_TMPDIR``. Pointing both away from the real ones, before any test runs,
means a test can create, capture and kill sessions without ever reaching the
sessions a person (or a fleet of agents) is running on this machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import pytest


@pytest.fixture(scope="session", autouse=True)
def _private_tmux_server():
    saved = {k: os.environ.get(k) for k in ("TMUX", "TMUX_PANE", "TMUX_TMPDIR")}
    tmpdir = tempfile.mkdtemp(prefix="xa-tmux-")
    os.environ.pop("TMUX", None)
    os.environ.pop("TMUX_PANE", None)
    os.environ["TMUX_TMPDIR"] = tmpdir
    try:
        yield tmpdir
    finally:
        if shutil.which("tmux") and hasattr(os, "getuid"):
            # The private socket by its full path, so this can only ever
            # stop the server the suite itself started.
            sock = os.path.join(tmpdir, f"tmux-{os.getuid()}", "default")
            subprocess.run(
                ["tmux", "-S", sock, "kill-server"], capture_output=True, check=False
            )
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmpdir, ignore_errors=True)
