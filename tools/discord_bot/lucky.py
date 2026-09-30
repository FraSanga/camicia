import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import discord

from engine import get_rarity

logger = logging.getLogger("camicia.lucky")

DEFAULT_DAILY_ATTEMPTS = 3
LINKED_DAILY_ATTEMPTS = 5


class LuckyManager:
    """Manages daily /lucky attempts, daily leaderboard, and role assignments."""

    def __init__(self, state_file: Path):
        self.state_file = Path(state_file)
        self.state: Dict[str, Any] = self._load_state()

    def _get_today_utc(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load_state(self) -> Dict[str, Any]:
        today = self._get_today_utc()
        default_state = {
            "date": today,
            "users": {},
            "leaderboard": [],
        }

        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("date") == today:
                        return {**default_state, **data}
                    else:
                        logger.info("New UTC day detected (%s != %s). Resetting daily lucky state.", data.get("date"), today)
                        return default_state
            except Exception as e:
                logger.warning("Could not read lucky state %s: %s. Using fresh state.", self.state_file, e)

        return default_state

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_file.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            tmp_path.replace(self.state_file)
        except Exception as e:
            logger.error("Failed to save lucky state to %s: %s", self.state_file, e)

    def _ensure_current_day(self) -> None:
        today = self._get_today_utc()
        if self.state.get("date") != today:
            logger.info("Resetting daily lucky leaderboard and attempts for UTC %s", today)
            self.state = {
                "date": today,
                "users": {},
                "leaderboard": [],
            }
            self._save_state()

    def get_max_attempts(self, user_id: int, is_linked: bool = False) -> int:
        """
        Returns max attempts for user today.
        Base is 3. Linked BOINC volunteers get 5 (+2 bonus rolls).
        """
        return LINKED_DAILY_ATTEMPTS if is_linked else DEFAULT_DAILY_ATTEMPTS

    def can_roll(self, user_id: int, is_linked: bool = False) -> Tuple[bool, int, int]:
        """Returns (can_roll, used_attempts, max_attempts)."""
        self._ensure_current_day()
        uid_str = str(user_id)
        user_info = self.state["users"].get(uid_str, {})
        used = user_info.get("attempts", 0)
        max_att = self.get_max_attempts(user_id, is_linked=is_linked)
        return (used < max_att, used, max_att)

    def record_roll(
        self,
        user_id: int,
        username: str,
        cards: int,
        tricks: int,
        deal_index: str,
        status: str,
        is_linked: bool = False,
    ) -> Tuple[int, int, Optional[int]]:
        """
        Records a roll for today.
        Returns: (used_attempts, max_attempts, leaderboard_rank_or_None).
        """
        self._ensure_current_day()
        uid_str = str(user_id)
        now_ts = int(datetime.now(timezone.utc).timestamp())

        user_info = self.state["users"].setdefault(uid_str, {
            "attempts": 0,
            "username": username,
            "best_cards": 0,
            "total_rolls": 0,
        })
        user_info["attempts"] += 1
        user_info["total_rolls"] += 1
        user_info["username"] = username

        if cards > user_info.get("best_cards", 0):
            user_info["best_cards"] = cards

        used = user_info["attempts"]
        max_att = self.get_max_attempts(user_id, is_linked=is_linked)

        # Update leaderboard: keep each user's highest roll of the day
        lb = self.state.setdefault("leaderboard", [])
        existing_idx = next((i for i, entry in enumerate(lb) if entry["user_id"] == user_id), None)

        if existing_idx is not None:
            if cards > lb[existing_idx]["cards"]:
                lb[existing_idx] = {
                    "user_id": user_id,
                    "username": username,
                    "cards": cards,
                    "tricks": tricks,
                    "deal_index": deal_index,
                    "timestamp": now_ts,
                    "status": status,
                }
        else:
            lb.append({
                "user_id": user_id,
                "username": username,
                "cards": cards,
                "tricks": tricks,
                "deal_index": deal_index,
                "timestamp": now_ts,
                "status": status,
            })

        # Sort leaderboard descending by cards played
        lb.sort(key=lambda x: x["cards"], reverse=True)
        # Keep top 10
        self.state["leaderboard"] = lb[:10]

        self._save_state()

        # Find user's current rank
        rank = None
        for i, entry in enumerate(self.state["leaderboard"]):
            if entry["user_id"] == user_id:
                rank = i + 1
                break

        return (used, max_att, rank)

    def get_leaderboard(self) -> List[Dict[str, Any]]:
        self._ensure_current_day()
        return list(self.state.get("leaderboard", []))

    def next_midnight_timestamp(self) -> int:
        now = datetime.now(timezone.utc)
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return int(midnight.timestamp())

    @staticmethod
    def format_deal_layout(deck_str: str) -> str:
        """Formats the 52-card deal into a clean 2-line standard representation."""
        p1 = deck_str[:26]
        p2 = deck_str[26:]

        p1_faces = sum(1 for c in p1 if c in "AKQJ")
        p2_faces = sum(1 for c in p2 if c in "AKQJ")

        p1_spaced = " ".join(p1)
        p2_spaced = " ".join(p2)

        return (
            f"```text\n"
            f"P1 ({p1_faces} face): {p1_spaced}\n"
            f"P2 ({p2_faces} face): {p2_spaced}\n"
            f"```"
        )

    async def check_and_award_roles(
        self,
        guild: Optional[discord.Guild],
        member: Optional[discord.Member],
        is_loop: bool,
        cards: int,
        standing_record_cards: int,
    ) -> List[str]:
        """Awards 'Loop Discoverer' or 'Record Holder' roles if conditions are met."""
        if guild is None or member is None:
            return []

        awarded: List[str] = []

        # 1. Loop Discoverer Role
        if is_loop:
            role = discord.utils.get(guild.roles, name="Loop Discoverer")
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason="Discovered an infinite loop via /lucky")
                    awarded.append(role.name)
                    logger.info("Awarded %s role to %s", role.name, member.display_name)
                except Exception as e:
                    logger.error("Failed to assign %s role: %s", role.name, e)

        # 2. Record Holder Role (if beating standing record or world record)
        if cards > standing_record_cards and cards > 0:
            role = discord.utils.get(guild.roles, name="Record Holder")
            if role:
                try:
                    # Remove from previous holders so it stays a single champion title
                    for prev_member in role.members:
                        if prev_member.id != member.id:
                            await prev_member.remove_roles(role, reason="Record beaten by new champion")

                    if role not in member.roles:
                        await member.add_roles(role, reason=f"New record champion ({cards:,} cards) via /lucky")
                        awarded.append(role.name)
                        logger.info("Awarded %s role to %s", role.name, member.display_name)
                except Exception as e:
                    logger.error("Failed to assign %s role: %s", role.name, e)

        return awarded
