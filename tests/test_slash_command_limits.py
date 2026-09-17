import os
import tempfile
import unittest
from types import SimpleNamespace

import discord
from discord.ext import commands

# Cogs loaded by app/bot.py's setup_hook — kept in sync manually since the bot
# loads them by string name rather than importing this list.
COG_EXTENSIONS = (
    "app.cogs.tournament",
    "app.cogs.help",
    "app.cogs.admin",
    "app.cogs.ratings",
    "app.cogs.team",
    "app.cogs.team_tournament",
    "app.cogs.arena",
    "app.cogs.move",
)

# Discord's application command limits (see discord/app_commands docs):
# name <= 32 chars, description <= 100 chars. A single command over these
# limits makes Discord reject the *entire* bulk sync (HTTP 400 / 50035),
# leaving every other command stuck on its previous (possibly incompatible)
# definition — that's what broke /linklol after the Riot ID migration.
MAX_NAME_LEN = 32
MAX_DESCRIPTION_LEN = 100


class SlashCommandLimitsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        intents = discord.Intents.default()
        self.bot = commands.Bot(command_prefix="!", intents=intents)
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        self.bot.settings = SimpleNamespace(DB_PATH=db_path, RIOT_API_KEY=None, OWNER_ID=0, GUILD_ID=0)
        for ext in COG_EXTENSIONS:
            await self.bot.load_extension(ext)

    async def test_command_and_parameter_lengths_within_discord_limits(self):
        problems = []
        commands_seen = 0
        for cmd in self.bot.tree.walk_commands():
            commands_seen += 1
            name = cmd.name
            description = getattr(cmd, "description", "") or ""
            if len(name) > MAX_NAME_LEN:
                problems.append(f"{name}: name is {len(name)} chars (max {MAX_NAME_LEN})")
            if len(description) > MAX_DESCRIPTION_LEN:
                problems.append(f"{name}: description is {len(description)} chars (max {MAX_DESCRIPTION_LEN})")
            for param in getattr(cmd, "parameters", []):
                if len(param.name) > MAX_NAME_LEN:
                    problems.append(f"{name}.{param.name}: param name is {len(param.name)} chars")
                if param.description and len(param.description) > MAX_DESCRIPTION_LEN:
                    problems.append(f"{name}.{param.name}: param description is {len(param.description)} chars")

        self.assertGreater(commands_seen, 0, "no slash commands were registered — cogs failed to load?")
        self.assertEqual(problems, [], "Commands exceeding Discord limits:\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
