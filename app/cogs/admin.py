# app/cogs/admin.py
import os, sys, asyncio, subprocess
import discord
from discord import app_commands
from discord.ext import commands

class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _is_authorized(self, inter: discord.Interaction) -> bool:
        settings = self.bot.settings
        if settings.OWNER_ID and inter.user.id == settings.OWNER_ID:
            return True
        member = inter.guild and inter.guild.get_member(inter.user.id)
        return bool(member and (member.guild_permissions.administrator or member.guild_permissions.manage_guild))

    @app_commands.command(name="whoami", description="Affiche ton User ID.")
    async def whoami(self, inter: discord.Interaction):
        await inter.response.send_message(f"🪪 Ton User ID : `{inter.user.id}`", ephemeral=True)

    @app_commands.command(name="resync", description="Resynchroniser les commandes (owner/admin).")
    async def resync(self, inter: discord.Interaction):
        if not self._is_authorized(inter):
            await inter.response.send_message("⛔ Autorisation refusée.", ephemeral=True); return
        await inter.response.defer(ephemeral=True, thinking=True)
        if self.bot.settings.GUILD_ID:
            g = discord.Object(id=self.bot.settings.GUILD_ID)
            self.bot.tree.copy_global_to(guild=g)
            await self.bot.tree.sync(guild=g)
            await inter.followup.send("✅ Commands resynchronisées (guild).", ephemeral=True)
        else:
            await self.bot.tree.sync()
            await inter.followup.send("✅ Commands resynchronisées (global).", ephemeral=True)

    @app_commands.command(name="shutdown", description="Arrêter le bot (owner/admin).")
    async def shutdown(self, inter: discord.Interaction):
        if not self._is_authorized(inter):
            await inter.response.send_message("⛔ Autorisation refusée.", ephemeral=True); return
        await inter.response.send_message("🛑 Extinction…", ephemeral=True)
        await asyncio.sleep(0.2)
        await self.bot.close()

    @app_commands.command(name="restart", description="Redémarrer le bot (owner/admin).")
    async def restart(self, inter: discord.Interaction):
        if not self._is_authorized(inter):
            await inter.response.send_message("⛔ Autorisation refusée.", ephemeral=True); return
        await inter.response.send_message("🔄 Redémarrage…", ephemeral=True)
        await asyncio.sleep(0.2)
        if self.bot.settings.RESTART_MODE == "manager":
            await self.bot.close()
            os._exit(0)
        else:
            try:
                python = sys.executable
                subprocess.Popen([python] + sys.argv)
            finally:
                await self.bot.close()
                os._exit(0)

async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
