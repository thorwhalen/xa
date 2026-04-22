"""xa — tools for managing remote Claude Code sessions."""

from xa.claude_fs import (
    DEFAULT_CLAUDE_HOME,
    HistoryEntry,
    TranscriptForensics,
    TranscriptMeta,
    encode_project_slug,
    history_iter,
    iter_ephemeral_sessions,
    iter_project_slugs,
    iter_transcript_events,
    iter_transcript_files,
    parse_project_slug,
    read_ephemeral_session,
    transcript_forensics,
    transcript_metadata,
    transcript_path,
)
from xa.claude_cli import (
    SpawnResult,
    resolve_bridge_url,
    resume_session,
    spawn_session,
)
from xa.sessions import (
    Session,
    SessionState,
    get_session,
    iter_local_sessions,
    kill_session,
    list_sessions,
    resume,
)
from xa.store import (
    FileStore,
    JsonLinesStore,
    default_events_store,
    default_pane_store,
)
from xa.archive import (
    ArchiveRecord,
    DeathReason,
    append_created,
    append_gone,
    append_url_acquired,
    classify_death,
    reconcile,
    records,
)
from xa.hosts import (
    HTTPHost,
    Host,
    LocalHost,
    SSHHost,
    default_hosts,
)
from xa.config import Settings, default_config_path, load, load_hosts

__version__ = "0.1.0"

__all__ = [
    # claude_fs
    "DEFAULT_CLAUDE_HOME",
    "HistoryEntry",
    "TranscriptForensics",
    "TranscriptMeta",
    "encode_project_slug",
    "history_iter",
    "iter_ephemeral_sessions",
    "iter_project_slugs",
    "iter_transcript_events",
    "iter_transcript_files",
    "parse_project_slug",
    "read_ephemeral_session",
    "transcript_forensics",
    "transcript_metadata",
    "transcript_path",
    # claude_cli
    "SpawnResult",
    "resolve_bridge_url",
    "resume_session",
    "spawn_session",
    # sessions
    "Session",
    "SessionState",
    "get_session",
    "iter_local_sessions",
    "kill_session",
    "list_sessions",
    "resume",
    # store
    "FileStore",
    "JsonLinesStore",
    "default_events_store",
    "default_pane_store",
    # archive
    "ArchiveRecord",
    "DeathReason",
    "append_created",
    "append_gone",
    "append_url_acquired",
    "classify_death",
    "reconcile",
    "records",
    # hosts
    "HTTPHost",
    "Host",
    "LocalHost",
    "SSHHost",
    "default_hosts",
    # config
    "Settings",
    "default_config_path",
    "load",
    "load_hosts",
]
