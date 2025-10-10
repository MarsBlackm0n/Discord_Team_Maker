# app/cogs/help.py
from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands

# ──────────────────────────────────────────────────────────────────────────────
# Contenu d'aide (texte statique, clair et maintenable)
# ──────────────────────────────────────────────────────────────────────────────

HELP_SECTIONS = {
    "🧩 Équipes (Team Builder)": [
        {
            "name": "/team",
            "desc": (
                "Créer des équipes **équilibrées** (par défaut) ou **aléatoires** à partir des joueurs "
                "du salon vocal de l'auteur (ou d'une liste de mentions). "
                "Options clés : `mode` (balanced/random), `team_count`, `sizes` (ex: \"3/3/2\"), "
                "`with_groups` (garder ensemble), `avoid_pairs` (séparer), `members` (mentions), "
                "`create_voice`, `channel_ttl`, `auto_import_riot`.\n"
                "💾 Sauvegarde un *snapshot* (dernière config) réutilisé par `/go`, `/teamroll`, `/tournament`."
            ),
            "examples": [
                "/team",
                "/team mode:random team_count:3",
                "/team sizes:\"3/3/2\" with_groups:\"@A @B | @C @D\" avoid_pairs:\"@X @Y\"",
                "/team members:\"@A @B @C @D @E @F\" create_voice:true channel_ttl:60",
            ],
        },
        {
            "name": "/teamroll",
            "desc": (
                "Génère une nouvelle combinaison **inédite** d'équipes en évitant les paires déjà vues "
                "sur la **session** en cours. Fallback: si pas de mentions et pas de vocal, reprend la "
                "dernière config `/team` (mêmes joueurs et tailles).\n"
                "🎛️ Paramètres utiles : `session` (si vide → `auto-YYYYMMDD`), `attempts`, `mode` (balanced/random). "
                "📌 Un bouton **Reroll** est ajouté sous l'embed pour rejouer avec les mêmes paramètres."
            ),
            "examples": [
                "/teamroll",  # session auto du jour
                "/teamroll session:\"soiree-ven\" team_count:4 attempts:500",
            ],
        },
        {
            "name": "/team_last",
            "desc": "Affiche la **dernière configuration** d'équipes enregistrée pour le serveur.",
        },
        {
            "name": "/go",
            "desc": (
                "Crée/réutilise les salons vocaux **Team 1..K** et **déplace** les joueurs selon la **dernière config**. "
                "Option : `channel_ttl` (durée de vie des salons)."
            ),
        },
        {
            "name": "/disbandteams",
            "desc": "Supprime les **salons vocaux temporaires** créés via `/team create_voice:true` ou `/go`.",
        },
        {
            "name": "/move",
            "desc": "Déplace rapidement des joueurs (outil utilitaire, si présent dans ton projet).",
        },
    ],
    "♻️ Historique & Sessions TeamRoll": [
        {
            "name": "/teamroll_end",
            "desc": "Termine/efface une **session** de roll (réinitialise l’historique des **paires** pour cette session).",
            "examples": ["/teamroll_end session:\"soiree-ven\""],
        },
        {
            "name": "/teamroll_reset",
            "desc": (
                "Réinitialise l’historique des **compositions** (signatures fortes) pour une session. "
                "Utile si *toutes les compositions possibles* ont déjà été vues et que tu veux recommencer.\n"
                "Paramètres : `session` (si vide, tente de reprendre celle du snapshot), "
                "`for_current_snapshot` (limite le reset au set/tailles de la dernière config)."
            ),
            "examples": [
                "/teamroll_reset session:\"auto-20251010\"",
                "/teamroll_reset session:\"auto-20251010\" for_current_snapshot:true",
            ],
        },
    ],
    "🏆 Tournoi (Single Elimination)": [
        {
            "name": "/tournament create",
            "desc": "Crée un **tournoi** (état `setup`). Puis ajoute des participants et démarre.",
        },
        {
            "name": "/tournament add",
            "desc": "Ajoute des participants (mentions ou membres du **vocal** de l'auteur). Seed par rating décroissant.",
        },
        {
            "name": "/tournament start",
            "desc": "Démarre le bracket **Single Elimination** (best-of `best_of`, par défaut 1).",
        },
        {
            "name": "/tournament view",
            "desc": "Affiche l'état actuel du bracket (par **rounds**).",
        },
        {
            "name": "/tournament report",
            "desc": "Enregistre le **résultat** d’un match et **propage** le vainqueur dans le match suivant.",
        },
        {
            "name": "/tournament cancel",
            "desc": "Annule le tournoi actif.",
        },
        {
            "name": "/tournament_use_last",
            "desc": "Ajoute (ou crée puis ajoute) les joueurs de la **dernière config d'équipes** au tournoi actif.",
        },
    ],
    "🛡️ Arena LoL (2v2, classement individuel)": [
        {
            "name": "/arena start",
            "desc": (
                "Lance un tournoi **Arena** basé sur des **duos** qui changent à chaque round (round-robin : chacun joue une fois "
                "avec chaque autre coéquipier). Prend les joueurs depuis `members`, sinon **dernier /team**, sinon **vocal**.\n"
                "Paramètre : `rounds` (facultatif, par défaut `n-1`)."
            ),
        },
        {
            "name": "/arena round",
            "desc": (
                "Affiche le **round courant** avec la liste des **Duos 1..N** et un **bouton “📝 Reporter”** "
                "qui ouvre un **Modal** avec 3–4 champs pré-remplis."
            ),
        },
        {
            "name": "/arena report",
            "desc": (
                "Reporter un round avec un format **très court** : `#1:2 | 3:6 | @A @B:7`.\n"
                "• `#k:r` = le **Duo k** a fait **Top r** (1 à 8). "
                "• `@A @B:r` = le duo explicite a fait Top r.\n"
                "➡️ **Report partiel** supporté (vous êtes 6 IRL → ne reporter que vos 3 duos). "
                "Barème fixe par joueur : Top1=8, Top2=7, …, Top8=1."
            ),
            "examples": [
                "/arena report placements:\"#1:2 | #3:6 | @Iroshi @GrandCoquin:1\"",
            ],
        },
        {
            "name": "/arena status",
            "desc": "Affiche le **classement** provisoire et l’état (round X/Y).",
        },
        {
            "name": "/arena stop",
            "desc": "Termine le tournoi Arena et affiche le **podium** 🏆.",
        },
        {
            "name": "/arena cancel",
            "desc": "Annule le tournoi Arena en cours.",
        },
    ],
    "📊 Ratings & LoL": [
        {
            "name": "/setskill",
            "desc": "Fixe un **rating** manuel pour un joueur. Exemple : `/setskill user:@Alice rating:1320`.",
        },
        {
            "name": "/setrank",
            "desc": (
                "Définit un **rang LoL (offline)** (sans clé Riot) et calcule le rating. "
                "Paramètres : `tier` (Gold, Emerald…), `division` (I/II/III/IV, vide si Master+), `lp` (0–100)."
            ),
            "examples": ["/setrank user:@Alice tier:Emerald division:III lp:9"],
        },
        {
            "name": "/linklol",
            "desc": (
                "Lie un pseudo LoL et (si une **RIOT_API_KEY** est configurée) importe le rang **SoloQ** pour calculer le rating. "
                "Régions : `EUW, EUNE, NA, KR, BR, JP, LAN, LAS, OCE, TR, RU`."
            ),
        },
        {
            "name": "/ranks",
            "desc": (
                "Affiche les **ratings** stockés en BDD, avec le **rang LoL** (_Emerald III 9 LP_) s’il est connu. "
                "Options : `scope` (auto/voice/server), `sort` (rating_desc/rating_asc/name), `limit` (5–100)."
            ),
            "examples": ["/ranks", "/ranks scope:voice sort:name limit:50"],
        },
    ],
    "🛠️ Admin / Owner": [
        {"name": "/whoami", "desc": "Affiche votre **User ID** (pratique pour `OWNER_ID`)."},
        {"name": "/resync", "desc": "Resynchronise les **commandes** (utile après un déploiement)."},
        {"name": "/resyncglobal", "desc": "Resynchronise les **commandes globales**."},
        {"name": "/backupdb", "desc": "Sauvegarde la base de données."},
        {"name": "/exportcsv", "desc": "Export CSV (ratings, participants, etc. selon implémentation)."},
        {"name": "/restart", "desc": "Redémarre le bot (la plateforme relance le process)."},
        {"name": "/shutdown", "desc": "Arrête le bot (owner/admin)."},
    ],
}

# Aide ciblée par commande (texte concis + exemples)
COMMAND_DETAILS = {
    # Teams
    "team": {
        "title": "ℹ️ /team",
        "desc": (
            "Crée des équipes **équilibrées** (par défaut) ou **aléatoires**. "
            "Source : **vocal** de l'auteur ou `members:` (mentions). "
            "Peut créer les salons vocaux Team 1..K. "
            "Sauvegarde un *snapshot* utilisable par `/go`, `/teamroll`, `/tournament`."
        ),
        "examples": [
            "/team",
            "/team mode:random team_count:3",
            "/team sizes:\"3/3/2\" with_groups:\"@A @B | @C @D\" avoid_pairs:\"@X @Y\" create_voice:true",
        ],
    },
    "teamroll": {
        "title": "ℹ️ /teamroll",
        "desc": (
            "Reroll basé sur une **session** (si vide → `auto-YYYYMMDD`). Évite les paires répétées et les **compositions déjà vues**. "
            "Bouton 🎲 pour relancer instantanément. "
            "Réutilise le **dernier /team** si pas de mentions/vocal."
        ),
        "examples": [
            "/teamroll", "/teamroll session:\"soiree-ven\" team_count:4 attempts:500",
        ],
    },
    "team_last": {
        "title": "ℹ️ /team_last",
        "desc": "Affiche la **dernière config** d'équipes (joueurs + ratings + totaux).",
    },
    "go": {
        "title": "ℹ️ /go",
        "desc": "Crée/réutilise les **salons vocaux** Team 1..K et **déplace** les joueurs selon la dernière config.",
    },
    "disbandteams": {
        "title": "ℹ️ /disbandteams",
        "desc": "Nettoie les **salons** d'équipes temporaires.",
    },
    "teamroll_end": {
        "title": "ℹ️ /teamroll_end",
        "desc": "Termine/efface une **session** de roll (réinitialise l’historique des **paires**).",
        "examples": ["/teamroll_end session:\"auto-20251010\""],
    },
    "teamroll_reset": {
        "title": "ℹ️ /teamroll_reset",
        "desc": (
            "Réinitialise l’historique des **compositions** (signatures) pour une session. "
            "Option `for_current_snapshot:true` pour limiter au set/tailles du dernier /team."
        ),
        "examples": [
            "/teamroll_reset session:\"auto-20251010\"",
            "/teamroll_reset session:\"auto-20251010\" for_current_snapshot:true",
        ],
    },

    # Tournament
    "tournament create": {"title": "ℹ️ /tournament create", "desc": "Crée un tournoi (état `setup`)."},
    "tournament add": {"title": "ℹ️ /tournament add", "desc": "Ajoute des participants (mentions ou vocal)."},
    "tournament start": {"title": "ℹ️ /tournament start", "desc": "Démarre le bracket Single Elimination."},
    "tournament view": {"title": "ℹ️ /tournament view", "desc": "Affiche l'état par rounds."},
    "tournament report": {"title": "ℹ️ /tournament report", "desc": "Enregistre le résultat d’un match."},
    "tournament cancel": {"title": "ℹ️ /tournament cancel", "desc": "Annule le tournoi actif."},
    "tournament_use_last": {"title": "ℹ️ /tournament_use_last", "desc": "Ajoute les joueurs du **dernier /team** au tournoi actif."},

    # Arena
    "arena start": {
        "title": "ℹ️ /arena start",
        "desc": (
            "Lance un tournoi **Arena (2v2)** : les duos tournent à chaque round (round-robin, par défaut `n-1` rounds). "
            "Source joueurs : `members` → **dernier /team** → **vocal**."
        ),
    },
    "arena round": {
        "title": "ℹ️ /arena round",
        "desc": "Affiche le **round courant** (Duos 1..N) + **bouton** “📝 Reporter”.",
    },
    "arena report": {
        "title": "ℹ️ /arena report",
        "desc": (
            "Reporter rapidement : `#1:2 | 3:6 | @A @B:7`. "
            "Tu peux envoyer seulement **tes** duos. "
            "Barème par joueur : **Top1=8**, **Top2=7**, …, **Top8=1**."
        ),
    },
    "arena status": {"title": "ℹ️ /arena status", "desc": "Classement provisoire + état X/Y."},
    "arena stop": {"title": "ℹ️ /arena stop", "desc": "Termine et affiche le **podium** 🏆."},
    "arena cancel": {"title": "ℹ️ /arena cancel", "desc": "Annule le tournoi Arena en cours."},

    # Ratings / LoL
    "setskill": {"title": "ℹ️ /setskill", "desc": "Fixe un **rating** manuel."},
    "setrank": {"title": "ℹ️ /setrank", "desc": "Définit un **rang LoL** (offline) pour calculer le rating."},
    "linklol": {"title": "ℹ️ /linklol", "desc": "Lie un pseudo LoL et importe le rang (si **RIOT_API_KEY**)."},
    "ranks": {"title": "ℹ️ /ranks", "desc": "Affiche les **ratings** et rangs connus."},

    # Admin
    "whoami": {"title": "ℹ️ /whoami", "desc": "Affiche votre **User ID**."},
    "resync": {"title": "ℹ️ /resync", "desc": "Resynchronise les commandes."},
    "resyncglobal": {"title": "ℹ️ /resyncglobal", "desc": "Resynchronise les commandes **globales**."},
    "backupdb": {"title": "ℹ️ /backupdb", "desc": "Sauvegarde la BDD."},
    "exportcsv": {"title": "ℹ️ /exportcsv", "desc": "Export CSV."},
    "restart": {"title": "ℹ️ /restart", "desc": "Redémarre le bot."},
    "shutdown": {"title": "ℹ️ /shutdown", "desc": "Arrête le bot."},
}

# ──────────────────────────────────────────────────────────────────────────────
# Cog /help
# ──────────────────────────────────────────────────────────────────────────────

class HelpCog(commands.Cog):
    """Aide intégrée : /help avec aperçu global + recherche d’une commande."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Afficher l’aide du bot et les exemples de workflow.")
    @app_commands.describe(command="(optionnel) nom d’une commande pour l’aide ciblée (ex: teamroll, arena report)")
    async def help(self, inter: discord.Interaction, command: str | None = None):
        """Affiche l'aide générale ou celle d'une commande spécifique."""
        embed = discord.Embed(color=discord.Color.blurple())

        if not command:
            embed.title = "📖 Guide rapide — Team Builder, Arena & Tournois"
            embed.description = (
                "Voici les principales commandes du bot, ainsi qu’un **exemple de flow typique** 👇\n\n"
                "### 💡 Flow typique (customisable)\n"
                "1️⃣ `/team` — crée les équipes (équilibrées ou aléatoires)\n"
                "2️⃣ (optionnel) `/teamroll` — mixe en évitant répétitions (bouton 🎲 pour reroll)\n"
                "3️⃣ `/go` — crée/réutilise les salons vocaux et déplace les joueurs\n"
                "4️⃣ (optionnel) `/tournament create/add/start/view` — bracket **Single Elimination**\n"
                "   ou `/arena start/round/report/status` — **Arena 2v2** avec classement individuel\n"
                "5️⃣ `/disbandteams` — nettoie les salons après la session\n\n"
                "💾 La **dernière config** d’équipes est mémorisée (utilisée par `/go`, `/teamroll`, `/tournament`, `/arena`)."
            )
            # Résumé court par catégories
            embed.add_field(
                name="👥 Équipes",
                value="`/team`  `/teamroll`  `/team_last`  `/go`  `/disbandteams`  `/move`",
                inline=False
            )
            embed.add_field(
                name="♻️ Historique TeamRoll",
                value="`/teamroll_end`  `/teamroll_reset`",
                inline=False
            )
            embed.add_field(
                name="🏆 Tournoi (SE)",
                value="`/tournament create` `add` `start` `view` `report` `cancel`  •  `/tournament_use_last`",
                inline=False
            )
            embed.add_field(
                name="🛡️ Arena (2v2)",
                value="`/arena start`  `/arena round`  `/arena report`  `/arena status`  `/arena stop`  `/arena cancel`",
                inline=False
            )
            embed.add_field(
                name="📊 Ratings & LoL",
                value="`/ranks`  `/setskill`  `/setrank`  `/linklol`",
                inline=False
            )
            embed.add_field(
                name="🛠️ Admin",
                value="`/whoami`  `/resync`  `/resyncglobal`  `/backupdb`  `/exportcsv`  `/restart`  `/shutdown`",
                inline=False
            )
            embed.set_footer(text="Astuce: `/help command:arena report` pour l’aide d’une sous-commande.")
            await inter.response.send_message(embed=embed, ephemeral=True)
            return

        # Aide ciblée
        key = command.lower().strip()
        # autoriser des clés avec espace (ex: "arena report", "tournament add")
        if key not in COMMAND_DETAILS:
            # tenter de normaliser: remplace multiples espaces par un seul
            key = " ".join(key.split())

        info = COMMAND_DETAILS.get(key)
        if not info:
            embed.title = "❓ Aide"
            embed.description = f"Aucune aide détaillée pour `{command}`."
            await inter.response.send_message(embed=embed, ephemeral=True)
            return

        embed.title = info.get("title") or f"ℹ️ /{key}"
        embed.description = info.get("desc", "")
        examples = info.get("examples", [])
        if examples:
            embed.add_field(
                name="Exemples",
                value="\n".join(f"• `{ex}`" for ex in examples),
                inline=False
            )
        await inter.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
