# app/cogs/team.py
from typing import List, Dict, Tuple
import itertools

import discord
from discord import app_commands
from discord.ext import commands

from ..db import (
    get_rating, set_rating,
    get_or_create_session_id, load_pair_counts, bump_pair_counts, session_stats, end_session
)

# Import gracieux : si le helper Riot n'existe pas encore, on ne plante pas
try:
    from ..riot import fetch_lol_rank_info  # doit retourner (tier, division, lp, rating_float)
except Exception:
    fetch_lol_rank_info = None  # type: ignore

from ..team_logic import (
    parse_mentions, parse_sizes, group_by_with_constraints,
    parse_avoid_pairs, split_random, balance_k_teams_with_constraints, fmt_team
)
from ..voice import create_and_move_voice


class TeamCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -------- Helpers --------
    async def ensure_ratings_for_members(
        self,
        members: List[discord.Member],
        auto_import_riot: bool = True
    ) -> tuple[Dict[int, float], List[discord.Member], List[discord.Member]]:
        ratings: Dict[int, float] = {}
        used_default: List[discord.Member] = []
        imported: List[discord.Member] = []

        # 1) DB d'abord
        for m in members:
            r = await get_rating(self.bot.settings.DB_PATH, m.id)
            if r is not None:
                ratings[m.id] = r

        # 2) Riot si demandé et possible + si lien est connu
        if auto_import_riot and self.bot.settings.RIOT_API_KEY and fetch_lol_rank_info:
            from ..db import get_linked_lol, set_lol_rank
            for m in members:
                if m.id in ratings:
                    continue
                link = await get_linked_lol(self.bot.settings.DB_PATH, m.id)
                if not link:
                    continue
                summoner, region_code = link
                # fetch_lol_rank_info doit retourner (tier, division, lp, rating_float)
                info = await fetch_lol_rank_info(
                    self.bot.settings.RIOT_API_KEY,
                    region_code,
                    summoner
                )
                if info:
                    tier, division, lp, rr = info
                    ratings[m.id] = rr
                    await set_rating(self.bot.settings.DB_PATH, m.id, rr)
                    await set_lol_rank(
                        self.bot.settings.DB_PATH,
                        m.id,
                        source="riot",
                        tier=tier,
                        division=division,
                        lp=lp,
                    )
                    imported.append(m)

        # 3) défaut 1000
        for m in members:
            if m.id not in ratings:
                ratings[m.id] = 1000.0
                used_default.append(m)

        return ratings, used_default, imported

    # -------- Helper commun : génération d'un roll --------
    async def _generate_roll(
        self,
        inter: discord.Interaction,
        *,
        session: str,
        team_count: int,
        sizes: str,
        with_groups: str,
        avoid_pairs: str,
        members: str,
        mode: str,
        attempts: int,
        commit: bool,
    ) -> tuple[discord.Embed, List[List[discord.Member]], Dict[int, float]]:
        guild = inter.guild
        if not guild:
            raise RuntimeError("Cette commande doit être utilisée dans un serveur.")

        # 1) Collecte joueurs
        author = inter.user if isinstance(inter.user, discord.Member) else guild.get_member(inter.user.id)
        if members:
            selected: List[discord.Member] = parse_mentions(guild, members)
        else:
            if isinstance(author, discord.Member) and author.voice and author.voice.channel:
                selected = [m for m in author.voice.channel.members if not m.bot]
            else:
                raise RuntimeError("Pas de liste fournie et tu n'es pas en vocal.")

        if len(selected) < team_count:
            raise RuntimeError(f"Pas assez de joueurs pour {team_count} équipes.")

        # 2) Ratings + tailles + contraintes
        ratings, used_default, imported_from_riot = await self.ensure_ratings_for_members(
            selected, auto_import_riot=True
        )
        sizes_list = parse_sizes(sizes, len(selected), team_count)
        with_groups_list = group_by_with_constraints(guild, selected, with_groups) if with_groups else [[m] for m in selected]
        avoid_pairs_set = parse_avoid_pairs(guild, avoid_pairs)

        # 3) Session & compteurs de paires
        sid = await get_or_create_session_id(self.bot.settings.DB_PATH, guild.id, session)
        pair_counts = await load_pair_counts(self.bot.settings.DB_PATH, sid)
        pair_counts = {tuple(sorted(k)): v for k, v in pair_counts.items()}

        def penalty(teams: List[List[discord.Member]]) -> Tuple[int, int]:
            """Retourne (penalty_repetition, spread_totals)."""
            rep = 0
            for t in teams:
                ids = sorted(m.id for m in t)
                for a, b in itertools.combinations(ids, 2):
                    rep += pair_counts.get((a, b), 0)
            totals = [int(sum(ratings[m.id] for m in t)) for t in teams]
            spread = max(totals) - min(totals) if totals else 0
            return rep, spread

        # 4) Recherche de la meilleure combinaison
        BEST = None  # (pen_rep, spread, teams)
        attempts = max(20, min(2000, int(attempts)))
        for _ in range(attempts):
            if mode.lower() == "random":
                cand = split_random(selected, team_count, sizes_list)
            else:
                cand, _viol = balance_k_teams_with_constraints(
                    selected, ratings, team_count, sizes_list, with_groups_list, avoid_pairs_set
                )
            rep, spr = penalty(cand)
            if (BEST is None) or (rep, spr) < (BEST[0], BEST[1]):
                BEST = (rep, spr, cand)
            if BEST and BEST[0] == 0:
                break

        if BEST is None:
            raise RuntimeError("Impossible de générer des équipes.")

        rep, spr, teams = BEST

        # 5) Affichage
        embed = discord.Embed(title=f"🎲 Team Roll — session: {session}", color=discord.Color.blurple())
        for idx, team_list in enumerate(teams):
            lines = [f"- {m.display_name} ({int(ratings[m.id])})" for m in team_list]
            total = int(sum(ratings[m.id] for m in team_list))
            embed.add_field(
                name=f"Team {idx+1} — total {total}",
                value=("\n".join(lines) if lines else "_(vide)_"),
                inline=True
            )

        # progression couverture des paires pour CE set de joueurs
        seen, possible = await session_stats(self.bot.settings.DB_PATH, sid, [m.id for m in selected])
        footer = f"Répétitions évitées: {max(0, rep)} • Δ totals: {spr} • Couverture paires: {seen}/{possible}"
        embed.set_footer(text=footer)

        # 6) Commit dans l’historique (optionnel)
        if commit:
            await bump_pair_counts(
                self.bot.settings.DB_PATH,
                sid,
                [[m.id for m in t] for t in teams]
            )

        return embed, teams, ratings

    # -------- View: bouton Reroll (persistant) --------
    class RerollView(discord.ui.View):
        def __init__(self, cog: "TeamCog", *, params: dict | None = None, author_id: int | None = None, timeout: int = None):
            # timeout=None => persistant
            super().__init__(timeout=timeout)
            self.cog = cog
            self.params = params or {}
            self.author_id = author_id

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            # Auteur initial OU admin/manager
            if interaction.user.id == self.author_id:
                return True
            m = interaction.guild and interaction.guild.get_member(interaction.user.id)
            return bool(m and (m.guild_permissions.administrator or m.guild_permissions.manage_guild))

        @discord.ui.button(label="Reroll", emoji="🎲", style=discord.ButtonStyle.primary, custom_id="team_reroll_button")
        async def do_reroll(self, button: discord.ui.Button, interaction: discord.Interaction):
            await interaction.response.defer(thinking=True)
            # Récupérer les paramètres de la view si disponibles
            params = self.params or getattr(interaction.client, "last_teamroll_params", None)
            if not params:
                await interaction.followup.send("⚠️ Impossible de retrouver les paramètres du dernier roll.", ephemeral=True)
                return
            try:
                embed, _teams, _ratings = await self.cog._generate_roll(interaction, **params)
            except Exception as e:
                await interaction.followup.send(f"❌ {e}", ephemeral=True)
                return
            await interaction.edit_original_response(embed=embed, view=self)

    # -------- /team --------
    @app_commands.command(name="team", description="Créer des équipes (équilibrées ou aléatoires) avec options & fallback rating.")
    @app_commands.describe(
        mode="balanced ou random (défaut: balanced)",
        team_count="Nombre d'équipes (2–6, défaut 2)",
        sizes='Tailles fixées, ex: "3/3/2" (somme = nb joueurs)',
        with_groups='Groupes ensemble, ex: "@A @B | @C @D"',
        avoid_pairs='Paires à séparer, ex: "@A @B ; @C @D"',
        members="(Optionnel) liste de @mentions si pas de vocal)",
        create_voice="Créer des salons vocaux Team 1..K et déplacer les joueurs",
        channel_ttl="Durée de vie des salons vocaux (minutes, défaut 90)",
        auto_import_riot="Importer via Riot pour les joueurs liés si possible (défaut: true)",
    )
    async def team(
        self,
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
        author = guild and guild.get_member(inter.user.id)

        # Collecte joueurs
        if members:
            selected: List[discord.Member] = parse_mentions(guild, members)
        else:
            if author and author.voice and author.voice.channel:
                selected = [m for m in author.voice.channel.members if not m.bot]
            else:
                await inter.followup.send("❌ Pas de liste fournie et tu n'es pas en vocal.")
                return

        if len(selected) < team_count:
            await inter.followup.send(f"❌ Pas assez de joueurs pour {team_count} équipes.")
            return

        ratings, used_default, imported_from_riot = await self.ensure_ratings_for_members(
            selected, auto_import_riot
        )

        sizes_list = parse_sizes(sizes, len(selected), team_count)
        with_groups_list = group_by_with_constraints(guild, selected, with_groups) if with_groups else [[m] for m in selected]
        avoid_pairs_set = parse_avoid_pairs(guild, avoid_pairs)

        if mode.lower() == "random":
            teams = split_random(selected, team_count, sizes_list)
            violations: List[Tuple[int, int]] = []
        else:
            teams, violations = balance_k_teams_with_constraints(
                selected, ratings, team_count, sizes_list, with_groups_list, avoid_pairs_set
            )

        embed = discord.Embed(title="🎲 Team Builder", color=discord.Color.blurple())
        for idx, team_list in enumerate(teams):
            embed.add_field(name=f"Team {idx+1}", value=fmt_team(team_list, ratings, idx), inline=True)
        totals = [int(sum(ratings[m.id] for m in t)) for t in teams]
        spread = (max(totals) - min(totals)) if totals else 0
        footer = f"Mode: {'Équilibré' if mode.lower()!='random' else 'Aléatoire'} • Δ total: {spread}"
        if violations:
            footer += f" • Contraintes violées: {len(violations)}"
        embed.set_footer(text=footer)
        await inter.followup.send(embed=embed)

        notes = []
        if imported_from_riot:
            notes.append("🏷️ Import Riot: " + ", ".join(m.display_name for m in imported_from_riot))
        if used_default:
            notes.append("⚠️ Rating par défaut (1000): " + ", ".join(m.display_name for m in used_default) +
                         "\n→ `/setrank` ou `/setskill`, ou `/linklol`.")
        if notes:
            try:
                await inter.followup.send("\n".join(notes), ephemeral=True)
            except:
                pass

        if create_voice:
            try:
                await create_and_move_voice(inter, teams, sizes_list, ttl_minutes=max(channel_ttl, 1))
            except discord.Forbidden:
                await inter.followup.send("⚠️ Permissions manquantes (Manage Channels / Move Members).")

    # -------- /disbandteams --------
    @app_commands.command(name="disbandteams", description="Supprimer les salons vocaux d'équipe temporaires.")
    async def disbandteams(self, inter: discord.Interaction):
        from ..voice import TEMP_CHANNELS
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
                    await ch.delete(reason="TeamBuilder manual cleanup")
                    count += 1
                except discord.Forbidden:
                    pass
        await inter.response.send_message(f"🧹 Salons supprimés: {count}", ephemeral=True)

    # -------- /teamroll --------
    @app_commands.command(name="teamroll", description="Générer une nouvelle combinaison inédite (par session).")
    @app_commands.describe(
        session="Nom de la session (ex: 'soirée-08-10', même nom sur chaque roll)",
        team_count="Nombre d'équipes (2–6, défaut 2)",
        sizes='Tailles fixées (ex: "3/3/2")',
        with_groups='Groupes ensemble (ex: "@A @B | @C @D")',
        avoid_pairs='Paires à séparer (ex: "@A @B ; @C @D")',
        members="(Optionnel) liste de @mentions si pas de vocal",
        mode="balanced (défaut) ou random",
        attempts="Nombre d’essais à explorer (défaut 200)",
        commit="Sauvegarder le roll dans l’historique (défaut: true)"
    )
    async def teamroll(
        self,
        inter: discord.Interaction,
        session: str,
        team_count: int = 2,
        sizes: str = "",
        with_groups: str = "",
        avoid_pairs: str = "",
        members: str = "",
        mode: str = "balanced",
        attempts: int = 200,
        commit: bool = True
    ):
        await inter.response.defer(thinking=True)
        try:
            embed, teams, ratings = await self._generate_roll(
                inter,
                session=session,
                team_count=team_count,
                sizes=sizes,
                with_groups=with_groups,
                avoid_pairs=avoid_pairs,
                members=members,
                mode=mode,
                attempts=attempts,
                commit=commit,
            )
        except Exception as e:
            await inter.followup.send(f"❌ {e}")
            return

        # Bouton Reroll avec les mêmes paramètres
        params = dict(
            session=session, team_count=team_count, sizes=sizes,
            with_groups=with_groups, avoid_pairs=avoid_pairs,
            members=members, mode=mode, attempts=attempts, commit=commit
        )
        view = self.RerollView(self, params=params, author_id=inter.user.id, timeout=300)
        await inter.followup.send(embed=embed, view=view)

    # -------- /teamroll_end --------
    @app_commands.command(name="teamroll_end", description="Terminer/effacer une session de roll (réinitialise l’historique).")
    @app_commands.describe(session="Nom de la session à terminer")
    async def teamroll_end(self, inter: discord.Interaction, session: str):
        if not inter.guild:
            await inter.response.send_message("❌ À utiliser sur un serveur.", ephemeral=True)
            return
        if not inter.user.guild_permissions.administrator:
            await inter.response.send_message("⛔ Réservé aux admins.", ephemeral=True)
            return
        ok = await end_session(self.bot.settings.DB_PATH, inter.guild.id, session)
        if ok:
            await inter.response.send_message(f"🧹 Session `{session}` supprimée.", ephemeral=True)
        else:
            await inter.response.send_message(f"ℹ️ Session `{session}` introuvable.", ephemeral=True)


async def setup(bot: commands.Bot):
    cog = TeamCog(bot)
    await bot.add_cog(cog)

    # ✅ Ajouter la view persistante au démarrage
    # (le custom_id doit correspondre à celui du bouton)
    bot.add_view(TeamCog.RerollView(cog, timeout=None))
