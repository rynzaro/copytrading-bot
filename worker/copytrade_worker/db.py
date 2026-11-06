"""SQLite open + schema. Minimal version from ticket 0006; ticket 0005 owns this file."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = Path(__file__).with_name("db.sql")


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    con = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text())
    return con


def now_ms() -> int:
    return int(time.time() * 1000)
