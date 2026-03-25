"""Discord bot entrypoint wired to modular helpers."""
import asyncio
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

# from .config import bot
from .lobby import Lobby
from .stats_store import PlayerStatsStore
from .views import JoinView
from logging_config import setup_logging

log = setup_logging("boost_bot")

PRIVILEGED_USER_ID = 368755002824589322
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guild_messages = True

class BoostBot(commands.Bot):
    _app_commands_synced: bool = False

    async def sync_app_commands(self, mode_override: str | None = None) -> None:
        """
        Register slash commands everywhere.

        Discord global commands can take time to appear across guilds.
        This function optionally also syncs guild-scoped commands for fast refresh.
        """
        # `global` = sync global only (slower propagation)
        # `guild`  = sync each guild the bot is in (fast refresh)
        # `both`   = sync both (most reliable immediate visibility)
        mode = (mode_override or os.getenv("DISCORD_COMMAND_SYNC_MODE", "both")).strip().lower()

        try:
            if mode in ("global", "both"):
                synced_global = await self.tree.sync()
                log.info("Synced %d command(s) globally", len(synced_global))

            if mode in ("guild", "both"):
                # `@bot.tree.command` registers *global* commands. Guild sync only
                # uploads guild-scoped commands; without copy_global_to the guild tree
                # is empty → API returns 0 and slash commands won't show per-guild.
                guilds = list(self.guilds)
                log.info("Copying global commands + syncing to %d guild(s)...", len(guilds))
                sleep_secs = float(os.getenv("DISCORD_COMMAND_SYNC_GUILD_SLEEP_SECS", "0.25"))
                for g in guilds:
                    try:
                        guild_obj = discord.Object(id=g.id)
                        self.tree.copy_global_to(guild=guild_obj)
                        synced_guild = await self.tree.sync(guild=guild_obj)
                        log.info(
                            "Synced %d command(s) in guild %s",
                            len(synced_guild),
                            g.id,
                        )
                        if sleep_secs > 0:
                            await asyncio.sleep(sleep_secs)
                    except discord.HTTPException as e:
                        log.warning("Guild sync failed for %s: %s", g.id, e)
        except Exception as e:
            log.exception("Failed to sync app commands: %s", e)

    async def setup_hook(self):
        # Syncing here can run before `bot.guilds` is populated.
        # We'll sync in `on_ready` instead so guild-scoped syncing works reliably.
        log.info("setup_hook complete; will sync app commands on_ready.")


bot = BoostBot(command_prefix="!", intents=intents, sync_commands=False)

# Per-guild lobby state: guild_id -> Lobby
guild_lobbies: dict[int, Lobby] = {}
# Per-guild queue message for reuse
guild_queue_messages: dict[int, discord.Message] = {}


@bot.event
async def on_ready():
    log.info("Logged in as %s (id: %s)", bot.user, bot.user.id)
    if getattr(bot, "_app_commands_synced", False):
        return

    bot._app_commands_synced = True
    await bot.sync_app_commands()





@bot.tree.command(name="startqueue", description="Create a game queue")
@discord.app_commands.describe(title="Optional title to display at the top of the queue")
async def startqueue(interaction: discord.Interaction, title: str | None = None):
    if interaction.guild is None:
        return await interaction.response.send_message("Use this in a server.", ephemeral=True)
    is_admin = interaction.user.guild_permissions.administrator
    is_privileged = interaction.user.id == PRIVILEGED_USER_ID
    if not (is_admin or is_privileged):
        return await interaction.response.send_message(
            "Only server admins can start a queue.",
            ephemeral=True
        )

    gid = interaction.guild.id

    # Always create a fresh lobby
    lobby = Lobby(host_id=interaction.user.id, title=title or "Queue")
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


@bot.tree.command(name="kickfromqueue", description="Remove a mentioned user from the current queue")
@discord.app_commands.describe(user="User to remove from the queue")
async def kickfromqueue(interaction: discord.Interaction, user: discord.Member):
    if interaction.guild is None:
        return await interaction.response.send_message("Use this in a server.", ephemeral=True)

    gid = interaction.guild.id
    lobby = guild_lobbies.get(gid)
    if not lobby or lobby.started:
        return await interaction.response.send_message("No open queue. Start one first.", ephemeral=True)

    is_admin = interaction.user.guild_permissions.administrator if interaction.guild else False
    is_privileged = interaction.user.id == PRIVILEGED_USER_ID
    if interaction.user.id != lobby.host_id and not (is_admin or is_privileged):
        return await interaction.response.send_message("Only the host or a server admin can kick players.", ephemeral=True)

    removed = lobby.remove(user.id)
    if not removed:
        return await interaction.response.send_message("Could not remove user (not in queue or queue started).", ephemeral=True)

    view = JoinView(gid, lobby)
    msg = guild_queue_messages.get(gid)
    if msg:
        await msg.edit(embed=None, view=view)
        await view.update_queue_message(interaction,
            note=f"{user.display_name} was kicked by {interaction.user.display_name}.",
            target_message=msg
        )
        await interaction.response.send_message(f"{user.display_name} removed from the queue.", ephemeral=True)
        return

    await interaction.response.send_message("Could not update queue message. Please try again.", ephemeral=True)


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
    is_privileged = interaction.user.id == PRIVILEGED_USER_ID
    if interaction.user.id != lobby.host_id and not (is_admin or is_privileged):
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


@bot.command(name="synccommands")
async def synccommands(ctx: commands.Context, mode: str | None = None):
    """
    Force a slash-command sync now (admin-only).

    Usage: `!synccommands` or `!synccommands guild|global|both`
    """
    if ctx.guild is None:
        return await ctx.send("Use this in a server.")

    is_admin = getattr(ctx.author, "guild_permissions", None) and ctx.author.guild_permissions.administrator
    is_privileged = ctx.author.id == PRIVILEGED_USER_ID
    if not (is_admin or is_privileged):
        return await ctx.send("Only server admins can sync commands.")

    await ctx.send("Syncing slash commands...")
    await bot.sync_app_commands(mode_override=mode)
    await ctx.send("Done. Check the command list in your server.")



def run_bot():
    if not TOKEN:
        log.error("Set DISCORD_TOKEN environment variable with your bot token.")
    else:
        bot.run(TOKEN)

if __name__ == "__main__":
    run_bot()


