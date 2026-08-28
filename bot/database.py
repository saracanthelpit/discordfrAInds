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
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected
    approved_by INTEGER,
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


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def create_submission(name: str, image_url: str, submitted_by: int) -> int:
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


async def set_submission_status(card_id: int, status: str, approved_by: int | None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE cards SET status = ?, approved_by = ? WHERE id = ?",
            (status, approved_by, card_id),
        )
        await db.commit()


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
            SELECT uc.id, uc.card_id, c.name AS card_name, c.image_url, uc.print_number
            FROM user_cards uc
            JOIN cards c ON c.id = uc.card_id
            WHERE uc.owner_id = ?
            ORDER BY uc.claimed_at DESC
            """,
            (owner_id,),
        )
        rows = await cursor.fetchall()
        return [OwnedCard(**dict(row)) for row in rows]


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


async def get_pending_submissions() -> list[Card]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, name, image_url, submitted_by, status FROM cards WHERE status = 'pending'"
        )
        rows = await cursor.fetchall()
        return [Card(**dict(row)) for row in rows]
