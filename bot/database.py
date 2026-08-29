"""Async SQLite access layer for the card pool and ownership records."""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "cards.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    image_url TEXT NOT NULL,
    submitted_by INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'approved',   -- approved | removed
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL REFERENCES cards(id),
    owner_id INTEGER NOT NULL,
    print_number INTEGER NOT NULL,
    claimed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@dataclass
class Card:
    id: int
    name: str
    image_url: str
    submitted_by: int
    status: str


@dataclass
class OwnedCard:
    id: int
    card_id: int
    card_name: str
    image_url: str
    print_number: int
    submitted_by: int
    frame: str = "plain"


# Columns added after the initial schema, applied on startup if missing.
MIGRATIONS = {
    "user_cards": {
        "frame": "ALTER TABLE user_cards ADD COLUMN frame TEXT NOT NULL DEFAULT 'plain'",
    },
}


async def _apply_migrations(db: aiosqlite.Connection) -> None:
    for table, columns in MIGRATIONS.items():
        cursor = await db.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in await cursor.fetchall()}
        for column, ddl in columns.items():
            if column not in existing:
                await db.execute(ddl)


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await _apply_migrations(db)
        await db.commit()


async def create_card(name: str, image_url: str, submitted_by: int) -> int:
    """Submits art and puts it straight into the drop pool (no review step)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO cards (name, image_url, submitted_by) VALUES (?, ?, ?)",
            (name, image_url, submitted_by),
        )
        await db.commit()
        return cursor.lastrowid


async def get_card(card_id: int) -> Card | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, name, image_url, submitted_by, status FROM cards WHERE id = ?",
            (card_id,),
        )
        row = await cursor.fetchone()
        return Card(**dict(row)) if row else None


async def get_random_approved_cards(count: int) -> list[Card]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, name, image_url, submitted_by, status FROM cards WHERE status = 'approved'"
        )
        rows = await cursor.fetchall()
        cards = [Card(**dict(row)) for row in rows]
        if not cards:
            return []
        return random.sample(cards, k=min(count, len(cards)))


async def claim_card(card_id: int, owner_id: int) -> int:
    """Records a claim and returns the print number (1-indexed) the claimer got."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM user_cards WHERE card_id = ?", (card_id,)
        )
        (existing_count,) = await cursor.fetchone()
        print_number = existing_count + 1
        await db.execute(
            "INSERT INTO user_cards (card_id, owner_id, print_number) VALUES (?, ?, ?)",
            (card_id, owner_id, print_number),
        )
        await db.commit()
        return print_number


async def get_user_cards(owner_id: int) -> list[OwnedCard]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT uc.id, uc.card_id, c.name AS card_name, c.image_url,
                   uc.print_number, c.submitted_by, uc.frame
            FROM user_cards uc
            JOIN cards c ON c.id = uc.card_id
            WHERE uc.owner_id = ?
            ORDER BY uc.claimed_at DESC
            """,
            (owner_id,),
        )
        rows = await cursor.fetchall()
        return [OwnedCard(**dict(row)) for row in rows]


async def set_frame(user_card_id: int, owner_id: int, frame: str) -> OwnedCard | None:
    """Restyle one owned copy. Returns the updated row, or None if it isn't theirs."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT owner_id FROM user_cards WHERE id = ?", (user_card_id,)
        )
        row = await cursor.fetchone()
        if row is None or row["owner_id"] != owner_id:
            return None
        await db.execute(
            "UPDATE user_cards SET frame = ? WHERE id = ?", (frame, user_card_id)
        )
        await db.commit()
        cursor = await db.execute(
            """
            SELECT uc.id, uc.card_id, c.name AS card_name, c.image_url,
                   uc.print_number, c.submitted_by, uc.frame
            FROM user_cards uc
            JOIN cards c ON c.id = uc.card_id
            WHERE uc.id = ?
            """,
            (user_card_id,),
        )
        updated = await cursor.fetchone()
        return OwnedCard(**dict(updated)) if updated else None


async def get_card_print_count(card_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM user_cards WHERE card_id = ?", (card_id,)
        )
        (count,) = await cursor.fetchone()
        return count


async def transfer_card(user_card_id: int, from_owner_id: int, to_owner_id: int) -> bool:
    """Moves one owned card copy to a new owner. Returns False if it isn't theirs to give."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT owner_id FROM user_cards WHERE id = ?", (user_card_id,)
        )
        row = await cursor.fetchone()
        if row is None or row[0] != from_owner_id:
            return False
        await db.execute(
            "UPDATE user_cards SET owner_id = ? WHERE id = ?",
            (to_owner_id, user_card_id),
        )
        await db.commit()
        return True


async def _all_owned_by(db: aiosqlite.Connection, user_card_ids: list[int], owner_id: int) -> bool:
    for user_card_id in user_card_ids:
        cursor = await db.execute(
            "SELECT owner_id FROM user_cards WHERE id = ?", (user_card_id,)
        )
        row = await cursor.fetchone()
        if row is None or row[0] != owner_id:
            return False
    return True


async def swap_cards(
    give_ids: list[int],
    want_ids: list[int],
    giver_id: int,
    taker_id: int,
) -> bool:
    """Move ``give_ids`` to the taker and ``want_ids`` to the giver in one commit.

    Returns False without touching anything if either side no longer owns every
    card it's putting up (e.g. it was gifted or traded away since the offer).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        if not await _all_owned_by(db, give_ids, giver_id):
            return False
        if not await _all_owned_by(db, want_ids, taker_id):
            return False
        for user_card_id in give_ids:
            await db.execute(
                "UPDATE user_cards SET owner_id = ? WHERE id = ?", (taker_id, user_card_id)
            )
        for user_card_id in want_ids:
            await db.execute(
                "UPDATE user_cards SET owner_id = ? WHERE id = ?", (giver_id, user_card_id)
            )
        await db.commit()
        return True
