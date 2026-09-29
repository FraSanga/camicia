import asyncio
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import bot
from discord import app_commands


class TestRateLimitsAndAntiAbuse(unittest.TestCase):
    def setUp(self):
        bot._failed_link_attempts.clear()

    def test_link_failed_attempts_and_lockout(self):
        user_id = 987654321

        # Attempts 1 to 4 should not trigger lockout
        for i in range(1, 5):
            rem = bot.record_failed_link_attempt(user_id)
            self.assertEqual(rem, 5 - i)
            is_locked, remaining_sec = bot.check_link_lockout(user_id)
            self.assertFalse(is_locked)
            self.assertEqual(remaining_sec, 0)

        # 5th attempt triggers lockout
        rem = bot.record_failed_link_attempt(user_id)
        self.assertEqual(rem, 0)
        is_locked, remaining_sec = bot.check_link_lockout(user_id)
        self.assertTrue(is_locked)
        self.assertGreater(remaining_sec, 890)
        self.assertLessEqual(remaining_sec, 900)

        # 6th attempt while locked out remains locked out
        is_locked, remaining_sec = bot.check_link_lockout(user_id)
        self.assertTrue(is_locked)

        # Clear failed attempts (e.g. after successful link)
        bot.clear_failed_link_attempts(user_id)
        is_locked, remaining_sec = bot.check_link_lockout(user_id)
        self.assertFalse(is_locked)
        self.assertEqual(remaining_sec, 0)

    def test_link_lockout_expiration(self):
        user_id = 11223344
        past_time = datetime.now(timezone.utc).timestamp() - 905  # >15 mins ago
        bot._failed_link_attempts[user_id] = [past_time] * 5

        # Since attempts were > 15 minutes ago, lockout has expired
        is_locked, remaining_sec = bot.check_link_lockout(user_id)
        self.assertFalse(is_locked)
        self.assertEqual(remaining_sec, 0)

    def test_records_channel_cooldown(self):
        async def run_test():
            # records_cmd first check is cooldown
            check = bot.records_cmd.checks[0]
            mock1 = MagicMock()
            mock1.created_at = datetime.now(timezone.utc)
            mock1.channel_id = 4455

            # 1st execution in channel succeeds
            res1 = await check(mock1)
            self.assertTrue(res1)

            # 2nd execution in same channel triggers CommandOnCooldown (15s)
            with self.assertRaises(app_commands.CommandOnCooldown) as ctx:
                await check(mock1)
            self.assertEqual(ctx.exception.cooldown.rate, 1)
            self.assertEqual(ctx.exception.cooldown.per, 15.0)

            # Different channel is not affected
            mock2 = MagicMock()
            mock2.created_at = datetime.now(timezone.utc)
            mock2.channel_id = 9988
            res2 = await check(mock2)
            self.assertTrue(res2)

        asyncio.run(run_test())

    def test_luckyleaderboard_channel_cooldown(self):
        async def run_test():
            # luckyleaderboard_cmd first check is cooldown
            check = bot.luckyleaderboard_cmd.checks[0]
            mock1 = MagicMock()
            mock1.created_at = datetime.now(timezone.utc)
            mock1.channel_id = 7788

            # 1st execution in channel succeeds
            res1 = await check(mock1)
            self.assertTrue(res1)

            # 2nd execution in same channel triggers CommandOnCooldown (15s)
            with self.assertRaises(app_commands.CommandOnCooldown) as ctx:
                await check(mock1)
            self.assertEqual(ctx.exception.cooldown.rate, 1)
            self.assertEqual(ctx.exception.cooldown.per, 15.0)

        asyncio.run(run_test())

    def test_ping_has_no_cooldown(self):
        # Verify /ping has NO cooldown checks
        ping_cmd = bot.ping_cmd
        # ping_cmd has only default_permissions / checks if any, no cooldown predicate
        for check in ping_cmd.checks:
            func_name = getattr(check, "__qualname__", "") or getattr(check, "__name__", "")
            self.assertNotIn("cooldown", func_name.lower())

    def test_error_handler_cooldown_messages(self):
        async def run_test():
            # Test /records error message
            mock_interaction = MagicMock()
            mock_interaction.command.name = "records"
            mock_interaction.response.send_message = AsyncMock()
            mock_cooldown = MagicMock()
            cooldown_err = app_commands.CommandOnCooldown(mock_cooldown, retry_after=12.4)

            await bot.on_app_command_error(mock_interaction, cooldown_err)
            mock_interaction.response.send_message.assert_called_once()
            call_args = mock_interaction.response.send_message.call_args
            self.assertIn("The records were just posted in this channel", call_args[0][0])
            self.assertIn("12s", call_args[0][0])
            self.assertTrue(call_args[1].get("ephemeral"))

            # Test /luckyleaderboard error message
            mock_interaction.reset_mock()
            mock_interaction.command.name = "luckyleaderboard"
            mock_interaction.response.send_message = AsyncMock()
            cooldown_err = app_commands.CommandOnCooldown(mock_cooldown, retry_after=8.1)

            await bot.on_app_command_error(mock_interaction, cooldown_err)
            mock_interaction.response.send_message.assert_called_once()
            call_args = mock_interaction.response.send_message.call_args
            self.assertIn("Today's leaderboard was just posted in this channel", call_args[0][0])
            self.assertIn("8s", call_args[0][0])
            self.assertTrue(call_args[1].get("ephemeral"))

            # Test /lucky error message
            mock_interaction.reset_mock()
            mock_interaction.command.name = "lucky"
            mock_interaction.response.send_message = AsyncMock()
            cooldown_err = app_commands.CommandOnCooldown(mock_cooldown, retry_after=5.5)

            await bot.on_app_command_error(mock_interaction, cooldown_err)
            mock_interaction.response.send_message.assert_called_once()
            call_args = mock_interaction.response.send_message.call_args
            self.assertIn("Slow down!", call_args[0][0])
            self.assertIn("5.5s", call_args[0][0])
            self.assertTrue(call_args[1].get("ephemeral"))

        asyncio.run(run_test())

    def test_unlink_cooldown_constant(self):
        self.assertEqual(bot.UNLINK_COOLDOWN_SECONDS, 3600)


if __name__ == "__main__":
    unittest.main()
