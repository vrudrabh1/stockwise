"""SQLite database operations for watchlists and search history."""

import sqlite3
from datetime import datetime

from config import DB_PATH


def get_connection() -> sqlite3.Connection:
    """Get a database connection, creating tables if needed."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _create_tables(conn)
    return conn


def _create_tables(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            name TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS recommendation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            signal TEXT NOT NULL,
            score REAL,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            shares REAL NOT NULL,
            buy_price REAL NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS price_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            target_price REAL NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('above', 'below')),
            workflow_id TEXT,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'triggered', 'cancelled')),
            triggered_price REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            triggered_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS stock_journeys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            interval_seconds INTEGER NOT NULL,
            max_duration_hours REAL NOT NULL,
            workflow_id TEXT,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'completed', 'cancelled')),
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS stock_journey_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            journey_id INTEGER NOT NULL REFERENCES stock_journeys(id),
            price REAL NOT NULL,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS visitor_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page TEXT NOT NULL,
            ip TEXT,
            user_agent TEXT,
            visited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS investment_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            quantity REAL NOT NULL,
            order_type TEXT NOT NULL,
            limit_price REAL,
            reason TEXT,
            success INTEGER NOT NULL DEFAULT 1,
            order_id TEXT,
            filled_price REAL,
            filled_quantity REAL,
            message TEXT,
            executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)


def add_to_watchlist(symbol: str, name: str = "") -> bool:
    """Add a ticker to the watchlist. Returns True if added, False if already exists."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (symbol, name) VALUES (?, ?)",
            (symbol.upper(), name),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def remove_from_watchlist(symbol: str) -> bool:
    """Remove a ticker from the watchlist."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def get_watchlist() -> list[dict]:
    """Get all tickers in the watchlist."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT symbol, name, added_at FROM watchlist ORDER BY added_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def log_search(symbol: str) -> None:
    """Log a search query."""
    conn = get_connection()
    try:
        conn.execute("INSERT INTO search_history (symbol) VALUES (?)", (symbol.upper(),))
        conn.commit()
    finally:
        conn.close()


def get_recent_searches(limit: int = 10) -> list[str]:
    """Get recent unique search symbols."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT DISTINCT symbol FROM search_history
               ORDER BY searched_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [r["symbol"] for r in rows]
    finally:
        conn.close()


def add_position(symbol: str, shares: float, buy_price: float) -> bool:
    """Add or replace a position. Returns True if added."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO positions (symbol, shares, buy_price) VALUES (?, ?, ?)",
            (symbol.upper(), shares, buy_price),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def remove_position(symbol: str) -> bool:
    """Remove a position by symbol."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol.upper(),))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def get_positions() -> list[dict]:
    """Get all positions."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT symbol, shares, buy_price, added_at FROM positions ORDER BY added_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_position(symbol: str, shares: float, buy_price: float) -> bool:
    """Update an existing position."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE positions SET shares = ?, buy_price = ? WHERE symbol = ?",
            (shares, buy_price, symbol.upper()),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def create_price_alert(symbol: str, target_price: float, direction: str, workflow_id: str = "") -> int:
    """Create a price alert. Returns the new alert id."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO price_alerts (symbol, target_price, direction, workflow_id) VALUES (?, ?, ?, ?)",
            (symbol.upper(), target_price, direction, workflow_id),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_alert_workflow_id(alert_id: int, workflow_id: str) -> None:
    """Update the workflow_id for an alert after it's been started."""
    conn = get_connection()
    try:
        conn.execute("UPDATE price_alerts SET workflow_id = ? WHERE id = ?", (workflow_id, alert_id))
        conn.commit()
    finally:
        conn.close()


def trigger_price_alert(alert_id: int, triggered_price: float) -> None:
    """Mark an alert as triggered."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE price_alerts SET status = 'triggered', triggered_price = ?, triggered_at = ? WHERE id = ?",
            (triggered_price, datetime.now().isoformat(), alert_id),
        )
        conn.commit()
    finally:
        conn.close()


def cancel_price_alert(alert_id: int) -> None:
    """Cancel a price alert."""
    conn = get_connection()
    try:
        conn.execute("UPDATE price_alerts SET status = 'cancelled' WHERE id = ?", (alert_id,))
        conn.commit()
    finally:
        conn.close()


def get_active_alerts(symbol: str = "") -> list[dict]:
    """Get active alerts, optionally filtered by symbol."""
    conn = get_connection()
    try:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM price_alerts WHERE status = 'active' AND symbol = ? ORDER BY created_at DESC",
                (symbol.upper(),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM price_alerts WHERE status = 'active' ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_triggered_alerts(limit: int = 20) -> list[dict]:
    """Get recently triggered alerts."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM price_alerts WHERE status = 'triggered' ORDER BY triggered_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# --- Stock Journey ---

def create_stock_journey(symbol: str, interval_seconds: int, max_duration_hours: float, workflow_id: str = "") -> int:
    """Create a new stock journey. Returns the new journey id."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO stock_journeys (symbol, interval_seconds, max_duration_hours, workflow_id) VALUES (?, ?, ?, ?)",
            (symbol.upper(), interval_seconds, max_duration_hours, workflow_id),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_journey_workflow_id(journey_id: int, workflow_id: str) -> None:
    """Store the Temporal workflow_id on a journey."""
    conn = get_connection()
    try:
        conn.execute("UPDATE stock_journeys SET workflow_id = ? WHERE id = ?", (workflow_id, journey_id))
        conn.commit()
    finally:
        conn.close()


def record_journey_snapshot(journey_id: int, price: float) -> None:
    """Append a price snapshot to a journey."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO stock_journey_snapshots (journey_id, price) VALUES (?, ?)",
            (journey_id, price),
        )
        conn.commit()
    finally:
        conn.close()


def get_journey_snapshots(journey_id: int) -> list[dict]:
    """Return all price snapshots for a journey, oldest first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT price, recorded_at FROM stock_journey_snapshots WHERE journey_id = ? ORDER BY recorded_at ASC",
            (journey_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_active_journeys() -> list[dict]:
    """Return all active journeys."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM stock_journeys WHERE status = 'active' ORDER BY started_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_journeys(limit: int = 30) -> list[dict]:
    """Return recent journeys of all statuses."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM stock_journeys ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def finalize_journey(journey_id: int, status: str = "completed") -> None:
    """Mark a journey as completed or cancelled."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE stock_journeys SET status = ?, ended_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, journey_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_investment_log(limit: int = 50) -> list[dict]:
    """Return recent investment orders, newest first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM investment_log ORDER BY executed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def log_visit(page: str, ip: str = "", user_agent: str = "") -> None:
    """Log a page visit."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO visitor_log (page, ip, user_agent) VALUES (?, ?, ?)",
            (page, ip, user_agent),
        )
        conn.commit()
    finally:
        conn.close()


def get_visit_log(limit: int = 200) -> list[dict]:
    """Return recent visits, newest first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT page, ip, user_agent, visited_at FROM visitor_log ORDER BY visited_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_visit_summary() -> list[dict]:
    """Return per-page visit counts."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT page, COUNT(*) as visits,
               MAX(visited_at) as last_visit
               FROM visitor_log GROUP BY page ORDER BY visits DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def log_recommendation(symbol: str, signal: str, score: float, details: str = "") -> None:
    """Log a recommendation."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO recommendation_log (symbol, signal, score, details) VALUES (?, ?, ?, ?)",
            (symbol.upper(), signal, score, details),
        )
        conn.commit()
    finally:
        conn.close()
