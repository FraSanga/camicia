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


def is_dm_only():
    """Restricts command execution strictly to Direct Messages (DMs) to protect privacy."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return True
        raise app_commands.CheckFailure(
            "🔒 **Privacy Notice**: Linking and unlinking commands are only available in Direct Messages (DMs) with CamiciaBot to keep your account details secure. Please send me a private message!"
        )
    return app_commands.check(predicate)


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


@bot.tree.command(name="luckyleaderboard", description="View today's top /lucky rolls on the server")
@app_commands.guild_only()
@is_bot_commands_channel()
@app_commands.checks.cooldown(1, 15.0, key=lambda i: i.channel_id)
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
        elif cmd_name == "luckyleaderboard":
            msg = (
                f"⏳ **Recent Request**: Today's leaderboard was just posted in this channel! "
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
