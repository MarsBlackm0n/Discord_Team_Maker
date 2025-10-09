# app/cogs/team.py
from typing import List, Dict, Tuple, Optional
import itertools
import time
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from ..db import (
    get_rating, set_rating, set_team_last, get_team_last,
    get_or_create_session_id, load_pair_counts, bump_pair_counts, session_stats, end_session,
    load_team_signatures, add_team_signature, clear_team_signatures  # ⬅️ historique signatures
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
            selected, auto_import_riot=True
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

        # 4) Recherche de la meilleure combinaison (priorité aux inédites)
        BEST: Optional[tuple[int, int, List[List[discord.Member]]]] = None
        BEST_UNSEEN: Optional[List[List[discord.Member]]] = None
        attempts = max(50, min(5000, int(attempts)))
        for _ in range(attempts):
            if mode.lower() == "random":
                cand = split_random(selected, team_count, sizes_list)
            else:
                cand, _viol = balance_k_teams_with_constraints(
                    selected, ratings, team_count, sizes_list, with_groups_list, avoid_pairs_set
                )

            sig = self._composition_signature(cand)
            if sig not in seen_signatures and BEST_UNSEEN is None:
                BEST_UNSEEN = cand  # 1ère inédite trouvée

            rep, spr = penalty(cand)
            if (BEST is None) or (rep, spr) < (BEST[0], BEST[1]):
                BEST = (rep, spr, cand)

            if BEST_UNSEEN is not None and rep == 0:
                break  # inédite et parfaite côté paires

        if BEST is None:
            raise RuntimeError("Impossible de générer des équipes.")

        if BEST_UNSEEN is not None:
            teams = BEST_UNSEEN
            exhausted = False
            # rep/spr pour affichage (recalcule léger)
            rep, spr = penalty(teams)
        else:
            # toutes les signatures déjà vues pour ce set/tailles/session
            _, _, teams = BEST
            exhausted = True
            rep, spr = BEST[0], BEST[1]

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
        if exhausted:
            footer += " • ⚠️ Toutes les compositions possibles déjà vues pour cette session / ce set."
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

        # Embed + bouton Reroll (en un seul envoi)
        embed = discord.Embed(title="🎲 Team Builder", color=discord.Color.blurple())
        for idx, team_list in enumerate(teams):
            embed.add_field(name="\u200b", value=fmt_team(team_list, ratings, idx), inline=True)
        totals = [int(sum(ratings[m.id] for m in t)) for t in teams]
        spread = (max(totals) - min(totals)) if totals else 0
        footer = f"Mode: {'Équilibré' if mode.lower()!='random' else 'Aléatoire'} • Δ total: {spread}"
        if violations:
            footer += f" • Contraintes violées: {len(violations)}"
        embed.set_footer(text=footer)

        # Prépare les params pour un Reroll identique (mêmes joueurs/tailles)
        params = dict(
            session="",                      # auto-YYYYMMDD dans teamroll
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

        # Sauvegarde "dernière config" (serveur)
        try:
            snapshot = {
                "mode": mode.lower(),
                "team_count": team_count,
                "sizes": sizes_list,
                "teams": [[m.id for m in t] for t in teams],
                "ratings": {str(uid): float(ratings[uid]) for uid in [m.id for t in teams for m in t]},
                "params": {
                    "with_groups": with_groups, "avoid_pairs": avoid_pairs, "members": members,
                    "session": "", "attempts": 0
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
    @app_commands.command(name="teamroll", description="Relance un tirage à partir de la DERNIÈRE config /team (fallback auto), en évitant les répétitions.")
    @app_commands.describe(
        session="Nom de la session (ex: 'soirée-08-10'). Si vide: auto-YYYYMMDD",
        team_count="Nombre d'équipes (si vide, reprend celui du dernier /team)",
        sizes='Tailles fixées (ex: "3/3/2"). Si vide, reprend celles du dernier /team',
        with_groups='Groupes ensemble (ex: \"@A @B | @C @D\")',
        avoid_pairs='Paires à séparer (ex: \"@A @B ; @C @D\")',
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

        # Fallback session automatique
        if not session.strip():
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

        # Sauvegarde "dernière config" (serveur)
        try:
            if sizes_list_override is not None:
                sizes_list = sizes_list_override
            else:
                sizes_list = parse_sizes(sizes, sum(len(t) for t in teams), team_count)

            snapshot = {
                "mode": mode.lower(),
                "team_count": team_count,
                "sizes": sizes_list,
                "teams": [[m.id for m in t] for t in teams],
                "ratings": {str(uid): float(ratings[uid]) for uid in [m.id for t in teams for m in t]},
                "params": {
                    "with_groups": with_groups, "avoid_pairs": avoid_pairs, "members": members,
                    "session": session, "attempts": attempts
                },
                "created_by": inter.user.id,
                "created_at": int(time.time()),
            }
            await set_team_last(self.bot.settings.DB_PATH, inter.guild.id, snapshot)
        except Exception:
            pass

        # Bouton Reroll avec les mêmes paramètres (+ stock global simple)
        params = dict(
            session=session, team_count=team_count, sizes=sizes,
            with_groups=with_groups, avoid_pairs=avoid_pairs,
            members=members, mode=mode, attempts=attempts, commit=commit,
            selected_members=selected_members, sizes_list_override=sizes_list_override,
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
            for uid in team_ids:
                member = ids_to_members.get(int(uid))
                if member:
                    rating = int(float(snap.get("ratings", {}).get(str(uid), 0)))
                    names.append(f"- {member.display_name} ({rating})")
                else:
                    names.append(f"- (id:{uid})")
            total = sum(int(float(snap.get("ratings", {}).get(str(uid), 0))) for uid in team_ids)
            embed.add_field(name=f"Team {idx} — total {total}", value="\n".join(names) or "_(vide)_", inline=True)

        meta = snap.get("params", {})
        footer = f"Mode: {snap.get('mode','?')} • Équipes: {snap.get('team_count','?')}"
        if meta.get("session"):
            footer += f" • Session: {meta['session']}"
        embed.set_footer(text=footer)
        await inter.followup.send(embed=embed, ephemeral=True)

    # -------- /go --------
    @app_commands.command(name="go", description="Créer les salons vocaux et déplacer les joueurs selon la dernière config.")
    @app_commands.describe(
        channel_ttl="Durée de vie des salons (minutes, défaut 90)",
        reuse_existing="Réutiliser des salons 'Team 1', 'Team 2' existants si présents (si ton voice.py le gère)"
    )
    async def go(self, inter: discord.Interaction, channel_ttl: int = 90, reuse_existing: bool = True):
        await inter.response.defer(thinking=True)
        snap = await get_team_last(self.bot.settings.DB_PATH, inter.guild.id)
        if not snap:
            await inter.followup.send("ℹ️ Aucune config enregistrée. Lance d'abord /team ou /teamroll.", ephemeral=True)
            return

        # Recompose les objets Member à partir des IDs
        guild = inter.guild
        teams: List[List[discord.Member]] = []
        for team_ids in snap.get("teams", []):
            members_obj = []
            for uid in team_ids:
                m = guild.get_member(int(uid))
                if m and not m.bot:
                    members_obj.append(m)
            teams.append(members_obj)

        sizes = snap.get("sizes") or [len(t) for t in teams]
        try:
            await create_and_move_voice(
                inter,
                teams,
                sizes,
                ttl_minutes=max(int(channel_ttl), 1),
            )
            await inter.followup.send("🚀 Salons créés/réutilisés et joueurs déplacés.", ephemeral=True)
        except discord.Forbidden:
            await inter.followup.send("⚠️ Permissions manquantes (Manage Channels / Move Members).", ephemeral=True)

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
    @app_commands.command(name="teamroll_reset", description="Réinitialiser l'historique des compositions (signatures) pour une session.")
    @app_commands.describe(session="Nom de la session", for_current_snapshot="Limiter le reset au set/tailles de la dernière config /team")
    async def teamroll_reset(self, inter: discord.Interaction, session: str, for_current_snapshot: bool = False):
        if not inter.guild:
            await inter.response.send_message("❌ À utiliser sur un serveur.", ephemeral=True)
            return
        if not inter.user.guild_permissions.administrator:
            await inter.response.send_message("⛔ Réservé aux admins.", ephemeral=True)
            return

        players_fp = ""
        sizes_fp = ""
        if for_current_snapshot:
            snap = await get_team_last(self.bot.settings.DB_PATH, inter.guild.id)
            if not snap or not snap.get("teams"):
                await inter.response.send_message("ℹ️ Pas de snapshot /team pour cibler un set précis.", ephemeral=True)
                return
            lookup = {m.id: m for m in inter.guild.members}
            selected: List[discord.Member] = []
            for team_ids in snap["teams"]:
                for uid in team_ids:
                    m = lookup.get(int(uid))
                    if m and not m.bot:
                        selected.append(m)
            players_fp = self._players_fingerprint(selected)
            sizes_fp = self._sizes_fingerprint([len(team_ids) for team_ids in snap["teams"]])

        n = await clear_team_signatures(self.bot.settings.DB_PATH, inter.guild.id, session, players_fp, sizes_fp)
        await inter.response.send_message(f"🧽 Historique des compositions réinitialisé ({n} entrées supprimées).", ephemeral=True)


async def setup(bot: commands.Bot):
    cog = TeamCog(bot)
    await bot.add_cog(cog)
    # ✅ View persistante au démarrage (le custom_id doit correspondre au bouton)
    bot.add_view(TeamCog.RerollView(cog, timeout=None))
