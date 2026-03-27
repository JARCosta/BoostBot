import math

import discord

from logging_config import setup_logging

from .lobby import Lobby, format_player_mentions
from .stats_store import PlayerStatsStore

log = setup_logging("boost_bot.views")

PRIVILEGED_USER_ID = 368755002824589322


class JoinView(discord.ui.View):
    """View for joining a game lobby and managing match lifecycle."""

    def __init__(self, guild_id: int, lobby: Lobby, timeout: float | None = 3600):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.lobby = lobby
        self.team_a: list[int] = []
        self.team_b: list[int] = []
        self.points_delta = 25
        self.forfeit_votes_a: set[int] = set()
        self.forfeit_votes_b: set[int] = set()

    def _add_match_buttons(self):
        btn_a = discord.ui.Button(label="Team A Wins", style=discord.ButtonStyle.success)
        async def a_cb(interaction: discord.Interaction):
            if not self.lobby.started:
                return await interaction.response.send_message("Teams not formed yet.", ephemeral=True)
            await interaction.response.defer()
            await self.declare_winner(interaction, self.team_a, self.team_b)
        btn_a.callback = a_cb
        self.add_item(btn_a)

        btn_draw = discord.ui.Button(label="Draw", style=discord.ButtonStyle.secondary)
        async def draw_cb(interaction: discord.Interaction):
            if not self.lobby.started:
                return await interaction.response.send_message("Teams not formed yet.", ephemeral=True)
            await interaction.response.defer()
            await self.declare_draw(interaction)
        btn_draw.callback = draw_cb
        self.add_item(btn_draw)

        btn_b = discord.ui.Button(label="Team B Wins", style=discord.ButtonStyle.primary)
        async def b_cb(interaction: discord.Interaction):
            if not self.lobby.started:
                return await interaction.response.send_message("Teams not formed yet.", ephemeral=True)
            await interaction.response.defer()
            await self.declare_winner(interaction, self.team_b, self.team_a)
        btn_b.callback = b_cb
        self.add_item(btn_b)

        btn_ff = discord.ui.Button(label="Forfeit", style=discord.ButtonStyle.secondary)
        async def ff_cb(interaction: discord.Interaction):
            await self._forfeit_action(interaction)
        btn_ff.callback = ff_cb
        self.add_item(btn_ff)

        btn_c = discord.ui.Button(label="Cancel Match", style=discord.ButtonStyle.danger)
        async def c_cb(interaction: discord.Interaction):
            await interaction.response.defer()
            await self.cancel_match_action(interaction)
        btn_c.callback = c_cb
        self.add_item(btn_c)

    async def update_queue_message(self, interaction: discord.Interaction, note: str | None = None, target_message: discord.Message | None = None):
        try:
            host = interaction.guild.get_member(self.lobby.host_id) if interaction.guild else None
            host_text = host.mention if host else f"<@{self.lobby.host_id}>"

            if not self.lobby.started:
                count = len(self.lobby.players)
                players_text = format_player_mentions(interaction.guild, self.lobby.players)
                embed = discord.Embed(
                    title=f"🎮 {self.lobby.title}",
                    description=f"**Players:** {count}",
                    color=discord.Color.blue()
                )
                embed.add_field(name="Host", value=host_text, inline=False)
                embed.add_field(name="Joined", value=players_text, inline=False)
                if note:
                    embed.add_field(name="ℹ️ Info", value=note, inline=False)
            elif not self.lobby.finished:
                store = PlayerStatsStore(interaction.guild.id)
                points_map = await store.get_points_map()
                team_a_total = sum(points_map.get(str(uid), 1000) for uid in self.team_a)
                team_b_total = sum(points_map.get(str(uid), 1000) for uid in self.team_b)
                mentions_a = [
                    interaction.guild.get_member(uid).mention if interaction.guild.get_member(uid) else f"<@{uid}>"
                    for uid in self.team_a
                ]
                mentions_b = [
                    interaction.guild.get_member(uid).mention if interaction.guild.get_member(uid) else f"<@{uid}>"
                    for uid in self.team_b
                ]
                embed = discord.Embed(
                    title=f"⚔️ {self.lobby.title} — Game Started",
                    description="Teams are ready to play!",
                    color=discord.Color.orange()
                )
                team_a_value = ', '.join(mentions_a)
                ff_a = len(self.forfeit_votes_a)
                if ff_a:
                    team_a_value += f" ({ff_a}/{self._forfeit_threshold(len(self.team_a))} forfeit votes)"

                team_b_value = ', '.join(mentions_b)
                ff_b = len(self.forfeit_votes_b)
                if ff_b:
                    team_b_value += f" ({ff_b}/{self._forfeit_threshold(len(self.team_b))} forfeit votes)"

                embed.add_field(name="Host", value=host_text, inline=False)
                embed.add_field(name=f"🔵 Team A ({team_a_total} pts)", value=team_a_value, inline=False)
                embed.add_field(name=f"🔴 Team B ({team_b_total} pts)", value=team_b_value, inline=False)
                if note:
                    embed.add_field(name="ℹ️ Info", value=note, inline=False)
            else:
                embed = discord.Embed(
                    title=f"✅ {self.lobby.title} — Match Ended",
                    description="Final results recorded.",
                    color=discord.Color.green()
                )
                embed.add_field(name="Host", value=host_text, inline=False)
                if note:
                    embed.add_field(name="Results", value=note, inline=False)

            message = None
            # Prefer an explicit target message if provided; if it fails, don't create new messages
            if target_message:
                try:
                    await target_message.edit(embed=embed, view=self)
                    return target_message
                except Exception as e:
                    log.warning("Failed to edit target message: %s", e)
                    return None

            # Fallback to interaction message if available (e.g., button interactions)
            if interaction.message:
                try:
                    await interaction.message.edit(embed=embed, view=self)
                    return interaction.message
                except Exception as e:
                    log.warning("Failed to edit interaction message: %s", e)
                    message = None

            # If no message edited yet, send or follow up
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=embed, view=self)
                message = await interaction.original_response()
            else:
                message = await interaction.followup.send(embed=embed, view=self, wait=True)

            return message
        except Exception as e:
            log.exception("Exception in update_queue_message: %s", e)
            return None

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success)
    async def join_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not interaction.guild or interaction.guild.id != self.guild_id:
            return await interaction.response.send_message("Wrong server.", ephemeral=True)
        if self.lobby.started:
            return await interaction.response.send_message("Game already started.", ephemeral=True)
        if len(self.lobby.players) >= 10 and interaction.user.id not in self.lobby.players:
            return await interaction.response.send_message("Queue is full (10 players max).", ephemeral=True)
        joined = self.lobby.add(interaction.user.id)
        if joined:
            store = PlayerStatsStore(interaction.guild.id)
            await store.ensure_users(interaction.guild, [interaction.user.id])
            await interaction.response.defer()
            await self.update_queue_message(interaction,
                note="Press Join to enter. Host/Admin can Start or Cancel."
            )
        else:
            await interaction.response.send_message("Could not join.", ephemeral=True)

    @staticmethod
    def _partition_teams(player_points: list[tuple[int, int]]) -> tuple[list[int], list[int]]:
        """Partition an even number of players into two balanced teams of equal size.

        Uses subset-sum DP to find the most balanced half-sized partition.

        Args:
            player_points: List of (uid, points) tuples, sorted by points descending

        Returns:
            Tuple of (team_a, team_b) player lists
        """
        if not player_points:
            return [], []

        n = len(player_points)
        if n % 2 != 0:
            # If somehow odd, leave one out: last player goes to smaller team
            player_points = player_points[:-1]
            n = len(player_points)

        team_size = n // 2
        total_points = sum(pts for _, pts in player_points)
        target = total_points / 2

        # DP to find best team_size subset closest to half the total points
        # dp[(sum, count)] = set of player UIDs
        dp = {(0, 0): set()}

        for uid, pts in player_points:
            new_entries = {}
            for (current_sum, count), current_set in dp.items():
                if count < team_size:  # Only add if we haven't reached target size yet
                    new_sum = current_sum + pts
                    new_count = count + 1
                    new_set = current_set | {uid}
                    key = (new_sum, new_count)

                    if key not in dp and key not in new_entries:
                        new_entries[key] = new_set
                    elif key in new_entries:
                        # If duplicate, keep one with better balance (shouldn't happen often)
                        if abs(new_sum - target) < abs(key[0] - target):
                            new_entries[key] = new_set

            dp.update(new_entries)

        # Find the half-size subset closest to target points
        half_subsets = {k: v for k, v in dp.items() if k[1] == team_size}
        if half_subsets:
            best_key = min(half_subsets.keys(), key=lambda x: abs(x[0] - target))
            team_a_uids = list(half_subsets[best_key])
            team_b_uids = [uid for uid, _ in player_points if uid not in team_a_uids]
        else:
            # Fallback: simple greedy if DP fails
            team_a_uids = []
            team_b_uids = []
            team_a_points = 0
            team_b_points = 0

            for uid, pts in player_points:
                if len(team_a_uids) < team_size and (len(team_b_uids) == team_size or team_a_points <= team_b_points):
                    team_a_uids.append(uid)
                    team_a_points += pts
                else:
                    team_b_uids.append(uid)
                    team_b_points += pts

        return team_a_uids, team_b_uids

    @discord.ui.button(label="Start", style=discord.ButtonStyle.primary)
    async def start_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        is_admin = (
            interaction.user.guild_permissions.administrator
            if interaction.guild
            else False
        )
        is_privileged = interaction.user.id == PRIVILEGED_USER_ID
        if interaction.user.id != self.lobby.host_id and not (is_admin or is_privileged):
            return await interaction.response.send_message(
                "Only the host or a server admin can start.",
                ephemeral=True
            )
        if len(self.lobby.players) % 2 != 0:
            return await interaction.response.send_message(
                "Need an even number of players to start.",
                ephemeral=True
            )
        self.lobby.started = True
        players = list(self.lobby.players)

        store = PlayerStatsStore(interaction.guild.id)
        points_map = await store.get_points_map()

        # Create balanced teams using optimized partition algorithm
        player_points = [(uid, points_map.get(str(uid), 1000)) for uid in players]
        player_points.sort(key=lambda x: x[1], reverse=True)

        team_a_uids, team_b_uids = self._partition_teams(player_points)
        self.team_a = team_a_uids
        self.team_b = team_b_uids

        self.clear_items()
        self._add_match_buttons()
        await interaction.response.defer()
        await self.update_queue_message(interaction,
            note="Use Team A Wins / Team B Wins, or Cancel Match."
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        is_admin = (
            interaction.user.guild_permissions.administrator
            if interaction.guild
            else False
        )
        is_privileged = interaction.user.id == PRIVILEGED_USER_ID
        if interaction.user.id != self.lobby.host_id and not (is_admin or is_privileged):
            return await interaction.response.send_message(
                "Only the host or a server admin can cancel.",
                ephemeral=True
            )
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.response.defer()
        await self.update_queue_message(interaction, note="Queue canceled by host.")

    @staticmethod
    def _forfeit_threshold(team_size: int) -> int:
        """Minimum votes needed: more than 66% of the team (ceiling of 2/3)."""
        return -(-team_size * 2 // 3)

    async def _forfeit_action(self, interaction: discord.Interaction):
        uid = interaction.user.id
        if self.lobby.finished:
            return await interaction.response.send_message("Match already ended.", ephemeral=True)
        if uid in self.team_a:
            team_votes, team, other_team = self.forfeit_votes_a, self.team_a, self.team_b
        elif uid in self.team_b:
            team_votes, team, other_team = self.forfeit_votes_b, self.team_b, self.team_a
        else:
            return await interaction.response.send_message("You're not in this match.", ephemeral=True)

        if uid in team_votes:
            team_votes.discard(uid)
        else:
            team_votes.add(uid)

        await interaction.response.defer()

        threshold = self._forfeit_threshold(len(team))
        if len(team_votes) >= threshold:
            store = PlayerStatsStore(interaction.guild.id)
            await store.record_match(interaction.guild, other_team, team, delta=self.points_delta)
            self.lobby.finished = True
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
            winners = ', '.join(
                interaction.guild.get_member(u).mention if interaction.guild.get_member(u) else f'<@{u}>'
                for u in other_team
            )
            losers = ', '.join(
                interaction.guild.get_member(u).mention if interaction.guild.get_member(u) else f'<@{u}>'
                for u in team
            )
            await self.update_queue_message(interaction,
                note=f"Team forfeited!\nWinners (+{self.points_delta}): {winners}\nLosers (-{self.points_delta}): {losers}"
            )
        else:
            await self.update_queue_message(interaction)

    async def declare_winner(self, interaction: discord.Interaction, winning_team, losing_team):
        is_admin = (
            interaction.user.guild_permissions.administrator
            if interaction.guild
            else False
        )
        is_privileged = interaction.user.id == PRIVILEGED_USER_ID
        if interaction.user.id != self.lobby.host_id and not (is_admin or is_privileged):
            return await interaction.response.send_message(
                "Only the host or a server admin can declare the winner.",
                ephemeral=True
            )
        if self.lobby.finished:
            return await interaction.response.send_message("Already awarded.", ephemeral=True)
        store = PlayerStatsStore(interaction.guild.id)
        await store.record_match(interaction.guild, winning_team, losing_team, delta=self.points_delta)
        self.lobby.finished = True
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        winners = ', '.join([
            interaction.guild.get_member(uid).mention
            if interaction.guild.get_member(uid)
            else f'<@{uid}>'
            for uid in winning_team
        ])
        losers = ', '.join([
            interaction.guild.get_member(uid).mention
            if interaction.guild.get_member(uid)
            else f'<@{uid}>'
            for uid in losing_team
        ])
        await self.update_queue_message(interaction,
            note=f"Winners (+{self.points_delta}): {winners}\nLosers (-{self.points_delta}): {losers}"
        )

    async def declare_draw(self, interaction: discord.Interaction):
        is_admin = (
            interaction.user.guild_permissions.administrator
            if interaction.guild
            else False
        )
        is_privileged = interaction.user.id == PRIVILEGED_USER_ID
        if interaction.user.id != self.lobby.host_id and not (is_admin or is_privileged):
            return await interaction.response.send_message(
                "Only the host or a server admin can declare a draw.",
                ephemeral=True
            )
        if self.lobby.finished:
            return await interaction.response.send_message("Already awarded.", ephemeral=True)
        store = PlayerStatsStore(interaction.guild.id)
        await store.record_draw(interaction.guild, self.team_a, self.team_b)
        self.lobby.finished = True
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        team_a_mentions = ', '.join([
            interaction.guild.get_member(uid).mention
            if interaction.guild.get_member(uid)
            else f'<@{uid}>'
            for uid in self.team_a
        ])
        team_b_mentions = ', '.join([
            interaction.guild.get_member(uid).mention
            if interaction.guild.get_member(uid)
            else f'<@{uid}>'
            for uid in self.team_b
        ])
        await self.update_queue_message(interaction,
            note=f"Draw! 🤝\nTeam A: {team_a_mentions}\nTeam B: {team_b_mentions}"
        )

    async def cancel_match_action(self, interaction: discord.Interaction):
        is_admin = (
            interaction.user.guild_permissions.administrator
            if interaction.guild
            else False
        )
        is_privileged = interaction.user.id == PRIVILEGED_USER_ID
        if interaction.user.id != self.lobby.host_id and not (is_admin or is_privileged):
            return await interaction.response.send_message(
                "Only the host or a server admin can cancel the match.",
                ephemeral=True
            )
        if self.lobby.finished:
            return await interaction.response.send_message("Match already ended.", ephemeral=True)

        self.lobby.finished = True
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await self.update_queue_message(interaction,
            note="Match canceled by host. No points awarded."
        )
