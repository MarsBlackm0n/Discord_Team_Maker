# app/cogs/admin.py
import os, sys, asyncio, subprocess
import csv
import io
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

from discord import app_commands
import discord

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


# app/cogs/admin.py (dans class AdminCog)
@app_commands.command(name="exportcsv", description="Exporter la base en CSV (un fichier par table, zippé).")
async def exportcsv(self, inter: discord.Interaction):
    if not self._is_authorized(inter):
        await inter.response.send_message("⛔ Autorisation refusée.", ephemeral=True)
        return

    db_path = self.bot.settings.DB_PATH
    # Tables à exporter (si tu en ajoutes d'autres, complète la liste)
    # On peut aussi découvrir dynamiquement via sqlite_master.
    async with aiosqlite.connect(db_path) as db:
        # Découverte dynamique des tables (exclut les tables internes SQLite)
        tables = []
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name") as cur:
            async for (name,) in cur:
                tables.append(name)

    if not tables:
        await inter.response.send_message("😕 Aucune table à exporter.", ephemeral=True)
        return

    # Crée un zip temportaire avec 1 CSV par table
    ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
    zip_filename = f"export-{ts}.zip"

    # On écrit sur le FS éphémère (/tmp), puis on l'envoie
    with tempfile.TemporaryDirectory() as tmpdir:
        # Générer un CSV par table
        async with aiosqlite.connect(db_path) as db:
            for t in tables:
                csv_path = os.path.join(tmpdir, f"{t}.csv")
                async with db.execute(f"SELECT * FROM {t}") as cur:
                    # Récupération des colonnes
                    cols = [c[0] for c in cur.description]
                    # Écriture CSV
                    with open(csv_path, "w", newline="", encoding="utf-8") as f:
                        writer = csv.writer(f)
                        writer.writerow(cols)
                        async for row in cur:
                            writer.writerow(row)

        # Zipper tout
        zip_path = os.path.join(tmpdir, zip_filename)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for t in tables:
                z.write(os.path.join(tmpdir, f"{t}.csv"), arcname=f"{t}.csv")

        # Envoyer le zip
        try:
            await inter.response.send_message(
                content=f"🗂️ Export CSV ({len(tables)} tables) — `{zip_filename}`",
                file=discord.File(fp=zip_path, filename=zip_filename),
                ephemeral=True
            )
        except Exception as e:
            await inter.response.send_message(f"❌ Échec de l'export CSV : {e}", ephemeral=True)
