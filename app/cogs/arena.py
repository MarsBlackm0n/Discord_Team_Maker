# app/cogs/arena.py
from __future__ import annotations
from typing import List, Dict, Tuple, Optional
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from ..team_logic import parse_mentions
from ..db import (
    get_team_last,
    arena_get_active, arena_create, arena_update_scores_and_advance,
    arena_get_by_id, arena_set_state
)

def is_admin_or_owner(bot: commands.Bot, inter: discord.Interaction) -> bool:
    s = bot.settings
    if s.OWNER_ID and inter.user.id == s.OWNER_ID:
        return True
    m = inter.guild and inter.guild.get_member(inter.user.id)
    return bool(m and (m.guild_permissions.administrator or m.guild_permissions.manage_guild))


# ---------- algo round-robin : n joueurs -> n-1 rounds de duos ----------
def round_robin_duos(user_ids: List[int]) -> List[List[List[int]]]:
    """
    Retourne une liste de rounds.
    Chaque round = liste de duos [ [u1,u2], [u3,u4], ... ].
    Algo "circle method" pour couvrir chaque paire exactement 1 fois.
    """
    ids = user_ids[:]
    if len(ids) % 2 != 0:
        raise ValueError("Nombre de joueurs doit être pair pour l'Arena 2v2.")
    n = len(ids)
    if n < 4:
        raise ValueError("Minimum 4 joueurs.")
    # méthode du cercle (rotation simple, un joueur "fixe")
    fixed = ids[-1]
    rot = ids[:-1]
    rounds: List[List[List[int]]] = []
    for _ in range(n - 1):
        line = rot + [fixed]
        pairs = []
        for i in range(0, n, 2):
            pairs.append([line[i], line[i+1]])
        rounds.append(pairs)
        # rotation classique
        rot = rot[-1:] + rot[:-1]
    return rounds

def points_for_rank(rank: int, team_count: int) -> int:
    # 1er = team_count, 2e = team_count-1, ... 8e = 1
    return max(1, team_count - rank + 1)


class ArenaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="arena", description="Tournoi LoL Arena (2v2, classement individuel)")

    # /arena start
    @group.command(name="start", description="Démarrer un tournoi Arena (duos qui tournent chaque round).")
    @app_commands.describe(
        rounds="(Optionnel) nombre de rounds. Si vide: n-1 (tout le monde avec tout le monde).",
        members="(Optionnel) liste de @mentions; sinon dernier /team, sinon ton vocal."
    )
    async def start(self, inter: discord.Interaction, rounds: Optional[int] = None, members: str = ""):
        await inter.response.defer(ephemeral=True, thinking=True)
        if not is_admin_or_owner(self.bot, inter):
            await inter.followup.send("⛔ Réservé aux admins/owner.", ephemeral=True); return

        guild = inter.guild
        if not guild:
            await inter.followup.send("❌ À utiliser sur un serveur.", ephemeral=True); return

        # Récup participants
        selected: List[discord.Member] = []
        if members.strip():
            selected = parse_mentions(guild, members)
        else:
            # dernier /team
            snap = await get_team_last(self.bot.settings.DB_PATH, guild.id)
            if snap and snap.get("teams"):
                lookup = {m.id: m for m in guild.members}
                ids = [int(uid) for team_ids in snap["teams"] for uid in team_ids]
                selected = [lookup[i] for i in ids if i in lookup and not lookup[i].bot]
            # sinon vocal de l'auteur
            if not selected:
                me = guild.get_member(inter.user.id)
                if me and me.voice and me.voice.channel:
                    selected = [m for m in me.voice.channel.members if not m.bot]

        if len(selected) < 4 or len(selected) % 2 != 0:
            await inter.followup.send("❌ Il faut un nombre **pair** de joueurs (min 4).", ephemeral=True); return
        if len(selected) > 16:
            await inter.followup.send("❌ Maximum 16 joueurs (8 duos).", ephemeral=True); return

        user_ids = [m.id for m in selected]
        schedule_full = round_robin_duos(user_ids)  # n-1 rounds
        full_rounds = len(schedule_full)

        if not rounds or rounds <= 0 or rounds > full_rounds:
            rounds = full_rounds  # par défaut n-1

        schedule = schedule_full[:rounds]
        arena_id = await arena_create(
            self.bot.settings.DB_PATH,
            guild.id, inter.user.id, rounds, user_ids, schedule
        )

        await inter.followup.send(
            f"✅ Tournoi Arena lancé (id `{arena_id}`) — **{len(selected)}** joueurs, **{rounds}** rounds.",
            ephemeral=True
        )
        await self._post_round_embed(inter.channel, selected, schedule, current_round=1)
        await self._post_scores_embed(inter.channel, user_ids, {})  # scores 0 au départ

    # /arena round
    @group.command(name="round", description="Afficher le round courant à jouer.")
    async def round(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        guild = inter.guild
        arena = guild and await arena_get_active(self.bot.settings.DB_PATH, guild.id)
        if not arena or arena["state"] != "running":
            await inter.followup.send("ℹ️ Aucun tournoi Arena en cours.", ephemeral=True); return

        ids = arena["participants"]
        lookup = {m.id: m for m in inter.guild.members}
        members = [lookup[i] for i in ids if i in lookup]
        await inter.followup.send("📣 Round courant affiché dans le salon.", ephemeral=True)
        await self._post_round_embed(inter.channel, members, arena["schedule"], current_round=arena["current_round"])

    # /arena status
    @group.command(name="status", description="Afficher le classement et l'état du tournoi Arena.")
    async def status(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        guild = inter.guild
        arena = guild and await arena_get_active(self.bot.settings.DB_PATH, guild.id)
        if not arena:
            await inter.followup.send("ℹ️ Aucun tournoi Arena actif.", ephemeral=True); return
        ids = arena["participants"]
        await inter.followup.send("📊 Statut posté.", ephemeral=True)
        await self._post_scores_embed(inter.channel, ids, arena["scores"],
                                      title_suffix=f"(Round {min(arena['current_round'], arena['rounds_total'])}/{arena['rounds_total']}, état: {arena['state']})")

    # /arena report
    @group.command(name="report", description="Reporter le résultat d'un round (ex: '@A @B | @C @D | ...' du 1er au dernier).")
    @app_commands.describe(
        placements="Duos du 1er au dernier, séparés par '|' (ex: '@A @B | @C @D | ...')."
    )
    async def report(self, inter: discord.Interaction, placements: str):
        await inter.response.defer(ephemeral=True, thinking=True)
        if not is_admin_or_owner(self.bot, inter):
            await inter.followup.send("⛔ Réservé aux admins/owner.", ephemeral=True); return

        guild = inter.guild
        arena = guild and await arena_get_active(self.bot.settings.DB_PATH, guild.id)
        if not arena or arena["state"] != "running":
            await inter.followup.send("ℹ️ Aucun tournoi Arena en cours.", ephemeral=True); return

        cur_round = arena["current_round"]
        schedule = arena["schedule"]
        if cur_round < 1 or cur_round > len(schedule):
            await inter.followup.send("❌ Plus de round à jouer.", ephemeral=True); return

        # Parse du string de placements
        chunks = [c.strip() for c in placements.split("|") if c.strip()]
        line_pairs: List[Tuple[int, int]] = []
        for ch in chunks:
            ms = parse_mentions(guild, ch)
            ms = [m for m in ms if not m.bot]
            if len(ms) != 2:
                await inter.followup.send(f"❌ Impossible de lire un duo dans: `{ch}`", ephemeral=True); return
            a, b = sorted([ms[0].id, ms[1].id])
            line_pairs.append((a, b))

        # Vérif : nombre et conformité des duos
        expected_pairs = schedule[cur_round - 1]
        team_count = len(expected_pairs)
        if len(line_pairs) != team_count:
            await inter.followup.send(f"❌ Il faut exactement **{team_count}** duos pour ce round.", ephemeral=True); return
        expected_set = {tuple(sorted(pair)) for pair in expected_pairs}
        given_set = set(line_pairs)
        if expected_set != given_set:
            await inter.followup.send("❌ Les duos saisis ne correspondent pas aux duos du round courant.", ephemeral=True); return

        # Points joueurs
        new_scores: Dict[int, int] = {}
        for rank, (u1, u2) in enumerate(line_pairs, start=1):
            pts = points_for_rank(rank, team_count)
            new_scores[u1] = new_scores.get(u1, 0) + pts
            new_scores[u2] = new_scores.get(u2, 0) + pts

        await arena_update_scores_and_advance(self.bot.settings.DB_PATH, arena["id"], new_scores)
        arena2 = await arena_get_by_id(self.bot.settings.DB_PATH, arena["id"])

        await inter.followup.send("✅ Résultat enregistré. Classement mis à jour.", ephemeral=True)
        await self._post_scores_embed(inter.channel, arena2["participants"], arena2["scores"])

        if arena2["state"] == "running":
            lookup = {m.id: m for m in inter.guild.members}
            members = [lookup[i] for i in arena2["participants"] if i in lookup]
            await self._post_round_embed(inter.channel, members, arena2["schedule"], current_round=arena2["current_round"])
        else:
            await self._post_podium_embed(inter.channel, arena2["participants"], arena2["scores"])

    # /arena stop
    @group.command(name="stop", description="Terminer le tournoi Arena en cours et afficher le podium.")
    async def stop(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        if not is_admin_or_owner(self.bot, inter):
            await inter.followup.send("⛔ Réservé aux admins/owner.", ephemeral=True); return

        guild = inter.guild
        arena = guild and await arena_get_active(self.bot.settings.DB_PATH, guild.id)
        if not arena:
            await inter.followup.send("ℹ️ Aucun tournoi Arena actif.", ephemeral=True); return

        await arena_set_state(self.bot.settings.DB_PATH, arena["id"], "finished")
        await inter.followup.send("🏁 Tournoi arrêté. Podium affiché dans le salon.", ephemeral=True)
        await self._post_podium_embed(inter.channel, arena["participants"], arena["scores"])

    # /arena cancel
    @group.command(name="cancel", description="Annuler (supprimer) le tournoi Arena en cours.")
    async def cancel(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        if not is_admin_or_owner(self.bot, inter):
            await inter.followup.send("⛔ Réservé aux admins/owner.", ephemeral=True); return
        guild = inter.guild
        arena = guild and await arena_get_active(self.bot.settings.DB_PATH, guild.id)
        if not arena:
            await inter.followup.send("ℹ️ Aucun tournoi Arena actif.", ephemeral=True); return
        await arena_set_state(self.bot.settings.DB_PATH, arena["id"], "cancelled")
        await inter.followup.send("🛑 Tournoi Arena annulé.", ephemeral=True)

    # ------- embeds -------
    async def _post_round_embed(self, channel: discord.abc.Messageable, members: List[discord.Member],
                                schedule: List[List[List[int]]], current_round: int):
        lookup = {m.id: m for m in members}
        pairs = schedule[current_round - 1]
        lines = []
        for i, (u1, u2) in enumerate(pairs, start=1):
            m1 = lookup.get(u1); m2 = lookup.get(u2)
            lines.append(f"**Duo {i}** — {m1.mention if m1 else f'<@{u1}>'} & {m2.mention if m2 else f'<@{u2}>'}")
        emb = discord.Embed(title=f"🧭 Arena — Round {current_round}", color=discord.Color.blurple())
        emb.description = "\n".join(lines) or "_(vide)_"
        emb.set_footer(text="Report: /arena report  « @A @B | @C @D | ... » (du 1er au dernier)")
        await channel.send(embed=emb)

    async def _post_scores_embed(self, channel: discord.abc.Messageable, participants: List[int],
                                 scores: Dict[str, int], title_suffix: str = ""):
        norm = {int(k): int(v) for k, v in (scores or {}).items()}
        rows = sorted([(uid, norm.get(uid, 0)) for uid in participants], key=lambda x: (-x[1], x[0]))
        emb = discord.Embed(title=f"📊 Arena — Classement {title_suffix}".strip(), color=discord.Color.gold())
        desc = []
        for rank, (uid, pts) in enumerate(rows, start=1):
            desc.append(f"**#{rank}** — <@{uid}> — **{pts}** pts")
        emb.description = "\n".join(desc) or "_(personne)_"
        await channel.send(embed=emb)

    async def _post_podium_embed(self, channel: discord.abc.Messageable, participants: List[int],
                                 scores: Dict[str, int]):
        norm = {int(k): int(v) for k, v in (scores or {}).items()}
        rows = sorted([(uid, norm.get(uid, 0)) for uid in participants], key=lambda x: (-x[1], x[0]))
        emb = discord.Embed(title="🏆 Arena — Podium", color=discord.Color.brand_green())
        medals = ["🥇", "🥈", "🥉"]
        for i in range(min(3, len(rows))):
            uid, pts = rows[i]
            emb.add_field(name=medals[i], value=f"<@{uid}> — **{pts}** pts", inline=False)
        await channel.send(embed=emb)


async def setup(bot: commands.Bot):
    await bot.add_cog(ArenaCog(bot))
