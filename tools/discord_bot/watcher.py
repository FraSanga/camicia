import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import discord

import config
from config import REAL_WORLD_RECORD_CARDS

logger = logging.getLogger("camicia.watcher")


class RecordsWatcher:
    """Monitors Camicia's output records and produces rich Discord embeds for new discoveries."""

    def __init__(
        self,
        project_dir: Path,
        state_file: Path,
        db_pool: Optional[Any] = None,
        icon_provider: Optional[Any] = None,
    ):
        self.project_dir = Path(project_dir)
        self.state_file = Path(state_file)
        self.db_pool = db_pool
        self.icon_provider = icon_provider

        self.longest_history_file = self.project_dir / "records_longest_history.txt"
        self.longest_file = self.project_dir / "records_longest.txt"
        self.loops_file = self.project_dir / "records_loops.txt"

        self.user_cache: Dict[int, str] = {}
        self.state: Dict[str, Any] = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        """Loads persistent line/byte cursors from the state file."""
        default_state = {
            "longest_history_line": 0,
            "loops_line": 0,
            "last_best_cards": 0,
            "initialized": False,
        }

        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return {**default_state, **data}
            except Exception as e:
                logger.warning("Could not read state file %s: %s. Using default state.", self.state_file, e)

        # First boot: initialize cursors to current EOF to avoid blasting old records
        state = dict(default_state)
        state["longest_history_line"] = self._count_lines(self.longest_history_file)
        state["loops_line"] = self._count_lines(self.loops_file)
        state["last_best_cards"] = self._get_current_longest_cards()
        state["initialized"] = True
        self._save_state(state)
        logger.info(
            "Initialized watcher state: loops_line=%d, longest_history_line=%d, best_cards=%d",
            state["loops_line"],
            state["longest_history_line"],
            state["last_best_cards"],
        )
        return state

    def _save_state(self, state: Optional[Dict[str, Any]] = None) -> None:
        """Saves current cursors atomically to the state file."""
        if state is None:
            state = self.state

        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_file.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            tmp_path.replace(self.state_file)
        except Exception as e:
            logger.error("Failed to save state to %s: %s", self.state_file, e)

    def _count_lines(self, path: Path) -> int:
        if not path.exists():
            return 0
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return sum(1 for _ in f)
        except Exception as e:
            logger.warning("Error counting lines in %s: %s", path, e)
            return 0

    def _get_current_longest_cards(self) -> int:
        """Reads the highest cards value from records_longest.txt if it exists."""
        if not self.longest_file.exists():
            return 0
        try:
            with open(self.longest_file, "r", encoding="utf-8") as f:
                first_line = f.readline().strip()
                if first_line:
                    parts = first_line.split()
                    if parts and parts[0].isdigit():
                        return int(parts[0])
        except Exception as e:
            logger.warning("Error reading %s: %s", self.longest_file, e)
        return 0

    async def resolve_username(self, userid: int) -> str:
        """Looks up BOINC volunteer username from cache or MariaDB, falling back safely."""
        if userid <= 0:
            return "Anonymous Volunteer"

        if userid in self.user_cache:
            return self.user_cache[userid]

        # Query MariaDB if a connection pool is provided
        if self.db_pool is not None:
            try:
                async with self.db_pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "SELECT u.name, l.discord_id "
                            "FROM user u "
                            "LEFT JOIN camicia_discord_links l ON l.boinc_user_id = u.id AND l.linked_at IS NOT NULL AND l.unlinked_at IS NULL "
                            "WHERE u.id = %s",
                            (userid,),
                        )
                        row = await cur.fetchone()
                        if row and row[0]:
                            if row[1]:
                                username = f"{row[0]} (<@{row[1]}>)"
                            else:
                                username = f"{row[0]} (ID: {userid})"
                            self.user_cache[userid] = username
                            return username
            except Exception as e:
                logger.debug("Database user lookup failed for id %d: %s", userid, e)

        fallback = f"Volunteer #{userid}"
        self.user_cache[userid] = fallback
        return fallback

    def _parse_longest_line(self, line: str) -> Optional[Dict[str, Any]]:
        """Parses a line formatted as: <cards> <tricks> <deal_index> <wu_name> <timestamp> [<userid> <hostid>]"""
        parts = line.strip().split()
        if len(parts) < 5:
            return None

        try:
            cards = int(parts[0])
            tricks = int(parts[1])
            deal_index = parts[2]
            wu_name = parts[3]
            timestamp = int(parts[4])
            userid = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0
            hostid = int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else 0

            return {
                "cards": cards,
                "tricks": tricks,
                "deal_index": deal_index,
                "wu_name": wu_name,
                "timestamp": timestamp,
                "userid": userid,
                "hostid": hostid,
            }
        except Exception as e:
            logger.error("Failed to parse record line '%s': %s", line, e)
            return None

    def _parse_loop_line(self, line: str) -> Optional[Dict[str, Any]]:
        """Parses a line formatted as: <deal_index> <wu_name> <timestamp> [<userid> <hostid>]"""
        parts = line.strip().split()
        if len(parts) < 3:
            return None

        try:
            deal_index = parts[0]
            wu_name = parts[1]
            timestamp = int(parts[2])
            userid = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            hostid = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0

            return {
                "deal_index": deal_index,
                "wu_name": wu_name,
                "timestamp": timestamp,
                "userid": userid,
                "hostid": hostid,
            }
        except Exception as e:
            logger.error("Failed to parse loop line '%s': %s", line, e)
            return None

    def _get_icon_url(self) -> Optional[str]:
        if callable(self.icon_provider):
            try:
                return self.icon_provider()
            except Exception:
                pass
        return None

    async def build_longest_embed(self, record: Dict[str, Any]) -> discord.Embed:
        """Constructs a Discord Embed for a new longest game record."""
        cards = record["cards"]
        tricks = record["tricks"]
        deal_index = record["deal_index"]
        wu_name = record["wu_name"]
        timestamp = record["timestamp"]
        userid = record["userid"]

        volunteer_str = await self.resolve_username(userid)
        is_world_record = cards > REAL_WORLD_RECORD_CARDS
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        time_tag = f"<t:{timestamp}:F> (<t:{timestamp}:R>)"

        if is_world_record:
            embed = discord.Embed(
                title="🚨 ALL-TIME WORLD RECORD BROKEN! 🏆",
                description=(
                    f"A new finite Beggar-My-Neighbour game exceeding the world record "
                    f"({REAL_WORLD_RECORD_CARDS:,} cards) has been discovered and verified!"
                ),
                color=0xFF0033,  # Vivid Crimson
                timestamp=dt,
            )
        else:
            embed = discord.Embed(
                title="⭐ New Project Record Discovered!",
                description=(
                    f"A new milestone game length has been recorded by Camicia's distributed search!"
                ),
                color=0xF1C40F,  # Warm Gold
                timestamp=dt,
            )

        embed.add_field(name="🃏 Cards Played", value=f"**{cards:,}** cards", inline=True)
        embed.add_field(name="🔄 Tricks", value=f"**{tricks:,}** tricks", inline=True)
        embed.add_field(name="👤 Volunteer", value=f"**{volunteer_str}**", inline=True)

        # Format deal index cleanly (truncate if excessively long)
        index_display = deal_index if len(deal_index) <= 28 else f"{deal_index[:25]}..."
        embed.add_field(name="🔢 Deal Index", value=f"`{index_display}`", inline=True)
        embed.add_field(name="⏱️ Discovered", value=time_tag, inline=True)

        embed.set_footer(
            text="Camicia BOINC Project • Beggar-My-Neighbour Search",
            icon_url=self._get_icon_url(),
        )
        return embed

    async def build_loop_embed(self, loop_record: Dict[str, Any]) -> discord.Embed:
        """Constructs a Discord Embed for an infinite loop discovery."""
        deal_index = loop_record["deal_index"]
        wu_name = loop_record["wu_name"]
        timestamp = loop_record["timestamp"]
        userid = loop_record["userid"]

        volunteer_str = await self.resolve_username(userid)
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        time_tag = f"<t:{timestamp}:F> (<t:{timestamp}:R>)"

        embed = discord.Embed(
            title="♾️ Infinite Loop Discovered!",
            description=(
                "A non-terminating Beggar-My-Neighbour deal has been discovered! "
                "This deal enters a repeating cycle and **loops forever**."
            ),
            color=0x9B59B6,  # Royal Purple
            timestamp=dt,
        )

        embed.add_field(name="👤 Discovered By", value=f"**{volunteer_str}**", inline=True)
        embed.add_field(name="🔢 Deal Index", value=f"`{deal_index}`", inline=True)
        embed.add_field(name="⏱️ Verified At", value=time_tag, inline=False)

        embed.set_footer(
            text="Camicia BOINC Project • Infinite Cycle Proof",
            icon_url=self._get_icon_url(),
        )
        return embed

    async def check_new_records(self) -> List[discord.Embed]:
        """Scans for new records in flat files and returns a list of Discord Embeds to post."""
        embeds: List[discord.Embed] = []

        # 1. Check records_longest_history.txt (preferred append-only history)
        if self.longest_history_file.exists():
            try:
                with open(self.longest_history_file, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()

                start_idx = self.state.get("longest_history_line", 0)
                if len(lines) > start_idx:
                    for line in lines[start_idx:]:
                        parsed = self._parse_longest_line(line)
                        if parsed:
                            current_best = self.state.get("last_best_cards", 0)
                            if parsed["cards"] > current_best:
                                embed = await self.build_longest_embed(parsed)
                                embeds.append(embed)
                                self.state["last_best_cards"] = parsed["cards"]
                            else:
                                logger.info(
                                    "Skipping historical deal (%d cards) because standing record is already %d cards",
                                    parsed["cards"],
                                    current_best,
                                )
                    self.state["longest_history_line"] = len(lines)
                    self._save_state()
            except Exception as e:
                logger.error("Error reading %s: %s", self.longest_history_file, e)

        # 1b. Fallback: if records_longest_history.txt doesn't exist, check records_longest.txt
        elif self.longest_file.exists():
            try:
                with open(self.longest_file, "r", encoding="utf-8") as f:
                    content = f.readline().strip()
                if content:
                    parsed = self._parse_longest_line(content)
                    if parsed and parsed["cards"] > self.state.get("last_best_cards", 0):
                        embed = await self.build_longest_embed(parsed)
                        embeds.append(embed)
                        self.state["last_best_cards"] = parsed["cards"]
                        self._save_state()
            except Exception as e:
                logger.error("Error reading %s: %s", self.longest_file, e)

        # 2. Check records_loops.txt (append-only)
        if self.loops_file.exists():
            try:
                with open(self.loops_file, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()

                start_idx = self.state.get("loops_line", 0)
                if len(lines) > start_idx:
                    for line in lines[start_idx:]:
                        parsed = self._parse_loop_line(line)
                        if parsed:
                            embed = await self.build_loop_embed(parsed)
                            embeds.append(embed)
                    self.state["loops_line"] = len(lines)
                    self._save_state()
            except Exception as e:
                logger.error("Error reading %s: %s", self.loops_file, e)

        return embeds
