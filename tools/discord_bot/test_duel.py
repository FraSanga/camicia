import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import duel
import engine


class TestDuelLogic(unittest.TestCase):

    def test_apply_cut(self):
        # 26-card sample deck
        half_deck = "AAAAKKKKQQQQJJJJ----------"
        self.assertEqual(len(half_deck), 26)

        # Cut at index 6
        cut_top = duel.apply_cut(half_deck, 6)
        self.assertEqual(len(cut_top), 26)
        self.assertEqual(cut_top, half_deck[6:] + half_deck[:6])
        # Ensure exact multiset of cards is preserved
        self.assertEqual(sorted(cut_top), sorted(half_deck))

        # Cut at index 13
        cut_mid = duel.apply_cut(half_deck, 13)
        self.assertEqual(len(cut_mid), 26)
        self.assertEqual(cut_mid, half_deck[13:] + half_deck[:13])
        self.assertEqual(sorted(cut_mid), sorted(half_deck))

        # Cut at index 20
        cut_bot = duel.apply_cut(half_deck, 20)
        self.assertEqual(len(cut_bot), 26)
        self.assertEqual(cut_bot, half_deck[20:] + half_deck[:20])
        self.assertEqual(sorted(cut_bot), sorted(half_deck))

        # Keep as-is (offset 0)
        cut_keep = duel.apply_cut(half_deck, 0)
        self.assertEqual(cut_keep, half_deck)

    def test_elo_calculation_examples(self):
        # Example 1: Equal Match (1000 vs 1000, A wins) -> +16, -16
        r1, r2, d1, d2 = duel.calculate_elo(1000, 1000, 1.0, k=32)
        self.assertEqual((d1, d2), (16, -16))
        self.assertEqual((r1, r2), (1016, 984))

        # Example 2: Moderate Underdog Wins (1100 vs 1200, A wins) -> +20, -20
        r1, r2, d1, d2 = duel.calculate_elo(1100, 1200, 1.0, k=32)
        self.assertEqual((d1, d2), (20, -20))
        self.assertEqual((r1, r2), (1120, 1180))

        # Example 3: Major Upset (1000 vs 1400, A wins) -> +29, -29
        r1, r2, d1, d2 = duel.calculate_elo(1000, 1400, 1.0, k=32)
        self.assertEqual((d1, d2), (29, -29))
        self.assertEqual((r1, r2), (1029, 1371))

        # Example 4: Favorite Wins (1000 vs 1400, B wins) -> -3, +3
        r1, r2, d1, d2 = duel.calculate_elo(1000, 1400, 0.0, k=32)
        self.assertEqual((d1, d2), (-3, 3))
        self.assertEqual((r1, r2), (997, 1403))

        # Example 5: Draw / Infinite Loop with Rating Gap (1150 vs 1350) -> +8, -8
        r1, r2, d1, d2 = duel.calculate_elo(1150, 1350, 0.5, k=32)
        self.assertEqual((d1, d2), (8, -8))
        self.assertEqual((r1, r2), (1158, 1342))

    def test_elo_floor(self):
        # Verify rating never drops below 100 (105 - 16 = 89 -> clamped to 100)
        r1, r2, d1, d2 = duel.calculate_elo(105, 105, 0.0, k=32)
        self.assertEqual(r1, 100)  # clamped to floor

    def test_count_hand_cards(self):
        half_deck = "AAAKKQJ-----------" + "--------"  # 3A, 2K, 1Q, 1J, 19 blanks = 26
        counts = duel.count_hand_cards(half_deck)
        self.assertEqual(counts['A'], 3)
        self.assertEqual(counts['K'], 2)
        self.assertEqual(counts['Q'], 1)
        self.assertEqual(counts['J'], 1)
        self.assertEqual(counts['-'], 19)

    def test_duel_manager_locks_and_challenges(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock())
        u1, u2, u3 = 1001, 1002, 1003

        # Concurrency locks
        self.assertFalse(mgr.is_user_busy(u1))
        mgr.lock_users(u1, u2)
        self.assertTrue(mgr.is_user_busy(u1))
        self.assertTrue(mgr.is_user_busy(u2))
        self.assertFalse(mgr.is_user_busy(u3))

        mgr.release_users(u1, u2)
        self.assertFalse(mgr.is_user_busy(u1))
        self.assertFalse(mgr.is_user_busy(u2))

        # Single pending challenge limit
        self.assertFalse(mgr.has_pending_challenge_target(u2))
        self.assertFalse(mgr.has_pending_challenge_source(u1))

        mgr.set_pending_challenge(u2, u1)
        self.assertTrue(mgr.has_pending_challenge_target(u2))
        self.assertTrue(mgr.has_pending_challenge_source(u1))

        mgr.clear_pending_challenge(u2)
        self.assertFalse(mgr.has_pending_challenge_target(u2))
        self.assertFalse(mgr.has_pending_challenge_source(u1))

    def test_spectator_cheers(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock())
        msg_id = 99999
        p1, p2 = 101, 102
        s1, s2, s3 = 201, 202, 203

        # Spectator 1 cheers P1
        c1, c2 = mgr.record_cheer(msg_id, s1, p1)
        self.assertEqual((c1, c2), (1, 0))

        # Spectator 2 cheers P2
        mgr.record_cheer(msg_id, s2, p2)
        # Spectator 3 cheers P1
        mgr.record_cheer(msg_id, s3, p1)
        c1, c2 = mgr.get_cheer_counts(msg_id, p1, p2)
        self.assertEqual((c1, c2), (2, 1))

        # Spectator 1 switches vote to P2
        mgr.record_cheer(msg_id, s1, p2)
        c1, c2 = mgr.get_cheer_counts(msg_id, p1, p2)
        self.assertEqual((c1, c2), (1, 2))

        mgr.clear_cheers(msg_id)
        c1, c2 = mgr.get_cheer_counts(msg_id, p1, p2)
        self.assertEqual((c1, c2), (0, 0))

    def test_simulate_game_and_best_of_3(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock())
        deal_idx = 1000

        # Simulate 1 casual game
        res = mgr.simulate_game(deal_idx, cut_a=6, cut_b=13, p1_starts=True)
        self.assertIn(res["winner"], (0, 1, 2))
        self.assertGreater(res["cards"], 0)
        self.assertGreater(res["tricks"], 0)
        self.assertIn("tier", res)
        self.assertEqual(len(res["hand_a"]), 26)
        self.assertEqual(len(res["hand_b"]), 26)

        # Simulate Best-of-3
        bo3 = mgr.simulate_best_of_3(cut_a=6, cut_b=13)
        self.assertIn(bo3["series_winner"], (1, 2))
        self.assertIn(" - ", bo3["series_score"])
        self.assertGreater(bo3["total_cards"], 0)
        self.assertIsNotNone(bo3["g1"])
        self.assertIsNotNone(bo3["g2"])


class TestDuelBotCommands(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        import bot
        self.bot_mod = bot
        self.bot_mod._duel_cooldowns.clear()
        self.bot_mod.duel_mgr._active_duels.clear()
        self.bot_mod.duel_mgr._pending_challenges.clear()
        self.mock_pool = MagicMock()
        self.pool_patch = patch.object(self.bot_mod, "get_db_pool", AsyncMock(return_value=self.mock_pool))
        self.pool_patch.start()

    async def asyncTearDown(self):
        self.bot_mod._duel_cooldowns.clear()
        self.bot_mod.duel_mgr._active_duels.clear()
        self.bot_mod.duel_mgr._pending_challenges.clear()
        self.pool_patch.stop()

    async def test_duel_cmd_when_service_down(self):
        interaction = AsyncMock()
        with patch.object(self.bot_mod, "get_db_pool", AsyncMock(return_value=None)):
            await self.bot_mod.duel_cmd.callback(interaction, mode=MagicMock(value="casual"))
            interaction.response.send_message.assert_called_once()
            args = interaction.response.send_message.call_args
            self.assertIn("Duels Temporarily Unavailable", args[0][0])
            self.assertNotIn("db", args[0][0].lower())
            self.assertNotIn("database", args[0][0].lower())

    async def test_duel_settings_when_service_down(self):
        interaction = AsyncMock()
        with patch.object(self.bot_mod, "get_db_pool", AsyncMock(return_value=None)):
            await self.bot_mod.duel_settings_cmd.callback(interaction, direct_challenges=True)
            interaction.response.send_message.assert_called_once()
            args = interaction.response.send_message.call_args
            self.assertIn("Settings Temporarily Unavailable", args[0][0])

    async def test_can_play_casual_quotas(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        # Volunteer has unlimited games
        can_play, reason = await mgr.can_play_casual(101, is_volunteer=True)
        self.assertTrue(can_play)
        self.assertEqual(reason, "")

        # Mock stats for guest with 2 games
        with patch.object(mgr, "get_duel_stats", AsyncMock(return_value={"daily_casual_count": 2})):
            can_play, reason = await mgr.can_play_casual(101, is_volunteer=False)
            self.assertTrue(can_play)

        # Mock stats for guest with 3 games (limit reached)
        with patch.object(mgr, "get_duel_stats", AsyncMock(return_value={"daily_casual_count": 3})):
            can_play, reason = await mgr.can_play_casual(101, is_volunteer=False)
            self.assertFalse(can_play)
            self.assertIn("reached your daily guest limit", reason)

    async def test_can_play_ranked_quotas_and_anti_win_trading(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))

        # P1 exceeded daily ranked tickets
        with patch.object(mgr, "get_duel_stats", AsyncMock(side_effect=[
            {"daily_ranked_count": 5},
            {"daily_ranked_count": 0},
        ])):
            can_play, reason = await mgr.can_play_ranked(101, 102)
            self.assertFalse(can_play)
            self.assertIn("used all 5 ranked tickets", reason)

        # Anti-win-trading check with mock DB pool
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.fetchone = AsyncMock(return_value=(2,))  # Already played 2 matches today
        mock_conn.cursor = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_cur), __aexit__=AsyncMock()))
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock()))

        mgr_db = duel.DuelManager(db_pool_getter=AsyncMock(return_value=mock_pool))
        with patch.object(mgr_db, "get_duel_stats", AsyncMock(return_value={"daily_ranked_count": 1})):
            can_play, reason = await mgr_db.can_play_ranked(101, 102)
            self.assertFalse(can_play)
            self.assertIn("already played 2 ranked games together today", reason)

    async def test_update_direct_challenges_setting(self):
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_conn.cursor = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_cur), __aexit__=AsyncMock()))
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock()))

        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=mock_pool))
        await mgr.update_direct_challenges_setting(101, False)
        mock_cur.execute.assert_called_once()
        args = mock_cur.execute.call_args[0]
        self.assertIn("ON DUPLICATE KEY UPDATE direct_challenges_enabled", args[0])
        self.assertEqual(args[1], (101, False, False))

    async def test_duel_cmd_self_challenge(self):
        interaction = AsyncMock()
        interaction.user.id = 12345
        choice = MagicMock()
        choice.value = "casual"

        opponent = MagicMock()
        opponent.id = 12345  # Self
        opponent.bot = False

        with patch.object(self.bot_mod, "check_is_volunteer", AsyncMock(return_value=True)):
            await self.bot_mod.duel_cmd.callback(interaction, mode=choice, opponent=opponent)
            interaction.response.send_message.assert_called_once()
            args = interaction.response.send_message.call_args
            self.assertIn("cannot challenge yourself", args[0][0])

    async def test_duel_cmd_bot_challenge(self):
        interaction = AsyncMock()
        interaction.user.id = 12345
        choice = MagicMock()
        choice.value = "casual"

        opponent = MagicMock()
        opponent.id = 99999
        opponent.bot = True

        with patch.object(self.bot_mod, "check_is_volunteer", AsyncMock(return_value=True)):
            await self.bot_mod.duel_cmd.callback(interaction, mode=choice, opponent=opponent)
            interaction.response.send_message.assert_called_once()
            args = interaction.response.send_message.call_args
            self.assertIn("cannot challenge bot accounts", args[0][0])

    async def test_duel_cmd_ranked_requires_opponent(self):
        interaction = AsyncMock()
        interaction.user.id = 12345
        choice = MagicMock()
        choice.value = "ranked"

        with patch.object(self.bot_mod, "check_is_volunteer", AsyncMock(return_value=True)):
            await self.bot_mod.duel_cmd.callback(interaction, mode=choice, opponent=None)
            interaction.response.send_message.assert_called_once()
            args = interaction.response.send_message.call_args
            self.assertIn("Ranked Mode Requires an Opponent", args[0][0])

    async def test_duel_cmd_ranked_volunteer_restriction(self):
        interaction = AsyncMock()
        interaction.user.id = 12345
        choice = MagicMock()
        choice.value = "ranked"

        opponent = MagicMock()
        opponent.id = 67890
        opponent.bot = False

        # Challenger is not volunteer
        with patch.object(self.bot_mod, "check_is_volunteer", AsyncMock(return_value=False)):
            await self.bot_mod.duel_cmd.callback(interaction, mode=choice, opponent=opponent)
            interaction.response.send_message.assert_called_once()
            args = interaction.response.send_message.call_args
            self.assertIn("Ranked Mode Restricted", args[0][0])

    async def test_duel_cmd_disabled_direct_challenges(self):
        interaction = AsyncMock()
        interaction.user.id = 12345
        choice = MagicMock()
        choice.value = "casual"

        opponent = MagicMock()
        opponent.id = 67890
        opponent.bot = False
        opponent.mention = "<@67890>"

        with patch.object(self.bot_mod, "check_is_volunteer", AsyncMock(return_value=True)):
            with patch.object(self.bot_mod.duel_mgr, "get_duel_stats", AsyncMock(return_value={"direct_challenges_enabled": False})):
                await self.bot_mod.duel_cmd.callback(interaction, mode=choice, opponent=opponent)
                interaction.response.send_message.assert_called_once()
                args = interaction.response.send_message.call_args
                self.assertIn("has disabled direct challenge requests", args[0][0])

    async def test_duel_settings_cmd(self):
        interaction = AsyncMock()
        interaction.user.id = 12345
        with patch.object(self.bot_mod.duel_mgr, "update_direct_challenges_setting", AsyncMock()) as mock_update:
            await self.bot_mod.duel_settings_cmd.callback(interaction, direct_challenges=False)
            mock_update.assert_called_once_with(12345, False)
            interaction.response.send_message.assert_called_once()
            args = interaction.response.send_message.call_args
            self.assertIn("disabled", args[0][0])

    async def test_on_duel_accept(self):
        interaction = AsyncMock()
        interaction.message = AsyncMock()
        p1 = MagicMock()
        p1.id = 101
        p1.mention = "<@101>"
        p2 = MagicMock()
        p2.id = 102
        p2.mention = "<@102>"

        with patch.object(self.bot_mod.duel_mgr, "lock_users") as mock_lock:
            await self.bot_mod.on_duel_accept(interaction, p1, p2, "casual")
            mock_lock.assert_called_once_with(101, 102)
            interaction.response.edit_message.assert_called_once()
            kwargs = interaction.response.edit_message.call_args[1]
            self.assertIn("embed", kwargs)
            self.assertIn("view", kwargs)
            self.assertIsInstance(kwargs["view"], self.bot_mod.DeckCutAndCheerView)

    async def test_on_duel_cuts_complete_casual(self):
        msg = AsyncMock()
        msg.id = 77777
        p1 = MagicMock()
        p1.id = 101
        p1.display_name = "PlayerOne"
        p1.mention = "<@101>"
        p2 = MagicMock()
        p2.id = 102
        p2.display_name = "PlayerTwo"
        p2.mention = "<@102>"

        with patch.object(self.bot_mod.duel_mgr, "record_match", AsyncMock(return_value=42)) as mock_rec, \
             patch.object(self.bot_mod.duel_mgr, "release_users") as mock_rel, \
             patch("asyncio.sleep", AsyncMock()):
            await self.bot_mod.on_duel_cuts_complete(msg, p1, p2, cut_a=6, cut_b=13, mode="casual")
            mock_rec.assert_called_once()
            self.assertEqual(mock_rec.call_args[1]["mode"], "casual")
            mock_rel.assert_called_once_with(101, 102)
            # Check final edit called with embed and RematchView
            self.assertGreaterEqual(msg.edit.call_count, 2)
            final_call = msg.edit.call_args_list[-1][1]
            self.assertIn("embed", final_call)
            self.assertIn("view", final_call)
            self.assertIsInstance(final_call["view"], self.bot_mod.RematchView)

    async def test_on_duel_cuts_complete_ranked(self):
        msg = AsyncMock()
        msg.id = 88888
        p1 = MagicMock()
        p1.id = 101
        p1.display_name = "PlayerOne"
        p1.mention = "<@101>"
        p2 = MagicMock()
        p2.id = 102
        p2.display_name = "PlayerTwo"
        p2.mention = "<@102>"

        with patch.object(self.bot_mod.duel_mgr, "get_duel_stats", AsyncMock(return_value={"elo_rating": 1000})), \
             patch.object(self.bot_mod.duel_mgr, "update_elo_ratings", AsyncMock()) as mock_elo, \
             patch.object(self.bot_mod.duel_mgr, "record_match", AsyncMock(return_value=99)) as mock_rec, \
             patch.object(self.bot_mod.duel_mgr, "release_users") as mock_rel, \
             patch("asyncio.sleep", AsyncMock()):
            await self.bot_mod.on_duel_cuts_complete(msg, p1, p2, cut_a=0, cut_b=20, mode="ranked")
            mock_elo.assert_called_once()
            mock_rec.assert_called_once()
            self.assertEqual(mock_rec.call_args[1]["mode"], "ranked")
            mock_rel.assert_called_once_with(101, 102)
            final_call = msg.edit.call_args_list[-1][1]
            self.assertIn("embed", final_call)
            self.assertIsNone(final_call["view"])  # No rematch button for ranked

    async def test_on_duel_rematch(self):
        interaction = AsyncMock()
        interaction.message = AsyncMock()
        p1 = MagicMock()
        p1.id = 101
        p1.mention = "<@101>"
        p2 = MagicMock()
        p2.id = 102
        p2.mention = "<@102>"

        with patch.object(self.bot_mod, "check_is_volunteer", AsyncMock(return_value=True)), \
             patch.object(self.bot_mod.duel_mgr, "can_play_casual", AsyncMock(return_value=(True, ""))), \
             patch.object(self.bot_mod.duel_mgr, "is_user_busy", MagicMock(return_value=False)), \
             patch.object(self.bot_mod.duel_mgr, "lock_users") as mock_lock:
            await self.bot_mod.on_duel_rematch(interaction, p1, p2)
            mock_lock.assert_called_once_with(101, 102)
            interaction.response.edit_message.assert_called_once()
            kwargs = interaction.response.edit_message.call_args[1]
            self.assertIn("embed", kwargs)
            self.assertIn("view", kwargs)
    async def test_on_duel_accept_service_down(self):
        interaction = AsyncMock()
        interaction.message = AsyncMock()
        p1 = MagicMock()
        p1.id = 101
        p2 = MagicMock()
        p2.id = 102

        with patch.object(self.bot_mod, "get_db_pool", AsyncMock(return_value=None)), \
             patch.object(self.bot_mod.duel_mgr, "release_users") as mock_rel:
            await self.bot_mod.on_duel_accept(interaction, p1, p2, "casual")
            mock_rel.assert_called_once_with(101, 102)
            interaction.response.send_message.assert_called_once()
            self.assertIn("Duel Unavailable", interaction.response.send_message.call_args[0][0])

    async def test_on_duel_rematch_service_down_clears_view(self):
        interaction = AsyncMock()
        interaction.message = AsyncMock()
        p1 = MagicMock()
        p1.id = 101
        p2 = MagicMock()
        p2.id = 102

        with patch.object(self.bot_mod, "get_db_pool", AsyncMock(return_value=None)):
            await self.bot_mod.on_duel_rematch(interaction, p1, p2)
            interaction.response.send_message.assert_called_once()
            self.assertIn("Rematch Unavailable", interaction.response.send_message.call_args[0][0])
            interaction.message.edit.assert_called_once_with(view=None)

    async def test_duel_cmd_timeout_direct_vs_open(self):
        interaction = AsyncMock()
        interaction.user.id = 101
        p2 = MagicMock()
        p2.id = 102
        p2.bot = False
        p2.mention = "<@102>"

        with patch.object(self.bot_mod, "check_is_volunteer", AsyncMock(return_value=True)), \
             patch.object(self.bot_mod.duel_mgr, "get_duel_stats", AsyncMock(return_value={"direct_challenges_enabled": True})):
            # Direct challenge: timeout should be config.DUEL_DIRECT_TIMEOUT_SECONDS (45s)
            await self.bot_mod.duel_cmd.callback(interaction, mode=MagicMock(value="casual"), opponent=p2)
            view_direct = interaction.response.send_message.call_args[1]["view"]
            self.assertEqual(view_direct.timeout, self.bot_mod.config.DUEL_DIRECT_TIMEOUT_SECONDS)

            # Open challenge: timeout should be config.DUEL_OPEN_TIMEOUT_SECONDS (90s)
            self.bot_mod._duel_cooldowns.clear()
            self.bot_mod.duel_mgr.release_users(101, 102)
            self.bot_mod.duel_mgr.clear_pending_challenge(102)
            interaction.response.send_message.reset_mock()
            await self.bot_mod.duel_cmd.callback(interaction, mode=MagicMock(value="casual"), opponent=None)
            view_open = interaction.response.send_message.call_args[1]["view"]
            self.assertEqual(view_open.timeout, self.bot_mod.config.DUEL_OPEN_TIMEOUT_SECONDS)

    def test_simulate_status_enum_mapping(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        # A normal finished game should map status to "completed" (matching MariaDB enum)
        with patch.object(duel.engine, "simulate", return_value={"status": "finished", "cards": 100, "tricks": 20, "winner": 1}):
            res = mgr.simulate_game(100, cut_a=0, cut_b=0, p1_starts=True)
            self.assertEqual(res["status"], "completed")

        # A loop game maps to "loop"
        with patch.object(duel.engine, "simulate", return_value={"status": "loop", "cards": 50, "tricks": 10, "winner": 0}):
            res = mgr.simulate_game(100, cut_a=0, cut_b=0, p1_starts=True)
            self.assertEqual(res["status"], "loop")

        # A record game (> 8344 cards) maps to "record"
        with patch.object(duel.engine, "simulate", return_value={"status": "finished", "cards": 9000, "tricks": 1000, "winner": 1}):
            res = mgr.simulate_game(100, cut_a=0, cut_b=0, p1_starts=True)
            self.assertEqual(res["status"], "record")

    def test_simulate_best_of_3_tiebreak_tricks(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        g1 = {"status": "completed", "cards": 100, "tricks": 15, "winner": 1, "is_record": False, "is_loop": False, "hand_a": "A"*26, "hand_b": "K"*26}
        g2 = {"status": "completed", "cards": 120, "tricks": 18, "winner": 2, "is_record": False, "is_loop": False, "hand_a": "A"*26, "hand_b": "K"*26}
        t_a = {"status": "completed", "cards": 200, "tricks": 25, "winner": 1, "is_record": False, "is_loop": False, "hand_a": "A"*26, "hand_b": "K"*26}
        t_b = {"status": "completed", "cards": 180, "tricks": 22, "winner": 1, "is_record": False, "is_loop": False, "hand_a": "A"*26, "hand_b": "K"*26}

        with patch.object(mgr, "simulate_game", side_effect=[g1, g2, t_a, t_b]):
            bo3 = mgr.simulate_best_of_3(cut_a=6, cut_b=13)
            self.assertEqual(bo3["series_winner"], 1)
            self.assertEqual(bo3["series_score"], "2 - 1")
            self.assertEqual(bo3["total_cards"], 100 + 120 + 200 + 180)
            self.assertEqual(bo3["total_tricks"], 15 + 18 + 25 + 22)
            self.assertIn("p1_tricks", bo3["g3"])
            self.assertEqual(bo3["g3"]["p1_tricks"], 25)

    def test_simulate_best_of_3_tiebreak_record_and_loop_flags(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        g1 = {"status": "completed", "cards": 100, "tricks": 15, "winner": 1, "is_record": False, "is_loop": False, "hand_a": "A"*26, "hand_b": "K"*26}
        g2 = {"status": "completed", "cards": 120, "tricks": 18, "winner": 2, "is_record": False, "is_loop": False, "hand_a": "A"*26, "hand_b": "K"*26}
        t_rec = {"status": "record", "cards": 9000, "tricks": 1000, "winner": 1, "is_record": True, "is_loop": False, "hand_a": "A"*26, "hand_b": "K"*26}
        t_b = {"status": "completed", "cards": 180, "tricks": 22, "winner": 1, "is_record": False, "is_loop": False, "hand_a": "A"*26, "hand_b": "K"*26}

        with patch.object(mgr, "simulate_game", side_effect=[g1, g2, t_rec, t_b]):
            bo3 = mgr.simulate_best_of_3(cut_a=6, cut_b=13)
            self.assertTrue(bo3["is_record"])

    def test_elo_floor_actual_delta(self):
        # When clamped to 100, actual delta must be new_rating - rating (e.g. 100 - 105 = -5)
        r1, r2, d1, d2 = duel.calculate_elo(105, 105, 0.0, k=32)
        self.assertEqual(r1, 100)
        self.assertEqual(d1, -5)

    def test_duel_challenge_view_open_pool_removes_decline_button(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        challenger = MagicMock()
        view = duel.DuelChallengeView(
            duel_mgr=mgr,
            challenger=challenger,
            opponent=None,  # Open challenge
            mode="casual",
            timeout=90.0,
            on_accept_callback=AsyncMock(),
        )
        custom_ids = [getattr(c, "custom_id", None) for c in view.children]
        self.assertNotIn("decline", custom_ids)
        self.assertIn("accept", custom_ids)
        self.assertIn("cancel", custom_ids)

    async def test_duel_challenge_view_duplicate_accept(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        challenger = MagicMock()
        challenger.id = 101
        on_accept = AsyncMock()
        view = duel.DuelChallengeView(
            duel_mgr=mgr,
            challenger=challenger,
            opponent=None,
            mode="casual",
            timeout=90.0,
            on_accept_callback=on_accept,
        )

        inter1 = AsyncMock()
        inter1.user.id = 102

        await view.accept_button.callback(inter1)
        self.assertEqual(on_accept.call_count, 1)

        # Second click should be rejected
        inter2 = AsyncMock()
        inter2.user.id = 103
        await view.accept_button.callback(inter2)
        self.assertEqual(on_accept.call_count, 1)  # Still 1
        inter2.response.send_message.assert_called_once()
        self.assertIn("already been accepted", inter2.response.send_message.call_args[0][0])

    async def test_deck_cut_and_cheer_view_initial_labels_and_cooldown(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        p1 = MagicMock()
        p1.id = 101
        p1.display_name = "SuperLongPlayerNameExceedingThirtyCharacters"
        p2 = MagicMock()
        p2.id = 102
        p2.display_name = "ShortName"

        view = duel.DeckCutAndCheerView(
            duel_mgr=mgr,
            p1=p1,
            p2=p2,
            timeout=15.0,
            on_cuts_complete_callback=AsyncMock(),
        )

        labels = {getattr(c, "custom_id", None): getattr(c, "label", "") for c in view.children}
        # Verify truncation to 25 chars max in display name
        self.assertTrue(labels["cheer_p1"].startswith(f"Cheer {p1.display_name[:25]}"))
        self.assertTrue(len(labels["cheer_p1"]) < 80)

        # Verify cheer cooldown
        msg = AsyncMock()
        msg.id = 9999
        view.message = msg

        inter = AsyncMock()
        inter.user.id = 301  # Spectator
        await view.cheer_p1.callback(inter)
        inter.response.edit_message.assert_called_once()

        # Immediate second cheer should hit 1s cooldown
        inter2 = AsyncMock()
        inter2.user.id = 301
        await view.cheer_p2.callback(inter2)
        inter2.response.send_message.assert_called_once()
        self.assertIn("wait a moment", inter2.response.send_message.call_args[0][0])

    async def test_deck_cut_view_timeout_releases_users_when_message_none(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        mgr.lock_users(101, 102)
        self.assertTrue(mgr.is_user_busy(101))

        p1 = MagicMock()
        p1.id = 101
        p2 = MagicMock()
        p2.id = 102

        view = duel.DeckCutAndCheerView(
            duel_mgr=mgr,
            p1=p1,
            p2=p2,
            timeout=15.0,
            on_cuts_complete_callback=AsyncMock(),
        )
        view.message = None  # Message missing

        await view.on_timeout()
        # Ensure users were released and not left stranded
        self.assertFalse(mgr.is_user_busy(101))
        self.assertFalse(mgr.is_user_busy(102))

    async def test_get_duel_stats_sql_10_columns_10_values(self):
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.fetchone = AsyncMock(return_value=None)  # Simulates first-time user
        mock_conn.cursor = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_cur), __aexit__=AsyncMock()))
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock()))

        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=mock_pool))
        stats = await mgr.get_duel_stats(12345)
        self.assertEqual(stats["elo_rating"], 1000)

        # Inspect INSERT query executed
        insert_calls = [c for c in mock_cur.execute.call_args_list if "INSERT INTO" in c[0][0]]
        self.assertTrue(len(insert_calls) > 0, "No INSERT INTO call found")
        insert_call = insert_calls[0]
        query = insert_call[0][0]
        params = insert_call[0][1]

        # Verify columns vs values count in INSERT
        columns_part = query.split("VALUES")[0]
        values_part = query.split("VALUES")[1].split("ON DUPLICATE")[0]
        column_count = columns_part.count(",") + 1
        value_count = values_part.count(",") + 1
        self.assertEqual(column_count, value_count, f"Columns: {column_count}, Values: {value_count}")
        self.assertEqual(column_count, 10)

    async def test_direct_challenge_acceptance_via_interaction_check(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        challenger = MagicMock(id=101)
        opponent = MagicMock(id=102)

        mgr.lock_users(101, 102)
        mgr.set_pending_challenge(102, 101)

        view = duel.DuelChallengeView(
            duel_mgr=mgr,
            challenger=challenger,
            opponent=opponent,
            mode="casual",
            timeout=45.0,
            on_accept_callback=AsyncMock(),
        )

        # 1. Designated opponent should pass interaction_check despite being locked
        inter_opp = AsyncMock()
        inter_opp.data = {"custom_id": "accept"}
        inter_opp.user.id = 102
        self.assertTrue(await view.interaction_check(inter_opp))

        # 2. Unauthorized third-party user must be rejected
        inter_third = AsyncMock()
        inter_third.data = {"custom_id": "accept"}
        inter_third.user.id = 103
        self.assertFalse(await view.interaction_check(inter_third))
        inter_third.response.send_message.assert_called_once()
        self.assertIn("issued specifically", inter_third.response.send_message.call_args[0][0])

    async def test_open_challenge_host_and_busy_checks(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        challenger = MagicMock(id=101)
        mgr._active_duels.add(101)
        mgr._active_duels.add(105)  # Busy user 105

        view = duel.DuelChallengeView(
            duel_mgr=mgr,
            challenger=challenger,
            opponent=None,
            mode="casual",
            timeout=90.0,
            on_accept_callback=AsyncMock(),
            can_accept_callback=AsyncMock(return_value=(True, "")),
        )

        # 1. Host cannot accept own challenge
        inter_host = AsyncMock()
        inter_host.data = {"custom_id": "accept"}
        inter_host.user.id = 101
        self.assertFalse(await view.interaction_check(inter_host))

        # 2. Busy user cannot accept
        inter_busy = AsyncMock()
        inter_busy.data = {"custom_id": "accept"}
        inter_busy.user.id = 105
        self.assertFalse(await view.interaction_check(inter_busy))

        # 3. Free user can accept
        inter_free = AsyncMock()
        inter_free.data = {"custom_id": "accept"}
        inter_free.user.id = 106
        self.assertTrue(await view.interaction_check(inter_free))

    async def test_rematch_timeout_clears_content(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        p1 = MagicMock(id=101)
        p2 = MagicMock(id=102)

        view = duel.RematchView(
            duel_mgr=mgr,
            p1=p1,
            p2=p2,
            on_rematch_callback=AsyncMock(),
        )
        msg = AsyncMock()
        view.message = msg

        await view.on_timeout()
        msg.edit.assert_called_once_with(content=None, view=view)

    async def test_duel_cmd_send_message_failure_releases_locks(self):
        interaction = AsyncMock()
        interaction.user.id = 101
        p2 = MagicMock()
        p2.id = 102
        p2.bot = False
        p2.mention = "<@102>"

        # Simulate send_message raising an exception
        interaction.response.send_message.side_effect = RuntimeError("Discord API outage")

        with patch.object(self.bot_mod, "check_is_volunteer", AsyncMock(return_value=True)), \
             patch.object(self.bot_mod.duel_mgr, "get_duel_stats", AsyncMock(return_value={"direct_challenges_enabled": True})):
            with self.assertRaises(RuntimeError):
                await self.bot_mod.duel_cmd.callback(interaction, mode=MagicMock(value="casual"), opponent=p2)

            # Ensure locks and pending challenges were cleaned up despite failure
            self.assertFalse(self.bot_mod.duel_mgr.is_user_busy(101))
            self.assertFalse(self.bot_mod.duel_mgr.is_user_busy(102))
            self.assertFalse(self.bot_mod.duel_mgr.has_pending_challenge_target(102))

    def test_tiebreak_endurance_loop_beats_finite_game(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        g1 = {"status": "completed", "cards": 100, "tricks": 15, "winner": 1, "is_record": False, "is_loop": False, "hand_a": "A"*26, "hand_b": "K"*26}
        g2 = {"status": "completed", "cards": 120, "tricks": 18, "winner": 2, "is_record": False, "is_loop": False, "hand_a": "A"*26, "hand_b": "K"*26}
        # P1 gets infinite loop with only 50 cards detected
        t_loop = {"status": "loop", "cards": 50, "tricks": 5, "winner": 0, "is_record": False, "is_loop": True, "hand_a": "A"*26, "hand_b": "K"*26}
        # P2 gets finite game with 500 cards
        t_finite = {"status": "completed", "cards": 500, "tricks": 50, "winner": 1, "is_record": False, "is_loop": False, "hand_a": "A"*26, "hand_b": "K"*26}

        with patch.object(mgr, "simulate_game", side_effect=[g1, g2, t_loop, t_finite]):
            bo3 = mgr.simulate_best_of_3(cut_a=6, cut_b=13)
            # P1 should win tiebreak because infinite loop has infinite endurance
            self.assertEqual(bo3["series_winner"], 1)
            self.assertTrue(bo3["is_loop"])

    def test_simulate_game_returns_deal_index(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        res = mgr.simulate_game(deal_index=12345, cut_a=0, cut_b=0)
        self.assertEqual(res["deal_index"], 12345)

    def test_simulate_best_of_3_record_and_loop_info(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        g1 = {"status": "record", "cards": 9999, "tricks": 1500, "winner": 1, "is_record": True, "is_loop": False, "deal_index": 111, "hand_a": "A"*26, "hand_b": "K"*26}
        g2 = {"status": "loop", "cards": 200, "tricks": 30, "winner": 0, "is_record": False, "is_loop": True, "deal_index": 222, "hand_a": "A"*26, "hand_b": "K"*26}
        with patch.object(mgr, "simulate_game", side_effect=[g1, g2]):
            bo3 = mgr.simulate_best_of_3(cut_a=0, cut_b=0)
            self.assertTrue(bo3["is_record"])
            self.assertTrue(bo3["is_loop"])
            self.assertIsNotNone(bo3["record_game"])
            self.assertEqual(bo3["record_game"]["cards"], 9999)
            self.assertEqual(bo3["record_game"]["deal_index"], 111)
            self.assertIsNotNone(bo3["loop_game"])
            self.assertEqual(bo3["loop_game"]["deal_index"], 222)

    async def test_broadcast_duel_discovery_loop(self):
        mock_channel = AsyncMock()
        mock_channel.name = "records-and-loops"
        p1 = MagicMock(id=101, mention="<@101>")
        p2 = MagicMock(id=102, mention="<@102>")

        with patch.object(self.bot_mod.config, "DISCORD_RECORDS_CHANNEL_ID", 999), \
             patch.object(self.bot_mod.bot, "get_channel", return_value=mock_channel):
            await self.bot_mod.broadcast_duel_discovery(
                p1=p1,
                p2=p2,
                mode="ranked",
                is_loop=True,
                cards=250,
                tricks=40,
                deal_index=54321,
                match_id=77,
            )
            mock_channel.send.assert_called_once()
            call_kwargs = mock_channel.send.call_args[1]
            self.assertIsNone(call_kwargs.get("content"))
            embed = call_kwargs.get("embed")
            self.assertIn("Infinite Loop Discovered", embed.title)
            self.assertEqual(embed.color.value, 0x9B59B6)
            field_names = [f.name for f in embed.fields]
            self.assertIn("⚔️ Duelists", field_names)
            self.assertIn("🔢 Deal Index", field_names)
            self.assertIn("🎮 Match ID", field_names)

    async def test_broadcast_duel_discovery_record(self):
        mock_channel = AsyncMock()
        mock_channel.name = "records-and-loops"
        p1 = MagicMock(id=101, mention="<@101>")
        p2 = MagicMock(id=102, mention="<@102>")
        mock_watcher = MagicMock()
        mock_watcher.state = {"last_best_cards": 500}

        with patch.object(self.bot_mod.config, "DISCORD_RECORDS_CHANNEL_ID", 999), \
             patch.object(self.bot_mod.bot, "get_channel", return_value=mock_channel), \
             patch.object(self.bot_mod, "watcher", mock_watcher):
            await self.bot_mod.broadcast_duel_discovery(
                p1=p1,
                p2=p2,
                mode="casual",
                is_loop=False,
                cards=8500,
                tricks=1200,
                deal_index=99999,
                match_id=88,
            )
            mock_channel.send.assert_called_once()
            call_kwargs = mock_channel.send.call_args[1]
            self.assertEqual(call_kwargs.get("content"), "@everyone")
            embed = call_kwargs.get("embed")
            self.assertIn("WORLD RECORD BROKEN", embed.title)
            self.assertEqual(embed.color.value, 0xFF0033)
            self.assertEqual(mock_watcher.state["last_best_cards"], 8500)
            mock_watcher._save_state.assert_called_once()

    async def test_duel_challenge_view_timeout_guard_when_accepted(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        challenger = MagicMock(id=101)
        opponent = MagicMock(id=102)
        mgr.lock_users(101, 102)

        view = duel.DuelChallengeView(
            duel_mgr=mgr,
            challenger=challenger,
            opponent=opponent,
            mode="casual",
            timeout=45.0,
            on_accept_callback=AsyncMock(),
        )
        msg = AsyncMock()
        view.message = msg
        view._accepted = True

        await view.on_timeout()
        # Should not edit message or release users
        msg.edit.assert_not_called()
        self.assertTrue(mgr.is_user_busy(101))
        self.assertTrue(mgr.is_user_busy(102))

    async def test_rematch_view_timeout_guard_when_accepted(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        p1 = MagicMock(id=101)
        p2 = MagicMock(id=102)
        view = duel.RematchView(duel_mgr=mgr, p1=p1, p2=p2, on_rematch_callback=AsyncMock())
        msg = AsyncMock()
        view.message = msg
        view._accepted = True

        await view.on_timeout()
        msg.edit.assert_not_called()

    async def test_duelist_cannot_spectator_cheer(self):
        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        p1 = MagicMock(id=101)
        p2 = MagicMock(id=102)
        view = duel.DeckCutAndCheerView(
            duel_mgr=mgr,
            p1=p1,
            p2=p2,
            timeout=15.0,
            on_cuts_complete_callback=AsyncMock(),
        )
        inter = AsyncMock()
        inter.user.id = 101  # Duelist

        await view._handle_cheer(inter, 102, "Player2")
        inter.response.send_message.assert_called_once()
        self.assertIn("Duelists cannot participate in spectator cheering!", inter.response.send_message.call_args[0][0])

    async def test_opponent_outgoing_pending_challenge_message(self):
        interaction = AsyncMock()
        interaction.user.id = 101
        p2 = MagicMock()
        p2.id = 102
        p2.bot = False
        p2.mention = "<@102>"

        with patch.object(self.bot_mod, "check_is_volunteer", AsyncMock(return_value=True)), \
             patch.object(self.bot_mod.duel_mgr, "has_pending_challenge_source", MagicMock(side_effect=lambda uid: uid == 102)):
            await self.bot_mod.duel_cmd.callback(interaction, mode=MagicMock(value="casual"), opponent=p2)
            interaction.response.send_message.assert_called_once()
            call_text = interaction.response.send_message.call_args[0][0]
            self.assertIn("already issued a challenge to another player", call_text)

    async def test_get_duel_leaderboard(self):
        # Pool is None
        mgr_none = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        self.assertEqual(await mgr_none.get_duel_leaderboard(), [])

        # Pool with records
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.fetchall = AsyncMock(return_value=[
            (101, 1250, 10, 2, 0),
            (102, 1100, 5, 5, 0),
        ])
        mock_conn.cursor = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_cur), __aexit__=AsyncMock()))
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock()))

        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=mock_pool))
        lb = await mgr.get_duel_leaderboard(limit=10)
        self.assertEqual(len(lb), 2)
        self.assertEqual(lb[0]["discord_id"], 101)
        self.assertEqual(lb[0]["elo_rating"], 1250)
        self.assertEqual(lb[0]["total_games"], 12)
        self.assertAlmostEqual(lb[0]["win_rate"], 83.333333, places=2)

    async def test_get_user_rank(self):
        # Pool is None
        mgr_none = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        self.assertIsNone(await mgr_none.get_user_rank(101))

        # Unranked user
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.fetchone = AsyncMock(side_effect=[
            (1000, 0),  # User row: 0 games played
        ])
        mock_conn.cursor = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_cur), __aexit__=AsyncMock()))
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock()))

        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=mock_pool))
        self.assertIsNone(await mgr.get_user_rank(101))

        # Ranked user (rank 3)
        mock_cur.fetchone = AsyncMock(side_effect=[
            (1150, 10),  # User row: elo 1150, 10 games
            (2,),        # Count of users ranked higher -> 2
        ])
        self.assertEqual(await mgr.get_user_rank(101), 3)

    async def test_get_user_recent_matches(self):
        # Pool is None
        mgr_none = duel.DuelManager(db_pool_getter=AsyncMock(return_value=None))
        self.assertEqual(await mgr_none.get_user_recent_matches(101), [])

        # Pool with matches
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.fetchall = AsyncMock(return_value=[
            (1, "ranked", 101, 102, 101, 240, 32, "2-1", "completed", "2026-10-08"),  # win
            (2, "casual", 103, 101, 103, 110, 14, "1-0", "completed", "2026-10-08"),  # loss
            (3, "ranked", 101, 104, None, 300, 40, "1-1", "loop", "2026-10-08"),       # tie/loop
        ])
        mock_conn.cursor = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_cur), __aexit__=AsyncMock()))
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock()))

        mgr = duel.DuelManager(db_pool_getter=AsyncMock(return_value=mock_pool))
        matches = await mgr.get_user_recent_matches(101, limit=3)
        self.assertEqual(len(matches), 3)
        self.assertEqual(matches[0]["outcome"], "win")
        self.assertEqual(matches[0]["opponent_id"], 102)
        self.assertEqual(matches[1]["outcome"], "loss")
        self.assertEqual(matches[1]["opponent_id"], 103)
        self.assertEqual(matches[2]["outcome"], "tie")
        self.assertEqual(matches[2]["opponent_id"], 104)

    async def test_duel_leaderboard_cmd(self):
        interaction = AsyncMock()
        interaction.user.id = 101
        interaction.user.mention = "<@101>"

        # 1. DB pool is None
        with patch.object(self.bot_mod, "get_db_pool", AsyncMock(return_value=None)):
            await self.bot_mod.duel_leaderboard_cmd.callback(interaction)
            interaction.response.send_message.assert_called_once()
            self.assertIn("Leaderboard Temporarily Unavailable", interaction.response.send_message.call_args[0][0])

        # 2. Empty leaderboard
        interaction.response.send_message.reset_mock()
        with patch.object(self.bot_mod.duel_mgr, "get_duel_leaderboard", AsyncMock(return_value=[])), \
             patch.object(self.bot_mod.duel_mgr, "get_user_rank", AsyncMock(return_value=None)), \
             patch.object(self.bot_mod.duel_mgr, "get_duel_stats", AsyncMock(return_value={"wins": 0, "losses": 0, "ties": 0, "elo_rating": 1000})):
            await self.bot_mod.duel_leaderboard_cmd.callback(interaction)
            interaction.response.send_message.assert_called_once()
            embed = interaction.response.send_message.call_args[1]["embed"]
            self.assertIn("No ranked duels recorded yet", embed.description)

        # 3. Populated leaderboard with caller outside top 10
        interaction.response.send_message.reset_mock()
        fake_lb = [
            {"discord_id": 999, "elo_rating": 1400, "wins": 20, "losses": 1, "ties": 0, "total_games": 21, "win_rate": 95.2}
        ]
        with patch.object(self.bot_mod.duel_mgr, "get_duel_leaderboard", AsyncMock(return_value=fake_lb)), \
             patch.object(self.bot_mod.duel_mgr, "get_user_rank", AsyncMock(return_value=12)), \
             patch.object(self.bot_mod.duel_mgr, "get_duel_stats", AsyncMock(return_value={"wins": 5, "losses": 5, "ties": 0, "elo_rating": 1000})):
            await self.bot_mod.duel_leaderboard_cmd.callback(interaction)
            interaction.response.send_message.assert_called_once()
            embed = interaction.response.send_message.call_args[1]["embed"]
            field_names = [f.name for f in embed.fields]
            self.assertIn("Your Standing", field_names)

    async def test_duel_stats_cmd_dm_restriction(self):
        interaction = AsyncMock()
        interaction.guild = None  # In DM
        interaction.user.id = 101

        other_user = MagicMock(id=102)

        # Inspecting another user in DM -> blocked
        await self.bot_mod.duel_stats_cmd.callback(interaction, user=other_user)
        interaction.response.send_message.assert_called_once()
        call_text = interaction.response.send_message.call_args[0][0]
        self.assertIn("In Direct Messages, you can only view your own duel stats", call_text)

    async def test_duel_stats_cmd_success(self):
        interaction = AsyncMock()
        interaction.guild = MagicMock()
        interaction.user.id = 101
        interaction.user.display_name = "PlayerOne"
        interaction.user.display_avatar.url = "https://example.com/avatar.png"

        fake_stats = {
            "elo_rating": 1150,
            "wins": 6,
            "losses": 2,
            "ties": 1,
            "daily_casual_count": 1,
            "daily_ranked_count": 2,
            "direct_challenges_enabled": True,
        }
        fake_matches = [
            {
                "match_id": 1,
                "mode": "ranked",
                "opponent_id": 102,
                "outcome": "win",
                "series_score": "2-1",
                "cards_played": 312,
                "tricks": 40,
                "status": "completed",
                "played_date": "2026-10-08",
            }
        ]

        with patch.object(self.bot_mod.duel_mgr, "get_duel_stats", AsyncMock(return_value=fake_stats)), \
             patch.object(self.bot_mod.duel_mgr, "get_user_rank", AsyncMock(return_value=2)), \
             patch.object(self.bot_mod.duel_mgr, "get_user_recent_matches", AsyncMock(return_value=fake_matches)), \
             patch.object(self.bot_mod, "check_is_volunteer", AsyncMock(return_value=True)):
            await self.bot_mod.duel_stats_cmd.callback(interaction, user=None)
            interaction.response.send_message.assert_called_once()
            embed = interaction.response.send_message.call_args[1]["embed"]
            self.assertIn("PlayerOne", embed.title)
            self.assertIn("1,150", embed.fields[0].value)
            self.assertIn("#2", embed.fields[0].value)
            self.assertIn("3 / 5", embed.fields[1].value)  # 5 - 2 = 3 tickets left
            self.assertIn("WIN", embed.fields[2].value)

    async def test_on_duel_cuts_complete_suspense_delay_3s(self):
        msg = AsyncMock()
        p1 = MagicMock(id=101, display_name="P1")
        p2 = MagicMock(id=102, display_name="P2")
        p1.mention = "<@101>"
        p2.mention = "<@102>"

        fake_sim = {
            "winner": 1,
            "status": "completed",
            "cards": 100,
            "tricks": 10,
            "is_loop": False,
            "is_record": False,
            "tier": "Common",
            "rarity_desc": "Common",
            "color": 0x3498DB,
            "hand_a": "A" * 26,
            "hand_b": "K" * 26,
            "deal_index": 1,
        }
        with patch("asyncio.sleep", AsyncMock()) as mock_sleep, \
             patch.object(self.bot_mod.duel_mgr, "simulate_game", return_value=fake_sim), \
             patch.object(self.bot_mod.duel_mgr, "record_match", AsyncMock(return_value=1)), \
             patch.object(self.bot_mod.duel_mgr, "release_users"):
            await self.bot_mod.on_duel_cuts_complete(msg, p1, p2, cut_a=0, cut_b=0, mode="casual")
            mock_sleep.assert_called_once_with(3.0)

    async def test_help_cmd_in_dm_guest(self):
        interaction = AsyncMock()
        interaction.guild = None
        interaction.user = MagicMock(id=101)

        with patch.object(self.bot_mod, "check_is_volunteer", AsyncMock(return_value=False)):
            await self.bot_mod.help_cmd.callback(interaction)
            interaction.response.send_message.assert_called_once()
            self.assertTrue(interaction.response.send_message.call_args[1].get("ephemeral"))
            embed = interaction.response.send_message.call_args[1]["embed"]
            self.assertIn("Guest (Unlinked)", embed.description)
            self.assertIn("Direct Messages", embed.description)
            field_names = [f.name for f in embed.fields]
            self.assertIn("⚙️ Private Duel Settings", field_names)
            self.assertIn("🔐 Link BOINC Account (Unlock Volunteer Perks)", field_names)
            self.assertNotIn("🔧 Administrator Tools", field_names)

    async def test_help_cmd_in_bot_commands_volunteer(self):
        interaction = AsyncMock()
        interaction.guild = MagicMock()
        interaction.channel_id = self.bot_mod.config.DISCORD_BOT_COMMANDS_CHANNEL_ID or 999
        interaction.channel.name = "bot-commands"
        user = MagicMock(id=102)
        user.guild_permissions.administrator = False
        interaction.user = user
        interaction.guild.get_member = MagicMock(return_value=user)

        with patch.object(self.bot_mod, "check_is_volunteer", AsyncMock(return_value=True)):
            await self.bot_mod.help_cmd.callback(interaction)
            interaction.response.send_message.assert_called_once()
            self.assertTrue(interaction.response.send_message.call_args[1].get("ephemeral"))
            embed = interaction.response.send_message.call_args[1]["embed"]
            self.assertIn("Linked Volunteer", embed.description)
            field_names = [f.name for f in embed.fields]
            self.assertIn("⚔️ Multiplayer Duels (Beggar-My-Neighbour)", field_names)
            self.assertIn("🍀 Lucky Permutations", field_names)
            self.assertIn("🏅 BOINC Volunteer Account", field_names)
            self.assertNotIn("🔧 Administrator Tools", field_names)

    async def test_help_cmd_admin_tools(self):
        interaction = AsyncMock()
        interaction.guild = MagicMock()
        interaction.channel_id = 999
        interaction.channel.name = "bot-commands"
        user = MagicMock(id=103)
        user.guild_permissions.administrator = True
        interaction.user = user

        with patch.object(self.bot_mod, "check_is_volunteer", AsyncMock(return_value=True)):
            await self.bot_mod.help_cmd.callback(interaction)
            interaction.response.send_message.assert_called_once()
            embed = interaction.response.send_message.call_args[1]["embed"]
            self.assertIn("Server Administrator", embed.description)
            field_names = [f.name for f in embed.fields]
            self.assertIn("🔧 Administrator Tools", field_names)


if __name__ == "__main__":
    unittest.main()

