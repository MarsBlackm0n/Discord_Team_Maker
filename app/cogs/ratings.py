# app/cogs/ratings.py
from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands
from ..db import set_rating, fetch_all_ratings_and_links, link_lol, set_lol_rank, get_linked_lol
from ..riot import (
    PLATFORM_MAP, fetch_lol_rank_info, fetch_lol_rank_by_puuid, rank_to_rating,
    parse_riot_id, RiotKeyInvalid, RiotNotFound, RiotRateLimited, RiotApiError,
)

class RatingsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setskill", description="Définir un rating manuel pour un joueur.")
    @app_commands.describe(user="Membre", rating="Score (ex: 1200)")
    async def setskill(self, inter: discord.Interaction, user: discord.Member, rating: float):
        await set_rating(self.bot.settings.DB_PATH, user.id, rating)
        await inter.response.send_message(f"✅ Niveau de **{user.display_name}** défini à **{int(rating)}**.", ephemeral=True)

    # Choix pour /setrank
    TIER_CHOICES = [app_commands.Choice(name=t.title(), value=t) for t in ["IRON","BRONZE","SILVER","GOLD","PLATINUM","EMERALD","DIAMOND","MASTER","GRANDMASTER","CHALLENGER"]]
    DIV_CHOICES = [app_commands.Choice(name=d, value=d) for d in ["I","II","III","IV"]]

    @app_commands.command(name="setrank", description="Définir le rang LoL (offline) d'un joueur pour estimer son rating (sans Riot API).")
    @app_commands.describe(user="Membre", tier="Palier", division="I/II/III/IV (vide si Master+)", lp="0–100")
    @app_commands.choices(tier=TIER_CHOICES, division=DIV_CHOICES)
    async def setrank(self, inter: discord.Interaction, user: discord.Member, tier: app_commands.Choice[str], division: Optional[app_commands.Choice[str]], lp: int = 0):
        r = rank_to_rating(tier.value, division.value if division else None, lp)
        await set_rating(self.bot.settings.DB_PATH, user.id, r)
        await set_lol_rank(self.bot.settings.DB_PATH, user.id, source="offline", tier=tier.value, division=(division.value if division else None), lp=lp)
        div_txt = division.value if division else "-"
        await inter.response.send_message(f"✅ Rang défini pour **{user.display_name}** → {tier.name} {div_txt} {lp} LP → rating **{int(r)}**.", ephemeral=True)

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

    @app_commands.command(name="ranks", description="Lister les ratings (BDD) + rang LoL si dispo.")
    @app_commands.describe(scope="auto/voice/server", sort="rating_desc/rating_asc/name", limit="5–100")
    @app_commands.choices(scope=SCOPE_CHOICES, sort=SORT_CHOICES)
    async def ranks(self, inter: discord.Interaction, scope: Optional[app_commands.Choice[str]] = None, sort: Optional[app_commands.Choice[str]] = None, limit: int = 25):
        await inter.response.defer(ephemeral=True, thinking=True)
        guild = inter.guild
        if not guild:
            await inter.followup.send("❌ À utiliser en serveur.", ephemeral=True); return

        scope_val = (scope.value if scope else "auto").lower()
        sort_val = (sort.value if sort else "rating_desc").lower()
        limit = max(5, min(100, int(limit)))

        all_rows, linked, ranks_map = await fetch_all_ratings_and_links(self.bot.settings.DB_PATH)

        allowed_ids = None
        use_vocal = False
        author = guild.get_member(inter.user.id)
        if scope_val == "voice" or (scope_val == "auto" and author and author.voice and author.voice.channel):
            use_vocal = True
            if author and author.voice and author.voice.channel:
                allowed_ids = {m.id for m in author.voice.channel.members if not m.bot}

        filtered = []
        for uid, rating in all_rows:
            if allowed_ids is not None and uid not in allowed_ids: continue
            filtered.append((uid, rating))
        if not filtered:
            await inter.followup.send("🔇 Aucun joueur correspondant dans la portée choisie." if use_vocal else "🗒️ Aucune donnée à afficher.", ephemeral=True)
            return

        if sort_val == "rating_asc":
            filtered.sort(key=lambda x: x[1])
        elif sort_val == "name":
            filtered.sort(key=lambda x: (guild.get_member(x[0]).display_name if guild.get_member(x[0]) else f"id:{x[0]}").lower())
        else:
            filtered.sort(key=lambda x: x[1], reverse=True)

        total = len(filtered)
        filtered = filtered[:limit]

        lines = []
        for i, (uid, rating) in enumerate(filtered, start=1):
            m = guild.get_member(uid)
            name = m.display_name if m else f"(id:{uid})"
            link_mark = " 🔗" if uid in linked else ""
            rank_txt = ""
            if uid in ranks_map:
                tier, division, lp = ranks_map[uid]
                pretty = f"{tier.title()}{(' ' + division) if division else ''} {lp} LP".strip()
                rank_txt = f" · _{pretty}_"
            out_server = "" if m else " *(hors serveur)*"
            lines.append(f"{i}. {name} — **{int(rating)}**{link_mark}{rank_txt}{out_server}")

        title = f"📒 Rangs enregistrés — {'Salon vocal' if use_vocal else 'Serveur'}"
        desc = "\n".join(lines) if lines else "_(aucune entrée)_"
        embed = discord.Embed(title=title, description=desc, color=discord.Color.green())
        embed.set_footer(text=" • ".join([f"{len(filtered)}/{total} affichés", f"Tri: {sort_val.replace('_',' ')}", f"Portée: {'vocal' if use_vocal else 'serveur'}"]))
        await inter.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="linklol", description="Lier un compte LoL (Riot ID) + import du rang si clé Riot.")
    @app_commands.describe(user="Membre", riot_id="Riot ID complet, ex: Pseudo#EUW", region="EUW/EUNE/NA/KR/BR/JP/LAN/LAS/OCE/TR/RU")
    async def linklol(self, inter: discord.Interaction, user: discord.Member, riot_id: str, region: str):
        await inter.response.defer(ephemeral=False, thinking=True)
        code = PLATFORM_MAP.get(region.upper())
        if not code:
            await inter.followup.send("❌ Région invalide.")
            return
        try:
            game_name, tag_line = parse_riot_id(riot_id)
        except ValueError as exc:
            await inter.followup.send(f"❌ {exc}")
            return

        if not self.bot.settings.RIOT_API_KEY:
            await link_lol(self.bot.settings.DB_PATH, user.id, game_name, tag_line, code)
            await inter.followup.send("ℹ️ Lien enregistré. Pas de clé Riot configurée → utilise `/setrank` ou `/setskill`.")
            return

        try:
            info = await fetch_lol_rank_info(self.bot.settings.RIOT_API_KEY, code, game_name, tag_line)
        except RiotKeyInvalid:
            await inter.followup.send("⛔ Clé Riot invalide ou expirée côté bot. Préviens l'admin (variable `RIOT_API_KEY` sur Railway).")
            return
        except RiotNotFound:
            await inter.followup.send(f"❌ Riot ID **{riot_id}** introuvable sur la région **{region}**. Vérifie l'orthographe et le tag.")
            return
        except RiotRateLimited as exc:
            await inter.followup.send(f"⏳ Riot API rate limitée, réessaie dans ~{int(exc.retry_after)}s.")
            return
        except RiotApiError as exc:
            await inter.followup.send(f"⚠️ Erreur Riot API ({exc.status}), réessaie plus tard.")
            return

        if info is None:
            await link_lol(self.bot.settings.DB_PATH, user.id, game_name, tag_line, code)
            await inter.followup.send(f"✅ Lien enregistré pour **{riot_id}** ({region}). Pas de partie classée solo/duo trouvée cette saison.")
            return

        tier, division, lp, rating, puuid = info
        await link_lol(self.bot.settings.DB_PATH, user.id, game_name, tag_line, code, puuid)
        await set_rating(self.bot.settings.DB_PATH, user.id, rating)
        await set_lol_rank(self.bot.settings.DB_PATH, user.id, source="riot", tier=tier, division=division, lp=lp)
        div_txt = f" {division}" if division else ""
        await inter.followup.send(f"✅ **{user.display_name}** lié à **{riot_id}** ({region}) → **{int(rating)}** • _{tier.title()}{div_txt} {lp} LP_.", ephemeral=True)

    @app_commands.command(name="syncrank", description="Re-synchroniser ton rang LoL (ou celui d'un autre) depuis Riot, sans ressaisir ton Riot ID.")
    @app_commands.describe(user="Autre joueur (Gérer le serveur requis)")
    async def syncrank(self, inter: discord.Interaction, user: Optional[discord.Member] = None):
        target = user or inter.user
        if target.id != inter.user.id and not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("⛔ Gérer le serveur est requis pour resynchroniser un autre joueur.", ephemeral=True)
            return
        if not self.bot.settings.RIOT_API_KEY:
            await inter.response.send_message("❌ Pas de clé Riot configurée sur le bot.", ephemeral=True)
            return
        link = await get_linked_lol(self.bot.settings.DB_PATH, target.id)
        if not link:
            await inter.response.send_message(f"❌ {target.display_name} n'a pas de compte lié. Utilise `/linklol` d'abord.", ephemeral=True)
            return
        game_name, region, tag_line, puuid = link
        if not puuid:
            await inter.response.send_message("❌ Lien incomplet (ancien format). Relance `/linklol` pour ce joueur.", ephemeral=True)
            return

        await inter.response.defer(ephemeral=True, thinking=True)
        try:
            rank = await fetch_lol_rank_by_puuid(self.bot.settings.RIOT_API_KEY, region, puuid)
        except RiotKeyInvalid:
            await inter.followup.send("⛔ Clé Riot invalide ou expirée côté bot. Préviens l'admin.", ephemeral=True)
            return
        except RiotRateLimited as exc:
            await inter.followup.send(f"⏳ Riot API rate limitée, réessaie dans ~{int(exc.retry_after)}s.", ephemeral=True)
            return
        except RiotApiError as exc:
            await inter.followup.send(f"⚠️ Erreur Riot API ({exc.status}), réessaie plus tard.", ephemeral=True)
            return

        if rank is None:
            await inter.followup.send(f"ℹ️ {target.display_name} : pas de partie classée solo/duo trouvée cette saison.", ephemeral=True)
            return

        tier, division, lp, rating = rank
        await set_rating(self.bot.settings.DB_PATH, target.id, rating)
        await set_lol_rank(self.bot.settings.DB_PATH, target.id, source="riot", tier=tier, division=division, lp=lp)
        div_txt = f" {division}" if division else ""
        await inter.followup.send(f"✅ **{target.display_name}** → **{int(rating)}** • _{tier.title()}{div_txt} {lp} LP_.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(RatingsCog(bot))
