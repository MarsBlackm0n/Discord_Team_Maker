# app/bot.py
import os, sys, asyncio, subprocess
import discord
from discord.ext import commands
from discord import app_commands
from .config import Settings
from .db import init_db

class TeamBot(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        intents.members = True
        intents.voice_states = True
        intents.message_content = True  # optionnel; utile si tu utilises des cmds préfixées
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings

    async def setup_hook(self) -> None:
        # Charger les cogs
        for ext in [
            "app.cogs.tournament",
            "app.cogs.help",
            "app.cogs.admin",
            "app.cogs.ratings",
            "app.cogs.team",
        ]:
            try:
                await self.load_extension(ext)
                print(f"✅ Loaded {ext}")
            except Exception as e:
                import traceback
                print(f"❌ Failed to load {ext}: {e}")
                traceback.print_exc()

        # Init DB
        await init_db(self.settings.DB_PATH)

        # ✅ Publier les commandes en GLOBAL (tous serveurs) + sur la GUILD de dev (immédiat)
        try:
            await self.tree.sync()  # global sync (peut prendre quelques minutes à apparaître partout)
            print("✅ Global commands synced")
        except Exception as e:
            print("⚠️ Global sync error:", e)

        if self.settings.GUILD_ID:
            try:
                guild = discord.Object(id=self.settings.GUILD_ID)
                self.tree.copy_global_to(guild=guild)  # duplique les commandes pour apparition immédiate
                await self.tree.sync(guild=guild)
                print(f"✅ Guild commands synced for {self.settings.GUILD_ID}")
            except Exception as e:
                print("⚠️ Guild sync error:", e)

    async def on_ready(self):
        print(f"✅ Connecté comme {self.user} — slash prêts. DB: {self.settings.DB_PATH}")

def create_bot(settings: Settings) -> TeamBot:
    return TeamBot(settings)
