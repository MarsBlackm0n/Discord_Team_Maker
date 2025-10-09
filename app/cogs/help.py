# app/cogs/help.py
import discord
from discord import app_commands
from discord.ext import commands

# Contenu d'aide (texte statique, clair et maintenable)
HELP_SECTIONS = {
    "🧩 Formation d'équipes": [
        {
            "name": "/team",
            "desc": (
                "Créer des équipes **équilibrées** (par défaut) ou **aléatoires** à partir des joueurs "
                "du salon vocal de l'auteur (ou d'une liste de mentions). "
                "Options clés : `mode`, `team_count`, `sizes`, `with_groups`, `avoid_pairs`, `members`, "
                "`create_voice`, `channel_ttl`, `auto_import_riot`."
            ),
            "examples": [
                "/team",
                "/team mode:random team_count:3",
                "/team sizes:\"3/3/2\" with_groups:\"@A @B | @C @D\" avoid_pairs:\"@X @Y\"",
                "/team members:\"@A @B @C @D @E @F\" create_voice:true channel_ttl:60",
            ],
        },
        {
            "name": "/disbandteams",
            "desc": "Supprime les **salons vocaux temporaires** créés par `/team create_voice:true`.",
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
    "🛡️ Admin / Owner": [
        {"name": "/whoami", "desc": "Affiche votre **User ID** (pratique pour `OWNER_ID`)."},
        {"name": "/resync", "desc": "Resynchronise les commandes (utile après un déploiement)."},
        {"name": "/restart", "desc": "Redémarre le bot (sur Railway, le process se coupe puis est relancé par la plateforme)."},
        {"name": "/shutdown", "desc": "Arrête le bot (owner/admin)."},
    ],
}

class HelpCog(commands.Cog):
    """Aide intégrée : /help avec aperçu global + recherche d’une commande."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Afficher l’aide du bot et les exemples de workflow.")
    @app_commands.describe(command="(optionnel) nom d’une commande pour l’aide ciblée")
    async def help(self, inter: discord.Interaction, command: str = None):
        """Affiche l'aide générale ou celle d'une commande spécifique."""
        embed = discord.Embed(color=discord.Color.blurple())

        if not command:
            embed.title = "📖 Guide rapide — Team Builder & Tournois"
            embed.description = (
                "Voici les principales commandes du bot, ainsi qu’un **exemple de flow typique** 👇\n\n"
                "### 💡 Exemple de flow typique\n"
                "1️⃣ `/team` — crée les équipes (équilibrées ou aléatoires)\n"
                "2️⃣ (optionnel) `/teamroll` — mixe les équipes avec combinaisons inédites\n"
                "3️⃣ `/go` — crée/réutilise les salons vocaux et déplace les joueurs\n"
                "4️⃣ (optionnel) `/tournament create` → `/tournament add/start/view` — gère le bracket\n"
                "5️⃣ `/disbandteams` — nettoie les salons après la session\n\n"
                "💾 Les dernières équipes sont mémorisées (utilisées par `/go` et `/tournament`).\n"
            )
            embed.add_field(
                name="👥 Équipes",
                value="`/team` `/teamroll` `/go` `/disbandteams`",
                inline=False
            )
            embed.add_field(
                name="🏆 Tournoi",
                value="`/tournament create` `add` `start` `view` `report` `cancel`",
                inline=False
            )
            embed.add_field(
                name="📊 Ratings & LoL",
                value="`/ranks` `/setskill` `/setrank` `/linklol`",
                inline=False
            )
            embed.add_field(
                name="🛠️ Admin",
                value="`/whoami` `/resync` `/backupdb` `/exportcsv` `/shutdown`",
                inline=False
            )
            embed.set_footer(text="Utilise `/help command:team` pour plus de détails sur une commande.")
        else:
            cmd = command.lower()
            if cmd in {"team", "teamroll", "go"}:
                embed.title = f"ℹ️ /{cmd}"
                if cmd == "team":
                    embed.description = (
                        "**Crée des équipes équilibrées ou aléatoires.**\n"
                        "Peut utiliser le vocal courant ou une liste de mentions.\n"
                        "Optionnel : création automatique de salons 'Team 1..K'.\n\n"
                        "Exemple : `/team mode:balanced team_count:3 create_voice:true`"
                    )
                elif cmd == "teamroll":
                    embed.description = (
                        "**Génère des combinaisons inédites** de joueurs (évite les paires déjà jouées).\n"
                        "Chaque `/teamroll` peut être reroll via le bouton 🎲.\n\n"
                        "Exemple : `/teamroll session:'soirée-08-10' team_count:4`"
                    )
                elif cmd == "go":
                    embed.description = (
                        "**Crée ou réutilise les salons vocaux 'Team 1..K' et déplace les joueurs.**\n"
                        "S’appuie sur la dernière configuration d’équipe générée (`/team` ou `/teamroll`)."
                    )
            else:
                embed.description = f"Aucune aide détaillée pour `{cmd}`."

        await inter.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
