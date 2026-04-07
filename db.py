"""
Database layer for Alchemy (SQLite).

Tables: users, mp_credentials, price_lists.
Auto-creates tables + admin user on first run.
"""

import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path("data/alchemy.db")


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if not exist. Seed admin user."""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT DEFAULT '',
            company TEXT DEFAULT '',
            is_admin BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS mp_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            marketplace TEXT NOT NULL,
            key_name TEXT NOT NULL,
            encrypted_value TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, marketplace, key_name)
        );

        CREATE TABLE IF NOT EXISTS product_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            article TEXT NOT NULL,
            name TEXT DEFAULT '',
            cost_price REAL DEFAULT 0,
            ozon_product_id INTEGER,
            ozon_sku TEXT,
            wb_nm_id INTEGER,
            ym_market_sku TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, article)
        );

        CREATE TABLE IF NOT EXISTS price_lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT 1,
            file_path TEXT NOT NULL,
            sku_count INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    """)
    conn.commit()

    # Seed admin if no users exist
    row = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()
    if row["cnt"] == 0:
        from auth import hash_password
        conn.execute(
            "INSERT INTO users (username, password_hash, name, company, is_admin) "
            "VALUES (?, ?, ?, ?, ?)",
            ("admin", hash_password("admin123"), "Администратор", "", 1),
        )
        conn.commit()
        print("✅ Создан администратор: admin / admin123")

    conn.close()


# ── User CRUD ──────────────────────────────────────────────────────────

def get_user(username: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_users() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_user(username: str, password_hash: str, name: str = "",
                company: str = "", is_admin: bool = False) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, name, company, is_admin) "
        "VALUES (?, ?, ?, ?, ?)",
        (username, password_hash, name, company, int(is_admin)),
    )
    conn.commit()
    uid = cur.lastrowid
    conn.close()
    return uid


def update_user_password(user_id: int, password_hash: str):
    conn = get_conn()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                 (password_hash, user_id))
    conn.commit()
    conn.close()


def delete_user(user_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


# ── Credentials CRUD ───────────────────────────────────────────────────

def get_credentials(user_id: int, marketplace: str) -> dict:
    """Return {key_name: encrypted_value} for a marketplace."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT key_name, encrypted_value FROM mp_credentials "
        "WHERE user_id = ? AND marketplace = ?",
        (user_id, marketplace),
    ).fetchall()
    conn.close()
    return {r["key_name"]: r["encrypted_value"] for r in rows}


def save_credential(user_id: int, marketplace: str, key_name: str,
                    encrypted_value: str):
    conn = get_conn()
    conn.execute(
        "INSERT INTO mp_credentials (user_id, marketplace, key_name, encrypted_value) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, marketplace, key_name) "
        "DO UPDATE SET encrypted_value = excluded.encrypted_value",
        (user_id, marketplace, key_name, encrypted_value),
    )
    conn.commit()
    conn.close()


def delete_credentials(user_id: int, marketplace: str):
    conn = get_conn()
    conn.execute(
        "DELETE FROM mp_credentials WHERE user_id = ? AND marketplace = ?",
        (user_id, marketplace),
    )
    conn.commit()
    conn.close()


# ── Price lists CRUD ───────────────────────────────────────────────────

def get_active_price(user_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM price_lists WHERE user_id = ? AND is_active = 1 "
        "ORDER BY uploaded_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Product catalog CRUD ───────────────────────────────────────────

def get_catalog(user_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM product_catalog WHERE user_id = ? ORDER BY article",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_catalog_items(user_id: int, items: list[dict]):
    """Insert or update catalog items. Each item must have 'article' key."""
    conn = get_conn()
    for it in items:
        conn.execute("""
            INSERT INTO product_catalog (user_id, article, name, ozon_product_id, ozon_sku, wb_nm_id, ym_market_sku, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, article) DO UPDATE SET
                name = COALESCE(NULLIF(excluded.name, ''), product_catalog.name),
                ozon_product_id = COALESCE(excluded.ozon_product_id, product_catalog.ozon_product_id),
                ozon_sku = COALESCE(excluded.ozon_sku, product_catalog.ozon_sku),
                wb_nm_id = COALESCE(excluded.wb_nm_id, product_catalog.wb_nm_id),
                ym_market_sku = COALESCE(excluded.ym_market_sku, product_catalog.ym_market_sku),
                updated_at = CURRENT_TIMESTAMP
        """, (
            user_id,
            str(it["article"]).strip(),
            it.get("name", ""),
            it.get("ozon_product_id"),
            it.get("ozon_sku"),
            it.get("wb_nm_id"),
            it.get("ym_market_sku"),
        ))
    conn.commit()
    conn.close()


def update_cost_prices(user_id: int, updates: dict):
    """Batch update cost_price by article. updates = {article: cost_price}."""
    conn = get_conn()
    for article, cost in updates.items():
        conn.execute(
            "UPDATE product_catalog SET cost_price = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE user_id = ? AND article = ?",
            (cost, user_id, str(article).strip()),
        )
    conn.commit()
    conn.close()


def get_cost_map(user_id: int) -> dict:
    """Return {article: cost_price} for items with cost > 0."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT article, cost_price FROM product_catalog "
        "WHERE user_id = ? AND cost_price > 0",
        (user_id,),
    ).fetchall()
    conn.close()
    return {r["article"]: r["cost_price"] for r in rows}


def get_catalog_count(user_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM product_catalog WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row["cnt"]


# ── Price lists CRUD ───────────────────────────────────────────────────

def save_price_list(user_id: int, filename: str, file_path: str,
                    sku_count: int) -> int:
    conn = get_conn()
    # Deactivate previous
    conn.execute(
        "UPDATE price_lists SET is_active = 0 WHERE user_id = ?",
        (user_id,),
    )
    cur = conn.execute(
        "INSERT INTO price_lists (user_id, filename, file_path, sku_count) "
        "VALUES (?, ?, ?, ?)",
        (user_id, filename, file_path, sku_count),
    )
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid
