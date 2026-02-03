"""Discord bot entrypoint wired to modular helpers."""
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

# from .config import bot
from .lobby import Lobby
from .stats_store import PlayerStatsStore
from .views import JoinView

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guild_messages = True

bot = commands.Bot(command_prefix="!", intents=intents, sync_commands=True)



# Per-guild lobby state: guild_id -> Lobby
guild_lobbies: dict[int, Lobby] = {}
# Per-guild queue message for reuse
guild_queue_messages: dict[int, discord.Message] = {}


@bot.event
async def on_ready():
    # Sync commands globally for availability
    print("Syncing commands globally...")
    # try:
    synced = await bot.tree.sync()
    print(f"✓ Global sync complete. Synced {len(synced)} command(s).")
    # except Exception as e:
    #     print(f"✗ Failed to sync commands globally: {e}")
    #     print("  - Ensure bot has 'applications.commands' OAuth2 scope")
    #     print("  - Check bot permissions in server(s)")

    print(f"Logged in as {bot.user} (id: {bot.user.id})")





@bot.tree.command(name="startqueue", description="Create a game queue")
@discord.app_commands.describe(title="Optional title to display at the top of the queue")
async def startqueue(interaction: discord.Interaction, title: str | None = None):
    if interaction.guild is None:
        return await interaction.response.send_message("Use this in a server.", ephemeral=True)
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(
            "Only server admins can start a queue.",
            ephemeral=True
        )

    gid = interaction.guild.id

    # Always create a fresh lobby
    lobby = Lobby(host_id=interaction.user.id, title=title or "Queue")
    lobby.add(interaction.user.id)
    guild_lobbies[gid] = lobby

    store = PlayerStatsStore(interaction.guild.id)
    await store.ensure_users(interaction.guild, [interaction.user.id])

    view = JoinView(gid, lobby)
    await interaction.response.defer()
    msg = await view.update_queue_message(interaction,
        note="Press Join to enter. Host/Admin can Start or Cancel."
    )
    if msg:
        guild_queue_messages[gid] = msg


@bot.tree.command(name="addtoqueue", description="Add a mentioned user to the current queue")
@discord.app_commands.describe(user="User to add to the queue")
async def addtoqueue(interaction: discord.Interaction, user: discord.Member):
    if interaction.guild is None:
        return await interaction.response.send_message("Use this in a server.", ephemeral=True)

    gid = interaction.guild.id
    lobby = guild_lobbies.get(gid)
    if not lobby or lobby.started:
        return await interaction.response.send_message("No open queue. Start one first.", ephemeral=True)

    is_admin = interaction.user.guild_permissions.administrator if interaction.guild else False
    if interaction.user.id != lobby.host_id and not is_admin:
        return await interaction.response.send_message("Only the host or a server admin can add players.", ephemeral=True)

    added = lobby.add(user.id)
    if not added:
        return await interaction.response.send_message("Could not add user (queue may have started).", ephemeral=True)

    store = PlayerStatsStore(interaction.guild.id)
    await store.ensure_users(interaction.guild, [user.id])

    view = JoinView(gid, lobby)

    msg = guild_queue_messages.get(gid)
    if msg:
        # Try to edit the existing queue message directly
        # try:
        await msg.edit(embed=None, view=view)
        # Update embed inline
        await view.update_queue_message(interaction,
            note=f"{user.display_name} was added by {interaction.user.display_name}.",
            target_message=msg
        )
        await interaction.response.send_message(f"{user.display_name} added to the queue.", ephemeral=True)
        return
        # except Exception as e:
        #     print(f"Failed to edit existing queue message: {e}")
        #     pass

    # Fallback if message not found
    await interaction.response.send_message("Could not update queue message. Please try again.", ephemeral=True)


@bot.tree.command(name="leaderboard", description="Show all players ranked by points")
async def leaderboard(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Use this in a server.", ephemeral=True)

    store = PlayerStatsStore(interaction.guild.id)
    stats = await store.load()

    if not stats:
        return await interaction.response.send_message("No stats available.", ephemeral=True)

    rows = []
    for _, data in stats.items():
        if not isinstance(data, dict):
            continue
        wins = int(data.get("wins", 0))
        losses = int(data.get("losses", 0))
        draws = int(data.get("draws", 0))
        rows.append({
            "name": data.get("name", "Unknown"),
            "elo": data.get("points", 1000),
            "wins": wins,
            "loses": losses,
            "draws": draws,
        })

    rows.sort(key=lambda r: r["elo"], reverse=True)

    header = f"{'#':<2} | {'Player':<12} | {'Elo':>4} | {'W-D-L':^7} | {'WR':>5}  \n"
    header += "-" * 44

    lines = [header]
    for i, r in enumerate(rows, start=1):
        name = (r["name"] or "Unknown")[:12]
        total = r["wins"] + r["loses"]
        win_rate = (r["wins"] / total * 100) if total > 0 else 0.0
        record = f"{r['wins']}-{r['draws']}-{r['loses']}"
        lines.append(
            f"{i:<2} | {name.capitalize():<12} | {r['elo']:>4} | {record:^7} | {win_rate:>5.1f}%"
        )

    table_text = "```\n" + "\n".join(lines) + "\n```"

    embed = discord.Embed(
        title="🏆 Leaderboard",
        description=table_text,
        color=discord.Color.gold()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)



def run_bot():
    if not TOKEN:
        print("Set DISCORD_TOKEN environment variable with your bot token.")
    else:
        bot.run(TOKEN)

if __name__ == "__main__":
    run_bot()
