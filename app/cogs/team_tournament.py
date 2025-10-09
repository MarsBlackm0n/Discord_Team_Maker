# app/cogs/team_tournament.py
from __future__ import annotations
from typing import List, Optional, Tuple
import json
import time
import math

import discord
from discord import app_commands
from discord.ext import commands

import aiosqlite

from ..db import get_team_last  # on réutilise ton snapshot "dernière config d'équipes"


# ----------------- Helpers DB locaux (tables dédiées Team vs Team) -----------------

CREATE_TEAM_TOURNAMENTS_SQL = """
CREATE TABLE IF NOT EXISTS team_tournaments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    state TEXT NOT NULL, -- setup | running | cancelled | finished
    created_by INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    cancelled_at INTEGER
);
"""

CREATE_TEAM_MATCHES_SQL = """
CREATE TABLE IF NOT EXISTS team_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL,
    round INTEGER NOT NULL,
    pos_in_round INTEGER NOT NULL,
    p1_team_json TEXT,          -- JSON [user_id...]
    p2_team_json TEXT,          -- JSON [user_id...]
    best_of INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,       -- pending | running | done
    p1_score INTEGER NOT NULL DEFAULT 0,
    p2_score INTEGER NOT NULL DEFAULT 0,
    winner_team_json TEXT,      -- JSON gagnante [user_id...]
    next_match_id INTEGER,      -- FK vers team_matches.id
    next_slot INTEGER,          -- 1 ou 2
    FOREIGN KEY (tournament_id) REFERENCES team_tournaments(id)
);
"""

async def ensure_tables(db_path: str):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(CREATE_TEAM_TOURNAMENTS_SQL)
        await db.execute(CREATE_TEAM_MATCHES_SQL)
        await db.commit()

async def tt_create(db_path: str, guild_id: int, name: str, created_by: int) -> int:
    await ensure_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO team_tournaments (guild_id, name, state, created_by, created_at) VALUES (?,?,?,?,?)",
            (guild_id, name, "setup", created_by, int(time.time()))
        )
        await db.commit()
        return cur.lastrowid

async def tt_get_active(db_path: str, guild_id: int):
    await ensure_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT * FROM team_tournaments WHERE guild_id=? AND state IN ('setup','running') ORDER BY id DESC LIMIT 1",
            (guild_id,)
        )
        row = await cur.fetchone()
        await cur.close()
        return row

async def tt_set_state(db_path: str, tournament_id: int, state: str):
    async with aiosqlite.connect(db_path) as db:
        if state == "running":
            await db.execute("UPDATE team_tournaments SET state=?, started_at=? WHERE id=?", (state, int(time.time()), tournament_id))
        elif state == "cancelled":
            await db.execute("UPDATE team_tournaments SET state=?, cancelled_at=? WHERE id=?", (state, int(time.time()), tournament_id))
        else:
            await db.execute("UPDATE team_tournaments SET state=? WHERE id=?", (state, tournament_id))
        await db.commit()

async def tm_clear(db_path: str, tournament_id: int):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM team_matches WHERE tournament_id=?", (tournament_id,))
        await db.commit()

async def tm_create_many(db_path: str, tournament_id: int, matches: List[dict]):
    async with aiosqlite.connect(db_path) as db:
        await db.executemany(
            """
            INSERT INTO team_matches
            (tournament_id, round, pos_in_round, p1_team_json, p2_team_json, best_of, status, p1_score, p2_score, winner_team_json, next_match_id, next_slot)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    tournament_id,
                    m["round"],
                    m["pos_in_round"],
                    json.dumps(m.get("p1_team") or []),
                    json.dumps(m.get("p2_team") or []),
                    int(m.get("best_of", 1)),
                    m.get("status", "pending"),
                    int(m.get("p1_score", 0)),
                    int(m.get("p2_score", 0)),
                    json.dumps(m.get("winner_team") or []),
                    m.get("next_match_id"),
                    m.get("next_slot"),
                )
                for m in matches
            ]
        )
        await db.commit()

async def tm_list(db_path: str, tournament_id: int) -> List[dict]:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("SELECT * FROM team_matches WHERE tournament_id=? ORDER BY round, pos_in_round", (tournament_id,))
        cols = [c[0] for c in cur.description]
        rows = await cur.fetchall()
        await cur.close()
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        # JSON -> list[int]
        for k in ("p1_team_json", "p2_team_json", "winner_team_json"):
            if d.get(k):
                try:
                    d[k] = json.loads(d[k])
                except Exception:
                    d[k] = []
            else:
                d[k] = []
        out.append(d)
    return out

async def tm_update_next_links(db_path: str, tournament_id: int, triples: List[Tuple[int, int, int]]):
    """triples: (match_id, next_match_id, next_slot)"""
    async with aiosqlite.connect(db_path) as db:
        await db.executemany(
            "UPDATE team_matches SET next_match_id=?, next_slot=? WHERE id=? AND tournament_id=?",
            [(nm, ns, mid, tournament_id) for (mid, nm, ns) in triples]
        )
        await db.commit()

async def tm_set_result(db_path: str, tournament_id: int, match_id: int, winner_team: List[int], p1_score: int, p2_score: int) -> Optional[int]:
    """Retourne next_match_id si dispo, sinon None."""
    async with aiosqlite.connect(db_path) as db:
        # récupérer match + slots
        cur = await db.execute(
            "SELECT id, p1_team_json, p2_team_json, next_match_id, next_slot FROM team_matches WHERE id=? AND tournament_id=?",
            (match_id, tournament_id)
        )
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return None
        _id, p1_json, p2_json, next_id, next_slot = row
        # set winner + score + status
        await db.execute(
            "UPDATE team_matches SET winner_team_json=?, p1_score=?, p2_score=?, status='done' WHERE id=? AND tournament_id=?",
            (json.dumps(winner_team), int(p1_score), int(p2_score), match_id, tournament_id)
        )
        # Propager au match suivant si existe
        if next_id:
            # Insère gagnant dans slot 1 ou 2
            if int(next_slot) == 1:
                await db.execute(
                    "UPDATE team_matches SET p1_team_json=?, status=CASE WHEN status='pending' THEN 'running' ELSE status END WHERE id=? AND tournament_id=?",
                    (json.dumps(winner_team), next_id, tournament_id)
                )
            else:
                await db.execute(
                    "UPDATE team_matches SET p2_team_json=?, status=CASE WHEN status='pending' THEN 'running' ELSE status END WHERE id=? AND tournament_id=?",
                    (json.dumps(winner_team), next_id, tournament_id)
                )
        await db.commit()
        return next_id


# ----------------- Bracket builder Team vs Team -----------------

def _build_team_bracket(team_count: int, best_of: int = 1) -> List[dict]:
    """
    Construit la structure des matches (sans équipes encore), rounds & pos_in_round + wiring next_slot.
    On remplit p1_team/p2_team après en injectant les équipes initiales.
    """
    # nombre total de rounds pour N équipes (power-of-two ceiling)
    pow2 = 1
    while pow2 < team_count:
        pow2 *= 2
    rounds = int(math.log2(pow2))

    matches: List[dict] = []
    match_seq_per_round = []
    # construire tous les rounds
    next_id_counter = 1  # provisoire (sera remplacé par les IDs SQL après insert)
    for r in range(1, rounds + 1):
        num_matches = pow2 // (2 ** r)
        ids_for_round = []
        for i in range(num_matches):
            matches.append({
                "_tmp_id": next_id_counter,  # provisoire
                "round": r,
                "pos_in_round": i + 1,
                "p1_team": None,
                "p2_team": None,
                "best_of": best_of,
                "status": "pending" if r > 1 else "running",  # R1 en running, le reste pending
                "next_match_id": None,
                "next_slot": None,
            })
            ids_for_round.append(next_id_counter)
            next_id_counter += 1
        match_seq_per_round.append(ids_for_round)

    # wiring next ids: chaque match de round r va vers un match de round r+1
    for r in range(0, rounds - 1):
        cur_ids = match_seq_per_round[r]
        nxt_ids = match_seq_per_round[r + 1]
        for i, cur in enumerate(cur_ids):
            target = nxt_ids[i // 2]
            slot = 1 if (i % 2 == 0) else 2
            # on stocke provisoirement (_tmp_next_id, next_slot); on fixera après insertion SQL
            for m in matches:
                if m["_tmp_id"] == cur:
                    m["next_slot"] = slot
                    m["next_match_id"] = target  # _tmp id
                    break

    return matches

def _inject_round1_teams(matches: List[dict], teams: List[List[int]]):
    """Injecte les équipes réelles dans les matches du Round 1; si pow2 > len(teams), byes (None)."""
    # Récupérer les matches du round 1, ordonnés
    r1 = [m for m in matches if m["round"] == 1]
    r1.sort(key=lambda x: x["pos_in_round"])
    # Place Team1 vs Team2, Team3 vs Team4, etc.
    # Si le nombre n'est pas power-of-two, on met des byes (None) en face.
    t = teams[:]
    # Compléter à power-of-two avec None
    pow2 = 1
    while pow2 < len(t):
        pow2 *= 2
    while len(t) < pow2:
        t.append(None)

    it = iter(t)
    for m in r1:
        a = next(it, None)
        b = next(it, None)
        m["p1_team"] = a
        m["p2_team"] = b
        # si bye -> gagnant auto déterminable (on laisse la logique de report le gérer au premier score report)
    return matches

def _resolve_next_ids(sql_ids_in_order: List[int], built: List[dict]) -> List[dict]:
    """
    Remplace les next_match_id temporaires par les vrais SQL ids.
    On suppose `sql_ids_in_order` correspond à l'ordre (round, pos_in_round) trié.
    """
    # construire l'ordre (round, pos) des matches construits
    built_sorted = sorted(built, key=lambda m: (m["round"], m["pos_in_round"]))
    # map _tmp_id -> final_id
    tmp_to_final = {b["_tmp_id"]: sql_ids_in_order[i] for i, b in enumerate(built_sorted)}
    resolved = []
    for b in built_sorted:
        nb = dict(b)
        tmp_next = nb.get("next_match_id")
        nb["next_match_id"] = tmp_to_final.get(tmp_next) if tmp_next else None
        nb.pop("_tmp_id", None)
        resolved.append(nb)
    return resolved


# ----------------- Permissions -----------------

def is_admin_or_owner(bot: commands.Bot, inter: discord.Interaction) -> bool:
    s = bot.settings
    if s.OWNER_ID and inter.user.id == s.OWNER_ID:
        return True
    m = inter.guild and inter.guild.get_member(inter.user.id)
    return bool(m and (m.guild_permissions.administrator or m.guild_permissions.manage_guild))


# ----------------- COG -----------------

class TeamTournamentCog(commands.Cog):
    """Tournoi Single Elimination : Team vs Team (utilise la dernière config d'équipes)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="tt", description="Tournoi Team vs Team")

    # ------- CREATE -------
    @group.command(name="create", description="Créer un tournoi Team vs Team (préparation).")
    @app_commands.describe(name="Nom du tournoi (ex: Clash #1)")
    async def create(self, inter: discord.Interaction, name: str):
        await inter.response.defer(ephemeral=True, thinking=True)
        guild = inter.guild
        if not guild:
            await inter.followup.send("❌ À utiliser en serveur.", ephemeral=True); return
        active = await tt_get_active(self.bot.settings.DB_PATH, guild.id)
        if active:
            await inter.followup.send(f"⚠️ Un tournoi est déjà actif: **{active['name']}** (état: {active['state']}).", ephemeral=True); return
        tid = await tt_create(self.bot.settings.DB_PATH, guild.id, name, inter.user.id)
        await inter.followup.send(f"✅ Tournoi **{name}** créé (id: `{tid}`)\n→ Lance `/tt start` pour générer le bracket à partir des **dernières équipes**.", ephemeral=True)

    # ------- START -------
    @group.command(name="start", description="Démarrer le bracket Team vs Team en utilisant les DERNIÈRES équipes.")
    @app_commands.describe(best_of="Nombre de manches (best of). Par défaut 1.")
    async def start(self, inter: discord.Interaction, best_of: int = 1):
        await inter.response.defer(ephemeral=True, thinking=True)
        if not is_admin_or_owner(self.bot, inter):
            await inter.followup.send("⛔ Réservé aux admins/owner.", ephemeral=True); return
        guild = inter.guild
        if not guild:
            await inter.followup.send("❌ À utiliser en serveur.", ephemeral=True); return

        t = await tt_get_active(self.bot.settings.DB_PATH, guild.id)
        if not t or t["state"] != "setup":
            await inter.followup.send("❌ Aucun tournoi Team vs Team en préparation.", ephemeral=True); return

        # Récupère les DERNIÈRES équipes
        snap = await get_team_last(self.bot.settings.DB_PATH, guild.id)
        if not snap or not snap.get("teams"):
            await inter.followup.send("ℹ️ Aucune **dernière configuration d’équipes** trouvée. Utilise `/team` ou `/teamroll` d’abord.", ephemeral=True); return

        teams: List[List[int]] = [[int(x) for x in t] for t in snap["teams"] if t]
        if len(teams) < 2:
            await inter.followup.send("❌ Il faut au moins 2 équipes.", ephemeral=True); return

        # Construit la structure de bracket
        built = _build_team_bracket(len(teams), best_of=best_of)
        _inject_round1_teams(built, teams)

        # On insère sans next ids, on récupère l'ordre SQL, puis on met à jour les next ids
        await tm_clear(self.bot.settings.DB_PATH, t["id"])
        await tm_create_many(self.bot.settings.DB_PATH, t["id"], built)
        # Récupérer l'ordre inséré
        created = await tm_list(self.bot.settings.DB_PATH, t["id"])
        created_sorted = sorted(created, key=lambda r: (r["round"], r["pos_in_round"]))
        sql_ids = [row["id"] for row in created_sorted]

        # Fixer les next ids
        resolved = _resolve_next_ids(sql_ids, built)
        triples = []
        for row, nb in zip(created_sorted, resolved):
            if nb.get("next_match_id"):
                triples.append((row["id"], nb["next_match_id"], nb.get("next_slot") or None))
        if triples:
            await tm_update_next_links(self.bot.settings.DB_PATH, t["id"], triples)

        await tt_set_state(self.bot.settings.DB_PATH, t["id"], "running")
        await inter.followup.send("✅ Tournoi Team vs Team démarré ! Utilisez `/tt view` pour voir le bracket.", ephemeral=True)
        await self._post_bracket(inter, t["id"], title=f"🏆 {t['name']} — Round 1 (Teams)")

    # ------- REPORT -------
    @group.command(name="report", description="Reporter le résultat d'un match (Team vs Team).")
    @app_commands.describe(match_id="ID du match", winner_slot="1 = équipe à gauche, 2 = équipe à droite", p1_score="Score équipe 1", p2_score="Score équipe 2")
    async def report(self, inter: discord.Interaction, match_id: int, winner_slot: int, p1_score: int, p2_score: int):
        await inter.response.defer(ephemeral=True, thinking=True)
        if not is_admin_or_owner(self.bot, inter):
            await inter.followup.send("⛔ Réservé aux admins/owner pour l’instant.", ephemeral=True); return

        guild = inter.guild
        t = guild and await tt_get_active(self.bot.settings.DB_PATH, guild.id)
        if not t or t["state"] != "running":
            await inter.followup.send("❌ Pas de tournoi Team vs Team en cours.", ephemeral=True); return

        # On récupère le match pour savoir quelles équipes sont en slot 1 / 2
        allm = await tm_list(self.bot.settings.DB_PATH, t["id"])
        mm = next((m for m in allm if m["id"] == match_id), None)
        if not mm:
            await inter.followup.send("❌ Match introuvable.", ephemeral=True); return

        p1_team = mm.get("p1_team_json") or []
        p2_team = mm.get("p2_team_json") or []
        if winner_slot == 1 and not p1_team:
            await inter.followup.send("❌ Le slot 1 est vide (bye).", ephemeral=True); return
        if winner_slot == 2 and not p2_team:
            await inter.followup.send("❌ Le slot 2 est vide (bye).", ephemeral=True); return

        winner = p1_team if winner_slot == 1 else p2_team
        next_id = await tm_set_result(self.bot.settings.DB_PATH, t["id"], match_id, winner, p1_score, p2_score)
        await inter.followup.send("✅ Résultat enregistré.", ephemeral=True)
        await self._post_bracket(inter, t["id"], title="🔄 Bracket Teams mis à jour")
        if next_id:
            await inter.channel.send(f"➡️ L'équipe gagnante avance au match `{next_id}`.")

    # ------- VIEW -------
    @group.command(name="view", description="Afficher le bracket / rounds (Team vs Team).")
    async def view(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        guild = inter.guild
        t = guild and await tt_get_active(self.bot.settings.DB_PATH, guild.id)
        if not t:
            await inter.followup.send("❌ Aucun tournoi Team vs Team actif.", ephemeral=True); return
        await inter.followup.send("✅ Bracket envoyé dans le salon.", ephemeral=True)
        await self._post_bracket(inter, t["id"], title=f"🏆 {t['name']} — Bracket (Teams)")

    # ------- CANCEL -------
    @group.command(name="cancel", description="Annuler le tournoi Team vs Team actif.")
    async def cancel(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        if not is_admin_or_owner(self.bot, inter):
            await inter.followup.send("⛔ Réservé aux admins/owner.", ephemeral=True); return
        guild = inter.guild
        t = guild and await tt_get_active(self.bot.settings.DB_PATH, guild.id)
        if not t:
            await inter.followup.send("❌ Aucun tournoi Team vs Team actif.", ephemeral=True); return
        await tt_set_state(self.bot.settings.DB_PATH, t["id"], "cancelled")
        await inter.followup.send("🛑 Tournoi Teams annulé.", ephemeral=True)

    # ------- Helpers rendu -------
    async def _post_bracket(self, inter: discord.Interaction, tournament_id: int, title: str):
        matches = await tm_list(self.bot.settings.DB_PATH, tournament_id)
        if not matches:
            await inter.channel.send("Aucun match (Teams).")
            return
        # group by round
        rounds = {}
        for m in matches:
            rounds.setdefault(m["round"], []).append(m)
        for r in rounds.values():
            r.sort(key=lambda x: x["pos_in_round"])

        guild = inter.guild
        emb = discord.Embed(title=title, color=discord.Color.gold())
        for rnd in sorted(rounds.keys()):
            lines = []
            for m in rounds[rnd]:
                def fmt_team(user_ids: List[int]) -> str:
                    if not user_ids:
                        return "—"
                    names = []
                    for uid in user_ids:
                        mem = guild.get_member(int(uid)) if guild else None
                        names.append(mem.display_name if mem else f"(id:{uid})")
                    return ", ".join(names)

                p1 = fmt_team(m["p1_team_json"])
                p2 = fmt_team(m["p2_team_json"])
                status = m["status"]
                score = f" ({m['p1_score']}–{m['p2_score']})" if status == "done" else ""
                w = ""
                if m.get("winner_team_json"):
                    w = " → **" + fmt_team(m["winner_team_json"]) + "**"
                lines.append(f"`#{m['id']}` {p1}  vs  {p2}  [{status}]{score}{w}")
            emb.add_field(name=f"Round {rnd}", value=("\n".join(lines) if lines else "—"), inline=False)

        await inter.channel.send(embed=emb)


async def setup(bot: commands.Bot):
    await bot.add_cog(TeamTournamentCog(bot))
