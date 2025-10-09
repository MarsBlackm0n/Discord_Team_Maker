# app/bot.py
import os
import sys
import asyncio
import subprocess
import traceback
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
        # Garde message_content seulement si tu utilises des commandes préfixées ou lis du contenu
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings

    async def setup_hook(self) -> None:
        # 1) Charger les cogs (avec logs d’erreurs lisibles)
        async def _safe_load(ext: str):
            try:
                await self.load_extension(ext)
                print(f"✅ Loaded {ext}")
            except Exception as e:
                print(f"❌ Failed to load {ext}: {e}")
                traceback.print_exc()

        for ext in (
            "app.cogs.tournament",
            "app.cogs.help",
            "app.cogs.admin",
            "app.cogs.ratings",
            "app.cogs.team",
            "app.cogs.teamm_tournament"
        ):
            await _safe_load(ext)

        # 2) Init DB (avant la sync, pour éviter toute commande qui accède à la DB au tout début)
        await init_db(self.settings.DB_PATH)

        # 3) Sync des commandes
        #    - GLOBAL (peut prendre un peu de temps à se propager) — activable via SYNC_GLOBAL=true (défaut)
        #    - GUILD (si GUILD_ID défini) — apparition immédiate sur ta guild de dev
        if os.getenv("SYNC_GLOBAL", "true").lower() == "true":
            try:
                gcmds = await self.tree.sync()
                print(f"✅ Global commands synced: {len(gcmds)} -> {[c.name for c in gcmds]}")
            except Exception as e:
                print("⚠️ Global sync error:", e)

        if self.settings.GUILD_ID:
            try:
                guild = discord.Object(id=self.settings.GUILD_ID)
                # Copie les commandes globales pour apparition instantanée sur la guild
                self.tree.copy_global_to(guild=guild)
                lcmds = await self.tree.sync(guild=guild)
                print(f"✅ Guild commands synced for {self.settings.GUILD_ID}: {len(lcmds)} -> {[c.name for c in lcmds]}")
            except Exception as e:
                print("⚠️ Guild sync error:", e)

    async def on_ready(self):
        print(f"✅ Connecté comme {self.user} — slash prêts. DB: {self.settings.DB_PATH}")


def create_bot(settings: Settings) -> TeamBot:
    return TeamBot(settings)
