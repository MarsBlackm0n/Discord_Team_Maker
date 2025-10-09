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
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------- USE LAST TEAM SNAPSHOT -------
    @app_commands.command(
        name="tournament_use_last",
        description="Ajouter (ou créer puis ajouter) les joueurs du dernier /team ou /teamroll au tournoi actif."
    )
    @app_commands.describe(
        name="Nom du tournoi (créé si aucun en cours).",
        dry_run="Aperçu sans écrire en base (False par défaut)."
    )
    async def tournament_use_last(self, inter: discord.Interaction, name: str = "Tournoi", dry_run: bool = False):
        await inter.response.defer(ephemeral=True, thinking=True)

        guild = inter.guild
        if not guild:
            await inter.followup.send("❌ À utiliser en serveur.", ephemeral=True)
            return

        # 1) Charger le snapshot des dernières équipes
        snap = await get_team_last(self.bot.settings.DB_PATH, guild.id)
        if not snap or not snap.get("teams"):
            await inter.followup.send("ℹ️ Aucune **dernière configuration d’équipes** trouvée. Utilise `/team` ou `/teamroll` d’abord.", ephemeral=True)
            return

        teams: list[list[int]] = [[int(x) for x in t] for t in snap.get("teams", [])]
        mode: str = str(snap.get("mode", "—"))
        team_count: int = int(snap.get("team_count", len(teams)))

        # 2) Trouver (ou créer) le tournoi actif
        t = await get_active_tournament(self.bot.settings.DB_PATH, guild.id)

        # En dry-run, on ne crée pas de tournoi s’il n’existe pas : on simule
        created_now = False
        if not t and not dry_run:
            tid = await create_tournament(self.bot.settings.DB_PATH, guild.id, name, inter.user.id)
            t = await get_active_tournament(self.bot.settings.DB_PATH, guild.id)
            created_now = True

        tournament_id = int(t["id"]) if t else None

        # 3) Participants déjà présents pour éviter les doublons
        existing_ids = set()
        if tournament_id is not None:
            existing = await list_participants(self.bot.settings.DB_PATH, tournament_id)
            existing_ids = {int(p["user_id"]) for p in existing}
            start_seed = 1 + len(existing_ids)
        else:
            # Pas de tournoi actif (dry-run) : on simule une base vide
            start_seed = 1

        # 4) Construire l’ordre d’inscription (par blocs d’équipe ; tri interne rating décroissant)
        ratings_cache: dict[int, float] = {}

        async def _rating(uid: int) -> float:
            if uid not in ratings_cache:
                r = await get_rating(self.bot.settings.DB_PATH, uid)
                ratings_cache[uid] = float(r) if r is not None else 1000.0
            return ratings_cache[uid]

        for team in teams:
            for uid in team:
                await _rating(uid)

        ordered: list[int] = []
        for team in teams:
            ordered.extend(sorted(team, key=lambda u: ratings_cache.get(u, 1000.0), reverse=True))

        # 5) Ajouter (ou simuler)
        planned_rows = []
        added = 0
        seed = start_seed
        for uid in ordered:
            if uid in existing_ids:
                planned_rows.append((seed, uid, ratings_cache.get(uid, 1000.0), True))
                # seed non incrémenté car on ne crée pas une nouvelle entrée
                continue

            planned_rows.append((seed, uid, ratings_cache.get(uid, 1000.0), False))
            if not dry_run and tournament_id is not None:
                await add_participant(self.bot.settings.DB_PATH, tournament_id, uid, seed, float(ratings_cache[uid]))
            added += 1
            seed += 1

        # 6) Rendu UX (embed)
        def _name(uid: int) -> str:
            m = guild.get_member(uid)
            return m.display_name if m else f"(id:{uid})"

        if dry_run:
            title_prefix = "🧪 APERÇU (dry-run)"
            footer_note = "Aucune écriture effectuée. Relance sans `dry_run` pour appliquer."
            tour_label = (t["name"] if t else f"(sera créé : {name})")
            tour_id_txt = (f"(id: `{tournament_id}`)" if tournament_id is not None else "(pas encore créé)")
        else:
            title_prefix = "👥 Import effectué"
            footer_note = "—"
            tour_label = t["name"] if t else name
            tour_id_txt = f"(id: `{tournament_id}`)" if tournament_id is not None else ""

        title = f"{title_prefix} — dernière config (mode: {mode}, équipes: {team_count})"

        desc_lines = []
        for s, uid, r, is_dup in planned_rows[:60]:
            dup = " *(déjà inscrit)*" if is_dup else ""
            desc_lines.append(f"{s:>2}. {_name(uid)} — **{int(r)}**{dup}")

        embed = discord.Embed(title=title, color=(discord.Color.orange() if dry_run else discord.Color.green()))
        embed.description = "\n".join(desc_lines) if desc_lines else "_Aucun joueur._"
        embed.add_field(name="Tournoi", value=f"{tour_label} {tour_id_txt}".strip(), inline=True)
        embed.add_field(name=("Ajouts possibles" if dry_run else "Ajouts effectués"), value=str(added), inline=True)
        if created_now and not dry_run:
            embed.add_field(name="Info", value="Un nouveau tournoi a été créé automatiquement.", inline=False)
        if dry_run:
            embed.set_footer(text=footer_note)
        elif len(planned_rows) > 60:
            embed.set_footer(text="Aperçu limité à 60 lignes.")

        await inter.followup.send(embed=embed, ephemeral=True)

    """Gestion d'un tournoi Single Elimination."""
    group = app_commands.Group(name="tournament", description="Gestion de tournoi")

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

        # Évite d'écraser les seeds existants
        existing = await list_participants(self.bot.settings.DB_PATH, t["id"])
        next_seed = 1 + len(existing)

        for i, (m, r) in enumerate(pairs, start=0):
            await add_participant(self.bot.settings.DB_PATH, t["id"], m.id, next_seed + i, r)

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
        raw_matches = build_bracket_matches(user_ids_by_seed, best_of=best_of)

        await clear_bracket(self.bot.settings.DB_PATH, t["id"])
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

        created = await list_matches(self.bot.settings.DB_PATH, t["id"])
        created.sort(key=lambda r: (r["round"], r["pos_in_round"]))  # <— ordre déterministe
        sql_ids = [row["id"] for row in created]
        resolved = resolve_next_ids(sql_ids, raw_matches)

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

        await set_tournament_state(self.bot.settings.DB_PATH, t["id"], "running", started=True)

        await inter.followup.send("✅ Tournoi démarré ! Utilisez `/tournament view` pour voir le bracket.", ephemeral=True)
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
