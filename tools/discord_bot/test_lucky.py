import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

if "discord" not in sys.modules:
    try:
        import discord
    except ImportError:
        sys.modules["discord"] = MagicMock()

from lucky import LuckyManager


class TestLuckyManager(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.tmpdir.name) / "lucky_state.json"
        self.manager = LuckyManager(self.state_file)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_daily_attempt_limiting(self):
        user_id = 12345
        username = "TestUser"

        # Attempt 1
        can_roll, used, max_att = self.manager.can_roll(user_id)
        self.assertTrue(can_roll)
        self.assertEqual(used, 0)
        self.assertEqual(max_att, 3)

        u, m, r = self.manager.record_roll(user_id, username, 300, 45, "123", "finished")
        self.assertEqual(u, 1)

        # Attempt 2
        can_roll, used, max_att = self.manager.can_roll(user_id)
        self.assertTrue(can_roll)
        self.assertEqual(used, 1)
        self.manager.record_roll(user_id, username, 400, 50, "124", "finished")

        # Attempt 3
        can_roll, used, max_att = self.manager.can_roll(user_id)
        self.assertTrue(can_roll)
        self.assertEqual(used, 2)
        self.manager.record_roll(user_id, username, 150, 20, "125", "finished")

        # Attempt 4 (Exceeded for unlinked user)
        can_roll, used, max_att = self.manager.can_roll(user_id)
        self.assertFalse(can_roll)
        self.assertEqual(used, 3)

    def test_linked_user_attempts(self):
        user_id = 99999
        username = "LinkedVolunteer"

        # Verify linked user can roll up to 5 times
        for i in range(5):
            can_roll, used, max_att = self.manager.can_roll(user_id, is_linked=True)
            self.assertTrue(can_roll)
            self.assertEqual(used, i)
            self.assertEqual(max_att, 5)
            self.manager.record_roll(user_id, username, 100 + i, 10, f"idx_{i}", "finished", is_linked=True)

        # Attempt 6 should be blocked
        can_roll, used, max_att = self.manager.can_roll(user_id, is_linked=True)
        self.assertFalse(can_roll)
        self.assertEqual(used, 5)
        self.assertEqual(max_att, 5)

    def test_daily_leaderboard_sorting(self):
        self.manager.record_roll(1, "Alice", 200, 30, "idx1", "finished")
        self.manager.record_roll(2, "Bob", 800, 110, "idx2", "finished")
        self.manager.record_roll(3, "Charlie", 500, 75, "idx3", "finished")

        lb = self.manager.get_leaderboard()
        self.assertEqual(len(lb), 3)
        self.assertEqual(lb[0]["username"], "Bob")
        self.assertEqual(lb[0]["cards"], 800)
        self.assertEqual(lb[1]["username"], "Charlie")
        self.assertEqual(lb[1]["cards"], 500)
        self.assertEqual(lb[2]["username"], "Alice")
        self.assertEqual(lb[2]["cards"], 200)

        # Alice rolls again and beats Bob
        self.manager.record_roll(1, "Alice", 1000, 140, "idx4", "finished")
        lb2 = self.manager.get_leaderboard()
        self.assertEqual(lb2[0]["username"], "Alice")
        self.assertEqual(lb2[0]["cards"], 1000)

    def test_format_deal_layout(self):
        deck = "A" * 4 + "K" * 4 + "Q" * 4 + "J" * 4 + "-" * 36
        layout = self.manager.format_deal_layout(deck)
        self.assertIn("P1", layout)
        self.assertIn("P2", layout)
        self.assertIn("```text", layout)


if __name__ == "__main__":
    unittest.main()
