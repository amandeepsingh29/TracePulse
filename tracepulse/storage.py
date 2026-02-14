"""
SQLite storage for persisting trace results and enabling historical analysis.
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from tracepulse.tracer import TimingBreakdown

DEFAULT_DB_PATH = Path.home() / ".tracepulse" / "traces.db"


def _get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get a SQLite connection, creating the database if necessary."""
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_tables(conn)
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            method TEXT NOT NULL DEFAULT 'GET',
            status_code INTEGER,
            response_size INTEGER,
            ip_address TEXT,
            tls_version TEXT,
            dns_ms REAL,
            tcp_connect_ms REAL,
            tls_handshake_ms REAL,
            server_processing_ms REAL,
            content_transfer_ms REAL,
            total_ms REAL,
            error TEXT,
            headers_sent TEXT,
            headers_received TEXT,
            label TEXT,
            created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_traces_url ON traces(url);
        CREATE INDEX IF NOT EXISTS idx_traces_created_at ON traces(created_at);
        CREATE INDEX IF NOT EXISTS idx_traces_label ON traces(label);

        CREATE TABLE IF NOT EXISTS presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            url TEXT NOT NULL,
            method TEXT NOT NULL DEFAULT 'GET',
            headers TEXT DEFAULT '{}',
            body TEXT,
            created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_presets_name ON presets(name);
    """
    )


def save_trace(
    timing: TimingBreakdown,
    label: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> int:
    """Save a trace result to the database. Returns the row ID."""
    conn = _get_connection(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO traces (
                url, method, status_code, response_size, ip_address, tls_version,
                dns_ms, tcp_connect_ms, tls_handshake_ms, server_processing_ms,
                content_transfer_ms, total_ms, error, headers_sent, headers_received,
                label, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timing.url,
                timing.method,
                timing.status_code,
                timing.response_size,
                timing.ip_address,
                timing.tls_version,
                round(timing.dns_ms, 2),
                round(timing.tcp_connect_ms, 2),
                round(timing.tls_handshake_ms, 2),
                round(timing.server_processing_ms, 2),
                round(timing.content_transfer_ms, 2),
                round(timing.total_ms, 2),
                timing.error,
                json.dumps(timing.headers_sent),
                json.dumps(timing.headers_received),
                label,
                time.time(),
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_traces(
    url: Optional[str] = None,
    label: Optional[str] = None,
    limit: int = 50,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """Retrieve traces from the database, optionally filtered by URL or label."""
    conn = _get_connection(db_path)
    try:
        query = "SELECT * FROM traces WHERE 1=1"
        params = []

        if url:
            query += " AND url = ?"
            params.append(url)
        if label:
            query += " AND label = ?"
            params.append(label)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_all_urls(db_path: Optional[Path] = None) -> list[str]:
    """Get all unique URLs that have been traced."""
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT url FROM traces ORDER BY url"
        ).fetchall()
        return [row["url"] for row in rows]
    finally:
        conn.close()


def get_trace_by_id(trace_id: int, db_path: Optional[Path] = None) -> Optional[dict]:
    """Get a single trace by ID."""
    conn = _get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM traces WHERE id = ?", (trace_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_stats(url: str, db_path: Optional[Path] = None) -> dict:
    """Get aggregate statistics for a URL."""
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) as trace_count,
                AVG(total_ms) as avg_total_ms,
                MIN(total_ms) as min_total_ms,
                MAX(total_ms) as max_total_ms,
                AVG(dns_ms) as avg_dns_ms,
                AVG(tcp_connect_ms) as avg_tcp_ms,
                AVG(tls_handshake_ms) as avg_tls_ms,
                AVG(server_processing_ms) as avg_server_ms,
                AVG(content_transfer_ms) as avg_transfer_ms,
                MIN(created_at) as first_traced,
                MAX(created_at) as last_traced
            FROM traces WHERE url = ?
            """,
            (url,),
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def delete_traces(
    url: Optional[str] = None,
    older_than_days: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> int:
    """Delete traces, optionally filtered. Returns number of deleted rows."""
    conn = _get_connection(db_path)
    try:
        query = "DELETE FROM traces WHERE 1=1"
        params = []

        if url:
            query += " AND url = ?"
            params.append(url)
        if older_than_days:
            cutoff = time.time() - (older_than_days * 86400)
            query += " AND created_at < ?"
            params.append(cutoff)

        cursor = conn.execute(query, params)
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


# --- Percentile Stats ---

def get_percentile_stats(url: str, db_path: Optional[Path] = None) -> dict:
    """Get percentile statistics (P50, P95, P99) for a URL."""
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT total_ms, dns_ms, tcp_connect_ms, tls_handshake_ms, "
            "server_processing_ms, content_transfer_ms FROM traces "
            "WHERE url = ? AND total_ms IS NOT NULL ORDER BY total_ms",
            (url,),
        ).fetchall()

        if not rows:
            return {}

        data = [dict(r) for r in rows]
        n = len(data)

        def percentile(values, pct):
            idx = int(pct / 100 * n)
            idx = min(idx, n - 1)
            return values[idx]

        total_sorted = sorted(r["total_ms"] for r in data)

        return {
            "count": n,
            "p50_ms": round(percentile(total_sorted, 50), 1),
            "p95_ms": round(percentile(total_sorted, 95), 1),
            "p99_ms": round(percentile(total_sorted, 99), 1),
            "p50_dns": round(percentile(sorted(r["dns_ms"] for r in data), 50), 1),
            "p95_dns": round(percentile(sorted(r["dns_ms"] for r in data), 95), 1),
            "p50_tcp": round(percentile(sorted(r["tcp_connect_ms"] for r in data), 50), 1),
            "p95_tcp": round(percentile(sorted(r["tcp_connect_ms"] for r in data), 95), 1),
            "p50_tls": round(percentile(sorted(r["tls_handshake_ms"] for r in data), 50), 1),
            "p95_tls": round(percentile(sorted(r["tls_handshake_ms"] for r in data), 95), 1),
            "p50_server": round(percentile(sorted(r["server_processing_ms"] for r in data), 50), 1),
            "p95_server": round(percentile(sorted(r["server_processing_ms"] for r in data), 95), 1),
            "p50_transfer": round(percentile(sorted(r["content_transfer_ms"] for r in data), 50), 1),
            "p95_transfer": round(percentile(sorted(r["content_transfer_ms"] for r in data), 95), 1),
        }
    finally:
        conn.close()


# --- Presets ---

def save_preset(
    name: str,
    url: str,
    method: str = "GET",
    headers: Optional[dict] = None,
    body: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> int:
    """Save a preset. Returns the row ID."""
    conn = _get_connection(db_path)
    try:
        cursor = conn.execute(
            "INSERT OR REPLACE INTO presets (name, url, method, headers, body, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, url, method, json.dumps(headers or {}), body, time.time()),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_preset(name: str, db_path: Optional[Path] = None) -> Optional[dict]:
    """Get a preset by name."""
    conn = _get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM presets WHERE name = ?", (name,)).fetchone()
        if row:
            result = dict(row)
            result["headers"] = json.loads(result.get("headers", "{}"))
            return result
        return None
    finally:
        conn.close()


def get_all_presets(db_path: Optional[Path] = None) -> list[dict]:
    """Get all presets."""
    conn = _get_connection(db_path)
    try:
        rows = conn.execute("SELECT * FROM presets ORDER BY name").fetchall()
        results = []
        for row in rows:
            r = dict(row)
            r["headers"] = json.loads(r.get("headers", "{}"))
            results.append(r)
        return results
    finally:
        conn.close()


def delete_preset(name: str, db_path: Optional[Path] = None) -> bool:
    """Delete a preset by name. Returns True if deleted."""
    conn = _get_connection(db_path)
    try:
        cursor = conn.execute("DELETE FROM presets WHERE name = ?", (name,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
