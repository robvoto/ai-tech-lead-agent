"""Bounded, durable operator override of an execution tier.

An override lets an operator temporarily resolve one tier's calls (e.g.
"simple") using a different tier's profile (e.g. "strong") for a fixed
number of remaining calls. It is stored in SQLite so it survives an ATL
restart/reload rather than silently reverting mid-test — but it is not a
config change: the permanent default in settings/registry is never touched,
and the override always expires on its own once remaining_calls hits zero.

See execution_profiles.py for the tiers/ceilings this overrides between, and
telegram_operator.py for the operator-facing /profile commands.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .app_settings import AppSettings
from .config import DB_PATH
from .execution_profiles import (
    PURPOSE_TIER,
    TIER_CEILING_EFFORT,
    ExecutionProfile,
    ExecutionProfileError,
    resolve_profile_for_purpose,
)

OVERRIDE_DB_PATH = DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profile_tier_overrides (
    tier             TEXT PRIMARY KEY,
    override_tier    TEXT NOT NULL,
    remaining_calls  INTEGER NOT NULL,
    created_at       TEXT NOT NULL,
    created_by       TEXT NOT NULL
);
"""


class ProfileOverrideError(RuntimeError):
    """Raised for an invalid override request."""


@dataclass(frozen=True)
class ProfileTierOverride:
    tier: str
    override_tier: str
    remaining_calls: int
    created_at: str
    created_by: str


class ProfileOverrideStore:
    """Persist and consume bounded per-tier overrides."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or OVERRIDE_DB_PATH

    def set_override(
        self, *, tier: str, override_tier: str, calls: int, created_by: str
    ) -> ProfileTierOverride:
        if tier not in TIER_CEILING_EFFORT:
            raise ProfileOverrideError(f"Unknown tier: '{tier}'")
        if override_tier not in TIER_CEILING_EFFORT:
            raise ProfileOverrideError(f"Unknown override tier: '{override_tier}'")
        if calls < 1:
            raise ProfileOverrideError("Override must last at least 1 call.")
        override = ProfileTierOverride(
            tier=tier,
            override_tier=override_tier,
            remaining_calls=calls,
            created_at=_now_iso(),
            created_by=created_by.strip(),
        )
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO profile_tier_overrides
                    (tier, override_tier, remaining_calls, created_at, created_by)
                    VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tier) DO UPDATE SET
                    override_tier=excluded.override_tier,
                    remaining_calls=excluded.remaining_calls,
                    created_at=excluded.created_at,
                    created_by=excluded.created_by""",
                (
                    override.tier,
                    override.override_tier,
                    override.remaining_calls,
                    override.created_at,
                    override.created_by,
                ),
            )
        return override

    def clear_override(self, tier: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM profile_tier_overrides WHERE tier = ?", (tier,))

    def list_overrides(self) -> list[ProfileTierOverride]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT tier, override_tier, remaining_calls, created_at, created_by "
                "FROM profile_tier_overrides ORDER BY tier"
            ).fetchall()
        return [
            ProfileTierOverride(
                tier=row[0],
                override_tier=row[1],
                remaining_calls=row[2],
                created_at=row[3],
                created_by=row[4],
            )
            for row in rows
        ]

    def consume_active_override_tier(self, tier: str) -> str | None:
        """Return the override tier to use in place of `tier` for one call,
        decrementing its remaining-calls counter and deleting it once spent.

        Returns None when no override is active for `tier` — the caller
        should then resolve `tier` normally.
        """

        with self._connect() as conn:
            row = conn.execute(
                "SELECT override_tier, remaining_calls FROM profile_tier_overrides "
                "WHERE tier = ?",
                (tier,),
            ).fetchone()
            if row is None:
                return None
            override_tier, remaining_calls = row[0], row[1]
            if remaining_calls <= 1:
                conn.execute("DELETE FROM profile_tier_overrides WHERE tier = ?", (tier,))
            else:
                conn.execute(
                    "UPDATE profile_tier_overrides SET remaining_calls = ? WHERE tier = ?",
                    (remaining_calls - 1, tier),
                )
        return override_tier

    def _connect(self) -> "_ManagedConnection":
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.executescript(_SCHEMA)
        return _ManagedConnection(conn)


class _ManagedConnection:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_profile_with_override(
    purpose: str,
    *,
    settings: AppSettings | None = None,
    min_output_tokens: int | None = None,
    store: ProfileOverrideStore | None = None,
) -> ExecutionProfile:
    """The one entry point workflow call sites use: resolve `purpose`,
    applying any active bounded override for its tier and consuming one call
    of that override's remaining count."""

    try:
        tier = PURPOSE_TIER[purpose]
    except KeyError:
        raise ExecutionProfileError(f"Unknown orchestrator call purpose: '{purpose}'") from None

    active_store = store or ProfileOverrideStore()
    override_tier = active_store.consume_active_override_tier(tier)
    return resolve_profile_for_purpose(
        purpose,
        settings=settings,
        min_output_tokens=min_output_tokens,
        override_tier=override_tier,
    )
