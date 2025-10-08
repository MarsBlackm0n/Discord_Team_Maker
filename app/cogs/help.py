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

    @app_commands.command(name="help", description="Afficher l'aide des commandes du bot.")
    @app_commands.describe(
        command="(Optionnel) Nom d'une commande pour l'aide détaillée",
        ephemeral="Répondre seulement à vous (par défaut: true)"
    )
    async def help_cmd(
        self,
        inter: discord.Interaction,
        command: str | None = None,
        ephemeral: bool = True
    ):
        if command:
            # Recherche d'une commande par nom (case-insensitive)
            name = command.strip().lstrip("/").lower()
            for section, items in HELP_SECTIONS.items():
                for it in items:
                    if it["name"].lstrip("/").lower() == name:
                        emb = discord.Embed(
                            title=f"❓ Aide — {it['name']}",
                            description=it["desc"],
                            color=discord.Color.blurple()
                        )
                        if "examples" in it and it["examples"]:
                            emb.add_field(
                                name="Exemples",
                                value="\n".join(f"`{ex}`" for ex in it["examples"]),
                                inline=False
                            )
                        await inter.response.send_message(embed=emb, ephemeral=ephemeral)
                        return
            # Non trouvé : petite liste de suggestions
            all_names = ", ".join(f"`{i['name']}`" for sec in HELP_SECTIONS.values() for i in sec)
            await inter.response.send_message(
                f"⚠️ Commande inconnue : `{command}`.\nEssaye l'une de : {all_names}",
                ephemeral=True
            )
            return

        # Aide globale
        emb = discord.Embed(
            title="📚 Aide — Team Builder Bot",
            description=(
                "Formez des équipes équilibrées, créez des salons vocaux et gérez les ratings/rangs LoL.\n"
                "• Pour l’aide d’une commande précise : `/help command:team` (par ex.)"
            ),
            color=discord.Color.blurple()
        )
        for section, items in HELP_SECTIONS.items():
            lines = [f"• **{it['name']}** — {it['desc']}" for it in items]
            emb.add_field(name=section, value="\n".join(lines), inline=False)

        emb.set_footer(text="Astuce: utilisez /resync si les commandes n'apparaissent pas après un déploiement.")
        await inter.response.send_message(embed=emb, ephemeral=ephemeral)

async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
