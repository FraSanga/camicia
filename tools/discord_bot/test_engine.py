import unittest
from engine import get_nth_permutation, simulate, TOTAL_PERMUTATIONS


class TestEngine(unittest.TestCase):
    def test_unranking_boundaries(self):
        # Index 0: 4 A, 4 K, 4 Q, 4 J, 36 -
        d0 = get_nth_permutation(0)
        self.assertEqual(len(d0), 52)
        self.assertEqual(d0[:16], "AAAAKKKKQQQQJJJJ")
        self.assertEqual(d0[16:], "-" * 36)

        # Last index: 36 -, 4 J, 4 Q, 4 K, 4 A
        d_last = get_nth_permutation(TOTAL_PERMUTATIONS - 1)
        self.assertEqual(len(d_last), 52)
        self.assertEqual(d_last[:36], "-" * 36)
        self.assertEqual(d_last[36:], "JJJJQQQQKKKKAAAA")

    def test_simulate_index_0(self):
        d0 = get_nth_permutation(0)
        res = simulate(d0)
        self.assertEqual(res["status"], "finished")
        self.assertEqual(res["cards"], 34)
        self.assertEqual(res["tricks"], 8)
        self.assertEqual(res["winner"], 1)

    def test_simulate_last_index(self):
        d_last = get_nth_permutation(TOTAL_PERMUTATIONS - 1)
        res = simulate(d_last)
        self.assertEqual(res["status"], "finished")
        self.assertEqual(res["cards"], 45)
        self.assertEqual(res["tricks"], 9)
        self.assertEqual(res["winner"], 2)

    def test_casella_2024_loop(self):
        # Casella 2024: known infinite game
        idx = 472460898658889399111
        d_loop = get_nth_permutation(idx)
        res = simulate(d_loop)
        self.assertEqual(res["status"], "loop")
        self.assertEqual(res["cards"], 474)
        self.assertEqual(res["tricks"], 66)
        self.assertEqual(res["winner"], 0)

    def test_nessler_2022_record(self):
        # Nessler 2022: 8344 cards, 1164 tricks
        idx = 447247122283566119055
        d_rec = get_nth_permutation(idx)
        res = simulate(d_rec)
        self.assertEqual(res["status"], "finished")
        self.assertEqual(res["cards"], 8344)
        self.assertEqual(res["tricks"], 1164)


if __name__ == "__main__":
    unittest.main()
