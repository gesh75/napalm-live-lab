#!/usr/bin/env python3
"""Persistent test-run store (stdlib sqlite3) — survives launchd restarts.

A SuiteRun is the result of running one suite: totals + per-check results. Stored
in OUTPUT_DIR/test_runs.db so history/trends/exports outlive the (frequent)
launchd KeepAlive respawns that wipe the in-memory _jobs dict.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR

DB_PATH = OUTPUT_DIR / "test_runs.db"
_lock = threading.Lock()


@dataclass
class SuiteRun:
    run_id: str
    suite_id: str
    suite_name: str
    fabric: str
    status: str = "running"          # running | done | error
    started: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished: str = ""
    totals: dict = field(default_factory=lambda: {"passed": 0, "failed": 0, "errored": 0, "total": 0})
    results: list = field(default_factory=list)   # list of CheckResult-as-dict
    error: str = ""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _lock, _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id     TEXT PRIMARY KEY,
                suite_id   TEXT, suite_name TEXT, fabric TEXT,
                status     TEXT, started TEXT, finished TEXT,
                passed     INTEGER, failed INTEGER, errored INTEGER, total INTEGER,
                run_json   TEXT
            )""")


def save_run(run: SuiteRun) -> None:
    init_db()
    t = run.totals
    with _lock, _conn() as c:
        c.execute(
            """INSERT INTO runs
               (run_id,suite_id,suite_name,fabric,status,started,finished,passed,failed,errored,total,run_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(run_id) DO UPDATE SET
                 status=excluded.status, finished=excluded.finished,
                 passed=excluded.passed, failed=excluded.failed,
                 errored=excluded.errored, total=excluded.total, run_json=excluded.run_json""",
            (run.run_id, run.suite_id, run.suite_name, run.fabric, run.status,
             run.started, run.finished, t.get("passed", 0), t.get("failed", 0),
             t.get("errored", 0), t.get("total", 0), json.dumps(asdict(run), default=str)),
        )


def get_run(run_id: str) -> dict | None:
    init_db()
    with _lock, _conn() as c:
        row = c.execute("SELECT run_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
    return json.loads(row["run_json"]) if row else None


def list_runs(limit: int = 50, suite_id: str | None = None) -> list[dict]:
    init_db()
    q = ("SELECT run_id,suite_id,suite_name,fabric,status,started,finished,"
         "passed,failed,errored,total FROM runs")
    args: list = []
    if suite_id:
        q += " WHERE suite_id=?"
        args.append(suite_id)
    q += " ORDER BY started DESC LIMIT ?"
    args.append(int(limit))
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]
