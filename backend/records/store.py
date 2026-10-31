"""Small helpers for the shared record. Table and column names only ever come
from this codebase; every value is sent as a parameter."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Iterable, List, Optional


def new_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def insert(db: sqlite3.Connection, table: str, **values) -> str:
    values.setdefault("id", new_id())
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    db.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(values.values()))
    return values["id"]


def update(db: sqlite3.Connection, table: str, row_id: str, **values) -> None:
    assignments = ", ".join(f"{column} = ?" for column in values)
    db.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", (*values.values(), row_id))


def one(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row is not None else None


def rows(result: Iterable[sqlite3.Row]) -> List[dict]:
    return [dict(row) for row in result]


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def clean(values: dict) -> dict:
    """Keeps only the fields a request actually sent."""
    return {key: value for key, value in values.items() if value is not None}
