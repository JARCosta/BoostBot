import os
import json
import aiofiles
import discord

class PlayerStatsStore:
    """Async helper for reading/writing player stats to guild-specific JSON files."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.file_path = os.path.join(
            os.path.dirname(__file__),
            "points",
            f"{guild_id}.json",
        )
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)

    async def load(self) -> dict:
        try:
            async with aiofiles.open(self.file_path, mode="r", encoding="utf-8") as f:
                data = await f.read()
                return json.loads(data) if data else {}
        except Exception:
            return {}

    async def save(self, stats: dict):
        async with aiofiles.open(self.file_path, mode="w", encoding="utf-8") as f:
            await f.write(json.dumps(stats, indent=2))

    def _ensure_entry(self, stats: dict, uid: int, name: str | None = None):
        key = str(uid)
        if key not in stats:
            stats[key] = {
                "points": 1000,
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "name": name or "",
            }
            return
        elif isinstance(stats[key], dict):
            stats[key].setdefault("points", 1000)
            stats[key].setdefault("wins", 0)
            stats[key].setdefault("losses", 0)
            stats[key].setdefault("draws", 0)
            stats[key].setdefault("name", name or "Undefined")

    def _get_member_name(self, guild: discord.Guild | None, uid: int) -> str | None:
        if not guild:
            return None
        member = guild.get_member(uid)
        if not member:
            return None
        return getattr(member, "display_name", None) or getattr(member, "name", None)

    async def ensure_users(self, guild: discord.Guild | None, user_ids: list[int] | set[int]):
        """
        Ensure all user IDs have entries in the stats store, creating them if necessary.
        """
        stats = await self.load()
        for uid in user_ids:
            self._ensure_entry(stats, uid, self._get_member_name(guild, uid))
        await self.save(stats)

    async def record_match(self, guild: discord.Guild | None, winners: list[int], losers: list[int], delta: int = 50):
        """
        Record the results of a match, updating points, wins, and losses.
        """
        stats = await self.load()
        for uid in list(winners) + list(losers):
            self._ensure_entry(stats, uid, self._get_member_name(guild, uid))
        for uid in winners:
            entry = stats[str(uid)]
            entry["points"] = int(entry.get("points", 1000)) + delta
            entry["wins"] = int(entry.get("wins", 0)) + 1
        for uid in losers:
            entry = stats[str(uid)]
            entry["points"] = int(entry.get("points", 1000)) - delta
            entry["losses"] = int(entry.get("losses", 0)) + 1
        await self.save(stats)

    async def record_draw(self, guild: discord.Guild | None, team_a: list[int], team_b: list[int]):
        """
        Record a draw, updating draws count for all players.
        """
        stats = await self.load()
        for uid in list(team_a) + list(team_b):
            self._ensure_entry(stats, uid, self._get_member_name(guild, uid))
        for uid in list(team_a) + list(team_b):
            entry = stats[str(uid)]
            entry["draws"] = int(entry.get("draws", 0)) + 1
        await self.save(stats)

    async def get_points_map(self) -> dict[str, int]:
        """
        Get a mapping of user IDs to their current points.
        """
        stats = await self.load()
        out: dict[str, int] = {}
        for k, v in stats.items():
            if isinstance(v, int):
                out[k] = v
            elif isinstance(v, dict):
                out[k] = int(v.get("points", 1000))
        return out
