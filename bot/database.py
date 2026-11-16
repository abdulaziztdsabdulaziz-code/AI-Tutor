"""
Async SQLite database layer with connection pooling for aiogram 3.x.

Features:
- Thread-safe / coroutine-safe AsyncConnectionPool on top of aiosqlite.
- WAL (Write-Ahead Logging) journal mode enabled for high-concurrency read/write.
- Foreign key enforcement enabled on every pooled connection.
- Strict typing, PEP 8 compliance, clean architecture.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
from typing import Any, AsyncIterator

import aiosqlite

logger = logging.getLogger(__name__)

_CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    user_id      INTEGER PRIMARY KEY,
    username     TEXT,
    first_name   TEXT    NOT NULL,
    active_model TEXT    NOT NULL DEFAULT 'claude-3-7-sonnet',
    mode         TEXT    NOT NULL DEFAULT 'chat',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_CHAT_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS chat_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    role       TEXT    NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);
"""

_CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_chat_history_user_id ON chat_history (user_id, id DESC);
"""


class AsyncConnectionPool:
    """
    Asynchronous connection pool for aiosqlite.

    Maintains a pool of active aiosqlite connections protected by an asyncio.Queue,
    ensuring thread-safe and coroutine-safe concurrent access without connection thrashing.
    """

    def __init__(self, db_path: str, max_connections: int = 5) -> None:
        self._db_path = db_path
        self._max_connections = max_connections
        self._pool: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue(maxsize=max_connections)
        self._all_conns: list[aiosqlite.Connection] = []
        self._is_initialized: bool = False
        self._lock = asyncio.Lock()

    async def _create_single_connection(self) -> aiosqlite.Connection:
        """Create and configure a single connection with WAL and foreign keys."""
        conn = await aiosqlite.connect(self._db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA foreign_keys=ON;")
        await conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    async def initialize(self) -> None:
        """Initialize the connection pool up to max_connections."""
        async with self._lock:
            if self._is_initialized:
                return
            for _ in range(self._max_connections):
                conn = await self._create_single_connection()
                self._all_conns.append(conn)
                await self._pool.put(conn)
            self._is_initialized = True
            logger.info(
                "AsyncConnectionPool initialized for '%s' with %d connections.",
                self._db_path,
                self._max_connections,
            )

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[aiosqlite.Connection]:
        """
        Acquire a connection from the pool, yielding it to the caller.
        Guarantees safe return to the pool upon block completion or exception.
        """
        if not self._is_initialized:
            await self.initialize()

        conn = await self._pool.get()
        try:
            yield conn
        finally:
            await self._pool.put(conn)

    async def close(self) -> None:
        """Close all connections in the pool."""
        async with self._lock:
            if not self._is_initialized:
                return
            while not self._pool.empty():
                try:
                    self._pool.get_nowait()
                except asyncio.QueueEmpty:
                    break
            for conn in self._all_conns:
                try:
                    await conn.close()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Error closing pooled database connection: %s", exc)
            self._all_conns.clear()
            self._is_initialized = False
            logger.info("AsyncConnectionPool for '%s' closed.", self._db_path)


class Database:
    """
    High-level async database service managing users, preferences, and chat history.
    Backed by an AsyncConnectionPool.
    """

    def __init__(self, db_path: str, max_connections: int = 5) -> None:
        self._db_path = db_path
        self._pool = AsyncConnectionPool(db_path=db_path, max_connections=max_connections)

    async def connect(self) -> None:
        """Initialize connection pool and execute schema migrations."""
        await self._pool.initialize()
        async with self._pool.acquire() as conn:
            await conn.execute(_CREATE_USERS_TABLE)
            await conn.execute(_CREATE_CHAT_HISTORY_TABLE)
            await conn.execute(_CREATE_INDEXES)
            await conn.commit()
        logger.info("Database schema verified and active at %s", self._db_path)

    async def close(self) -> None:
        """Gracefully shut down the database connection pool."""
        await self._pool.close()

    # ── User Profile & State Operations ────────────────────────────────────────

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        """Retrieve user profile dict or None if non-existent."""
        async with self._pool.acquire() as conn:
            async with conn.execute(
                "SELECT * FROM users WHERE user_id = ?;", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def upsert_user(
        self,
        user_id: int,
        username: str | None,
        first_name: str,
    ) -> None:
        """Create user or update profile while preserving current model and mode."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (user_id, username, first_name)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username   = excluded.username,
                    first_name = excluded.first_name,
                    updated_at = datetime('now');
                """,
                (user_id, username, first_name),
            )
            await conn.commit()

    async def get_user_model(self, user_id: int) -> str:
        """Fetch active model key for user, defaulting to 'claude-3-7-sonnet'."""
        async with self._pool.acquire() as conn:
            async with conn.execute(
                "SELECT active_model FROM users WHERE user_id = ?;", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return str(row["active_model"]) if row else "claude-3-7-sonnet"

    async def set_user_model(self, user_id: int, model_key: str) -> None:
        """Update active model preference for user."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE users
                   SET active_model = ?, updated_at = datetime('now')
                 WHERE user_id = ?;
                """,
                (model_key, user_id),
            )
            await conn.commit()

    async def get_user_mode(self, user_id: int) -> str:
        """Fetch user's current workflow mode ('chat', 'voice', 'chess')."""
        async with self._pool.acquire() as conn:
            async with conn.execute(
                "SELECT mode FROM users WHERE user_id = ?;", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return str(row["mode"]) if row else "chat"

    async def set_user_mode(self, user_id: int, mode: str) -> None:
        """Persist workflow mode for user ('chat', 'voice', 'chess')."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE users
                   SET mode = ?, updated_at = datetime('now')
                 WHERE user_id = ?;
                """,
                (mode, user_id),
            )
            await conn.commit()

    # ── Chat History Operations ───────────────────────────────────────────────

    async def get_chat_history(
        self, user_id: int, limit: int = 20
    ) -> list[dict[str, str]]:
        """
        Retrieve recent conversational turns for user, chronological order (oldest first).

        Returns:
            list[dict[str, str]] with keys 'role' and 'content'.
        """
        async with self._pool.acquire() as conn:
            async with conn.execute(
                """
                SELECT role, content
                  FROM chat_history
                 WHERE user_id = ?
                 ORDER BY id DESC
                 LIMIT ?;
                """,
                (user_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {"role": str(r["role"]), "content": str(r["content"])}
                    for r in reversed(rows)
                ]

    async def add_message(self, user_id: int, role: str, content: str) -> None:
        """Append a message turn to the user's conversation history."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO chat_history (user_id, role, content)
                VALUES (?, ?, ?);
                """,
                (user_id, role, content),
            )
            await conn.commit()

    async def clear_history(self, user_id: int) -> None:
        """Wipe all chat history records for the specified user."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM chat_history WHERE user_id = ?;", (user_id,)
            )
            await conn.commit()
        logger.info("Cleared chat history for user_id=%d", user_id)
