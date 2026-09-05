"""Datenbank-Layer (Supabase / PostgreSQL via asyncpg)."""
from __future__ import annotations

import json
import logging
import ssl
from typing import Any, Iterable, Sequence
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import asyncpg

import config

log = logging.getLogger("verifybot.db")


SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_configs (
    guild_id            BIGINT PRIMARY KEY,
    panel_channel_id    BIGINT,
    requests_channel_id BIGINT,
    log_channel_id      BIGINT,
    staff_role_ids      BIGINT[] NOT NULL DEFAULT '{}',
    add_role_ids        BIGINT[] NOT NULL DEFAULT '{}',
    remove_role_ids     BIGINT[] NOT NULL DEFAULT '{}',
    autorole_ids        BIGINT[] NOT NULL DEFAULT '{}',
    embed_title         TEXT,
    embed_description   TEXT,
    embed_color         BIGINT,
    embed_footer        TEXT,
    embed_image_url     TEXT,
    embed_thumbnail_url TEXT,
    button_label        TEXT,
    button_emoji        TEXT,
    button_style        TEXT NOT NULL DEFAULT 'success',
    application_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    auto_approve        BOOLEAN NOT NULL DEFAULT FALSE,
    dm_user             BOOLEAN NOT NULL DEFAULT TRUE,
    panel_message_id    BIGINT,
    setup_completed     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS form_fields (
    id          BIGSERIAL PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    position    INTEGER NOT NULL DEFAULT 0,
    label       TEXT NOT NULL,
    placeholder TEXT,
    style       TEXT NOT NULL DEFAULT 'short',
    required    BOOLEAN NOT NULL DEFAULT TRUE,
    min_length  INTEGER,
    max_length  INTEGER
);
CREATE INDEX IF NOT EXISTS form_fields_guild_idx ON form_fields (guild_id, position);

CREATE TABLE IF NOT EXISTS checkboxes (
    id          BIGSERIAL PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    position    INTEGER NOT NULL DEFAULT 0,
    label       TEXT NOT NULL,
    description TEXT,
    required    BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS checkboxes_guild_idx ON checkboxes (guild_id, position);

CREATE TABLE IF NOT EXISTS verification_requests (
    id          BIGSERIAL PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    channel_id  BIGINT,
    message_id  BIGINT,
    status      TEXT NOT NULL DEFAULT 'pending',
    answers     JSONB NOT NULL DEFAULT '[]'::jsonb,
    checks      JSONB NOT NULL DEFAULT '[]'::jsonb,
    handled_by  BIGINT,
    reason      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    handled_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS verification_requests_guild_idx
    ON verification_requests (guild_id, user_id, status);
"""


def _normalise_dsn(dsn: str) -> str:
    """Entfernt Query-Parameter, die asyncpg nicht versteht (z.B. pgbouncer=true)."""
    parsed = urlparse(dsn)
    if parsed.scheme == "postgres":
        parsed = parsed._replace(scheme="postgresql")
    query = parse_qs(parsed.query)
    for key in ("sslmode", "pgbouncer", "supa", "options", "schema", "connection_limit"):
        query.pop(key, None)
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


class Database:
    """Duenner Wrapper um einen asyncpg-Pool mit allen Queries des Bots."""

    def __init__(self, dsn: str) -> None:
        self.dsn = _normalise_dsn(dsn)
        self.pool: asyncpg.Pool | None = None

    # ------------------------------------------------------------ lifecycle --
    async def connect(self) -> None:
        ctx = ssl.create_default_context()
        # Supabase liefert ein Zertifikat, dessen CA nicht immer im Container liegt.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        async def init(conn: asyncpg.Connection) -> None:
            await conn.set_type_codec(
                "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
            )
            await conn.set_type_codec(
                "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
            )

        self.pool = await asyncpg.create_pool(
            dsn=self.dsn,
            ssl=ctx,
            min_size=1,
            max_size=5,
            command_timeout=30,
            max_inactive_connection_lifetime=60,
            init=init,
            # Pflicht fuer den Supabase Transaction-Pooler (pgbouncer)
            statement_cache_size=0,
        )
        await self.setup()
        log.info("Datenbank verbunden.")

    async def setup(self) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(SCHEMA)
            # Migrationen fuer aeltere Installationen
            for column, ddl in (
                ("log_channel_id", "BIGINT"),
                ("embed_footer", "TEXT"),
                ("embed_image_url", "TEXT"),
                ("embed_thumbnail_url", "TEXT"),
                ("button_emoji", "TEXT"),
                ("auto_approve", "BOOLEAN NOT NULL DEFAULT FALSE"),
                ("dm_user", "BOOLEAN NOT NULL DEFAULT TRUE"),
            ):
                await conn.execute(
                    f"ALTER TABLE guild_configs ADD COLUMN IF NOT EXISTS {column} {ddl}"
                )

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    # --------------------------------------------------------------- config --
    async def get_config(self, guild_id: int) -> dict[str, Any]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM guild_configs WHERE guild_id = $1", guild_id
            )
            if row is None:
                row = await conn.fetchrow(
                    """
                    INSERT INTO guild_configs (guild_id, embed_title, embed_description,
                                               embed_color, button_label, button_emoji)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (guild_id) DO UPDATE SET guild_id = EXCLUDED.guild_id
                    RETURNING *
                    """,
                    guild_id,
                    config.DEFAULT_EMBED_TITLE,
                    config.DEFAULT_EMBED_DESCRIPTION,
                    config.DEFAULT_EMBED_COLOR,
                    config.DEFAULT_BUTTON_LABEL,
                    config.DEFAULT_BUTTON_EMOJI,
                )
        return dict(row)

    async def update_config(self, guild_id: int, **values: Any) -> dict[str, Any]:
        if not values:
            return await self.get_config(guild_id)
        await self.get_config(guild_id)  # stellt sicher, dass die Zeile existiert
        assert self.pool is not None

        columns = list(values.keys())
        assignments = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(columns))
        query = (
            f"UPDATE guild_configs SET {assignments}, updated_at = NOW() "
            f"WHERE guild_id = $1 RETURNING *"
        )
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, guild_id, *values.values())
        return dict(row)

    async def reset_guild(self, guild_id: int) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM form_fields WHERE guild_id = $1", guild_id)
                await conn.execute("DELETE FROM checkboxes WHERE guild_id = $1", guild_id)
                await conn.execute("DELETE FROM guild_configs WHERE guild_id = $1", guild_id)

    # ---------------------------------------------------------- form fields --
    async def get_form_fields(self, guild_id: int) -> list[dict[str, Any]]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM form_fields WHERE guild_id = $1 ORDER BY position, id",
                guild_id,
            )
        return [dict(r) for r in rows]

    async def add_form_field(
        self,
        guild_id: int,
        label: str,
        placeholder: str | None = None,
        style: str = "short",
        required: bool = True,
        max_length: int | None = None,
    ) -> int:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            position = await conn.fetchval(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM form_fields WHERE guild_id = $1",
                guild_id,
            )
            return await conn.fetchval(
                """
                INSERT INTO form_fields (guild_id, position, label, placeholder, style,
                                         required, max_length)
                VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id
                """,
                guild_id, position, label, placeholder, style, required, max_length,
            )

    async def update_form_field(self, field_id: int, **values: Any) -> None:
        if not values:
            return
        assert self.pool is not None
        columns = list(values.keys())
        assignments = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(columns))
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"UPDATE form_fields SET {assignments} WHERE id = $1",
                field_id, *values.values(),
            )

    async def delete_form_field(self, field_id: int) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM form_fields WHERE id = $1", field_id)

    # ------------------------------------------------------------ checkboxes --
    async def get_checkboxes(self, guild_id: int) -> list[dict[str, Any]]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM checkboxes WHERE guild_id = $1 ORDER BY position, id",
                guild_id,
            )
        return [dict(r) for r in rows]

    async def add_checkbox(
        self,
        guild_id: int,
        label: str,
        description: str | None = None,
        required: bool = True,
    ) -> int:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            position = await conn.fetchval(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM checkboxes WHERE guild_id = $1",
                guild_id,
            )
            return await conn.fetchval(
                """
                INSERT INTO checkboxes (guild_id, position, label, description, required)
                VALUES ($1, $2, $3, $4, $5) RETURNING id
                """,
                guild_id, position, label, description, required,
            )

    async def update_checkbox(self, checkbox_id: int, **values: Any) -> None:
        if not values:
            return
        assert self.pool is not None
        columns = list(values.keys())
        assignments = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(columns))
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"UPDATE checkboxes SET {assignments} WHERE id = $1",
                checkbox_id, *values.values(),
            )

    async def delete_checkbox(self, checkbox_id: int) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM checkboxes WHERE id = $1", checkbox_id)

    # --------------------------------------------------------------- requests --
    async def create_request(
        self,
        guild_id: int,
        user_id: int,
        answers: Sequence[dict[str, Any]],
        checks: Sequence[dict[str, Any]],
    ) -> int:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                """
                INSERT INTO verification_requests (guild_id, user_id, answers, checks)
                VALUES ($1, $2, $3, $4) RETURNING id
                """,
                guild_id, user_id, list(answers), list(checks),
            )

    async def get_request(self, request_id: int) -> dict[str, Any] | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM verification_requests WHERE id = $1", request_id
            )
        return dict(row) if row else None

    async def get_pending_request(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM verification_requests
                WHERE guild_id = $1 AND user_id = $2 AND status = 'pending'
                ORDER BY id DESC LIMIT 1
                """,
                guild_id, user_id,
            )
        return dict(row) if row else None

    async def set_request_message(self, request_id: int, channel_id: int, message_id: int) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE verification_requests SET channel_id = $2, message_id = $3 WHERE id = $1",
                request_id, channel_id, message_id,
            )

    async def close_request(
        self, request_id: int, status: str, handled_by: int, reason: str | None = None
    ) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE verification_requests
                SET status = $2, handled_by = $3, reason = $4, handled_at = NOW()
                WHERE id = $1
                """,
                request_id, status, handled_by, reason,
            )

    async def is_verified(self, guild_id: int, user_id: int) -> bool:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    """
                    SELECT 1 FROM verification_requests
                    WHERE guild_id = $1 AND user_id = $2 AND status = 'approved' LIMIT 1
                    """,
                    guild_id, user_id,
                )
            )


def as_ids(value: Iterable[Any] | None) -> list[int]:
    """asyncpg gibt BIGINT[] als Liste zurueck - hier robust nach int casten."""
    if not value:
        return []
    return [int(v) for v in value]
