# app/voice.py
import asyncio
import discord
from typing import List, Dict

TEMP_CHANNELS: Dict[int, List[int]] = {}

async def create_and_move_voice(inter: discord.Interaction, teams: List[List[discord.Member]], sizes: List[int], ttl_minutes: int = 60):
    guild = inter.guild
    if not guild: return
    parent = None
    author = inter.user if isinstance(inter.user, discord.Member) else guild.get_member(inter.user.id)
    if isinstance(author, discord.Member) and author.voice and author.voice.channel and author.voice.channel.category:
        parent = author.voice.channel.category

    created = []
    for i, _team in enumerate(teams, start=1):
        ch = await guild.create_voice_channel(
            name=f"Team {i}",
            user_limit=sizes[i-1] if i-1 < len(sizes) else None,
            category=parent
        )
        created.append(ch.id)

    for i, team in enumerate(teams):
        dest = guild.get_channel(created[i])
        if not dest: continue
        for m in team:
            if m.voice and m.voice.channel:
                try: await m.move_to(dest, reason="TeamBuilder")
                except discord.Forbidden: pass

    TEMP_CHANNELS[guild.id] = created

    async def cleanup():
        await asyncio.sleep(ttl_minutes * 60)
        ids = TEMP_CHANNELS.get(guild.id, [])
        for cid in ids:
            ch = guild.get_channel(cid)
            try:
                if ch: await ch.delete(reason="TeamBuilder cleanup")
            except discord.Forbidden:
                pass
        TEMP_CHANNELS.pop(guild.id, None)

    asyncio.create_task(cleanup())
