import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from app import voice


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        voice.TEMP_CHANNELS.clear()
        voice._IDLE_TTLS.clear()
        voice._OCCUPIED.clear()
        self.channel = Mock(spec=discord.VoiceChannel)
        self.channel.id = 2
        self.channel.members = []
        self.channel.delete = AsyncMock()
        self.guild = SimpleNamespace(id=1, unavailable=False, get_channel=lambda cid: self.channel)
        with patch("app.voice.time.time", return_value=0):
            voice._track_channel(1, 2, 1)

    async def cleanup(self, now):
        with patch("app.voice.time.time", return_value=now):
            await voice._cleanup_once(self.guild)

    async def test_occupied_expired_channel_is_never_deleted(self):
        self.channel.members = [object()]
        await self.cleanup(10000)
        self.channel.delete.assert_not_awaited()
        self.assertIn(2, voice.TEMP_CHANNELS[1])

    async def test_full_idle_delay_after_last_player_leaves(self):
        self.channel.members = [object()]
        await self.cleanup(60)
        self.channel.members = []
        await self.cleanup(120)
        await self.cleanup(179)
        self.channel.delete.assert_not_awaited()
        await self.cleanup(180)
        self.channel.delete.assert_awaited_once()
        self.assertNotIn(2, voice.TEMP_CHANNELS[1])

    async def test_reuse_resets_deadline(self):
        with patch("app.voice.time.time", return_value=59):
            voice._track_channel(1, 2, 1)
        await self.cleanup(60)
        self.channel.delete.assert_not_awaited()
        await self.cleanup(119)
        self.channel.delete.assert_awaited_once()

    async def test_delete_failure_is_retried(self):
        self.channel.delete.side_effect = discord.HTTPException(Mock(status=500, reason="error"), "error")
        await self.cleanup(60)
        self.assertIn(2, voice.TEMP_CHANNELS[1])
        self.channel.delete.side_effect = None
        await self.cleanup(120)
        self.assertNotIn(2, voice.TEMP_CHANNELS[1])

    async def test_unavailable_guild_is_not_cleaned(self):
        self.guild.unavailable = True
        await self.cleanup(10000)
        self.channel.delete.assert_not_awaited()

    async def test_existing_untracked_channel_is_not_deleted(self):
        voice.TEMP_CHANNELS.clear()
        await self.cleanup(10000)
        self.channel.delete.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
