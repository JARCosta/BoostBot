import discord

class Lobby:
    """Represents a game lobby."""

    def __init__(self, host_id: int, title: str = "Queue"):
        self.host_id = host_id
        self.title = title
        self.players = set()
        self.started = False
        self.finished = False

    def add(self, user_id: int):
        if self.started:
            return False
        self.players.add(user_id)
        return True


def format_player_mentions(guild: discord.Guild | None, player_ids):
    mentions = []
    for uid in player_ids:
        member = guild.get_member(uid) if guild else None
        mentions.append(member.mention if member else f"<@{uid}>")
    return ", ".join(mentions) if mentions else "No players yet."
