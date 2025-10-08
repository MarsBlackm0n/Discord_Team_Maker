# app/db.py
from __future__ import annotations
from pathlib import Path
import time
import aiosqlite
from typing import Optional, Tuple, List, Set, Dict

# Schéma + connexions
async def init_db(db_path: Path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS skills (
            user_id TEXT PRIMARY KEY,
            rating REAL NOT NULL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS lol_links (
            user_id TEXT PRIMARY KEY,
            summoner_name TEXT NOT NULL,
            region TEXT NOT NULL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS lol_rank (
            user_id TEXT PRIMARY KEY,
            source TEXT NOT NULL, -- offline/riot
            tier TEXT NOT NULL,
            division TEXT,
            lp INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL
        )""")
        await db.commit()

# Repos Skills
async def get_rating(db_path: Path, user_id: int) -> Optional[float]:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT rating FROM skills WHERE user_id=?", (str(user_id),)) as cur:
            row = await cur.fetchone()
            return float(row[0]) if row else None

async def set_rating(db_path: Path, user_id: int, rating: float):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO skills(user_id, rating) VALUES(?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET rating=excluded.rating",
            (str(user_id), float(rating)),
        )
        await db.commit()

# Repos Liens LoL
async def get_linked_lol(db_path: Path, user_id: int) -> Optional[Tuple[str, str]]:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT summoner_name, region FROM lol_links WHERE user_id=?", (str(user_id),)) as cur:
            row = await cur.fetchone()
            return (row[0], row[1]) if row else None

async def link_lol(db_path: Path, user_id: int, summoner: str, region: str):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO lol_links(user_id, summoner_name, region) VALUES(?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET summoner_name=excluded.summoner_name, region=excluded.region",
            (str(user_id), summoner, region),
        )
        await db.commit()

# Repos Rang LoL “humain”
async def set_lol_rank(db_path: Path, user_id: int, source: str, tier: str, division: Optional[str], lp: int):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
        INSERT INTO lol_rank (user_id, source, tier, division, lp, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
          source=excluded.source, tier=excluded.tier, division=excluded.division,
          lp=excluded.lp, updated_at=excluded.updated_at
        """, (str(user_id), source, tier.upper(), division, int(lp or 0), int(time.time())))
        await db.commit()

async def fetch_all_ratings_and_links(db_path: Path) -> tuple[list[tuple[int, float]], set[int], dict[int, tuple[str, Optional[str], int]]]:
    rows: list[tuple[int, float]] = []
    linked: set[int] = set()
    ranks: dict[int, tuple[str, Optional[str], int]] = {}
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT user_id, rating FROM skills") as cur:
            async for uid, rating in cur:
                try: rows.append((int(uid), float(rating)))
                except: pass
        async with db.execute("SELECT user_id FROM lol_links") as cur:
            async for (uid,) in cur:
                try: linked.add(int(uid))
                except: pass
        async with db.execute("SELECT user_id, tier, division, lp FROM lol_rank") as cur:
            async for uid, tier, division, lp in cur:
                try: ranks[int(uid)] = (str(tier or ""), (division if division else None), int(lp or 0))
                except: pass
    return rows, linked, ranks
