# app/voice.py
import asyncio
import discord
from typing import List, Dict, Tuple, Optional

# On ne mémorise que les salons créés par le bot pour pouvoir les supprimer ensuite.
# guild_id -> list[channel_id]
TEMP_CHANNELS: Dict[int, List[int]] = {}

async def create_and_move_voice(
    inter: discord.Interaction,
    teams: List[List[discord.Member]],
    sizes: List[int],
    ttl_minutes: int = 90,
    reuse_existing: bool = True
):
    """
    Crée (ou réutilise) des salons vocaux Team 1..K et déplace les joueurs.
    - reuse_existing=True : si "Team i" existe déjà, on le réutilise au lieu de le recréer.
    - Seuls les salons créés par le bot sont auto-supprimés après `ttl_minutes`.
    """
    guild = inter.guild
    if not guild:
        return

    # Tente d’aligner sous la même catégorie que le vocal de l’auteur (si dispo)
    parent = None
    author = inter.user if isinstance(inter.user, discord.Member) else guild.get_member(inter.user.id)
    if isinstance(author, discord.Member) and author.voice and author.voice.channel and author.voice.channel.category:
        parent = author.voice.channel.category

    created_now: List[int] = []
    resolved_channels: List[discord.VoiceChannel] = []

    # Index : nom -> salon existant
    existing_by_name = {ch.name: ch for ch in guild.voice_channels}

    # 1) Résoudre / créer les salons Team 1..K
    for i in range(1, len(teams) + 1):
        name = f"Team {i}"
        ch: Optional[discord.VoiceChannel] = None

        if reuse_existing:
            ch = existing_by_name.get(name)

        if ch is None:
            # Créer uniquement si pas trouvé
            ch = await guild.create_voice_channel(
                name=name,
                user_limit=sizes[i-1] if i-1 < len(sizes) else None,
                category=parent
            )
            created_now.append(ch.id)

        else:
            # Met à jour le user_limit si besoin (facultatif)
            try:
                if (i-1) < len(sizes) and ch.user_limit != sizes[i-1]:
                    await ch.edit(user_limit=sizes[i-1])
            except discord.Forbidden:
                pass

        resolved_channels.append(ch)

    # 2) Déplacer les joueurs dans chaque salon
    for idx, team in enumerate(teams):
        dest = resolved_channels[idx]
        if not dest:
            continue
        for m in team:
            if m.voice and m.voice.channel != dest:
                try:
                    await m.move_to(dest, reason="TeamBuilder")
                except discord.Forbidden:
                    pass

    # 3) Planifier le cleanup uniquement des salons créés à cette exécution
    if created_now:
        TEMP_CHANNELS[guild.id] = created_now

        async def cleanup():
            try:
                await asyncio.sleep(max(1, int(ttl_minutes)) * 60)
                ids = TEMP_CHANNELS.pop(guild.id, [])
                for cid in ids:
                    ch = guild.get_channel(cid)
                    try:
                        if ch:
                            await ch.delete(reason="TeamBuilder cleanup (TTL)")
                    except discord.Forbidden:
                        # Pas grave : peut rester si le bot n'a pas les perms
                        pass
            except Exception:
                # On évite de faire planter la task silencieusement
                pass

        asyncio.create_task(cleanup())
