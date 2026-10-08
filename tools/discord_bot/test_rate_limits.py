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
        bot._dm_cooldowns.clear()
        bot._user_spam_strikes.clear()
        bot._user_mute_until.clear()

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

    def test_lucky_leaderboard_channel_cooldown(self):
        async def run_test():
            # lucky_leaderboard_cmd first check is cooldown
            check = bot.lucky_leaderboard_cmd.checks[0]
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

            # Test /lucky-leaderboard error message
            mock_interaction.reset_mock()
            mock_interaction.command.name = "lucky-leaderboard"
            mock_interaction.response.send_message = AsyncMock()
            cooldown_err = app_commands.CommandOnCooldown(mock_cooldown, retry_after=8.1)

            await bot.on_app_command_error(mock_interaction, cooldown_err)
            mock_interaction.response.send_message.assert_called_once()
            call_args = mock_interaction.response.send_message.call_args
            self.assertIn("The leaderboard was just posted in this channel", call_args[0][0])
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

            # Test /link error message
            mock_interaction.reset_mock()
            mock_interaction.command.name = "link"
            mock_interaction.response.send_message = AsyncMock()
            cooldown_err = app_commands.CommandOnCooldown(mock_cooldown, retry_after=2.8)

            await bot.on_app_command_error(mock_interaction, cooldown_err)
            mock_interaction.response.send_message.assert_called_once()
            call_args = mock_interaction.response.send_message.call_args
            self.assertIn("Slow down!", call_args[0][0])
            self.assertIn("2.8s", call_args[0][0])
            self.assertTrue(call_args[1].get("ephemeral"))

            # Test /unlink error message
            mock_interaction.reset_mock()
            mock_interaction.command.name = "unlink"
            mock_interaction.response.send_message = AsyncMock()
            cooldown_err = app_commands.CommandOnCooldown(mock_cooldown, retry_after=4.2)

            await bot.on_app_command_error(mock_interaction, cooldown_err)
            mock_interaction.response.send_message.assert_called_once()
            call_args = mock_interaction.response.send_message.call_args
            self.assertIn("Slow down!", call_args[0][0])
            self.assertIn("4.2s", call_args[0][0])
            self.assertTrue(call_args[1].get("ephemeral"))

        asyncio.run(run_test())

    def test_unlink_cooldown_constant(self):
        self.assertEqual(bot.UNLINK_COOLDOWN_SECONDS, 3600)

    def test_unlink_cooldown_embed(self):
        embed = bot.get_unlink_cooldown_embed(1800, account_type="Discord")
        self.assertEqual(embed.title, "⏳ Account Unlink Cooldown Active")
        self.assertEqual(embed.color.value, 0xE67E22)
        self.assertIn("30 minutes", embed.fields[0].value)
        self.assertIn("1800s", embed.fields[0].value)

        # Test BOINC account type
        embed_boinc = bot.get_unlink_cooldown_embed(600, account_type="BOINC")
        self.assertIn("BOINC account", embed_boinc.description)

    def test_lockout_embed(self):
        embed = bot.get_lockout_embed(900)
        self.assertEqual(embed.title, "⛔ Verification Temporarily Locked")
        self.assertEqual(embed.color.value, 0xE74C3C)
        self.assertIn("15 minutes", embed.fields[0].value)
        self.assertIn("900s", embed.fields[0].value)

    def test_malformed_code_counts_toward_lockout(self):
        async def run_test():
            user = MagicMock()
            user.id = 55667788

            # Attempts 1-4 with invalid format (e.g. "123")
            for i in range(1, 5):
                success, res = await bot.execute_link_flow(user, "123")
                self.assertFalse(success)
                self.assertIsInstance(res, str)
                self.assertIn("Invalid verification code format", res)
                expected_rem = 5 - i
                self.assertIn(f"**{expected_rem}** attempt", res)

            # 5th attempt with invalid format triggers lockout embed
            success, res = await bot.execute_link_flow(user, "abc")
            self.assertFalse(success)
            self.assertIsInstance(res, bot.discord.Embed)
            self.assertEqual(res.title, "⛔ Verification Temporarily Locked")

            # Subsequent attempt while locked out immediately returns lockout embed
            success, res = await bot.execute_link_flow(user, "999999")
            self.assertFalse(success)
            self.assertIsInstance(res, bot.discord.Embed)
            self.assertEqual(res.title, "⛔ Verification Temporarily Locked")

        asyncio.run(run_test())

    def test_link_slash_command_cooldown(self):
        async def run_test():
            # Find cooldown check on link_cmd
            cooldown_checks = [c for c in bot.link_cmd.checks if "cooldown" in getattr(c, "__qualname__", "").lower()]
            self.assertTrue(len(cooldown_checks) > 0)
            check = cooldown_checks[0]

            mock_inter = MagicMock()
            mock_inter.created_at = datetime.now(timezone.utc)
            mock_inter.user.id = 12345

            # 1st call succeeds
            res1 = await check(mock_inter)
            self.assertTrue(res1)

            # 2nd call raises CommandOnCooldown (3.0s)
            with self.assertRaises(app_commands.CommandOnCooldown) as ctx:
                await check(mock_inter)
            self.assertEqual(ctx.exception.cooldown.rate, 1)
            self.assertEqual(ctx.exception.cooldown.per, 3.0)

        asyncio.run(run_test())

    def test_unlink_slash_command_cooldown(self):
        async def run_test():
            # Find cooldown check on unlink_cmd
            cooldown_checks = [c for c in bot.unlink_cmd.checks if "cooldown" in getattr(c, "__qualname__", "").lower()]
            self.assertTrue(len(cooldown_checks) > 0)
            check = cooldown_checks[0]

            mock_inter = MagicMock()
            mock_inter.created_at = datetime.now(timezone.utc)
            mock_inter.user.id = 67890

            # 1st call succeeds
            res1 = await check(mock_inter)
            self.assertTrue(res1)

            # 2nd call raises CommandOnCooldown (5.0s)
            with self.assertRaises(app_commands.CommandOnCooldown) as ctx:
                await check(mock_inter)
            self.assertEqual(ctx.exception.cooldown.rate, 1)
            self.assertEqual(ctx.exception.cooldown.per, 5.0)

        asyncio.run(run_test())

    def test_dm_cooldown_helper(self):
        user_id = 998877
        # 1st check passes
        is_cd, rem = bot.check_dm_cooldown(user_id, "link", 3.0)
        self.assertFalse(is_cd)
        self.assertEqual(rem, 0.0)

        # 2nd check within 3s is on cooldown
        is_cd, rem = bot.check_dm_cooldown(user_id, "link", 3.0)
        self.assertTrue(is_cd)
        self.assertGreater(rem, 1.0)
        self.assertLessEqual(rem, 3.0)

        # Different action for same user is not on cooldown
        is_cd, rem = bot.check_dm_cooldown(user_id, "unlink", 5.0)
        self.assertFalse(is_cd)

        # Different user is not on cooldown
        is_cd, rem = bot.check_dm_cooldown(112233, "link", 3.0)
        self.assertFalse(is_cd)

    def test_spam_strikes_accumulation_and_mute(self):
        user_id = 445566
        # Strikes 1 to 4
        for i in range(1, 5):
            cnt, is_muted, rem_mute = bot.record_spam_strike(user_id)
            self.assertEqual(cnt, i)
            self.assertFalse(is_muted)
            self.assertEqual(rem_mute, 0)
            is_m, rem = bot.check_user_muted(user_id)
            self.assertFalse(is_m)

        # 5th strike triggers 15-minute mute
        cnt, is_muted, rem_mute = bot.record_spam_strike(user_id)
        self.assertEqual(cnt, 5)
        self.assertTrue(is_muted)
        self.assertEqual(rem_mute, 900)

        # check_user_muted returns True
        is_m, rem = bot.check_user_muted(user_id)
        self.assertTrue(is_m)
        self.assertGreater(rem, 890)
        self.assertLessEqual(rem, 900)

        # 6th attempt while muted returns muted state
        cnt, is_muted, rem_mute = bot.record_spam_strike(user_id)
        self.assertTrue(is_muted)
        self.assertGreater(rem_mute, 890)

    def test_spam_strikes_decay_after_window(self):
        user_id = 998811
        import time
        past = time.monotonic() - 305  # >5 minutes ago
        bot._user_spam_strikes[user_id] = [past, past, past]

        # Recording a strike now should drop expired strikes
        cnt, is_muted, rem = bot.record_spam_strike(user_id)
        self.assertEqual(cnt, 1)  # Only the new strike remains
        self.assertFalse(is_muted)

    def test_mute_expiration(self):
        user_id = 332211
        import time
        bot._user_mute_until[user_id] = time.monotonic() - 5  # expired 5s ago
        is_m, rem = bot.check_user_muted(user_id)
        self.assertFalse(is_m)
        self.assertEqual(rem, 0)
        self.assertNotIn(user_id, bot._user_mute_until)
        self.assertNotIn(user_id, bot._user_spam_strikes)

    def test_clear_spam_strikes(self):
        user_id = 776655
        bot.record_spam_strike(user_id)
        bot.record_spam_strike(user_id)
        bot.clear_spam_strikes(user_id)
        self.assertNotIn(user_id, bot._user_spam_strikes)
        self.assertNotIn(user_id, bot._user_mute_until)

    def test_spam_muted_embed(self):
        embed = bot.get_spam_muted_embed(900)
        self.assertEqual(embed.title, "🔇 Commands Temporarily Ignored")
        self.assertEqual(embed.color.value, 0xE74C3C)
        self.assertIn("15 minutes", embed.fields[0].value)
        self.assertIn("900s", embed.fields[0].value)

    def test_unlink_unlinked_user_accumulates_strikes_and_mutes(self):
        async def run_test():
            user_id = 121212
            # Mock DB to return no linked row
            mock_pool = MagicMock()
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_cur.fetchone = AsyncMock(return_value=None)
            mock_cur.execute = AsyncMock()

            # Async context manager setup
            mock_conn.cursor.return_value.__aenter__ = AsyncMock(return_value=mock_cur)
            mock_conn.cursor.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

            original_get_db = bot.get_db_pool
            bot.get_db_pool = AsyncMock(return_value=mock_pool)
            try:
                # 4 unlinked calls accumulate strikes 1-4
                for i in range(1, 5):
                    success, msg, uid, vname = await bot.unlink_discord_user(user_id)
                    self.assertFalse(success)
                    self.assertIsInstance(msg, str)
                    self.assertIn(f"Strike {i}/5", msg)

                # 5th call triggers mute embed
                success, embed, uid, vname = await bot.unlink_discord_user(user_id)
                self.assertFalse(success)
                self.assertIsInstance(embed, bot.discord.Embed)
                self.assertEqual(embed.title, "🔇 Commands Temporarily Ignored")

                # Subsequent call while muted immediately returns mute embed without DB query
                mock_cur.execute.reset_mock()
                success, embed2, uid, vname = await bot.unlink_discord_user(user_id)
                self.assertFalse(success)
                self.assertIsInstance(embed2, bot.discord.Embed)
                self.assertEqual(embed2.title, "🔇 Commands Temporarily Ignored")
                mock_cur.execute.assert_not_called()
            finally:
                bot.get_db_pool = original_get_db

        asyncio.run(run_test())

    def test_on_message_silent_drop_when_muted(self):
        async def run_test():
            user_id = 999111
            import time
            bot._user_mute_until[user_id] = time.monotonic() + 500  # Muted

            mock_msg = MagicMock()
            mock_msg.author.id = user_id
            mock_msg.author.bot = False
            mock_msg.guild = None  # DM
            mock_msg.content = "/link 123456"
            mock_msg.channel.send = AsyncMock()

            await bot.on_message(mock_msg)
            # Message should be silently dropped without any response
            mock_msg.channel.send.assert_not_called()

        asyncio.run(run_test())

    def test_is_tester_or_admin(self):
        # 1. None member
        self.assertFalse(bot.is_tester_or_admin(None))

        # 2. Regular member
        regular = MagicMock()
        regular.guild_permissions.administrator = False
        role1 = MagicMock()
        role1.id = 12345
        role1.name = "Member"
        regular.roles = [role1]
        self.assertFalse(bot.is_tester_or_admin(regular))

        # 3. Admin member
        admin = MagicMock()
        admin.guild_permissions.administrator = True
        admin.roles = []
        self.assertTrue(bot.is_tester_or_admin(admin))

        # 4. Tester by configured role ID
        orig_tester_id = bot.config.DISCORD_TESTER_ROLE_ID
        try:
            bot.config.DISCORD_TESTER_ROLE_ID = 998877
            tester_by_id = MagicMock()
            tester_by_id.guild_permissions.administrator = False
            role_tester = MagicMock()
            role_tester.id = 998877
            role_tester.name = "CustomRole"
            tester_by_id.roles = [role_tester]
            self.assertTrue(bot.is_tester_or_admin(tester_by_id))
        finally:
            bot.config.DISCORD_TESTER_ROLE_ID = orig_tester_id

        # 5. Tester by role name "tester" / "Tester"
        tester_by_name = MagicMock()
        tester_by_name.guild_permissions.administrator = False
        role_named = MagicMock()
        role_named.id = 554433
        role_named.name = "Tester"
        tester_by_name.roles = [role_named]
        self.assertTrue(bot.is_tester_or_admin(tester_by_name))

    def test_staging_interaction_check(self):
        async def run_test():
            orig_mode = bot.config.STAGING_MODE
            orig_get_member = bot.get_guild_member
            try:
                # When STAGING_MODE is False, anyone passes
                bot.config.STAGING_MODE = False
                mock_inter = MagicMock()
                mock_inter.user = MagicMock(spec=bot.discord.Member)
                mock_inter.user.guild_permissions.administrator = False
                mock_inter.user.roles = []
                self.assertTrue(await bot.staging_interaction_check(mock_inter))

                # When STAGING_MODE is True
                bot.config.STAGING_MODE = True

                # Regular user raises CheckFailure
                with self.assertRaises(app_commands.CheckFailure):
                    await bot.staging_interaction_check(mock_inter)

                # Admin member passes
                admin_inter = MagicMock()
                admin_inter.user = MagicMock(spec=bot.discord.Member)
                admin_inter.user.guild_permissions.administrator = True
                admin_inter.user.roles = []
                self.assertTrue(await bot.staging_interaction_check(admin_inter))

                # Tester member passes
                tester_inter = MagicMock()
                tester_inter.user = MagicMock(spec=bot.discord.Member)
                tester_inter.user.guild_permissions.administrator = False
                role_tester = MagicMock()
                role_tester.name = "Tester"
                tester_inter.user.roles = [role_tester]
                self.assertTrue(await bot.staging_interaction_check(tester_inter))

                # DM interaction (user is discord.User, not discord.Member)
                dm_inter = MagicMock()
                dm_inter.user = MagicMock(spec=bot.discord.User)
                dm_inter.user.id = 887766

                # DM interaction when guild member is Tester
                tester_guild_member = MagicMock(spec=bot.discord.Member)
                tester_guild_member.guild_permissions.administrator = False
                tester_guild_member.roles = [role_tester]
                bot.get_guild_member = AsyncMock(return_value=tester_guild_member)
                self.assertTrue(await bot.staging_interaction_check(dm_inter))

                # DM interaction when user is not a tester/admin
                bot.get_guild_member = AsyncMock(return_value=None)
                with self.assertRaises(app_commands.CheckFailure):
                    await bot.staging_interaction_check(dm_inter)
            finally:
                bot.config.STAGING_MODE = orig_mode
                bot.get_guild_member = orig_get_member

        asyncio.run(run_test())

    def test_on_message_silent_drop_in_staging_for_non_testers(self):
        async def run_test():
            orig_mode = bot.config.STAGING_MODE
            orig_get_member = bot.get_guild_member
            try:
                bot.config.STAGING_MODE = True

                # Non-tester member in guild
                regular_member = MagicMock()
                regular_member.guild_permissions.administrator = False
                regular_member.roles = []
                bot.get_guild_member = AsyncMock(return_value=regular_member)

                mock_msg = MagicMock()
                mock_msg.author.id = 123456
                mock_msg.author.bot = False
                mock_msg.guild = None  # DM
                mock_msg.content = "/link 123456"
                mock_msg.channel.send = AsyncMock()

                await bot.on_message(mock_msg)
                mock_msg.channel.send.assert_not_called()
            finally:
                bot.config.STAGING_MODE = orig_mode
                bot.get_guild_member = orig_get_member

        asyncio.run(run_test())

    def test_is_bot_commands_channel_isolation(self):
        async def run_test():
            orig_mode = bot.config.STAGING_MODE
            orig_channel_id = bot.config.DISCORD_BOT_COMMANDS_CHANNEL_ID
            try:
                # bot.records_cmd has is_bot_commands_channel check at index 1
                predicate = bot.records_cmd.checks[1]

                # Set allowed channel to 99999 (e.g. #staging-bot-commands)
                bot.config.DISCORD_BOT_COMMANDS_CHANNEL_ID = 99999

                # 1. Matching channel passes
                mock_inter_match = MagicMock()
                mock_inter_match.channel_id = 99999
                self.assertTrue(await predicate(mock_inter_match))

                # 2. Non-matching channel fails, even if named "bot-commands"
                mock_inter_wrong = MagicMock()
                mock_inter_wrong.channel_id = 11111
                mock_inter_wrong.channel.name = "bot-commands"
                mock_inter_wrong.user.guild_permissions.administrator = False
                with self.assertRaises(app_commands.CheckFailure):
                    await predicate(mock_inter_wrong)

                # 3. In STAGING_MODE, admin must also use configured channel
                bot.config.STAGING_MODE = True
                mock_admin_wrong = MagicMock()
                mock_admin_wrong.channel_id = 11111
                mock_admin_wrong.channel.name = "bot-commands"
                mock_admin_wrong.user.guild_permissions.administrator = True
                with self.assertRaises(app_commands.CheckFailure):
                    await predicate(mock_admin_wrong)

                # 4. In PRODUCTION, admin can use any channel
                bot.config.STAGING_MODE = False
                self.assertTrue(await predicate(mock_admin_wrong))
            finally:
                bot.config.STAGING_MODE = orig_mode
                bot.config.DISCORD_BOT_COMMANDS_CHANNEL_ID = orig_channel_id

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
