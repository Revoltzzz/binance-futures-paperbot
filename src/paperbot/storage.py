"""SQLite-backed durable snapshots for a paper-trading worker."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class SessionConflict(RuntimeError):
    """Raised when a session snapshot changed since it was read."""


class SQLitePaperStore:
    """Store versioned paper-session snapshots with atomic compare-and-swap writes."""

    def __init__(self, database_path: str | Path):
        self.path = Path(database_path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_sessions (
                    session_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                )
                """
            )

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(str(self.path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load(self, session_id: str) -> tuple[int, dict[str, Any]] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT revision, state_json FROM paper_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            state = json.loads(row["state_json"])
        except json.JSONDecodeError as exc:
            raise ValueError(f"Stored paper-session JSON is corrupt for {session_id}") from exc
        if not isinstance(state, dict):
            raise ValueError(f"Stored paper-session state is malformed for {session_id}")
        return int(row["revision"]), state

    def save(
        self,
        session_id: str,
        state: dict[str, Any],
        *,
        expected_revision: int | None,
    ) -> int:
        """Persist a snapshot; expected_revision=None means create-only."""
        payload = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM paper_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if expected_revision is None:
                if row is not None:
                    raise SessionConflict(f"Session already exists: {session_id}")
                revision = 1
                connection.execute(
                    "INSERT INTO paper_sessions (session_id, revision, state_json) VALUES (?, ?, ?)",
                    (session_id, revision, payload),
                )
            else:
                if row is None or int(row["revision"]) != expected_revision:
                    actual = None if row is None else int(row["revision"])
                    raise SessionConflict(
                        f"Session revision conflict for {session_id}: "
                        f"expected {expected_revision}, found {actual}"
                    )
                revision = expected_revision + 1
                cursor = connection.execute(
                    """
                    UPDATE paper_sessions
                    SET revision = ?, state_json = ?,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE session_id = ? AND revision = ?
                    """,
                    (revision, payload, session_id, expected_revision),
                )
                if cursor.rowcount != 1:
                    raise SessionConflict(f"Session changed while saving: {session_id}")
        return revision
