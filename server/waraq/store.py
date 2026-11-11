from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from waraq.config import Settings


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(settings.data_dir / "desk.sqlite3", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                content_type TEXT,
                source_path TEXT NOT NULL,
                status TEXT NOT NULL,
                phase TEXT NOT NULL,
                engine TEXT,
                pages_total INTEGER NOT NULL DEFAULT 0,
                pages_done INTEGER NOT NULL DEFAULT 0,
                message TEXT,
                error TEXT,
                warnings TEXT NOT NULL DEFAULT '[]',
                include_headers_footers INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pages (
                job_id TEXT NOT NULL,
                page_index INTEGER NOT NULL,
                markdown TEXT,
                html TEXT,
                plain TEXT,
                chunks_json TEXT,
                token_count INTEGER,
                width INTEGER,
                height INTEGER,
                error TEXT,
                preview_path TEXT,
                PRIMARY KEY (job_id, page_index)
            );
            """
        )
        self._conn.commit()

    def recover_interrupted(self) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                UPDATE jobs
                SET status='queued', phase='queued', message=NULL, updated_at=?
                WHERE status='processing'
                """,
                (now,),
            )
            self._conn.commit()

    def create_job(
        self,
        *,
        filename: str,
        content_type: str,
        source_path: Path,
        include_headers_footers: bool,
        engine: str,
        job_id: str | None = None,
    ) -> str:
        job_id = job_id or uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs (
                    id, filename, content_type, source_path, status, phase, engine,
                    include_headers_footers, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', 'queued', ?, ?, ?, ?)
                """,
                (
                    job_id,
                    filename,
                    content_type,
                    str(source_path),
                    engine,
                    1 if include_headers_footers else 0,
                    now,
                    now,
                ),
            )
            self._conn.commit()
        return job_id

    def claim_next(self) -> str | None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                self._conn.execute("COMMIT")
                return None
            self._conn.execute(
                """
                UPDATE jobs
                SET status='processing', phase='ocr', updated_at=?
                WHERE id=?
                """,
                (time.time(), row["id"]),
            )
            self._conn.execute("COMMIT")
            return row["id"]

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return None
            pages = self._conn.execute(
                """
                SELECT page_index, token_count, width, height, error, preview_path,
                       CASE WHEN markdown IS NULL AND html IS NULL AND error IS NULL
                            THEN 0 ELSE 1 END AS ready
                FROM pages WHERE job_id=? ORDER BY page_index
                """,
                (job_id,),
            ).fetchall()
        return _job_dict(row, pages)

    def list_jobs(self, limit: int = 12) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_job_dict(row, []) for row in rows]

    def get_page(self, job_id: str, page_index: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM pages WHERE job_id=? AND page_index=?",
                (job_id, page_index),
            ).fetchone()
        if row is None:
            return None
        return {
            "index": row["page_index"],
            "markdown": row["markdown"] or "",
            "html": row["html"] or "",
            "plain": row["plain"] or "",
            "chunks": json.loads(row["chunks_json"] or "[]"),
            "token_count": row["token_count"] or 0,
            "width": row["width"],
            "height": row["height"],
            "error": row["error"],
            "preview_path": row["preview_path"],
        }

    def list_page_records(self, job_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM pages WHERE job_id=? ORDER BY page_index",
                (job_id,),
            ).fetchall()
        return [
            {
                "index": row["page_index"],
                "markdown": row["markdown"] or "",
                "html": row["html"] or "",
                "plain": row["plain"] or "",
                "chunks": json.loads(row["chunks_json"] or "[]"),
                "token_count": row["token_count"] or 0,
                "width": row["width"],
                "height": row["height"],
                "error": row["error"],
            }
            for row in rows
        ]

    def update_job(self, job_id: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        columns = ", ".join(f"{key}=?" for key in fields)
        values = list(fields.values()) + [job_id]
        with self._lock:
            self._conn.execute(f"UPDATE jobs SET {columns} WHERE id=?", values)
            self._conn.commit()

    def save_page(self, job_id: str, page_index: int, **fields) -> None:
        current = self.get_page(job_id, page_index) or {}
        merged = {
            "markdown": current.get("markdown"),
            "html": current.get("html"),
            "plain": current.get("plain"),
            "chunks_json": json.dumps(current.get("chunks") or []),
            "token_count": current.get("token_count"),
            "width": current.get("width"),
            "height": current.get("height"),
            "error": current.get("error"),
            "preview_path": current.get("preview_path"),
        }
        if "chunks" in fields:
            merged["chunks_json"] = json.dumps(fields.pop("chunks"))
        merged.update(fields)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO pages (
                    job_id, page_index, markdown, html, plain, chunks_json,
                    token_count, width, height, error, preview_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, page_index) DO UPDATE SET
                    markdown=excluded.markdown,
                    html=excluded.html,
                    plain=excluded.plain,
                    chunks_json=excluded.chunks_json,
                    token_count=excluded.token_count,
                    width=excluded.width,
                    height=excluded.height,
                    error=excluded.error,
                    preview_path=excluded.preview_path
                """,
                (
                    job_id,
                    page_index,
                    merged["markdown"],
                    merged["html"],
                    merged["plain"],
                    merged["chunks_json"],
                    merged["token_count"],
                    merged["width"],
                    merged["height"],
                    merged["error"],
                    merged["preview_path"],
                ),
            )
            self._conn.commit()

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return False
            if row["status"] in {"complete", "failed", "cancelled"}:
                return False
            self._conn.execute(
                """
                UPDATE jobs
                SET status='cancelled', phase='cancelled', message=NULL, updated_at=?
                WHERE id=?
                """,
                (time.time(), job_id),
            )
            self._conn.commit()
        return True

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        return row is not None and row["status"] == "cancelled"

    def delete_job(self, job_id: str) -> dict | None:
        job = self.get_job(job_id)
        if job is None:
            return None
        with self._lock:
            self._conn.execute("DELETE FROM pages WHERE job_id=?", (job_id,))
            self._conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            self._conn.commit()
        return job


def _job_dict(row: sqlite3.Row, pages: list[sqlite3.Row]) -> dict:
    return {
        "id": row["id"],
        "filename": row["filename"],
        "content_type": row["content_type"],
        "source_path": row["source_path"],
        "status": row["status"],
        "phase": row["phase"],
        "engine": row["engine"],
        "pages_total": row["pages_total"],
        "pages_done": row["pages_done"],
        "message": row["message"],
        "error": row["error"],
        "warnings": json.loads(row["warnings"] or "[]"),
        "include_headers_footers": bool(row["include_headers_footers"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "pages": [
            {
                "index": page["page_index"],
                "ready": bool(page["ready"]) if "ready" in page.keys() else False,
                "error": page["error"] if "error" in page.keys() else None,
                "token_count": page["token_count"] if "token_count" in page.keys() else 0,
                "width": page["width"] if "width" in page.keys() else None,
                "height": page["height"] if "height" in page.keys() else None,
                "has_preview": bool(page["preview_path"]) if "preview_path" in page.keys() else False,
            }
            for page in pages
        ],
    }
