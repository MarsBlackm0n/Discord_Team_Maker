# app/riot.py
from __future__ import annotations
from typing import Optional, Tuple
import aiohttp

PLATFORM_MAP = {
    "EUW":"euw1","EUNE":"eun1","NA":"na1","KR":"kr","BR":"br1",
    "JP":"jp1","LAN":"la1","LAS":"la2","OCE":"oc1","TR":"tr1","RU":"ru",
}
TIER_BASE = {
    "IRON":800,"BRONZE":900,"SILVER":1000,"GOLD":1100,
    "PLATINUM":1200,"EMERALD":1300,"DIAMOND":1400,
    "MASTER":1500,"GRANDMASTER":1600,"CHALLENGER":1700
}
DIV_BONUS = {"IV":0,"III":20,"II":40,"I":60}

def rank_to_rating(tier: str, division: Optional[str], lp: int) -> float:
    base = TIER_BASE.get((tier or "").upper(), 1000)
    bonus = DIV_BONUS.get((division or "").upper(), 0)
    lp_bonus = max(0, min(int(lp or 0), 100)) * 0.5
    return base + bonus + lp_bonus

async def fetch_lol_rank_info(riot_key: Optional[str], region_code: str, summoner_name: str) -> Optional[Tuple[str, Optional[str], int, float]]:
    if not riot_key: return None
    headers = {"X-Riot-Token": riot_key}
    base = f"https://{region_code}.api.riotgames.com"
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{base}/lol/summoner/v4/summoners/by-name/{summoner_name}", headers=headers) as r:
            if r.status != 200: return None
            summ = await r.json()
        summ_id = summ.get("id")
        if not summ_id: return None
        async with session.get(f"{base}/lol/league/v4/entries/by-summoner/{summ_id}", headers=headers) as r:
            if r.status != 200: return None
            entries = await r.json()
    chosen = next((e for e in entries if e.get("queueType")=="RANKED_SOLO_5x5"), entries[0] if entries else None)
    if not chosen: return None
    tier = (chosen.get("tier") or "").upper()
    division = chosen.get("rank")
    lp = int(chosen.get("leaguePoints", 0))
    rating = rank_to_rating(tier, division, lp)
    return tier, division, lp, rating
