# app/db.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple, List, Set, Dict, Iterable

import time
import itertools
import aiosqlite
import json


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

        # ---- Tournoi (user vs user) ----
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

        await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_team_pairs_by_session
        ON team_pair_counts(session_id)
        """)

        # ---- Historique des compositions (signatures fortes) ----
        await _ensure_team_history_table(db)

        await db.commit()

# --- helpers JSON sûrs (si pas déjà dans ton fichier)
def _json_dump(x) -> str:
    return json.dumps(x, separators=(",", ":"), ensure_ascii=False)

def _json_load(s, default):
    try:
        return json.loads(s) if isinstance(s, (str, bytes, bytearray)) else (s or default)
    except Exception:
        return default

async def ensure_arena_schema(db_path: str) -> None:
    """Crée la table arena si manquante et ajoute la colonne 'reported' si absente."""
    async with aiosqlite.connect(db_path) as db:
        # 1) Table principale
        await db.execute("""
        CREATE TABLE IF NOT EXISTS arena (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            creator_id INTEGER NOT NULL,
            state TEXT NOT NULL,                 -- 'setup' | 'running' | 'finished' | 'cancelled'
            participants TEXT NOT NULL,          -- JSON list[int]
            schedule TEXT NOT NULL,              -- JSON list[list[[u1,u2],...]] par round
            scores TEXT NOT NULL,                -- JSON dict[str->int]
            current_round INTEGER NOT NULL,      -- 1-based
            rounds_total INTEGER NOT NULL,
            reported TEXT NOT NULL DEFAULT '{}', -- JSON dict[str(round)-> list['u1-u2']]
            created_at INTEGER NOT NULL
        );
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_arena_guild ON arena(guild_id);")

        # 2) Migration légère : colonne 'reported' si elle n'existe pas
        cur = await db.execute("PRAGMA table_info(arena);")
        cols = [r[1] for r in await cur.fetchall()]
        if "reported" not in cols:
            await db.execute("ALTER TABLE arena ADD COLUMN reported TEXT NOT NULL DEFAULT '{}';")

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
# Repos Tournoi (user vs user)
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
# Repos TeamRolls (paires)
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


# =========================
# Utilitaire wiring matches (compat schémas)
# =========================
async def set_next_links(db_path, tournament_id, updates):
    """
    updates: list[(match_id, next_match_id, next_slot)]
    Met à jour next_match_id / next_slot sans recréer les matchs.
    """
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


# =========================
# Historique compositions d'équipes (signatures fortes)
# =========================

async def _ensure_team_history_table(db: aiosqlite.Connection):
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


async def prune_team_signatures(db_path: str, guild_id: int, session: str, players_fp: str, sizes_fp: str, keep_last: int) -> int:
    """
    Ne conserve que les 'keep_last' dernières signatures (créées les plus récentes en premier)
    pour (guild_id, session, players_fp, sizes_fp).
    Retourne le nombre de lignes supprimées.
    """
    async with aiosqlite.connect(db_path) as db:
        await _ensure_team_history_table(db)
        cur = await db.execute("""
            SELECT id
            FROM team_history
            WHERE guild_id=? AND session=? AND players_fp=? AND sizes_fp=?
            ORDER BY created_at DESC, id DESC
        """, (guild_id, session, players_fp, sizes_fp))
        rows = await cur.fetchall()
        await cur.close()
        ids = [r[0] for r in rows]
        if len(ids) <= keep_last:
            return 0
        to_delete = ids[keep_last:]
        qmarks = ",".join("?" for _ in to_delete)
        await db.execute(f"DELETE FROM team_history WHERE id IN ({qmarks})", to_delete)
        await db.commit()
        return len(to_delete)


async def clear_team_signatures(db_path: str, guild_id: int, session: str, players_fp: str = "", sizes_fp: str = "") -> int:
    """
    Si 'session' est vide: on efface pour TOUTES les sessions mais UNIQUEMENT si players_fp & sizes_fp sont fournis.
    Sinon, si session fournie:
      - si players_fp & sizes_fp fournis: purge ciblée (set + tailles)
      - sinon: purge toute la session
    """
    async with aiosqlite.connect(db_path) as db:
        await _ensure_team_history_table(db)
        if session and players_fp and sizes_fp:
            cur = await db.execute("""
                DELETE FROM team_history
                WHERE guild_id=? AND session=? AND players_fp=? AND sizes_fp=?;
            """, (guild_id, session, players_fp, sizes_fp))
        elif session:
            cur = await db.execute("""
                DELETE FROM team_history
                WHERE guild_id=? AND session=?;
            """, (guild_id, session))
        else:
            # session vide -> on exige players_fp & sizes_fp pour éviter un wipe global
            if not (players_fp and sizes_fp):
                return 0
            cur = await db.execute("""
                DELETE FROM team_history
                WHERE guild_id=? AND players_fp=? AND sizes_fp=?;
            """, (guild_id, players_fp, sizes_fp))
        n = cur.rowcount if cur.rowcount is not None else 0
        await db.commit()
        return n


# ====== Arena (tournoi LoL 2v2, classement individuel) ======

ARENA_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS arena_tournaments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  state TEXT NOT NULL,             -- setup|running|finished|cancelled
  created_by INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  rounds_total INTEGER NOT NULL,
  current_round INTEGER NOT NULL,  -- 1-based, prochain round à jouer
  participants_json TEXT NOT NULL, -- [user_id,...] (ordre stable)
  schedule_json TEXT NOT NULL,     -- [[ [u1,u2], [u3,u4], ... ], ...] rounds -> duos
  scores_json TEXT NOT NULL        -- {user_id: points}
);
CREATE INDEX IF NOT EXISTS idx_arena_by_guild ON arena_tournaments(guild_id, state);
"""

async def _arena_ensure_tables(db_path: str):
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(ARENA_TABLE_SQL)
        await db.commit()

async def arena_get_active(db_path: str, guild_id: int):
    """Retourne le tournoi Arena actif (setup/running) sous forme de dict Python (JSON déjà décodés), ou None."""
    await _arena_ensure_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT * FROM arena_tournaments WHERE guild_id=? AND state IN ('setup','running') ORDER BY id DESC LIMIT 1",
            (int(guild_id),)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            cols = [c[0] for c in cur.description]
            rec = dict(zip(cols, row))
            rec["participants"] = json.loads(rec["participants_json"])
            rec["schedule"] = json.loads(rec["schedule_json"])
            rec["scores"] = json.loads(rec["scores_json"])
            return rec

async def arena_create(db_path: str, guild_id: int, created_by: int, rounds_total: int,
                       participants: list[int], schedule: list[list[list[int]]]) -> int:
    """Crée un tournoi Arena en état 'running' (round courant = 1). Retourne l'id."""
    await _arena_ensure_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        scores = {str(uid): 0 for uid in participants}
        await db.execute("""
            INSERT INTO arena_tournaments
            (guild_id, state, created_by, created_at, rounds_total, current_round, participants_json, schedule_json, scores_json)
            VALUES (?, 'running', ?, ?, ?, 1, ?, ?, ?)
        """, (
            int(guild_id), int(created_by), int(time.time()), int(rounds_total),
            json.dumps(participants), json.dumps(schedule), json.dumps(scores)
        ))
        await db.commit()
        cur = await db.execute("SELECT last_insert_rowid()")
        (tid,) = await cur.fetchone()
        return int(tid)

async def arena_update_scores_and_advance(db_path: str, arena_id: int, new_scores: dict[int, int]):
    """Ajoute des points aux joueurs et passe au round suivant (ou termine si dernier round atteint)."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT rounds_total, current_round, scores_json FROM arena_tournaments WHERE id=?",
            (int(arena_id),)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return
            rounds_total, current_round, scores_json = row
            scores = json.loads(scores_json)

        for uid, pts in (new_scores or {}).items():
            scores[str(int(uid))] = int(scores.get(str(int(uid)), 0)) + int(pts)

        next_round = int(current_round) + 1
        state = "running" if next_round <= int(rounds_total) else "finished"

        await db.execute("""
            UPDATE arena_tournaments
            SET scores_json=?, current_round=?, state=?
            WHERE id=?
        """, (
            json.dumps(scores),
            int(next_round if state == "running" else current_round),
            state,
            int(arena_id)
        ))
        await db.commit()

async def arena_get_by_id(db_path: str, arena_id: int):
    """Récupère un tournoi Arena par id (dict avec JSON décodés)."""
    await _arena_ensure_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT * FROM arena_tournaments WHERE id=?", (int(arena_id),)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            cols = [c[0] for c in cur.description]
            rec = dict(zip(cols, row))
            rec["participants"] = json.loads(rec["participants_json"])
            rec["schedule"] = json.loads(rec["schedule_json"])
            rec["scores"] = json.loads(rec["scores_json"])
            return rec

async def arena_set_state(db_path: str, arena_id: int, new_state: str):
    """Force l'état (running/finished/cancelled)."""
    await _arena_ensure_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE arena_tournaments SET state=? WHERE id=?", (new_state, int(arena_id)))
        await db.commit()



def _pair_key(a: int, b: int) -> str:
    x, y = sorted((int(a), int(b)))
    return f"{x}-{y}"

async def arena_mark_results(db_path: str, arena_id: int, round_index: int,
                             new_scores: Dict[int, int],
                             reported_pairs: List[Tuple[int, int]]) -> Dict:
    """
    - Ajoute `new_scores` aux scores
    - Marque les `reported_pairs` pour le round `round_index`
    - Avance si le round courant est complet
    - Migration à la volée: ajoute reported_json à arena_tournaments si absente
    """
    import aiosqlite, json

    def _pair_key(a: int, b: int) -> str:
        x, y = sorted((int(a), int(b)))
        return f"{x}-{y}"

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        # --- MIGRATION à la volée : s'assurer que reported_json existe
        # (ne casse rien si la colonne est déjà là)
        try:
            cur = await db.execute("PRAGMA table_info(arena_tournaments);")
            cols = [r[1] for r in await cur.fetchall()]
            await cur.close()
            if "reported_json" not in cols:
                await db.execute(
                    "ALTER TABLE arena_tournaments "
                    "ADD COLUMN reported_json TEXT NOT NULL DEFAULT '{}';"
                )
                await db.commit()
        except Exception:
            # On ignore silencieusement : si la table n'existe pas encore, on tombera sur la table legacy plus bas
            pass

        # 1) On tente la table moderne
        cur = await db.execute("SELECT * FROM arena_tournaments WHERE id=?", (int(arena_id),))
        row = await cur.fetchone()
        await cur.close()

        table = "arena_tournaments"
        if not row:
            # 2) Fallback vers la table legacy
            table = "arena"
            cur = await db.execute("SELECT * FROM arena WHERE id=?", (int(arena_id),))
            row = await cur.fetchone()
            await cur.close()

        if not row:
            raise RuntimeError("Arena introuvable")

        # --- Convertir en dict pour .get()
        rec = {k: row[k] for k in row.keys()}

        # --- Décodage JSON double schéma
        if "participants_json" in rec:  # nouveau schéma
            participants = json.loads(rec["participants_json"]) if rec.get("participants_json") else []
            schedule = json.loads(rec["schedule_json"]) if rec.get("schedule_json") else []
            scores = {int(k): int(v) for k, v in (json.loads(rec["scores_json"]) if rec.get("scores_json") else {}).items()}
            reported = json.loads(rec.get("reported_json") or "{}")
        else:  # legacy
            def _jload(val, default):
                try:
                    return json.loads(val) if isinstance(val, (str, bytes, bytearray)) else (val or default)
                except Exception:
                    return default
            participants = _jload(rec.get("participants"), [])
            schedule = _jload(rec.get("schedule"), [])
            scores = {int(k): int(v) for k, v in _jload(rec.get("scores"), {}).items()}
            reported = _jload(rec.get("reported"), {})

        if not isinstance(reported, dict):
            reported = {}

        # --- 1) MAJ scores
        for uid, pts in (new_scores or {}).items():
            uid = int(uid)
            scores[uid] = int(scores.get(uid, 0)) + int(pts)

        # --- 2) Marquer les paires reportées
        rkey = str(int(round_index))
        seen = set(reported.get(rkey, []))
        for (a, b) in (reported_pairs or []):
            seen.add(_pair_key(a, b))
        reported[rkey] = sorted(seen)

        # --- 3) Complétude du round courant
        cur_round = int(rec["current_round"])
        rounds_total = int(rec["rounds_total"])
        state = rec["state"]

        this_pairs = schedule[cur_round - 1] if 1 <= cur_round <= len(schedule) else []
        needed = {_pair_key(p[0], p[1]) for p in this_pairs}
        have = set(reported.get(str(cur_round), []))
        complete = (needed and have.issuperset(needed))

        if complete:
            next_round = cur_round + 1
            if next_round > rounds_total:
                state = "finished"
            else:
                state = "running"
                cur_round = next_round

        # --- 4) Persist
        if table == "arena_tournaments":
            await db.execute("""
                UPDATE arena_tournaments
                SET scores_json=?, reported_json=?, current_round=?, state=?
                WHERE id=?
            """, (json.dumps(scores), json.dumps(reported), cur_round, state, int(arena_id)))
        else:
            await db.execute("""
                UPDATE arena
                SET scores=?, reported=?, current_round=?, state=?
                WHERE id=?
            """, (json.dumps(scores), json.dumps(reported), cur_round, state, int(arena_id)))

        await db.commit()

        # --- 5) Retour
        return {
            "id": int(arena_id),
            "participants": participants,
            "schedule": schedule,
            "scores": scores,
            "reported": reported,
            "current_round": cur_round,
            "rounds_total": rounds_total,
            "state": state,
        }
