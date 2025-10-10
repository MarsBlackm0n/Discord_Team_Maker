# app/cogs/arena.py
from __future__ import annotations
from typing import List, Dict, Optional
from datetime import datetime
import random
import discord
from discord import app_commands
from discord.ext import commands
from pathlib import Path
from ..team_logic import parse_mentions
from ..voice import create_and_move_voice  # ← voix centralisée (crée/réutilise, lobby, pin on top)
from ..db import (
    get_team_last, set_team_last,
    arena_get_active, arena_create, arena_update_scores_and_advance,
    arena_get_by_id, arena_set_state
)

def is_admin_or_owner(bot: commands.Bot, inter: discord.Interaction) -> bool:
    s = bot.settings
    if s.OWNER_ID and inter.user.id == s.OWNER_ID:
        return True
    m = inter.guild and inter.guild.get_member(inter.user.id)
    return bool(m and (m.guild_permissions.administrator or m.guild_permissions.manage_guild))


# ---------- algo round-robin : n joueurs -> n-1 rounds de duos ----------
def round_robin_duos(user_ids: List[int]) -> List[List[List[int]]]:
    """
    Retourne une liste de rounds.
    Chaque round = liste de duos [ [u1,u2], [u3,u4], ... ].
    Algo "circle method" pour couvrir chaque paire exactement 1 fois.
    """
    ids = user_ids[:]
    if len(ids) % 2 != 0:
        raise ValueError("Nombre de joueurs doit être pair pour l'Arena 2v2.")
    n = len(ids)
    if n < 4:
        raise ValueError("Minimum 4 joueurs.")
    fixed = ids[-1]
    rot = ids[:-1]
    rounds: List[List[List[int]]] = []
    for _ in range(n - 1):
        line = rot + [fixed]
        pairs = []
        for i in range(0, n, 2):
            pairs.append([line[i], line[i+1]])
        rounds.append(pairs)
        rot = rot[-1:] + rot[:-1]
    return rounds


# ---------- barème fixe Arena ----------
def points_for_rank(rank: int) -> int:
    """Barème Arena fixe : 1→8pts, 2→7, ..., 8→1. Tout le reste = 0."""
    return 9 - int(rank) if 1 <= int(rank) <= 8 else 0


class ArenaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="arena", description="Tournoi LoL Arena (2v2, classement individuel)")

    # ======================================================================
    # UI: Bouton “Reporter” + Modal
    # ======================================================================
    class ReportModal(discord.ui.Modal, title="Reporter le round"):
        """
        Modal paginé : on ne saisit que le TOP (1..8) pour une tranche de duos.
        - round_pairs_page: liste de duos pour CETTE page (ex: 5 duos max)
        - start_index: index global du 1er duo affiché (1-based)
        """
        def __init__(
            self,
            cog: "ArenaCog",
            *,
            guild: discord.Guild,
            round_pairs_page: list[list[int]],
            start_index: int,
        ):
            super().__init__(timeout=180)
            self.cog = cog
            self.guild = guild
            self.round_pairs_page = round_pairs_page
            self.start_index = int(start_index)
            self.inputs: list[discord.ui.TextInput] = []

            # 5 champs max par modal (Discord)
            for i, pair in enumerate(round_pairs_page, start=0):
                u1, u2 = pair
                m1 = guild.get_member(u1) if guild else None
                m2 = guild.get_member(u2) if guild else None
                duo_label = f"Duo {self.start_index + i}: {(m1.display_name if m1 else u1)} & {(m2.display_name if m2 else u2)}"

                ti = discord.ui.TextInput(
                    label=duo_label[:45],          # affichage non éditable (dans le label)
                    placeholder="Top (1..8)",      # on saisit juste le rang
                    required=False,
                    max_length=2,
                    style=discord.TextStyle.short,
                    custom_id=f"duo_{self.start_index + i}"
                )
                self.inputs.append(ti)
                self.add_item(ti)

        async def on_submit(self, interaction: discord.Interaction):
            # Recompose "#<index global>:<rank>" pour les champs non vides
            chunks = []
            for i, field in enumerate(self.inputs, start=0):
                v = (field.value or "").strip()
                if not v:
                    continue
                global_index = self.start_index + i
                chunks.append(f"#{global_index}:{v}")

            joined = " | ".join(chunks)
            if not joined:
                await interaction.response.send_message("ℹ️ Aucun top renseigné.", ephemeral=True)
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            await self.cog._process_report(interaction, joined)


    class ReportView(discord.ui.View):
        """View avec boutons 'Reporter …' par pages de 5 duos, + bouton Move."""
        def __init__(self, cog: "ArenaCog", *, guild: discord.Guild, round_pairs: list[list[int]]):
            super().__init__(timeout=None)
            self.cog = cog
            self.guild = guild
            self.round_pairs = round_pairs

            # --- Boutons Reporter paginés (5 duos max par modal) ---
            n = len(round_pairs)
            if n <= 5:
                # un seul bouton
                btn = discord.ui.Button(
                    label="Reporter",
                    emoji="📝",
                    style=discord.ButtonStyle.primary,
                    custom_id="arena_report_1"
                )

                async def cb(interaction: discord.Interaction, _start=1, _end=n):
                    page = round_pairs[_start-1:_end]
                    modal = ArenaCog.ReportModal(
                        self.cog, guild=self.guild,
                        round_pairs_page=page, start_index=_start
                    )
                    await interaction.response.send_modal(modal)

                btn.callback = cb
                self.add_item(btn)
            else:
                # plusieurs boutons: 1–5, 6–10, ...
                for start in range(1, n + 1, 5):
                    end = min(start + 5 - 1, n)
                    btn = discord.ui.Button(
                        label=f"Reporter {start}–{end}",
                        emoji="📝",
                        style=discord.ButtonStyle.primary,
                        custom_id=f"arena_report_{start}"
                    )

                    async def cb(interaction: discord.Interaction, _start=start, _end=end):
                        page = round_pairs[_start-1:_end]
                        modal = ArenaCog.ReportModal(
                            self.cog, guild=self.guild,
                            round_pairs_page=page, start_index=_start
                        )
                        await interaction.response.send_modal(modal)

                    btn.callback = cb
                    self.add_item(btn)

        # --- Bouton Move ---
        @discord.ui.button(label="Move", emoji="🚚", style=discord.ButtonStyle.secondary, custom_id="arena_move_button")
        async def move_round(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.defer(ephemeral=True, thinking=True)
            guild = interaction.guild
            pairs = self.round_pairs or []
            if not guild or not pairs:
                await interaction.followup.send("❌ Impossible de récupérer les duos du round.", ephemeral=True)
                return
            teams = []
            for (u1, u2) in pairs:
                m1 = guild.get_member(int(u1))
                m2 = guild.get_member(int(u2))
                team = [m for m in (m1, m2) if m and not m.bot]
                teams.append(team)
            sizes = [len(t) for t in teams]
            try:
                await create_and_move_voice(
                    interaction, teams, sizes,
                    ttl_minutes=90, reuse_existing=True,
                    base_name="Team", create_lobby=True, lobby_name="Lobby Tournoi",
                    pin_on_top=True,
                )
            except discord.Forbidden:
                await interaction.followup.send("⚠️ Permissions manquantes (Manage Channels / Move Members).", ephemeral=True)
                return
            await interaction.followup.send("✅ Équipes déplacées vers les salons vocaux.", ephemeral=True)

    # ======================================================================
    # Helper commun : traitement du report (commande + modal)
    # ======================================================================
    async def _process_report(self, inter: discord.Interaction, placements: str) -> bool:
        guild = inter.guild
        arena = guild and await arena_get_active(self.bot.settings.DB_PATH, guild.id)
        if not arena or arena["state"] != "running":
            await inter.followup.send("ℹ️ Aucun tournoi Arena en cours.", ephemeral=True)
            return False

        cur_round = arena["current_round"]
        schedule = arena["schedule"]
        if cur_round < 1 or cur_round > len(schedule):
            await inter.followup.send("❌ Plus de round à jouer.", ephemeral=True)
            return False

        expected_pairs = schedule[cur_round - 1]  # [[u1,u2], ...] (Duo 1..N)
        expected_set = {tuple(sorted(p)) for p in expected_pairs}
        duo_count = len(expected_pairs)

        def _parse_duo_index(token: str) -> Optional[int]:
            t = token.strip().lower()
            for pref in ("#", "d", "duo"):
                if t.startswith(pref):
                    t = t[len(pref):]
            try:
                i = int(t)
                if 1 <= i <= duo_count:
                    return i
            except Exception:
                pass
            return None

        chunks = [c.strip() for c in (placements or "").split("|") if c.strip()]
        if not chunks:
            await inter.followup.send("❌ Saisie vide. Ex: `#1:1 | 3:6 | @A @B:7`.", ephemeral=True)
            return False

        used_ranks: set[int] = set()
        used_pairs: set[tuple[int, int]] = set()
        new_scores: dict[int, int] = {}

        for ch in chunks:
            if ":" not in ch:
                await inter.followup.send(f"❌ Il manque le ':top' dans « {ch} » (ex.: ':1').", ephemeral=True)
                return False
            left, right = ch.rsplit(":", 1)
            left = left.strip()
            try:
                rank = int(right.strip())
            except ValueError:
                await inter.followup.send(f"❌ Top invalide dans « {ch} » (attendu 1..8).", ephemeral=True)
                return False
            if rank < 1 or rank > 8:
                await inter.followup.send(f"❌ Top hors borne dans « {ch} » (1..8).", ephemeral=True)
                return False
            if rank in used_ranks:
                await inter.followup.send(f"❌ Le top {rank} est déjà attribué dans ta saisie.", ephemeral=True)
                return False

            # 1) Essayer un index de duo (#1, 1, d2, duo3)
            idx = _parse_duo_index(left)
            pair: tuple[int, int] | None = None
            if idx is not None:
                u1, u2 = expected_pairs[idx - 1]
                pair = tuple(sorted((u1, u2)))
            else:
                # 2) Sinon parse mentions
                ms = parse_mentions(guild, left)
                ms = [m for m in ms if not m.bot]
                if len(ms) != 2:
                    await inter.followup.send(f"❌ Impossible de lire un duo dans: « {ch} »", ephemeral=True)
                    return False
                a, b = sorted([ms[0].id, ms[1].id])
                pair = (a, b)
                if pair not in expected_set:
                    await inter.followup.send(f"❌ Ce duo n'est pas prévu au round courant: « {ch} »", ephemeral=True)
                    return False

            if pair in used_pairs:
                await inter.followup.send("❌ Duo répété dans ta saisie (index/mentions en double).", ephemeral=True)
                return False

            used_ranks.add(rank)
            used_pairs.add(pair)

            pts = points_for_rank(rank)  # 1→8 pts pour chacun
            a, b = pair
            new_scores[a] = new_scores.get(a, 0) + pts
            new_scores[b] = new_scores.get(b, 0) + pts

        # Applique les points partiels saisis et avance si besoin
        await arena_update_scores_and_advance(self.bot.settings.DB_PATH, arena["id"], new_scores)
        arena2 = await arena_get_by_id(self.bot.settings.DB_PATH, arena["id"])

        await inter.followup.send("✅ Résultat enregistré. Classement mis à jour.", ephemeral=True)
        await self._post_scores_embed(inter.channel, arena2["participants"], arena2["scores"])

        if arena2["state"] == "running":
            lookup = {m.id: m for m in inter.guild.members}
            members = [lookup[i] for i in arena2["participants"] if i in lookup]
            await self._post_round_embed(inter.channel, members, arena2["schedule"], current_round=arena2["current_round"])
        else:
            await self._post_podium_embed(inter.channel, arena2["participants"], arena2["scores"])

        return True

    # ======================================================================
    # Commandes
    # ======================================================================

    @group.command(name="start", description="Démarrer un tournoi Arena (duos qui tournent chaque round).")
    @app_commands.describe(
        rounds="(Optionnel) nombre de rounds. Si vide: n-1 (tout le monde avec tout le monde).",
        members="(Optionnel) liste de @mentions; sinon dernier /team, sinon ton vocal."
    )
    async def start(self, inter: discord.Interaction, rounds: Optional[int] = None, members: str = ""):
        await inter.response.defer(ephemeral=True, thinking=True)
        if not is_admin_or_owner(self.bot, inter):
            await inter.followup.send("⛔ Réservé aux admins/owner.", ephemeral=True); return

        guild = inter.guild
        if not guild:
            await inter.followup.send("❌ À utiliser sur un serveur.", ephemeral=True); return

        # Récup participants
        selected: List[discord.Member] = []
        if members.strip():
            selected = parse_mentions(guild, members)
        else:
            snap = await get_team_last(self.bot.settings.DB_PATH, guild.id)
            if snap and snap.get("teams"):
                lookup = {m.id: m for m in guild.members}
                ids = [int(uid) for team_ids in snap["teams"] for uid in team_ids]
                selected = [lookup[i] for i in ids if i in lookup and not lookup[i].bot]
            if not selected:
                me = guild.get_member(inter.user.id)
                if me and me.voice and me.voice.channel:
                    selected = [m for m in me.voice.channel.members if not m.bot]

        if len(selected) < 4 or len(selected) % 2 != 0:
            await inter.followup.send("❌ Il faut un nombre **pair** de joueurs (min 4).", ephemeral=True); return
        if len(selected) > 16:
            await inter.followup.send("❌ Maximum 16 joueurs (8 duos).", ephemeral=True); return

        user_ids = [m.id for m in selected]
        schedule_full = round_robin_duos(user_ids)  # n-1 rounds
        full_rounds = len(schedule_full)

        if not rounds or rounds <= 0 or rounds > full_rounds:
            rounds = full_rounds  # par défaut n-1

        schedule = schedule_full[:rounds]
        arena_id = await arena_create(
            self.bot.settings.DB_PATH,
            guild.id, inter.user.id, rounds, user_ids, schedule
        )

        await inter.followup.send(
            f"✅ Tournoi Arena lancé (id `{arena_id}`) — **{len(selected)}** joueurs, **{rounds}** rounds.",
            ephemeral=True
        )
        await self._post_round_embed(inter.channel, selected, schedule, current_round=1)
        await self._post_scores_embed(inter.channel, user_ids, {})  # scores 0 au départ

    @group.command(name="round", description="Afficher le round courant à jouer.")
    async def round(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        guild = inter.guild
        arena = guild and await arena_get_active(self.bot.settings.DB_PATH, guild.id)
        if not arena or arena["state"] != "running":
            await inter.followup.send("ℹ️ Aucun tournoi Arena en cours.", ephemeral=True); return

        ids = arena["participants"]
        lookup = {m.id: m for m in inter.guild.members}
        members = [lookup[i] for i in ids if i in lookup]
        await inter.followup.send("📣 Round courant affiché dans le salon.", ephemeral=True)
        await self._post_round_embed(inter.channel, members, arena["schedule"], current_round=arena["current_round"])

    @group.command(name="status", description="Afficher le classement et l'état du tournoi Arena.")
    async def status(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        guild = inter.guild
        arena = guild and await arena_get_active(self.bot.settings.DB_PATH, guild.id)
        if not arena:
            await inter.followup.send("ℹ️ Aucun tournoi Arena actif.", ephemeral=True); return
        ids = arena["participants"]
        await inter.followup.send("📊 Statut posté.", ephemeral=True)
        await self._post_scores_embed(
            inter.channel, ids, arena["scores"],
            title_suffix=f"(Round {min(arena['current_round'], arena['rounds_total'])}/{arena['rounds_total']}, état: {arena['state']})"
        )

    @group.command(
        name="report",
        description="Reporter le résultat d'un round. Format: '#1:1 | 3:6 | @A @B:7' (tops 1..8)."
    )
    @app_commands.describe(
        placements="Duos avec top: '#1:1 | 3:6 | @A @B:7'. Tu peux n'envoyer que tes duos (report partiel)."
    )
    async def report(self, inter: discord.Interaction, placements: str):
        await inter.response.defer(ephemeral=True, thinking=True)
        if not is_admin_or_owner(self.bot, inter):
            await inter.followup.send("⛔ Réservé aux admins/owner.", ephemeral=True); return
        await self._process_report(inter, placements)

    @group.command(name="stop", description="Terminer le tournoi Arena en cours et afficher le podium.")
    async def stop(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        if not is_admin_or_owner(self.bot, inter):
            await inter.followup.send("⛔ Réservé aux admins/owner.", ephemeral=True); return

        guild = inter.guild
        arena = guild and await arena_get_active(self.bot.settings.DB_PATH, guild.id)
        if not arena:
            await inter.followup.send("ℹ️ Aucun tournoi Arena actif.", ephemeral=True); return

        await arena_set_state(self.bot.settings.DB_PATH, arena["id"], "finished")
        await inter.followup.send("🏁 Tournoi arrêté. Podium affiché dans le salon.", ephemeral=True)
        await self._post_podium_embed(inter.channel, arena["participants"], arena["scores"])

    @group.command(name="cancel", description="Annuler (supprimer) le tournoi Arena en cours.")
    async def cancel(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        if not is_admin_or_owner(self.bot, inter):
            await inter.followup.send("⛔ Réservé aux admins/owner.", ephemeral=True); return
        guild = inter.guild
        arena = guild and await arena_get_active(self.bot.settings.DB_PATH, guild.id)
        if not arena:
            await inter.followup.send("ℹ️ Aucun tournoi Arena actif.", ephemeral=True); return
        await arena_set_state(self.bot.settings.DB_PATH, arena["id"], "cancelled")
        await inter.followup.send("🛑 Tournoi Arena annulé.", ephemeral=True)

    # ======================================================================
    # Rendu embeds
    # ======================================================================
    async def _post_round_embed(self, channel: discord.abc.Messageable, members: List[discord.Member],
                                schedule: List[List[List[int]]], current_round: int):
        lookup = {m.id: m for m in members}
        pairs = schedule[current_round - 1]
        lines = []
        for i, (u1, u2) in enumerate(pairs, start=1):
            m1 = lookup.get(u1); m2 = lookup.get(u2)
            lines.append(f"**Duo {i}** — {m1.mention if m1 else f'<@{u1}>'} & {m2.mention if m2 else f'<@{u2}>'}")
        emb = discord.Embed(title=f"🧭 Arena — Round {current_round}", color=discord.Color.blurple())
        emb.description = "\n".join(lines) or "_(vide)_"
        emb.set_footer(text="Saisie rapide : '#1:1 | 3:6 | @A @B:7'  (tops 1..8)")

        # Sauvegarde 'last team' pour que /move sache déplacer selon Duo 1..N
        try:
            import time
            snapshot = {
                "mode": "arena_round",
                "team_count": len(pairs),
                "sizes": [2] * len(pairs),
                "teams": [[int(u1), int(u2)] for (u1, u2) in pairs],
                "ratings": {str(uid): 0.0 for uid in [x for duo in pairs for x in duo]},
                "params": {"arena_round": int(current_round)},
                "created_by": members[0].id if members else 0,
                "created_at": int(time.time()),
            }
            guild_id = members[0].guild.id if members else None
            if guild_id:
                await set_team_last(self.bot.settings.DB_PATH, guild_id, snapshot)
        except Exception:
            pass

        the_guild = members[0].guild if members else None
        view = self.ReportView(self, guild=the_guild, round_pairs=pairs)
        await channel.send(embed=emb, view=view)

    async def _post_scores_embed(self, channel: discord.abc.Messageable, participants: List[int],
                                 scores: Dict[str, int], title_suffix: str = ""):
        norm = {int(k): int(v) for k, v in (scores or {}).items()}
        rows = sorted([(uid, norm.get(uid, 0)) for uid in participants], key=lambda x: (-x[1], x[0]))
        emb = discord.Embed(title=f"📊 Arena — Classement {title_suffix}".strip(), color=discord.Color.gold())
        desc = []
        for rank, (uid, pts) in enumerate(rows, start=1):
            desc.append(f"**#{rank}** — <@{uid}> — **{pts}** pts")
        emb.description = "\n".join(desc) or "_(personne)_"
        await channel.send(embed=emb)


    async def _post_podium_embed(self, channel: discord.abc.Messageable, participants: list[int],
                                scores: dict[str, int]):
        MAX_BYTES = 7_500_000  # ~7.5 MB pour rester sous la limite standard (~8MB)
        norm = {int(k): int(v) for k, v in (scores or {}).items()}
        rows = sorted([(uid, norm.get(uid, 0)) for uid in participants], key=lambda x: (-x[1], x[0]))

        emb = discord.Embed(title="🏆 Arena — Podium", color=discord.Color.brand_green())

        # Podium
        medals = ["🥇", "🥈", "🥉"]
        for i in range(min(3, len(rows))):
            uid, pts = rows[i]
            emb.add_field(name=medals[i], value=f"<@{uid}> — **{pts}** pts", inline=False)

        enable_trash_talk = getattr(self.bot.settings, "ENABLE_TRASH_TALK", True)
        file_to_send: discord.File | None = None

        if enable_trash_talk and len(rows) >= 2:
            loser_uid, loser_pts = rows[-1]
            jokes = [
                "A mon avis, tu devrais poser cette Goudale et te servir un verre d'eau.",
                "Chez LRM, on ne laisse personne derrière… sauf toi.",
                "Oui on sait, c'est parce que les champions en face étaient broken",
                "La VAR est formelle, t'as bien perdu.",
                "Tu devrais aller jouer à Minecraft.",
                "Faut se rendre à l'évidence, t'es trop vieux pour ces conneries.",
            ]
            emb.add_field(
                name="🖕 Loser Award",
                value=f"**<@{loser_uid}>** — {loser_pts} pts\n*{random.choice(jokes)}*",
                inline=False,
            )

            # 🔽 Cherche un fichier image/gif local raisonnable
            try:
                assets_dir = Path(__file__).parents[1] / "assets" / "arena_gifs"
                candidates = []
                if assets_dir.exists():
                    for p in assets_dir.iterdir():
                        if p.is_file() and p.suffix.lower() in {".gif", ".png", ".jpg", ".jpeg", ".webp"}:
                            try:
                                size = p.stat().st_size
                            except Exception:
                                size = 0
                            if 0 < size <= MAX_BYTES:
                                candidates.append((p, size))
                # Choisit un au hasard parmi ceux qui passent la taille
                if candidates:
                    fp, size = random.choice(candidates)
                    # Important: le filename doit matcher l’URL attachment://
                    emb.set_image(url=f"attachment://{fp.name}")
                    file_to_send = discord.File(fp.open("rb"), filename=fp.name)
                else:
                    # (Optionnel) log léger pour debug
                    print("[arena podium] Aucun GIF/image éligible (taille/extension) dans", assets_dir)
            except Exception as e:
                print("[arena podium] Erreur sélection GIF:", e)
                file_to_send = None  # on enverra sans image

        # Envoi
        try:
            if file_to_send:
                await channel.send(embed=emb, file=file_to_send)
            else:
                await channel.send(embed=emb)
        except Exception as e:
            print("[arena podium] Envoi message avec image échoué:", e)
            # Dernier fallback: envoyer l’embed sans image
            await channel.send(embed=emb)


async def setup(bot: commands.Bot):
    cog = ArenaCog(bot)
    await bot.add_cog(cog)
    # Pour rendre la view persistante après reboot, il faudrait une ReportView tolérante à guild=None/round_pairs=[],
    # puis enregistrer ici une instance "globale" :
    # bot.add_view(ArenaCog.ReportView(cog, guild=None, round_pairs=[]))
