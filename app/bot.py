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
        intents.message_content = True  # <- pour supprimer le warning
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings

    async def setup_hook(self) -> None:
        # Charger les cogs
        await self.load_extension("app.cogs.help")
        await self.load_extension("app.cogs.admin")
        await self.load_extension("app.cogs.ratings")
        await self.load_extension("app.cogs.team")

        # Init DB
        await init_db(self.settings.DB_PATH)

        # Sync slash (guild si fourni, sinon global)
        if self.settings.GUILD_ID:
            guild = discord.Object(id=self.settings.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        print(f"✅ Connecté comme {self.user} — slash prêts. DB: {self.settings.DB_PATH}")

def create_bot(settings: Settings) -> TeamBot:
    return TeamBot(settings)
