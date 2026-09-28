import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from engine import get_nth_permutation, get_random_deal_index, get_rarity, simulate
from lucky import LuckyManager
from watcher import RecordsWatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("camicia.bot")

intents = discord.Intents.default()
# If configured in developer portal, enable members intent
try:
    intents.members = True
except Exception:
    pass

bot = commands.Bot(command_prefix="!", intents=intents)
watcher: Optional[RecordsWatcher] = None
lucky_mgr = LuckyManager(config.LUCKY_STATE_FILE)
db_pool = None


async def init_db_pool():
    """Initializes MariaDB connection pool if aiomysql is installed and password is set."""
    if not config.DB_PASSWD:
        logger.info("Database password not provided; running volunteer lookup in fallback mode.")
        return None

    try:
        import aiomysql

        pool = await aiomysql.create_pool(
            host=config.DB_HOST,
            port=config.DB_PORT,
            user=config.DB_USER,
            password=config.DB_PASSWD,
            db=config.DB_NAME,
            autocommit=True,
            minsize=1,
            maxsize=5,
            connect_timeout=5,
        )
        logger.info("Connected to MariaDB at %s:%s (%s)", config.DB_HOST, config.DB_PORT, config.DB_NAME)
        return pool
    except Exception as e:
        logger.warning("Could not connect to MariaDB (%s). Running with volunteer ID fallback.", e)
        return None


@tasks.loop(seconds=config.POLL_INTERVAL_SECONDS)
async def check_records_loop():
    """Background task polling for new Camicia records and loops."""
    if watcher is None:
        return

    try:
        embeds = await watcher.check_new_records()
        if not embeds:
            return

        if not config.DISCORD_RECORDS_CHANNEL_ID:
            logger.warning("Discovered %d new record(s), but DISCORD_RECORDS_CHANNEL_ID is not configured!", len(embeds))
            return

        channel = bot.get_channel(config.DISCORD_RECORDS_CHANNEL_ID)
        if channel is None:
            try:
                channel = await bot.fetch_channel(config.DISCORD_RECORDS_CHANNEL_ID)
            except Exception as e:
                logger.error("Could not find or fetch records channel %s: %s", config.DISCORD_RECORDS_CHANNEL_ID, e)
                return

        for embed in embeds:
            # Ping @everyone only when an all-time world record is broken
            content = None
            if "WORLD RECORD BROKEN" in (embed.title or ""):
                content = "@everyone"

            await channel.send(content=content, embed=embed)
            logger.info("Announced record in channel %s: %s", channel.name, embed.title)

    except Exception as e:
        logger.exception("Unexpected error in check_records_loop: %s", e)


@check_records_loop.before_loop
async def before_check_records_loop():
    await bot.wait_until_ready()
    logger.info("Records watcher background poller started (interval: %.1fs)", config.POLL_INTERVAL_SECONDS)


@bot.event
async def on_ready():
    logger.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)
    logger.info("Project directory: %s", config.CAMICIA_PROJECT_DIR.resolve())
    logger.info("Records channel ID: %s", config.DISCORD_RECORDS_CHANNEL_ID)
    logger.info("Bot commands channel ID: %s", config.DISCORD_BOT_COMMANDS_CHANNEL_ID)


def is_bot_commands_channel():
    """Restricts command execution to the #bot-commands channel (administrators can bypass)."""
    async def predicate(interaction: discord.Interaction) -> bool:
        # Administrators can test anywhere
        if interaction.user and hasattr(interaction.user, "guild_permissions") and interaction.user.guild_permissions.administrator:
            return True

        allowed_id = config.DISCORD_BOT_COMMANDS_CHANNEL_ID
        if allowed_id and interaction.channel_id == allowed_id:
            return True
        if interaction.channel and getattr(interaction.channel, "name", "") == "bot-commands":
            return True

        target_mention = f"<#{allowed_id}>" if allowed_id else "#bot-commands"
        raise app_commands.CheckFailure(
            f"❌ Bot commands can only be used in {target_mention} to keep discussions clean!"
        )
    return app_commands.check(predicate)


@bot.tree.command(name="ping", description="Check bot latency and project storage status (Admin only)")
@app_commands.default_permissions(administrator=True)
async def ping_cmd(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000, 1)

    proj_dir = config.CAMICIA_PROJECT_DIR.resolve()
    has_longest = (proj_dir / "records_longest.txt").exists()
    has_history = (proj_dir / "records_longest_history.txt").exists()
    has_loops = (proj_dir / "records_loops.txt").exists()

    status_lines = [
        f"🏓 **Pong!** Latency: `{latency_ms}ms`",
        f"📂 **Project Dir**: `{proj_dir}`",
        f"• `records_longest.txt`: {'✅' if has_longest else '❌'}",
        f"• `records_longest_history.txt`: {'✅' if has_history else '❌'}",
        f"• `records_loops.txt`: {'✅' if has_loops else '❌'}",
    ]

    await interaction.response.send_message("\n".join(status_lines), ephemeral=True)


@bot.tree.command(name="records", description="View the current standing longest game and total loops found")
@is_bot_commands_channel()
async def records_cmd(interaction: discord.Interaction):
    if watcher is None:
        await interaction.response.send_message("Records watcher is not yet initialized.", ephemeral=True)
        return

    longest_path = watcher.longest_file
    loops_path = watcher.loops_file

    best_cards = 0
    best_tricks = 0
    best_deal = "N/A"
    best_wu = "N/A"
    best_time = 0
    best_user = 0

    if longest_path.exists():
        try:
            with open(longest_path, "r", encoding="utf-8") as f:
                line = f.readline().strip()
                if line:
                    parts = line.split()
                    if len(parts) >= 5:
                        best_cards = int(parts[0])
                        best_tricks = int(parts[1])
                        best_deal = parts[2]
                        best_wu = parts[3]
                        best_time = int(parts[4])
                        best_user = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0
        except Exception as e:
            logger.error("Error reading %s: %s", longest_path, e)

    loops_count = watcher._count_lines(loops_path)
    user_str = await watcher.resolve_username(best_user)

    embed = discord.Embed(
        title="🏆 Camicia Standing Records",
        color=0xF1C40F,
        timestamp=datetime.now(timezone.utc),
    )

    if best_cards > 0:
        embed.add_field(name="🃏 Standing Longest Game", value=f"**{best_cards:,}** cards ({best_tricks:,} tricks)", inline=False)
        embed.add_field(name="👤 Held By", value=f"**{user_str}**", inline=True)
        time_tag = f"<t:{best_time}:R>" if best_time else "Unknown"
        embed.add_field(name="⏱️ Discovered", value=time_tag, inline=True)
    else:
        embed.add_field(name="🃏 Standing Longest Game", value="No record registered yet", inline=False)

    embed.add_field(name="♾️ Total Loops Found", value=f"**{loops_count:,}** infinite games", inline=False)
    embed.add_field(name="🌐 Official World Record", value=f"**{config.REAL_WORLD_RECORD_CARDS:,} cards** (Nessler 2022)", inline=False)

    embed.set_footer(
        text="Camicia BOINC Project",
        icon_url=config.PROJECT_ICON_URL,
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="lucky", description="Draw a random Beggar-My-Neighbour deal and test your luck! (3 rolls/day)")
@is_bot_commands_channel()
@app_commands.checks.cooldown(1, 10.0, key=lambda i: i.user.id)
async def lucky_cmd(interaction: discord.Interaction):
    can_roll, used_att, max_att = lucky_mgr.can_roll(interaction.user.id)
    if not can_roll:
        next_ts = lucky_mgr.next_midnight_timestamp()
        await interaction.response.send_message(
            f"⏳ **Daily Limit Reached!**\n"
            f"You have used all **{max_att} of your /lucky attempts** for today.\n"
            f"Your rolls will reset at **00:00 UTC** (<t:{next_ts}:R>).",
            ephemeral=True,
        )
        return

    # Defer response to allow simulation and role handling
    await interaction.response.defer()

    # Generate random deal index across the entire ~6.53e20 permutation space
    deal_index_int = get_random_deal_index()
    deal_index_str = str(deal_index_int)
    deck_str = get_nth_permutation(deal_index_int)

    # Simulate game turn-by-turn with cycle detection
    res = simulate(deck_str)
    status = res["status"]
    cards = res["cards"]
    tricks = res["tricks"]
    winner = res["winner"]
    is_loop = (status == "loop")

    # Record roll in daily tracker
    used_att, max_att, rank = lucky_mgr.record_roll(
        interaction.user.id,
        interaction.user.display_name,
        cards,
        tricks,
        deal_index_str,
        status,
    )

    # Check and award roles: Loop Discoverer or Record Holder
    standing_record = watcher.state.get("last_best_cards", 0) if watcher else 0
    record_threshold = max(standing_record, config.REAL_WORLD_RECORD_CARDS)
    member_obj = interaction.user if isinstance(interaction.user, discord.Member) else None
    awarded_roles = await lucky_mgr.check_and_award_roles(
        interaction.guild,
        member_obj,
        is_loop,
        cards,
        record_threshold,
    )

    tier_name, color, percentile_str = get_rarity(cards, is_loop)

    embed = discord.Embed(
        title="🎰 Do You Feel Lucky?",
        description=f"Rolled by {interaction.user.mention}",
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    # Deal layout (26 cards P1, 26 cards P2)
    layout_str = LuckyManager.format_deal_layout(deck_str)
    embed.add_field(name="🃏 Deal Layout", value=layout_str, inline=False)

    # Outcome
    if is_loop:
        outcome_str = f"**♾️ INFINITE LOOP!** (Game never ends after {cards:,} cards / {tricks:,} tricks!)"
    else:
        outcome_str = f"Finished in **{cards:,} cards** ({tricks:,} tricks) • **Player {winner} Won**"
    embed.add_field(name="⚔️ Outcome", value=outcome_str, inline=False)

    # Rarity
    embed.add_field(name="🌟 Rarity", value=f"**{tier_name}** • {percentile_str}", inline=False)

    # Daily Leaderboard placement
    if rank is not None and rank <= 3:
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        embed.add_field(
            name="🏆 Daily Leaderboard",
            value=f"{medals.get(rank, '🏅')} Placed **#{rank}** on today's server leaderboard!",
            inline=False,
        )

    # Role award notice
    if awarded_roles:
        embed.add_field(
            name="🎖️ Role Awarded!",
            value=f"🎉 You have been awarded the **{', '.join(awarded_roles)}** role!",
            inline=False,
        )

    # Deal index
    index_display = deal_index_str if len(deal_index_str) <= 30 else f"{deal_index_str[:27]}..."
    embed.add_field(name="🔢 Deal Index", value=f"`{index_display}`", inline=True)

    embed.set_footer(
        text=f"Attempt {used_att}/{max_att} today • Resets at 00:00 UTC",
        icon_url=config.PROJECT_ICON_URL,
    )

    await interaction.followup.send(embed=embed)

    # Broadcast to #records-and-loops if an Infinite Loop or World Record was discovered via /lucky
    if config.DISCORD_RECORDS_CHANNEL_ID and (is_loop or cards > record_threshold):
        records_ch = bot.get_channel(config.DISCORD_RECORDS_CHANNEL_ID)
        if records_ch is None:
            try:
                records_ch = await bot.fetch_channel(config.DISCORD_RECORDS_CHANNEL_ID)
            except Exception as e:
                logger.error("Could not fetch records channel for /lucky discovery: %s", e)

        if records_ch:
            now_ts = int(datetime.now(timezone.utc).timestamp())
            time_tag = f"<t:{now_ts}:F> (<t:{now_ts}:R>)"
            b_content = None

            if is_loop:
                b_embed = discord.Embed(
                    title="♾️ Infinite Loop Discovered via /lucky!",
                    description=(
                        f"A non-terminating Beggar-My-Neighbour deal has just been discovered "
                        f"by {interaction.user.mention} using `/lucky`!"
                    ),
                    color=0x9B59B6,
                    timestamp=datetime.now(timezone.utc),
                )
                b_embed.add_field(name="👤 Discovered By", value=interaction.user.mention, inline=True)
                b_embed.add_field(name="🔢 Deal Index", value=f"`{deal_index_str}`", inline=True)
                b_embed.add_field(name="⏱️ Verified At", value=time_tag, inline=False)
                b_embed.set_footer(
                    text="Camicia BOINC Project • /lucky Discovery",
                    icon_url=config.PROJECT_ICON_URL,
                )
            else:
                b_content = "@everyone"
                b_embed = discord.Embed(
                    title="🚨 ALL-TIME WORLD RECORD BROKEN VIA /LUCKY! 🏆",
                    description=(
                        f"A new finite Beggar-My-Neighbour deal exceeding the world record "
                        f"({record_threshold:,} cards) has just been rolled by {interaction.user.mention} using `/lucky`!"
                    ),
                    color=0xFF0033,
                    timestamp=datetime.now(timezone.utc),
                )
                b_embed.add_field(name="🃏 Cards Played", value=f"**{cards:,}** cards", inline=True)
                b_embed.add_field(name="🔄 Tricks", value=f"**{tricks:,}** tricks", inline=True)
                b_embed.add_field(name="👤 Discoverer", value=interaction.user.mention, inline=True)
                b_embed.add_field(name="🔢 Deal Index", value=f"`{deal_index_str}`", inline=True)
                b_embed.add_field(name="⏱️ Discovered", value=time_tag, inline=False)
                b_embed.set_footer(
                    text="Camicia BOINC Project • /lucky Record",
                    icon_url=config.PROJECT_ICON_URL,
                )
                if watcher:
                    watcher.state["last_best_cards"] = cards
                    watcher._save_state()

            try:
                await records_ch.send(content=b_content, embed=b_embed)
                logger.info("Broadcasted /lucky discovery to %s", records_ch.name)
            except Exception as e:
                logger.error("Failed to broadcast /lucky discovery to records channel: %s", e)


@bot.tree.command(name="luckyleaderboard", description="View today's top /lucky rolls on the server")
@is_bot_commands_channel()
async def luckyleaderboard_cmd(interaction: discord.Interaction):
    lb = lucky_mgr.get_leaderboard()
    next_ts = lucky_mgr.next_midnight_timestamp()

    embed = discord.Embed(
        title="🏆 Today's Luckiest Rolls (UTC)",
        description=f"Leaderboard resets at 00:00 UTC (<t:{next_ts}:R>)\n",
        color=0xF1C40F,
        timestamp=datetime.now(timezone.utc),
    )

    if not lb:
        embed.description += "\n*No rolls recorded yet today! Be the first to run `/lucky`.*"
    else:
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, entry in enumerate(lb, start=1):
            medal = medals.get(i, f"`#{i}`")
            status_tag = " ♾️" if entry.get("status") == "loop" else ""
            lines.append(
                f"{medal} **{entry['username']}**: **{entry['cards']:,} cards** ({entry['tricks']:,} tricks){status_tag}"
            )
        embed.add_field(name="Top Rolls Today", value="\n".join(lines), inline=False)

    embed.set_footer(
        text="Camicia BOINC Project • /lucky",
        icon_url=config.PROJECT_ICON_URL,
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Handles cooldowns and channel permission checks gracefully with clear ephemeral messages."""
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"⏳ **Slow down!** You can roll again in **{error.retry_after:.1f}s**.",
            ephemeral=True,
        )
    elif isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message(str(error), ephemeral=True)
    else:
        logger.error("Unhandled command error: %s", error, exc_info=True)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "An unexpected error occurred while processing the command.",
                ephemeral=True,
            )


async def main():
    global watcher, db_pool

    if not config.DISCORD_BOT_TOKEN:
        logger.error(
            "DISCORD_BOT_TOKEN is not set! Set it in .env or provide it as an environment variable."
        )
        sys.exit(1)

    db_pool = await init_db_pool()
    watcher = RecordsWatcher(
        project_dir=config.CAMICIA_PROJECT_DIR,
        state_file=config.WATCHER_STATE_FILE,
        db_pool=db_pool,
    )

    async with bot:
        # Register commands sync
        if config.DISCORD_GUILD_ID:
            guild_obj = discord.Object(id=config.DISCORD_GUILD_ID)
            bot.tree.copy_global_to(guild=guild_obj)
            logger.info("Will sync slash commands directly to Guild ID %d", config.DISCORD_GUILD_ID)

        check_records_loop.start()

        # Custom setup hook for syncing slash commands on connection
        async def on_tree_sync():
            await bot.wait_until_ready()
            try:
                if config.DISCORD_GUILD_ID:
                    guild_obj = discord.Object(id=config.DISCORD_GUILD_ID)
                    synced = await bot.tree.sync(guild=guild_obj)
                    logger.info("Synced %d guild slash command(s)", len(synced))
                else:
                    synced = await bot.tree.sync()
                    logger.info("Synced %d global slash command(s)", len(synced))
            except Exception as e:
                logger.error("Failed to sync slash commands: %s", e)

        asyncio.create_task(on_tree_sync())

        try:
            await bot.start(config.DISCORD_BOT_TOKEN)
        finally:
            if db_pool:
                db_pool.close()
                await db_pool.wait_closed()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
