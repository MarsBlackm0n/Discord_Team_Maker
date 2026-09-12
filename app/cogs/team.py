# app/cogs/team.py
from typing import List, Dict, Tuple, Optional, Literal
import itertools
import time
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from ..db import (
    get_rating, set_rating, set_team_last, get_team_last,
    load_lane_preferences, set_lane_preferences,
    get_or_create_session_id, load_pair_counts, bump_pair_counts, session_stats, end_session,
    load_team_signatures, add_team_signature, clear_team_signatures, prune_team_signatures
)

# Import gracieux : si le helper Riot n'existe pas encore, on ne plante pas
try:
    from ..riot import fetch_lol_rank_info  # doit retourner (tier, division, lp, rating_float)
except Exception:
    fetch_lol_rank_info = None  # type: ignore

from ..team_logic import (
    parse_mentions, parse_sizes, group_by_with_constraints,
    parse_avoid_pairs
)
from ..voice import create_and_move_voice
from ..lanes import ROLES, parse_roles, select_teams, format_player, signature


class TeamCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        from ..voice import TEMP_CHANNELS, _IDLE_TTLS, _track_channel
        if before.channel == after.channel:
            return
        for channel in (before.channel, after.channel):
            if channel and channel.id in TEMP_CHANNELS.get(member.guild.id, {}):
                ttl = _IDLE_TTLS.get(member.guild.id, {}).get(channel.id, 90 * 60)
                _track_channel(member.guild.id, channel.id, int(ttl / 60))

    @app_commands.command(name="setroles", description="Enregistrer tes rôles LoL par ordre de préférence (1 à 5).")
    @app_commands.guild_only()
    @app_commands.describe(first_role="Rôle principal", second_role="Deuxième choix",
                           third_role="Troisième choix", fourth_role="Quatrième choix",
                           fifth_role="Cinquième choix", user="Autre joueur (Gérer le serveur requis)")
    async def setroles(
        self, inter: discord.Interaction,
        first_role: Literal["top", "jgl", "mid", "bot", "sup"],
        second_role: Optional[Literal["top", "jgl", "mid", "bot", "sup"]] = None,
        third_role: Optional[Literal["top", "jgl", "mid", "bot", "sup"]] = None,
        fourth_role: Optional[Literal["top", "jgl", "mid", "bot", "sup"]] = None,
        fifth_role: Optional[Literal["top", "jgl", "mid", "bot", "sup"]] = None,
        user: Optional[discord.Member] = None,
    ):
        target = user or inter.user
        if target.id != inter.user.id and not inter.user.guild_permissions.manage_guild:
            await inter.response.send_message("⛔ Gérer le serveur est requis pour modifier un autre joueur.", ephemeral=True)
            return
        try:
            roles = parse_roles(" ".join(r for r in (first_role, second_role, third_role, fourth_role, fifth_role) if r))
        except ValueError as exc:
            await inter.response.send_message(str(exc), ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        await set_lane_preferences(self.bot.settings.DB_PATH, inter.guild.id, target.id, roles)
        await inter.followup.send(f"✅ {target.display_name} : " + " → ".join(r.upper() for r in roles), ephemeral=True)

    @app_commands.command(name="roles", description="Afficher les préférences de rôles d'un joueur.")
    @app_commands.guild_only()
    async def roles(self, inter: discord.Interaction, user: Optional[discord.Member] = None):
        await inter.response.defer(ephemeral=True)
        target = user or inter.user
        preferences = await load_lane_preferences(self.bot.settings.DB_PATH, inter.guild.id)
        roles = preferences.get(target.id, [])
        text = " → ".join(r.upper() for r in roles) if roles else "Aucune préférence enregistrée. Utilise /setroles."
        await inter.followup.send(f"**{target.display_name}** : {text}", ephemeral=True)

    @staticmethod
    def teams_embed(teams, ratings, assignments, preferences, mode, title):
        balanced = mode.lower() == "balanced"
        embed = discord.Embed(title=title, color=discord.Color.blurple())
        embed.description = (
            "Un joueur par rôle • priorité aux préférences, puis à leur répartition entre équipes."
            if assignments else "Attribution des rôles disponible pour deux équipes de 5 joueurs."
        )
        if assignments and any(not preferences.get(m.id) for t in teams for m in t):
            embed.description += "\nPréférences inconnues : aucune pénalité de rôle ; renseignez /setroles."
        for idx, team in enumerate(teams, 1):
            ordered = sorted(team, key=lambda m: ROLES.index(assignments[m.id])) if assignments else team
            lines = [format_player(m, ratings, assignments, preferences, balanced) for m in ordered]
            name = f"Team {idx}"
            if balanced:
                name += f" — total {int(sum(ratings[m.id] for m in team))}"
            embed.add_field(name=name, value="\n".join(lines) or "_(vide)_", inline=True)
        return embed

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

    # -------- Helpers signatures (anti-répétition forte) --------
    @staticmethod
    def _players_fingerprint(members: List[discord.Member]) -> str:
        """Empreinte déterministe de l'ensemble de joueurs (ordre indépendant)."""
        ids = sorted(m.id for m in members if not m.bot)
        return "P:" + ",".join(str(i) for i in ids)

    @staticmethod
    def _composition_signature(teams: List[List[discord.Member]]) -> str:
        """
        Signature canonique d'une composition :
        - joueurs de chaque équipe triés
        - équipes triées entre elles
        -> indépendante de l'ordre d'affichage
        """
        team_chunks = []
        for t in teams:
            ids = sorted(m.id for m in t if not m.bot)
            team_chunks.append("-".join(str(x) for x in ids))
        team_chunks.sort()
        return "|".join(team_chunks)

    @staticmethod
    def _sizes_fingerprint(sizes: List[int]) -> str:
        return "S:" + ",".join(str(s) for s in sizes)

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
        # --- ajouts pour réutiliser le dernier /team ---
        selected_members: Optional[List[discord.Member]] = None,
        sizes_list_override: Optional[List[int]] = None,
    ) -> tuple[discord.Embed, List[List[discord.Member]], Dict[int, float]]:
        guild = inter.guild
        if not guild:
            raise RuntimeError("Cette commande doit être utilisée dans un serveur.")

        # ⚙️ session par défaut si vide
        session = (session or "").strip() or f"auto-{datetime.utcnow().strftime('%Y%m%d')}"

        # 1) Collecte joueurs
        if selected_members is not None:
            selected: List[discord.Member] = selected_members
        else:
            author = inter.user if isinstance(inter.user, discord.Member) else guild.get_member(inter.user.id)
            if members:
                selected = parse_mentions(guild, members)
            else:
                if isinstance(author, discord.Member) and author.voice and author.voice.channel:
                    selected = [m for m in author.voice.channel.members if not m.bot]
                else:
                    # Fallback snapshot auto si pas de liste et pas en vocal
                    snap = await get_team_last(self.bot.settings.DB_PATH, guild.id)
                    if snap and snap.get("teams"):
                        ids = [int(uid) for team_ids in snap["teams"] for uid in team_ids]
                        look = {m.id: m for m in guild.members}
                        selected = [look[i] for i in ids if i in look and not look[i].bot]
                        if not selected:
                            raise RuntimeError("Pas de liste fournie, pas en vocal, et la dernière config n'est pas résoluble.")
                        if not sizes.strip():
                            sizes_list_override = [len(team_ids) for team_ids in snap["teams"]]
                            team_count = len(sizes_list_override)
                    else:
                        raise RuntimeError("Pas de liste fournie et tu n'es pas en vocal.")
        if len(selected) < team_count:
            raise RuntimeError(f"Pas assez de joueurs pour {team_count} équipes.")

        # 2) Ratings + tailles + contraintes
        ratings, used_default, imported_from_riot = await self.ensure_ratings_for_members(
            selected, auto_import_riot=(mode.lower() == "balanced")
        )

        if sizes_list_override is not None:
            sizes_list = sizes_list_override
        else:
            sizes_list = parse_sizes(sizes, len(selected), team_count)

        with_groups_list = group_by_with_constraints(guild, selected, with_groups) if with_groups else [[m] for m in selected]
        avoid_pairs_set = parse_avoid_pairs(guild, avoid_pairs)

        # 3) Session, compteurs de paires & signatures déjà vues
        sid = await get_or_create_session_id(self.bot.settings.DB_PATH, guild.id, session)
        pair_counts = await load_pair_counts(self.bot.settings.DB_PATH, sid)
        pair_counts = {tuple(sorted(k)): v for k, v in pair_counts.items()}

        players_fp = self._players_fingerprint(selected)
        sizes_fp = self._sizes_fingerprint(sizes_list)
        seen_signatures = await load_team_signatures(
            self.bot.settings.DB_PATH, guild.id, session, players_fp, sizes_fp
        )

        preferences = await load_lane_preferences(self.bot.settings.DB_PATH, guild.id)
        teams, assignments, violations = select_teams(
            selected, ratings, sizes_list, with_groups_list, avoid_pairs_set,
            preferences, mode, attempts, seen_signatures, pair_counts,
        )
        exhausted = signature(teams) in seen_signatures
        rep = sum(pair_counts.get(tuple(sorted((a.id, b.id))), 0)
                  for t in teams for a, b in itertools.combinations(t, 2))
        totals = [sum(ratings[m.id] for m in t) for t in teams]
        spr = max(totals) - min(totals)

        embed = self.teams_embed(teams, ratings, assignments, preferences, mode,
                                f"🎲 Team Roll — session: {session}")

        # progression couverture des paires pour CE set de joueurs
        seen, possible = await session_stats(self.bot.settings.DB_PATH, sid, [m.id for m in selected])
        footer = f"Paires déjà jouées: {rep} • Couverture paires: {seen}/{possible}"
        if mode.lower() == "balanced":
            footer += f" • Δ totals: {int(spr)}"
        if violations:
            footer += f" • Contraintes violées: {len(violations)}"
        if exhausted:
            footer += " • ♻️ Composition déjà jouée : priorité à la qualité des équipes"
        embed.set_footer(text=footer)

        # 6) Commit dans l’historique (optionnel)
        if commit:
            # historise les paires
            await bump_pair_counts(
                self.bot.settings.DB_PATH,
                sid,
                [[m.id for m in t] for t in teams]
            )
            # historise la signature forte
            sig = self._composition_signature(teams)
            await add_team_signature(
                self.bot.settings.DB_PATH,
                guild.id, session, players_fp, sizes_fp, sig, int(time.time())
            )
            # garde une fenêtre max d’historique (ex: 200 dernières compos)
            KEEP_LAST = 200
            try:
                await prune_team_signatures(
                    self.bot.settings.DB_PATH, guild.id, session, players_fp, sizes_fp, KEEP_LAST
                )
            except Exception:
                pass

        if commit:
            await set_team_last(self.bot.settings.DB_PATH, guild.id, {
                "mode": mode.lower(), "team_count": team_count, "sizes": sizes_list,
                "teams": [[m.id for m in t] for t in teams],
                "ratings": {str(uid): r for uid, r in ratings.items()},
                "assignments": {str(uid): role for uid, role in assignments.items()},
                "params": {"with_groups": with_groups, "avoid_pairs": avoid_pairs,
                           "session": session, "attempts": attempts},
                "created_by": inter.user.id, "created_at": int(time.time()),
            })
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
            # Auteur initial OU admin/manager; si author_id inconnu (reboot), on autorise admin/manager
            if self.author_id is None:
                m = interaction.guild and interaction.guild.get_member(interaction.user.id)
                return bool(m and (m.guild_permissions.administrator or m.guild_permissions.manage_guild))
            if interaction.user.id == self.author_id:
                return True
            m = interaction.guild and interaction.guild.get_member(interaction.user.id)
            return bool(m and (m.guild_permissions.administrator or m.guild_permissions.manage_guild))

        @discord.ui.button(label="Reroll", emoji="🎲", style=discord.ButtonStyle.primary, custom_id="team_reroll_button")
        async def do_reroll(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.defer(thinking=True)
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
        channel_ttl="Délai de suppression après inoccupation des salons (minutes, défaut 90)",
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
        
        session = f"auto-{datetime.utcnow().strftime('%Y%m%d')}"
        guild = inter.guild
        if not guild:
            await inter.followup.send("À utiliser sur un serveur.", ephemeral=True)
            return
        author = guild.get_member(inter.user.id)

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
            selected, auto_import_riot and mode.lower() == "balanced"
        )

        sizes_list = parse_sizes(sizes, len(selected), team_count)
        with_groups_list = group_by_with_constraints(guild, selected, with_groups) if with_groups else [[m] for m in selected]
        avoid_pairs_set = parse_avoid_pairs(guild, avoid_pairs)

        preferences = await load_lane_preferences(self.bot.settings.DB_PATH, guild.id)
        try:
            teams, assignments, violations = select_teams(
                selected, ratings, sizes_list, with_groups_list, avoid_pairs_set, preferences, mode
            )
        except ValueError as exc:
            await inter.followup.send(f"❌ {exc}", ephemeral=True)
            return
        embed = self.teams_embed(teams, ratings, assignments, preferences, mode,
                                f"🎲 Team Builder — session: {session}")
        footer = f"Mode: {mode.lower()}"
        if mode.lower() == "balanced":
            totals = [sum(ratings[m.id] for m in t) for t in teams]
            footer += f" • Δ total: {int(max(totals) - min(totals))}"
        if violations:
            footer += f" • Contraintes violées: {len(violations)}"
        embed.set_footer(text=footer)

        # Include the initial composition in the history used by Reroll.
        sid = await get_or_create_session_id(self.bot.settings.DB_PATH, guild.id, session)
        await bump_pair_counts(self.bot.settings.DB_PATH, sid, [[m.id for m in t] for t in teams])
        await add_team_signature(self.bot.settings.DB_PATH, guild.id, session,
                                 self._players_fingerprint(selected), self._sizes_fingerprint(sizes_list),
                                 signature(teams), int(time.time()))

        # Prépare les params pour un Reroll identique (mêmes joueurs/tailles) — session auto
        params = dict(
            session=session,
            team_count=team_count,
            sizes="",                        # on figera via sizes_list_override
            with_groups=with_groups,
            avoid_pairs=avoid_pairs,
            members="",                      # pas de mentions
            mode=mode,
            attempts=200,
            commit=True,
            selected_members=selected,       # mêmes joueurs
            sizes_list_override=sizes_list,  # mêmes tailles
        )
        setattr(inter.client, "last_teamroll_params", params)
        view = self.RerollView(self, params=params, author_id=inter.user.id, timeout=300)

        await inter.followup.send(embed=embed, view=view)

        # Notes annexes
        notes = []
        if imported_from_riot:
            notes.append("🏷️ Import Riot: " + ", ".join(m.display_name for m in imported_from_riot))
        if used_default and mode.lower() == "balanced":
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

        # Sauvegarde "dernière config" (serveur)
        try:
            snapshot = {
                "mode": mode.lower(),
                "assignments": {str(uid): role for uid, role in assignments.items()},
                "team_count": team_count,
                "sizes": sizes_list,
                "teams": [[m.id for m in t] for t in teams],
                "ratings": {str(uid): float(ratings[uid]) for uid in [m.id for t in teams for m in t]},
                "params": {
                    "with_groups": with_groups, "avoid_pairs": avoid_pairs, "members": members,
                    "session": session, "attempts": 200
                },
                "created_by": inter.user.id,
                "created_at": int(time.time()),
            }
            await set_team_last(self.bot.settings.DB_PATH, inter.guild.id, snapshot)
        except Exception:
            pass

    # -------- /disbandteams --------
    @app_commands.command(name="disbandteams", description="Supprimer les salons vocaux d'équipe temporaires.")
    async def disbandteams(self, inter: discord.Interaction):
        from ..voice import TEMP_CHANNELS, _forget_channel
        guild = inter.guild
        if not guild:
            await inter.response.send_message("❌ Guild inconnue.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        ids = list(TEMP_CHANNELS.get(guild.id, {}))
        count = 0
        occupied = 0
        for cid in ids:
            ch = guild.get_channel(cid)
            if ch and ch.members:
                occupied += 1
                continue
            if ch:
                try:
                    await ch.delete(reason="TeamBuilder manual cleanup")
                    count += 1
                except discord.NotFound:
                    pass
                except (discord.Forbidden, discord.HTTPException):
                    continue
            _forget_channel(guild.id, cid)
        await inter.followup.send(f"🧹 Salons supprimés: {count} • Salons occupés conservés: {occupied}", ephemeral=True)

    # -------- /teamroll --------
    @app_commands.command(name="teamroll", description="Relance un tirage à partir de la DERNIÈRE config /team (fallback auto), en évitant les répétitions.")
    @app_commands.describe(
        session="Nom de la session (ex: 'soirée-08-10'). Si vide: auto-YYYYMMDD",
        team_count="Nombre d'équipes (si vide, reprend celui du dernier /team)",
        sizes='Tailles fixées (ex: "3/3/2"). Si vide, reprend celles du dernier /team',
        with_groups='Groupes ensemble (ex: "@A @B | @C @D")',
        avoid_pairs='Paires à séparer (ex: "@A @B ; @C @D")',
        members="(Optionnel) liste de @mentions; sinon vocal; sinon dernière config /team",
        mode="balanced (défaut) ou random",
        attempts="Nombre d’essais à explorer (défaut 200)",
        commit="Sauvegarder le roll dans l’historique de session (défaut: true)",
        use_last="Ignorer le vocal et reprendre le dernier /team (défaut: false)"
    )
    async def teamroll(
        self,
        inter: discord.Interaction,
        session: str = "",
        team_count: Optional[int] = None,
        sizes: str = "",
        with_groups: str = "",
        avoid_pairs: str = "",
        members: str = "",
        mode: str = "balanced",
        attempts: int = 200,
        commit: bool = True,
        use_last: bool = False
    ):
        await inter.response.defer(thinking=True)

        guild = inter.guild
        if not guild:
            await inter.followup.send("❌ À utiliser en serveur.", ephemeral=True)
            return

        # Session auto si vide
        if not (session or "").strip():
            session = f"auto-{datetime.utcnow().strftime('%Y%m%d')}"

        # Fallback snapshot si pas de mentions et pas en vocal (ou si use_last=True)
        selected_members: Optional[List[discord.Member]] = None
        sizes_list_override: Optional[List[int]] = None

        need_snapshot_fallback = False
        if not members.strip():
            author = guild.get_member(inter.user.id)
            if use_last:
                need_snapshot_fallback = True
            else:
                need_snapshot_fallback = not (author and author.voice and author.voice.channel)

        if need_snapshot_fallback:
            snap = await get_team_last(self.bot.settings.DB_PATH, guild.id)
            if not snap or not snap.get("teams"):
                await inter.followup.send("ℹ️ Aucune **dernière configuration d’équipes** trouvée. Utilise d’abord `/team`.", ephemeral=True)
                return

            lookup = {m.id: m for m in guild.members}
            selected_members = []
            for team_ids in snap["teams"]:
                for uid in team_ids:
                    m = lookup.get(int(uid))
                    if m and not m.bot:
                        selected_members.append(m)

            if team_count is None:
                team_count = int(snap.get("team_count", len(snap["teams"])))
            if not sizes.strip():
                sizes_list_override = [len(team_ids) for team_ids in snap["teams"]]

            meta = snap.get("params", {}) or {}
            if not with_groups:
                with_groups = meta.get("with_groups", "")
            if not avoid_pairs:
                avoid_pairs = meta.get("avoid_pairs", "")

        if team_count is None:
            await inter.followup.send("❌ Impossible de déterminer le nombre d'équipes (aucune source).", ephemeral=True)
            return

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
                selected_members=selected_members,
                sizes_list_override=sizes_list_override,
            )
        except Exception as e:
            await inter.followup.send(f"❌ {e}", ephemeral=True)
            return

        # Bouton Reroll avec les mêmes paramètres (+ stock global simple)
        params = dict(
            session=session, team_count=team_count, sizes="",
            with_groups=with_groups, avoid_pairs=avoid_pairs,
            members=members, mode=mode, attempts=attempts, commit=commit,
            selected_members=[m for t in teams for m in t], sizes_list_override=[len(t) for t in teams],
        )
        setattr(inter.client, "last_teamroll_params", params)

        view = self.RerollView(self, params=params, author_id=inter.user.id, timeout=300)
        await inter.followup.send(embed=embed, view=view)

    # -------- /team_last --------
    @app_commands.command(name="team_last", description="Afficher la dernière configuration d'équipes enregistrée pour ce serveur.")
    async def team_last(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        snap = await get_team_last(self.bot.settings.DB_PATH, inter.guild.id)
        if not snap:
            await inter.followup.send("ℹ️ Aucune configuration de team enregistrée pour ce serveur.", ephemeral=True)
            return

        ids_to_members = {m.id: m for m in inter.guild.members}
        embed = discord.Embed(title="🗂️ Dernière config d'équipes", color=discord.Color.green())
        for idx, team_ids in enumerate(snap.get("teams", []), start=1):
            names = []
            assignments = snap.get("assignments", {})
            for uid in team_ids:
                member = ids_to_members.get(int(uid))
                if member:
                    rating = int(float(snap.get("ratings", {}).get(str(uid), 0)))
                    role = assignments.get(str(uid))
                    label = f"**{role.upper()}** — " if role else ""
                    rating_label = f" ({rating})" if snap.get("mode") != "random" else ""
                    names.append(f"- {label}{member.display_name}{rating_label}")
                else:
                    names.append(f"- (id:{uid})")
            total = sum(int(float(snap.get("ratings", {}).get(str(uid), 0))) for uid in team_ids)
            embed.add_field(name=(f"Team {idx} — total {total}" if snap.get("mode") != "random" else f"Team {idx}"), value="\n".join(names) or "_(vide)_", inline=True)

        meta = snap.get("params", {})
        footer = f"Mode: {snap.get('mode','?')} • Équipes: {snap.get('team_count','?')}"
        if meta.get("session"):
            footer += f" • Session: {meta['session']}"
        embed.set_footer(text=footer)
        await inter.followup.send(embed=embed, ephemeral=True)


    # -------- /teamroll_end (pairs) --------
    @app_commands.command(name="teamroll_end", description="Terminer/effacer une session de roll (réinitialise l’historique des paires).")
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
            await inter.response.send_message(f"🧹 Session `{session}` supprimée (paires).", ephemeral=True)
        else:
            await inter.response.send_message(f"ℹ️ Session `{session}` introuvable.", ephemeral=True)

    # -------- /teamroll_reset (signatures fortes) --------
    @app_commands.command(name="teamroll_reset", description="Réinitialiser l'historique des compositions (signatures).")
    @app_commands.describe(
        session="(Optionnel) Nom de la session. Si vide: purge pour TOUTES les sessions mais UNIQUEMENT pour le set/tailles de la dernière config.",
        for_current_snapshot="Limiter au set/tailles de la dernière config (défaut: true)"
    )
    async def teamroll_reset(self, inter: discord.Interaction, session: str = "", for_current_snapshot: bool = True):
        if not inter.guild:
            await inter.response.send_message("❌ À utiliser sur un serveur.", ephemeral=True); return
        if not inter.user.guild_permissions.administrator:
            await inter.response.send_message("⛔ Réservé aux admins.", ephemeral=True); return

        players_fp = ""
        sizes_fp = ""

        if for_current_snapshot:
            snap = await get_team_last(self.bot.settings.DB_PATH, inter.guild.id)
            if not snap or not snap.get("teams"):
                await inter.response.send_message("ℹ️ Pas de snapshot /team pour cibler un set précis.", ephemeral=True); return
            lookup = {m.id: m for m in inter.guild.members}
            selected = []
            for team_ids in snap["teams"]:
                for uid in team_ids:
                    m = lookup.get(int(uid))
                    if m and not m.bot:
                        selected.append(m)
            players_fp = self._players_fingerprint(selected)
            sizes_fp = self._sizes_fingerprint([len(team_ids) for team_ids in snap["teams"]])

        # utilise clear_team_signatures adapté dans db.py
        from ..db import clear_team_signatures
        n = await clear_team_signatures(self.bot.settings.DB_PATH, inter.guild.id, session, players_fp, sizes_fp)

        scope = f"session `{session}`" if session else "toutes les sessions (pour ce set/tailles)"
        await inter.response.send_message(f"🧽 Historique des compositions réinitialisé ({n} entrées supprimées) — {scope}.", ephemeral=True)


async def setup(bot: commands.Bot):
    cog = TeamCog(bot)
    await bot.add_cog(cog)
    # ✅ View persistante au démarrage (le custom_id doit correspondre au bouton)
    bot.add_view(TeamCog.RerollView(cog, timeout=None))
