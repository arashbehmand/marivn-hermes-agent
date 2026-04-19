"""
Marvin canonical store — SQLite-backed behavioral ledger.

Four tables: observations (raw signals), facts (compiled beliefs),
goals (user intentions), outcomes (what Marvin did + user response).
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_marvin_home() -> Path:
    """Return Marvin's data directory, respecting HERMES_HOME."""
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    base = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    return base / "marvin"


class MarvinStore:
    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            home = get_marvin_home()
            home.mkdir(parents=True, exist_ok=True)
            db_path = home / "marvin.db"
        self.db_path = db_path
        self._local = threading.local()
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self.db_path),
                detect_types=sqlite3.PARSE_DECLTYPES,
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    @contextmanager
    def _tx(self):
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_schema(self):
        with self._tx() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '{}',
                    session_id TEXT
                );

                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    superseded_by INTEGER REFERENCES facts(id)
                );

                CREATE TABLE IF NOT EXISTS goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    area TEXT NOT NULL,
                    description TEXT NOT NULL,
                    targets TEXT NOT NULL DEFAULT '{}',
                    set_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    delivered_at TEXT NOT NULL,
                    channel TEXT NOT NULL DEFAULT 'cli',
                    user_response TEXT,
                    notes TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_obs_category
                    ON observations(category);
                CREATE INDEX IF NOT EXISTS idx_obs_observed_at
                    ON observations(observed_at);
                CREATE INDEX IF NOT EXISTS idx_facts_current
                    ON facts(superseded_by) WHERE superseded_by IS NULL;
                CREATE INDEX IF NOT EXISTS idx_goals_active
                    ON goals(active) WHERE active = 1;
                CREATE INDEX IF NOT EXISTS idx_outcomes_delivered
                    ON outcomes(delivered_at);

                CREATE TABLE IF NOT EXISTS profile (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)

    # ----- Observations -----

    def add_observation(
        self,
        source: str,
        category: str,
        content: Any = None,
        session_id: str = None,
        observed_at: str = None,
    ) -> int:
        content_json = json.dumps(content) if content is not None else "{}"
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO observations (observed_at, source, category, content, session_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (observed_at or _now_iso(), source, category, content_json, session_id),
            )
            return cur.lastrowid

    def get_observations(
        self,
        category: str = None,
        since: str = None,
        limit: int = 100,
    ) -> list[dict]:
        conn = self._get_conn()
        clauses, params = [], []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if since:
            clauses.append("observed_at >= ?")
            params.append(since)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM observations {where} ORDER BY observed_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count_observations(self, category: str, since: str) -> int:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM observations WHERE category = ? AND observed_at >= ?",
            (category, since),
        ).fetchone()
        return row["cnt"]

    # ----- Facts -----

    def add_fact(self, claim: str, confidence: float = 0.5) -> int:
        now = _now_iso()
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO facts (claim, confidence, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (claim, confidence, now, now),
            )
            return cur.lastrowid

    def get_current_facts(self) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM facts WHERE superseded_by IS NULL ORDER BY updated_at DESC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def supersede_fact(self, old_id: int, new_claim: str, confidence: float = 0.5) -> int:
        new_id = self.add_fact(new_claim, confidence)
        with self._tx() as conn:
            conn.execute(
                "UPDATE facts SET superseded_by = ?, updated_at = ? WHERE id = ?",
                (new_id, _now_iso(), old_id),
            )
        return new_id

    # ----- Goals -----

    def add_goal(
        self,
        area: str,
        description: str,
        targets: Any = None,
    ) -> int:
        targets_json = json.dumps(targets) if targets is not None else "{}"
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO goals (area, description, targets, set_at) VALUES (?, ?, ?, ?)",
                (area, description, targets_json, _now_iso()),
            )
            return cur.lastrowid

    def get_active_goals(self) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM goals WHERE active = 1 ORDER BY set_at DESC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def deactivate_goal(self, goal_id: int):
        with self._tx() as conn:
            conn.execute("UPDATE goals SET active = 0 WHERE id = ?", (goal_id,))

    # ----- Outcomes -----

    def add_outcome(
        self,
        action_type: str,
        content: str,
        channel: str = "cli",
        delivered_at: str = None,
    ) -> int:
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO outcomes (action_type, content, delivered_at, channel) "
                "VALUES (?, ?, ?, ?)",
                (action_type, content, delivered_at or _now_iso(), channel),
            )
            return cur.lastrowid

    def update_outcome_response(self, outcome_id: int, response: str, notes: str = None):
        with self._tx() as conn:
            conn.execute(
                "UPDATE outcomes SET user_response = ?, notes = ? WHERE id = ?",
                (response, notes, outcome_id),
            )

    def get_recent_outcomes(self, limit: int = 20) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM outcomes ORDER BY delivered_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ----- Profile -----

    def set_profile(self, key: str, value: str):
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO profile (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, _now_iso()),
            )

    def get_profile(self, key: str, default: str = None) -> Optional[str]:
        conn = self._get_conn()
        row = conn.execute("SELECT value FROM profile WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def get_all_profile(self) -> dict:
        conn = self._get_conn()
        rows = conn.execute("SELECT key, value FROM profile").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ----- Meta (internal state tracking) -----

    def set_meta(self, key: str, value: str):
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_meta(self, key: str, default: str = None) -> Optional[str]:
        conn = self._get_conn()
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    # ----- Aggregate queries -----

    def get_observations_between(self, since: str, until: str, category: str = None) -> list[dict]:
        conn = self._get_conn()
        if category:
            rows = conn.execute(
                "SELECT * FROM observations WHERE observed_at >= ? AND observed_at < ? AND category = ? "
                "ORDER BY observed_at ASC",
                (since, until, category),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM observations WHERE observed_at >= ? AND observed_at < ? "
                "ORDER BY observed_at ASC",
                (since, until),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_observation_categories(self, since: str = None) -> list[dict]:
        conn = self._get_conn()
        if since:
            rows = conn.execute(
                "SELECT category, COUNT(*) as count FROM observations WHERE observed_at >= ? "
                "GROUP BY category ORDER BY count DESC",
                (since,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT category, COUNT(*) as count FROM observations "
                "GROUP BY category ORDER BY count DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_unresponded_outcomes(self, limit: int = 10) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM outcomes WHERE user_response IS NULL ORDER BY delivered_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ----- Helpers -----

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        for key in ("content", "targets"):
            if key in d and isinstance(d[key], str):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d
