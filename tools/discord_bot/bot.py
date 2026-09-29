import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

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


async def get_volunteer_role(guild: discord.Guild) -> Optional[discord.Role]:
    """Finds the Volunteer role by ID or by name 'Volunteer'."""
    if config.DISCORD_VOLUNTEER_ROLE_ID:
        role = guild.get_role(config.DISCORD_VOLUNTEER_ROLE_ID)
        if role:
            return role
    return discord.utils.get(guild.roles, name="Volunteer")


async def get_guild_member(user_id: int) -> Optional[discord.Member]:
    """Gets the Member object for a user in the configured guild."""
    if not config.DISCORD_GUILD_ID:
        return None
    guild = bot.get_guild(config.DISCORD_GUILD_ID)
    if guild is None:
        try:
            guild = await bot.fetch_guild(config.DISCORD_GUILD_ID)
        except Exception as e:
            logger.warning("Could not fetch guild %s: %s", config.DISCORD_GUILD_ID, e)
            return None
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except Exception:
            return None
    return member


async def is_discord_user_linked(discord_id: int) -> Tuple[bool, Optional[int], Optional[str]]:
    """Checks if a Discord user is linked to a BOINC account. Returns (is_linked, boinc_uid, volunteer_name)."""
    if db_pool is None:
        return False, None, None
    try:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT l.boinc_user_id, u.name "
                    "FROM camicia_discord_links l "
                    "LEFT JOIN user u ON u.id = l.boinc_user_id "
                    "WHERE l.discord_id = %s AND l.linked_at IS NOT NULL",
                    (discord_id,),
                )
                row = await cur.fetchone()
                if row:
                    return True, row[0], row[1]
    except Exception as e:
        logger.debug("Error checking discord user link for %s: %s", discord_id, e)
    return False, None, None


async def link_discord_user(
    discord_id: int, discord_username: str, pin: str
) -> Tuple[bool, str, Optional[int], Optional[str]]:
    """
    Validates a 6-digit PIN and links the Discord account to the BOINC account.
    Returns: (success, message, boinc_user_id, volunteer_name)
    """
    if db_pool is None:
        return False, "Database connection is currently unavailable. Please try again in a few moments.", None, None

    clean_pin = pin.strip()
    if not clean_pin.isdigit() or len(clean_pin) != 6:
        return False, "Invalid verification code format. The code must be a 6-digit number (e.g. `/link 123456`).", None, None

    try:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 1. Check if this Discord user is already linked to another BOINC account
                await cur.execute(
                    "SELECT boinc_user_id FROM camicia_discord_links WHERE discord_id = %s AND linked_at IS NOT NULL",
                    (discord_id,),
                )
                row = await cur.fetchone()
                if row:
                    return (
                        False,
                        f"Your Discord account is already linked to BOINC volunteer #{row[0]}. Run `/unlink` first if you wish to switch accounts.",
                        None,
                        None,
                    )

                # 2. Look up the PIN in camicia_discord_links
                await cur.execute(
                    "SELECT l.boinc_user_id, l.pin_expires_at, l.linked_at, u.name "
                    "FROM camicia_discord_links l "
                    "LEFT JOIN user u ON u.id = l.boinc_user_id "
                    "WHERE l.pin = %s",
                    (clean_pin,),
                )
                rows = await cur.fetchall()
                if not rows:
                    return False, "❌ Invalid verification code. Please check that you entered the code correctly.", None, None

                if len(rows) > 1:
                    logger.warning("Collision detected for PIN %s across %d rows: %s", clean_pin, len(rows), [r[0] for r in rows])
                    return (
                        False,
                        "❌ Code collision detected. For security, please request a fresh code on the website and try again.",
                        None,
                        None,
                    )

                boinc_user_id, pin_expires_at, linked_at, volunteer_name = rows[0]
                volunteer_name = volunteer_name or f"Volunteer #{boinc_user_id}"

                if linked_at is not None:
                    return False, "❌ This verification code has already been used.", None, None

                now_utc = datetime.now(timezone.utc)
                if pin_expires_at and pin_expires_at.replace(tzinfo=timezone.utc) < now_utc:
                    return False, "❌ This verification code has expired (valid for 15 minutes). Please request a new code on the website.", None, None

                # 3. Complete the link
                await cur.execute(
                    "UPDATE camicia_discord_links "
                    "SET discord_id = %s, discord_username = %s, linked_at = NOW(), pin = NULL, pin_expires_at = NULL "
                    "WHERE boinc_user_id = %s",
                    (discord_id, discord_username, boinc_user_id),
                )
                return True, "Success", boinc_user_id, volunteer_name

    except Exception as e:
        logger.exception("Error linking discord user %s: %s", discord_id, e)
        return False, "An unexpected error occurred while linking accounts. Please try again later.", None, None


async def unlink_discord_user(discord_id: int) -> Tuple[bool, str, Optional[int], Optional[str]]:
    """
    Unlinks a Discord account from its BOINC account.
    Returns: (success, message, boinc_user_id, volunteer_name)
    """
    if db_pool is None:
        return False, "Database connection is currently unavailable. Please try again in a few moments.", None, None

    try:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT l.boinc_user_id, u.name "
                    "FROM camicia_discord_links l "
                    "LEFT JOIN user u ON u.id = l.boinc_user_id "
                    "WHERE l.discord_id = %s AND l.linked_at IS NOT NULL",
                    (discord_id,),
                )
                row = await cur.fetchone()
                if not row:
                    return False, "❌ Your Discord account is not currently linked to any Camicia BOINC account.", None, None

                boinc_user_id, volunteer_name = row
                volunteer_name = volunteer_name or f"Volunteer #{boinc_user_id}"

                await cur.execute(
                    "DELETE FROM camicia_discord_links WHERE discord_id = %s",
                    (discord_id,),
                )
                return True, "Success", boinc_user_id, volunteer_name
    except Exception as e:
        logger.exception("Error unlinking discord user %s: %s", discord_id, e)
        return False, "An unexpected error occurred while unlinking accounts. Please try again later.", None, None


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


def is_bot_commands_or_dm():
    """Allows command execution in the #bot-commands channel OR in Direct Messages (DMs)."""
    async def predicate(interaction: discord.Interaction) -> bool:
        # Direct Messages (DMs) are always allowed
        if interaction.guild is None:
            return True

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
            f"❌ This command can only be used in {target_mention} or in a Direct Message (DM) to CamiciaBot!"
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


@bot.tree.command(name="lucky", description="Draw a random Beggar-My-Neighbour deal and test your luck! (3-5 rolls/day)")
@is_bot_commands_channel()
@app_commands.checks.cooldown(1, 10.0, key=lambda i: i.user.id)
async def lucky_cmd(interaction: discord.Interaction):
    # Check if user is linked to BOINC account
    is_linked, boinc_uid, vol_name = await is_discord_user_linked(interaction.user.id)

    can_roll, used_att, max_att = lucky_mgr.can_roll(interaction.user.id, is_linked=is_linked)
    if not can_roll:
        next_ts = lucky_mgr.next_midnight_timestamp()
        if not is_linked:
            bonus_hint = "\n\n💡 *Tip: Link your BOINC account with `/link` to unlock 5 daily rolls!*"
        else:
            bonus_hint = ""
        await interaction.response.send_message(
            f"⏳ **Daily Limit Reached!**\n"
            f"You have used all **{max_att} of your /lucky attempts** for today.\n"
            f"Your rolls will reset at **00:00 UTC** (<t:{next_ts}:R>).{bonus_hint}",
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
        is_linked=is_linked,
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

    footer_text = f"Attempt {used_att}/{max_att} today • Resets at 00:00 UTC"
    if is_linked:
        footer_text += " • Volunteer Bonus Active"
    else:
        footer_text += " • Link BOINC account for 5 rolls/day"

    embed.set_footer(
        text=footer_text,
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


@bot.tree.command(name="link", description="Link your Discord account to your Camicia BOINC volunteer account")
@app_commands.describe(code="The 6-digit verification code sent to your registered BOINC email")
@is_bot_commands_or_dm()
async def link_cmd(interaction: discord.Interaction, code: Optional[str] = None):
    """Links Discord account to BOINC profile using email verification code, or shows instructions."""
    if code is None or not code.strip():
        embed = discord.Embed(
            title="🔗 Link Your Camicia BOINC Account",
            description=(
                "Connect your account to earn the **Volunteer** role on Discord "
                "and unlock **5 daily rolls** on `/lucky` (instead of 3)!\n\n"
                "**How to link:**\n"
                "1️⃣ Log in to your account at **https://camicia.dev** (or your staging URL)\n"
                "2️⃣ Go to your **Account** page (`home.php`), look under **Community**, and click **Link Discord account**.\n"
                "3️⃣ Click **Send Verification Code via Email** to receive your 6-digit PIN.\n"
                "4️⃣ Return here (in `#bot-commands` or in a Direct Message to me) and run:\n"
                "```\n/link <your-6-digit-code>\n```\n"
                "*(When typing `/link`, select the `code` parameter and enter your digits, e.g. `/link 123456`)*"
            ),
            color=0x1B4332,
        )
        embed.set_footer(
            text="Camicia BOINC Project • Verification codes expire in 15 minutes",
            icon_url=config.PROJECT_ICON_URL,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    discord_username = str(interaction.user)
    success, msg, boinc_uid, volunteer_name = await link_discord_user(
        interaction.user.id, discord_username, code
    )

    if not success:
        await interaction.followup.send(msg, ephemeral=True)
        return

    # Assign Volunteer role on guild
    role_status = "Volunteer role granted 🏅"
    member = await get_guild_member(interaction.user.id)
    if member and member.guild:
        volunteer_role = await get_volunteer_role(member.guild)
        if volunteer_role:
            try:
                await member.add_roles(volunteer_role, reason="Linked Camicia BOINC account")
                role_status = f"Assigned **@{volunteer_role.name}** role 🏅"
            except Exception as e:
                logger.warning("Could not add Volunteer role to %s: %s", member, e)
                role_status = "⚠️ Linked, but could not assign role (bot lacks Manage Roles permission)"
        else:
            role_status = "⚠️ Linked, but 'Volunteer' role was not found on server"

    embed = discord.Embed(
        title="🎉 Account Linked Successfully!",
        description=(
            f"Welcome, **{volunteer_name}**! Your Discord account is now linked to your Camicia BOINC profile (ID: `{boinc_uid}`)."
        ),
        color=0x2ECC71,  # Emerald Green
    )
    embed.add_field(name="🏅 Server Role", value=role_status, inline=False)
    embed.add_field(
        name="🎲 Lucky Mini-Game",
        value="**5 attempts/day** unlocked (+2 bonus rolls every day!)",
        inline=False,
    )
    embed.set_footer(
        text="Camicia BOINC Project • Thank you for your contribution!",
        icon_url=config.PROJECT_ICON_URL,
    )
    await interaction.followup.send(embed=embed, ephemeral=True)
    logger.info("User %s linked to BOINC account #%s (%s)", interaction.user, boinc_uid, volunteer_name)


@bot.tree.command(name="unlink", description="Unlink your Discord account from your Camicia BOINC account")
@is_bot_commands_or_dm()
async def unlink_cmd(interaction: discord.Interaction):
    """Unlinks Discord account from BOINC profile."""
    await interaction.response.defer(ephemeral=True)
    success, msg, boinc_uid, volunteer_name = await unlink_discord_user(interaction.user.id)

    if not success:
        await interaction.followup.send(msg, ephemeral=True)
        return

    # Remove Volunteer role if present
    member = await get_guild_member(interaction.user.id)
    if member and member.guild:
        volunteer_role = await get_volunteer_role(member.guild)
        if volunteer_role and volunteer_role in member.roles:
            try:
                await member.remove_roles(volunteer_role, reason="Unlinked Camicia BOINC account")
            except Exception as e:
                logger.warning("Could not remove Volunteer role from %s: %s", member, e)

    embed = discord.Embed(
        title="✅ Account Unlinked",
        description=f"Your Discord account has been disconnected from BOINC volunteer **{volunteer_name}**.",
        color=0x95A5A6,
    )
    embed.add_field(name="Status", value="Volunteer role removed • /lucky rolls reset to 3/day", inline=False)
    embed.set_footer(
        text="You can re-link anytime from your account page at https://camicia.dev",
        icon_url=config.PROJECT_ICON_URL,
    )
    await interaction.followup.send(embed=embed, ephemeral=True)
    logger.info("User %s unlinked from BOINC account #%s (%s)", interaction.user, boinc_uid, volunteer_name)


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
