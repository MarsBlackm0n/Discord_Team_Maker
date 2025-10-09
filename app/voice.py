# app/voice.py
from __future__ import annotations

import asyncio
import time
from typing import List, Dict, Optional

import discord

# TEMP_CHANNELS[guild_id][channel_id] = expires_at (timestamp en secondes)
# On ne suit QUE les salons créés par le bot.
TEMP_CHANNELS: Dict[int, Dict[int, float]] = {}

# Pour éviter de lancer plusieurs boucles de cleanup en parallèle par serveur
_CLEANUP_RUNNING: Dict[int, bool] = {}


async def _cleanup_loop(guild: discord.Guild):
    """Boucle de nettoyage par serveur. Supprime uniquement les salons dont l'expiration est atteinte."""
    if _CLEANUP_RUNNING.get(guild.id):
        return
    _CLEANUP_RUNNING[guild.id] = True
    try:
        while True:
            entries = TEMP_CHANNELS.get(guild.id, {})
            if not entries:
                # Rien à nettoyer -> on arrête la boucle
                _CLEANUP_RUNNING[guild.id] = False
                return

            now = time.time()
            # Prochaine échéance
            next_exp = min(entries.values())
            sleep_for = max(1.0, next_exp - now)
            await asyncio.sleep(sleep_for)

            # Après l'attente, supprime tout ce qui a expiré
            entries = TEMP_CHANNELS.get(guild.id, {})
            if not entries:
                continue
            now = time.time()
            expired_ids = [cid for cid, exp in entries.items() if exp <= now]

            for cid in expired_ids:
                ch = guild.get_channel(cid)
                if ch and isinstance(ch, discord.VoiceChannel):
                    try:
                        await ch.delete(reason="TeamBuilder cleanup (TTL)")
                    except discord.Forbidden:
                        pass
                    except discord.HTTPException:
                        pass
                # Retire de la table, même si déjà supprimé / plus accessible
                entries.pop(cid, None)

            # Si plus rien à suivre, la boucle se terminera au prochain tour
            if not entries:
                TEMP_CHANNELS.pop(guild.id, None)
    except Exception:
        # En cas d'erreur inattendue, on libère le flag pour pouvoir relancer plus tard
        _CLEANUP_RUNNING[guild.id] = False


async def _find_existing_team_channels(guild: discord.Guild, k: int) -> List[Optional[discord.VoiceChannel]]:
    """Retourne une liste de longueur k, avec VoiceChannel si trouvé ('Team i') sinon None. Insensible à la casse."""
    by_name = {vc.name.lower(): vc for vc in guild.voice_channels}
    out: List[Optional[discord.VoiceChannel]] = []
    for i in range(1, k + 1):
        out.append(by_name.get(f"team {i}"))
    return out


async def create_and_move_voice(
    inter: discord.Interaction,
    teams: List[List[discord.Member]],
    sizes: List[int],
    *,
    ttl_minutes: int = 90,
) -> None:
    """
    Crée OU réutilise des salons vocaux 'Team 1..K' et déplace les joueurs.
    - Si 'Team i' existe déjà : réutilisation automatique (pas de suppression à cause d'un ancien TTL).
      * Si ce salon avait été créé par le bot et suivi dans TEMP_CHANNELS, son TTL est **réinitialisé**.
    - Si 'Team i' n'existe pas : création et ajout au suivi TTL.
    - Le nettoyage ne supprime **que** les salons créés par le bot, au moment où leur TTL expire.
    """
    guild = inter.guild
    if not guild:
        return

    # Catégorie par défaut = catégorie du vocal de l'auteur (si dispo)
    parent: Optional[discord.CategoryChannel] = None
    author = guild.get_member(inter.user.id)
    if author and author.voice and author.voice.channel and author.voice.channel.category:
        parent = author.voice.channel.category

    k = len(teams)
    existing = await _find_existing_team_channels(guild, k)

    # Prépare la map de suivi pour ce serveur
    if guild.id not in TEMP_CHANNELS:
        TEMP_CHANNELS[guild.id] = {}
    tracked = TEMP_CHANNELS[guild.id]

    channels: List[discord.VoiceChannel] = []
    expires_at = time.time() + max(1, int(ttl_minutes)) * 60

    for i in range(k):
        wanted_limit = sizes[i] if i < len(sizes) and sizes[i] > 0 else 0  # 0 = illimité
        ch = existing[i]

        if ch is None:
            # Crée le salon Team i+1
            ch = await guild.create_voice_channel(
                name=f"Team {i+1}",
                user_limit=wanted_limit,
                category=parent,
                reason="TeamBuilder create voice channel",
            )
            # Suit ce salon, avec sa date d'expiration
            tracked[ch.id] = expires_at
        else:
            # Réutilisation : ajuste le user_limit si nécessaire
            try:
                if ch.user_limit != wanted_limit:
                    await ch.edit(user_limit=wanted_limit, reason="TeamBuilder adjust team channel size")
            except discord.Forbidden:
                pass
            except discord.HTTPException:
                pass

            # Si ce salon avait été créé par le bot auparavant et est suivi, on RESET son TTL
            if ch.id in tracked:
                tracked[ch.id] = expires_at

        channels.append(ch)

    # Déplacer les joueurs
    for idx, team in enumerate(teams):
        dest = channels[idx]
        for m in team:
            if m.voice and m.voice.channel and m.voice.channel.id != dest.id:
                try:
                    await m.move_to(dest, reason="TeamBuilder move")
                except discord.Forbidden:
                    pass
                except discord.HTTPException:
                    pass

    # Lancer (ou relancer) la boucle de nettoyage pour ce serveur
    asyncio.create_task(_cleanup_loop(guild))
