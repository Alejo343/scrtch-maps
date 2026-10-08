import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    location TEXT,
    latitude REAL,
    longitude REAL,
    zoom INTEGER,
    radius_km REAL,
    limit_count INTEGER NOT NULL DEFAULT 60,
    places_found INTEGER,
    places_done INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | error
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT UNIQUE NOT NULL,
    job_id INTEGER,
    name TEXT,
    category TEXT,
    address TEXT,
    phone TEXT,
    website TEXT,
    rating REAL,
    reviews_count INTEGER,
    latitude REAL,
    longitude REAL,
    maps_url TEXT,
    scraped_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # migracion para bases de datos creadas antes de agregar lat/lng/zoom
        _ensure_column(conn, "jobs", "latitude", "REAL")
        _ensure_column(conn, "jobs", "longitude", "REAL")
        _ensure_column(conn, "jobs", "zoom", "INTEGER")
        _ensure_column(conn, "jobs", "radius_km", "REAL")
        _ensure_column(conn, "jobs", "places_found", "INTEGER")
        _ensure_column(conn, "jobs", "places_done", "INTEGER")


def enqueue_job(
    query: str,
    location: str | None,
    limit_count: int,
    latitude: float | None = None,
    longitude: float | None = None,
    zoom: int | None = None,
    radius_km: float | None = None,
) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (query, location, latitude, longitude, zoom, radius_km, limit_count, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (query, location, latitude, longitude, zoom, radius_km, limit_count, _now(), _now()),
        )
        return cur.lastrowid


def claim_next_job() -> sqlite3.Row | None:
    """Atomically claim the oldest pending job so concurrent workers don't race."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = 'pending' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE jobs SET status = 'running', attempts = attempts + 1, updated_at = ? WHERE id = ? AND status = 'pending'",
            (_now(), row["id"]),
        )
        if conn.total_changes == 0:
            return None  # another worker grabbed it first
        return row


def update_job_progress(job_id: int, places_found: int, places_done: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET places_found = ?, places_done = ?, updated_at = ? WHERE id = ?",
            (places_found, places_done, _now(), job_id),
        )


def mark_job_done(job_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'done', updated_at = ? WHERE id = ?",
            (_now(), job_id),
        )


def mark_job_error(job_id: int, error: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'error', last_error = ?, updated_at = ? WHERE id = ?",
            (error[:2000], _now(), job_id),
        )


def list_job_groups() -> list[sqlite3.Row]:
    """Agrupa los jobs por busqueda (query+location) - una busqueda por ciudad
    o por zona dibujada genera muchos jobs (uno por celda de la grilla), pero
    para el usuario es 'una sola busqueda'. Sirve para filtrar resultados por
    busqueda en vez de por celda individual."""
    with connect() as conn:
        return conn.execute(
            """
            SELECT query, location, COUNT(*) as n_jobs,
                   SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as n_done,
                   MIN(created_at) as created_at
            FROM jobs
            GROUP BY query, location
            ORDER BY created_at DESC
            """
        ).fetchall()


def job_ids_for_group(query: str, location: str | None) -> list[int]:
    with connect() as conn:
        if location is None:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE query = ? AND location IS NULL", (query,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE query = ? AND location = ?", (query, location)
            ).fetchall()
        return [r["id"] for r in rows]


def job_counts() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute("SELECT status, COUNT(*) as n FROM jobs GROUP BY status").fetchall()
    return {row["status"]: row["n"] for row in rows}


def list_jobs(limit: int = 300) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def count_places() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) as n FROM places").fetchone()["n"]


def reset_error_jobs() -> int:
    with connect() as conn:
        cur = conn.execute("UPDATE jobs SET status = 'pending', updated_at = ? WHERE status = 'error'", (_now(),))
        return cur.rowcount


def clear_finished_jobs() -> int:
    with connect() as conn:
        cur = conn.execute("DELETE FROM jobs WHERE status IN ('done', 'error')")
        return cur.rowcount


def upsert_place(job_id: int, place: dict) -> None:
    dedupe_key = place.get("maps_url") or f"{place.get('name')}|{place.get('address')}"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO places (
                dedupe_key, job_id, name, category, address, phone, website,
                rating, reviews_count, latitude, longitude, maps_url, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dedupe_key) DO UPDATE SET
                name=excluded.name, category=excluded.category, address=excluded.address,
                phone=excluded.phone, website=excluded.website, rating=excluded.rating,
                reviews_count=excluded.reviews_count, latitude=excluded.latitude,
                longitude=excluded.longitude, scraped_at=excluded.scraped_at
            """,
            (
                dedupe_key, job_id,
                place.get("name"), place.get("category"), place.get("address"),
                place.get("phone"), place.get("website"), place.get("rating"),
                place.get("reviews_count"), place.get("latitude"), place.get("longitude"),
                place.get("maps_url"), _now(),
            ),
        )
