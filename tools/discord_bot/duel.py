import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import discord

import config
import engine

logger = logging.getLogger("camicia.duel")

# Cut positions for 26-card half-decks
CUT_POSITIONS = {
    "top": 6,      # Top 1/4
    "mid": 13,     # Middle
    "bot": 20,     # Bottom 1/4
    "keep": 0,     # Keep deck as-is
}


def apply_cut(half_deck: str, offset: int) -> str:
    """Cyclic shift of a 26-card half-deck to simulate cutting the deck."""
    if offset <= 0 or offset >= len(half_deck):
        return half_deck
    return half_deck[offset:] + half_deck[:offset]


def calculate_elo(rating_a: int, rating_b: int, score_a: float, k: int = 32) -> Tuple[int, int, int, int]:
    """
    Standard Elo rating calculation with floor at 100.
    Returns: (new_rating_a, new_rating_b, delta_a, delta_b)
    """
    # Expected scores
    exp_a = 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))
    
    # Delta for Player A
    raw_delta_a = k * (score_a - exp_a)
    delta_a = int(round(raw_delta_a))
    delta_b = -delta_a

    new_rating_a = max(100, rating_a + delta_a)
    new_rating_b = max(100, rating_b + delta_b)
    actual_delta_a = new_rating_a - rating_a
    actual_delta_b = new_rating_b - rating_b

    return new_rating_a, new_rating_b, actual_delta_a, actual_delta_b


def count_hand_cards(half_deck: str) -> Dict[str, int]:
    """Counts face cards and blanks in a 26-card half deck."""
    return {
        'A': half_deck.count('A'),
        'K': half_deck.count('K'),
        'Q': half_deck.count('Q'),
        'J': half_deck.count('J'),
        '-': half_deck.count('-'),
    }


def format_hand_summary(half_deck: str) -> str:
    """Formats starting hand for Discord display with card emojis."""
    c = count_hand_cards(half_deck)
    faces = c['A'] + c['K'] + c['Q'] + c['J']
    return (
        f"{c['A']}A 🂡, {c['K']}K 🂮, {c['Q']}Q 🂭, {c['J']}J 🂫 "
        f"({faces} face cards, {c['-']} blanks)"
    )


class DuelManager:
    """Manages active duel state, concurrency locks, quotas, and match execution."""

    def __init__(self, db_pool_getter, standing_record_getter=None):
        self.get_db_pool = db_pool_getter
        self.get_standing_record = standing_record_getter
        self._active_duels: Set[int] = set()
        self._pending_challenges: Dict[int, int] = {}  # opponent_id -> challenger_id
        self._cheers: Dict[int, Dict[int, int]] = {}  # message_id -> {spectator_id: player_id}

    def is_user_busy(self, discord_id: int) -> bool:
        return discord_id in self._active_duels

    def lock_users(self, user_a: int, user_b: int):
        self._active_duels.add(user_a)
        self._active_duels.add(user_b)

    def release_users(self, user_a: int, user_b: int):
        self._active_duels.discard(user_a)
        self._active_duels.discard(user_b)

    def has_pending_challenge_target(self, opponent_id: int) -> bool:
        return opponent_id in self._pending_challenges

    def has_pending_challenge_source(self, challenger_id: int) -> bool:
        return challenger_id in self._pending_challenges.values()

    def set_pending_challenge(self, opponent_id: int, challenger_id: int):
        self._pending_challenges[opponent_id] = challenger_id

    def clear_pending_challenge(self, opponent_id: int):
        self._pending_challenges.pop(opponent_id, None)

    def record_cheer(self, message_id: int, spectator_id: int, player_id: int) -> Tuple[int, int]:
        """Records a spectator cheer and returns (cheers_p1, cheers_p2)."""
        if message_id not in self._cheers:
            self._cheers[message_id] = {}
        self._cheers[message_id][spectator_id] = player_id
        return self.get_cheer_counts(message_id, player_id)

    def get_cheer_counts(self, message_id: int, p1_id: int, p2_id: Optional[int] = None) -> Tuple[int, int]:
        """Returns cheer counts (count_p1, count_p2)."""
        match_cheers = self._cheers.get(message_id, {})
        p1_count = sum(1 for v in match_cheers.values() if v == p1_id)
        if p2_id is not None:
            p2_count = sum(1 for v in match_cheers.values() if v == p2_id)
        else:
            p2_count = len(match_cheers) - p1_count
        return p1_count, p2_count

    def clear_cheers(self, message_id: int):
        self._cheers.pop(message_id, None)

    async def get_duel_stats(self, discord_id: int) -> Dict[str, Any]:
        """Fetches or creates a user's duel stats row."""
        pool = await self.get_db_pool()
        if pool is None:
            return {
                "discord_id": discord_id,
                "elo_rating": 1000,
                "wins": 0,
                "losses": 0,
                "ties": 0,
                "daily_casual_count": 0,
                "daily_ranked_count": 0,
                "direct_challenges_enabled": True,
            }

        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT discord_id, boinc_user_id, elo_rating, wins, losses, ties, "
                    "daily_casual_count, daily_ranked_count, last_played_date, direct_challenges_enabled "
                    "FROM camicia_duel_stats WHERE discord_id = %s",
                    (discord_id,),
                )
                row = await cur.fetchone()
                now_date = datetime.now(timezone.utc).date()

                if row:
                    last_date = row[8]
                    daily_casual = row[6]
                    daily_ranked = row[7]
                    # UTC day reset
                    if last_date != now_date:
                        daily_casual = 0
                        daily_ranked = 0
                        await cur.execute(
                            "UPDATE camicia_duel_stats SET daily_casual_count = 0, daily_ranked_count = 0, "
                            "last_played_date = %s WHERE discord_id = %s",
                            (now_date, discord_id),
                        )
                    return {
                        "discord_id": row[0],
                        "boinc_user_id": row[1],
                        "elo_rating": row[2],
                        "wins": row[3],
                        "losses": row[4],
                        "ties": row[5],
                        "daily_casual_count": daily_casual,
                        "daily_ranked_count": daily_ranked,
                        "direct_challenges_enabled": bool(row[9]),
                    }
                else:
                    await cur.execute(
                        "INSERT INTO camicia_duel_stats (discord_id, boinc_user_id, elo_rating, wins, losses, ties, "
                        "daily_casual_count, daily_ranked_count, last_played_date, direct_challenges_enabled) "
                        "VALUES (%s, (SELECT boinc_user_id FROM camicia_discord_links WHERE discord_id = %s AND linked_at IS NOT NULL AND unlinked_at IS NULL LIMIT 1), 1000, 0, 0, 0, 0, 0, %s, TRUE) "
                        "ON DUPLICATE KEY UPDATE discord_id = discord_id",
                        (discord_id, discord_id, now_date),
                    )
                    await cur.execute(
                        "SELECT discord_id, boinc_user_id, elo_rating, wins, losses, ties, "
                        "daily_casual_count, daily_ranked_count, last_played_date, direct_challenges_enabled "
                        "FROM camicia_duel_stats WHERE discord_id = %s",
                        (discord_id,),
                    )
                    row = await cur.fetchone()
                    if not row:
                        row = (discord_id, None, 1000, 0, 0, 0, 0, 0, now_date, True)

                    return {
                        "discord_id": row[0],
                        "boinc_user_id": row[1],
                        "elo_rating": row[2],
                        "wins": row[3],
                        "losses": row[4],
                        "ties": row[5],
                        "daily_casual_count": 0,
                        "daily_ranked_count": 0,
                        "direct_challenges_enabled": bool(row[9]),
                    }

    async def can_play_casual(self, discord_id: int, is_volunteer: bool) -> Tuple[bool, str]:
        """Checks if a user can play a casual match based on their quota."""
        if is_volunteer:
            return True, ""
        stats = await self.get_duel_stats(discord_id)
        if stats["daily_casual_count"] >= config.MAX_GUEST_DAILY_CASUAL_DUELS:
            return (
                False,
                f"You have reached your daily guest limit of {config.MAX_GUEST_DAILY_CASUAL_DUELS} casual games today. "
                "Link your BOINC account to unlock **unlimited** casual games!",
            )
        return True, ""

    async def can_play_ranked(self, p1_id: int, p2_id: int) -> Tuple[bool, str]:
        """Checks daily ranked tickets and anti-win-trading limit between p1 and p2."""
        p1_stats = await self.get_duel_stats(p1_id)
        p2_stats = await self.get_duel_stats(p2_id)

        if p1_stats["daily_ranked_count"] >= config.MAX_VOLUNTEER_DAILY_RANKED_DUELS:
            return False, f"<@{p1_id}> has used all {config.MAX_VOLUNTEER_DAILY_RANKED_DUELS} ranked tickets for today."
        if p2_stats["daily_ranked_count"] >= config.MAX_VOLUNTEER_DAILY_RANKED_DUELS:
            return False, f"<@{p2_id}> has used all {config.MAX_VOLUNTEER_DAILY_RANKED_DUELS} ranked tickets for today."

        # Anti-win-trading check
        pool = await self.get_db_pool()
        if pool is not None:
            now_date = datetime.now(timezone.utc).date()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT COUNT(*) FROM camicia_duel_matches "
                        "WHERE mode = 'ranked' AND played_date = %s AND ("
                        "(player1_id = %s AND player2_id = %s) OR (player1_id = %s AND player2_id = %s))",
                        (now_date, p1_id, p2_id, p2_id, p1_id),
                    )
                    count_row = await cur.fetchone()
                    match_count = count_row[0] if count_row else 0
                    if match_count >= config.MAX_DAILY_RANKED_VERSUS_SAME_OPPONENT:
                        return (
                            False,
                            f"You have already played {config.MAX_DAILY_RANKED_VERSUS_SAME_OPPONENT} ranked games together today. "
                            "You can still battle in **Casual** mode!",
                        )

        return True, ""

    async def update_direct_challenges_setting(self, discord_id: int, enabled: bool):
        """Updates user's direct challenges preference."""
        pool = await self.get_db_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO camicia_duel_stats (discord_id, direct_challenges_enabled) "
                    "VALUES (%s, %s) ON DUPLICATE KEY UPDATE direct_challenges_enabled = %s",
                    (discord_id, enabled, enabled),
                )

    async def record_match(
        self,
        mode: str,
        p1_id: int,
        p2_id: int,
        winner_id: Optional[int],
        cards_played: int,
        tricks: int,
        series_score: str,
        status: str,
    ) -> Optional[int]:
        """Records completed match and updates duel stats."""
        pool = await self.get_db_pool()
        if pool is None:
            return None

        now_utc = datetime.now(timezone.utc)
        now_date = now_utc.date()

        # Ensure both players have stats rows initialized before updating
        await self.get_duel_stats(p1_id)
        await self.get_duel_stats(p2_id)

        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 1. Insert match
                await cur.execute(
                    "INSERT INTO camicia_duel_matches "
                    "(mode, player1_id, player2_id, winner_id, cards_played, tricks, series_score, status, played_date, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (mode, p1_id, p2_id, winner_id, cards_played, tricks, series_score, status, now_date, now_utc),
                )
                match_id = cur.lastrowid

                # 2. Update quotas
                col = "daily_ranked_count" if mode == "ranked" else "daily_casual_count"
                await cur.execute(
                    f"UPDATE camicia_duel_stats SET {col} = {col} + 1, last_played_date = %s WHERE discord_id IN (%s, %s)",
                    (now_date, p1_id, p2_id),
                )

                # 3. Update win/loss/tie stats
                if winner_id is not None:
                    loser_id = p2_id if winner_id == p1_id else p1_id
                    await cur.execute(
                        "UPDATE camicia_duel_stats SET wins = wins + 1 WHERE discord_id = %s",
                        (winner_id,),
                    )
                    await cur.execute(
                        "UPDATE camicia_duel_stats SET losses = losses + 1 WHERE discord_id = %s",
                        (loser_id,),
                    )
                else:
                    await cur.execute(
                        "UPDATE camicia_duel_stats SET ties = ties + 1 WHERE discord_id IN (%s, %s)",
                        (p1_id, p2_id),
                    )

                return match_id

    async def update_elo_ratings(self, p1_id: int, p2_id: int, new_r1: int, new_r2: int):
        """Updates Elo ratings in MariaDB."""
        pool = await self.get_db_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE camicia_duel_stats SET elo_rating = %s WHERE discord_id = %s",
                    (new_r1, p1_id),
                )
                await cur.execute(
                    "UPDATE camicia_duel_stats SET elo_rating = %s WHERE discord_id = %s",
                    (new_r2, p2_id),
                )

    def simulate_game(self, deal_index: int, cut_a: int, cut_b: int, p1_starts: bool = True) -> Dict[str, Any]:
        """Simulates one Camicia game with deck cuts and starting turn."""
        deck_str = engine.get_nth_permutation(deal_index)
        half_a = apply_cut(deck_str[:26], cut_a)
        half_b = apply_cut(deck_str[26:], cut_b)

        if p1_starts:
            full_deck = half_a + half_b
        else:
            full_deck = half_b + half_a

        sim_res = engine.simulate(full_deck)
        sim_winner = sim_res["winner"]
        
        # Translate engine winner back to p1 / p2
        if sim_winner == 0:
            winner = 0  # Loop / Draw
        elif p1_starts:
            winner = 1 if sim_winner == 1 else 2
        else:
            winner = 2 if sim_winner == 1 else 1

        is_loop = (sim_res["status"] == "loop")
        tier, color, rarity_desc = engine.get_rarity(sim_res["cards"], is_loop)

        # Check World Record
        standing_rec = config.REAL_WORLD_RECORD_CARDS
        if self.get_standing_record:
            try:
                standing_rec = max(standing_rec, self.get_standing_record())
            except Exception:
                pass
        
        is_record = sim_res["cards"] > standing_rec
        status_val = "record" if is_record else ("completed" if sim_res["status"] == "finished" else sim_res["status"])

        return {
            "status": status_val,
            "cards": sim_res["cards"],
            "tricks": sim_res["tricks"],
            "winner": winner,
            "hand_a": half_a,
            "hand_b": half_b,
            "tier": tier,
            "color": color,
            "rarity_desc": rarity_desc,
            "is_record": is_record,
            "is_loop": is_loop,
            "deal_index": deal_index,
        }

    def simulate_best_of_3(self, cut_a: int, cut_b: int) -> Dict[str, Any]:
        """Simulates a Ranked Best-of-3 series with swapped turns and tiebreaker."""
        idx_1 = engine.get_random_deal_index()
        idx_2 = engine.get_random_deal_index()

        # Game 1: P1 starts
        g1 = self.simulate_game(idx_1, cut_a, cut_b, p1_starts=True)
        # Game 2: P2 starts
        g2 = self.simulate_game(idx_2, cut_a, cut_b, p1_starts=False)

        wins_p1 = (1 if g1["winner"] == 1 else 0) + (1 if g2["winner"] == 1 else 0)
        wins_p2 = (1 if g1["winner"] == 2 else 0) + (1 if g2["winner"] == 2 else 0)

        g3 = None
        series_winner = 0
        t_a = None
        t_b = None

        if wins_p1 > wins_p2:
            series_winner = 1
        elif wins_p2 > wins_p1:
            series_winner = 2
        else:
            # 1-1 Tiebreaker: Endurance showdown (each gets independent deal)
            idx_3a = engine.get_random_deal_index()
            idx_3b = engine.get_random_deal_index()
            t_a = self.simulate_game(idx_3a, cut_a, 0, p1_starts=True)
            t_b = self.simulate_game(idx_3b, cut_b, 0, p1_starts=True)

            if t_a["is_loop"] and not t_b["is_loop"]:
                series_winner = 1
            elif t_b["is_loop"] and not t_a["is_loop"]:
                series_winner = 2
            elif t_a["cards"] > t_b["cards"]:
                series_winner = 1
            elif t_b["cards"] > t_a["cards"]:
                series_winner = 2
            elif t_a["tricks"] > t_b["tricks"]:
                series_winner = 1
            elif t_b["tricks"] > t_a["tricks"]:
                series_winner = 2
            else:
                series_winner = random.choice([1, 2])

            g3 = {
                "p1_cards": t_a["cards"],
                "p2_cards": t_b["cards"],
                "p1_tricks": t_a["tricks"],
                "p2_tricks": t_b["tricks"],
                "winner": series_winner,
                "is_record": t_a["is_record"] or t_b["is_record"],
                "is_loop": t_a["is_loop"] or t_b["is_loop"],
            }

        total_cards = g1["cards"] + g2["cards"] + (g3["p1_cards"] + g3["p2_cards"] if g3 else 0)
        total_tricks = g1["tricks"] + g2["tricks"] + (g3["p1_tricks"] + g3["p2_tricks"] if g3 else 0)

        is_any_record = g1["is_record"] or g2["is_record"] or (g3["is_record"] if g3 else False)
        is_any_loop = g1["is_loop"] or g2["is_loop"] or (g3["is_loop"] if g3 else False)

        series_score = f"{wins_p1 + (1 if series_winner == 1 and g3 else 0)} - {wins_p2 + (1 if series_winner == 2 and g3 else 0)}"

        all_games = [g1, g2]
        if t_a is not None and t_b is not None:
            all_games.extend([t_a, t_b])

        record_game = max(all_games, key=lambda g: g["cards"]) if is_any_record else None
        loop_game = next((g for g in all_games if g["is_loop"]), None) if is_any_loop else None

        return {
            "g1": g1,
            "g2": g2,
            "g3": g3,
            "series_winner": series_winner,
            "series_score": series_score,
            "total_cards": total_cards,
            "total_tricks": total_tricks,
            "is_record": is_any_record,
            "is_loop": is_any_loop,
            "record_game": record_game,
            "loop_game": loop_game,
        }


# ==========================================
# Discord UI Views
# ==========================================

class DuelChallengeView(discord.ui.View):
    """View with Accept / Decline / Cancel buttons for challenge invitations."""

    def __init__(
        self,
        duel_mgr: DuelManager,
        challenger: discord.Member,
        opponent: Optional[discord.Member],
        mode: str,
        timeout: float,
        on_accept_callback,
        can_accept_callback=None,
    ):
        super().__init__(timeout=timeout)
        self.duel_mgr = duel_mgr
        self.challenger = challenger
        self.opponent = opponent
        self.mode = mode
        self.on_accept_callback = on_accept_callback
        self.can_accept_callback = can_accept_callback
        self.message: Optional[discord.Message] = None
        self._accepted = False

        # Open tavern challenges do not have a specific opponent, so remove the Decline button
        if self.opponent is None:
            self.remove_item(self.decline_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.is_finished() or self._accepted:
            await interaction.response.send_message("This duel challenge has already been concluded.", ephemeral=True)
            return False

        custom_id = interaction.data.get("custom_id")
        # For direct challenge: only opponent can accept/decline; only challenger can cancel
        if self.opponent is not None:
            if custom_id == "cancel":
                if interaction.user.id != self.challenger.id:
                    await interaction.response.send_message("Only the challenger can cancel this duel.", ephemeral=True)
                    return False
                return True
            else:
                if interaction.user.id != self.opponent.id:
                    await interaction.response.send_message(
                        f"This challenge was issued specifically to <@{self.opponent.id}>.",
                        ephemeral=True,
                    )
                    return False
        else:
            # Open pool: challenger can cancel; anyone else can accept
            if custom_id == "cancel":
                if interaction.user.id != self.challenger.id:
                    await interaction.response.send_message("Only the host can cancel this open challenge.", ephemeral=True)
                    return False
                return True
            else:
                if interaction.user.id == self.challenger.id:
                    await interaction.response.send_message("You cannot accept your own open challenge.", ephemeral=True)
                    return False

        if custom_id == "accept":
            if self.opponent is None:
                if self.duel_mgr.is_user_busy(interaction.user.id):
                    await interaction.response.send_message("You are currently in another active duel.", ephemeral=True)
                    return False
                if self.can_accept_callback is not None:
                    can_accept, reason = await self.can_accept_callback(interaction.user)
                    if not can_accept:
                        await interaction.response.send_message(reason, ephemeral=True)
                        return False

        return True

    @discord.ui.button(label="Accept Duel", emoji="⚔️", style=discord.ButtonStyle.success, custom_id="accept")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._accepted:
            await interaction.response.send_message("This challenge has already been accepted!", ephemeral=True)
            return
        self._accepted = True
        for item in self.children:
            item.disabled = True
        self.stop()
        accepter = interaction.user
        await self.on_accept_callback(interaction, self.challenger, accepter, self.mode)

    @discord.ui.button(label="Decline", emoji="🏳️", style=discord.ButtonStyle.secondary, custom_id="decline")
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._accepted:
            return
        for item in self.children:
            item.disabled = True
        self.stop()
        if self.opponent:
            self.duel_mgr.clear_pending_challenge(self.opponent.id)
        self.duel_mgr.release_users(self.challenger.id, self.opponent.id if self.opponent else self.challenger.id)

        embed = discord.Embed(
            title="🏳️ Duel Declined",
            description=f"<@{interaction.user.id}> declined the challenge from <@{self.challenger.id}>.",
            color=0x95A5A6,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    @discord.ui.button(label="Cancel", emoji="❌", style=discord.ButtonStyle.danger, custom_id="cancel")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        self.stop()
        if self.opponent:
            self.duel_mgr.clear_pending_challenge(self.opponent.id)
        self.duel_mgr.release_users(self.challenger.id, self.opponent.id if self.opponent else self.challenger.id)

        embed = discord.Embed(
            title="❌ Challenge Cancelled",
            description=f"<@{self.challenger.id}> cancelled the challenge.",
            color=0x95A5A6,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    async def on_timeout(self):
        if self._accepted:
            return
        for item in self.children:
            item.disabled = True
        if self.opponent:
            self.duel_mgr.clear_pending_challenge(self.opponent.id)
        self.duel_mgr.release_users(self.challenger.id, self.opponent.id if self.opponent else self.challenger.id)

        if self.message:
            try:
                embed = discord.Embed(
                    title="⏳ Challenge Expired",
                    description=(
                        f"Challenge between <@{self.challenger.id}> and "
                        f"{'<@' + str(self.opponent.id) + '>' if self.opponent else 'the open pool'} expired without response."
                    ),
                    color=0x95A5A6,
                )
                await self.message.edit(content=None, embed=embed, view=self)
            except Exception:
                pass


class DeckCutAndCheerView(discord.ui.View):
    """View with Deck Cut choices for duelists and Cheer buttons for spectators."""

    def __init__(
        self,
        duel_mgr: DuelManager,
        p1: discord.Member,
        p2: discord.Member,
        timeout: float,
        on_cuts_complete_callback,
        mode: str = "casual",
    ):
        super().__init__(timeout=timeout)
        self.duel_mgr = duel_mgr
        self.p1 = p1
        self.p2 = p2
        self.cuts: Dict[int, int] = {}
        self.on_cuts_complete_callback = on_cuts_complete_callback
        self.mode = mode
        self.message: Optional[discord.Message] = None
        self._completed = False
        self._cheer_cooldowns: Dict[int, float] = {}  # spectator_id -> timestamp

        # Set initial button labels with duelist names (truncated to stay well under Discord's 80-char limit)
        p1_name = self.p1.display_name[:25]
        p2_name = self.p2.display_name[:25]
        for child in self.children:
            if getattr(child, "custom_id", None) == "cheer_p1":
                child.label = f"Cheer {p1_name} (0)"
            elif getattr(child, "custom_id", None) == "cheer_p2":
                child.label = f"Cheer {p2_name} (0)"

    def update_cheer_labels(self):
        if not self.message:
            return
        c1, c2 = self.duel_mgr.get_cheer_counts(self.message.id, self.p1.id, self.p2.id)
        p1_name = self.p1.display_name[:25]
        p2_name = self.p2.display_name[:25]
        for child in self.children:
            if getattr(child, "custom_id", None) == "cheer_p1":
                child.label = f"Cheer {p1_name} ({c1})"
            elif getattr(child, "custom_id", None) == "cheer_p2":
                child.label = f"Cheer {p2_name} ({c2})"

    @discord.ui.button(label="Top 1/4 (Card 6)", emoji="✂️", style=discord.ButtonStyle.primary, row=0)
    async def cut_top(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_cut(interaction, CUT_POSITIONS["top"], "Top 1/4 (Card 6)")

    @discord.ui.button(label="Middle (Card 13)", emoji="✂️", style=discord.ButtonStyle.primary, row=0)
    async def cut_mid(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_cut(interaction, CUT_POSITIONS["mid"], "Middle (Card 13)")

    @discord.ui.button(label="Bottom 1/4 (Card 20)", emoji="✂️", style=discord.ButtonStyle.primary, row=0)
    async def cut_bot(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_cut(interaction, CUT_POSITIONS["bot"], "Bottom 1/4 (Card 20)")

    @discord.ui.button(label="Keep As-Is", emoji="🛡️", style=discord.ButtonStyle.secondary, row=0)
    async def cut_keep(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_cut(interaction, CUT_POSITIONS["keep"], "Keep As-Is")

    @discord.ui.button(label="Cheer P1 (0)", emoji="📣", style=discord.ButtonStyle.secondary, row=1, custom_id="cheer_p1")
    async def cheer_p1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_cheer(interaction, self.p1.id, self.p1.display_name)

    @discord.ui.button(label="Cheer P2 (0)", emoji="📣", style=discord.ButtonStyle.secondary, row=1, custom_id="cheer_p2")
    async def cheer_p2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_cheer(interaction, self.p2.id, self.p2.display_name)

    async def _handle_cheer(self, interaction: discord.Interaction, cheered_player_id: int, name: str):
        if self._completed:
            await interaction.response.send_message("Deck cut phase has ended; cheers are closed!", ephemeral=True)
            return
        if interaction.user.id in (self.p1.id, self.p2.id):
            await interaction.response.send_message("Duelists cannot participate in spectator cheering!", ephemeral=True)
            return

        now = time.monotonic()
        last_cheer = self._cheer_cooldowns.get(interaction.user.id, 0.0)
        if now - last_cheer < 1.0:
            await interaction.response.send_message("⏳ Please wait a moment before changing your cheer!", ephemeral=True)
            return
        self._cheer_cooldowns[interaction.user.id] = now

        if self.message:
            current_cheer = self.duel_mgr._cheers.get(self.message.id, {}).get(interaction.user.id)
            if current_cheer == cheered_player_id:
                await interaction.response.send_message(f"You are already cheering for {name}!", ephemeral=True)
                return
            self.duel_mgr.record_cheer(self.message.id, interaction.user.id, cheered_player_id)
            self.update_cheer_labels()
            try:
                await interaction.response.edit_message(view=self)
            except Exception:
                if not interaction.response.is_done():
                    try:
                        await interaction.response.send_message(f"Cheered for {name}!", ephemeral=True)
                    except Exception:
                        pass
        else:
            await interaction.response.send_message(f"Cheered for {name}!", ephemeral=True)

    async def _handle_cut(self, interaction: discord.Interaction, cut_val: int, label: str):
        if self._completed:
            await interaction.response.send_message("The deck cut phase has already concluded!", ephemeral=True)
            return
        user_id = interaction.user.id
        if user_id not in (self.p1.id, self.p2.id):
            await interaction.response.send_message("Only the duelists can cut their decks.", ephemeral=True)
            return

        if user_id in self.cuts:
            await interaction.response.send_message(f"You already selected your deck cut ({label})!", ephemeral=True)
            return

        self.cuts[user_id] = cut_val
        trigger_callback = False
        if len(self.cuts) == 2 and not self._completed:
            self._completed = True
            self.stop()
            trigger_callback = True

        await interaction.response.send_message(f"✅ Cut chosen: **{label}**! Waiting for opponent...", ephemeral=True)

        if trigger_callback:
            cut_a = self.cuts.get(self.p1.id, 0)
            cut_b = self.cuts.get(self.p2.id, 0)
            await self.on_cuts_complete_callback(self.message, self.p1, self.p2, cut_a, cut_b, self.mode)

    async def on_timeout(self):
        if self._completed:
            return
        self._completed = True
        self.stop()
        cut_a = self.cuts.get(self.p1.id, 0)
        cut_b = self.cuts.get(self.p2.id, 0)
        if self.message:
            await self.on_cuts_complete_callback(self.message, self.p1, self.p2, cut_a, cut_b, self.mode)
        else:
            self.duel_mgr.release_users(self.p1.id, self.p2.id)


class RematchView(discord.ui.View):
    """View with a 30-second Rematch button for Casual matches."""

    def __init__(self, duel_mgr: DuelManager, p1: discord.Member, p2: discord.Member, on_rematch_callback):
        super().__init__(timeout=config.DUEL_REMATCH_TIMEOUT_SECONDS)
        self.duel_mgr = duel_mgr
        self.p1 = p1
        self.p2 = p2
        self.on_rematch_callback = on_rematch_callback
        self.rematch_requester: Optional[int] = None
        self.message: Optional[discord.Message] = None
        self._accepted = False

    @discord.ui.button(label="Rematch", emoji="🔁", style=discord.ButtonStyle.primary)
    async def rematch_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._accepted:
            return
        user_id = interaction.user.id
        if user_id not in (self.p1.id, self.p2.id):
            await interaction.response.send_message("Only the duelists can request a rematch.", ephemeral=True)
            return

        if self.rematch_requester is None:
            self.rematch_requester = user_id
            other_id = self.p2.id if user_id == self.p1.id else self.p1.id
            button.label = "Accept Rematch"
            button.style = discord.ButtonStyle.success
            await interaction.response.edit_message(
                content=f"🔁 <@{user_id}> requested a rematch! <@{other_id}>, click to accept!",
                view=self,
            )
        else:
            if user_id == self.rematch_requester:
                await interaction.response.send_message("Waiting for your opponent to accept the rematch.", ephemeral=True)
                return
            if self._accepted:
                return
            self._accepted = True
            # Both accepted
            for item in self.children:
                item.disabled = True
            self.stop()
            await self.on_rematch_callback(interaction, self.p2, self.p1)  # Swap starting roles

    async def on_timeout(self):
        if self._accepted:
            return
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(content=None, view=self)
            except Exception:
                pass
