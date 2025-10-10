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


def _match_team_name(name: str, base_name: str) -> bool:
    return name.lower().startswith(base_name.lower() + " ")


async def _find_existing_team_channels(
    guild: discord.Guild,
    k: int,
    *,
    base_name: str = "Team",
    category: Optional[discord.CategoryChannel] = None,
) -> List[Optional[discord.VoiceChannel]]:
    """
    Retourne une liste de longueur k :
    - à l'index i (0-based) -> VoiceChannel "base_name {i+1}" si trouvé dans la (catégorie filtrée si donnée), sinon None.
    """
    by_name: Dict[str, discord.VoiceChannel] = {}
    for vc in guild.voice_channels:
        if category and vc.category_id != category.id:
            continue
        if _match_team_name(vc.name, base_name):
            by_name[vc.name.lower()] = vc

    out: List[Optional[discord.VoiceChannel]] = []
    for i in range(1, k + 1):
        out.append(by_name.get(f"{base_name.lower()} {i}"))
    return out


async def _ensure_lobby_and_pin_top(
    guild: discord.Guild,
    *,
    parent: Optional[discord.CategoryChannel],
    base_name: str = "Team",
    lobby_name: str = "Lobby Tournoi",
    ttl_minutes: int = 90,
):
    """
    Crée un salon 'Lobby Tournoi' si absent et remonte Lobby + base_name 1..K en haut de la catégorie.
    Détermine l'ordre : Lobby (0), puis Team 1..K (par numéro).
    """
    import time as _time

    if not parent:
        return

    # Créer/trouver le lobby dans cette catégorie
    lobby: Optional[discord.VoiceChannel] = None
    for ch in parent.voice_channels:
        if ch.name.lower() == lobby_name.lower():
            lobby = ch
            break

    if lobby is None:
        try:
            lobby = await guild.create_voice_channel(
                name=lobby_name,
                user_limit=0,
                category=parent,
                reason="Arena lobby",
            )
            # suivi TTL (dict de dicts)
            TEMP_CHANNELS.setdefault(guild.id, {})[lobby.id] = int(_time.time()) + ttl_minutes * 60
        except discord.Forbidden:
            lobby = None  # pas bloquant

    # Récupérer tous les channels "base_name i" de la catégorie
    def team_index(ch: discord.VoiceChannel) -> int:
        parts = ch.name.split()
        try:
            return int(parts[-1]) if _match_team_name(ch.name, base_name) else 9999
        except Exception:
            return 9999

    team_channels = [vc for vc in parent.voice_channels if _match_team_name(vc.name, base_name)]
    team_channels.sort(key=team_index)

    # Ordre voulu : Lobby (0) puis Team 1..K (1..K)
    ordered: List[discord.VoiceChannel] = []
    if lobby:
        ordered.append(lobby)
    ordered.extend(team_channels)

    # Repositionner en haut
    for pos, ch in enumerate(ordered):
        try:
            await ch.edit(position=pos, reason="Pin lobby/teams on top")
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass


async def create_and_move_voice(
    inter: discord.Interaction,
    teams: List[List[discord.Member]],
    sizes: List[int],
    *,
    ttl_minutes: int = 90,
    reuse_existing: bool = True,
    base_name: str = "Team",
    create_lobby: bool = True,
    lobby_name: str = "Lobby Tournoi",
    pin_on_top: bool = True,
) -> None:
    """
    Crée OU réutilise des salons vocaux f"{base_name} 1..K" et déplace les joueurs.
    Voir doc d’origine…
    """
    guild = inter.guild
    if not guild:
        return

    # ---------- NOUVELLE LOGIQUE DE SÉLECTION DE CATÉGORIE ----------
    def most_common(items):
        from collections import Counter
        if not items:
            return None
        return Counter(items).most_common(1)[0][0]

    def looks_like_voice_cat(cat: discord.CategoryChannel | None) -> int:
        """Score simple pour favoriser les catégories 'vocal/voice/voix'."""
        if not cat:
            return 0
        name = cat.name.lower()
        return int(
            ("vocal" in name) or ("voice" in name) or ("voix" in name)
        )

    parent: Optional[discord.CategoryChannel] = None

    # 1) Si des joueurs sont déjà en vocal → prendre la catégorie la plus courante.
    member_voice_cats: List[discord.CategoryChannel] = []
    for team in teams:
        for m in team:
            if isinstance(m, discord.Member) and m.voice and m.voice.channel and m.voice.channel.category:
                member_voice_cats.append(m.voice.channel.category)
    if member_voice_cats:
        parent = most_common(member_voice_cats)

    # 2) Sinon, s'il existe déjà des salons "{base_name} i" → réutiliser leur catégorie.
    if parent is None:
        existing_team_vcs = [vc for vc in guild.voice_channels if vc.name.lower().startswith(base_name.lower() + " ")]
        if existing_team_vcs:
            # Choisir la catégorie la plus fréquente parmi celles existantes
            cats = [vc.category for vc in existing_team_vcs if vc.category]
            parent = most_common(cats) if cats else None

    # 3) Sinon, choisir une catégorie adaptée aux vocaux :
    #    - catégorie avec le plus de voice channels
    #    - tie-breaker: nom "vocal/voice/voix"
    if parent is None:
        from collections import defaultdict
        count_by_cat: dict[Optional[discord.CategoryChannel], int] = defaultdict(int)
        for vc in guild.voice_channels:
            count_by_cat[vc.category] += 1
        if count_by_cat:
            # score (count, bonus_nom) trié descendant
            parent = max(
                count_by_cat.keys(),
                key=lambda c: (count_by_cat[c], looks_like_voice_cat(c))
            )

    # 4) Si toujours None → on laissera Discord créer à la racine des salons vocaux (pas de catégorie texte).
    # ---------------------------------------------------------------

    k = len(teams)
    if k <= 0:
        return


    # Prépare la map de suivi pour ce serveur
    tracked = TEMP_CHANNELS.setdefault(guild.id, {})
    expires_at = time.time() + max(1, int(ttl_minutes)) * 60

    # Option réutilisation : ne chercher que dans la catégorie cible (si connue)
    existing: List[Optional[discord.VoiceChannel]] = [None] * k
    if reuse_existing:
        existing = await _find_existing_team_channels(guild, k, base_name=base_name, category=parent)

    channels: List[discord.VoiceChannel] = []
    for i in range(k):
        wanted_limit = sizes[i] if i < len(sizes) and sizes[i] > 0 else 0  # 0 = illimité
        ch = existing[i]

        if ch is None:
            # Crée le salon base_name i+1
            ch = await guild.create_voice_channel(
                name=f"{base_name} {i+1}",
                user_limit=wanted_limit,
                category=parent,
                reason="TeamBuilder create voice channel",
            )
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

    # Lobby + épinglage en haut (optionnel)
    if pin_on_top or create_lobby:
        await _ensure_lobby_and_pin_top(
            guild,
            parent=parent,
            base_name=base_name,
            lobby_name=lobby_name,
            ttl_minutes=ttl_minutes,
        )

    # Lancer (ou relancer) la boucle de nettoyage pour ce serveur
    asyncio.create_task(_cleanup_loop(guild))
