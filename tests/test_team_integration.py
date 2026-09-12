import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.cogs.team import TeamCog
from app.db import init_db, set_lane_preferences, load_lane_preferences, get_team_last, set_rating, get_rating
from app.lanes import ROLES


class TeamIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.path = Path(__file__).parent / f"test-{uuid.uuid4().hex}.sqlite"
        await init_db(self.path)

    async def asyncTearDown(self):
        self.path.unlink(missing_ok=True)

    async def test_preferences_persist_and_are_scoped_to_guild_without_losing_ratings(self):
        await set_rating(self.path, 42, 1500)
        await set_lane_preferences(self.path, 1, 42, ["mid", "top", "jgl"])
        await init_db(self.path)
        self.assertEqual(await load_lane_preferences(self.path, 1), {42: ["mid", "top", "jgl"]})
        self.assertEqual(await load_lane_preferences(self.path, 2), {})
        self.assertEqual(await get_rating(self.path, 42), 1500)
        await set_lane_preferences(self.path, 1, 42, ["sup"])
        self.assertEqual(await load_lane_preferences(self.path, 1), {42: ["sup"]})

    async def test_roll_persists_lanes_and_random_does_not_import_riot(self):
        members = [SimpleNamespace(id=i, display_name=f"Player {i}", bot=False) for i in range(10)]
        for m in members:
            await set_lane_preferences(self.path, 1, m.id, [ROLES[m.id % 5]])
        cog = TeamCog(SimpleNamespace(settings=SimpleNamespace(DB_PATH=self.path)))
        cog.ensure_ratings_for_members = AsyncMock(return_value=({i: 1000 for i in range(10)}, [], []))
        interaction = SimpleNamespace(guild=SimpleNamespace(id=1), user=SimpleNamespace(id=0))
        embed, teams, ratings = await cog._generate_roll(
            interaction, session="test-session", team_count=2, sizes="", with_groups="",
            avoid_pairs="", members="", mode="random", attempts=50, commit=True,
            selected_members=members,
        )
        cog.ensure_ratings_for_members.assert_awaited_once_with(members, auto_import_riot=False)
        snapshot = await get_team_last(self.path, 1)
        self.assertEqual(snapshot["params"]["session"], "test-session")
        self.assertEqual(len(snapshot["assignments"]), 10)
        self.assertEqual(snapshot["teams"], [[m.id for m in t] for t in teams])
        self.assertIn("**TOP**", embed.fields[0].value)
        self.assertNotIn("1000", embed.fields[0].value)


if __name__ == "__main__":
    unittest.main()
