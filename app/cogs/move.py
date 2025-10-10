# app/cogs/move.py
from __future__ import annotations
from typing import List
import discord
from discord import app_commands
from discord.ext import commands

from ..db import get_team_last
from ..voice import create_and_move_voice


class MoveCog(commands.Cog):
    """Déplacer les joueurs selon la dernière 'last team' enregistrée (team / teamroll / arena)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="move",
        description="Créer (ou réutiliser) les salons Team 1..K et déplacer les joueurs d'après la dernière configuration d'équipes."
    )
    @app_commands.describe(
        channel_ttl="Durée de vie des salons (minutes, défaut 90)",
        reuse_existing="Réutiliser des salons 'Team 1', 'Team 2' existants si présents (si ton voice.py le gère)"
    )
    async def move(self, inter: discord.Interaction, channel_ttl: int = 90, reuse_existing: bool = True):
        await inter.response.defer(thinking=True)
        guild = inter.guild
        if not guild:
            await inter.followup.send("❌ À utiliser en serveur.", ephemeral=True)
            return

        snap = await get_team_last(self.bot.settings.DB_PATH, guild.id)
        if not snap or not snap.get("teams"):
            await inter.followup.send("ℹ️ Aucune config enregistrée. Lance d'abord `/team`, `/teamroll` ou un round d'`/arena`.", ephemeral=True)
            return

        # Recompose les objets Member à partir des IDs
        teams: List[List[discord.Member]] = []
        for team_ids in snap.get("teams", []):
            members_obj = []
            for uid in team_ids:
                m = guild.get_member(int(uid))
                if m and not m.bot:
                    members_obj.append(m)
            teams.append(members_obj)

        sizes = snap.get("sizes") or [len(t) for t in teams]
        try:
            await create_and_move_voice(
                inter,
                teams,
                sizes,
                ttl_minutes=max(int(channel_ttl), 1),
            )
            await inter.followup.send("🚀 Salons créés/réutilisés et joueurs déplacés.", ephemeral=True)
        except discord.Forbidden:
            await inter.followup.send("⚠️ Permissions manquantes (Manage Channels / Move Members).", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MoveCog(bot))
