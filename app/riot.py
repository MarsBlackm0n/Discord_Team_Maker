# app/riot.py
from __future__ import annotations
from typing import Optional, Tuple, Dict, List
import asyncio
import aiohttp

PLATFORM_MAP = {
    "EUW": "euw1", "EUNE": "eun1", "NA": "na1", "KR": "kr", "BR": "br1",
    "JP": "jp1", "LAN": "la1", "LAS": "la2", "OCE": "oc1", "TR": "tr1", "RU": "ru",
}

# Routing continental pour l'API Account-v1 (par région de jeu, pas par plateforme).
REGION_TO_CONTINENT = {
    "EUW": "europe", "EUNE": "europe", "TR": "europe", "RU": "europe",
    "NA": "americas", "BR": "americas", "LAN": "americas", "LAS": "americas",
    "KR": "asia", "JP": "asia",
    "OCE": "sea",
}

TIER_BASE = {
    "IRON":800,"BRONZE":900,"SILVER":1000,"GOLD":1100,
    "PLATINUM":1200,"EMERALD":1300,"DIAMOND":1400,
    "MASTER":1500,"GRANDMASTER":1600,"CHALLENGER":1700
}
DIV_BONUS = {"IV":0,"III":20,"II":40,"I":60}

# Sans ceci, aiohttp attend jusqu'à 5 minutes (défaut) avant d'abandonner une requête muette,
# laissant l'interaction Discord bloquée sur "réfléchit..." bien après le délai raisonnable.
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)


def _new_session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(timeout=_HTTP_TIMEOUT)


def rank_to_rating(tier: str, division: Optional[str], lp: int) -> float:
    base = TIER_BASE.get((tier or "").upper(), 1000)
    bonus = DIV_BONUS.get((division or "").upper(), 0)
    lp_bonus = max(0, min(int(lp or 0), 100)) * 0.5
    return base + bonus + lp_bonus


class RiotApiError(Exception):
    """Erreur Riot API générique, porte le code HTTP reçu."""
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class RiotKeyInvalid(RiotApiError):
    """Clé Riot manquante, invalide ou expirée (401/403)."""


class RiotNotFound(RiotApiError):
    """Riot ID ou compte introuvable (404)."""


class RiotRateLimited(RiotApiError):
    """Trop de requêtes (429). `retry_after` est en secondes."""
    def __init__(self, retry_after: float):
        super().__init__(429, f"Riot API : rate limité, réessayer dans {retry_after:.0f}s")
        self.retry_after = retry_after


def parse_riot_id(raw: str) -> Tuple[str, str]:
    """'Pseudo#TAG' -> ('Pseudo', 'TAG'). Lève ValueError si le format est invalide."""
    if "#" not in raw:
        raise ValueError("Format attendu : Pseudo#TAG (Riot ID complet, visible dans le client LoL).")
    game_name, _, tag_line = raw.partition("#")
    game_name, tag_line = game_name.strip(), tag_line.strip()
    if not game_name or not tag_line:
        raise ValueError("Format attendu : Pseudo#TAG (Riot ID complet, visible dans le client LoL).")
    return game_name, tag_line


async def _get_json(session: aiohttp.ClientSession, url: str, headers: dict):
    try:
        async with session.get(url, headers=headers) as r:
            if r.status == 200:
                return await r.json()
            if r.status in (401, 403):
                raise RiotKeyInvalid(r.status, "Clé Riot manquante, invalide ou expirée.")
            if r.status == 404:
                raise RiotNotFound(r.status, "Introuvable côté Riot.")
            if r.status == 429:
                retry_after = float(r.headers.get("Retry-After", "1") or "1")
                raise RiotRateLimited(retry_after)
            raise RiotApiError(r.status, f"Erreur Riot API (HTTP {r.status}).")
    except asyncio.TimeoutError:
        raise RiotApiError(0, "Riot API : délai d'attente dépassé (réseau ou service indisponible).")
    except aiohttp.ClientError as exc:
        raise RiotApiError(0, f"Riot API : erreur réseau ({exc}).")


async def fetch_puuid_by_riot_id(riot_key: str, region_code: str, game_name: str, tag_line: str) -> str:
    """Account-v1 : Riot ID -> PUUID. Lève RiotApiError (voir sous-classes) en cas d'échec."""
    continent = REGION_TO_CONTINENT.get((region_code or "").upper())
    if not continent:
        raise ValueError(f"Région inconnue : {region_code}")
    headers = {"X-Riot-Token": riot_key}
    url = f"https://{continent}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    async with _new_session() as session:
        data = await _get_json(session, url, headers)
    puuid = data.get("puuid")
    if not puuid:
        raise RiotNotFound(404, "PUUID introuvable pour ce Riot ID.")
    return puuid


async def fetch_lol_rank_by_puuid(
    riot_key: str, region_code: str, puuid: str
) -> Optional[Tuple[str, Optional[str], int, float]]:
    """League-v4 by-puuid : renvoie le rang RANKED_SOLO_5x5, ou None si non classé cette saison."""
    platform = PLATFORM_MAP.get((region_code or "").upper())
    if not platform:
        raise ValueError(f"Région inconnue : {region_code}")
    headers = {"X-Riot-Token": riot_key}
    url = f"https://{platform}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
    async with _new_session() as session:
        entries = await _get_json(session, url, headers)
    chosen = next((e for e in entries if e.get("queueType") == "RANKED_SOLO_5x5"), None)
    if not chosen:
        return None
    tier = (chosen.get("tier") or "").upper()
    division = chosen.get("rank")
    lp = int(chosen.get("leaguePoints", 0))
    rating = rank_to_rating(tier, division, lp)
    return tier, division, lp, rating


async def fetch_lol_rank_info(
    riot_key: Optional[str], region_code: str, game_name: str, tag_line: str
) -> Optional[Tuple[str, Optional[str], int, float, str]]:
    """
    Riot ID complet -> (tier, division, lp, rating, puuid).
    Lève RiotApiError (RiotKeyInvalid/RiotNotFound/RiotRateLimited) en cas d'échec Riot ;
    renvoie None uniquement si le compte n'a pas de rang en solo/duo.
    """
    if not riot_key:
        return None
    puuid = await fetch_puuid_by_riot_id(riot_key, region_code, game_name, tag_line)
    rank = await fetch_lol_rank_by_puuid(riot_key, region_code, puuid)
    if rank is None:
        return None
    tier, division, lp, rating = rank
    return tier, division, lp, rating, puuid


# ---- Inférence des rôles préférés à partir de l'historique de matchs ----

RANKED_SOLO_QUEUE_ID = 420

# teamPosition (Match-v5) -> code de rôle interne (voir app/lanes.py: ROLES).
POSITION_TO_ROLE = {
    "TOP": "top", "JUNGLE": "jgl", "MIDDLE": "mid", "BOTTOM": "bot", "UTILITY": "sup",
}
_ROLE_ORDER = ("top", "jgl", "mid", "bot", "sup")

# Pause entre deux appels match-v5 pour ne pas dépasser le rate limit applicatif
# d'une clé perso (~20 req/s), qui s'applique en plus des limites par méthode affichées
# sur le Developer Portal.
_MATCH_FETCH_DELAY = 0.07


async def fetch_recent_role_counts(
    riot_key: str, region_code: str, puuid: str, count: int = 8
) -> Dict[str, int]:
    """
    Analyse les `count` dernières games RANKED_SOLO_5x5 du joueur (Match-v5) et renvoie
    {role: nombre_de_parties}. `count` doit rester bas (8-10) : chaque partie coûte un
    appel API en plus de celui qui liste les IDs, et une clé perso est limitée à
    ~100 requêtes/2min tous endpoints confondus.
    """
    continent = REGION_TO_CONTINENT.get((region_code or "").upper())
    if not continent:
        raise ValueError(f"Région inconnue : {region_code}")
    headers = {"X-Riot-Token": riot_key}
    base = f"https://{continent}.api.riotgames.com"
    ids_url = f"{base}/lol/match/v5/matches/by-puuid/{puuid}/ids?queue={RANKED_SOLO_QUEUE_ID}&count={int(count)}"

    counts: Dict[str, int] = {}
    async with _new_session() as session:
        match_ids: List[str] = await _get_json(session, ids_url, headers)
        for match_id in match_ids:
            detail = await _get_json(session, f"{base}/lol/match/v5/matches/{match_id}", headers)
            await asyncio.sleep(_MATCH_FETCH_DELAY)
            participants = (detail.get("info") or {}).get("participants") or []
            me = next((p for p in participants if p.get("puuid") == puuid), None)
            if not me:
                continue
            role = POSITION_TO_ROLE.get((me.get("teamPosition") or "").upper())
            if role:
                counts[role] = counts.get(role, 0) + 1
    return counts


def rank_roles_by_frequency(counts: Dict[str, int]) -> List[str]:
    """{role: count} -> liste de rôles triée du plus au moins joué (ties: ordre top/jgl/mid/bot/sup)."""
    return [r for r, _ in sorted(counts.items(), key=lambda kv: (-kv[1], _ROLE_ORDER.index(kv[0])))]
