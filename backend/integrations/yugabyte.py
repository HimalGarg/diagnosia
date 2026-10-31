"""Technical execution metadata in a separately provisioned YugabyteDB, through YSQL.

This is Diagnosia's own database, and has nothing to do with the YugabyteDB inside Meko, which is
never connected to. It sits outside every request path: only the delivery worker writes to it, so
an outage can't touch a clinical workflow.

YSQL speaks the PostgreSQL protocol, so a connection alone proves nothing about what's on the other
end. identity() asks the server, and a plain PostgreSQL server is never reported as YugabyteDB.
"""
import logging
import re
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.config import settings

logger = logging.getLogger("diagnosia.integrations")
# The pool's own messages quote the server's error text, which can name a user or a host.
logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)

MIGRATIONS = Path(__file__).parent / "migrations"
CONNECT_TIMEOUT_SECONDS = 5
STATEMENT_TIMEOUT_MS = 10000


class YugabyteStore:
    def __init__(self, pool):
        self.pool = pool

    def identity(self) -> Dict[str, object]:
        """Which server this really is. `version()` carries a -YB- build, and yb_servers() exists only on YugabyteDB."""
        with self.pool.connection() as connection:
            version = connection.execute("SELECT version()").fetchone()[0]
        match = re.search(r"-YB-([0-9.]+)", version)
        nodes = None
        if match:
            with self.pool.connection() as connection:
                nodes = connection.execute("SELECT count(*) FROM yb_servers()").fetchone()[0]
        return {"is_yugabytedb": bool(match and nodes), "yugabyte_version": match.group(1) if match else None, "nodes": nodes}

    def migrate(self) -> List[str]:
        """Each file runs once. Safe to repeat, and nothing in a migration destroys data."""
        applied = []
        with self.pool.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS integration_schema_migrations (
                    migration_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('applied', 'failed')),
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            done = {row[0] for row in connection.execute("SELECT migration_id FROM integration_schema_migrations WHERE status = 'applied'").fetchall()}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            version = int(path.name.split("_")[0])
            try:
                with self.pool.connection() as connection:
                    # One statement at a time, because each is safe to repeat if a later one fails.
                    for statement in _statements(path.read_text(encoding="utf-8")):
                        connection.execute(statement)
            except Exception:
                self._record_migration(path.name, version, "failed")
                raise
            self._record_migration(path.name, version, "applied")
            applied.append(path.name)
        return applied

    def _record_migration(self, name: str, version: int, status: str) -> None:
        with self.pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO integration_schema_migrations (migration_id, version, status) VALUES (%s, %s, %s)
                ON CONFLICT (migration_id) DO UPDATE SET status = excluded.status, applied_at = now()
                """,
                (name, version, status),
            )

    def ping(self) -> None:
        with self.pool.connection() as connection:
            connection.execute("SELECT 1")

    def save_runs(self, runs: List[Dict]) -> None:
        """One transaction per run, so a run and its attempts always arrive together.

        A run is sent when it begins and again when it ends, so this updates as well as inserts."""
        for run in runs:
            with self.pool.connection() as connection, connection.transaction():
                connection.execute(
                    """
                    INSERT INTO integration_agent_runs
                        (run_id, workflow_ref, module, tool, status, provider, model, fallback_used, fallback_provider, fallback_model,
                         error_category, trace_ref, attempt_count, started_at, finished_at, duration_ms)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id) DO UPDATE SET
                        status = excluded.status, provider = excluded.provider, model = excluded.model,
                        fallback_used = excluded.fallback_used, fallback_provider = excluded.fallback_provider,
                        fallback_model = excluded.fallback_model, error_category = excluded.error_category,
                        attempt_count = excluded.attempt_count, finished_at = excluded.finished_at,
                        duration_ms = excluded.duration_ms, updated_at = now()
                    """,
                    (
                        run["run_id"], run["workflow_ref"], run["module"], run["tool"], run["status"], run["provider"], run["model"],
                        bool(run["fallback_used"]), run["fallback_provider"], run["fallback_model"], run["error_category"],
                        run["trace_ref"], run["attempt_count"], run["started_at"], run["finished_at"], run["duration_ms"],
                    ),
                )
                for attempt in run["attempts"]:
                    connection.execute(
                        """
                        INSERT INTO integration_task_attempts
                            (attempt_id, run_id, attempt_number, provider, model, status, error_category, started_at, finished_at, duration_ms)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (attempt_id) DO NOTHING
                        """,
                        (
                            attempt["attempt_id"], run["run_id"], attempt["attempt_number"], attempt["provider"], attempt["model"],
                            attempt["status"], attempt["error_category"], attempt["started_at"], attempt["finished_at"], attempt["duration_ms"],
                        ),
                    )

    def save_handoff(self, handoff: dict, state: str = "recorded", context_ref: Optional[str] = None) -> None:
        """Keyed on the source event, so a retried delivery updates one row rather than adding another."""
        with self.pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO integration_handoffs
                    (handoff_id, correlation_id, workflow_ref, source_module, target_module, source_event_id, handoff_state,
                     human_review_required, context_ref, meko_datapack_ref, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_event_id) DO UPDATE SET
                    handoff_state = excluded.handoff_state, context_ref = excluded.context_ref, updated_at = now()
                """,
                (
                    handoff["handoff_id"], handoff["correlation_id"], handoff["workflow_ref"], handoff["source_module"],
                    handoff["target_module"], handoff["source_event_ids"][0], state, handoff["human_review_required"],
                    context_ref, settings.meko_datapack_id or None, handoff["created_at"],
                ),
            )

    def handoffs(self, workflow_ref: str) -> List[dict]:
        with self.pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT source_module, target_module, created_at, human_review_required, handoff_state, context_ref
                FROM integration_handoffs WHERE workflow_ref = %s ORDER BY created_at
                """,
                (workflow_ref,),
            ).fetchall()
        return [
            {
                "source_module": row[0], "target_module": row[1], "created_at": row[2].isoformat(),
                "human_review_required": row[3], "handoff_state": row[4], "context_ref": row[5],
            }
            for row in rows
        ]

    def run_summary(self) -> List[dict]:
        with self.pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT tool, count(*), count(*) FILTER (WHERE status = 'failed'), count(*) FILTER (WHERE attempt_count > 1)
                FROM integration_agent_runs GROUP BY tool ORDER BY tool
                """
            ).fetchall()
        return [{"tool": row[0], "runs": row[1], "failed": row[2], "needed_fallback": row[3]} for row in rows]

    def operations(self) -> Dict[str, object]:
        """Fixed questions, with no user input in them. No endpoint runs SQL it was given."""
        with self.pool.connection() as connection:
            runs = connection.execute(
                """
                SELECT count(*), count(*) FILTER (WHERE status = 'succeeded'), count(*) FILTER (WHERE status = 'failed'),
                       count(*) FILTER (WHERE status = 'interrupted'), count(*) FILTER (WHERE status = 'running'),
                       count(*) FILTER (WHERE fallback_used), avg(duration_ms), COALESCE(sum(GREATEST(attempt_count - 1, 0)), 0)
                FROM integration_agent_runs
                """
            ).fetchone()
            handoffs = connection.execute("SELECT count(*) FROM integration_handoffs").fetchone()[0]
        return {
            "total_runs": runs[0], "successful_runs": runs[1], "failed_runs": runs[2], "interrupted_runs": runs[3], "running_runs": runs[4],
            "fallback_runs": runs[5], "average_duration_ms": float(runs[6]) if runs[6] is not None else None, "retry_count": int(runs[7]),
            "handoff_count": handoffs,
        }

    def verify(self) -> List[Tuple[str, bool, str]]:
        """Every step of the health check, on a throwaway row. Safe against real configuration."""
        checks: List[Tuple[str, bool, str]] = []
        found = self.identity()
        checks.append(("server is YugabyteDB", bool(found["is_yugabytedb"]), f"version {found['yugabyte_version']}, {found['nodes']} node(s)" if found["is_yugabytedb"] else "this server is not YugabyteDB"))
        with self.pool.connection() as connection:
            role = connection.execute("SELECT has_database_privilege(current_user, current_database(), 'CREATE')").fetchone()[0]
        checks.append(("role can create tables", bool(role), "yes" if role else "no CREATE permission"))
        self.migrate()
        with self.pool.connection() as connection:
            tables = {row[0] for row in connection.execute("SELECT tablename FROM pg_tables WHERE tablename LIKE 'integration_%%'").fetchall()}
        wanted = {"integration_agent_runs", "integration_task_attempts", "integration_handoffs", "integration_schema_migrations"}
        checks.append(("migrations applied", wanted <= tables, f"{len(wanted & tables)} of {len(wanted)} tables"))
        run_id = f"run_health_{uuid.uuid4().hex}"
        row = (run_id, "health_check", "health_check")
        insert = "INSERT INTO integration_agent_runs (run_id, module, tool, status, started_at) VALUES (%s, %s, %s, 'succeeded', now())"
        with self.pool.connection() as connection:
            connection.execute(insert, row)
        with self.pool.connection() as connection:
            stored = connection.execute("SELECT status FROM integration_agent_runs WHERE run_id = %s", (run_id,)).fetchone()
            connection.execute("DELETE FROM integration_agent_runs WHERE run_id = %s", (run_id,))
        checks.append(("write and read back", stored is not None, "row stored, read and removed"))
        rolled_back = f"run_health_{uuid.uuid4().hex}"
        try:
            with self.pool.connection() as connection, connection.transaction():
                connection.execute(insert, (rolled_back, "health_check", "health_check"))
                raise _Rollback
        except _Rollback:
            pass
        with self.pool.connection() as connection:
            leftover = connection.execute("SELECT 1 FROM integration_agent_runs WHERE run_id = %s", (rolled_back,)).fetchone()
        checks.append(("transaction rolls back", leftover is None, "an abandoned transaction left nothing behind"))
        return checks


class _Rollback(Exception):
    pass


def _statements(script: str) -> List[str]:
    lines = [line for line in script.splitlines() if not line.strip().startswith("--")]
    return [statement.strip() for statement in "\n".join(lines).split(";") if statement.strip()]


_store: Optional[YugabyteStore] = None


def store() -> Optional[YugabyteStore]:
    """None unless switched on and configured."""
    global _store
    if not (settings.yugabyte_enabled and settings.yugabyte_dsn):
        return None
    if _store is None:
        from psycopg_pool import ConnectionPool

        pool = ConnectionPool(
            settings.yugabyte_dsn,
            min_size=1,
            max_size=settings.yugabyte_pool_max,
            timeout=CONNECT_TIMEOUT_SECONDS,
            kwargs={"connect_timeout": CONNECT_TIMEOUT_SECONDS, "options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"},
            open=False,
        )
        try:
            pool.open(wait=True, timeout=CONNECT_TIMEOUT_SECONDS + 1)
            created = YugabyteStore(pool)
            created.migrate()
        except Exception:
            pool.close()
            raise
        _store = created
    return _store


def reset() -> None:
    global _store
    if _store is not None:
        _store.pool.close()
    _store = None
