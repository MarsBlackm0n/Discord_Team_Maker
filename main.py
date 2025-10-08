# main.py
import os, sys, subprocess, re, math, random, asyncio
from pathlib import Path
from typing import List, Dict, Tuple, Set, Optional
from dotenv import load_dotenv
import aiosqlite
import aiohttp
import discord
from discord import app_commands

# ========= ENV / CLIENT =========
load_dotenv(dotenv_path=Path(__file__).with_name(".env"))
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
RIOT_API_KEY = os.getenv("RIOT_API_KEY")  # peut être None -> fallback auto
OWNER_ID = int(os.getenv("OWNER_ID", "0"))  # 0 => désactivé

if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN manquant dans .env")

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

DB_PATH = Path(os.getenv("DB_PATH", str(Path(__file__).with_name("skills.db"))))

# ========= AUTH =========
def is_authorized(inter: discord.Interaction) -> bool:
    if OWNER_ID and inter.user.id == OWNER_ID:
        return True
    member = inter.user if isinstance(inter.user, discord.Member) else inter.guild and inter.guild.get_member(inter.user.id)
    return bool(member and (member.guild_permissions.administrator or member.guild_permissions.manage_guild))

# ========= DB =========
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS skills (
            user_id TEXT PRIMARY KEY,
            rating REAL NOT NULL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS lol_links (
            user_id TEXT PRIMARY KEY,
            summoner_name TEXT NOT NULL,
            region TEXT NOT NULL
        )""")
        await db.commit()

async def get_rating(user_id: int) -> Optional[float]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT rating FROM skills WHERE user_id = ?", (str(user_id),)) as cur:
            row = await cur.fetchone()
            return float(row[0]) if row else None

async def set_rating(user_id: int, rating: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO skills(user_id, rating) VALUES(?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET rating=excluded.rating",
            (str(user_id), float(rating)),
        )
        await db.commit()

async def get_linked_lol(user_id: int) -> Optional[Tuple[str,str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT summoner_name, region FROM lol_links WHERE user_id = ?", (str(user_id),)) as cur:
            row = await cur.fetchone()
            return (row[0], row[1]) if row else None

async def link_lol(user_id: int, summoner_name: str, region: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO lol_links(user_id, summoner_name, region) VALUES(?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET summoner_name=excluded.summoner_name, region=excluded.region",
            (str(user_id), summoner_name, region),
        )
        await db.commit()

# ========= RIOT API & OFFLINE RANK =========
PLATFORM_MAP = {
    "EUW": "euw1", "EUNE": "eun1", "NA": "na1", "KR": "kr",
    "BR": "br1", "JP": "jp1", "LAN": "la1", "LAS": "la2",
    "OCE": "oc1", "TR": "tr1", "RU": "ru",
}
TIER_BASE = {
    "IRON": 800, "BRONZE": 900, "SILVER": 1000, "GOLD": 1100,
    "PLATINUM": 1200, "EMERALD": 1300, "DIAMOND": 1400,
    "MASTER": 1500, "GRANDMASTER": 1600, "CHALLENGER": 1700
}
DIV_BONUS = {"IV": 0, "III": 20, "II": 40, "I": 60}

def rank_to_rating(tier: str, division: Optional[str], lp: int) -> float:
    tier = (tier or "").upper().strip()
    division = (division or "").upper().strip()
    base = TIER_BASE.get(tier, 1000)
    bonus = DIV_BONUS.get(division, 0)
    lp_bonus = min(max(int(lp or 0), 0), 100) * 0.5  # +0..+50
    return base + bonus + lp_bonus

async def fetch_lol_rating(session: aiohttp.ClientSession, region_code: str, summoner_name: str) -> Optional[float]:
    if not RIOT_API_KEY:
        return None
    headers = {"X-Riot-Token": RIOT_API_KEY}
    base = f"https://{region_code}.api.riotgames.com"
    # Summoner → id
    async with session.get(f"{base}/lol/summoner/v4/summoners/by-name/{summoner_name}", headers=headers) as r:
        if r.status != 200:
            return None
        summ = await r.json()
    summ_id = summ.get("id")
    if not summ_id:
        return None
    # Entries → choisir SoloQ si possible
    async with session.get(f"{base}/lol/league/v4/entries/by-summoner/{summ_id}", headers=headers) as r:
        if r.status != 200:
            return None
        entries = await r.json()
    chosen = next((e for e in entries if e.get("queueType") == "RANKED_SOLO_5x5"), entries[0] if entries else None)
    if not chosen:
        return None
    tier = chosen.get("tier")
    rank = chosen.get("rank")  # None pour Master+
    lp = int(chosen.get("leaguePoints", 0))
    return rank_to_rating(tier, rank, lp)

# ========= COLLECT & FALLBACK RATINGS =========
async def ensure_ratings_for_members(members: List[discord.Member], auto_import_riot: bool = True) -> Tuple[Dict[int,float], List[discord.Member], List[discord.Member]]:
    """
    Retourne:
      ratings: {user_id: rating}
      used_default: membres sans rating (utiliseront 1000 par défaut)
      imported_from_riot: membres importés via Riot pendant l'appel
    """
    ratings: Dict[int, float] = {}
    used_default: List[discord.Member] = []
    imported_from_riot: List[discord.Member] = []

    # 1) d'abord DB
    for m in members:
        r = await get_rating(m.id)
        if r is not None:
            ratings[m.id] = r

    # 2) puis Riot (si demandé et possible) pour ceux qui n'ont pas encore de rating
    if auto_import_riot and RIOT_API_KEY:
        async with aiohttp.ClientSession() as session:
            for m in members:
                if m.id in ratings:
                    continue
                link = await get_linked_lol(m.id)
                if not link:
                    continue
                summoner, region_code = link
                rr = await fetch_lol_rating(session, region_code, summoner)
                if rr is not None:
                    ratings[m.id] = rr
                    await set_rating(m.id, rr)
                    imported_from_riot.append(m)

    # 3) défaut 1000 pour les restants (sans écrire en DB)
    for m in members:
        if m.id not in ratings:
            ratings[m.id] = 1000.0
            used_default.append(m)

    return ratings, used_default, imported_from_riot

# ========= BUILD TEAMS =========
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
    groups: List[List[discord.Member]] = []
    used: Set[int] = set()
    if with_groups_text:
        chunks = [c.strip() for c in with_groups_text.split("|") if c.strip()]
        for ch in chunks:
            grp = []
            for m in parse_mentions(guild, ch):
                if m.id in allowed and m.id not in used:
                    used.add(m.id); grp.append(allowed[m.id])
            if grp:
                groups.append(grp)
    for m in members:
        if m.id not in used:
            groups.append([m])
    return groups

def parse_avoid_pairs(guild: discord.Guild, avoid_text: str) -> Set[Tuple[int,int]]:
    pairs: Set[Tuple[int,int]] = set()
    if not avoid_text:
        return pairs
    for p in [p.strip() for p in avoid_text.split(";") if p.strip()]:
        ms = parse_mentions(guild, p)
        if len(ms) >= 2:
            a, b = ms[0].id, ms[1].id
            if a != b:
                pairs.add(tuple(sorted((a,b))))
    return pairs

def split_random(members: List[discord.Member], k: int, sizes: List[int]) -> List[List[discord.Member]]:
    arr = members[:]
    random.shuffle(arr)
    teams = [[] for _ in range(k)]
    caps = sizes[:]
    i = 0
    for m in arr:
        # place m dans la prochaine équipe avec de la place
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
) -> Tuple[List[List[discord.Member]], List[Tuple[int,int]]]:
    # empaqueter les groupes + trier par score desc
    units = [(grp, sum(ratings[m.id] for m in grp)) for grp in with_groups]
    units.sort(key=lambda x: x[1], reverse=True)

    teams: List[List[discord.Member]] = [[] for _ in range(k)]
    totals = [0.0]*k
    caps = sizes[:]
    violations: List[Tuple[int,int]] = []

    def conflicts(grp: List[discord.Member], team: List[discord.Member]) -> int:
        s, tids = 0, [m.id for m in team]
        for m in grp:
            for tid in tids:
                if tuple(sorted((m.id, tid))) in avoid_pairs:
                    s += 1
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

# ========= VOICE SUPPORT =========
TEMP_CHANNELS: Dict[int, List[int]] = {}  # guild_id -> [channel_ids]

async def create_and_move_voice(inter: discord.Interaction, teams: List[List[discord.Member]], sizes: List[int], ttl_minutes: int = 60):
    guild = inter.guild
    if not guild:
        return
    parent = None
    author = inter.user if isinstance(inter.user, discord.Member) else guild.get_member(inter.user.id)
    if isinstance(author, discord.Member) and author.voice and author.voice.channel and author.voice.channel.category:
        parent = author.voice.channel.category

    created = []
    for i, team in enumerate(teams, start=1):
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

# ========= EVENTS =========

@client.event
async def on_ready():
    await init_db()
    await tree.sync()
    print(f"✅ Connecté en tant que {client.user} — slash prêts. DB: {DB_PATH}")


# ========= COMMANDES =========
@tree.command(name="setskill", description="Définir un rating manuel pour un joueur.")
@app_commands.describe(user="Membre", rating="Score (ex: 1200)")
async def setskill_cmd(inter: discord.Interaction, user: discord.Member, rating: float):
    await set_rating(user.id, rating)
    await inter.response.send_message(f"✅ Niveau de **{user.display_name}** défini à **{int(rating)}**.", ephemeral=True)

# OFFLINE rank → rating (sans Riot)
TIER_CHOICES = [app_commands.Choice(name=t.title(), value=t) for t in TIER_BASE.keys()]
DIV_CHOICES = [app_commands.Choice(name=d, value=d) for d in ["I","II","III","IV"]]

@tree.command(name="setrank", description="Définir le rang LoL (offline) d'un joueur pour estimer son rating (sans Riot API).")
@app_commands.describe(user="Membre", tier="Palier (ex: Gold, Diamond...)", division="Division (I/II/III/IV) ou vide si Master+",
                       lp="League Points (0-100)")
@app_commands.choices(tier=TIER_CHOICES, division=DIV_CHOICES)
async def setrank_cmd(inter: discord.Interaction, user: discord.Member, tier: app_commands.Choice[str], division: Optional[app_commands.Choice[str]], lp: int = 0):
    r = rank_to_rating(tier.value, division.value if division else None, lp)
    await set_rating(user.id, r)
    div_txt = division.value if division else "-"
    await inter.response.send_message(
        f"✅ Rang défini pour **{user.display_name}** → {tier.name} {div_txt} {lp} LP → rating **{int(r)}**.",
        ephemeral=True
    )

# ========= LISTE DES RANGS (BDD) =========
SCOPE_CHOICES = [
    app_commands.Choice(name="Auto (vocal si possible)", value="auto"),
    app_commands.Choice(name="Salon vocal uniquement", value="voice"),
    app_commands.Choice(name="Serveur entier", value="server"),
]
SORT_CHOICES = [
    app_commands.Choice(name="Rating décroissant", value="rating_desc"),
    app_commands.Choice(name="Rating croissant", value="rating_asc"),
    app_commands.Choice(name="Nom (A→Z)", value="name"),
]

async def _fetch_all_ratings_and_links() -> Tuple[List[Tuple[int, float]], Set[int]]:
    """Retourne (liste (user_id, rating), set(user_id liés LoL))."""
    rows: List[Tuple[int, float]] = []
    linked: Set[int] = set()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, rating FROM skills") as cur:
            async for uid, rating in cur:
                try:
                    rows.append((int(uid), float(rating)))
                except Exception:
                    # ignore lignes corrompues
                    pass
        async with db.execute("SELECT user_id FROM lol_links") as cur:
            async for (uid,) in cur:
                try:
                    linked.add(int(uid))
                except Exception:
                    pass
    return rows, linked

def _format_member_name(guild: discord.Guild, uid: int) -> Tuple[str, bool]:
    """Retourne (nom_affiché, present_dans_le_serveur)."""
    m = guild.get_member(uid)
    if m:
        return m.display_name, True
    return f"(id:{uid})", False

@tree.command(name="ranks", description="Lister les ratings enregistrés en BDD (skills).")
@app_commands.describe(
    scope="Portée : auto (vocal si possible), vocal, ou serveur",
    sort="Tri des résultats",
    limit="Nombre max de lignes (5–100)"
)
@app_commands.choices(scope=SCOPE_CHOICES, sort=SORT_CHOICES)
async def ranks_cmd(
    inter: discord.Interaction,
    scope: Optional[app_commands.Choice[str]] = None,
    sort: Optional[app_commands.Choice[str]] = None,
    limit: int = 25
):
    await inter.response.defer(ephemeral=True, thinking=True)

    # paramètres
    scope_val = (scope.value if scope else "auto").lower()
    sort_val = (sort.value if sort else "rating_desc").lower()
    limit = max(5, min(100, int(limit)))

    guild = inter.guild
    if not guild:
        await inter.followup.send("❌ Cette commande ne peut être utilisée qu'en serveur.", ephemeral=True)
        return

    all_rows, linked = await _fetch_all_ratings_and_links()
    if not all_rows:
        await inter.followup.send("🗒️ Aucune donnée de rating n'est enregistrée pour le moment.", ephemeral=True)
        return

    # Détermine la portée (ensemble de user_ids autorisés)
    allowed_ids: Optional[Set[int]] = None
    use_vocal = False
    author = inter.user if isinstance(inter.user, discord.Member) else guild.get_member(inter.user.id)
    if scope_val == "voice" or (scope_val == "auto" and isinstance(author, discord.Member) and author.voice and author.voice.channel):
        use_vocal = True
        if isinstance(author, discord.Member) and author.voice and author.voice.channel:
            allowed_ids = {m.id for m in author.voice.channel.members if not m.bot}
        else:
            # Pas en vocal → fallback serveur
            allowed_ids = None

    # Filtrage par portée
    filtered = []
    for uid, rating in all_rows:
        if allowed_ids is not None and uid not in allowed_ids:
            continue
        filtered.append((uid, rating))

    if not filtered:
        if use_vocal:
            await inter.followup.send("🔇 Aucun joueur du **salon vocal** n'a de rating enregistré.", ephemeral=True)
        else:
            await inter.followup.send("🗒️ Aucun rating correspondant à la portée sélectionnée.", ephemeral=True)
        return

    # Tri
    if sort_val == "rating_asc":
        filtered.sort(key=lambda x: x[1])
    elif sort_val == "name":
        filtered.sort(key=lambda x: _format_member_name(guild, x[0])[0].lower())
    else:  # rating_desc
        filtered.sort(key=lambda x: x[1], reverse=True)

    # Limite
    total = len(filtered)
    filtered = filtered[:limit]

    # Construction du rendu
    lines = []
    for i, (uid, rating) in enumerate(filtered, start=1):
        name, present = _format_member_name(guild, uid)
        link_mark = " 🔗" if uid in linked else ""
        out_server = " *(hors serveur)*" if not present else ""
        lines.append(f"{i}. {name} — **{int(rating)}**{link_mark}{out_server}")

    scope_label = "Salon vocal" if use_vocal else "Serveur"
    title = f"📒 Rangs enregistrés — {scope_label}"
    desc = "\n".join(lines) if lines else "_(aucune entrée)_"

    embed = discord.Embed(title=title, description=desc, color=discord.Color.green())
    footer_bits = [f"{len(filtered)}/{total} affichés", f"Tri: {sort_val.replace('_', ' ')}"]
    if use_vocal:
        footer_bits.append("Portée: vocal")
    else:
        footer_bits.append("Portée: serveur")
    embed.set_footer(text=" • ".join(footer_bits))

    await inter.followup.send(embed=embed, ephemeral=True)


@tree.command(name="linklol", description="Lier un compte LoL et initialiser le rating depuis le rang (Riot API).")
@app_commands.describe(user="Membre", summoner="Pseudo LoL exact", region="Plateforme: EUW, EUNE, NA, KR, BR, JP, LAN, LAS, OCE, TR, RU")
async def linklol_cmd(inter: discord.Interaction, user: discord.Member, summoner: str, region: str):
    await inter.response.defer(ephemeral=True, thinking=True)
    code = PLATFORM_MAP.get(region.upper())
    if not code:
        await inter.followup.send("❌ Région invalide. Ex: EUW, EUNE, NA, KR, BR, JP, LAN, LAS, OCE, TR, RU")
        return
    if not RIOT_API_KEY:
        await link_lol(user.id, summoner, code)
        await inter.followup.send("ℹ️ Clé Riot absente → lien enregistré, mais import différé. Utilise `/setrank` ou `/setskill` en attendant.")
        return
    async with aiohttp.ClientSession() as session:
        rating = await fetch_lol_rating(session, code, summoner)
    await link_lol(user.id, summoner, code)
    if rating is None:
        await inter.followup.send("⚠️ Lien enregistré, mais impossible de récupérer le rang maintenant (clé manquante/expirée, pseudo, pas de ranked…). Utilise `/setrank` ou `/setskill`.")
    else:
        await set_rating(user.id, rating)
        await inter.followup.send(f"✅ **{user.display_name}** lié à **{summoner}** ({region}) → rating **{int(rating)}**.")

@tree.command(name="team", description="Créer des équipes (équilibrées ou aléatoires) avec options avancées & fallback rating.")
@app_commands.describe(
    mode="balanced ou random (défaut: balanced)",
    team_count="Nombre d'équipes (2–6, défaut 2)",
    sizes='Tailles fixées, ex: "3/3/2" (somme = nb joueurs)',
    with_groups='Groupes ensemble, ex: "@A @B | @C @D"',
    avoid_pairs='Paires à séparer, ex: "@A @B ; @C @D"',
    members="(Optionnel) liste de @mentions si pas de vocal",
    create_voice="Créer des salons vocaux Team 1..K et déplacer les joueurs",
    channel_ttl="Durée de vie des salons vocaux (minutes, défaut 90)",
    auto_import_riot="Importer automatiquement via Riot pour les joueurs liés si possible (défaut: true)"
)
async def team_cmd(
    inter: discord.Interaction,
    mode: str = "balanced",
    team_count: int = 2,
    sizes: str = "",
    with_groups: str = "",
    avoid_pairs: str = "",
    members: str = "",
    create_voice: bool = False,
    channel_ttl: int = 90,
    auto_import_riot: bool = True
):
    await inter.response.defer(thinking=True)

    if team_count < 2 or team_count > 6:
        await inter.followup.send("❌ team_count doit être entre 2 et 6.")
        return

    guild = inter.guild
    author = inter.user if isinstance(inter.user, discord.Member) else guild.get_member(inter.user.id)

    # Collecte joueurs
    selected: List[discord.Member] = []
    if members:
        selected = parse_mentions(guild, members)
    else:
        if isinstance(author, discord.Member) and author.voice and author.voice.channel:
            selected = [m for m in author.voice.channel.members if not m.bot]
        else:
            await inter.followup.send("❌ Pas de liste fournie et tu n'es pas en vocal.")
            return

    if len(selected) < team_count:
        await inter.followup.send(f"❌ Pas assez de joueurs pour {team_count} équipes.")
        return

    # Ratings avec fallback (DB → Riot si possible → défaut 1000)
    ratings, used_default, imported_from_riot = await ensure_ratings_for_members(selected, auto_import_riot=auto_import_riot)

    # Tailles & contraintes
    sizes_list = parse_sizes(sizes, len(selected), team_count)
    with_groups_list = group_by_with_constraints(guild, selected, with_groups) if with_groups else [[m] for m in selected]
    avoid_pairs_set = parse_avoid_pairs(guild, avoid_pairs)

    # Création des équipes
    if mode.lower() == "random":
        teams = split_random(selected, team_count, sizes_list)
        violations = []
    else:
        teams, violations = balance_k_teams_with_constraints(selected, ratings, team_count, sizes_list, with_groups_list, avoid_pairs_set)

    # Affichage
    embed = discord.Embed(title="🎲 Team Builder", color=discord.Color.blurple())
    for idx, team_list in enumerate(teams):
        embed.add_field(name=f"Team {idx+1}", value=fmt_team(team_list, ratings, idx), inline=True)
    totals = [int(sum(ratings[m.id] for m in t)) for t in teams]
    spread = max(totals) - min(totals) if totals else 0
    footer = f"Mode: {'Équilibré' if mode.lower()!='random' else 'Aléatoire'} • Δ total: {spread}"
    if violations:
        footer += f" • Contraintes violées: {len(violations)}"
    embed.set_footer(text=footer)
    await inter.followup.send(embed=embed)

    # Info éphemère: qui a été importé Riot / qui est par défaut 1000
    notes = []
    if imported_from_riot:
        notes.append("🏷️ Import Riot: " + ", ".join(f"{m.display_name}" for m in imported_from_riot))
    if used_default:
        notes.append("⚠️ Rating par défaut (1000): " + ", ".join(f"{m.display_name}" for m in used_default) +
                     "\n→ utilise `/setrank` (offline) ou `/setskill`, ou lie avec `/linklol`.")
    if notes:
        try:
            await inter.followup.send("\n".join(notes), ephemeral=True)
        except Exception:
            pass

    # Création salons + move
    if create_voice:
        try:
            await create_and_move_voice(inter, teams, sizes_list, ttl_minutes=max(channel_ttl, 1))
        except discord.Forbidden:
            await inter.followup.send("⚠️ Permissions manquantes (Manage Channels / Move Members).")

@tree.command(name="disbandteams", description="Supprimer les salons vocaux d'équipe temporaires.")
async def disbandteams_cmd(inter: discord.Interaction):
    guild = inter.guild
    if not guild:
        await inter.response.send_message("❌ Guild inconnue.", ephemeral=True)
        return
    ids = TEMP_CHANNELS.pop(guild.id, [])
    count = 0
    for cid in ids:
        ch = guild.get_channel(cid)
        if ch:
            try:
                await ch.delete(reason="TeamBuilder manual cleanup"); count += 1
            except discord.Forbidden:
                pass
    await inter.response.send_message(f"🧹 Salons supprimés: {count}", ephemeral=True)

# Admin: shutdown / restart
@tree.command(name="shutdown", description="Arrêter le bot (owner/admin).")
async def shutdown_cmd(inter: discord.Interaction):
    if not is_authorized(inter):
        await inter.response.send_message("⛔ Autorisation refusée.", ephemeral=True); return
    await inter.response.send_message("🛑 Extinction…", ephemeral=True)
    await asyncio.sleep(0.2)
    await client.close()

@tree.command(name="restart", description="Redémarrer le bot (owner/admin).")
async def restart_cmd(inter: discord.Interaction):
    if not is_authorized(inter):
        await inter.response.send_message("⛔ Autorisation refusée.", ephemeral=True); return
    await inter.response.send_message("🔄 Redémarrage…", ephemeral=True)
    await asyncio.sleep(0.2)
    if RESTART_MODE == "manager":
        # ✅ Sur Railway: on se contente de quitter proprement
        await client.close()
        os._exit(0)  # Railway relance le service (restart policy)
    else:
        # Mode local: relance auto du script
        try:
            python = sys.executable
            subprocess.Popen([python] + sys.argv, cwd=str(Path(__file__).parent))
        finally:
            await client.close()
            os._exit(0)

# Optionnel: récupérer son User ID vite
@tree.command(name="whoami", description="Affiche ton User ID.")
async def whoami_cmd(inter: discord.Interaction):
    await inter.response.send_message(f"🪪 Ton User ID : `{inter.user.id}`", ephemeral=True)

client.run(TOKEN)
