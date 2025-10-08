# app/cogs/admin.py
import os, sys, asyncio, subprocess
import csv
import aiosqlite
import tempfile
import zipfile
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------ helper d'autorisation (owner/admin) ------
    def _is_authorized(self, inter: discord.Interaction) -> bool:
        settings = self.bot.settings
        if settings.OWNER_ID and inter.user.id == settings.OWNER_ID:
            return True
        member = inter.guild and inter.guild.get_member(inter.user.id)
        return bool(member and (member.guild_permissions.administrator or member.guild_permissions.manage_guild))

    # ------ utilitaires ------
    @app_commands.command(name="whoami", description="Affiche ton User ID.")
    async def whoami(self, inter: discord.Interaction):
        await inter.response.send_message(f"🪪 Ton User ID : `{inter.user.id}`", ephemeral=True)

    # petit helper d’affichage
    def _fmt_cmds(self, cmds: list[app_commands.AppCommand]) -> str:
        try:
            names = [c.name for c in cmds]
        except Exception:
            names = []
        return ", ".join(names) or "(aucune)"

    @app_commands.command(name="resync", description="Resynchroniser les commandes (owner/admin).")
    async def resync(self, inter: discord.Interaction):
        if not self._is_authorized(inter):
            await inter.response.send_message("⛔ Autorisation refusée.", ephemeral=True); return
        await inter.response.defer(ephemeral=True, thinking=True)

        msgs = []
        # 1) Global sync (protégée)
        try:
            g = await self.bot.tree.sync()
            msgs.append(f"🌐 Global: {len(g)} cmds ({self._fmt_cmds(g)})")
        except Exception as e:
            msgs.append(f"🌐 Global: échec ({e})")

        # 2) Guild sync immédiate (si configurée)
        if self.bot.settings.GUILD_ID:
            try:
                guild = discord.Object(id=self.bot.settings.GUILD_ID)
                self.bot.tree.copy_global_to(guild=guild)
                l = await self.bot.tree.sync(guild=guild)
                msgs.append(f"🏠 Guild {self.bot.settings.GUILD_ID}: {len(l)} cmds ({self._fmt_cmds(l)})")
            except Exception as e:
                msgs.append(f"🏠 Guild {self.bot.settings.GUILD_ID}: échec ({e})")

        await inter.followup.send("\n".join(msgs), ephemeral=True)

    @app_commands.command(name="resyncglobal", description="Publier/mettre à jour les commandes globales (+ guild).")
    async def resyncglobal(self, inter: discord.Interaction):
        if not self._is_authorized(inter):
            await inter.response.send_message("⛔ Autorisation refusée.", ephemeral=True); return
        await inter.response.defer(ephemeral=True, thinking=True)

        msgs = []
        try:
            g = await self.bot.tree.sync()
            msgs.append(f"🌐 Global: {len(g)} cmds ({self._fmt_cmds(g)})")
        except Exception as e:
            msgs.append(f"🌐 Global: échec ({e})")

        if self.bot.settings.GUILD_ID:
            try:
                guild = discord.Object(id=self.bot.settings.GUILD_ID)
                self.bot.tree.copy_global_to(guild=guild)
                l = await self.bot.tree.sync(guild=guild)
                msgs.append(f"🏠 Guild {self.bot.settings.GUILD_ID}: {len(l)} cmds ({self._fmt_cmds(l)})")
            except Exception as e:
                msgs.append(f"🏠 Guild {self.bot.settings.GUILD_ID}: échec ({e})")

        await inter.followup.send("\n".join(msgs), ephemeral=True)

    # ------ cycle de vie ------
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

    # ------ sauvegarde / export ------
    @app_commands.command(name="backupdb", description="Télécharger la base SQLite (admin).")
    async def backupdb(self, inter: discord.Interaction):
        if not self._is_authorized(inter):
            await inter.response.send_message("⛔ Autorisation refusée.", ephemeral=True)
            return
        path = self.bot.settings.DB_PATH
        try:
            await inter.response.send_message(
                content=f"📦 Sauvegarde de `{path.name}`",
                file=discord.File(fp=str(path), filename=path.name),
                ephemeral=True
            )
        except Exception as e:
            await inter.response.send_message(f"❌ Impossible d'envoyer la BDD: {e}", ephemeral=True)

    @app_commands.command(name="exportcsv", description="Exporter la base en CSV (un fichier par table, zippé).")
    async def exportcsv(self, inter: discord.Interaction):
        if not self._is_authorized(inter):
            await inter.response.send_message("⛔ Autorisation refusée.", ephemeral=True)
            return

        db_path = self.bot.settings.DB_PATH

        # Découverte dynamique des tables (hors tables internes SQLite)
        async with aiosqlite.connect(db_path) as db:
            tables = []
            async with db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            ) as cur:
                async for (name,) in cur:
                    tables.append(name)

        if not tables:
            await inter.response.send_message("😕 Aucune table à exporter.", ephemeral=True)
            return

        ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
        zip_filename = f"export-{ts}.zip"

        # Ecrit des CSV temporaires dans /tmp puis zip
        with tempfile.TemporaryDirectory() as tmpdir:
            async with aiosqlite.connect(db_path) as db:
                for t in tables:
                    csv_path = os.path.join(tmpdir, f"{t}.csv")
                    async with db.execute(f"SELECT * FROM {t}") as cur:
                        cols = [c[0] for c in cur.description]
                        with open(csv_path, "w", newline="", encoding="utf-8") as f:
                            writer = csv.writer(f)
                            writer.writerow(cols)
                            async for row in cur:
                                writer.writerow(row)

            zip_path = os.path.join(tmpdir, zip_filename)
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for t in tables:
                    z.write(os.path.join(tmpdir, f"{t}.csv"), arcname=f"{t}.csv")

            try:
                await inter.response.send_message(
                    content=f"🗂️ Export CSV ({len(tables)} tables) — `{zip_filename}`",
                    file=discord.File(fp=zip_path, filename=zip_filename),
                    ephemeral=True
                )
            except Exception as e:
                await inter.response.send_message(f"❌ Échec de l'export CSV : {e}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
