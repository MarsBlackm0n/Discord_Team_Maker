# app/db.py

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple, List, Set, Dict, Any

import time
import aiosqlite


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

        # ======== TOURNOI ========
        await db.execute("""CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            name TEXT NOT NULL,
            state TEXT NOT NULL, -- setup | running | finished | cancelled
            created_by TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            started_at INTEGER
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS tournament_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            seed INTEGER NOT NULL,
            rating REAL NOT NULL,
            UNIQUE(tournament_id, user_id)
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS tournament_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            round INTEGER NOT NULL,
            pos_in_round INTEGER NOT NULL, -- index du match dans la ronde
            p1_user_id TEXT,
            p2_user_id TEXT,
            p1_score INTEGER DEFAULT 0,
            p2_score INTEGER DEFAULT 0,
            best_of INTEGER NOT NULL DEFAULT 1,
            winner_user_id TEXT,
            status TEXT NOT NULL, -- pending | open | done
            next_match_id INTEGER,
            next_slot INTEGER -- 1 ou 2 (position dans le match suivant)
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

# ======== REPO TOURNOI ========
import time
from typing import Any

async def create_tournament(db_path: Path, guild_id: int, name: str, created_by: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO tournaments (guild_id, name, state, created_by, created_at) VALUES (?, ?, 'setup', ?, ?)",
            (str(guild_id), name, str(created_by), int(time.time()))
        )
        await db.commit()
        cur = await db.execute("SELECT last_insert_rowid()")
        (tid,) = await cur.fetchone()
        return int(tid)

async def get_active_tournament(db_path: Path, guild_id: int) -> Optional[dict]:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT * FROM tournaments WHERE guild_id=? AND state IN ('setup','running') ORDER BY id DESC LIMIT 1",
                              (str(guild_id),)) as cur:
            row = await cur.fetchone()
            if not row: return None
            cols = [c[0] for c in cur.description]
            return dict(zip(cols, row))

async def set_tournament_state(db_path: Path, tournament_id: int, new_state: str, started: bool = False):
    async with aiosqlite.connect(db_path) as db:
        if started:
            await db.execute("UPDATE tournaments SET state=?, started_at=? WHERE id=?",
                             (new_state, int(time.time()), int(tournament_id)))
        else:
            await db.execute("UPDATE tournaments SET state=? WHERE id=?", (new_state, int(tournament_id)))
        await db.commit()

async def add_participant(db_path: Path, tournament_id: int, user_id: int, seed: int, rating: float):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""INSERT OR IGNORE INTO tournament_participants
            (tournament_id, user_id, seed, rating) VALUES (?, ?, ?, ?)""",
            (int(tournament_id), str(user_id), int(seed), float(rating)))
        await db.commit()

async def list_participants(db_path: Path, tournament_id: int) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("""SELECT user_id, seed, rating
                                 FROM tournament_participants
                                 WHERE tournament_id=?
                                 ORDER BY seed ASC""", (int(tournament_id),)) as cur:
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) async for row in cur]

async def clear_bracket(db_path: Path, tournament_id: int):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM tournament_matches WHERE tournament_id=?", (int(tournament_id),))
        await db.commit()

async def create_matches(db_path: Path, tournament_id: int, matches: list[dict]):
    """
    matches: liste de dicts:
      {round, pos_in_round, p1_user_id, p2_user_id, best_of, status, next_match_id, next_slot}
    """
    async with aiosqlite.connect(db_path) as db:
        for m in matches:
            await db.execute("""INSERT INTO tournament_matches
                (tournament_id, round, pos_in_round, p1_user_id, p2_user_id, best_of, status, next_match_id, next_slot)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (int(tournament_id), int(m["round"]), int(m["pos_in_round"]),
                 str(m.get("p1_user_id")) if m.get("p1_user_id") else None,
                 str(m.get("p2_user_id")) if m.get("p2_user_id") else None,
                 int(m.get("best_of", 1)), m.get("status", "pending"),
                 m.get("next_match_id"), m.get("next_slot")))
        await db.commit()

async def list_matches(db_path: Path, tournament_id: int) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("""SELECT * FROM tournament_matches
                                 WHERE tournament_id=?
                                 ORDER BY round ASC, pos_in_round ASC""", (int(tournament_id),)) as cur:
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) async for row in cur]

async def update_match_participant(db_path: Path, match_id: int, slot: int, user_id: int):
    col = "p1_user_id" if slot == 1 else "p2_user_id"
    async with aiosqlite.connect(db_path) as db:
        await db.execute(f"UPDATE tournament_matches SET {col}=? WHERE id=?", (str(user_id), int(match_id)))
        await db.commit()

async def set_match_open_if_ready(db_path: Path, match_id: int):
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT p1_user_id, p2_user_id FROM tournament_matches WHERE id=?", (int(match_id),)) as cur:
            row = await cur.fetchone()
            if row and row[0] and row[1]:
                await db.execute("UPDATE tournament_matches SET status='open' WHERE id=?", (int(match_id),))
                await db.commit()

async def report_match_result(db_path: Path, tournament_id: int, match_id: int, winner_user_id: int, p1_score: int, p2_score: int) -> Optional[int]:
    """
    Met à jour le match, propage le vainqueur au match suivant.
    Retourne l'ID du match suivant (ou None).
    """
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("""SELECT id, p1_user_id, p2_user_id, next_match_id, next_slot
                                 FROM tournament_matches WHERE id=? AND tournament_id=?""",
                              (int(match_id), int(tournament_id))) as cur:
            row = await cur.fetchone()
            if not row: return None
            _id, p1, p2, next_id, next_slot = row

        await db.execute("""UPDATE tournament_matches
                            SET winner_user_id=?, p1_score=?, p2_score=?, status='done'
                            WHERE id=? AND tournament_id=?""",
                         (str(winner_user_id), int(p1_score), int(p2_score), int(match_id), int(tournament_id)))
        await db.commit()

        if next_id:
            await update_match_participant(db_path, int(next_id), int(next_slot), int(winner_user_id))
            await set_match_open_if_ready(db_path, int(next_id))
            return int(next_id)
        return None
