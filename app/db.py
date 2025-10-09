# app/db.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple, List, Set, Dict, Iterable

import time
import itertools
import aiosqlite
import json, time 


# =========================
# Init DB (toutes les tables)
# =========================
async def init_db(db_path: Path):
    async with aiosqlite.connect(db_path) as db:
        # Important pour ON DELETE CASCADE
        await db.execute("PRAGMA foreign_keys = ON;")

        # ---- Skills / Liens LoL / Rang LoL ----
        await db.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            user_id TEXT PRIMARY KEY,
            rating REAL NOT NULL
        )""")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS lol_links (
            user_id TEXT PRIMARY KEY,
            summoner_name TEXT NOT NULL,
            region TEXT NOT NULL
        )""")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS lol_rank (
            user_id TEXT PRIMARY KEY,
            source TEXT NOT NULL, -- offline/riot
            tier TEXT NOT NULL,
            division TEXT,
            lp INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL
        )""")

        # ---- Tournoi ----
        await db.execute("""
        CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            name TEXT NOT NULL,
            state TEXT NOT NULL, -- setup | running | finished | cancelled
            created_by TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            started_at INTEGER
        )""")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS tournament_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            seed INTEGER NOT NULL,
            rating REAL NOT NULL,
            UNIQUE(tournament_id, user_id)
        )""")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS tournament_matches (
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

        # ---- TeamRolls (sessions & paires) ----
        await db.execute("""
        CREATE TABLE IF NOT EXISTS team_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )""")

        await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_team_sessions_unique
        ON team_sessions(guild_id, name)
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS team_pair_counts (
            session_id INTEGER NOT NULL,
            user_a TEXT NOT NULL,
            user_b TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (session_id, user_a, user_b),
            FOREIGN KEY(session_id) REFERENCES team_sessions(id) ON DELETE CASCADE
        )""")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS team_last (
            guild_id TEXT PRIMARY KEY,
            snapshot_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )""")

        # (Optionnel) index de perf si besoin de stats lourdes par session
        await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_team_pairs_by_session
        ON team_pair_counts(session_id)
        """)

        await db.commit()


# =========================
# Repos Skills
# =========================
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


# =========================
# Repos Liens LoL
# =========================
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


# =========================
# Repos Rang LoL “humain”
# =========================
async def set_lol_rank(
    db_path: Path,
    user_id: int,
    source: str,
    tier: str,
    division: Optional[str],
    lp: int
):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
        INSERT INTO lol_rank (user_id, source, tier, division, lp, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
          source=excluded.source, tier=excluded.tier, division=excluded.division,
          lp=excluded.lp, updated_at=excluded.updated_at
        """, (str(user_id), source, tier.upper(), division, int(lp or 0), int(time.time())))
        await db.commit()


async def fetch_all_ratings_and_links(
    db_path: Path
) -> tuple[list[tuple[int, float]], set[int], dict[int, tuple[str, Optional[str], int]]]:
    rows: list[tuple[int, float]] = []
    linked: set[int] = set()
    ranks: dict[int, tuple[str, Optional[str], int]] = {}
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT user_id, rating FROM skills") as cur:
            async for uid, rating in cur:
                try:
                    rows.append((int(uid), float(rating)))
                except Exception:
                    pass
        async with db.execute("SELECT user_id FROM lol_links") as cur:
            async for (uid,) in cur:
                try:
                    linked.add(int(uid))
                except Exception:
                    pass
        async with db.execute("SELECT user_id, tier, division, lp FROM lol_rank") as cur:
            async for uid, tier, division, lp in cur:
                try:
                    ranks[int(uid)] = (str(tier or ""), (division if division else None), int(lp or 0))
                except Exception:
                    pass
    return rows, linked, ranks


# =========================
# Repos Tournoi
# =========================
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
        async with db.execute(
            "SELECT * FROM tournaments WHERE guild_id=? AND state IN ('setup','running') ORDER BY id DESC LIMIT 1",
            (str(guild_id),)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            cols = [c[0] for c in cur.description]
            return dict(zip(cols, row))


async def set_tournament_state(db_path: Path, tournament_id: int, new_state: str, started: bool = False):
    async with aiosqlite.connect(db_path) as db:
        if started:
            await db.execute(
                "UPDATE tournaments SET state=?, started_at=? WHERE id=?",
                (new_state, int(time.time()), int(tournament_id))
            )
        else:
            await db.execute("UPDATE tournaments SET state=? WHERE id=?", (new_state, int(tournament_id)))
        await db.commit()


async def add_participant(db_path: Path, tournament_id: int, user_id: int, seed: int, rating: float):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO tournament_participants (tournament_id, user_id, seed, rating) VALUES (?, ?, ?, ?)",
            (int(tournament_id), str(user_id), int(seed), float(rating))
        )
        await db.commit()


async def list_participants(db_path: Path, tournament_id: int) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("""
            SELECT user_id, seed, rating
            FROM tournament_participants
            WHERE tournament_id=?
            ORDER BY seed ASC
        """, (int(tournament_id),)) as cur:
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
            await db.execute("""
                INSERT INTO tournament_matches
                (tournament_id, round, pos_in_round, p1_user_id, p2_user_id, best_of, status, next_match_id, next_slot)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                int(tournament_id), int(m["round"]), int(m["pos_in_round"]),
                str(m.get("p1_user_id")) if m.get("p1_user_id") else None,
                str(m.get("p2_user_id")) if m.get("p2_user_id") else None,
                int(m.get("best_of", 1)), m.get("status", "pending"),
                m.get("next_match_id"), m.get("next_slot")
            ))
        await db.commit()


async def list_matches(db_path: Path, tournament_id: int) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("""
            SELECT * FROM tournament_matches
            WHERE tournament_id=?
            ORDER BY round ASC, pos_in_round ASC
        """, (int(tournament_id),)) as cur:
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


async def report_match_result(
    db_path: Path,
    tournament_id: int,
    match_id: int,
    winner_user_id: int,
    p1_score: int,
    p2_score: int
) -> Optional[int]:
    """
    Met à jour le match, propage le vainqueur au match suivant.
    Retourne l'ID du match suivant (ou None).
    """
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("""
            SELECT id, p1_user_id, p2_user_id, next_match_id, next_slot
            FROM tournament_matches
            WHERE id=? AND tournament_id=?
        """, (int(match_id), int(tournament_id))) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            _id, p1, p2, next_id, next_slot = row

        await db.execute("""
            UPDATE tournament_matches
            SET winner_user_id=?, p1_score=?, p2_score=?, status='done'
            WHERE id=? AND tournament_id=?
        """, (str(winner_user_id), int(p1_score), int(p2_score), int(match_id), int(tournament_id)))
        await db.commit()

        if next_id:
            await update_match_participant(db_path, int(next_id), int(next_slot), int(winner_user_id))
            await set_match_open_if_ready(db_path, int(next_id))
            return int(next_id)
        return None


# =========================
# Repos TeamRolls
# =========================
async def get_or_create_session_id(db_path: Path, guild_id: int, name: str) -> int:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT id FROM team_sessions WHERE guild_id=? AND name=?",
            (str(guild_id), name)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return int(row[0])
        await db.execute(
            "INSERT INTO team_sessions(guild_id, name, created_at) VALUES(?,?,?)",
            (str(guild_id), name, int(time.time()))
        )
        await db.commit()
        async with db.execute(
            "SELECT id FROM team_sessions WHERE guild_id=? AND name=?",
            (str(guild_id), name)
        ) as cur:
            row = await cur.fetchone()
            return int(row[0])


async def load_pair_counts(db_path: Path, session_id: int) -> Dict[Tuple[int, int], int]:
    out: Dict[Tuple[int, int], int] = {}
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT user_a, user_b, count FROM team_pair_counts WHERE session_id=?",
            (session_id,)
        ) as cur:
            async for ua, ub, c in cur:
                out[(int(ua), int(ub))] = int(c)
    return out


async def bump_pair_counts(db_path: Path, session_id: int, teams: Iterable[Iterable[int]]) -> None:
    """Incrémente le compteur pour chaque paire de coéquipiers de cette combinaison."""
    pairs: Dict[Tuple[int, int], int] = {}
    for team in teams:
        members = list(team)
        for a, b in itertools.combinations(sorted(members), 2):
            key = (a, b)
            pairs[key] = pairs.get(key, 0) + 1

    if not pairs:
        return

    async with aiosqlite.connect(db_path) as db:
        # upsert pour chaque paire
        for (a, b), inc in pairs.items():
            await db.execute("""
                INSERT INTO team_pair_counts(session_id, user_a, user_b, count)
                VALUES(?,?,?,?)
                ON CONFLICT(session_id, user_a, user_b)
                DO UPDATE SET count = count + excluded.count
            """, (session_id, str(a), str(b), int(inc)))
        await db.commit()


async def end_session(db_path: Path, guild_id: int, name: str) -> int:
    """Supprime la session + ses compteurs. Retourne 1 si supprimée, 0 sinon."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT id FROM team_sessions WHERE guild_id=? AND name=?",
            (str(guild_id), name)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return 0
        sid = int(row[0])
        await db.execute("DELETE FROM team_pair_counts WHERE session_id=?", (sid,))
        await db.execute("DELETE FROM team_sessions WHERE id=?", (sid,))
        await db.commit()
        return 1


async def session_stats(db_path: Path, session_id: int, user_ids: Iterable[int]) -> Tuple[int, int]:
    """
    Retourne (paires_vues, paires_possibles) pour le set de joueurs courant.
    Utile pour afficher une progression “tout le monde a joué avec tout le monde”.
    """
    ids = sorted(set(int(x) for x in user_ids))
    all_pairs = set(itertools.combinations(ids, 2))
    seen = 0
    counts = await load_pair_counts(db_path, session_id)
    for a, b in all_pairs:
        if (a, b) in counts:
            seen += 1
    return seen, len(all_pairs)

async def set_team_last(db_path: Path, guild_id: int, snapshot: dict) -> None:
    payload = json.dumps(snapshot, ensure_ascii=False)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            INSERT INTO team_last(guild_id, snapshot_json, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
              snapshot_json=excluded.snapshot_json,
              updated_at=excluded.updated_at
        """, (str(guild_id), payload, int(time.time())))
        await db.commit()

async def get_team_last(db_path: Path, guild_id: int) -> Optional[dict]:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT snapshot_json FROM team_last WHERE guild_id=?", (str(guild_id),)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            try:
                return json.loads(row[0])
            except Exception:
                return None


async def set_next_links(db_path, tournament_id, updates):
    """
    updates: list[(match_id, next_match_id, next_slot)]
    Met à jour next_match_id / next_slot sans recréer les matchs.
    """
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        # Détecte le bon nom de table utilisé par le schéma existant
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('matches','tournament_matches')"
        )
        row = await cur.fetchone()
        await cur.close()
        table = row[0] if row else "matches"  # fallback

        sql = f"UPDATE {table} SET next_match_id=?, next_slot=? WHERE id=? AND tournament_id=?"
        await db.executemany(sql, ((nmid, slot, mid, tournament_id) for (mid, nmid, slot) in updates))
        await db.commit()


# --- Historique compositions d'équipes (anti-répétition globale) ---

async def _ensure_team_history_table(db):
    await db.execute("""
    CREATE TABLE IF NOT EXISTS team_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        session TEXT NOT NULL,
        players_fp TEXT NOT NULL,
        sizes_fp TEXT NOT NULL,
        signature TEXT NOT NULL,
        created_at INTEGER NOT NULL
    );
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_th_lookup ON team_history(guild_id, session, players_fp, sizes_fp);")
    await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_th_unique ON team_history(guild_id, session, players_fp, sizes_fp, signature);")


async def load_team_signatures(db_path: str, guild_id: int, session: str, players_fp: str, sizes_fp: str) -> set[str]:
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        await _ensure_team_history_table(db)
        cur = await db.execute("""
            SELECT signature
            FROM team_history
            WHERE guild_id=? AND session=? AND players_fp=? AND sizes_fp=?;
        """, (guild_id, session, players_fp, sizes_fp))
        rows = await cur.fetchall()
        await cur.close()
        return {r[0] for r in rows}


async def add_team_signature(db_path: str, guild_id: int, session: str, players_fp: str, sizes_fp: str, signature: str, created_at: int) -> bool:
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        await _ensure_team_history_table(db)
        try:
            await db.execute("""
                INSERT INTO team_history (guild_id, session, players_fp, sizes_fp, signature, created_at)
                VALUES (?, ?, ?, ?, ?, ?);
            """, (guild_id, session, players_fp, sizes_fp, signature, created_at))
            await db.commit()
            return True
        except Exception:
            # Signature déjà vue (unique constraint)
            return False


async def clear_team_signatures(db_path: str, guild_id: int, session: str, players_fp: str = "", sizes_fp: str = "") -> int:
    """Efface l'historique pour une session (et éventuellement un fingerprint précis). Renvoie le nb de lignes supprimées."""
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        await _ensure_team_history_table(db)
        if players_fp and sizes_fp:
            cur = await db.execute("""
                DELETE FROM team_history
                WHERE guild_id=? AND session=? AND players_fp=? AND sizes_fp=?;
            """, (guild_id, session, players_fp, sizes_fp))
        else:
            cur = await db.execute("""
                DELETE FROM team_history
                WHERE guild_id=? AND session=?;
            """, (guild_id, session))
        n = cur.rowcount if cur.rowcount is not None else 0
        await db.commit()
        return n
