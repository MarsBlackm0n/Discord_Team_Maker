# app/team_logic.py
from __future__ import annotations
import random, re
from typing import List, Dict, Tuple, Set, Optional
import discord

def parse_mentions(guild: discord.Guild, text: str) -> List[discord.Member]:
    ids = [int(x) for x in re.findall(r"<@!?(\d+)>", text or "")]
    out, seen = [], set()
    for mid in ids:
        m = guild.get_member(mid)
        if m and not m.bot and mid not in seen:
            seen.add(mid); out.append(m)
    return out

def parse_sizes(s: Optional[str], total_players: int, k: int) -> List[int]:
    if s:
        nums = [int(x) for x in s.strip().split("/") if x.strip().isdigit()]
        if nums and sum(nums) == total_players and len(nums) == k:
            return nums
    base = total_players // k
    rem = total_players % k
    return [base + (1 if i < rem else 0) for i in range(k)]

def group_by_with_constraints(guild: discord.Guild, members: List[discord.Member], with_groups_text: str) -> List[List[discord.Member]]:
    allowed = {m.id: m for m in members}
    groups, used = [], set()
    if with_groups_text:
        chunks = [c.strip() for c in with_groups_text.split("|") if c.strip()]
        for ch in chunks:
            grp = []
            for m in parse_mentions(guild, ch):
                if m.id in allowed and m.id not in used:
                    used.add(m.id); grp.append(allowed[m.id])
            if grp: groups.append(grp)
    for m in members:
        if m.id not in used:
            groups.append([m])
    return groups

def parse_avoid_pairs(guild: discord.Guild, avoid_text: str) -> Set[Tuple[int,int]]:
    pairs: Set[Tuple[int,int]] = set()
    if not avoid_text: return pairs
    for p in [p.strip() for p in avoid_text.split(";") if p.strip()]:
        ms = parse_mentions(guild, p)
        if len(ms) >= 2:
            a, b = ms[0].id, ms[1].id
            if a != b: pairs.add(tuple(sorted((a,b))))
    return pairs

def split_random(members: List[discord.Member], k: int, sizes: List[int]) -> List[List[discord.Member]]:
    arr = members[:]
    random.shuffle(arr)
    teams = [[] for _ in range(k)]
    caps = sizes[:]
    i = 0
    for m in arr:
        for _ in range(k):
            idx = (i % k)
            if len(teams[idx]) < caps[idx]:
                teams[idx].append(m); i += 1; break
            i += 1
        else:
            j = min(range(k), key=lambda t: len(teams[t]))
            teams[j].append(m)
    return teams

def balance_k_teams_with_constraints(
    members: List[discord.Member],
    ratings: Dict[int, float],
    k: int,
    sizes: List[int],
    with_groups: List[List[discord.Member]],
    avoid_pairs: Set[Tuple[int,int]]
) -> tuple[List[List[discord.Member]], List[Tuple[int,int]]]:
    units = [(grp, sum(ratings[m.id] for m in grp)) for grp in with_groups]
    units.sort(key=lambda x: x[1], reverse=True)

    teams: List[List[discord.Member]] = [[] for _ in range(k)]
    totals = [0.0]*k
    caps = sizes[:]
    violations: List[Tuple[int,int]] = []

    def conflicts(grp, team) -> int:
        s, tids = 0, [m.id for m in team]
        for m in grp:
            for tid in tids:
                if tuple(sorted((m.id, tid))) in avoid_pairs: s += 1
        return s

    for grp, gscore in units:
        choices = []
        for idx in range(k):
            if len(teams[idx]) + len(grp) <= caps[idx]:
                pen = conflicts(grp, teams[idx])
                choices.append((pen, totals[idx], len(teams[idx]), idx))
        if not choices:
            idx = min(range(k), key=lambda t: len(teams[t]))
            for m in grp:
                for e in teams[idx]:
                    pair = tuple(sorted((m.id, e.id)))
                    if pair in avoid_pairs: violations.append(pair)
        else:
            choices.sort()
            idx = choices[0][3]
            if choices[0][0] > 0:
                for m in grp:
                    for e in teams[idx]:
                        pair = tuple(sorted((m.id, e.id)))
                        if pair in avoid_pairs: violations.append(pair)
        teams[idx].extend(grp)
        totals[idx] += gscore

    return teams, sorted(list(set(violations)))

def fmt_team(team: List[discord.Member], ratings: Dict[int, float], idx: int) -> str:
    lines = [f"- {m.display_name} ({int(ratings[m.id])})" for m in team]
    total = int(sum(ratings[m.id] for m in team))
    return f"**Team {idx+1}**\n" + ("\n".join(lines) if lines else "_(vide)_") + f"\n**Total**: {total}"
