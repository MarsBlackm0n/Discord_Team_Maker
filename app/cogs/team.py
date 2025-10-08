# app/cogs/team.py
from typing import List, Dict, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from ..db import get_rating, set_rating
from ..riot import fetch_lol_rank_info
from ..team_logic import (
    parse_mentions, parse_sizes, group_by_with_constraints,
    parse_avoid_pairs, split_random, balance_k_teams_with_constraints, fmt_team
)
from ..voice import create_and_move_voice

class TeamCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def ensure_ratings_for_members(self, members: List[discord.Member], auto_import_riot: bool = True) -> tuple[Dict[int,float], List[discord.Member], List[discord.Member]]:
        ratings: Dict[int, float] = {}
        used_default, imported = [], []

        # 1) DB d'abord
        for m in members:
            r = await get_rating(self.bot.settings.DB_PATH, m.id)
            if r is not None: ratings[m.id] = r

        # 2) Riot si demandé et possible + si lien est connu
        if auto_import_riot and self.bot.settings.RIOT_API_KEY:
            from ..db import get_linked_lol, set_lol_rank
            for m in members:
                if m.id in ratings: continue
                link = await get_linked_lol(self.bot.settings.DB_PATH, m.id)
                if not link: continue
                summoner, region_code = link
                info = await fetch_lol_rank_info(self.bot.settings.RIOT_API_KEY, region_code, summoner)
                if info:
                    tier, division, lp, rr = info
                    ratings[m.id] = rr
                    await set_rating(self.bot.settings.DB_PATH, m.id, rr)
                    await set_lol_rank(self.bot.settings.DB_PATH, m.id, source="riot", tier=tier, division=division, lp=lp)
                    imported.append(m)

        # 3) défaut 1000
        for m in members:
            if m.id not in ratings:
                ratings[m.id] = 1000.0
                used_default.append(m)

        return ratings, used_default, imported

    @app_commands.command(name="team", description="Créer des équipes (équilibrées ou aléatoires) avec options & fallback rating.")
    @app_commands.describe(
        mode="balanced ou random (défaut: balanced)",
        team_count="Nombre d'équipes (2–6, défaut 2)",
        sizes='Tailles fixées, ex: "3/3/2" (somme = nb joueurs)',
        with_groups='Groupes ensemble, ex: "@A @B | @C @D"',
        avoid_pairs='Paires à séparer, ex: "@A @B ; @C @D"',
        members="(Optionnel) liste de @mentions si pas de vocal",
        create_voice="Créer des salons vocaux Team 1..K et déplacer les joueurs",
        channel_ttl="Durée de vie des salons vocaux (minutes, défaut 90)",
        auto_import_riot="Importer via Riot pour les joueurs liés si possible (défaut: true)",
    )
    async def team(
        self,
        inter: discord.Interaction,
        mode: str = "balanced",
        team_count: int = 2,
        sizes: str = "",
        with_groups: str = "",
        avoid_pairs: str = "",
        members: str = "",
        create_voice: bool = False,
        channel_ttl: int = 90,
        auto_import_riot: bool = True
    ):
        await inter.response.defer(thinking=True)
        if team_count < 2 or team_count > 6:
            await inter.followup.send("❌ team_count doit être entre 2 et 6."); return

        guild = inter.guild
        author = guild and guild.get_member(inter.user.id)

        selected: List[discord.Member] = []
        if members:
            selected = parse_mentions(guild, members)
        else:
            if author and author.voice and author.voice.channel:
                selected = [m for m in author.voice.channel.members if not m.bot]
            else:
                await inter.followup.send("❌ Pas de liste fournie et tu n'es pas en vocal."); return

        if len(selected) < team_count:
            await inter.followup.send(f"❌ Pas assez de joueurs pour {team_count} équipes."); return

        ratings, used_default, imported_from_riot = await self.ensure_ratings_for_members(selected, auto_import_riot)

        sizes_list = parse_sizes(sizes, len(selected), team_count)
        with_groups_list = group_by_with_constraints(guild, selected, with_groups) if with_groups else [[m] for m in selected]
        avoid_pairs_set = parse_avoid_pairs(guild, avoid_pairs)

        if mode.lower() == "random":
            teams = split_random(selected, team_count, sizes_list); violations = []
        else:
            teams, violations = balance_k_teams_with_constraints(selected, ratings, team_count, sizes_list, with_groups_list, avoid_pairs_set)

        embed = discord.Embed(title="🎲 Team Builder", color=discord.Color.blurple())
        for idx, team_list in enumerate(teams):
            embed.add_field(name=f"Team {idx+1}", value=fmt_team(team_list, ratings, idx), inline=True)
        totals = [int(sum(ratings[m.id] for m in t)) for t in teams]
        spread = (max(totals) - min(totals)) if totals else 0
        footer = f"Mode: {'Équilibré' if mode.lower()!='random' else 'Aléatoire'} • Δ total: {spread}"
        if violations: footer += f" • Contraintes violées: {len(violations)}"
        embed.set_footer(text=footer)
        await inter.followup.send(embed=embed)

        notes = []
        if imported_from_riot: notes.append("🏷️ Import Riot: " + ", ".join(m.display_name for m in imported_from_riot))
        if used_default:
            notes.append("⚠️ Rating par défaut (1000): " + ", ".join(m.display_name for m in used_default) + "\n→ `/setrank` ou `/setskill`, ou `/linklol`.")
        if notes:
            try: await inter.followup.send("\n".join(notes), ephemeral=True)
            except: pass

        if create_voice:
            try:
                await create_and_move_voice(inter, teams, sizes_list, ttl_minutes=max(channel_ttl, 1))
            except discord.Forbidden:
                await inter.followup.send("⚠️ Permissions manquantes (Manage Channels / Move Members).")

    @app_commands.command(name="disbandteams", description="Supprimer les salons vocaux d'équipe temporaires.")
    async def disbandteams(self, inter: discord.Interaction):
        from ..voice import TEMP_CHANNELS
        guild = inter.guild
        if not guild:
            await inter.response.send_message("❌ Guild inconnue.", ephemeral=True); return
        ids = TEMP_CHANNELS.pop(guild.id, [])
        count = 0
        for cid in ids:
            ch = guild.get_channel(cid)
            if ch:
                try: await ch.delete(reason="TeamBuilder manual cleanup"); count += 1
                except discord.Forbidden: pass
        await inter.response.send_message(f"🧹 Salons supprimés: {count}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(TeamCog(bot))
