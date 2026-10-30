"""SQLite store for analysis jobs and their clips. Survives server restarts.

Tests point DB_PATH at a temp file before use.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

DB_PATH = Path(__file__).parent / "data" / "verticlip.db"

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS clips_job ON clips(job_id);
"""


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """One connection per call, serialized by a lock; commits on success."""
    with _lock:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=10)
        try:
            conn.executescript(SCHEMA)
            yield conn
            conn.commit()
        finally:
            conn.close()


def create_job(job_id: str, **fields) -> dict:
    now = time.time()
    job = {"id": job_id, "created": now, **fields}
    with _db() as c:
        c.execute("INSERT INTO jobs (id, data, updated) VALUES (?, ?, ?)",
                  (job_id, json.dumps(job), now))
    return job


def update_job(job_id: str, **fields) -> None:
    with _db() as c:
        row = c.execute("SELECT data FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        job = json.loads(row[0])
        job.update(fields)
        c.execute("UPDATE jobs SET data = ?, updated = ? WHERE id = ?",
                  (json.dumps(job), time.time(), job_id))


def get_job(job_id: str) -> Optional[dict]:
    with _db() as c:
        row = c.execute("SELECT data FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        job = json.loads(row[0])
        rows = c.execute("SELECT data FROM clips WHERE job_id = ? ORDER BY score DESC",
                         (job_id,)).fetchall()
    job["clips"] = [json.loads(r[0]) for r in rows]
    return job


def add_clips(job_id: str, clips: list[dict]) -> None:
    with _db() as c:
        c.executemany(
            "INSERT INTO clips (id, job_id, score, data) VALUES (?, ?, ?, ?)",
            [(cl["id"], job_id, float(cl.get("score", 0)), json.dumps(cl)) for cl in clips],
        )


def get_clip(clip_id: str) -> Optional[dict]:
    with _db() as c:
        row = c.execute("SELECT data FROM clips WHERE id = ?", (clip_id,)).fetchone()
    return json.loads(row[0]) if row else None


def set_publish_result(clip_id: str, platform: str, account_id: str, **fields) -> None:
    """Insert or update one per-clip, per-account publish result.

    Read-modify-write runs under the store lock, so parallel upload threads
    cannot overwrite each other's results.
    """
    with _db() as c:
        row = c.execute("SELECT data FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if row is None:
            raise KeyError(clip_id)
        clip = json.loads(row[0])
        results = clip.setdefault("publish", [])
        entry = next((r for r in results
                      if r["platform"] == platform and r["account_id"] == account_id), None)
        if entry is None:
            entry = {"platform": platform, "account_id": account_id}
            results.append(entry)
        entry.update(fields)
        c.execute("UPDATE clips SET data = ? WHERE id = ?", (json.dumps(clip), clip_id))


def list_jobs(limit: int = 100) -> list[dict]:
    """Newest first. Each job carries its clips, without the per-clip publish detail."""
    with _db() as c:
        jobs = []
        for raw, updated in c.execute("SELECT data, updated FROM jobs ORDER BY updated DESC LIMIT ?", (limit,)):
            job = json.loads(raw)
            job.setdefault("created", updated)  # jobs saved before `created` existed
            jobs.append(job)
        for job in jobs:
            job["clips"] = [json.loads(r[0]) for r in c.execute(
                "SELECT data FROM clips WHERE job_id = ? ORDER BY score DESC", (job["id"],))]
    return jobs


def delete_job(job_id: str) -> bool:
    with _db() as c:
        c.execute("DELETE FROM clips WHERE job_id = ?", (job_id,))
        return c.execute("DELETE FROM jobs WHERE id = ?", (job_id,)).rowcount > 0


def interrupt_unfinished() -> int:
    """Mark jobs left queued or running by a stopped server as errors. Their worker thread is gone."""
    n = 0
    with _db() as c:
        for job_id, raw in c.execute("SELECT id, data FROM jobs").fetchall():
            job = json.loads(raw)
            if job.get("status") in ("queued", "running"):
                job.update(status="error", message="Interrupted when the server stopped. Start it again.")
                c.execute("UPDATE jobs SET data = ?, updated = ? WHERE id = ?",
                          (json.dumps(job), time.time(), job_id))
                n += 1
    return n
