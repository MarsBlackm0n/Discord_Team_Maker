# app/cogs/tournament.py
from __future__ import annotations
from typing import List, Optional
import discord
from discord import app_commands
from discord.ext import commands
from ..db import (
    get_rating, create_tournament, get_active_tournament, set_tournament_state,
    add_participant, list_participants, clear_bracket, create_matches, list_matches,
    report_match_result, get_team_last
)
from ..tournament_logic import build_bracket_matches, resolve_next_ids
from ..team_logic import parse_mentions

def is_admin_or_owner(bot: commands.Bot, inter: discord.Interaction) -> bool:
    s = bot.settings
    if s.OWNER_ID and inter.user.id == s.OWNER_ID:
        return True
    m = inter.guild and inter.guild.get_member(inter.user.id)
    return bool(m and (m.guild_permissions.administrator or m.guild_permissions.manage_guild))

class TournamentCog(commands.Cog):

    # ------- USE LAST TEAM SNAPSHOT -------

    @app_commands.command(name="tournament_use_last", description="Créer/ajouter les joueurs du dernier /team ou /teamroll au tournoi courant.")
    @app_commands.describe(name="Nom du tournoi (créé si aucun en cours)")
    async def tournament_use_last(self, inter: discord.Interaction, name: str = "Tournoi"):
        await inter.response.defer(thinking=True)
        snap = await get_team_last(self.bot.settings.DB_PATH, inter.guild.id)
        if not snap:
            await inter.followup.send("ℹ️ Aucune config de team enregistrée pour ce serveur.", ephemeral=True)
            return

        # Trouver ou créer le tournoi
        from ..db import get_active_tournament
        t = await get_active_tournament(self.bot.settings.DB_PATH, inter.guild.id)
        if not t:
            tid = await create_tournament(self.bot.settings.DB_PATH, inter.guild.id, name, inter.user.id)
        else:
            tid = int(t["id"])

        # Ajouter tous les joueurs présents dans la dernière config (seed simple = ordre d’apparition)
        seed = 1
        uids = []
        for team_ids in snap.get("teams", []):
            for uid in team_ids:
                if uid not in uids:
                    uids.append(int(uid))

        for uid in uids:
            r = await get_rating(self.bot.settings.DB_PATH, uid)
            await add_participant(self.bot.settings.DB_PATH, tid, uid, seed, float(r or 1000.0))
            seed += 1

        await inter.followup.send(f"✅ {len(uids)} joueurs ajoutés au tournoi **#{tid}** (source: dernière config).", ephemeral=True)

    """Gestion d'un tournoi Single Elimination."""

    group = app_commands.Group(name="tournament", description="Gestion de tournoi")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------- CREATE -------
    @group.command(name="create", description="Créer un tournoi (préparation).")
    @app_commands.describe(name="Nom du tournoi (ex: Clash #1)")
    async def create(self, inter: discord.Interaction, name: str):
        await inter.response.defer(ephemeral=True, thinking=True)
        guild = inter.guild
        if not guild:
            await inter.followup.send("❌ À utiliser en serveur.", ephemeral=True); return
        active = await get_active_tournament(self.bot.settings.DB_PATH, guild.id)
        if active:
            await inter.followup.send(f"⚠️ Un tournoi est déjà actif: **{active['name']}** (état: {active['state']}).", ephemeral=True); return
        tid = await create_tournament(self.bot.settings.DB_PATH, guild.id, name, inter.user.id)
        await inter.followup.send(f"✅ Tournoi **{name}** créé (id: `{tid}`) — ajoutez des participants avec `/tournament add` puis `/tournament start`.", ephemeral=True)

    # ------- ADD -------
    @group.command(name="add", description="Ajouter des participants (mentions ou salon vocal).")
    @app_commands.describe(members="Liste de @mentions (si vide: prend les membres du vocal de l'auteur)")
    async def add(self, inter: discord.Interaction, members: str = ""):
        await inter.response.defer(ephemeral=True, thinking=True)
        guild = inter.guild
        if not guild:
            await inter.followup.send("❌ À utiliser en serveur.", ephemeral=True); return
        t = await get_active_tournament(self.bot.settings.DB_PATH, guild.id)
        if not t or t["state"] != "setup":
            await inter.followup.send("❌ Aucun tournoi en préparation. Lance `/tournament create`.", ephemeral=True); return

        # Collecte
        selected: List[discord.Member] = []
        if members.strip():
            selected = parse_mentions(guild, members)
        else:
            me = guild.get_member(inter.user.id)
            if me and me.voice and me.voice.channel:
                selected = [m for m in me.voice.channel.members if not m.bot]
            else:
                await inter.followup.send("❌ Pas de mentions et tu n'es pas en vocal.", ephemeral=True); return

        if not selected:
            await inter.followup.send("❌ Aucun joueur trouvé.", ephemeral=True); return

        # Seed par rating décroissant
        pairs = []
        for m in selected:
            r = await get_rating(self.bot.settings.DB_PATH, m.id)
            pairs.append((m, int(r) if r is not None else 1000))
        pairs.sort(key=lambda x: x[1], reverse=True)

        for seed, (m, r) in enumerate(pairs, start=1):
            await add_participant(self.bot.settings.DB_PATH, t["id"], m.id, seed, r)

        names = ", ".join(f"{m.display_name}" for m, _ in pairs)
        await inter.followup.send(f"✅ Ajouté {len(pairs)} joueurs: {names}", ephemeral=True)

    # ------- START -------
    @group.command(name="start", description="Démarrer le bracket (single elimination).")
    @app_commands.describe(best_of="Nombre de manches à jouer (best of). Par défaut 1.")
    async def start(self, inter: discord.Interaction, best_of: int = 1):
        await inter.response.defer(ephemeral=True, thinking=True)
        if not is_admin_or_owner(self.bot, inter):
            await inter.followup.send("⛔ Réservé aux admins/owner.", ephemeral=True); return

        guild = inter.guild
        if not guild:
            await inter.followup.send("❌ À utiliser en serveur.", ephemeral=True); return
        t = await get_active_tournament(self.bot.settings.DB_PATH, guild.id)
        if not t or t["state"] != "setup":
            await inter.followup.send("❌ Aucun tournoi en préparation.", ephemeral=True); return

        part = await list_participants(self.bot.settings.DB_PATH, t["id"])
        if len(part) < 2:
            await inter.followup.send("❌ Il faut au moins 2 joueurs.", ephemeral=True); return

        user_ids_by_seed = [int(p["user_id"]) for p in part]  # déjà triés par seed ASC
        # 1er passage : matches positionnels
        raw_matches = build_bracket_matches(user_ids_by_seed, best_of=best_of)

        # On insère d'abord "placeholder" pour récupérer les IDs SQL
        await clear_bracket(self.bot.settings.DB_PATH, t["id"])
        # insertion dummy pour récupérer un nombre d'IDs
        # Astuce : on insère avec next_match_id=None puis on met à jour.
        for m in raw_matches:
            m["next_match_id"] = None

        await create_matches(self.bot.settings.DB_PATH, t["id"], [
            {
                "round": m["round"],
                "pos_in_round": m["pos_in_round"],
                "p1_user_id": m["p1_user_id"],
                "p2_user_id": m["p2_user_id"],
                "best_of": m["best_of"],
                "status": m["status"],
                "next_match_id": None,
                "next_slot": m["next_slot"]
            } for m in raw_matches
        ])

        # Relire pour obtenir l'ordre & IDs SQL
        created = await list_matches(self.bot.settings.DB_PATH, t["id"])
        sql_ids = [row["id"] for row in created]  # même ordre d'insert
        # Résoudre les next_match_id à partir des positions
        resolved = resolve_next_ids(sql_ids, raw_matches)

        # Réécrire les next_match_id
        await clear_bracket(self.bot.settings.DB_PATH, t["id"])
        await create_matches(self.bot.settings.DB_PATH, t["id"], [
            {
                "round": m["round"],
                "pos_in_round": m["pos_in_round"],
                "p1_user_id": m["p1_user_id"],
                "p2_user_id": m["p2_user_id"],
                "best_of": m["best_of"],
                "status": m["status"],
                "next_match_id": m["next_match_id"],
                "next_slot": m["next_slot"]
            } for m in resolved
        ])

        # Passer en running
        await set_tournament_state(self.bot.settings.DB_PATH, t["id"], "running", started=True)

        await inter.followup.send("✅ Tournoi démarré ! Utilisez `/tournament view` pour voir le bracket.", ephemeral=True)
        # Affichage public du round 1
        await self._post_bracket(inter, t["id"], title=f"🏆 {t['name']} — Round 1")




    # ------- REPORT -------
    @group.command(name="report", description="Reporter le résultat d'un match.")
    @app_commands.describe(match_id="ID du match", winner="Vainqueur", p1_score="Score du joueur 1", p2_score="Score du joueur 2")
    async def report(self, inter: discord.Interaction, match_id: int, winner: discord.Member, p1_score: int, p2_score: int):
        await inter.response.defer(ephemeral=True, thinking=True)
        if not is_admin_or_owner(self.bot, inter):
            await inter.followup.send("⛔ Réservé aux admins/owner pour l’instant.", ephemeral=True); return

        guild = inter.guild
        t = guild and await get_active_tournament(self.bot.settings.DB_PATH, guild.id)
        if not t or t["state"] != "running":
            await inter.followup.send("❌ Pas de tournoi en cours.", ephemeral=True); return

        next_id = await report_match_result(self.bot.settings.DB_PATH, t["id"], match_id, winner.id, p1_score, p2_score)
        await inter.followup.send("✅ Résultat enregistré.", ephemeral=True)
        await self._post_bracket(inter, t["id"], title="🔄 Bracket mis à jour")
        if next_id:
            await inter.channel.send(f"➡️ Le vainqueur avance au match `{next_id}`.")

    # ------- VIEW -------
    @group.command(name="view", description="Afficher le bracket / les rounds.")
    async def view(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        guild = inter.guild
        t = guild and await get_active_tournament(self.bot.settings.DB_PATH, guild.id)
        if not t:
            await inter.followup.send("❌ Aucun tournoi actif.", ephemeral=True); return
        await inter.followup.send("✅ Bracket envoyé dans le salon.", ephemeral=True)
        await self._post_bracket(inter, t["id"], title=f"🏆 {t['name']} — Bracket")

    # ------- CANCEL -------
    @group.command(name="cancel", description="Annuler le tournoi actif.")
    async def cancel(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        if not is_admin_or_owner(self.bot, inter):
            await inter.followup.send("⛔ Réservé aux admins/owner.", ephemeral=True); return
        guild = inter.guild
        t = guild and await get_active_tournament(self.bot.settings.DB_PATH, guild.id)
        if not t:
            await inter.followup.send("❌ Aucun tournoi actif.", ephemeral=True); return
        await set_tournament_state(self.bot.settings.DB_PATH, t["id"], "cancelled")
        await inter.followup.send("🛑 Tournoi annulé.", ephemeral=True)

    # ------- helpers -------
    async def _post_bracket(self, inter: discord.Interaction, tournament_id: int, title: str):
        matches = await list_matches(self.bot.settings.DB_PATH, tournament_id)
        if not matches:
            await inter.channel.send("Aucun match.")
            return
        # Group by round
        rounds = {}
        for m in matches:
            rounds.setdefault(m["round"], []).append(m)
        # tri
        for r in rounds.values():
            r.sort(key=lambda x: x["pos_in_round"])

        emb = discord.Embed(title=title, color=discord.Color.gold())
        for rnd in sorted(rounds.keys()):
            lines = []
            for m in rounds[rnd]:
                p1 = f"<@{m['p1_user_id']}>" if m["p1_user_id"] else "—"
                p2 = f"<@{m['p2_user_id']}>" if m["p2_user_id"] else "—"
                status = m["status"]
                score = f" ({m['p1_score']}–{m['p2_score']})" if status == "done" else ""
                w = f" → **<@{m['winner_user_id']}>**" if m.get("winner_user_id") else ""
                lines.append(f"`#{m['id']}` {p1} vs {p2} [{status}]{score}{w}")
            emb.add_field(name=f"Round {rnd}", value="\n".join(lines), inline=False)

        await inter.channel.send(embed=emb)


async def setup(bot: commands.Bot):
    await bot.add_cog(TournamentCog(bot))
