import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, Union

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from engine import get_nth_permutation, get_random_deal_index, get_rarity, simulate
from lucky import LuckyManager
from watcher import RecordsWatcher
import duel
from duel import (
    CUT_POSITIONS,
    DeckCutAndCheerView,
    DuelChallengeView,
    DuelManager,
    RematchView,
    calculate_elo,
    format_hand_summary,
)

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

# Rate limiting & Anti-abuse configurations
LINK_LOCKOUT_MAX_ATTEMPTS = 5
LINK_LOCKOUT_DURATION_SECONDS = 900  # 15 minutes in seconds
UNLINK_COOLDOWN_SECONDS = 3600  # 1 hour cooldown after unlinking
LINK_SPAM_COOLDOWN_SECONDS = 3.0  # 3s per-user cooldown on /link
UNLINK_SPAM_COOLDOWN_SECONDS = 5.0  # 5s per-user cooldown on /unlink
SPAM_STRIKE_MAX_ATTEMPTS = 5  # 5 strikes trigger temporary ignore
SPAM_STRIKE_WINDOW_SECONDS = 300.0  # 5 minutes sliding window for strikes
SPAM_MUTE_DURATION_SECONDS = 900  # 15 minutes temporary ignore

_failed_link_attempts: dict[int, list[float]] = {}
_dm_cooldowns: dict[Tuple[int, str], float] = {}
_user_spam_strikes: dict[int, list[float]] = {}
_user_mute_until: dict[int, float] = {}


def check_user_muted(discord_id: int) -> Tuple[bool, int]:
    """
    Checks if a user is currently muted/ignored for spamming.
    Returns (is_muted, remaining_seconds).
    If the mute duration has expired, clears mute and strikes and returns (False, 0).
    """
    now = time.monotonic()
    mute_expiry = _user_mute_until.get(discord_id, 0.0)
    if now < mute_expiry:
        return True, max(1, int(round(mute_expiry - now)))
    if discord_id in _user_mute_until:
        _user_mute_until.pop(discord_id, None)
        _user_spam_strikes.pop(discord_id, None)
    return False, 0


def record_spam_strike(discord_id: int) -> Tuple[int, bool, int]:
    """
    Records an abusive spam attempt (e.g. repeated unlinking when unlinked,
    hammering while on cooldown, or spamming /link on active unlink cooldown).
    Returns (current_strike_count, is_now_muted, remaining_mute_seconds).
    """
    now = time.monotonic()
    is_muted, rem = check_user_muted(discord_id)
    if is_muted:
        return SPAM_STRIKE_MAX_ATTEMPTS, True, rem

    strikes = [t for t in _user_spam_strikes.get(discord_id, []) if now - t < SPAM_STRIKE_WINDOW_SECONDS]
    strikes.append(now)
    _user_spam_strikes[discord_id] = strikes

    if len(strikes) >= SPAM_STRIKE_MAX_ATTEMPTS:
        _user_mute_until[discord_id] = now + SPAM_MUTE_DURATION_SECONDS
        _user_spam_strikes.pop(discord_id, None)
        return SPAM_STRIKE_MAX_ATTEMPTS, True, SPAM_MUTE_DURATION_SECONDS

    return len(strikes), False, 0


def clear_spam_strikes(discord_id: int):
    """Resets strikes and mute for a user (e.g. on successful link or unlink)."""
    _user_spam_strikes.pop(discord_id, None)
    _user_mute_until.pop(discord_id, None)


def check_dm_cooldown(discord_id: int, action: str, cooldown_seconds: float) -> Tuple[bool, float]:
    """
    Checks if a user is currently on cooldown for a given action in DMs.
    Returns (is_on_cooldown, remaining_seconds).
    If not on cooldown, sets the cooldown and returns (False, 0.0).
    """
    now = time.monotonic()
    if len(_dm_cooldowns) > 500:
        expired = [k for k, exp in _dm_cooldowns.items() if now >= exp]
        for k in expired:
            _dm_cooldowns.pop(k, None)

    key = (discord_id, action)
    expire_at = _dm_cooldowns.get(key, 0.0)
    if now < expire_at:
        return True, expire_at - now
    _dm_cooldowns[key] = now + cooldown_seconds
    return False, 0.0


def check_link_lockout(discord_id: int) -> Tuple[bool, int]:
    """
    Checks if a Discord user is locked out due to excessive failed PIN attempts.
    Returns: (is_locked_out, remaining_seconds)
    """
    now = datetime.now(timezone.utc).timestamp()
    attempts = _failed_link_attempts.get(discord_id, [])
    valid_attempts = [t for t in attempts if now - t < LINK_LOCKOUT_DURATION_SECONDS]
    _failed_link_attempts[discord_id] = valid_attempts

    if len(valid_attempts) >= LINK_LOCKOUT_MAX_ATTEMPTS:
        earliest = min(valid_attempts)
        remaining = int(LINK_LOCKOUT_DURATION_SECONDS - (now - earliest))
        if remaining > 0:
            return True, remaining
        _failed_link_attempts[discord_id] = []
        return False, 0
    return False, 0


def record_failed_link_attempt(discord_id: int) -> int:
    """
    Records a failed PIN attempt.
    Returns the number of remaining attempts before temporary lockout.
    """
    now = datetime.now(timezone.utc).timestamp()
    attempts = [t for t in _failed_link_attempts.get(discord_id, []) if now - t < LINK_LOCKOUT_DURATION_SECONDS]
    attempts.append(now)
    _failed_link_attempts[discord_id] = attempts
    return max(0, LINK_LOCKOUT_MAX_ATTEMPTS - len(attempts))


def clear_failed_link_attempts(discord_id: int):
    """Clears failed attempt history on successful link."""
    _failed_link_attempts.pop(discord_id, None)


async def get_unlink_cooldown_remaining(discord_id: int) -> int:
    """Returns remaining seconds on 1-hour unlink cooldown for a Discord user, or 0 if none."""
    pool = await get_db_pool()
    if pool is None:
        return 0
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT unlinked_at FROM camicia_discord_links WHERE discord_id = %s AND unlinked_at IS NOT NULL",
                    (discord_id,),
                )
                rows = await cur.fetchall()
                now_utc = datetime.now(timezone.utc)
                max_remaining = 0
                for row in rows:
                    if row and row[0]:
                        unlinked_at = row[0]
                        if unlinked_at.tzinfo is None:
                            unlinked_at = unlinked_at.replace(tzinfo=timezone.utc)
                        diff = (now_utc - unlinked_at).total_seconds()
                        if diff < UNLINK_COOLDOWN_SECONDS:
                            rem = int(UNLINK_COOLDOWN_SECONDS - diff)
                            if rem > max_remaining:
                                max_remaining = rem
                return max_remaining
    except Exception as e:
        logger.debug("Error checking unlink cooldown for Discord ID %s: %s", discord_id, e)
    return 0


def get_bot_avatar_url() -> Optional[str]:
    """Returns the bot's own Discord CDN avatar URL, eliminating external HTTP fetches."""
    if bot.user and bot.user.display_avatar:
        return bot.user.display_avatar.url
    return None


async def init_db_pool():
    """Initializes MariaDB connection pool with fallback hosts and clear logging."""
    if not config.DB_PASSWD:
        logger.warning(
            "MariaDB password not found in environment (MARIADB_PASSWORD/MARIADB_ROOT_PASSWORD) "
            "or config.xml; database features will be disabled."
        )
        return None

    try:
        import aiomysql
    except ImportError:
        logger.error("aiomysql library is not installed; database features cannot be used.")
        return None

    # Try configured host, then service name fallback 'database', then localhost
    hosts_to_try = [config.DB_HOST]
    for fallback in ("database", "127.0.0.1"):
        if fallback not in hosts_to_try:
            hosts_to_try.append(fallback)

    for host in hosts_to_try:
        try:
            pool = await aiomysql.create_pool(
                host=host,
                port=config.DB_PORT,
                user=config.DB_USER,
                password=config.DB_PASSWD,
                db=config.DB_NAME,
                autocommit=True,
                minsize=1,
                maxsize=5,
                connect_timeout=5,
            )
            logger.info("Connected to MariaDB at %s:%s (user=%s, db=%s)", host, config.DB_PORT, config.DB_USER, config.DB_NAME)
            return pool
        except Exception as e:
            logger.warning("Could not connect to MariaDB at %s:%s (user=%s, db=%s): %s", host, config.DB_PORT, config.DB_USER, config.DB_NAME, e)

    return None


async def get_db_pool():
    """Returns active DB pool, attempting to reconnect if not connected or closed."""
    global db_pool, watcher
    if db_pool is not None:
        try:
            if not db_pool._closed:
                return db_pool
        except Exception:
            pass
    db_pool = await init_db_pool()
    if watcher is not None and db_pool is not None:
        watcher.db_pool = db_pool
    return db_pool


def get_standing_record_cards() -> int:
    if watcher is not None:
        return watcher._get_current_longest_cards()
    return config.REAL_WORLD_RECORD_CARDS


duel_mgr = duel.DuelManager(get_db_pool, standing_record_getter=get_standing_record_cards)
_duel_cooldowns: dict[int, float] = {}

CUT_LABELS = {
    6: "Card 6 (Top 1/4)",
    13: "Card 13 (Middle)",
    20: "Card 20 (Bottom 1/4)",
    0: "Kept As-Is",
}


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


def is_tester_or_admin(member: Optional[discord.Member]) -> bool:
    """Checks if a guild member has Administrator permissions or the Tester role."""
    if not member:
        return False
    perms = getattr(member, "guild_permissions", None)
    if perms and getattr(perms, "administrator", False):
        return True

    roles = getattr(member, "roles", [])
    if config.DISCORD_TESTER_ROLE_ID:
        for r in roles:
            if getattr(r, "id", None) == config.DISCORD_TESTER_ROLE_ID:
                return True

    for r in roles:
        if getattr(r, "name", "").lower() == "tester":
            return True

    return False


async def is_discord_user_linked(discord_id: int) -> Tuple[bool, Optional[int], Optional[str]]:
    """Checks if a Discord user is linked to a BOINC account. Returns (is_linked, boinc_uid, volunteer_name)."""
    pool = await get_db_pool()
    if pool is None:
        return False, None, None
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT l.boinc_user_id, u.name "
                    "FROM camicia_discord_links l "
                    "LEFT JOIN user u ON u.id = l.boinc_user_id "
                    "WHERE l.discord_id = %s AND l.linked_at IS NOT NULL AND l.unlinked_at IS NULL",
                    (discord_id,),
                )
                row = await cur.fetchone()
                if row:
                    return True, row[0], row[1]
    except Exception as e:
        logger.debug("Error checking discord user link for %s: %s", discord_id, e)
    return False, None, None


async def check_is_volunteer(user: Union[discord.User, discord.Member], guild: Optional[discord.Guild] = None) -> bool:
    """Checks if a user has the Volunteer role or a linked BOINC account."""
    if isinstance(user, discord.Member):
        if config.DISCORD_VOLUNTEER_ROLE_ID and any(r.id == config.DISCORD_VOLUNTEER_ROLE_ID for r in user.roles):
            return True
        if any(r.name == "Volunteer" for r in user.roles):
            return True
    elif guild is not None:
        member = guild.get_member(user.id)
        if member:
            if config.DISCORD_VOLUNTEER_ROLE_ID and any(r.id == config.DISCORD_VOLUNTEER_ROLE_ID for r in member.roles):
                return True
            if any(r.name == "Volunteer" for r in member.roles):
                return True

    is_linked, _, _ = await is_discord_user_linked(user.id)
    return is_linked


class LinkHelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Open Verification Page",
                url=getattr(config, "PROJECT_LINK_URL", f"https://{config.PROJECT_DOMAIN}/camicia/discord_link.php"),
                emoji="🔗",
                style=discord.ButtonStyle.link,
            )
        )


def get_link_instructions_embed() -> discord.Embed:
    base_url = getattr(config, "PROJECT_BASE_URL", f"https://{config.PROJECT_DOMAIN}/camicia")
    link_url = getattr(config, "PROJECT_LINK_URL", f"{base_url}/discord_link.php")
    embed = discord.Embed(
        title="🔗 Link Your Camicia BOINC Account",
        description=(
            "Connect your account to earn the **Volunteer** role on Discord "
            "and unlock **5 daily rolls** on `/lucky` (instead of 3)!\n\n"
            "**How to link:**\n"
            f"1️⃣ Log in to your account at [**{config.PROJECT_DOMAIN}**]({base_url})\n"
            "2️⃣ Go to your **Account** page (`home.php`), look under **Community**, and click **Link Discord account** (or tap the button below)\n"
            "3️⃣ Click **Send Verification Code via Email** to receive your 6-digit code.\n"
            "4️⃣ In this DM with CamiciaBot, run:\n"
            "```\n/link <code>\n```\n"
            "*(Replace `<code>` with your 6-digit code, e.g. `/link 123456`)*"
        ),
        color=0x1B4332,
    )
    embed.set_footer(
        text="Camicia BOINC Project • Verification codes expire in 15 minutes",
        icon_url=get_bot_avatar_url(),
    )
    return embed


def get_already_linked_embed(boinc_uid: int, volunteer_name: Optional[str]) -> discord.Embed:
    name = volunteer_name or f"Volunteer #{boinc_uid}"
    embed = discord.Embed(
        title="🏅 Account Already Linked",
        description=(
            f"Your Discord account is already connected to Camicia volunteer **{name}** (BOINC ID: `{boinc_uid}`).\n\n"
            "• **Server Role**: You have the **Volunteer** role on our Discord server.\n"
            "• **Mini-Game**: You have **5 daily rolls** unlocked on `/lucky`.\n\n"
            "To disconnect or switch accounts, run `/unlink` in this DM."
        ),
        color=0x2ECC71,
    )
    embed.set_footer(
        text="Camicia BOINC Project • Account Active",
        icon_url=get_bot_avatar_url(),
    )
    return embed


def get_unlink_cooldown_embed(remaining_seconds: int, account_type: str = "Discord", strike_count: int = 0) -> discord.Embed:
    link_url = getattr(config, "PROJECT_LINK_URL", f"https://{config.PROJECT_DOMAIN}/camicia/discord_link.php")
    rem_min = max(1, (remaining_seconds + 59) // 60)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    unfreeze_ts = now_ts + remaining_seconds

    if account_type == "BOINC":
        subject_desc = "This Camicia BOINC account was unlinked from Discord recently."
    else:
        subject_desc = "Your Discord account was unlinked from a Camicia BOINC profile recently."

    embed = discord.Embed(
        title="⏳ Account Unlink Cooldown Active",
        description=(
            f"{subject_desc}\n\n"
            "To prevent spam and rapid account cycling, a **1-hour cooldown** applies "
            "before this account can be linked again."
        ),
        color=0xE67E22,  # Amber / Warning Orange
    )
    embed.add_field(
        name="⏱️ Cooldown Remaining",
        value=f"**{rem_min} minute{'s' if rem_min != 1 else ''}** ({remaining_seconds}s) • Unlocks <t:{unfreeze_ts}:R> (<t:{unfreeze_ts}:T>)",
        inline=False,
    )
    if strike_count > 0:
        embed.add_field(
            name="⚠️ Anti-Spam Notice",
            value=(
                f"Please wait for the cooldown to expire. Continued repeated requests will cause the bot "
                f"to ignore your messages for 15 minutes. (**Strike {strike_count}/5**)"
            ),
            inline=False,
        )
    embed.add_field(
        name="🔗 Next Steps",
        value=(
            f"Once the cooldown expires, visit the [**Discord Verification Page**]({link_url}) "
            "to request a new 6-digit verification code."
        ),
        inline=False,
    )
    embed.set_footer(
        text=f"Camicia BOINC Project • Anti-Abuse Cooldown",
        icon_url=get_bot_avatar_url(),
    )
    return embed


def get_spam_muted_embed(remaining_seconds: int) -> discord.Embed:
    rem_min = max(1, (remaining_seconds + 59) // 60)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    unlock_ts = now_ts + remaining_seconds

    embed = discord.Embed(
        title="🔇 Commands Temporarily Ignored",
        description=(
            "You have sent too many repeated or spammed requests in a short period.\n\n"
            "To prevent service disruption, CamiciaBot will temporarily ignore commands "
            "and messages from your Discord account."
        ),
        color=0xE74C3C,  # Crimson Red
    )
    embed.add_field(
        name="⏱️ Ignored Remaining",
        value=f"**{rem_min} minute{'s' if rem_min != 1 else ''}** ({remaining_seconds}s) • Resumes <t:{unlock_ts}:R> (<t:{unlock_ts}:T>)",
        inline=False,
    )
    embed.add_field(
        name="💡 What should I do?",
        value="Please take a break. Access to the bot will automatically resume once the timer expires.",
        inline=False,
    )
    embed.set_footer(
        text="Camicia BOINC Project • Anti-Spam Protection",
        icon_url=get_bot_avatar_url(),
    )
    return embed


def get_lockout_embed(remaining_seconds: int) -> discord.Embed:
    link_url = getattr(config, "PROJECT_LINK_URL", f"https://{config.PROJECT_DOMAIN}/camicia/discord_link.php")
    rem_min = max(1, (remaining_seconds + 59) // 60)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    unlock_ts = now_ts + remaining_seconds

    embed = discord.Embed(
        title="⛔ Verification Temporarily Locked",
        description=(
            "Too many incorrect verification attempts have been submitted from this Discord account.\n\n"
            "For security reasons, your account has been temporarily locked out from submitting verification codes."
        ),
        color=0xE74C3C,  # Crimson Red
    )
    embed.add_field(
        name="⏱️ Lockout Remaining",
        value=f"**{rem_min} minute{'s' if rem_min != 1 else ''}** ({remaining_seconds}s) • Unlocks <t:{unlock_ts}:R> (<t:{unlock_ts}:T>)",
        inline=False,
    )
    embed.add_field(
        name="💡 What should I do?",
        value=(
            f"Please wait for the lockout to expire, or visit the [**Discord Verification Page**]({link_url}) "
            "to request a fresh verification code."
        ),
        inline=False,
    )
    embed.set_footer(
        text="Camicia BOINC Project • Security Lockout",
        icon_url=get_bot_avatar_url(),
    )
    return embed


async def link_discord_user(
    discord_id: int, discord_username: str, pin: str
) -> Tuple[bool, Union[str, discord.Embed], Optional[int], Optional[str]]:
    """
    Validates a 6-digit PIN and links the Discord account to the BOINC account.
    Enforces brute-force lockout (5 attempts -> 15 min) and 1-hour unlink cooldown.
    Returns: (success, message_or_embed, boinc_user_id, volunteer_name)
    """
    # 0. Check spam mute & brute-force lockout
    is_muted, remaining_mute = check_user_muted(discord_id)
    if is_muted:
        return (
            False,
            get_spam_muted_embed(remaining_mute),
            None,
            None,
        )

    is_locked, remaining_lockout = check_link_lockout(discord_id)
    if is_locked:
        return (
            False,
            get_lockout_embed(remaining_lockout),
            None,
            None,
        )

    clean_pin = pin.strip().replace(" ", "").replace("-", "").lstrip("#")
    if not clean_pin.isdigit() or len(clean_pin) != 6:
        rem_att = record_failed_link_attempt(discord_id)
        if rem_att > 0:
            return (
                False,
                f"❌ **Invalid verification code format.** The code must be a 6-digit number (e.g. `/link 123456`).\n"
                f"*(**{rem_att}** attempt{'s' if rem_att != 1 else ''} remaining before a 15-minute temporary lockout)*",
                None,
                None,
            )
        else:
            return (
                False,
                get_lockout_embed(LINK_LOCKOUT_DURATION_SECONDS),
                None,
                None,
            )

    pool = await get_db_pool()
    if pool is None:
        return False, "Database connection is currently unavailable. Please try again in a few moments.", None, None

    now_utc = datetime.now(timezone.utc)

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 1. Check if this Discord user is on 1-hour unlink cooldown
                await cur.execute(
                    "SELECT boinc_user_id, unlinked_at FROM camicia_discord_links WHERE discord_id = %s AND unlinked_at IS NOT NULL",
                    (discord_id,),
                )
                unlinked_rows = await cur.fetchall()
                for b_uid, u_at in unlinked_rows:
                    if u_at:
                        if u_at.tzinfo is None:
                            u_at = u_at.replace(tzinfo=timezone.utc)
                        diff_sec = (now_utc - u_at).total_seconds()
                        if diff_sec < UNLINK_COOLDOWN_SECONDS:
                            rem_sec = int(UNLINK_COOLDOWN_SECONDS - diff_sec)
                            strike_cnt, is_muted, rem_mute = record_spam_strike(discord_id)
                            if is_muted:
                                return (
                                    False,
                                    get_spam_muted_embed(rem_mute),
                                    None,
                                    None,
                                )
                            return (
                                False,
                                get_unlink_cooldown_embed(rem_sec, account_type="Discord", strike_count=strike_cnt),
                                None,
                                None,
                            )

                # 2. Check if this Discord user is already linked to another BOINC account
                await cur.execute(
                    "SELECT boinc_user_id FROM camicia_discord_links WHERE discord_id = %s AND linked_at IS NOT NULL AND unlinked_at IS NULL",
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

                # 3. Look up the PIN in camicia_discord_links
                await cur.execute(
                    "SELECT l.boinc_user_id, l.pin_expires_at, l.linked_at, l.unlinked_at, u.name "
                    "FROM camicia_discord_links l "
                    "LEFT JOIN user u ON u.id = l.boinc_user_id "
                    "WHERE l.pin = %s",
                    (clean_pin,),
                )
                rows = await cur.fetchall()
                if not rows:
                    rem_att = record_failed_link_attempt(discord_id)
                    if rem_att > 0:
                        return (
                            False,
                            f"❌ **Invalid verification code.** Please check that you entered the code correctly.\n"
                            f"*(**{rem_att}** attempt{'s' if rem_att != 1 else ''} remaining before a 15-minute temporary lockout)*",
                            None,
                            None,
                        )
                    else:
                        return (
                            False,
                            get_lockout_embed(LINK_LOCKOUT_DURATION_SECONDS),
                            None,
                            None,
                        )

                if len(rows) > 1:
                    logger.warning("Collision detected for PIN %s across %d rows: %s", clean_pin, len(rows), [r[0] for r in rows])
                    return (
                        False,
                        "❌ Code collision detected. For security, please request a fresh code on the website and try again.",
                        None,
                        None,
                    )

                boinc_user_id, pin_expires_at, linked_at, unlinked_at, volunteer_name = rows[0]
                volunteer_name = volunteer_name or f"Volunteer #{boinc_user_id}"

                # 4. Check if the BOINC account associated with the PIN is on 1-hour unlink cooldown
                if unlinked_at is not None:
                    if unlinked_at.tzinfo is None:
                        unlinked_at = unlinked_at.replace(tzinfo=timezone.utc)
                    diff_sec = (now_utc - unlinked_at).total_seconds()
                    if diff_sec < UNLINK_COOLDOWN_SECONDS:
                        rem_sec = int(UNLINK_COOLDOWN_SECONDS - diff_sec)
                        strike_cnt, is_muted, rem_mute = record_spam_strike(discord_id)
                        if is_muted:
                            return (
                                False,
                                get_spam_muted_embed(rem_mute),
                                None,
                                None,
                            )
                        return (
                            False,
                            get_unlink_cooldown_embed(rem_sec, account_type="BOINC", strike_count=strike_cnt),
                            None,
                            None,
                        )
                    return False, "❌ This verification code is no longer valid. Please request a new code on the website.", None, None

                if linked_at is not None:
                    return False, "❌ This verification code has already been used.", None, None

                if pin_expires_at:
                    if pin_expires_at.tzinfo is None:
                        pin_expires_at = pin_expires_at.replace(tzinfo=timezone.utc)
                    if pin_expires_at < now_utc:
                        return False, "❌ This verification code has expired (valid for 15 minutes). Please request a new code on the website.", None, None

                # 5. Clear any old unlinked row with this discord_id on a different boinc_user_id
                await cur.execute(
                    "UPDATE camicia_discord_links SET discord_id = NULL WHERE discord_id = %s AND boinc_user_id != %s",
                    (discord_id, boinc_user_id),
                )

                # 6. Complete the link
                await cur.execute(
                    "UPDATE camicia_discord_links "
                    "SET discord_id = %s, discord_username = %s, linked_at = NOW(), pin = NULL, pin_expires_at = NULL, unlinked_at = NULL "
                    "WHERE boinc_user_id = %s",
                    (discord_id, discord_username, boinc_user_id),
                )

                clear_failed_link_attempts(discord_id)
                clear_spam_strikes(discord_id)
                return True, "Success", boinc_user_id, volunteer_name

    except Exception as e:
        logger.exception("Error linking discord user %s: %s", discord_id, e)
        return False, "An unexpected error occurred while linking accounts. Please try again later.", None, None


async def unlink_discord_user(
    discord_id: int,
) -> Tuple[bool, Union[str, discord.Embed], Optional[int], Optional[str]]:
    """
    Unlinks a Discord account from its BOINC account.
    Records unlinked_at timestamp in MariaDB to enforce the 1-hour anti-abuse cooldown.
    Returns: (success, message_or_embed, boinc_user_id, volunteer_name)
    """
    is_muted, rem_mute = check_user_muted(discord_id)
    if is_muted:
        return False, get_spam_muted_embed(rem_mute), None, None

    pool = await get_db_pool()
    if pool is None:
        return False, "Database connection is currently unavailable. Please try again in a few moments.", None, None

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT l.boinc_user_id, u.name "
                    "FROM camicia_discord_links l "
                    "LEFT JOIN user u ON u.id = l.boinc_user_id "
                    "WHERE l.discord_id = %s AND l.linked_at IS NOT NULL AND l.unlinked_at IS NULL",
                    (discord_id,),
                )
                row = await cur.fetchone()
                if not row:
                    strike_cnt, is_muted, rem_mute = record_spam_strike(discord_id)
                    if is_muted:
                        return False, get_spam_muted_embed(rem_mute), None, None
                    return (
                        False,
                        f"❌ **Your Discord account is not currently linked to any Camicia BOINC account.**\n"
                        f"*(Strike {strike_cnt}/5: Continued repeated requests will cause the bot to ignore your messages for 15 minutes)*",
                        None,
                        None,
                    )

                boinc_user_id, volunteer_name = row
                volunteer_name = volunteer_name or f"Volunteer #{boinc_user_id}"

                # Update row with unlinked_at = NOW() and linked_at = NULL to preserve record for 1-hour cooldown
                await cur.execute(
                    "UPDATE camicia_discord_links "
                    "SET unlinked_at = NOW(), linked_at = NULL, pin = NULL "
                    "WHERE discord_id = %s",
                    (discord_id,),
                )
                clear_spam_strikes(discord_id)
                return True, "Success", boinc_user_id, volunteer_name
    except Exception as e:
        logger.exception("Error unlinking discord user %s: %s", discord_id, e)
        return False, "An unexpected error occurred while unlinking accounts. Please try again later.", None, None


@tasks.loop(seconds=30.0)
async def unlink_queue_loop():
    """
    Transactional Outbox queue worker (30-second interval):
    Polls camicia_discord_links for rows marked with pin = 'PENDING_REVOKE' (queued from website).
    Removes the Volunteer role on Discord, then clears pin to NULL while preserving unlinked_at
    for the 1-hour anti-abuse cooldown.
    This keeps Discord bot credentials completely off the web server.
    """
    if not config.DISCORD_GUILD_ID:
        return

    pool = await get_db_pool()
    if pool is None:
        return

    guild = bot.get_guild(config.DISCORD_GUILD_ID)
    if guild is None:
        try:
            guild = await bot.fetch_guild(config.DISCORD_GUILD_ID)
        except Exception:
            return
    if guild is None:
        return

    volunteer_role = await get_volunteer_role(guild)

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, discord_id, boinc_user_id FROM camicia_discord_links WHERE pin = 'PENDING_REVOKE'"
                )
                rows = await cur.fetchall()
                if not rows:
                    return

                for row_id, discord_id, boinc_user_id in rows:
                    if discord_id and volunteer_role:
                        try:
                            member = await get_guild_member(discord_id)
                            if member and volunteer_role in member.roles:
                                await member.remove_roles(volunteer_role, reason="Unlinked on Camicia website")
                                logger.info(
                                    "Unlink worker: removed Volunteer role from member %s (Discord ID: %s, BOINC #%s)",
                                    member,
                                    discord_id,
                                    boinc_user_id,
                                )
                        except Exception as e:
                            logger.warning(
                                "Unlink worker: could not remove Volunteer role from Discord ID %s: %s",
                                discord_id,
                                e,
                            )

                    await cur.execute("UPDATE camicia_discord_links SET pin = NULL WHERE id = %s", (row_id,))
                    if discord_id:
                        try:
                            await cur.execute("UPDATE camicia_duel_stats SET boinc_user_id = NULL WHERE discord_id = %s", (discord_id,))
                        except Exception:
                            pass
                    logger.info("Unlink worker: processed revoke for record ID %s (BOINC #%s)", row_id, boinc_user_id)
    except Exception as e:
        logger.debug("Error in unlink_queue_loop: %s", e)


@unlink_queue_loop.before_loop
async def before_unlink_queue_loop():
    await bot.wait_until_ready()
    logger.info("Unlink outbox queue worker started (interval: 30.0s)")


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
    if config.STAGING_MODE:
        logger.info("Running in STAGING MODE (Tester Role ID: %s)", config.DISCORD_TESTER_ROLE_ID)
        try:
            await bot.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.playing,
                    name="[STAGING] • Testing Mode",
                )
            )
        except Exception as e:
            logger.warning("Could not set staging presence: %s", e)
    else:
        logger.info("Running in PRODUCTION MODE")


async def staging_interaction_check(interaction: discord.Interaction) -> bool:
    """Restricts all interactions to Administrators and Testers when running in STAGING_MODE."""
    if not config.STAGING_MODE:
        return True

    member = (
        interaction.user
        if isinstance(interaction.user, discord.Member)
        else await get_guild_member(interaction.user.id)
    )
    if not is_tester_or_admin(member):
        raise app_commands.CheckFailure(
            "🔒 **Staging Bot Restricted**: This staging bot is currently restricted to Administrators and Testers."
        )
    return True


bot.tree.interaction_check = staging_interaction_check


def is_bot_commands_channel():
    """Restricts command execution to the configured bot-commands channel."""
    async def predicate(interaction: discord.Interaction) -> bool:
        allowed_id = config.DISCORD_BOT_COMMANDS_CHANNEL_ID
        if allowed_id:
            if interaction.channel_id == allowed_id:
                return True
        elif interaction.channel and getattr(interaction.channel, "name", "") == "bot-commands":
            return True

        # In production, administrators can test anywhere; in staging, strict channel isolation applies
        if not config.STAGING_MODE and interaction.user and hasattr(interaction.user, "guild_permissions") and interaction.user.guild_permissions.administrator:
            return True

        target_mention = f"<#{allowed_id}>" if allowed_id else "#bot-commands"
        raise app_commands.CheckFailure(
            f"❌ Bot commands can only be used in {target_mention} to keep discussions clean!"
        )
    return app_commands.check(predicate)


def is_bot_commands_channel_or_dm():
    """Allows command execution in Direct Messages (DMs) or the configured bot-commands channel."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return True
        allowed_id = config.DISCORD_BOT_COMMANDS_CHANNEL_ID
        if allowed_id:
            if interaction.channel_id == allowed_id:
                return True
        elif interaction.channel and getattr(interaction.channel, "name", "") == "bot-commands":
            return True

        if not config.STAGING_MODE and interaction.user and hasattr(interaction.user, "guild_permissions") and interaction.user.guild_permissions.administrator:
            return True

        target_mention = f"<#{allowed_id}>" if allowed_id else "#bot-commands"
        raise app_commands.CheckFailure(
            f"❌ This command can only be used in {target_mention} or in Direct Messages (DMs) with CamiciaBot!"
        )
    return app_commands.check(predicate)


def is_dm_only():
    """Restricts command execution strictly to Direct Messages (DMs) to protect privacy."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return True
        raise app_commands.CheckFailure(
            "🔒 **Privacy Notice**: Linking and unlinking commands are only available in Direct Messages (DMs) with CamiciaBot to keep your account details secure. Please send me a private message!"
        )
    return app_commands.check(predicate)


@bot.tree.command(name="help", description="View CamiciaBot commands, quotas, and volunteer perks")
async def help_cmd(interaction: discord.Interaction):
    is_dm = (interaction.guild is None)

    guild = interaction.guild or (bot.get_guild(config.DISCORD_GUILD_ID) if config.DISCORD_GUILD_ID else None)
    is_admin = False
    if interaction.guild is not None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else (
            interaction.guild.get_member(interaction.user.id) if hasattr(interaction.guild, "get_member") else None
        )
        if member and hasattr(member, "guild_permissions") and hasattr(member.guild_permissions, "administrator"):
            is_admin = bool(member.guild_permissions.administrator)

    is_vol = await check_is_volunteer(interaction.user, guild)

    # Determine location context
    allowed_id = config.DISCORD_BOT_COMMANDS_CHANNEL_ID
    bot_channel_mention = f"<#{allowed_id}>" if allowed_id else "#bot-commands"

    if is_dm:
        location_desc = "Direct Messages (Private) 📬"
        is_bot_channel = False
    else:
        is_bot_channel = (interaction.channel_id == allowed_id) or (
            getattr(interaction.channel, "name", "") == "bot-commands"
        )
        ch_name = getattr(interaction.channel, "name", "channel")
        location_desc = f"{bot_channel_mention} (Interactive Arena)" if is_bot_channel else f"#{ch_name} (Channel)"

    if is_admin:
        role_desc = "Server Administrator 👑"
        color = 0x9B59B6  # Purple
    elif is_vol:
        role_desc = "Linked Volunteer 🏅"
        color = 0xF1C40F  # Gold
    else:
        role_desc = "Guest (Unlinked) 👤"
        color = 0x3498DB  # Blue

    embed = discord.Embed(
        title="📖 CamiciaBot Command Guide",
        description=(
            f"• **Role**: **{role_desc}**\n"
            f"• **Location**: {location_desc}\n"
        ),
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    # 1. Gameplay & Mini-Games
    if is_bot_channel:
        embed.add_field(
            name="⚔️ Multiplayer Duels (Beggar-My-Neighbour)",
            value=(
                "• `/duel mode:casual`: Open tavern challenge or friendly direct duel *(Single game)*\n"
                "• `/duel mode:ranked opponent:@User`: Best-of-3 Elo series *(Volunteers only)*\n"
                "• `/duel-leaderboard`: View top 10 Elo champions and your standing\n"
                "• `/duel-stats [user]`: View your or another player's duel profile\n"
                "• `/duel-settings direct_challenges:<true|false>`: Enable or disable challenge invites"
            ),
            inline=False,
        )
        lucky_quota = "5 rolls/day 🏅" if is_vol else "3 rolls/day (Link for 5)"
        embed.add_field(
            name="🍀 Lucky Permutations",
            value=(
                f"• `/lucky`: Draw a random deal from ~6.5 × 10²⁰ space *({lucky_quota})*\n"
                "• `/lucky-leaderboard`: View today's top lucky rolls on the server"
            ),
            inline=False,
        )
    elif is_dm:
        embed.add_field(
            name="⚙️ Private Duel Settings",
            value=(
                "• `/duel-stats`: View your personal duel profile and remaining tickets in private\n"
                "• `/duel-settings direct_challenges:<true|false>`: Manage challenge privacy\n\n"
                f"💡 *Interactive games (`/duel`, `/lucky`) are played in the server's {bot_channel_mention} channel.*"
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="⚔️ Interactive Games & Arena",
            value=(
                f"💡 *To keep discussion clean, mini-games (`/duel`, `/lucky`, `/duel-leaderboard`, `/lucky-leaderboard`) are played exclusively in {bot_channel_mention}.*"
            ),
            inline=False,
        )

    # 2. Account Linking & Volunteer Perks
    if is_vol:
        unlink_info = (
            "• `/unlink`: Disconnect your BOINC account *(DM only)*\n"
            if is_dm
            else "• `/unlink`: Disconnect your BOINC account *(Run in DMs with CamiciaBot)*\n"
        )
        embed.add_field(
            name="🏅 BOINC Volunteer Account",
            value=(
                "✅ **Account Linked**: You have unlocked all volunteer perks!\n"
                "• **Active Perks**: Unlimited casual duels, 5 ranked tickets/day, direct challenges, 5 `/lucky` rolls/day.\n"
                f"{unlink_info}"
            ),
            inline=False,
        )
    else:
        link_info = (
            "• `/link <code>`: Link your BOINC account\n"
            if is_dm
            else "• `/link <code>`: Link your BOINC account *(Send code in DMs with CamiciaBot)*\n"
        )
        embed.add_field(
            name="🔐 Link BOINC Account (Unlock Volunteer Perks)",
            value=(
                f"{link_info}"
                f"• **Get your code**: [{config.PROJECT_LINK_URL}]({config.PROJECT_LINK_URL})\n\n"
                "🌟 **Perks Unlocked Upon Linking**:\n"
                "• 🏅 **@Volunteer** server role badge\n"
                "• ⚔️ **Ranked Best-of-3** matches & server Elo ladder\n"
                "• 🎯 **Direct challenges** against specific friends\n"
                "• ♾️ **Unlimited** casual duels *(guests capped at 3/day)*\n"
                "• 🍀 **5 daily rolls** in `/lucky` *(guests capped at 3/day)*"
            ),
            inline=False,
        )

    # 3. Project Science & Records
    embed.add_field(
        name="📊 Project Records & Research",
        value=(
            "• `/records`: View the standing world record game length and total infinite loops discovered across the project\n"
            f"• **Project Web**: [{config.PROJECT_BASE_URL}]({config.PROJECT_BASE_URL})"
        ),
        inline=False,
    )

    # 4. Administrator Diagnostics (Admins only!)
    if is_admin:
        embed.add_field(
            name="🔧 Administrator Tools",
            value=(
                "• `/ping`: Diagnostic latency, MariaDB pool status, disk storage, and process memory *(Admin only)*"
            ),
            inline=False,
        )

    embed.set_footer(
        text="Camicia Beggar-My-Neighbour • ~6.5 × 10²⁰ space",
        icon_url=get_bot_avatar_url(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ping", description="Check bot latency, database status, and project storage (Admin only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def ping_cmd(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000, 1)

    proj_dir = config.CAMICIA_PROJECT_DIR.resolve()
    has_longest = (proj_dir / "records_longest.txt").exists()
    has_history = (proj_dir / "records_longest_history.txt").exists()
    has_loops = (proj_dir / "records_loops.txt").exists()

    # Check MariaDB connection
    db_status = "❌ Not connected"
    pool = await get_db_pool()
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT VERSION()")
                    row = await cur.fetchone()
                    version = row[0] if row else "unknown"
                    db_status = f"✅ Connected (`MariaDB {version}` at `{config.DB_HOST}`)"
        except Exception as e:
            db_status = f"⚠️ Pool initialized but ping query failed: `{e}`"
    else:
        db_status = f"❌ Connection failed (`{config.DB_USER}@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}`)"

    status_lines = [
        f"🏓 **Pong!** Latency: `{latency_ms}ms`",
        f"📂 **Project Dir**: `{proj_dir}`",
        f"🗄️ **Database**: {db_status}",
        f"• `records_longest.txt`: {'✅' if has_longest else '❌'}",
        f"• `records_longest_history.txt`: {'✅' if has_history else '❌'}",
        f"• `records_loops.txt`: {'✅' if has_loops else '❌'}",
    ]

    await interaction.response.send_message("\n".join(status_lines), ephemeral=True)


@bot.tree.command(name="records", description="View the current standing longest game and total loops found")
@app_commands.guild_only()
@is_bot_commands_channel()
@app_commands.checks.cooldown(1, 15.0, key=lambda i: i.channel_id)
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
        icon_url=get_bot_avatar_url(),
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="lucky", description="Draw a random Beggar-My-Neighbour deal and test your luck! (3-5 rolls/day)")
@app_commands.guild_only()
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
        icon_url=get_bot_avatar_url(),
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
                    icon_url=get_bot_avatar_url(),
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
                    icon_url=get_bot_avatar_url(),
                )
                if watcher:
                    watcher.state["last_best_cards"] = cards
                    watcher._save_state()

            try:
                await records_ch.send(content=b_content, embed=b_embed)
                logger.info("Broadcasted /lucky discovery to %s", records_ch.name)
            except Exception as e:
                logger.error("Failed to broadcast /lucky discovery to records channel: %s", e)


@bot.tree.command(name="lucky-leaderboard", description="View today's top /lucky rolls on the server")
@app_commands.guild_only()
@is_bot_commands_channel()
@app_commands.checks.cooldown(1, 15.0, key=lambda i: i.channel_id)
async def lucky_leaderboard_cmd(interaction: discord.Interaction):
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
        text="Camicia BOINC Project • /lucky-leaderboard",
        icon_url=get_bot_avatar_url(),
    )
    await interaction.response.send_message(embed=embed)




# ==========================================
# /duel Mini-Game Callbacks & Commands
# ==========================================

async def can_accept_duel_callback(user: discord.User) -> Tuple[bool, str]:
    if duel_mgr.is_user_busy(user.id):
        return False, "You are currently participating in another active duel."
    guild = bot.get_guild(config.DISCORD_GUILD_ID) if config.DISCORD_GUILD_ID else None
    is_vol = await check_is_volunteer(user, guild)
    can_play, reason = await duel_mgr.can_play_casual(user.id, is_vol)
    if not can_play:
        return False, reason
    return True, ""


async def on_duel_accept(
    interaction: discord.Interaction,
    challenger: discord.Member,
    accepter: discord.Member,
    mode: str,
):
    try:
        pool = await get_db_pool()
        if pool is None:
            duel_mgr.release_users(challenger.id, accepter.id)
            if duel_mgr.has_pending_challenge_target(accepter.id):
                duel_mgr.clear_pending_challenge(accepter.id)
            await interaction.response.send_message(
                "⚔️ **Duel Unavailable**: The duel arena is currently undergoing maintenance. Please try again in a few moments!",
                ephemeral=True,
            )
            return

        duel_mgr.lock_users(challenger.id, accepter.id)
        if duel_mgr.has_pending_challenge_target(accepter.id):
            duel_mgr.clear_pending_challenge(accepter.id)

        cut_view = DeckCutAndCheerView(
            duel_mgr,
            challenger,
            accepter,
            timeout=config.DUEL_DECK_CUT_TIMEOUT_SECONDS,
            on_cuts_complete_callback=on_duel_cuts_complete,
            mode=mode,
        )

        mode_title = "Ranked Best-of-3 Series 🏆" if mode == "ranked" else "Casual Single Game 🤺"
        embed = discord.Embed(
            title="⚔️ MATCH STARTED: Deck Cut Phase",
            description=(
                f"{challenger.mention} 🆚 {accepter.mention}\n\n"
                f"• **Mode**: **{mode_title}**\n"
                f"• **Deck Cut Choice**: Alter the dealing sequence by cyclically shifting your 26-card half-deck!\n"
                f"• **Time Limit**: You have **15 seconds** to choose your cut below.\n\n"
                f"📣 **Spectators**: Cheer for your favorite duelist using the buttons below!"
            ),
            color=0xF1C40F if mode == "ranked" else 0x2ECC71,
        )
        embed.set_footer(
            text="Camicia Beggar-My-Neighbour • Deck Cut Phase",
            icon_url=get_bot_avatar_url(),
        )

        cut_view.message = interaction.message
        await interaction.response.edit_message(content=None, embed=embed, view=cut_view)
    except Exception as e:
        logger.error("Error transitioning to duel cut phase: %s", e, exc_info=True)
        duel_mgr.release_users(challenger.id, accepter.id)
        if duel_mgr.has_pending_challenge_target(accepter.id):
            duel_mgr.clear_pending_challenge(accepter.id)


async def broadcast_duel_discovery(
    p1: Union[discord.Member, discord.User],
    p2: Union[discord.Member, discord.User],
    mode: str,
    is_loop: bool,
    cards: int,
    tricks: int,
    deal_index: Optional[int] = None,
    match_id: Optional[int] = None,
):
    """
    Broadcasts a record-breaking deal or infinite loop discovered during a duel
    to the configured records channel.
    """
    if not config.DISCORD_RECORDS_CHANNEL_ID:
        return

    records_ch = bot.get_channel(config.DISCORD_RECORDS_CHANNEL_ID)
    if records_ch is None:
        try:
            records_ch = await bot.fetch_channel(config.DISCORD_RECORDS_CHANNEL_ID)
        except Exception as e:
            logger.error("Could not fetch records channel for /duel discovery: %s", e)
            return

    if not records_ch:
        return

    now_ts = int(datetime.now(timezone.utc).timestamp())
    time_tag = f"<t:{now_ts}:F> (<t:{now_ts}:R>)"
    mode_label = "Ranked Best-of-3" if mode == "ranked" else "Casual"
    deal_index_str = f"{deal_index}" if deal_index is not None else "N/A"

    if is_loop:
        b_embed = discord.Embed(
            title="♾️ Infinite Loop Discovered via /duel!",
            description=(
                f"A non-terminating Beggar-My-Neighbour deal has just been discovered "
                f"during a **{mode_label}** duel between {p1.mention} and {p2.mention}!"
            ),
            color=0x9B59B6,
            timestamp=datetime.now(timezone.utc),
        )
        b_embed.add_field(name="⚔️ Duelists", value=f"{p1.mention} 🆚 {p2.mention}", inline=True)
        if deal_index is not None:
            b_embed.add_field(name="🔢 Deal Index", value=f"`{deal_index_str}`", inline=True)
        if match_id is not None:
            b_embed.add_field(name="🎮 Match ID", value=f"`#{match_id}`", inline=True)
        b_embed.add_field(name="⏱️ Discovered At", value=time_tag, inline=False)
        b_embed.set_footer(
            text="Camicia BOINC Project • /duel Discovery",
            icon_url=get_bot_avatar_url(),
        )
        try:
            await records_ch.send(content=None, embed=b_embed)
            logger.info("Broadcasted /duel loop discovery to %s", records_ch.name)
        except Exception as e:
            logger.error("Failed to broadcast /duel loop discovery to records channel: %s", e)
    else:
        standing_rec = config.REAL_WORLD_RECORD_CARDS
        if duel_mgr.get_standing_record:
            try:
                standing_rec = max(standing_rec, duel_mgr.get_standing_record())
            except Exception:
                pass

        b_embed = discord.Embed(
            title="🚨 ALL-TIME WORLD RECORD BROKEN VIA /DUEL! 🏆",
            description=(
                f"A new finite Beggar-My-Neighbour deal exceeding the world record "
                f"({standing_rec:,} cards) has just been played in a **{mode_label}** duel "
                f"between {p1.mention} and {p2.mention}!"
            ),
            color=0xFF0033,
            timestamp=datetime.now(timezone.utc),
        )
        b_embed.add_field(name="🃏 Cards Played", value=f"**{cards:,}** cards", inline=True)
        b_embed.add_field(name="🔄 Tricks", value=f"**{tricks:,}** tricks", inline=True)
        b_embed.add_field(name="⚔️ Duelists", value=f"{p1.mention} 🆚 {p2.mention}", inline=True)
        if deal_index is not None:
            b_embed.add_field(name="🔢 Deal Index", value=f"`{deal_index_str}`", inline=True)
        if match_id is not None:
            b_embed.add_field(name="🎮 Match ID", value=f"`#{match_id}`", inline=True)
        b_embed.add_field(name="⏱️ Discovered At", value=time_tag, inline=False)
        b_embed.set_footer(
            text="Camicia BOINC Project • /duel Record",
            icon_url=get_bot_avatar_url(),
        )
        if watcher:
            watcher.state["last_best_cards"] = cards
            watcher._save_state()

        try:
            await records_ch.send(content="@everyone", embed=b_embed)
            logger.info("Broadcasted /duel record discovery to %s", records_ch.name)
        except Exception as e:
            logger.error("Failed to broadcast /duel record discovery to records channel: %s", e)


async def on_duel_cuts_complete(
    message: discord.Message,
    p1: discord.Member,
    p2: discord.Member,
    cut_a: int,
    cut_b: int,
    mode: str,
):
    if message is None:
        duel_mgr.release_users(p1.id, p2.id)
        return

    # Phase S3: Suspense Animation
    label_a = CUT_LABELS.get(cut_a, "Kept As-Is")
    label_b = CUT_LABELS.get(cut_b, "Kept As-Is")
    mode_label = "Ranked Best-of-3" if mode == "ranked" else "Casual Single Game"

    suspense_embed = discord.Embed(
        title="🔀 Dealing Hands & Cutting Decks...",
        description=(
            f"{p1.mention} 🆚 {p2.mention}\n\n"
            f"• **{p1.display_name}** cut: **{label_a}**\n"
            f"• **{p2.display_name}** cut: **{label_b}**\n\n"
            f"🎴 *Shuffling 52 cards across ~6.5 × 10²⁰ combinations...*\n"
            f"⚔️ *Simulating {mode_label} clash...*"
        ),
        color=0xF39C12,
    )
    suspense_embed.set_footer(
        text="Camicia Beggar-My-Neighbour • Simulating...",
        icon_url=get_bot_avatar_url(),
    )

    try:
        await message.edit(embed=suspense_embed, view=None)
    except Exception as e:
        logger.warning("Could not edit message for suspense phase: %s", e)

    # 3s rate-limit safe delay to build suspense
    await asyncio.sleep(3.0)

    # Phase S4: Simulation & Boxscore Results
    try:
        if mode == "casual":
            deal_index = get_random_deal_index()
            sim_res = duel_mgr.simulate_game(deal_index, cut_a, cut_b, p1_starts=True)
            winner_id = p1.id if sim_res["winner"] == 1 else (p2.id if sim_res["winner"] == 2 else None)
            series_score = "1-0" if sim_res["winner"] == 1 else ("0-1" if sim_res["winner"] == 2 else "0-0")
            status = sim_res["status"]

            match_id = await duel_mgr.record_match(
                mode="casual",
                p1_id=p1.id,
                p2_id=p2.id,
                winner_id=winner_id,
                cards_played=sim_res["cards"],
                tricks=sim_res["tricks"],
                series_score=series_score,
                status=status,
            )

            c1, c2 = duel_mgr.get_cheer_counts(message.id, p1.id, p2.id)
            duel_mgr.clear_cheers(message.id)
            duel_mgr.release_users(p1.id, p2.id)

            is_loop = sim_res["is_loop"]
            is_record = sim_res["is_record"]
            if is_record:
                title = f"🚨 WORLD RECORD SURPASSED! (Match #{match_id or '?'})"
                color = 0xE74C3C
                desc = "Incredible! This duel produced a game length surpassing the standing world record!"
            elif is_loop:
                title = f"♾️ COSMIC INFINITE LOOP! (Match #{match_id or '?'})"
                color = 0x9B59B6
                desc = "A non-terminating game cycle was discovered in this duel! Neither player loses."
            else:
                winner_name = p1.display_name if winner_id == p1.id else p2.display_name
                title = f"🏆 {winner_name} Wins! (Match #{match_id or '?'})"
                color = sim_res["color"]
                desc = f"**{winner_name}** claimed all 52 cards in a fierce Beggar-My-Neighbour battle!"

            embed = discord.Embed(
                title=title,
                description=desc,
                color=color,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(
                name="🃏 Starting Hands",
                value=(
                    f"• {p1.mention}: {format_hand_summary(sim_res['hand_a'])}\n"
                    f"• {p2.mention}: {format_hand_summary(sim_res['hand_b'])}"
                ),
                inline=False,
            )
            embed.add_field(
                name="📊 Match Breakdown",
                value=(
                    f"• **Winner**: {('<@' + str(winner_id) + '>') if winner_id else '🤝 Draw / Loop'}\n"
                    f"• **Game Length**: **{sim_res['cards']:,} cards** ({sim_res['tricks']:,} tricks)\n"
                    f"• **Rarity Tier**: **{sim_res['tier']}** ({sim_res['rarity_desc']})"
                ),
                inline=False,
            )
            embed.add_field(
                name="📣 Spectator Cheers",
                value=f"• **{p1.display_name}**: {c1} cheer{'s' if c1 != 1 else ''}\n• **{p2.display_name}**: {c2} cheer{'s' if c2 != 1 else ''}",
                inline=False,
            )
            embed.set_footer(
                text="Camicia Beggar-My-Neighbour • Combinatorial space: ~6.5 × 10²⁰",
                icon_url=get_bot_avatar_url(),
            )

            rematch_view = RematchView(duel_mgr, p1, p2, on_rematch_callback=on_duel_rematch)
            rematch_view.message = message
            await message.edit(embed=embed, view=rematch_view)

            if is_record or is_loop:
                asyncio.create_task(
                    broadcast_duel_discovery(
                        p1=p1,
                        p2=p2,
                        mode="casual",
                        is_loop=is_loop,
                        cards=sim_res["cards"],
                        tricks=sim_res["tricks"],
                        deal_index=sim_res.get("deal_index"),
                        match_id=match_id,
                    )
                )

        else:  # Ranked Mode
            b3_res = duel_mgr.simulate_best_of_3(cut_a, cut_b)
            winner_id = p1.id if b3_res["series_winner"] == 1 else (p2.id if b3_res["series_winner"] == 2 else None)
            series_score = b3_res["series_score"]
            status = "record" if b3_res["is_record"] else ("loop" if b3_res["is_loop"] else "completed")

            p1_stats = await duel_mgr.get_duel_stats(p1.id)
            p2_stats = await duel_mgr.get_duel_stats(p2.id)
            r1, r2 = p1_stats["elo_rating"], p2_stats["elo_rating"]
            score_a = 1.0 if b3_res["series_winner"] == 1 else (0.5 if b3_res["series_winner"] == 0 else 0.0)
            new_r1, new_r2, delta_a, delta_b = calculate_elo(r1, r2, score_a)
            await duel_mgr.update_elo_ratings(p1.id, p2.id, new_r1, new_r2)

            match_id = await duel_mgr.record_match(
                mode="ranked",
                p1_id=p1.id,
                p2_id=p2.id,
                winner_id=winner_id,
                cards_played=b3_res["total_cards"],
                tricks=b3_res["total_tricks"],
                series_score=series_score,
                status=status,
            )

            c1, c2 = duel_mgr.get_cheer_counts(message.id, p1.id, p2.id)
            duel_mgr.clear_cheers(message.id)
            duel_mgr.release_users(p1.id, p2.id)

            if b3_res["is_record"]:
                title = f"🚨 WORLD RECORD SURPASSED! (Match #{match_id or '?'})"
                color = 0xE74C3C
                desc = "Incredible! A game in this ranked series exceeded the standing world record!"
            elif b3_res["is_loop"]:
                title = f"♾️ COSMIC INFINITE LOOP! (Match #{match_id or '?'})"
                color = 0x9B59B6
                desc = "A non-terminating game cycle occurred during this ranked series!"
            else:
                winner_name = p1.display_name if winner_id == p1.id else p2.display_name
                title = f"🏆 {winner_name} Wins the Series! (Match #{match_id or '?'})"
                color = 0xF1C40F
                desc = f"**{winner_name}** conquered the Best-of-3 Ranked series ({series_score})!"

            g1_win = p1.mention if b3_res["g1"]["winner"] == 1 else (p2.mention if b3_res["g1"]["winner"] == 2 else "Loop")
            g2_win = p1.mention if b3_res["g2"]["winner"] == 1 else (p2.mention if b3_res["g2"]["winner"] == 2 else "Loop")
            breakdown_lines = [
                f"• **Series Score**: **{series_score}**",
                f"• **Game 1** ({p1.display_name} first): {b3_res['g1']['cards']:,} cards ({b3_res['g1']['tricks']:,} tricks) — Winner: {g1_win}",
                f"• **Game 2** ({p2.display_name} first): {b3_res['g2']['cards']:,} cards ({b3_res['g2']['tricks']:,} tricks) — Winner: {g2_win}",
            ]
            if b3_res["g3"]:
                g3_win = p1.mention if b3_res["g3"]["winner"] == 1 else p2.mention
                breakdown_lines.append(
                    f"• **Game 3 (Tiebreak)**: {p1.display_name} ({b3_res['g3']['p1_cards']:,} cards) vs {p2.display_name} ({b3_res['g3']['p2_cards']:,} cards) — Winner: {g3_win}"
                )
            breakdown_lines.append(f"• **Total Cards Played**: **{b3_res['total_cards']:,}** cards")

            delta_a_str = f"+{delta_a}" if delta_a >= 0 else f"{delta_a}"
            delta_b_str = f"+{delta_b}" if delta_b >= 0 else f"{delta_b}"
            breakdown_lines.append(
                f"\n**📈 Rating Adjustments**:\n"
                f"• {p1.mention}: **{r1}** ➔ **{new_r1}** ({delta_a_str})\n"
                f"• {p2.mention}: **{r2}** ➔ **{new_r2}** ({delta_b_str})"
            )

            embed = discord.Embed(
                title=title,
                description=desc,
                color=color,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(
                name="🃏 Starting Hands (Game 1)",
                value=(
                    f"• {p1.mention}: {format_hand_summary(b3_res['g1']['hand_a'])}\n"
                    f"• {p2.mention}: {format_hand_summary(b3_res['g1']['hand_b'])}"
                ),
                inline=False,
            )
            embed.add_field(
                name="📊 Series Breakdown",
                value="\n".join(breakdown_lines),
                inline=False,
            )
            embed.add_field(
                name="📣 Spectator Cheers",
                value=f"• **{p1.display_name}**: {c1} cheer{'s' if c1 != 1 else ''}\n• **{p2.display_name}**: {c2} cheer{'s' if c2 != 1 else ''}",
                inline=False,
            )
            embed.set_footer(
                text="Camicia Beggar-My-Neighbour • Ranked BO3 • ~6.5 × 10²⁰ space",
                icon_url=get_bot_avatar_url(),
            )
            await message.edit(embed=embed, view=None)

            if b3_res["is_loop"]:
                loop_g = b3_res.get("loop_game") or b3_res["g1"]
                asyncio.create_task(
                    broadcast_duel_discovery(
                        p1=p1,
                        p2=p2,
                        mode="ranked",
                        is_loop=True,
                        cards=loop_g.get("cards", 0),
                        tricks=loop_g.get("tricks", 0),
                        deal_index=loop_g.get("deal_index"),
                        match_id=match_id,
                    )
                )
            if b3_res["is_record"]:
                rec_g = b3_res.get("record_game") or b3_res["g1"]
                asyncio.create_task(
                    broadcast_duel_discovery(
                        p1=p1,
                        p2=p2,
                        mode="ranked",
                        is_loop=False,
                        cards=rec_g.get("cards", 0),
                        tricks=rec_g.get("tricks", 0),
                        deal_index=rec_g.get("deal_index"),
                        match_id=match_id,
                    )
                )

    except Exception as e:
        logger.error("Error executing duel completion: %s", e, exc_info=True)
        duel_mgr.release_users(p1.id, p2.id)
        duel_mgr.clear_cheers(message.id)


async def on_duel_rematch(interaction: discord.Interaction, p1: discord.Member, p2: discord.Member):
    pool = await get_db_pool()
    if pool is None:
        await interaction.response.send_message(
            "⚔️ **Rematch Unavailable**: The duel arena is currently undergoing maintenance. Please try again in a few moments!",
            ephemeral=True,
        )
        if interaction.message:
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass
        return

    is_p1_vol = await check_is_volunteer(p1, interaction.guild)
    is_p2_vol = await check_is_volunteer(p2, interaction.guild)

    can_p1, reason_p1 = await duel_mgr.can_play_casual(p1.id, is_p1_vol)
    if not can_p1:
        await interaction.response.send_message(f"Cannot rematch: {reason_p1}", ephemeral=True)
        if interaction.message:
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass
        return

    can_p2, reason_p2 = await duel_mgr.can_play_casual(p2.id, is_p2_vol)
    if not can_p2:
        await interaction.response.send_message(f"Cannot rematch: {reason_p2}", ephemeral=True)
        if interaction.message:
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass
        return

    if duel_mgr.is_user_busy(p1.id) or duel_mgr.is_user_busy(p2.id):
        await interaction.response.send_message("One of the players is already participating in another duel.", ephemeral=True)
        if interaction.message:
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass
        return

    duel_mgr.lock_users(p1.id, p2.id)

    try:
        cut_view = DeckCutAndCheerView(
            duel_mgr,
            p1,
            p2,
            timeout=config.DUEL_DECK_CUT_TIMEOUT_SECONDS,
            on_cuts_complete_callback=on_duel_cuts_complete,
            mode="casual",
        )

        embed = discord.Embed(
            title="⚔️ REMATCH STARTED: Deck Cut Phase",
            description=(
                f"{p1.mention} 🆚 {p2.mention}\n\n"
                f"• **Mode**: **Casual Single Game (Swapped Turns)** 🤺\n"
                f"• **Deck Cut Choice**: Alter the dealing sequence by cyclically shifting your 26-card half-deck!\n"
                f"• **Time Limit**: You have **15 seconds** to choose your cut below.\n\n"
                f"📣 **Spectators**: Cheer for your favorite duelist using the buttons below!"
            ),
            color=0x2ECC71,
        )
        embed.set_footer(
            text="Camicia Beggar-My-Neighbour • Rematch Deck Cut Phase",
            icon_url=get_bot_avatar_url(),
        )

        await interaction.response.edit_message(content=None, embed=embed, view=cut_view)
        cut_view.message = interaction.message
    except Exception as e:
        logger.error("Error transitioning to rematch cut phase: %s", e, exc_info=True)
        duel_mgr.release_users(p1.id, p2.id)


@bot.tree.command(name="duel", description="Challenge another player or the server to a Beggar-My-Neighbour duel!")
@app_commands.guild_only()
@is_bot_commands_channel()
@app_commands.choices(
    mode=[
        app_commands.Choice(name="Casual (Single game, open to all)", value="casual"),
        app_commands.Choice(name="Ranked (Best-of-3 series, Volunteers only)", value="ranked"),
    ]
)
@app_commands.describe(
    mode="Game mode: Casual (single deal) or Ranked (Best-of-3 series)",
    opponent="Opponent to challenge directly (required for Ranked, leave blank for Casual tavern challenge)",
)
async def duel_cmd(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str],
    opponent: Optional[discord.Member] = None,
):
    challenger = interaction.user
    selected_mode = mode.value

    # Upfront service availability check
    pool = await get_db_pool()
    if pool is None:
        await interaction.response.send_message(
            "⚔️ **Duels Temporarily Unavailable**: The duel arena is currently undergoing maintenance. Please check back in a few moments!",
            ephemeral=True,
        )
        return

    # Cooldown check
    now = time.monotonic()
    is_challenger_vol = await check_is_volunteer(challenger, interaction.guild)
    cd_time = config.DUEL_VOLUNTEER_COOLDOWN_SECONDS if is_challenger_vol else config.DUEL_GUEST_COOLDOWN_SECONDS
    last_duel = _duel_cooldowns.get(challenger.id, 0.0)
    if now - last_duel < cd_time:
        rem = max(1, int(round(cd_time - (now - last_duel))))
        await interaction.response.send_message(
            f"⏳ **Cooldown Active**: Please wait **{rem}s** before initiating another duel.",
            ephemeral=True,
        )
        return

    # Check if challenger has an incoming pending challenge invitation
    if duel_mgr.has_pending_challenge_target(challenger.id):
        source_id = duel_mgr._pending_challenges.get(challenger.id)
        source_mention = f"<@{source_id}>" if source_id else "another player"
        await interaction.response.send_message(
            f"⚔️ You have a pending challenge invitation from {source_mention} waiting for your response! Please accept or decline it first.",
            ephemeral=True,
        )
        return

    # Check if challenger already has an outgoing pending challenge
    if duel_mgr.has_pending_challenge_source(challenger.id):
        await interaction.response.send_message(
            "⚔️ You already have a pending challenge waiting for a response! Please wait for it to be accepted, declined, or cancelled.",
            ephemeral=True,
        )
        return

    # Check if challenger is currently in an active duel
    if duel_mgr.is_user_busy(challenger.id):
        await interaction.response.send_message(
            "⚔️ You are already participating in an active duel! Please complete it first.",
            ephemeral=True,
        )
        return

    # Validate based on mode and opponent
    if selected_mode == "ranked":
        if opponent is None:
            await interaction.response.send_message(
                "⚔️ **Ranked Mode Requires an Opponent**: Ranked matches are Best-of-3 head-to-head battles against a specific opponent. Please choose an `opponent` or select `Casual` mode for an open tavern challenge.",
                ephemeral=True,
            )
            return

        if not is_challenger_vol:
            await interaction.response.send_message(
                "⚔️ **Ranked Mode Restricted**: Ranked duels are exclusive to volunteers who have linked their BOINC account (`/link`).",
                ephemeral=True,
            )
            return

        is_opp_vol = await check_is_volunteer(opponent, interaction.guild)
        if not is_opp_vol:
            await interaction.response.send_message(
                f"⚔️ **Opponent Ineligible**: Ranked duels are exclusive to volunteers. {opponent.mention} has not linked their BOINC account yet.",
                ephemeral=True,
            )
            return

    else:  # casual
        if opponent is not None:
            if not is_challenger_vol:
                await interaction.response.send_message(
                    "⚔️ **Direct Challenges Restricted**: Direct challenges against specific opponents are exclusive to volunteers (`/link`). As a guest, you can still host an **Open Casual Challenge** by leaving the opponent field blank!",
                    ephemeral=True,
                )
                return

            is_opp_vol = await check_is_volunteer(opponent, interaction.guild)
            if not is_opp_vol:
                await interaction.response.send_message(
                    f"⚔️ **Opponent Ineligible**: Direct challenges are exclusive to volunteers. {opponent.mention} has not linked their BOINC account yet.",
                    ephemeral=True,
                )
                return
        else:
            can_play, reason = await duel_mgr.can_play_casual(challenger.id, is_challenger_vol)
            if not can_play:
                await interaction.response.send_message(f"⏳ **Limit Reached**: {reason}", ephemeral=True)
                return

    # Opponent validations if specified
    if opponent is not None:
        if opponent.bot:
            await interaction.response.send_message("🤖 You cannot challenge bot accounts to a duel.", ephemeral=True)
            return
        if opponent.id == challenger.id:
            await interaction.response.send_message("🃏 You cannot challenge yourself to a duel!", ephemeral=True)
            return
        if duel_mgr.has_pending_challenge_target(opponent.id):
            await interaction.response.send_message(
                f"⏳ {opponent.mention} already has a pending challenge invitation. Please try again in a moment.",
                ephemeral=True,
            )
            return
        if duel_mgr.has_pending_challenge_source(opponent.id):
            await interaction.response.send_message(
                f"⏳ {opponent.mention} has already issued a challenge to another player. Please try again once their challenge resolves.",
                ephemeral=True,
            )
            return
        if duel_mgr.is_user_busy(opponent.id):
            await interaction.response.send_message(
                f"⏳ {opponent.mention} is currently in another duel! Please wait until their match completes.",
                ephemeral=True,
            )
            return

        opp_stats = await duel_mgr.get_duel_stats(opponent.id)
        if not opp_stats["direct_challenges_enabled"]:
            await interaction.response.send_message(
                f"🛡️ {opponent.mention} has disabled direct challenge requests via `/duel-settings`.",
                ephemeral=True,
            )
            return

        if selected_mode == "ranked":
            can_ranked, reason = await duel_mgr.can_play_ranked(challenger.id, opponent.id)
            if not can_ranked:
                await interaction.response.send_message(f"⛔ **Ranked Play Unavailable**: {reason}", ephemeral=True)
                return

    # Record cooldown timestamp
    _duel_cooldowns[challenger.id] = now

    # Lock users & set pending challenge
    if opponent is not None:
        duel_mgr.lock_users(challenger.id, opponent.id)
        duel_mgr.set_pending_challenge(opponent.id, challenger.id)
    else:
        duel_mgr._active_duels.add(challenger.id)

    challenge_timeout = (
        config.DUEL_DIRECT_TIMEOUT_SECONDS if opponent is not None else config.DUEL_OPEN_TIMEOUT_SECONDS
    )

    # Build Challenge View
    view = DuelChallengeView(
        duel_mgr=duel_mgr,
        challenger=challenger,
        opponent=opponent,
        mode=selected_mode,
        timeout=challenge_timeout,
        on_accept_callback=on_duel_accept,
        can_accept_callback=can_accept_duel_callback,
    )

    exp_ts = int(time.time() + challenge_timeout)
    if selected_mode == "ranked":
        title = "⚔️ RANKED DUEL CHALLENGE"
        color = 0xF1C40F
        desc = (
            f"🏆 {challenger.mention} has challenged {opponent.mention} to a **Ranked Best-of-3 Series**!\n\n"
            f"• **Format**: Best of 3 Games (Swapped starting turns + tiebreak)\n"
            f"• **Stakes**: 1 Ranked Ticket each • Elo Rating on the line\n\n"
            f"Click **Accept Duel** below to battle! *(Expires <t:{exp_ts}:R>)*"
        )
    elif opponent is not None:
        title = "🤺 CASUAL DUEL CHALLENGE"
        color = 0x3498DB
        desc = (
            f"⚔️ {challenger.mention} has challenged {opponent.mention} to a **Casual Single-Deal Duel**!\n\n"
            f"• **Format**: Single Game (52 cards)\n"
            f"• **Stakes**: Friendly Match (No Elo rating)\n\n"
            f"Click **Accept Duel** below to battle! *(Expires <t:{exp_ts}:R>)*"
        )
    else:
        title = "🍻 OPEN TAVERN DUEL CHALLENGE"
        color = 0x3498DB
        desc = (
            f"⚔️ {challenger.mention} has thrown down the gauntlet in the tavern!\n\n"
            f"• **Format**: Casual Single Game (52 cards)\n"
            f"• **Open To**: Anyone in the server\n\n"
            f"Click **Accept Duel** below to accept the challenge! *(Expires <t:{exp_ts}:R>)*"
        )

    embed = discord.Embed(
        title=title,
        description=desc,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(
        text="Camicia Beggar-My-Neighbour • Waiting for response...",
        icon_url=get_bot_avatar_url(),
    )

    content = opponent.mention if opponent is not None else None
    try:
        await interaction.response.send_message(content=content, embed=embed, view=view)
        view.message = await interaction.original_response()
    except Exception as e:
        logger.error("Failed to send duel challenge message: %s", e)
        if opponent is not None:
            duel_mgr.clear_pending_challenge(opponent.id)
            duel_mgr.release_users(challenger.id, opponent.id)
        else:
            duel_mgr._active_duels.discard(challenger.id)
        raise


@bot.tree.command(name="duel-settings", description="Configure your /duel preferences (e.g. direct challenge requests)")
@is_bot_commands_channel_or_dm()
@app_commands.describe(direct_challenges="Allow or disallow direct challenge requests from other players")
async def duel_settings_cmd(interaction: discord.Interaction, direct_challenges: bool):
    pool = await get_db_pool()
    if pool is None:
        await interaction.response.send_message(
            "⚙️ **Settings Temporarily Unavailable**: Settings cannot be updated at the moment. Please try again in a few moments!",
            ephemeral=True,
        )
        return

    await duel_mgr.update_direct_challenges_setting(interaction.user.id, direct_challenges)
    status_str = "**enabled** ✅" if direct_challenges else "**disabled** ❌"
    await interaction.response.send_message(
        f"⚙️ Direct challenge requests have been {status_str}.\n"
        f"*(When disabled, other players cannot challenge you directly; you can still participate in open tavern matches.)*",
        ephemeral=True,
    )


@bot.tree.command(name="duel-leaderboard", description="View the server's top-ranked Camicia duel champions (Elo)")
@app_commands.guild_only()
@is_bot_commands_channel()
@app_commands.checks.cooldown(1, 15.0, key=lambda i: i.channel_id)
async def duel_leaderboard_cmd(interaction: discord.Interaction):
    pool = await get_db_pool()
    if pool is None:
        await interaction.response.send_message(
            "⚔️ **Leaderboard Temporarily Unavailable**: The duel arena is currently undergoing maintenance. Please check back in a few moments!",
            ephemeral=True,
        )
        return

    lb = await duel_mgr.get_duel_leaderboard(limit=10)
    caller_rank = await duel_mgr.get_user_rank(interaction.user.id)
    caller_stats = await duel_mgr.get_duel_stats(interaction.user.id)

    embed = discord.Embed(
        title="⚔️ Camicia Duel Leaderboard (Top 10 Elo)",
        description="Ranked Elo ratings update after every Best-of-3 series.\n",
        color=0xF1C40F,
        timestamp=datetime.now(timezone.utc),
    )

    if not lb:
        embed.description += "\n*No ranked duels recorded yet! Use `/duel mode:ranked opponent:@User` to claim the #1 spot.*"
    else:
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        caller_in_top10 = False
        for i, entry in enumerate(lb, start=1):
            if entry["discord_id"] == interaction.user.id:
                caller_in_top10 = True
            medal = medals.get(i, f"`#{i}`")
            lines.append(
                f"{medal} <@{entry['discord_id']}>: **{entry['elo_rating']:,} Elo** • "
                f"{entry['wins']}W - {entry['losses']}L - {entry['ties']}T ({entry['win_rate']:.1f}%)"
            )
        embed.add_field(name="Top Duelists", value="\n".join(lines), inline=False)

        if caller_rank is not None and not caller_in_top10:
            total_caller = caller_stats["wins"] + caller_stats["losses"] + caller_stats["ties"]
            caller_wr = (caller_stats["wins"] / total_caller * 100.0) if total_caller > 0 else 0.0
            embed.add_field(
                name="Your Standing",
                value=(
                    f"`#{caller_rank}` {interaction.user.mention}: **{caller_stats['elo_rating']:,} Elo** • "
                    f"{caller_stats['wins']}W - {caller_stats['losses']}L - {caller_stats['ties']}T ({caller_wr:.1f}%)"
                ),
                inline=False,
            )

    embed.set_footer(
        text="Camicia Beggar-My-Neighbour • /duel-leaderboard",
        icon_url=get_bot_avatar_url(),
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="duel-stats", description="View your or another player's Camicia duel record, Elo, and tickets")
@is_bot_commands_channel_or_dm()
@app_commands.describe(user="Optional player to inspect (leave blank for your own stats; in DMs only your own stats)")
async def duel_stats_cmd(
    interaction: discord.Interaction,
    user: Optional[Union[discord.Member, discord.User]] = None,
):
    # If in Direct Message, only allow inspecting oneself
    if interaction.guild is None and user is not None and user.id != interaction.user.id:
        await interaction.response.send_message(
            "🔒 In Direct Messages, you can only view your own duel stats. "
            "To view another player's stats, run `/duel-stats @user` in the server's #bot-commands channel.",
            ephemeral=True,
        )
        return

    target = user if user is not None else interaction.user

    pool = await get_db_pool()
    if pool is None:
        await interaction.response.send_message(
            "⚔️ **Stats Temporarily Unavailable**: The duel arena is currently undergoing maintenance. Please check back in a few moments!",
            ephemeral=True,
        )
        return

    stats = await duel_mgr.get_duel_stats(target.id)
    rank = await duel_mgr.get_user_rank(target.id)
    recent_matches = await duel_mgr.get_user_recent_matches(target.id, limit=3)
    is_vol = await check_is_volunteer(target, interaction.guild)

    total_ranked = stats["wins"] + stats["losses"] + stats["ties"]
    win_rate = (stats["wins"] / total_ranked * 100.0) if total_ranked > 0 else 0.0

    rem_ranked = max(0, config.MAX_VOLUNTEER_DAILY_RANKED_DUELS - stats["daily_ranked_count"])
    rem_casual = max(0, config.MAX_GUEST_DAILY_CASUAL_DUELS - stats["daily_casual_count"])

    rank_str = f"**#{rank}**" if rank is not None else "*Unranked*"
    direct_str = "Enabled ✅" if stats["direct_challenges_enabled"] else "Disabled ❌"
    vol_badge = "Volunteer 🏅" if is_vol else "Guest 👤"

    color = 0x2ECC71 if stats["wins"] > stats["losses"] else (0xE74C3C if stats["losses"] > stats["wins"] else 0x3498DB)

    embed = discord.Embed(
        title=f"⚔️ Camicia Duel Profile: {target.display_name}",
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    if hasattr(target, "display_avatar") and target.display_avatar:
        embed.set_thumbnail(url=target.display_avatar.url)

    embed.add_field(
        name="🏆 Ranked Standing",
        value=(
            f"• **Elo Rating**: **{stats['elo_rating']:,}** ({rank_str})\n"
            f"• **Record**: **{stats['wins']}W** - **{stats['losses']}L** - **{stats['ties']}T**\n"
            f"• **Win Rate**: **{win_rate:.1f}%** ({total_ranked} ranked series)"
        ),
        inline=False,
    )

    if is_vol:
        tickets_val = f"**{rem_ranked} / {config.MAX_VOLUNTEER_DAILY_RANKED_DUELS}** tickets left"
        casual_val = "Unlimited 🏅"
    else:
        tickets_val = "*Exclusive to linked volunteers* (`/link`)"
        casual_val = f"**{rem_casual} / {config.MAX_GUEST_DAILY_CASUAL_DUELS}** games left"

    embed.add_field(
        name="🎟️ Daily Quotas (UTC)",
        value=(
            f"• **Ranked Tickets**: {tickets_val}\n"
            f"• **Casual Quota**: {casual_val}\n"
            f"• **Status**: {vol_badge} • Direct Challenges: {direct_str}"
        ),
        inline=False,
    )

    if recent_matches:
        match_lines = []
        for m in recent_matches:
            mode_label = "Ranked BO3" if m["mode"] == "ranked" else "Casual"
            if m["outcome"] == "win":
                icon = "🟢 **WIN**"
            elif m["outcome"] == "loss":
                icon = "🔴 **LOSS**"
            else:
                icon = "⚪ **TIE**"
            score_info = f" ({m['series_score']})" if m["series_score"] else ""
            match_lines.append(
                f"• {icon} vs <@{m['opponent_id']}> — *{mode_label}*{score_info} • {m['cards_played']:,} cards"
            )
        embed.add_field(
            name="📜 Recent Matches",
            value="\n".join(match_lines),
            inline=False,
        )
    else:
        embed.add_field(
            name="📜 Recent Matches",
            value="*No matches recorded yet.*",
            inline=False,
        )

    embed.set_footer(
        text=f"Camicia Beggar-My-Neighbour • User ID: {target.id}",
        icon_url=get_bot_avatar_url(),
    )
    await interaction.response.send_message(embed=embed)


async def execute_link_flow(
    user: Union[discord.User, discord.Member], code: str
) -> Tuple[bool, Union[discord.Embed, str]]:
    clean_code = code.strip().replace(" ", "").replace("-", "").lstrip("#")
    discord_username = str(user)
    success, msg, boinc_uid, volunteer_name = await link_discord_user(
        user.id, discord_username, clean_code
    )
    if not success:
        return False, msg

    # Assign Volunteer role on guild
    role_status = "Volunteer role granted 🏅"
    member = await get_guild_member(user.id)
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

    # Sync boinc_user_id in camicia_duel_stats if exists
    pool = await get_db_pool()
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "UPDATE camicia_duel_stats SET boinc_user_id = %s WHERE discord_id = %s",
                        (boinc_uid, user.id),
                    )
        except Exception as e:
            logger.debug("Could not sync boinc_user_id in camicia_duel_stats for %s: %s", user.id, e)

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
        icon_url=get_bot_avatar_url(),
    )
    logger.info("User %s linked to BOINC account #%s (%s)", user, boinc_uid, volunteer_name)
    return True, embed


async def execute_unlink_flow(
    user: Union[discord.User, discord.Member]
) -> Tuple[bool, Union[discord.Embed, str]]:
    success, msg, boinc_uid, volunteer_name = await unlink_discord_user(user.id)
    if not success:
        return False, msg

    # Remove Volunteer role if present
    member = await get_guild_member(user.id)
    if member and member.guild:
        volunteer_role = await get_volunteer_role(member.guild)
        if volunteer_role and volunteer_role in member.roles:
            try:
                await member.remove_roles(volunteer_role, reason="Unlinked Camicia BOINC account")
            except Exception as e:
                logger.warning("Could not remove Volunteer role from %s: %s", member, e)

    # Clear boinc_user_id in camicia_duel_stats if exists
    pool = await get_db_pool()
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "UPDATE camicia_duel_stats SET boinc_user_id = NULL WHERE discord_id = %s",
                        (user.id,),
                    )
        except Exception as e:
            logger.debug("Could not clear boinc_user_id in camicia_duel_stats for %s: %s", user.id, e)

    link_url = getattr(config, "PROJECT_LINK_URL", f"https://{config.PROJECT_DOMAIN}/camicia/discord_link.php")
    embed = discord.Embed(
        title="✅ Account Unlinked",
        description=f"Your Discord account has been disconnected from BOINC volunteer **{volunteer_name}**.",
        color=0x95A5A6,
    )
    embed.add_field(name="Status", value="Volunteer role removed • /lucky rolls reset to 3/day", inline=False)
    embed.add_field(
        name="⏳ Cooldown Notice",
        value="To prevent abuse, there is a **1-hour cooldown** before you or this BOINC account can be linked again.",
        inline=False,
    )
    embed.set_footer(
        text=f"You can re-link after 1 hour at {link_url}",
        icon_url=get_bot_avatar_url(),
    )
    logger.info("User %s unlinked from BOINC account #%s (%s)", user, boinc_uid, volunteer_name)
    return True, embed


@bot.tree.command(name="link", description="Link your Discord account to your Camicia BOINC volunteer account (DM only)")
@app_commands.describe(code="The 6-digit verification code sent to your registered BOINC email")
@app_commands.checks.cooldown(1, LINK_SPAM_COOLDOWN_SECONDS, key=lambda i: i.user.id)
@is_dm_only()
async def link_cmd(interaction: discord.Interaction, code: Optional[str] = None):
    """Links Discord account to BOINC profile using email verification code, or shows instructions."""
    is_muted, rem = check_user_muted(interaction.user.id)
    if is_muted:
        await interaction.response.send_message(embed=get_spam_muted_embed(rem), ephemeral=True)
        return

    if code is None or not code.strip():
        is_linked, b_uid, v_name = await is_discord_user_linked(interaction.user.id)
        if is_linked:
            embed = get_already_linked_embed(b_uid, v_name)
            await interaction.response.send_message(embed=embed)
        else:
            cooldown_rem = await get_unlink_cooldown_remaining(interaction.user.id)
            if cooldown_rem > 0:
                strike_cnt, is_muted_after, rem_mute = record_spam_strike(interaction.user.id)
                if is_muted_after:
                    await interaction.response.send_message(embed=get_spam_muted_embed(rem_mute), ephemeral=True)
                else:
                    embed = get_unlink_cooldown_embed(cooldown_rem, account_type="Discord", strike_count=strike_cnt)
                    await interaction.response.send_message(embed=embed, view=LinkHelpView())
            else:
                embed = get_link_instructions_embed()
                await interaction.response.send_message(embed=embed, view=LinkHelpView())
        return

    await interaction.response.defer()
    success, result = await execute_link_flow(interaction.user, code)
    if isinstance(result, discord.Embed):
        view = LinkHelpView() if not success and "Cooldown" in (result.title or "") else None
        if view:
            await interaction.followup.send(embed=result, view=view)
        else:
            await interaction.followup.send(embed=result)
    else:
        await interaction.followup.send(result)


@bot.tree.command(name="unlink", description="Unlink your Discord account from your Camicia BOINC account (DM only)")
@app_commands.checks.cooldown(1, UNLINK_SPAM_COOLDOWN_SECONDS, key=lambda i: i.user.id)
@is_dm_only()
async def unlink_cmd(interaction: discord.Interaction):
    """Unlinks Discord account from BOINC profile."""
    is_muted, rem = check_user_muted(interaction.user.id)
    if is_muted:
        await interaction.response.send_message(embed=get_spam_muted_embed(rem), ephemeral=True)
        return

    await interaction.response.defer()
    success, result = await execute_unlink_flow(interaction.user)
    if isinstance(result, discord.Embed):
        await interaction.followup.send(embed=result)
    else:
        await interaction.followup.send(result)


@bot.event
async def on_message(message: discord.Message):
    """Handles commands in Direct Messages (DMs)."""
    if message.author.bot:
        return

    # Direct Message interaction
    if message.guild is None:
        if config.STAGING_MODE:
            member = await get_guild_member(message.author.id)
            if not is_tester_or_admin(member):
                return

        is_muted, rem = check_user_muted(message.author.id)
        if is_muted:
            # Completely ignore messages from muted users during 15-minute ignore
            return

        raw_content = message.content.strip()
        parts = raw_content.split(maxsplit=1)
        if not parts:
            return

        cmd = parts[0].lower()

        # Handle unlinking in DM: /unlink, !unlink, unlink
        if cmd in ("/unlink", "!unlink", "unlink"):
            is_cd, rem = check_dm_cooldown(message.author.id, "unlink", UNLINK_SPAM_COOLDOWN_SECONDS)
            if is_cd:
                strike_cnt, is_muted_now, rem_mute = record_spam_strike(message.author.id)
                if is_muted_now:
                    await message.channel.send(embed=get_spam_muted_embed(rem_mute))
                else:
                    await message.channel.send(
                        f"⏳ **Slow down!** Please wait **{rem:.1f}s** before requesting account disconnection again.\n"
                        f"*(Strike {strike_cnt}/5: Continued spam will cause the bot to ignore your messages for 15 minutes)*"
                    )
                return

            async with message.channel.typing():
                success, result = await execute_unlink_flow(message.author)
                if isinstance(result, discord.Embed):
                    await message.channel.send(embed=result)
                else:
                    await message.channel.send(result)
            return

        # Handle link in DM: /link, !link, link
        if cmd in ("/link", "!link", "link"):
            is_cd, rem = check_dm_cooldown(message.author.id, "link", LINK_SPAM_COOLDOWN_SECONDS)
            if is_cd:
                strike_cnt, is_muted_now, rem_mute = record_spam_strike(message.author.id)
                if is_muted_now:
                    await message.channel.send(embed=get_spam_muted_embed(rem_mute))
                else:
                    await message.channel.send(
                        f"⏳ **Slow down!** Please wait **{rem:.1f}s** before submitting another verification attempt.\n"
                        f"*(Strike {strike_cnt}/5: Continued spam will cause the bot to ignore your messages for 15 minutes)*"
                    )
                return

            if len(parts) > 1:
                code_arg = parts[1].strip()
                async with message.channel.typing():
                    success, result = await execute_link_flow(message.author, code_arg)
                    if isinstance(result, discord.Embed):
                        view = LinkHelpView() if not success and "Cooldown" in (result.title or "") else None
                        if view:
                            await message.channel.send(embed=result, view=view)
                        else:
                            await message.channel.send(embed=result)
                    else:
                        await message.channel.send(result)
                return
            else:
                # User typed just /link or link without a code
                is_linked, b_uid, v_name = await is_discord_user_linked(message.author.id)
                if is_linked:
                    await message.channel.send(embed=get_already_linked_embed(b_uid, v_name))
                else:
                    cooldown_rem = await get_unlink_cooldown_remaining(message.author.id)
                    if cooldown_rem > 0:
                        strike_cnt, is_muted_now, rem_mute = record_spam_strike(message.author.id)
                        if is_muted_now:
                            await message.channel.send(embed=get_spam_muted_embed(rem_mute))
                        else:
                            embed = get_unlink_cooldown_embed(cooldown_rem, account_type="Discord", strike_count=strike_cnt)
                            await message.channel.send(embed=embed, view=LinkHelpView())
                    else:
                        await message.channel.send(embed=get_link_instructions_embed(), view=LinkHelpView())
        # IMPORTANT: If user writes anything not equal to /link, /unlink, or /link <code>,
        # the bot does NOT respond anything.
        return

    await bot.process_commands(message)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Handles cooldowns and channel permission checks gracefully with clear ephemeral messages."""
    if isinstance(error, app_commands.CommandOnCooldown):
        cmd_name = interaction.command.name if interaction.command else ""
        retry_seconds = max(1, int(round(error.retry_after)))
        if cmd_name in ("link", "unlink"):
            strike_cnt, is_muted, rem_mute = record_spam_strike(interaction.user.id)
            if is_muted:
                embed = get_spam_muted_embed(rem_mute)
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            action_desc = "submitting another verification attempt" if cmd_name == "link" else "requesting account disconnection again"
            msg = (
                f"⏳ **Slow down!** Please wait **{error.retry_after:.1f}s** before {action_desc}.\n"
                f"*(Strike {strike_cnt}/5: Continued spam will cause the bot to ignore your messages for 15 minutes)*"
            )
            await interaction.response.send_message(msg, ephemeral=True)
            return
        elif cmd_name == "records":
            msg = (
                f"⏳ **Recent Request**: The records were just posted in this channel! "
                f"To keep the channel clean, please check the message above or try again in **{retry_seconds}s**."
            )
        elif cmd_name in ("lucky-leaderboard", "luckyleaderboard", "duel-leaderboard"):
            msg = (
                f"⏳ **Recent Request**: The leaderboard was just posted in this channel! "
                f"To keep the channel clean, please check the message above or try again in **{retry_seconds}s**."
            )
        elif cmd_name == "lucky":
            msg = f"⏳ **Slow down!** You can roll again in **{error.retry_after:.1f}s**."
        else:
            msg = f"⏳ **Cooldown Active**: Please wait **{retry_seconds}s** before using `/{cmd_name}` again."

        await interaction.response.send_message(msg, ephemeral=True)
    elif isinstance(error, app_commands.CheckFailure):
        if interaction.response.is_done():
            await interaction.followup.send(str(error), ephemeral=True)
        else:
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
        icon_provider=get_bot_avatar_url,
    )

    async with bot:
        check_records_loop.start()
        unlink_queue_loop.start()

        # Custom setup hook for syncing slash commands on connection
        async def on_tree_sync():
            await bot.wait_until_ready()
            try:
                # Clear any lingering guild-scoped commands to eliminate duplicate slash command listings
                if config.DISCORD_GUILD_ID:
                    guild_obj = discord.Object(id=config.DISCORD_GUILD_ID)
                    bot.tree.clear_commands(guild=guild_obj)
                    await bot.tree.sync(guild=guild_obj)
                    logger.info("Cleared guild-level slash commands for Guild ID %d to eliminate duplicates", config.DISCORD_GUILD_ID)

                # Sync global slash commands
                synced_global = await bot.tree.sync()
                logger.info("Synced %d global slash command(s)", len(synced_global))
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
