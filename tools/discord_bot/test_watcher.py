import asyncio
import tempfile
import unittest
from pathlib import Path

from watcher import RecordsWatcher


class TestRecordsWatcher(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.proj_dir = Path(self.tmpdir.name) / "project"
        self.proj_dir.mkdir()
        self.state_file = Path(self.tmpdir.name) / "watcher_state.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_line_parsing(self):
        watcher = RecordsWatcher(self.proj_dir, self.state_file)

        # Longest line parsing: <cards> <tricks> <deal_index> <wu_name> <timestamp> <userid> <hostid>
        line = "4210 590 12345678901234567890 camicia_wu_001 1700000000 42 101\n"
        res = watcher._parse_longest_line(line)
        self.assertIsNotNone(res)
        self.assertEqual(res["cards"], 4210)
        self.assertEqual(res["tricks"], 590)
        self.assertEqual(res["deal_index"], "12345678901234567890")
        self.assertEqual(res["wu_name"], "camicia_wu_001")
        self.assertEqual(res["timestamp"], 1700000000)
        self.assertEqual(res["userid"], 42)
        self.assertEqual(res["hostid"], 101)

        # Loop line parsing: <deal_index> <wu_name> <timestamp> <userid> <hostid>
        loop_line = "98765432109876543210 camicia_loop_wu 1700000050 99 202\n"
        loop_res = watcher._parse_loop_line(loop_line)
        self.assertIsNotNone(loop_res)
        self.assertEqual(loop_res["deal_index"], "98765432109876543210")
        self.assertEqual(loop_res["wu_name"], "camicia_loop_wu")
        self.assertEqual(loop_res["timestamp"], 1700000050)
        self.assertEqual(loop_res["userid"], 99)
        self.assertEqual(loop_res["hostid"], 202)

    def test_first_run_ignores_preexisting_and_detects_new(self):
        async def run_async():
            history_file = self.proj_dir / "records_longest_history.txt"
            loops_file = self.proj_dir / "records_loops.txt"

            # Pre-existing records before bot startup
            history_file.write_text("100 20 idx1 wu1 1700000000 1 1\n")
            loops_file.write_text("idx_loop1 wu_loop1 1700000001 1 1\n")

            # Initialize watcher for first time
            watcher = RecordsWatcher(self.proj_dir, self.state_file)

            # First poll: should have 0 new embeds (pre-existing are ignored on initial boot)
            embeds = await watcher.check_new_records()
            self.assertEqual(len(embeds), 0)

            # Now append a new record to history
            with open(history_file, "a") as f:
                f.write("1500 250 idx2 wu2 1700000010 2 2\n")

            embeds = await watcher.check_new_records()
            self.assertEqual(len(embeds), 1)
            self.assertIn("New Project Record", embeds[0].title)
            self.assertIn("1,500", embeds[0].fields[0].value)

            # Now append an infinite loop
            with open(loops_file, "a") as f:
                f.write("idx_loop2 wu_loop2 1700000020 3 3\n")

            embeds2 = await watcher.check_new_records()
            self.assertEqual(len(embeds2), 1)
            self.assertIn("Infinite Loop Discovered", embeds2[0].title)

            # Polling again immediately should return 0 new embeds
            embeds3 = await watcher.check_new_records()
            self.assertEqual(len(embeds3), 0)

        asyncio.run(run_async())

    def test_world_record_detection(self):
        async def run_async():
            history_file = self.proj_dir / "records_longest_history.txt"
            watcher = RecordsWatcher(self.proj_dir, self.state_file)

            # World record is > 8344
            with open(history_file, "a") as f:
                f.write("8345 1165 wr_idx wr_wu 1700000100 42 101\n")

            embeds = await watcher.check_new_records()
            self.assertEqual(len(embeds), 1)
            self.assertIn("ALL-TIME WORLD RECORD BROKEN", embeds[0].title)
            self.assertEqual(embeds[0].color.value, 0xFF0033)

        asyncio.run(run_async())

    def test_longest_fallback_file(self):
        async def run_async():
            # If records_longest_history.txt does NOT exist, watcher falls back to records_longest.txt
            longest_file = self.proj_dir / "records_longest.txt"
            longest_file.write_text("500 50 idx wu 1700000000 10 1\n")

            watcher = RecordsWatcher(self.proj_dir, self.state_file)
            embeds = await watcher.check_new_records()
            self.assertEqual(len(embeds), 0)

            # Update records_longest.txt with a higher score
            longest_file.write_text("750 80 idx2 wu2 1700000010 10 1\n")
            embeds2 = await watcher.check_new_records()
            self.assertEqual(len(embeds2), 1)
            self.assertEqual(watcher.state["last_best_cards"], 750)

        asyncio.run(run_async())

    def test_persistence_across_instances(self):
        async def run_async():
            history_file = self.proj_dir / "records_longest_history.txt"
            history_file.write_text("100 10 idx1 wu1 1700000000 1 1\n")

            watcher1 = RecordsWatcher(self.proj_dir, self.state_file)
            self.assertEqual(watcher1.state["longest_history_line"], 1)

            # Append a new record
            with open(history_file, "a") as f:
                f.write("200 20 idx2 wu2 1700000010 2 2\n")

            await watcher1.check_new_records()
            self.assertEqual(watcher1.state["longest_history_line"], 2)

            # Restart watcher in new instance with same state file
            watcher2 = RecordsWatcher(self.proj_dir, self.state_file)
            self.assertEqual(watcher2.state["longest_history_line"], 2)
            embeds = await watcher2.check_new_records()
            self.assertEqual(len(embeds), 0)

        asyncio.run(run_async())

    def test_lower_or_equal_cards_ignored(self):
        async def run_async():
            history_file = self.proj_dir / "records_longest_history.txt"
            watcher = RecordsWatcher(self.proj_dir, self.state_file)

            # Record 1: 500 cards
            with open(history_file, "a") as f:
                f.write("500 50 idx1 wu1 1700000000 1 1\n")
            embeds = await watcher.check_new_records()
            self.assertEqual(len(embeds), 1)
            self.assertEqual(watcher.state["last_best_cards"], 500)

            # Record 2: 450 cards (lower than 500) -> MUST be ignored!
            with open(history_file, "a") as f:
                f.write("450 45 idx2 wu2 1700000010 1 1\n")
            embeds = await watcher.check_new_records()
            self.assertEqual(len(embeds), 0)
            self.assertEqual(watcher.state["last_best_cards"], 500)

            # Record 3: 500 cards (equal to 500) -> MUST be ignored!
            with open(history_file, "a") as f:
                f.write("500 50 idx3 wu3 1700000020 1 1\n")
            embeds = await watcher.check_new_records()
            self.assertEqual(len(embeds), 0)
            self.assertEqual(watcher.state["last_best_cards"], 500)

            # Record 4: 600 cards (strictly greater) -> Announced!
            with open(history_file, "a") as f:
                f.write("600 60 idx4 wu4 1700000030 1 1\n")
            embeds = await watcher.check_new_records()
            self.assertEqual(len(embeds), 1)
            self.assertEqual(watcher.state["last_best_cards"], 600)

        asyncio.run(run_async())


if __name__ == "__main__":
    unittest.main()
